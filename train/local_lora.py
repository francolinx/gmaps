"""GMAPS parser LoRA, trained LOCALLY on the DGX Spark. Mirrors train/modal_lora.py without Unsloth:
plain transformers + peft + a hand-rolled completion-only mask, so it runs inside nvcr.io/nvidia/pytorch:25.11-py3 (aarch64,
CUDA 13) with pip-installable pure-Python deps only. No bitsandbytes, no Triton kernels to build, nothing to compile.

  python3 train/local_lora.py --base /models/qwen3-4b-instruct --data eval/synthetic/train_ft/pairs_asr.jsonl --out /out/gmaps-parser-4b
  python3 train/local_lora.py ... --max-steps 20 --no-merge            # smoke test
  python3 train/local_lora.py ... --save-steps 50 --step-sleep 2       # crash-tolerant (Oct 17): checkpoints + duty-cycled GPU
  python3 train/local_lora.py ... --save-steps 50 --resume             # same --out: continue from the newest checkpoint
  (normally launched by train/run_local_lora.sh, which pins the container, mounts and the memory guard)

What it does
  * loads the base model from a LOCAL directory (the SSD), bf16, gradient checkpointing; refuses a Hub id
  * data: a chat-format JSONL (pairs.jsonl or pairs_asr.jsonl). Rows tagged groundable=false are skipped unless
    --include-ungroundable. --variants filters pairs_asr.jsonl rows (default: all variants present)
  * renders each row with the tokenizer's own chat template, enable_thinking=False (exactly what vLLM renders at serving),
    and masks the loss on everything up to and including the generation prompt, so only the assistant JSON is learned
  * LoRA r=16, alpha=16, dropout 0, on q/k/v/o/gate/up/down (same as the Modal script), AdamW (torch), lr 2e-4, linear, warmup 10
  * saves the adapter, a merged fp16 copy (for `vllm serve <merged>`; the adapter alone works with --enable-lora), a
    report.json (losses, steps, runtime, peak memory, data sha256, base model identity) and the exact rendered example
Nothing here reads eval/truth, eval/labels or any held-out split. SIMULATED data only; the script checks the manifest flags.
"""
import argparse, hashlib, json, os, random, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def load_rows(path, variants, include_ungroundable, limit, seed):
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if variants and r.get('variant') and r['variant'] not in variants:
                continue
            if r.get('groundable') is False and not include_ungroundable:
                continue
            rows.append(r)
    random.Random(seed).shuffle(rows)
    return rows[:limit] if limit else rows


def manifest_ok(data_path):
    d = Path(data_path).parent
    for name in ('ASR_NOISE_MANIFEST.json', 'MANIFEST.json'):
        p = d / name
        if p.exists():
            m = json.loads(p.read_text())
            if not (m.get('SIMULATED') and m.get('TRAINING_ONLY') and m.get('text_only')):
                raise SystemExit(f'refusing: {p} is not SIMULATED + TRAINING_ONLY + text_only')
            return name
    raise SystemExit(f'refusing: no MANIFEST next to {data_path}')


def render(tok, messages):
    """(prompt_text, full_text): prompt = what vLLM sends before generation (thinking off); full = prompt + assistant + end."""
    kw = {'tokenize': False, 'enable_thinking': False}
    prompt = tok.apply_chat_template(messages[:-1], add_generation_prompt=True, **kw)
    full = tok.apply_chat_template(messages, add_generation_prompt=False, **kw)
    return prompt, full


def encode(tok, messages, max_len):
    prompt, full = render(tok, messages)
    if not full.startswith(prompt):
        # some templates strip the empty think block differently in the two calls; fall back to the assistant header
        marker = '<|im_start|>assistant\n'
        cut = full.rfind(marker)
        if cut < 0:
            raise ValueError('cannot locate the assistant turn in the rendered chat')
        prompt = full[:cut + len(marker)]
    p_ids = tok(prompt, add_special_tokens=False)['input_ids']
    f_ids = tok(full, add_special_tokens=False)['input_ids']
    if f_ids[:len(p_ids)] != p_ids:
        # token boundary moved at the join; re-tokenize the completion alone and concatenate (loss mask stays exact)
        c_ids = tok(full[len(prompt):], add_special_tokens=False)['input_ids']
        f_ids = p_ids + c_ids
    f_ids = f_ids[:max_len]
    labels = [-100] * min(len(p_ids), len(f_ids)) + f_ids[len(p_ids):]
    return {'input_ids': f_ids, 'labels': labels, 'attention_mask': [1] * len(f_ids)}


class Collator:
    def __init__(self, pad_id):
        self.pad_id = pad_id

    def __call__(self, feats):
        import torch
        n = max(len(f['input_ids']) for f in feats)
        ids = torch.full((len(feats), n), self.pad_id, dtype=torch.long)
        lab = torch.full((len(feats), n), -100, dtype=torch.long)
        att = torch.zeros((len(feats), n), dtype=torch.long)
        for i, f in enumerate(feats):
            L = len(f['input_ids'])
            ids[i, :L] = torch.tensor(f['input_ids']); lab[i, :L] = torch.tensor(f['labels']); att[i, :L] = 1
        return {'input_ids': ids, 'labels': lab, 'attention_mask': att}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='LOCAL directory of the base model (config.json inside); Hub ids are refused')
    ap.add_argument('--data', default=str(REPO / 'eval/synthetic/train_ft/pairs_asr.jsonl'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--variants', default='', help='comma list, e.g. clean,unprompted,prompted,harsh (pairs_asr.jsonl only)')
    ap.add_argument('--include-ungroundable', action='store_true')
    ap.add_argument('--epochs', type=float, default=2.0)
    ap.add_argument('--max-steps', type=int, default=-1)
    ap.add_argument('--limit', type=int, default=0, help='use only the first N shuffled rows (smoke tests)')
    ap.add_argument('--lr', type=float, default=2e-4)
    ap.add_argument('--lora-r', type=int, default=16)
    ap.add_argument('--batch', type=int, default=8)
    ap.add_argument('--grad-accum', type=int, default=1)
    ap.add_argument('--max-len', type=int, default=2048)
    ap.add_argument('--seed', type=int, default=3407)
    ap.add_argument('--no-merge', action='store_true')
    ap.add_argument('--cpu', action='store_true', help='CPU smoke test (fp32, tiny)')
    ap.add_argument('--save-steps', type=int, default=0, help='adapter checkpoint every N steps under <out>/checkpoints (keeps 3); 0 = none')
    ap.add_argument('--resume', action='store_true', help='resume from the newest <out>/checkpoints/checkpoint-* if one exists')
    ap.add_argument('--step-sleep', type=float, default=0.0, help='sleep S seconds after every optimizer step (duty-cycles the GPU: less sustained power and heat)')
    a = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainerCallback, TrainingArguments
    from peft import LoraConfig, get_peft_model

    base = Path(a.base)
    if not (base.is_dir() and (base / 'config.json').exists()):
        raise SystemExit(f'refusing: --base must be a local model directory with config.json (got {a.base})')
    man_name = manifest_ok(a.data)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows = load_rows(a.data, [v for v in a.variants.split(',') if v], a.include_ungroundable, a.limit, a.seed)
    if not rows:
        raise SystemExit('no training rows after filtering')
    log(f'rows: {len(rows)} from {a.data} ({man_name})')

    tok = AutoTokenizer.from_pretrained(str(base))
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    enc = [encode(tok, r['messages'], a.max_len) for r in rows]
    n_lab = [sum(1 for x in e['labels'] if x != -100) for e in enc]
    log(f'tokens: max {max(len(e["input_ids"]) for e in enc)}, mean {sum(len(e["input_ids"]) for e in enc) / len(enc):.0f}; '
        f'supervised tokens per row: min {min(n_lab)}, mean {sum(n_lab) / len(n_lab):.1f}')
    assert min(n_lab) > 0, 'a row has no supervised tokens: the completion mask is wrong'
    ex_prompt, ex_full = render(tok, rows[0]['messages'])
    (out / 'rendered_example.txt').write_text('PROMPT (masked):\n' + ex_prompt + '\n\nCOMPLETION (trained):\n' + ex_full[len(ex_prompt):] + '\n')

    dtype = torch.float32 if a.cpu else torch.bfloat16
    model = AutoModelForCausalLM.from_pretrained(str(base), dtype=dtype, device_map=None if a.cpu else {'': 0})
    model.config.use_cache = False
    if not a.cpu:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()
    lcfg = LoraConfig(r=a.lora_r, lora_alpha=a.lora_r, lora_dropout=0.0, bias='none', task_type='CAUSAL_LM',
                      target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'])
    model = get_peft_model(model, lcfg)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    log(f'LoRA r={a.lora_r}: trainable {trainable / 1e6:.1f}M of {total / 1e9:.2f}B')

    class DS(torch.utils.data.Dataset):
        def __len__(self):
            return len(enc)

        def __getitem__(self, i):
            return enc[i]

    args = TrainingArguments(output_dir=str(out / 'checkpoints'), per_device_train_batch_size=a.batch, gradient_accumulation_steps=a.grad_accum,
                             learning_rate=a.lr, num_train_epochs=a.epochs, max_steps=a.max_steps, warmup_steps=10, lr_scheduler_type='linear',
                             logging_steps=1, weight_decay=0.0, bf16=not a.cpu, fp16=False, seed=a.seed,
                             save_strategy='steps' if a.save_steps > 0 else 'no', save_steps=a.save_steps or 500, save_total_limit=3,
                             report_to=[], dataloader_num_workers=0, remove_unused_columns=False, optim='adamw_torch',
                             gradient_checkpointing=not a.cpu, use_cpu=a.cpu)
    class StepSleep(TrainerCallback):
        def on_step_end(self, args, state, control, **kw):
            time.sleep(a.step_sleep)

    trainer = Trainer(model=model, args=args, train_dataset=DS(), data_collator=Collator(tok.pad_token_id),
                      callbacks=[StepSleep()] if a.step_sleep > 0 else None)
    ckpts = sorted((out / 'checkpoints').glob('checkpoint-*'), key=lambda p: int(p.name.split('-')[-1])) if (out / 'checkpoints').is_dir() else []
    resumed_from = None
    if a.resume and ckpts:
        resumed_from = ckpts[-1].name
        log(f'resumed from {ckpts[-1]}')
    elif a.resume:
        log('--resume: no checkpoint under ' + str(out / 'checkpoints') + '; starting from step 0')
    stats = trainer.train(resume_from_checkpoint=True if resumed_from else None)
    losses = [h['loss'] for h in trainer.state.log_history if 'loss' in h]
    model.save_pretrained(str(out / 'adapter')); tok.save_pretrained(str(out / 'adapter'))
    merged_dir = None
    if not a.no_merge:
        merged = model.merge_and_unload()
        merged_dir = out / 'merged_fp16'
        merged.save_pretrained(str(merged_dir), safe_serialization=True); tok.save_pretrained(str(merged_dir))
    peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() and not a.cpu else None
    report = {'base_model_dir': str(base), 'base_model_config': json.loads((base / 'config.json').read_text()).get('_name_or_path'),
              'data': a.data, 'data_sha256': hashlib.sha256(Path(a.data).read_bytes()).hexdigest(), 'rows': len(rows),
              'variants': a.variants or 'all', 'include_ungroundable': a.include_ungroundable, 'epochs': a.epochs, 'max_steps': a.max_steps,
              'steps': trainer.state.global_step, 'lora_r': a.lora_r, 'lr': a.lr, 'batch': a.batch * a.grad_accum,
              'trainable_params_M': round(trainable / 1e6, 1), 'first_loss': losses[0] if losses else None, 'last_loss': losses[-1] if losses else None,
              'loss_every_25_steps': losses[::25], 'train_runtime_s': round(stats.metrics.get('train_runtime', 0), 1), 'wall_s': round(time.time() - t0, 1),
              'peak_gpu_mem_gib': round(peak, 1) if peak is not None else None, 'adapter': str(out / 'adapter'), 'merged_fp16': str(merged_dir) if merged_dir else None,
              'device': 'cpu' if a.cpu else torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'unknown',
              'save_steps': a.save_steps, 'resume': a.resume, 'resumed_from': resumed_from, 'step_sleep_s': a.step_sleep,
              'SIMULATED': True, 'note': 'parser LoRA; evaluate with eval/parser_baselines-style scoring on heldout4/heldout5 before any claim'}
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    log(json.dumps({k: report[k] for k in ('rows', 'steps', 'first_loss', 'last_loss', 'train_runtime_s', 'peak_gpu_mem_gib', 'adapter', 'merged_fp16')}))


if __name__ == '__main__':
    main()

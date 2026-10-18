"""GMAPS parser LoRA on Modal: trains BOTH Qwen/Qwen3-1.7B and Qwen/Qwen3-4B-Instruct-2507, one after the other. Adapted from Modal's official Unsloth example
 (modal.com/docs/examples/unsloth_finetune, modal-labs/modal-examples 06_gpu_and_ml/unsloth_finetune.py):
same pinned image and Unsloth flow, with these changes:
  * base model Qwen/Qwen3-4B-Instruct-2507, downloaded INSIDE the Modal container from Hugging Face (never uploaded from the SSD)
  * data = eval/synthetic/train_ft/pairs.jsonl ONLY (SIMULATED, text only), uploaded to a Modal volume; the script refuses any
    other file and refuses unless its sha256 equals train_ft/MANIFEST.json (no real clips, transcripts or LGA material)
  * LoRA r=16 (alpha 16, dropout 0), bf16 weights (no 4-bit), loss on the assistant turn only, thinking off, 2 epochs
  * saves the adapter (+ a merged fp16 copy) to the volume `gmaps-parser-out`; no wandb secret
  * spend guard (Franco: stop before $5): each training call has a hard 60 min timeout (L40S $1.95/h), and the local entrypoint
    starts a run only if the spend so far (wall time x rate) + that run's worst case stays under $5
Approved by Franco after `modal setup`.

  ~/ftvenv/bin/modal run train/modal_lora.py                        # both models
  ~/ftvenv/bin/modal volume get gmaps-parser-out <run>/adapter <dest>   # then: modal volume delete (Franco: delete the volumes)
"""
import hashlib, json, pathlib

import modal

REPO = pathlib.Path(__file__).resolve().parents[1]
PAIRS = REPO / 'eval/synthetic/train_ft/pairs.jsonl'
MANIFEST = REPO / 'eval/synthetic/train_ft/MANIFEST.json'
RUNS = [('qwen3-1.7b', 'Qwen/Qwen3-1.7B'), ('qwen3-4b-instruct-2507', 'Qwen/Qwen3-4B-Instruct-2507')]
RATE_PER_S = 0.000542       # L40S GPU; CPU and memory add a little (modal.com/pricing)
TIMEOUT_S = 60 * 60
BUDGET_USD = 5.0
GPU_TYPE = 'L40S'            # as the example; $0.000542/s (modal.com/pricing)

app = modal.App('gmaps-parser-lora')
train_image = (
    modal.Image.debian_slim(python_version='3.11')
    .uv_pip_install(           # the example's pinned versions; wandb dropped
        'accelerate==1.9.0', 'datasets==3.6.0', 'hf-transfer==0.1.9', 'huggingface_hub==0.34.2', 'peft==0.16.0',
        'transformers==4.54.0', 'trl==0.19.1', 'unsloth[cu128-torch270]==2025.7.8', 'unsloth_zoo==2025.7.10')
    .env({'HF_HOME': '/model_cache', 'HF_HUB_ENABLE_HF_TRANSFER': '1'})
)
with train_image.imports():
    import unsloth  # noqa: F401,I001  (unsloth must be first, as the example)
    import datasets
    import torch
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import train_on_responses_only

model_cache = modal.Volume.from_name('gmaps-parser-model-cache', create_if_missing=True)
data_vol = modal.Volume.from_name('gmaps-parser-data', create_if_missing=True)
out_vol = modal.Volume.from_name('gmaps-parser-out', create_if_missing=True)


@app.function(image=train_image, gpu=GPU_TYPE, timeout=TIMEOUT_S,
              volumes={'/model_cache': model_cache, '/data': data_vol, '/out': out_vol})
def finetune(run: str, base_model: str, epochs: float = 2.0, max_steps: int = -1, lora_r: int = 16, lr: float = 2e-4,
             max_seq_length: int = 2048, seed: int = 3407, merged: bool = True):
    import time
    t0 = time.time()
    model, tok = FastLanguageModel.from_pretrained(base_model, max_seq_length=max_seq_length, dtype=torch.bfloat16,
                                                   load_in_4bit=False, load_in_8bit=False)
    model = FastLanguageModel.get_peft_model(
        model, r=lora_r, lora_alpha=lora_r, lora_dropout=0.0, bias='none',
        target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'],
        use_gradient_checkpointing='unsloth', random_state=seed)
    rows = [json.loads(x) for x in open('/data/pairs.jsonl')]
    texts = [tok.apply_chat_template(r['messages'], tokenize=False, add_generation_prompt=False, enable_thinking=False) for r in rows]
    ds = datasets.Dataset.from_dict({'text': texts}).shuffle(seed=seed)
    trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=ds, args=SFTConfig(
        dataset_text_field='text', max_seq_length=max_seq_length, per_device_train_batch_size=8, gradient_accumulation_steps=1,
        learning_rate=lr, num_train_epochs=epochs, max_steps=max_steps, warmup_steps=10, lr_scheduler_type='linear', logging_steps=1, optim='adamw_8bit',
        weight_decay=0.0, bf16=True, fp16=False, seed=seed, output_dir=f'/out/{run}/checkpoints', save_strategy='no', report_to='none'))
    # completion-only loss: mask the system and user turns (Qwen chat markers)
    trainer = train_on_responses_only(trainer, instruction_part='<|im_start|>user\n', response_part='<|im_start|>assistant\n')
    print('rendered example (tail):', repr(texts[0][-260:]))
    stats = trainer.train()
    losses = [h['loss'] for h in trainer.state.log_history if 'loss' in h]
    model.save_pretrained(f'/out/{run}/adapter'); tok.save_pretrained(f'/out/{run}/adapter')
    if merged:
        model.save_pretrained_merged(f'/out/{run}/merged_fp16', tok, save_method='merged_16bit')
    report = {'run': run, 'base_model': base_model, 'rows': len(rows), 'epochs': epochs, 'steps': trainer.state.global_step, 'lora_r': lora_r,
              'gpu': GPU_TYPE, 'first_loss': losses[0] if losses else None, 'last_loss': losses[-1] if losses else None,
              'loss_every_25_steps': losses[::25], 'train_runtime_s': round(stats.metrics.get('train_runtime', 0), 1), 'wall_s': round(time.time() - t0, 1)}
    pathlib.Path(f'/out/{run}/report.json').write_text(json.dumps(report, indent=1))
    out_vol.commit()
    return report


@app.local_entrypoint()
def main(epochs: float = 2.0, merged: bool = True):
    import time
    man = json.loads(MANIFEST.read_text())
    if not (man.get('SIMULATED') and man.get('TRAINING_ONLY') and man.get('text_only')):
        raise SystemExit('refusing: train_ft MANIFEST is not SIMULATED + TRAINING_ONLY + text_only')
    sha = hashlib.sha256(PAIRS.read_bytes()).hexdigest()
    if sha != man['pairs_jsonl']['sha256']:
        raise SystemExit('refusing: pairs.jsonl does not match MANIFEST.json (regenerate with eval/synthetic/train_ft.py)')
    print(f'uploading the ONLY file that leaves the Spark: {PAIRS.name} sha256 {sha[:16]}…, {PAIRS.stat().st_size} bytes')
    with data_vol.batch_upload(force=True) as b:
        b.put_file(str(PAIRS), '/pairs.jsonl')
    spent, reports = 0.0, []
    for run, base in RUNS:
        worst = TIMEOUT_S * RATE_PER_S
        if spent + worst > BUDGET_USD:
            print(f'SKIP {run}: spent ~${spent:.2f} + worst case ${worst:.2f} would pass ${BUDGET_USD}')
            continue
        t0 = time.time()
        rep = finetune.remote(run=run, base_model=base, epochs=epochs, merged=merged)
        spent += (time.time() - t0) * RATE_PER_S
        rep['est_cost_usd_gpu'] = round((time.time() - t0) * RATE_PER_S, 3)
        reports.append(rep)
        print(json.dumps(rep, indent=1))
    print(f'estimated GPU spend (wall time x L40S rate): ${spent:.2f}; actual: modal billing summary')

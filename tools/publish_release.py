"""Assemble the PUBLIC release bundle for the Fine-Tuned & Specialized track. Nothing is uploaded.

The main repository cannot be made public as it stands (DECISIONS N15: INTERNAL LGA replay material and real-ATC transcripts in its
history), so "publish it openly" means a clean bundle built from an allow-list, checked against a deny-list, hashed, and pushed
to Hugging Face with the printed `hf upload` commands after Franco reads the cards.

  python3 tools/publish_release.py --run ~/gmaps_venue/adapters/<run> --name gmaps-parser-qwen3-4b-lora --hf-user <user>
  python3 tools/publish_release.py --dry-run --name gmaps-parser-qwen3-4b-lora          # no adapter yet: dataset + code + cards only

Bundle layout (<out>/<name>/):
  model/      adapter_config.json, adapter_model.safetensors, tokenizer files, report.json, rendered_example.txt
              (+ merged_fp16/ with --include-merged)
  dataset/    train/pairs_asr.jsonl + ASR_NOISE_MANIFEST.json + MANIFEST.json + asr_noise_model.json;
              eval/<set>.jsonl = the three held-out task files (synthetic, with gold); README.md = dataset card
  code/       the generator, augmenter, trainer, verifier and offline runner, with the small library modules they import
  README.md   model card with the scoreboard table (docs/SCOREBOARD.md) and the claim limits; MANIFEST.json = sha256 of every file
Refuses to build if any file path or content trips the deny-list (INTERNAL, real audio, LGA -22 fixture, eval/truth,
eval/labels, secrets, audio files, anything under ~/gmaps_venue/audio_real_private by bytes). SIMULATED data throughout.
"""
import argparse, ast, hashlib, io, json, os, re, shutil, subprocess, sys, tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools' / 'hooks'))
import precommit_check as PC   # noqa: E402  (SECRETS patterns, AUDIO_EXT, private_hashes)

DENY_PATH = re.compile(r'(?i)internal|audio_real_private|LGA_2026-03-22|lga_2026_03_22|eval/truth|eval/labels|transcripts?/real|real_atc|vasaviation|secrets|\.env$|\.pem$|\.key$')
DENY_TEXT = [re.compile(rb'(?i)\bINTERNAL\b.{0,40}\b(LGA|LaGuardia|replay)'), re.compile(rb'(?i)vasaviation'), re.compile(rb'(?i)audio_real_private'),
             re.compile(rb'(?i)would have prevented')]
BASE = {'4b': 'Qwen/Qwen3-4B-Instruct-2507', '1.7b': 'Qwen/Qwen3-1.7B'}

CODE_FILES = [
    'train/augment_asr_noise.py', 'train/local_lora.py', 'train/run_local_lora.sh', 'train/asr_noise_model.json',
    'eval/synthetic/generate_v4.py', 'eval/synthetic/train_ft.py', 'eval/synthetic/make_heldout5.py', 'eval/synthetic/render.py',
    'eval/synthetic/chart_ids.py', 'eval/synthetic/phraseology_7110.json',
    'gym/resources_servers/gmaps_parser/app.py', 'gym/resources_servers/gmaps_parser/tests/verifier_cases.jsonl', 'gym/run_offline_policy.py',
    'gym/build_dataset.py', 'gym/aggregate_by_set.py', 'venue/llm_fallback.py',
    'lib/gmaps_core/__init__.py', 'lib/gmaps_core/normalize.py', 'lib/gmaps_core/adapters.py', 'lib/gmaps_core/parser.py',
    'eval/parser_baselines.py', 'tools/budget_check.py', 'tools/scoreboard.py',
]
DATASET_FILES = {
    'train/pairs_asr.jsonl': 'eval/synthetic/train_ft/pairs_asr.jsonl',
    'train/ASR_NOISE_MANIFEST.json': 'eval/synthetic/train_ft/ASR_NOISE_MANIFEST.json',
    'train/MANIFEST.json': 'eval/synthetic/train_ft/MANIFEST.json',
    'train/asr_noise_model.json': 'train/asr_noise_model.json',
    'eval/heldout4_asr.jsonl': 'gym/environments/gmaps_parser/data/heldout4_asr.jsonl',
    'eval/heldout4_asr_prompt.jsonl': 'gym/environments/gmaps_parser/data/heldout4_asr_prompt.jsonl',
    'eval/heldout5_script.jsonl': 'gym/environments/gmaps_parser/data/heldout5_script.jsonl',
}
MODEL_FILES = ['adapter_config.json', 'adapter_model.safetensors', 'tokenizer.json', 'tokenizer_config.json', 'vocab.json', 'merges.txt',
               'special_tokens_map.json', 'added_tokens.json', 'chat_template.jinja', 'report.json', 'rendered_example.txt', 'README.md']


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def git_sha():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()
    except Exception:
        return 'unknown'


def scoreboard_table():
    p = REPO / 'docs/SCOREBOARD.md'
    if not p.exists():
        return '_(scoreboard not generated yet: run `python3 tools/scoreboard.py`)_'
    tbl = []
    for l in p.read_text().splitlines():   # the held-out table only (the first one); the other scoreboard tables are not card material
        if l.startswith('|'):
            tbl.append(l)
        elif tbl:
            break
    return '\n'.join(tbl) if tbl else '_(no rows yet)_'


CHARTS_PY = os.environ.get('CHARTS_PYTHON') or str(Path.home() / 'gymvenv313/bin/python')


def render_charts(dest):
    """tools/release_charts.py into <dest>/assets and <dest>/dataset/assets (needs a python with matplotlib), with --verify."""
    r = subprocess.run([CHARTS_PY, str(REPO / 'tools/release_charts.py'), '--out', str(dest / 'assets'), '--dataset-out', str(dest / 'dataset/assets'), '--verify'],
                       cwd=REPO, capture_output=True, text=True)
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip())
    return r.returncode == 0


def fig(name, alt, caption):
    return f'![{alt}](assets/{name}.png)\n\n*{caption}*'


DATE = r'(?:Oct|Sat|Sun)[a-z]*\.? \d{1,2}(?:[–-]\d{1,2})?(?: (?:evening|night|morning))?'
WHO = r'(?:Franco|[Cc]loud prep|weekend)'
DROP_PART = re.compile(rf'^(?:{WHO}|{WHO},? {DATE}|{DATE})$')


def scrub_comment(text):
    """Dates and dated attributions out of one comment or docstring; the technical note stays.
    '# N17: a flight number' -> '# N17: a flight number'; ' (round 3)' -> '(round 3)'."""
    def paren(m):
        inner = m.group(1)
        if not re.search(DATE, inner):
            return m.group(0)
        parts = [x.strip() for x in re.split(r'[,;:]', inner)]
        keep = [x for x in parts if x and not DROP_PART.match(x)]
        return f' ({", ".join(keep)})' if keep else ''
    new = re.sub(r' ?\(([^()]*)\)', paren, text)
    new = re.sub(rf'(?:{WHO},? )?(?:on )?\b{DATE}[:.,]? ?', '', new)
    if new != text:   # tidy only what the scrub touched: a dangling '. ' at the start, trailing blanks
        new = re.sub(r'^(\s*)\. ', r'\1', new)
        new = re.sub(r'[ \t]+(\n?)$', r'\1', new)
    return new


def scrub_py(src):
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    doc = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            e = node.body[0]
            if isinstance(e, ast.Expr) and isinstance(e.value, ast.Constant) and isinstance(e.value.value, str):
                doc.update(range(e.lineno - 1, e.end_lineno))
    for i in doc:
        lines[i] = scrub_comment(lines[i])
    toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)]
    edits = []   # (row, col, end_col, new text), applied right to left so earlier columns stay valid
    for k, tok in enumerate(toks):
        r, c = tok.start[0] - 1, tok.start[1]
        if r in doc:
            continue
        if tok.type == tokenize.COMMENT:
            edits.append((r, c, len(lines[r].rstrip('\n')), scrub_comment(tok.string)))
        elif tok.type == tokenize.STRING and tok.start[0] == tok.end[0]:
            # a human-readable label: plain single-line literal, 4+ words, a date in it, not a dict key
            m = re.fullmatch(r'([uU]?)([\'"])(.*)\2', tok.string, re.S)
            nxt = toks[k + 1].string if k + 1 < len(toks) else ''
            if m and len(m.group(3).split()) >= 4 and re.search(DATE, m.group(3)) and nxt != ':':
                edits.append((r, c, tok.end[1], m.group(1) + m.group(2) + scrub_comment(m.group(3)) + m.group(2)))
    for r, c, e, new in sorted(edits, reverse=True):
        lines[r] = lines[r][:c] + new + lines[r][e:]
    return ''.join(lines)


JSON_TEXT_KEYS = {'_readme', 'readme', 'cite', 'note', 'notes', 'comment', '_comment', 'description', 'why', 'use'}


def scrub_json(src):
    """Dates out of human-readable JSON fields (JSON_TEXT_KEYS values, at any depth); keys and every other value untouched.
    Edited as text so the file's formatting stays; the parsed result must equal the expected object."""
    pairs = []

    def walk(o, under):
        if isinstance(o, dict):
            return {k: walk(v, k in JSON_TEXT_KEYS) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v, under) for v in o]
        if isinstance(o, str) and under:
            new = scrub_comment(o)
            if new != o:
                pairs.append((o, new))
            return new
        return o
    want = walk(json.loads(src), False)
    text = src
    for old, new in pairs:
        for ascii_ in (False, True):
            enc = json.dumps(old, ensure_ascii=ascii_)[1:-1]
            if enc in text:
                text = text.replace(enc, json.dumps(new, ensure_ascii=ascii_)[1:-1]); break
    if json.loads(text) != want:
        raise ValueError('JSON scrub changed more than the human-readable fields')
    return text


def scrub_sh(src):
    out = []
    for l in src.splitlines(keepends=True):
        q, cut = '', None
        for i, ch in enumerate(l):   # first '#' outside quotes that starts a comment
            if ch in '\'"' and (not q or q == ch):
                q = '' if q else ch
            elif ch == '#' and not q and (i == 0 or l[i - 1] in ' \t') and not l.startswith('#!'):
                cut = i; break
        out.append(l if cut is None else l[:cut] + scrub_comment(l[cut:]))
    return ''.join(out)


def code_without_docstrings(src):
    """AST with docstrings blanked and every string constant passed through the scrub: equal before and after the scrub means
    nothing outside comments, docstrings and dated labels changed."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = scrub_comment(node.value)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            e = node.body[0]
            if isinstance(e, ast.Expr) and isinstance(e.value, ast.Constant) and isinstance(e.value.value, str):
                e.value.value = ''
    return ast.dump(tree)


def scrub_code(dest):
    """Date scrub of <dest>/code: comments, docstrings and dated human-readable string labels in *.py, comments in *.sh,
    human-readable fields in *.json. Code is never touched: the AST check below must hold, and every .py must compile."""
    problems = []
    for p in sorted((dest / 'code').rglob('*')):
        if p.suffix not in ('.py', '.sh', '.json'):
            continue
        src = p.read_text()
        try:
            new = scrub_py(src) if p.suffix == '.py' else scrub_sh(src) if p.suffix == '.sh' else scrub_json(src)
        except ValueError as e:
            problems.append(f'{p}: {e}'); continue
        if p.suffix == '.py':
            try:
                compile(new, str(p), 'exec')
                if code_without_docstrings(new) != code_without_docstrings(src):
                    problems.append(f'{p}: scrub changed code outside comments and docstrings'); continue
            except SyntaxError as e:
                problems.append(f'{p}: does not compile after the scrub ({e})'); continue
        if new != src:
            p.write_text(new)
    return problems


def _f3(x):
    return f'{x:.3f}' if isinstance(x, (int, float)) else '?'


def model_card(name, base, run_report, hf_user):
    rep = run_report or {}
    return f"""---
license: apache-2.0
base_model: {base}
library_name: peft
pipeline_tag: text-generation
language: [en]
tags: [lora, air-traffic-control, runway-incursion, structured-extraction, synthetic-data, nvidia-red-hat-small-models-hack]
datasets: [{(hf_user + '/' if hf_user else '')}{name}-data]
---

# {name}

A LoRA adapter on **{base}** that turns ONE transcribed air-traffic-control radio transmission into a five-field JSON record
`{{speaker_role, callsign, runway, instruction, taxiways}}`. It is the language-model fallback of GMAPS (Ground Movement
Awareness and Prediction System), a controller-side runway-incursion awareness prototype: a grammar parses most transmissions;
transmissions the grammar cannot type go to this model; a deterministic grounding rule then accepts or drops the answer.

**Decision support only; the controller decides.** Everything here was trained and evaluated on **SIMULATED** data: fictional
traffic, synthetic FAA JO 7110.65 phraseology, two public airport identifier lists (KLGA, KATL), used for runway and taxiway names only.
No real ATC audio or transcripts were used for training or evaluation. Made for the NVIDIA x Red Hat Small Models Hack, Raleigh;
trained and served on a DGX Spark (GB10).

## Results at a glance

{fig('record_exact', 'Record exact on three held-out sets', 'Record exact (all five fields right) on three held-out sets: untuned base, hand-written grammar and gmaps-parser, scored by the same verifier.')}

{fig('correct_and_accepted', 'Correct and accepted on three held-out sets', 'Correct AND accepted by the runtime grounding rule. The dashed line is what a perfect answer reaches on Whisper text: the ceiling the speech recognizer imposes.')}

{fig('base_vs_adapter', 'Untuned base vs gmaps-parser on Whisper text', 'Same base model, same server, same transmissions: the adapter against the untuned base, with the paired interval and the McNemar test.')}

### Exact values (held-out, same verifier for every system)

{scoreboard_table()}

`record exact` = all five fields match the scripted-scenario gold. `accepted by runtime` = the answer also passes the runtime
grounding rule on the transmission text (runway number heard, flight number heard, taxiway words heard, airline named). The two are
reported separately on purpose: a correct answer the runtime would reject is not a usable one. heldout5 was generated after the
parser freeze and is the blind set; heldout4_asr is Whisper large-v3-turbo text (the runtime input); heldout4_asr_prompt is the
same with an ICAO identifier prompt.

### NeMo Gym: orchestrated vs offline

{fig('gym_vs_offline', 'NeMo Gym orchestrated rollouts vs the offline runner', 'The same verifier packaged as a NeMo Gym resources server: orchestrated `gym eval run` rollouts reproduce the offline runner on 5 of 6 model x set pairs.')}

### More data scored lower

{fig('more_data_scored_lower', 'Submitted adapter vs the full-data adapter', 'A second adapter trained on all pairs for 2 epochs scored lower on every held-out set. Both were scored on one machine with the same task files; the 3,000-pair adapter is the one published here.')}

### Latency

{fig('latency', 'Parser latency per transmission', 'Per-transmission latency on held-out Whisper text: the grammar fast path vs this adapter served by vLLM (log scale).')}

## Training

| | |
|---|---|
| base | {base} |
| method | LoRA r={rep.get('lora_r', 16)}, alpha={rep.get('lora_alpha', 32)}, all linear projections, completion-only loss on the assistant JSON |
| data | {rep.get('rows', 'see dataset card')} pairs from `train/pairs_asr.jsonl` (clean / unprompted-Whisper-style / prompted-Whisper-style / harsh variants of 1,794 scripted transmissions) |
| epochs / steps | {rep.get('epochs', '?')} / {rep.get('steps', '?')} |
| hardware | {rep.get('device') or rep.get('gpu') or 'NVIDIA GB10 (DGX Spark)'}; peak {rep.get('peak_gpu_mem_gib', '?')} GiB; {rep.get('train_runtime_s', '?')} s |
| loss | first {_f3(rep.get('first_loss'))} -> last {_f3(rep.get('last_loss'))} (3 decimals, as on the scoreboard) |
| serving | vLLM v0.27.1, `--enable-lora --lora-modules gmaps-parser=<adapter>`; thinking off; `temperature 0` |

The Whisper-style noise model (`asr_noise_model.json`) was learned from development splits only (never from held-out), from
Whisper transcripts of locally synthesized speech. The recipe is `code/train/run_local_lora.sh`; the generator and augmenter are
in `code/`.

## Limits

- Five fields, one transmission at a time, airports whose identifier lists are supplied in the prompt. Not a general ATC model.
- Scores are on synthetic phraseology. Real-frequency performance is unknown and was not measured.
- Vehicle callsigns (e.g. "Truck 1", "Rescue 7") are outside the training data and the output schema's callsign pattern; the model refuses them. In GMAPS the grammar types those transmissions.
- Never claim this "understands ATC", and never claim it would have changed the outcome of any real event. "Prediction unavailable" is not "no conflict".
- Radio text is data: the model must never be given authority over anything; GMAPS binds it to five bounded tools.

{fig('demo_lines_vehicle_callsigns', 'Demo scenario lines: grammar, untuned base and gmaps-parser', "On scripted demo lines with vehicle callsigns (fictional scenarios), the adapter does not beat the untuned base; the grammar types those lines. Accepted = the runtime took the record; typed right = also the callsign and instruction match the ledger line.")}
"""


def dataset_figure():
    m = json.loads((REPO / DATASET_FILES['train/MANIFEST.json']).read_text())
    ov = m['overlap_with_heldout3_heldout4_after_exclusion']
    return (fig('dataset_composition', 'Training transmissions by instruction', 'The scripted transmissions behind the training pairs, by instruction (from `train/MANIFEST.json`).') +
            f"\n\nThe data is synthetic: FAA JO 7110.65 phraseology on fictional callsigns and traffic, with zero overlap with the held-out sets "
            f"(exact {ov['exact']}, normalized {ov['normalized']}; `train/MANIFEST.json`).")


def dataset_card(name, hf_user):
    return f"""---
license: apache-2.0
task_categories: [text-generation, token-classification]
language: [en]
tags: [air-traffic-control, synthetic, faa-7110-65, asr-noise, runway-incursion]
pretty_name: GMAPS parser pairs (synthetic ATC transmissions -> JSON)
size_categories: [1K<n<10K]
---

# {name}-data

{dataset_figure()}

Synthetic training and evaluation data for the GMAPS clearance parser (see the model card for `{(hf_user + '/' if hf_user else '')}{name}`).
**All of it is SIMULATED**: fictional callsigns and traffic, phraseology templated from FAA JO 7110.65, runway and taxiway names
from two public airport identifier lists (KLGA, KATL). No real ATC audio, no real transcripts, no audio files of any kind.

| file | rows | what |
|---|---|---|
| `train/pairs_asr.jsonl` | 7,065 | chat-format pairs `messages[system,user,assistant]`; 4 variants per scripted transmission: `clean`, `unprompted` (Whisper-style noise), `prompted` (Whisper with an identifier prompt), `harsh`; fields `id, src_id, airport, variant, groundable` |
| `train/ASR_NOISE_MANIFEST.json`, `train/MANIFEST.json` | | generator commits, seeds, pools, overlap checks (0 overlap with every held-out set) |
| `train/asr_noise_model.json` | | word/number rewrite statistics learned from DEV splits only (Whisper large-v3-turbo transcripts of locally synthesized speech) |
| `eval/heldout4_asr.jsonl` | 138 | Whisper text of synthetic speech, gold from the script; NeMo Gym task format |
| `eval/heldout4_asr_prompt.jsonl` | 138 | same transmissions, Whisper run with an ICAO identifier prompt |
| `eval/heldout5_script.jsonl` | 414 | scripted text, generated after the parser freeze (blind set) |

Gold taxiways use the callsign-aware rule (`gold_rule: fixed`): phonetic letters that belong to the callsign are not taxiways.
The verifier (`code/gym/resources_servers/gmaps_parser/app.py`) scores record exact match, per-field match and `groundable`.
Generate more with `code/eval/synthetic/train_ft.py` and `code/train/augment_asr_noise.py`.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', help='training run directory (adapter/, report.json, ...) from train/run_local_lora.sh')
    ap.add_argument('--name', default='gmaps-parser-qwen3-4b-lora')
    ap.add_argument('--base', default='4b', help='4b | 1.7b | a full HF id')
    ap.add_argument('--hf-user', default='')
    ap.add_argument('--out', default=str(Path.home() / 'gmaps_venue' / 'release'))
    ap.add_argument('--include-merged', action='store_true')
    ap.add_argument('--dry-run', action='store_true', help='no adapter: build dataset + code + cards only')
    ap.add_argument('--refresh', action='store_true', help='existing bundle: rewrite the cards, re-render the charts and re-hash MANIFEST.json; '
                                                         'model/, dataset/ data and code/ are not recopied')
    a = ap.parse_args()
    base = BASE.get(a.base, a.base)
    dest = Path(a.out) / a.name
    if a.refresh:
        return refresh(dest, a.name, base, a.hf_user)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / 'model').mkdir(parents=True); (dest / 'dataset').mkdir(); (dest / 'code').mkdir()
    problems, files = [], []

    def add(src, rel):
        src = Path(src)
        if not src.exists():
            problems.append(f'missing: {src}'); return
        if src.is_dir():
            for p in sorted(src.rglob('*')):
                if p.is_file():
                    add(p, f'{rel}/{p.relative_to(src)}')
            return
        if DENY_PATH.search(rel) or DENY_PATH.search(str(src)):
            problems.append(f'deny-listed path: {src} -> {rel}'); return
        if rel.lower().endswith(PC.AUDIO_EXT):
            problems.append(f'audio file refused: {rel}'); return
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d)
        files.append(rel)

    # model
    report = None
    if a.run:
        run = Path(a.run).expanduser()
        for f in MODEL_FILES:
            for cand in (run / 'adapter' / f, run / f):
                if cand.exists():
                    add(cand, f'model/{f}'); break
        if (run / 'report.json').exists():
            report = json.loads((run / 'report.json').read_text())
        if a.include_merged and (run / 'merged_fp16').is_dir():
            add(run / 'merged_fp16', 'model/merged_fp16')
        if not (dest / 'model/adapter_model.safetensors').exists():
            problems.append(f'no adapter_model.safetensors under {run} (adapter/ or top level)')
    elif not a.dry_run:
        problems.append('give --run <training run dir> or --dry-run')
    # dataset + code
    for rel, src in DATASET_FILES.items():
        add(REPO / src, f'dataset/{rel}')
    for src in CODE_FILES:
        add(REPO / src, f'code/{src}')
    # cards
    (dest / 'README.md').write_text(model_card(a.name, base, report, a.hf_user)); files.append('README.md')
    (dest / 'dataset/README.md').write_text(dataset_card(a.name, a.hf_user)); files.append('dataset/README.md')
    (dest / 'code/README.md').write_text(f'# code\n\nThe pieces needed to regenerate the data, retrain the adapter and re-score it, copied from the GMAPS repository at '
                                         f'`{git_sha()}`. Entry points: `train/run_local_lora.sh`, `train/augment_asr_noise.py`, `gym/run_offline_policy.py`, '
                                         f'`gym/resources_servers/gmaps_parser/app.py` (the NeMo Gym verifier). SIMULATED data only.\n'); files.append('code/README.md')
    problems += scrub_code(dest)
    if not render_charts(dest):
        problems.append('charts: tools/release_charts.py failed or --verify did not print CHARTS OK')
    files += [str(p.relative_to(dest)) for d in ('assets', 'dataset/assets') for p in sorted((dest / d).glob('*')) if p.is_file()]
    # content scan (secrets, deny-text, private-audio bytes)
    priv = PC.private_hashes()
    manifest = {}
    for rel in sorted(set(files)):
        p = dest / rel
        data = p.read_bytes()
        for prob in PC.check(rel, data, priv):
            if 'larger than 5 MB' in prob:
                continue   # pairs_asr.jsonl is 25 MB by design; the repo hook's size rule is for git, not for HF
            problems.append(f'{rel}: {prob}')
        if p.suffix in ('.md', '.py', '.sh', '.json', '.jsonl', '.txt', '.yaml', '.jinja') and p.name != 'adapter_model.safetensors':
            for rx in DENY_TEXT:
                m = rx.search(data)
                if m:
                    problems.append(f'{rel}: deny-listed text {m.group(0)[:60]!r}')
        manifest[rel] = {'sha256': sha(p), 'bytes': p.stat().st_size}
    (dest / 'MANIFEST.json').write_text(json.dumps({'SIMULATED': True, 'name': a.name, 'base_model': base,
                                                    'repo_commit': git_sha(), 'run': a.run, 'files': manifest}, indent=1))
    total = sum(v['bytes'] for v in manifest.values())
    print(f'{len(manifest)} files, {total / 1048576:.1f} MB -> {dest}')
    if problems:
        print('\nREFUSED: fix these before any upload')
        for p in problems:
            print('  - ' + p)
        if not a.dry_run or any('deny' in p or 'looks like' in p or 'audio' in p for p in problems):
            sys.exit(1)
    user = a.hf_user or '<hf-user>'
    print(f"""
Bundle is clean. Read README.md and dataset/README.md, then (nothing below has been run):
  hf repos create {user}/{a.name} --type model --private
  hf repos create {user}/{a.name}-data --type dataset --private
  hf upload {user}/{a.name} {dest} . --repo-type model --exclude "dataset/*"
  hf upload {user}/{a.name}-data {dest}/dataset . --repo-type dataset
  # flip both to public in the HF settings once the cards are right (the track says "publish it openly")""")


def refresh(dest, name, base, hf_user):
    """Cards + charts + MANIFEST hashes on an existing bundle (the same card and chart code as a full build)."""
    if not (dest / 'MANIFEST.json').exists():
        sys.exit(f'no bundle at {dest}')
    rep = dest / 'model/report.json'
    (dest / 'README.md').write_text(model_card(name, base, json.loads(rep.read_text()) if rep.exists() else None, hf_user))
    (dest / 'dataset/README.md').write_text(dataset_card(name, hf_user))
    scrub = scrub_code(dest)
    ok = render_charts(dest) and not scrub
    for p in scrub:
        print('  - ' + p)
    man = json.loads((dest / 'MANIFEST.json').read_text())
    for k in ('built', 'card_note'):   # no dates in the bundle; the hashes below are the record
        man.pop(k, None)
    man['files'] = {str(p.relative_to(dest)): {'sha256': sha(p), 'bytes': p.stat().st_size}
                    for p in sorted(dest.rglob('*')) if p.is_file() and p != dest / 'MANIFEST.json'}
    (dest / 'MANIFEST.json').write_text(json.dumps(man, indent=1))
    bad = [rel for rel in man['files'] for rx in DENY_TEXT if rel.endswith(('.md', '.json', '.svg')) and rx.search((dest / rel).read_bytes())]
    print(f"{len(man['files'])} files in MANIFEST.json -> {dest}" + (f'; deny-listed text in {bad}' if bad else ''))
    if not ok or bad:
        sys.exit(1)


if __name__ == '__main__':
    main()

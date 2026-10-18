"""Whisper-style noise augmentation of the train_ft split. SIMULATED. Text only.

  python3 train/augment_asr_noise.py                 # -> eval/synthetic/train_ft/pairs_asr.jsonl + ASR_NOISE_MANIFEST.json
  python3 train/augment_asr_noise.py --check-heldout-gold    # report only: callsign-suffix taxiway artefact in held-out truth

Why: on heldout4 the grammar (30 % all-fields) and the 35B fallback (27 %) are both weak on WHISPER text, the input the
runtime actually sees, while scripted text is easy. The parser must therefore train on text that looks like Whisper output.
This script learns how Whisper rewrites the scripted phraseology from the DEV splits only (dev, dev3, dev4: scripted text vs
the recorded *.asr.json and *.asr_prompt.json), never from any held-out split, and applies the same rewrites to train_ft.

Variants per transmission (gold target identical in all four):
  clean       the scripted text (taxiway gold corrected, see below)
  unprompted  Whisper without an initial prompt (the E1 path): number words -> digits, '26L' / '26 left', 'cleared'->'clear'...
  prompted    Whisper with the round-3 initial prompt (GMAPS_ASR_PROMPT_ICAO): flight numbers -> digits, runway words kept
  harsh       'unprompted' plus the error kinds seen on the 47 real clips: phonetic-letter homophones ('Keel'),
              'four'->'for', misspelled airlines, a dropped 'Roger', a stutter, stripped punctuation
Every row records whether the gold target still passes venue/llm_fallback.py's grounding on the noised text
(`groundable`). Training defaults to groundable rows only (an output the runtime would drop teaches nothing useful);
`--include-ungroundable` keeps them, for a later grounding-repair study.

Gold correction (W1): train_ft.py's spoken_taxiways() matched phonetic letters that belong to the CALLSIGN: the suffix of an
N-registration ('Citation two five zero five Alpha' -> taxiway A), its 'November' prefix (-> N), and the airline word 'Delta'
(-> D). Here the callsign's spoken spans are excluded before matching. The same artefact exists in the held-out truth files; --check-heldout-gold reports it and
changes nothing (that correction is Franco's call: DECISIONS N19).

System prompt: identical to train_ft.py (llm_fallback SYSTEM + the airport's JSON schema + "Output JSON only."). It needs the
airport's runway-end and taxiway lists: ChartIdentifiers when the build outputs exist (the Spark), else the committed
review files (RUNWAY_ENDS.json, taxiway_vocabulary.json). The manifest records which. Regenerate on the Spark before
training so the prompt is built from the same ChartIdentifiers the serving path uses.
"""
import argparse, collections, difflib, hashlib, json, random, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SYN = ROOT / 'eval/synthetic'
sys.path[:0] = [str(SYN), str(ROOT / 'venue'), str(ROOT / 'lib')]
import llm_fallback as LF                                                          # noqa: E402
from gmaps_core.normalize import NUMBER_WORDS, PHONETICS, group_form_number       # noqa: E402
from generate import say_num, say_twy, NATO                                       # noqa: E402

TRAIN = SYN / 'train_ft'
AIRPORTS = ('KLGA', 'KATL')
DEV_SPLITS = ('dev', 'dev3', 'dev4')
HELD_SPLITS = ('heldout3', 'heldout4', 'heldout5')
NUM = {k.lower(): v for k, v in NUMBER_WORDS.items()}
TEENS = {'ten': '10', 'eleven': '11', 'twelve': '12', 'thirteen': '13', 'fourteen': '14', 'fifteen': '15', 'sixteen': '16',
         'seventeen': '17', 'eighteen': '18', 'nineteen': '19'}
TENS = {'twenty': '20', 'thirty': '30', 'forty': '40', 'fifty': '50', 'sixty': '60', 'seventy': '70', 'eighty': '80', 'ninety': '90'}
GROUP_WORDS = set(TEENS) | set(TENS) | {'hundred', 'thousand'}
NUMBER_LIKE = set(NUM) | GROUP_WORDS
SUFFIX = {'left': 'L', 'right': 'R', 'center': 'C', 'centre': 'C'}
PHON = {k.lower(): v for k, v in PHONETICS.items()}
AIRLINE_WORDS = {w.lower() for w in LF.AIRLINES}
TYPE_WORDS = {'cessna', 'citation', 'november', 'piper', 'king', 'air', 'gulfstream', 'learjet', 'baron', 'bonanza'}
# Error kinds seen on the 47 real clips (venue/RESULTS.md) and in the unprompted dev transcripts. Applied by 'harsh' only.
HARSH_PHONETIC = {'kilo': ['Keel', 'Kilo'], 'mike': ['mic', 'Mike'], 'papa': ['Poppa'], 'juliet': ['Juliette'], 'quebec': ['Kebec'],
                  'alpha': ['Alfa'], 'foxtrot': ['Fox trot'], 'whiskey': ['Whisky'], 'yankee': ['Yank E'], 'golf': ['Gulf'],
                  'sierra': ['Sierra'], 'hotel': ['Hotel'], 'charlie': ['Charley'], 'tango': ['Tango'], 'zulu': ['Zoo Lou'],
                  'november': ['November'], 'echo': ['Echo'], 'delta': ['Delta'], 'bravo': ['Bravo'], 'lima': ['Lema'],
                  'romeo': ['Romeo'], 'oscar': ['Oscar'], 'uniform': ['Uniform'], 'victor': ['Victor'], 'india': ['India']}
HARSH_AIRLINE = {'endeavor': ['Endeavour'], 'jetblue': ['Jet Blue', 'jet blue'], 'frontier': ['Frontier'], 'cessna': ['Sessna', 'Cessna'],
                 'citation': ['Citation', 'Sitation'], 'rescue': ['Resco', 'RISCO'], 'southwest': ['South West'], 'spirit': ['Spirit']}


def norm_words(t):
    return re.sub(r"[^a-z0-9' ]", ' ', t.lower().replace('-', ' ')).split()


def tokens(text):
    """Word tokens keeping trailing punctuation on the word ('four,' stays one token); case preserved."""
    return text.split()


def core(tok):
    return re.sub(r'^[^A-Za-z0-9]+|[^A-Za-z0-9]+$', '', tok)


def with_punct_of(src_tok, new_core):
    lead = re.match(r'^[^A-Za-z0-9]*', src_tok).group(0)
    trail = re.search(r'[^A-Za-z0-9]*$', src_tok).group(0)
    return lead + new_core + trail


# ---------------------------------------------------------------- 1. learn how Whisper rewrites the script (dev splits only)
def aligned_dev_pairs(suffix):
    pairs = []
    for split in DEV_SPLITS:
        for sp in sorted((SYN / split).glob('*/*.scenario.json')):
            ap = sp.with_name(sp.name.replace('.scenario.json', f'.{suffix}.json'))
            if not ap.exists():
                continue
            asr = json.loads(ap.read_text())
            for tx in json.loads(sp.read_text())['transmissions']:
                a = asr.get(tx['id'])
                if a and a.get('text'):
                    pairs.append((tx['text'], a['text']))
    return pairs


def number_runs(words):
    """Maximal runs of number-like words in a normalized word list -> [(i, j)]."""
    runs, i = [], 0
    while i < len(words):
        if words[i] in NUMBER_LIKE:
            j = i
            while j < len(words) and words[j] in NUMBER_LIKE:
                j += 1
            runs.append((i, j)); i = j
        else:
            i += 1
    return runs


def run_class(words, i):
    prev = words[i - 1] if i > 0 else ''
    if prev == 'runway':
        return 'runway'
    if prev in AIRLINE_WORDS or prev in TYPE_WORDS:
        return 'flight'
    if prev in PHON:
        return 'taxiway_number'
    return 'other'


def learn(suffix):
    """Per run class: how often Whisper wrote digits; runway rendering formats; single-word substitutions with their rates."""
    pairs = aligned_dev_pairs(suffix)
    digits = collections.Counter(); seen = collections.Counter(); rwy_fmt = collections.Counter()
    word_sub = collections.Counter(); word_seen = collections.Counter()
    for script, asr in pairs:
        S, W = norm_words(script), norm_words(asr)
        sm = difflib.SequenceMatcher(a=S, b=W, autojunk=False)
        ops = sm.get_opcodes()
        for i, j in number_runs(S):
            cls = run_class(S, i); seen[cls] += 1
            # what did the ASR side produce for this span?
            out = []
            for op, i1, i2, j1, j2 in ops:
                if i2 <= i or i1 >= j:
                    continue
                out.extend(W[j1:j2] if op != 'equal' else S[max(i1, i):min(i2, j)])
            txt = ' '.join(out)
            if re.search(r'\d', txt):
                digits[cls] += 1
                if cls == 'runway':
                    nxt = S[j] if j < len(S) else ''
                    if nxt in SUFFIX:
                        rwy_fmt['26L' if re.search(r'\d[lrc]\b', txt) else '26 left' if re.search(r'\d+ (left|right|center)', txt) else 'other'] += 1
                    rwy_fmt['spaced' if re.search(r'\d \d', txt) else 'joined'] += 1
        for op, i1, i2, j1, j2 in ops:
            if op == 'replace' and i2 - i1 == 1 and not (S[i1] in NUMBER_LIKE):
                word_sub[(S[i1], ' '.join(W[j1:j2]))] += 1
        for w in S:
            if w not in NUMBER_LIKE:
                word_seen[w] += 1
    subs = {}
    for (a, b), c in word_sub.items():
        if c >= 2 and word_seen[a] and b and len(b) < 24:
            subs.setdefault(a, {})[b] = round(c / word_seen[a], 3)
    return {'pairs': len(pairs),
            'p_digits': {k: round(digits[k] / seen[k], 3) for k in seen},
            'seen': dict(seen),
            'runway_format': dict(rwy_fmt),
            'word_subs': subs}


# ---------------------------------------------------------------- 2. apply the learned rewrites to scripted text
def digits_of(run_words):
    """Digit string for a run of number words: 'four thirty five' -> '435', 'two six' -> '26', 'nine hundred' -> '900'."""
    up = [w.upper() for w in run_words]
    out, k = '', 0
    while k < len(up):
        g = group_form_number(up, k)
        if g:
            out += g[0]; k = g[1]
        elif up[k] in NUMBER_WORDS:
            out += NUMBER_WORDS[up[k]]; k += 1
        else:
            return None
    return out or None


def render(text, model, mode, rng, harsh=False):
    """Rewrite one scripted transmission the way Whisper (mode) would. Returns the new text."""
    toks = tokens(text)
    low = [core(t).lower() for t in toks]
    out = []
    i = 0
    p_dig = model['p_digits']
    fmt = model['runway_format']
    p_26L = fmt.get('26L', 0) / max(1, fmt.get('26L', 0) + fmt.get('26 left', 0))
    p_spaced = fmt.get('spaced', 0) / max(1, fmt.get('spaced', 0) + fmt.get('joined', 0))
    while i < len(toks):
        w = low[i]
        if w in NUMBER_LIKE:
            j = i
            while j < len(toks) and low[j] in NUMBER_LIKE:
                j += 1
            cls = run_class(low, i)
            d = digits_of(low[i:j])
            # 'niner' alone sometimes becomes '9 or' / 'nine or' (unprompted dev data)
            if d and rng.random() < p_dig.get(cls, p_dig.get('other', 0.5)):
                suffix_tok = None
                if cls == 'runway' and j < len(toks) and low[j] in SUFFIX:
                    suffix_tok = toks[j]
                if suffix_tok and rng.random() < p_26L:
                    body = d + SUFFIX[low[j]] + ('' if rng.random() < 0.7 else '').strip()
                    out.append(with_punct_of(suffix_tok, body)); i = j + 1; continue
                if rng.random() < p_spaced and len(d) > 1:
                    body = ' '.join(d)
                else:
                    body = d
                if harsh and cls == 'flight' and len(d) == 4 and rng.random() < 0.15:
                    body = d[:2] + '-' + d[2:]                       # 'American 29-59' (real clip)
                out.append(with_punct_of(toks[j - 1], body) if j - 1 > i else with_punct_of(toks[i], body))
                i = j; continue
            if w == 'niner' and mode == 'unprompted' and rng.random() < 0.15:
                out.append(with_punct_of(toks[i], rng.choice(['9 or', 'nine or', 'niner']))); i += 1; continue
            out.extend(toks[i:j]); i = j; continue
        subs = model['word_subs'].get(w)
        if subs:
            r = rng.random(); acc = 0.0
            for b, p in subs.items():
                acc += p
                if r < acc:
                    out.append(with_punct_of(toks[i], b)); break
            else:
                out.append(toks[i])
            i += 1; continue
        if harsh:
            if w in HARSH_PHONETIC and rng.random() < 0.22:
                out.append(with_punct_of(toks[i], rng.choice(HARSH_PHONETIC[w]))); i += 1; continue
            if w in HARSH_AIRLINE and rng.random() < 0.3:
                out.append(with_punct_of(toks[i], rng.choice(HARSH_AIRLINE[w]))); i += 1; continue
            if w == 'four' and rng.random() < 0.3:
                out.append(with_punct_of(toks[i], 'for')); i += 1; continue
        out.append(toks[i]); i += 1
    s = ' '.join(out)
    if harsh:
        if s.lower().startswith('roger') and rng.random() < 0.5:
            s = re.sub(r'^[Rr]oger[,.]?\s*', '', s)
        if rng.random() < 0.08:                                   # stutter / repeated chunk (real clip: 'truck one stop truck one stop')
            ws = s.split()
            if len(ws) > 3:
                k = rng.randrange(0, len(ws) - 1); ws[k:k] = ws[k:k + 2]; s = ' '.join(ws)
        if rng.random() < 0.5:
            s = re.sub(r'[,.]', '', s)
        if rng.random() < 0.3:
            s = s.lower()
    # Whisper capitalisation habits seen in dev: 'Runway Niner right', sentence case after a period
    if mode in ('unprompted', 'prompted') and rng.random() < 0.3:
        s = re.sub(r'\brunway\b', 'Runway', s)
    s = re.sub(r'\s+([,.])', r'\1', s)
    return s[0].upper() + s[1:] if s else s


# ---------------------------------------------------------------- 3. gold correction W1 (taxiways inside N-registrations)
def callsign_spans(words, callsign):
    """Token spans of the spoken callsign that must not be read as taxiways:
    N-registration  'november eight three five four lima'  -> the 'november' prefix (when digits follow) and the suffix letters after the digits
    airline         'delta twenty nine sixty eight'        -> the telephony word when it is a phonetic letter and a number follows"""
    spans = []
    m = re.fullmatch(r'N(\d{1,5})([A-Z]{0,2})', callsign or '')
    if m:
        for i, w in enumerate(words):
            if w == 'november' and i + 1 < len(words) and words[i + 1] in NUMBER_LIKE:
                spans.append((i, i + 1))
        if m.group(2):
            spoken = norm_words(say_num(m.group(1)) + ' ' + ' '.join(NATO[c] for c in m.group(2)))
            found = False
            for i in range(len(words) - len(spoken) + 1):
                if words[i:i + len(spoken)] == spoken:
                    spans.append((i, i + len(spoken))); found = True
            if not found:
                suf = [NATO[c].lower() for c in m.group(2)]
                for i in range(1, len(words) - len(suf) + 1):
                    if words[i:i + len(suf)] == suf and words[i - 1] in NUMBER_LIKE:
                        spans.append((i, i + len(suf)))
    m = re.fullmatch(r'([A-Z]{3})(\d{1,4})', callsign or '')
    if m:
        tele = (LF.TELEPHONY.get(m.group(1)) or '').lower()
        if tele in PHON:
            for i, w in enumerate(words):
                if w == tele and i + 1 < len(words) and words[i + 1] in NUMBER_LIKE:
                    spans.append((i, i + 1)); break
    return spans


def spoken_taxiways_fixed(text, vocab, callsign):
    words = norm_words(text)
    excl = callsign_spans(words, callsign)
    hits = []
    for t in vocab:
        sw = norm_words(say_twy(t))
        for i in range(len(words) - len(sw) + 1):
            if words[i:i + len(sw)] == sw:
                if any(not (i + len(sw) <= a or i >= b) for a, b in excl):
                    continue
                hits.append((i, -len(sw), t))
    out, used = [], set()
    for i, neg, t in sorted(hits):
        span = set(range(i, i - neg))
        if span & used:
            continue
        used |= span; out.append(t)
    return out[:4]


# ---------------------------------------------------------------- 4. airport schema / system prompt
def airport_lists(icao):
    base = ROOT / 'data/airports' / icao.lower()
    try:
        from chart_ids import ChartIdentifiers
        A = ChartIdentifiers(icao)
        return sorted({e['identifier'] for e in A.ends.values()}), sorted(A.vocab), 'chart_identifiers'
    except Exception:
        ends = json.loads((base / 'review/RUNWAY_ENDS.json').read_text())['ends']
        vocab = json.loads((base / 'review/taxiway_vocabulary.json').read_text())['taxiways']
        return sorted(e['id'] for e in ends), sorted(vocab), 'review_json_fallback (regenerate on the Spark)'


def system_for(fb):
    return fb.system + '\nJSON schema (allowed values for this airport):\n' + json.dumps(fb.schema, separators=(',', ':')) + '\nOutput JSON only.'


def heldout_norm_texts():
    s = set()
    for split in HELD_SPLITS:
        for p in (SYN / split).glob('*/*.json'):
            if p.name.endswith('.scenario.json'):
                for tx in json.loads(p.read_text())['transmissions']:
                    s.add(' '.join(norm_words(tx['text'])))
            elif re.search(r'\.asr(_prompt)?(_words)?\.json$', p.name):
                for v in json.loads(p.read_text()).values():
                    if isinstance(v, dict) and v.get('text'):
                        s.add(' '.join(norm_words(v['text'])))
    return s


def check_heldout_gold():
    """Report only. The evaluator (eval/parser_baselines.py) computes gold taxiways on the fly with train_ft.spoken_taxiways();
    this counts how many held-out golds would change under the callsign-aware rule. Nothing is written."""
    import train_ft as TF
    rep = {}
    for split in HELD_SPLITS:
        n = diff = 0; ex = []
        for icao in AIRPORTS:
            vocab = sorted(json.loads((ROOT / 'data/airports' / icao.lower() / 'review/taxiway_vocabulary.json').read_text())['taxiways'])
            for sp in sorted((SYN / split / icao.lower()).glob('*.scenario.json')):
                tr = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.truth.json')).read_text())
                texts = {t['id']: t['text'] for t in json.loads(sp.read_text())['transmissions']}
                for t in tr['transmissions']:
                    text = texts[t['id']]; cs = (t['fields'].get('callsigns') or [None])[0]
                    old = TF.spoken_taxiways(text, vocab); new = spoken_taxiways_fixed(text, vocab, cs)
                    n += 1
                    if old != new:
                        diff += 1
                        if len(ex) < 3:
                            ex.append({'id': t['id'], 'text': text, 'callsign': cs, 'gold_now': old, 'gold_fixed': new})
        rep[split] = {'transmissions': n, 'gold_taxiways_that_change': diff, 'examples': ex}
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(TRAIN / 'pairs_asr.jsonl'))
    ap.add_argument('--seed', type=int, default=20261017)
    ap.add_argument('--variants', default='clean,unprompted,prompted,harsh')
    ap.add_argument('--include-ungroundable', action='store_true')
    ap.add_argument('--check-heldout-gold', action='store_true')
    a = ap.parse_args()
    if a.check_heldout_gold:
        print(json.dumps(check_heldout_gold(), indent=1)); return
    rng = random.Random(a.seed)
    models = {'unprompted': learn('asr'), 'prompted': learn('asr_prompt')}
    (HERE / 'asr_noise_model.json').write_text(json.dumps({'learned_from': list(DEV_SPLITS), 'never_from': list(HELD_SPLITS), 'models': models}, indent=1) + '\n')
    held = heldout_norm_texts()
    variants = [v for v in a.variants.split(',') if v]
    rows, stats = [], collections.defaultdict(collections.Counter)
    prompt_src = {}
    fixed = 0
    for icao in AIRPORTS:
        runways, vocab, src = airport_lists(icao); prompt_src[icao] = src
        fb = LF.LLMFallback(runways, vocab)
        sysmsg = system_for(fb)
        for tp in sorted((TRAIN / icao.lower()).glob('*.truth.json')):
            sc = json.loads(tp.with_name(tp.name.replace('.truth.json', '.scenario.json')).read_text())
            texts = {t['id']: t for t in sc['transmissions']}
            for t in json.loads(tp.read_text())['transmissions']:
                tx = texts[t['id']]; target = dict(t['target'])
                tw = spoken_taxiways_fixed(tx['text'], vocab, target['callsign'])
                if tw != target['taxiways']:
                    fixed += 1; target['taxiways'] = tw
                for v in variants:
                    if v == 'clean':
                        text = tx['text']
                    elif v == 'harsh':
                        text = render(tx['text'], models['unprompted'], 'unprompted', rng, harsh=True)
                    else:
                        text = render(tx['text'], models[v], v, rng)
                    if ' '.join(norm_words(text)) in held:
                        stats[v]['dropped_heldout_overlap'] += 1; continue
                    ok, reason = fb.validate(dict(target), text)
                    groundable = ok is not None and (ok.get('actionable') or reason == 'no actionable content')
                    stats[v]['rows'] += 1; stats[v]['groundable' if groundable else 'ungroundable'] += 1
                    if not groundable:
                        stats[v]['ungroundable_reasons'] = stats[v].get('ungroundable_reasons', 0)
                        stats[f'{v}_reasons'][reason] += 1
                    if not groundable and not a.include_ungroundable:
                        continue
                    rows.append({'id': f"{t['id']}__{v}", 'src_id': t['id'], 'airport': icao, 'variant': v, 'groundable': groundable,
                                 'messages': [{'role': 'system', 'content': sysmsg},
                                              {'role': 'user', 'content': f'Transmission: """{text}"""'},
                                              {'role': 'assistant', 'content': json.dumps(target, separators=(',', ':'))}]})
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w') as f:
        for r in rows:
            f.write(json.dumps(r, separators=(',', ':')) + '\n')
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    man = {'SIMULATED': True, 'TRAINING_ONLY': True, 'text_only': True, 'source_split': 'eval/synthetic/train_ft (scenario + truth)',
           'generator': 'train/augment_asr_noise.py', 'generator_commit': commit, 'seed': a.seed, 'variants': variants,
           'noise_model': {'file': 'train/asr_noise_model.json', 'learned_from': list(DEV_SPLITS), 'never_from': list(HELD_SPLITS),
                           'dev_pairs': {k: m['pairs'] for k, m in models.items()}, 'p_digits': {k: m['p_digits'] for k, m in models.items()}},
           'gold_correction_W1': {'taxiways_inside_N_registration_removed': fixed, 'note': 'train_ft targets only; held-out truth untouched (DECISIONS N19)'},
           'prompt_source': prompt_src, 'rows_written': len(rows), 'include_ungroundable': a.include_ungroundable,
           'per_variant': {k: dict(v) for k, v in stats.items()},
           'overlap_with_heldout_after_drop': 0,
           'pairs_asr_jsonl': {'sha256': hashlib.sha256(out.read_bytes()).hexdigest(), 'bytes': out.stat().st_size, 'tracked_in_git': False,
                               'why': 'over the 5 MB pre-commit limit; regenerate with this script (deterministic for a seed)'},
           'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (TRAIN / 'ASR_NOISE_MANIFEST.json').write_text(json.dumps(man, indent=1) + '\n')
    print(json.dumps({k: man[k] for k in ('rows_written', 'per_variant', 'gold_correction_W1', 'prompt_source', 'noise_model')}, indent=1))
    print(f"overlap: 0  rows: {len(rows)}  out: {out}")


if __name__ == '__main__':
    main()

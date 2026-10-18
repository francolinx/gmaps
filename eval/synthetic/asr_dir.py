"""Whisper large-v3-turbo on synthetic scenario audio (SIMULATED). Runs INSIDE the throwaway offline container, like
venue/asr_clips.py: /gmaps (repo, ro)  /audio (synthetic clips, ro)  /models/whisper (ro)  /out (results).

  docker run --rm --network none --gpus all ... --entrypoint python3 <vllm image> /gmaps/eval/synthetic/asr_dir.py
Writes /out/asr.json: {transmission id: {'text', 'asr_s'}} (text only).
With env GMAPS_ASR_PROMPT_ICAO=<ICAO> (round 3): Whisper initial prompt = airline telephony names + NATO
taxiway words + that airport's runway designators (chart/NASR vocabulary), as one short ATC-style paragraph.
"""
import json, os, re, sys, time
from pathlib import Path

sys.path.insert(0, '/gmaps/lib')
import librosa                                   # noqa: E402
import numpy as np                               # noqa: E402
import soundfile as sf                           # noqa: E402
from gmaps_core.adapters import transcribe, transcribe_words   # noqa: E402

MODEL = '/models/whisper'
DIG = dict(zip('0123456789', 'zero one two three four five six seven eight niner'.split()))


def asr_prompt(icao):
    from gmaps_core.parser import AIRLINES
    import csv
    ends = sorted({r['RWY_END_ID'] for r in csv.DictReader(open(f'/gmaps/data/airports/{icao.lower()}/sources/nasr/APT_RWY_END.csv', newline='', encoding='utf-8'))
                   if r['LAT_DECIMAL'].strip() and not r['RWY_END_ID'].startswith('H')})
    say = lambda e: ' '.join(DIG[c] for c in re.match(r'0?(\d+)', e).group(1)) + {'L': ' left', 'R': ' right', 'C': ' center'}.get(e[-1], '')
    names = [k.title().replace('Jetblue', 'JetBlue') for k in AIRLINES if k not in ('SIMAIR', 'AIRCANADA')] + ['Air Canada']
    nato = 'Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliett Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey X-ray Yankee Zulu'
    return (f"{icao} tower. {', '.join(names)}. Runway {', runway '.join(say(e) for e in ends)}. Taxiway {nato}. "
            "Cleared to land, cleared for takeoff, line up and wait, hold short, cross runway, roger, wilco.")


PROMPT = asr_prompt(os.environ['GMAPS_ASR_PROMPT_ICAO']) if os.environ.get('GMAPS_ASR_PROMPT_ICAO') else None
if PROMPT:
    print('prompt:', PROMPT, flush=True)
out = {}
clips = sorted(Path('/audio').glob('*.wav'))
if clips:   # cold start (model load) excluded from the per-clip times, as in venue/asr_clips.py
    a0, _ = sf.read(str(clips[0]), dtype='float32', always_2d=True); transcribe({'raw': np.ascontiguousarray(a0.mean(axis=1)), 'sampling_rate': 16000}, MODEL, prompt=PROMPT)
for p in clips:
    a, sr = sf.read(str(p), dtype='float32', always_2d=True); a = a.mean(axis=1)
    if sr != 16000:
        a = librosa.resample(a, orig_sr=sr, target_sr=16000)
    t0 = time.perf_counter()
    if os.environ.get('GMAPS_ASR_WORDS'):   # round 4: per-word confidence (runway token of a readback)
        r = transcribe_words({'raw': np.ascontiguousarray(a, dtype=np.float32), 'sampling_rate': 16000}, MODEL, prompt=PROMPT)
    else:
        r = transcribe({'raw': np.ascontiguousarray(a, dtype=np.float32), 'sampling_rate': 16000}, MODEL, prompt=PROMPT)
    text = r['text']
    out[p.stem] = {'text': text.strip(), 'asr_s': round(time.perf_counter() - t0, 3), **({'words': r['words']} if 'words' in r else {})}
    print(p.stem, out[p.stem]['asr_s'], out[p.stem]['text'][:80], flush=True)
Path('/out/asr.json').write_text(json.dumps(out, indent=1))

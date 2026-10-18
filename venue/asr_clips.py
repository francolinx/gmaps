"""Transcribe owned re-voiced fictional clips with Whisper large-v3-turbo (venue code).

Runs INSIDE a throwaway container from the existing nemoclaw vLLM image (no network, read-only mounts):
  /gmaps (repo, ro)  /audio (clips, ro)  /models/whisper (weights, ro)  /out (results)
The image has no ffmpeg, so clips are decoded with soundfile and resampled to 16 kHz with librosa, then passed to
adapters.transcribe() as {'raw', 'sampling_rate'}. Misheard words are kept exactly as heard.
"""
import json, re, statistics, sys, time
from pathlib import Path

sys.path.insert(0, '/gmaps/lib')
import librosa                                          # noqa: E402
import numpy as np                                      # noqa: E402
import soundfile as sf                                  # noqa: E402
import torch                                            # noqa: E402
from gmaps_core.adapters import transcribe              # noqa: E402
from gmaps_core.parser import parse                     # noqa: E402
from gmaps_core.sim.simmap import SimAirport            # noqa: E402

MODEL = '/models/whisper'
OUT = Path('/out')


def load(p):
    a, sr = sf.read(str(p), dtype='float32', always_2d=True)
    a = a.mean(axis=1)
    if sr != 16000:
        a = librosa.resample(a, orig_sr=sr, target_sr=16000)
    return {'raw': np.ascontiguousarray(a, dtype=np.float32), 'sampling_rate': 16000}, sr, len(a) / 16000


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else ''
    clips = sorted(Path('/audio').glob(f'{prefix}*.wav'), key=lambda p: (p.stem.rsplit('_', 1)[0], float(p.stem.rsplit('_', 1)[1])))
    ref = {}
    for f in Path('/gmaps/episodes').glob('*.transcript.json'):
        d = json.loads(f.read_text())
        for e in d['events']:
            ref[f"{d['id']}_{e['t']}"] = e['text_clean']
    ap = SimAirport()
    t0 = time.perf_counter()
    first = load(clips[0])[0]
    transcribe(first, MODEL)                             # cold: model load + first decode (reported separately)
    torch.cuda.synchronize()
    cold_s = round(time.perf_counter() - t0, 3)
    rows = []
    for p in clips:
        ep, t = p.stem.rsplit('_', 1)
        t = float(t)
        audio, sr_in, dur = load(p)
        torch.cuda.synchronize()
        s0 = time.perf_counter()
        r = transcribe(audio, MODEL)
        torch.cuda.synchronize()
        asr_s = time.perf_counter() - s0
        ev = parse(r['text'], 'XSIM', resolver=ap, event_time=t, source={'id': p.name, 'start_s': t, 'end_s': None})
        rows.append({'clip': p.name, 'episode': ep, 't': t, 'audio_s': round(dur, 2), 'sr_in': sr_in,
                     'asr_text_raw': r['text'], 'script_text': ref.get(p.stem),
                     'asr_s': round(asr_s, 3), 'rtf': round(asr_s / dur, 3),
                     'parse': {k: ev.get(k) for k in ('event_id', 'speech_act', 'kind', 'actor_candidates', 'runway',
                                                     'crossing_at', 'hold_constraints', 'parse_path', 'asr_repairs_applied')},
                     'parse_uncertainty': ev.get('uncertainty')})
        print(f"{p.name:<40} {asr_s:6.3f}s  {r['text']!r}", flush=True)
    lat = [x['asr_s'] for x in rows]
    summary = {'model_dir': '~/gmaps_models/whisper-large-v3-turbo', 'device': torch.cuda.get_device_name(0),
               'torch': torch.__version__, 'n': len(rows), 'cold_load_plus_first_s': cold_s,
               'warm_asr_s_median': round(statistics.median(lat), 3), 'warm_asr_s_min': min(lat), 'warm_asr_s_max': max(lat),
               'note': 'Warm per-clip wall time of adapters.transcribe() on GPU, excluding file decode. Clips are re-voiced fictional lines.'}
    (OUT / 'asr_results.json').write_text(json.dumps({'summary': summary, 'clips': rows}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()

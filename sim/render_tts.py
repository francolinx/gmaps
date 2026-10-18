"""Render transmissions to synthetic radio audio (SIMULATED). Runs in ~/gmaps_models/tts-venv (Kokoro via kokoro-onnx, local).

  ~/gmaps_models/tts-venv/bin/python sim/render_tts.py JOBS.json
JOBS.json: [{"text", "voice", "speed", "seed", "out"}]; each "out" wav (16 kHz) is written once and then reused (cache).
Same TTS and radio channel as eval/synthetic/render.py (band-pass, pink noise at the dev SNR range, squelch bursts at key-up/down).
Audio is written only to the cache directory the runner gives (outside the repo); it is never committed.
"""
import json, random, sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'eval/synthetic'))
import render as R   # noqa: E402  (eval/synthetic/render.py: Kokoro, radio(), resample(), PROFILES)


def main():
    jobs = json.loads(Path(sys.argv[1]).read_text())
    k = R.Kokoro(str(R.MODELS / 'kokoro-v1.0.onnx'), str(R.MODELS / 'voices-v1.0.bin'))
    snr = R.PROFILES['dev']['snr_db']
    n = 0
    for j in jobs:
        out = Path(j['out'])
        if out.exists():
            continue
        rng = random.Random(j['seed'])
        s, sr = k.create(j['text'], voice=j['voice'], speed=j['speed'], lang='en-us')
        y, db = R.radio(R.resample(np.asarray(s, dtype=np.float32), sr), rng, snr)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix('.tmp.wav')
        sf.write(tmp, y, 16000)
        tmp.rename(out)
        out.with_suffix('.json').write_text(json.dumps({'text': j['text'], 'voice': j['voice'], 'speed': j['speed'], 'snr_db': db,
                                                        'seconds': round(len(y) / 16000, 2), 'SIMULATED': True}) + '\n')
        n += 1
    print(f'rendered {n}, cached {len(jobs) - n}', flush=True)


if __name__ == '__main__':
    main()

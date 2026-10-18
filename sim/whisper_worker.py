"""Whisper worker for the simulator (SIMULATED audio only). Runs INSIDE the same throwaway, offline container as the demo's ASR
(the existing nemoclaw vLLM image, --network none, read-only repo and model mounts; see sim/runner.py). Loads Whisper
large-v3-turbo once, then transcribes each end-of-speech segment the runner drops in /work/in (<n>.wav, written atomically) and
writes /work/out/<n>.json ({text, asr_s}). Exits when /work/STOP appears. No prompt (as the E1 demo path), unless the runner
passes GMAPS_SIM_PROMPT (sim/options.whisper_prompt, --whisper-prompt): then that round-3 initial prompt is used for every clip.
"""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, '/gmaps/lib')
import numpy as np                          # noqa: E402
import soundfile as sf                      # noqa: E402
from gmaps_core.adapters import transcribe  # noqa: E402

MODEL, W = '/models/whisper', Path('/work')
PROMPT = os.environ.get('GMAPS_SIM_PROMPT') or None


def main():
    (W / 'out').mkdir(exist_ok=True)
    warm = np.zeros(16000, dtype=np.float32)
    transcribe({'raw': warm, 'sampling_rate': 16000}, MODEL, prompt=PROMPT)   # model load + first decode
    (W / 'out' / 'READY').write_text('ready\n')
    done = set()
    while not (W / 'STOP').exists():
        todo = sorted(p for p in (W / 'in').glob('*.wav') if p.name not in done)
        if not todo:
            time.sleep(0.02)
            continue
        for p in todo:
            a, sr = sf.read(str(p), dtype='float32')
            t0 = time.perf_counter()
            r = transcribe({'raw': np.ascontiguousarray(a, dtype=np.float32), 'sampling_rate': sr}, MODEL, prompt=PROMPT)
            asr_s = round(time.perf_counter() - t0, 3)
            tmp = W / 'out' / (p.stem + '.tmp')
            tmp.write_text(json.dumps({'text': (r.get('text') or '').strip(), 'asr_s': asr_s}))
            tmp.rename(W / 'out' / (p.stem + '.json'))
            done.add(p.name)


if __name__ == '__main__':
    main()

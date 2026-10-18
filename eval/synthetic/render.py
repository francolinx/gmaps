"""Render synthetic scenarios to audio (SIMULATED): local TTS + a radio channel. Runs in ~/gmaps_models/tts-venv.

  ~/gmaps_models/tts-venv/bin/python eval/synthetic/render.py --icao KATL --split dev|heldout

TTS: Kokoro-82M (weights Apache-2.0) via kokoro-onnx (MIT), fully local (~/gmaps_models/kokoro/). Its phonemizer
(phonemizer, espeak-ng) is GPL-3.0: used locally at render time, never redistributed or committed.
Radio channel per transmission: 16 kHz, 300-3400 Hz band-pass, random gain, pink noise (dev 10-20 dB SNR, held-out 6-14 dB), mild clipping,
squelch noise bursts at key-up/key-down. One controller voice per airport, a different voice per aircraft.
Audio goes to ~/gmaps_venue/synthetic/<split>/<icao>/ (outside the repo; synthetic, not real ATC). The manifest with voices,
SNR and durations is written next to the scenarios (text only).
"""
import argparse, json, random
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

ROOT = Path(__file__).resolve().parents[2]
MODELS = Path.home() / 'gmaps_models/kokoro'
OUT = Path.home() / 'gmaps_venue/synthetic'
PROFILES = {   # held-out: voices never used in dev, faster speech, harder noise
    'dev': {'atc': ['am_michael', 'af_sarah', 'bm_george'], 'pilots': ['am_adam', 'am_eric', 'am_liam', 'am_onyx', 'af_bella', 'af_nicole', 'bm_lewis', 'bf_emma', 'am_fenrir', 'af_kore'],
            'snr_db': (10, 20), 'speed': (1.05, 1.3)},
    'heldout': {'atc': ['am_echo', 'bf_isabella', 'bm_daniel'], 'pilots': ['am_puck', 'af_alloy', 'af_aoede', 'af_jessica', 'af_river', 'bm_fable', 'bf_alice', 'bf_lily', 'af_nova', 'af_sky'],
                'snr_db': (6, 14), 'speed': (1.15, 1.4)},
    'dev3': {'atc': ['am_michael', 'af_sarah', 'bm_george'], 'pilots': ['am_adam', 'am_eric', 'am_liam', 'am_onyx', 'af_bella', 'af_nicole', 'bm_lewis', 'bf_emma', 'am_fenrir', 'af_kore'],
             'snr_db': (10, 20), 'speed': (1.05, 1.3)},
    # v3 held-out: every English voice NOT used in dev3, British voices with en-gb phonemes, a narrower band,
    # harder noise and a heterodyne whistle on some transmissions
    'heldout3': {'atc': ['am_echo', 'bf_isabella', 'am_santa'], 'pilots': ['am_puck', 'af_alloy', 'af_aoede', 'af_jessica', 'af_river', 'bm_fable', 'bf_alice', 'bf_lily', 'af_nova', 'af_sky', 'af_heart', 'bm_daniel'],
                 'snr_db': (4, 12), 'speed': (1.1, 1.45), 'band_hz': (400, 2800), 'whistle_p': 0.3, 'gb_lang_for_b_voices': True},
    'dev4': {'atc': ['am_michael', 'af_sarah', 'bm_george'], 'pilots': ['am_adam', 'am_eric', 'am_liam', 'am_onyx', 'af_bella', 'af_nicole', 'bm_lewis', 'bf_emma', 'am_fenrir', 'af_kore'],
             'snr_db': (10, 20), 'speed': (1.05, 1.3)},
    # v4 held-out (round 3): pilots are Kokoro's non-English voices speaking English (accents no dev set has); ATC voices never
    # used in dev; noise 5-15 dB, 350-3000 Hz, a whistle on some transmissions and short dropouts (50-150 ms) on others
    'heldout4': {'atc': ['bf_isabella', 'am_santa', 'af_heart'], 'pilots': ['ef_dora', 'em_alex', 'em_santa', 'ff_siwis', 'hf_alpha', 'hf_beta', 'hm_omega', 'hm_psi',
                                                                            'if_sara', 'im_nicola', 'pf_dora', 'pm_alex', 'pm_santa'],
                 'snr_db': (5, 15), 'speed': (1.05, 1.35), 'band_hz': (350, 3000), 'whistle_p': 0.15, 'dropout_p': 0.2},
}
LICENCES = {'kokoro_weights': 'Kokoro-82M v1.0, Apache-2.0', 'kokoro_onnx': 'kokoro-onnx 0.6.1, MIT', 'onnxruntime': 'MIT',
            'phonemizer': 'phonemizer 3.4.0, GPL-3.0', 'espeak_ng': 'espeak-ng (bundled by espeakng-loader 0.2.4), GPL-3.0',
            'use': 'Accepted by Franco (Oct 9) for LOCAL test-audio generation only. Never shipped or distributed with GMAPS; the venv and models live outside the repo (~/gmaps_models/).'}


def resample(x, sr, to=16000):
    n = int(round(len(x) * to / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def radio(x, rng, snr_range, sr=16000, band=(300, 3400), whistle_p=0.0, dropout_p=0.0):
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / sr)
    X[(f < band[0]) | (f > band[1])] = 0
    y = np.fft.irfft(X, n=len(x))
    y = y / (np.max(np.abs(y)) + 1e-9) * rng.uniform(0.5, 0.9)
    snr = rng.uniform(*snr_range)
    w = np.fft.rfft(rng_normal(rng, len(y))); w = w / np.sqrt(np.maximum(np.fft.rfftfreq(len(y), 1 / sr), 50))   # pink-ish
    noise = np.fft.irfft(w, n=len(y)); noise = noise / (np.std(noise) + 1e-9) * np.std(y) / (10 ** (snr / 20))
    if whistle_p and rng.random() < whistle_p:   # heterodyne whistle (two carriers beating), held-out v3 only
        y = y + np.sin(2 * np.pi * rng.uniform(900, 2400) * np.arange(len(y)) / sr) * np.std(y) * rng.uniform(0.15, 0.4)
    y = np.clip(y + noise, -0.8, 0.8)
    if dropout_p and rng.random() < dropout_p:   # a short signal dropout (held-out v4 only)
        n = int(rng.uniform(0.05, 0.15) * sr); k = rng.randint(0, max(0, len(y) - n)); y[k:k + n] = noise[k:k + n] * 0.3
    sq = lambda n: (rng_normal(rng, n) * 0.25).astype(np.float32)
    return np.concatenate([sq(int(0.12 * sr)), y.astype(np.float32), sq(int(0.18 * sr))]), round(snr, 1)


def rng_normal(rng, n):
    return np.array([rng.gauss(0, 1) for _ in range(n)], dtype=np.float32) if n < 2000 else np.random.default_rng(rng.randint(0, 2**31)).standard_normal(n).astype(np.float32)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True); ap.add_argument('--split', choices=tuple(PROFILES), default='dev'); ap.add_argument('--seed', type=int, default=9101)
    a = ap.parse_args(); icao = a.icao.lower(); rng = random.Random(a.seed + {'dev': 0, 'heldout': 1000, 'dev3': 2000, 'heldout3': 3000, 'dev4': 4000, 'heldout4': 5000}[a.split]); pr = PROFILES[a.split]
    ATC_VOICES, PILOT_VOICES = pr['atc'], pr['pilots']
    k = Kokoro(str(MODELS / 'kokoro-v1.0.onnx'), str(MODELS / 'voices-v1.0.bin'))
    atc = rng.choice(ATC_VOICES); manifest = {'icao': icao.upper(), 'split': a.split, 'SIMULATED': True, 'tts': 'Kokoro-82M v1.0 via kokoro-onnx (local)', 'licences': LICENCES,
                                              'profile': pr, 'atc_voice': atc, 'clips': {}}
    out = OUT / a.split / icao; out.mkdir(parents=True, exist_ok=True)
    for sp in sorted((ROOT / 'eval/synthetic' / a.split / icao).glob('*.scenario.json')):
        sc = json.loads(sp.read_text()); voices = {}
        for tx in sc['transmissions']:
            speaker_key = 'ATC' if tx['speaker'] == 'ATC' else tx.get('speaker_ref') or tx['text'].rsplit(',', 1)[-1].strip(' .')
            if tx['speaker'] == 'ATC':
                v = atc
            else:
                v = voices.setdefault(speaker_key, rng.choice([x for x in PILOT_VOICES if x not in voices.values()] or PILOT_VOICES))
            lang = 'en-gb' if pr.get('gb_lang_for_b_voices') and v.startswith('b') else 'en-us'
            s, sr = k.create(tx['text'], voice=v, speed=rng.uniform(*pr['speed']), lang=lang)
            y, snr = radio(resample(np.asarray(s, dtype=np.float32), sr), rng, pr['snr_db'], band=pr.get('band_hz', (300, 3400)), whistle_p=pr.get('whistle_p', 0.0), dropout_p=pr.get('dropout_p', 0.0))
            fn = out / f"{tx['id']}.wav"; sf.write(fn, y, 16000)
            manifest['clips'][tx['id']] = {'voice': v, 'lang': lang, 'snr_db': snr, 'seconds': round(len(y) / 16000, 2)}
    (ROOT / 'eval/synthetic' / a.split / icao / 'render_manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(f"{icao.upper()}: {len(manifest['clips'])} clips -> {out}")


if __name__ == '__main__':
    main()

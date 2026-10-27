---
license: apache-2.0
task_categories: [text-generation, token-classification]
language: [en]
tags: [air-traffic-control, synthetic, faa-7110-65, asr-noise, runway-incursion]
pretty_name: GMAPS parser pairs (synthetic ATC transmissions -> JSON)
size_categories: [1K<n<10K]
---

# gmaps-parser-qwen3-4b-lora-data

![Training transmissions by instruction](assets/dataset_composition.png)

*The scripted transmissions behind the training pairs, by instruction (from `train/MANIFEST.json`).*

The data is synthetic: FAA JO 7110.65 phraseology on fictional callsigns and traffic, with zero overlap with the held-out sets (exact 0, normalized 0; `train/MANIFEST.json`).

Synthetic training and evaluation data for the GMAPS clearance parser (see the model card for `darkstar008/gmaps-parser-qwen3-4b-lora`).
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

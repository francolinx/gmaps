# Published model and dataset cards (pre-existing)

These are the **pre-existing artifacts published before the Open Agent Hackathon 2026** for the GMAPS parser (NVIDIA x Red Hat Small Models Hack, Raleigh). They are copied here as published:
- model [darkstar008/gmaps-parser-qwen3-4b-lora](https://huggingface.co/darkstar008/gmaps-parser-qwen3-4b-lora)
- dataset [darkstar008/gmaps-parser-qwen3-4b-lora-data](https://huggingface.co/datasets/darkstar008/gmaps-parser-qwen3-4b-lora-data)

How the copies were made and checked:
- `gmaps-parser-data/README.md` matches the published sha256 in the model repo's `MANIFEST.json` (`8cf9652a…`).
- `gmaps-parser/README.md` is the current published card, same 8,271 bytes. The manifest's hash is for an earlier revision of that card.
- Every `*.svg` chart was re-rendered with `tools/release_charts.py` from the result files in this repo, and each matches the sha256 listed in the published `MANIFEST.json` byte for byte.
- The PNG versions are on the Hugging Face pages only. Hugging Face file downloads were blocked by the build session's network proxy, and the cards' `![](assets/*.png)` links point to them.

Charts (`gmaps-parser/assets/`):
| file | what |
|---|---|
| `record_exact.svg` | record exact on three held-out sets (Whisper text, Whisper text + vocabulary prompt, blind scripted): untuned base, grammar, gmaps-parser |
| `correct_and_accepted.svg` | correct AND accepted by the runtime grounding rule, with the perfect-answer ceiling |
| `base_vs_adapter.svg` | paired: same base, same server, same transmissions; paired interval and McNemar test |
| `gym_vs_offline.svg` | NeMo Gym orchestrated rollouts vs the offline runner |
| `more_data_scored_lower.svg` | submitted 3,000-pair adapter vs the full-data adapter |
| `latency.svg` | per-transmission latency, grammar vs adapter on vLLM |
| `demo_lines_vehicle_callsigns.svg` | demo lines with vehicle callsigns: the adapter does not beat the base |

Dataset chart (`gmaps-parser-data/assets/dataset_composition.svg`): training transmissions by instruction.

New this hackathon (not part of the published cards): `open_agent_2026/nemotron_vs_parser.png|svg`, written by `tools/release_charts.py --open-agent-compare` from `eval/results/open_agent_2026/`.

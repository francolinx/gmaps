---
license: apache-2.0
base_model: Qwen/Qwen3-4B-Instruct-2507
library_name: peft
pipeline_tag: text-generation
language: [en]
tags: [lora, air-traffic-control, runway-incursion, structured-extraction, synthetic-data, nvidia-red-hat-small-models-hack]
datasets: [darkstar008/gmaps-parser-qwen3-4b-lora-data]
---

# gmaps-parser-qwen3-4b-lora

A LoRA adapter on **Qwen/Qwen3-4B-Instruct-2507** that turns ONE transcribed air-traffic-control radio transmission into a five-field JSON record
`{speaker_role, callsign, runway, instruction, taxiways}`. It is the language-model fallback of GMAPS (Ground Movement
Awareness and Prediction System), a controller-side runway-incursion awareness prototype: a grammar parses most transmissions;
transmissions the grammar cannot type go to this model; a deterministic grounding rule then accepts or drops the answer.

**Decision support only; the controller decides.** Everything here was trained and evaluated on **SIMULATED** data: fictional
traffic, synthetic FAA JO 7110.65 phraseology, two public airport identifier lists (KLGA, KATL), used for runway and taxiway names only.
No real ATC audio or transcripts were used for training or evaluation. 

## Results at a glance

![Record exact on three held-out sets](assets/record_exact.png)

*Record exact (all five fields right) on three held-out sets: untuned base, hand-written grammar and gmaps-parser, scored by the same verifier.*

![Correct and accepted on three held-out sets](assets/correct_and_accepted.png)

*Correct AND accepted by the runtime grounding rule. The dashed line is what a perfect answer reaches on Whisper text: the ceiling the speech recognizer imposes.*

![Untuned base vs gmaps-parser on Whisper text](assets/base_vs_adapter.png)

*Same base model, same server, same transmissions: the adapter against the untuned base, with the paired interval and the McNemar test.*

### Exact values (held-out, same verifier for every system)

| system | set | n | record exact | speaker | callsign | runway | instruction | taxiways | JSON valid | accepted by runtime | correct AND accepted | latency med / p95 s | source |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gold | heldout4_asr | 138 | **1.000** | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.674 | 0.674 | 0.000 / 0.000 | `gym/rollouts/offline_heldout4_asr_gold.json` |
| gmaps-parser | heldout4_asr | 138 | **0.652** | 0.949 | 0.783 | 0.891 | 0.949 | 0.935 | 1.000 | 0.746 | 0.572 | 1.600 / 1.694 | `gym/rollouts/offline_heldout4_asr_endpoint_gmaps-parser.json` |
| gmaps-parser (gym eval run) | heldout4_asr | 138 | **0.652** | 0.942 | 0.783 | 0.891 | 0.942 | 0.935 | 1.000 | 0.754 | 0.572 | — / — | `gym/rollouts/validation_gmaps-parser_by_set.json` |
| grammar | heldout4_asr | 138 | **0.377** | 0.601 | 0.543 | 0.529 | 0.616 | 0.514 | 0.630 | 0.558 | 0.355 | 0.020 / 0.021 | `gym/rollouts/offline_heldout4_asr_grammar.json` |
| base | heldout4_asr | 138 | **0.254** | 0.529 | 0.630 | 0.870 | 0.899 | 0.768 | 1.000 | 0.536 | 0.239 | 1.537 / 1.672 | `gym/rollouts/offline_heldout4_asr_endpoint_base.json` |
| base (gym eval run) | heldout4_asr | 138 | **0.254** | 0.529 | 0.630 | 0.870 | 0.899 | 0.775 | 1.000 | 0.536 | 0.239 | — / — | `gym/rollouts/validation_base_by_set.json` |
| gmaps-parser | heldout4_asr_prompt | 138 | **0.761** | 0.971 | 0.848 | 0.913 | 0.993 | 0.957 | 1.000 | 0.884 | 0.761 | 1.608 / 1.698 | `gym/rollouts/offline_heldout4_asr_prompt_endpoint_gmaps-parser.json` |
| gmaps-parser (gym eval run) | heldout4_asr_prompt | 138 | **0.761** | 0.971 | 0.855 | 0.913 | 0.993 | 0.957 | 1.000 | 0.899 | 0.761 | — / — | `gym/rollouts/validation_gmaps-parser_by_set.json` |
| base | heldout4_asr_prompt | 138 | **0.304** | 0.536 | 0.710 | 0.913 | 0.978 | 0.783 | 1.000 | 0.696 | 0.304 | 1.542 / 1.631 | `gym/rollouts/offline_heldout4_asr_prompt_endpoint_base.json` |
| base (gym eval run) | heldout4_asr_prompt | 138 | **0.304** | 0.536 | 0.710 | 0.913 | 0.978 | 0.783 | 1.000 | 0.696 | 0.304 | — / — | `gym/rollouts/validation_base_by_set.json` |
| gmaps-parser | heldout5_script | 414 | **0.906** | 0.971 | 0.993 | 0.952 | 0.990 | 0.998 | 1.000 | 0.988 | 0.906 | 1.611 / 1.700 | `gym/rollouts/offline_heldout5_script_endpoint_gmaps-parser.json` |
| gmaps-parser (gym eval run) | heldout5_script | 414 | **0.899** | 0.966 | 0.993 | 0.952 | 0.988 | 0.998 | 1.000 | 0.986 | 0.899 | — / — | `gym/rollouts/validation_gmaps-parser_by_set.json` |
| grammar | heldout5_script | 414 | **0.773** | 0.942 | 0.942 | 0.870 | 0.942 | 0.845 | 0.942 | 0.940 | 0.773 | 0.020 / 0.021 | `gym/rollouts/offline_heldout5_script_grammar.json` |
| base | heldout5_script | 414 | **0.365** | 0.543 | 0.891 | 0.957 | 0.961 | 0.850 | 1.000 | 0.800 | 0.365 | 1.579 / 1.674 | `gym/rollouts/offline_heldout5_script_endpoint_base.json` |
| base (gym eval run) | heldout5_script | 414 | **0.365** | 0.543 | 0.894 | 0.957 | 0.961 | 0.850 | 1.000 | 0.802 | 0.365 | — / — | `gym/rollouts/validation_base_by_set.json` |

`record exact` = all five fields match the scripted-scenario gold. `accepted by runtime` = the answer also passes the runtime
grounding rule on the transmission text (runway number heard, flight number heard, taxiway words heard, airline named). The two are
reported separately on purpose: a correct answer the runtime would reject is not a usable one. heldout5 was generated after the
parser freeze and is the blind set; heldout4_asr is Whisper large-v3-turbo text (the runtime input); heldout4_asr_prompt is the
same with an ICAO identifier prompt.

### NeMo Gym: orchestrated vs offline

![NeMo Gym orchestrated rollouts vs the offline runner](assets/gym_vs_offline.png)

*The same verifier packaged as a NeMo Gym resources server: orchestrated `gym eval run` rollouts reproduce the offline runner on 5 of 6 model x set pairs.*

### More data scored lower

![Submitted adapter vs the full-data adapter](assets/more_data_scored_lower.png)

*A second adapter trained on all pairs for 2 epochs scored lower on every held-out set. Both were scored on one machine with the same task files; the 3,000-pair adapter is the one published here.*

### Latency

![Parser latency per transmission](assets/latency.png)

*Per-transmission latency on held-out Whisper text: the grammar fast path vs this adapter served by vLLM (log scale).*

## Training

| | |
|---|---|
| base | Qwen/Qwen3-4B-Instruct-2507 |
| method | LoRA r=16, alpha=32, all linear projections, completion-only loss on the assistant JSON |
| data | 3000 pairs from `train/pairs_asr.jsonl` (clean / unprompted-Whisper-style / prompted-Whisper-style / harsh variants of 1,794 scripted transmissions) |
| epochs / steps | 1.0 / 375 |
| hardware | NVIDIA GB10; peak 12.3 GiB; 4454.9 s |
| loss | first 0.894 -> last 0.000 (3 decimals, as on the scoreboard) |
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

![Demo scenario lines: grammar, untuned base and gmaps-parser](assets/demo_lines_vehicle_callsigns.png)

*On scripted demo lines with vehicle callsigns (fictional scenarios), the adapter does not beat the untuned base; the grammar types those lines. Accepted = the runtime took the record; typed right = also the callsign and instruction match the ledger line.*

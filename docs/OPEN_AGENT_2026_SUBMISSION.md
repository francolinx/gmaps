# GMAPS · Open Agent Hackathon 2026 · Wildcard [Tinkerer] · DGX Spark

Everything here is **SIMULATED**: a fictional airport (SIM-1, `XSIM`), scripted synthetic radio, simulated positions. GMAPS is decision support for a tower controller. It never transmits on a radio frequency, and the controller decides. A warning means two recorded authorizations, or an open obligation, on the same runway at time t. "Prediction unavailable" does not mean "no conflict".

## (a) Pre-existing work declaration
Everything in commit `e97a9a1` and earlier (the public GMAPS snapshot of 18 Oct 2026) is pre-existing work and is declared as such. That includes the grammar parser, the ledger and its rules, the SIM-1 look-ahead, the airport identifier data, the Raleigh configuration A/B, the `LocalLLM` adapter, the DGX Spark vLLM runtime, the demo page and the evaluation.

New work is the commit range **`e97a9a1..HEAD` on branch `open-agent-2026`**. New files:
- `lib/gmaps_core/providers/`: `nightshift.py` (`NightshiftLLM`), `sample.py` (offline replay of recorded traces), `base.py` (shared prompts, typed `Unavailable`), `__init__.py` (`GMAPS_PROVIDER=nightshift|local|sample`)
- `lib/gmaps_core/trace.py`: `TraceLogger`, one JSON line per model call and per agent step
- `lib/gmaps_core/grounding.py`: grounding rule for residue parses and for explanations
- `lib/gmaps_core/data/`: `LocalStore`, and a `ZetarisStore` wiring point (not integrated)
- `lib/gmaps_core/engines/`: `LocalRunwayMemory`, and a `MeterlessHMEM` wiring point (not integrated)
- `agent/open_agent_loop.py`: the Planner / Executor / Evaluator / Risk / Explainer / Alert loop
- `demo/run_open_agent.py`, `demo/record_open_agent.sh`
- `tools/show_trace.py`, `tools/nightshift_smoke.py`, `tools/zetaris_load.py`
- `eval/run_provider_compare.py`; the `--open-agent-compare` chart in `tools/release_charts.py` (other charts unchanged, verified byte-identical); `docs/model_card/` (copies of the pre-existing published cards)
- `tests/test_open_agent.py`, `tests/test_meterless_engine.py`, `tests/test_zetaris_store.py`, plus a hook in `tests/run_all.py`
- `.env.example`, this document, the README section
- `ui_scaffold/lga_scene.html`, `ui_scaffold/lga/` and `tools/build_lga_scene.py`: the animated LaGuardia scenario view (see the README)
- One small change to `ui_scaffold/demo.html`: the status line shows the agent's provider, the model it reported, and the last step. The AS-IS / WITH GMAPS playback is untouched.

## (b) Sponsor technology

| sponsor | status at push time | where |
|---|---|---|
| **Nightshift** (AI provider) | **built; Nightshift live run: not run.** Nightshift was not listening on the Spark (ports 8088/18088) at 23:09 UTC on 27 Oct 2026 and no runtime key was present, while vLLM was serving `nemotron-3-nano-30b-a3b-nvfp4` on 127.0.0.1:8002, so no live call was made through Nightshift and there is no `model_reported` or latency to report. | `lib/gmaps_core/providers/nightshift.py` |
| **NVIDIA Nemotron** (via Nightshift) | model id from `NIGHTSHIFT_MODEL` (intended: the local `nemotron-3-nano-30b-a3b-nvfp4` behind Nightshift; not called, since Nightshift was not running). It is used for the residue parse and the two-sentence explainer. | trace lines with `role: executor/explainer` |
| **Meterless** (one engine) | **not integrated** | `lib/gmaps_core/engines/meterless_hmem.py`, `runs/tests/meterless_*.txt` |
| **Zetaris** (data layer) | **not integrated** | `lib/gmaps_core/data/zetaris_store.py`, `runs/tests/zetaris_*.txt` |

**How the app connects to Nightshift.** `NightshiftLLM` sends OpenAI-compatible `POST {NIGHTSHIFT_BASE_URL}/chat/completions` with a Bearer key read from the environment. Key details:
- Temperature is 0 for parsing.
- Timeout per call, one retry on HTTP 5xx or a connection error. After that it returns a typed `provider_unavailable` result, never a guess.
- `model_reported` is read from the response body's `model` field and written to every trace line.
- It is a new class. `LocalLLM` still refuses every non-local URL, and the Raleigh local path never routes to the cloud (`tests/test_open_agent.py::test_local_llm_still_refuses_cloud`).

**What Nemotron does.**
1. **Residue parse.** When the grammar cannot type a transmission, the Planner sends it to Nemotron with the pre-existing `RESIDUE_SYSTEM_PROMPT` / `RESIDUE_SCHEMA`. The Evaluator applies the grounding rule: kind words, callsign digits and name, runway number and taxiway must be heard, and the runway and taxiway must be on the airport's identifier list. On rejection it retries once with the airport vocabulary in the prompt. A second rejection escalates "needs readback" to the controller instead of guessing.
2. **Explainer and Critic.** A second Nemotron call reviews each draft against the facts, with one revision round on its feedback. For each new warning, Nemotron writes two plain sentences from the warning's facts: both authorizations, rule id, times, and the runway's open items from runway memory. The Evaluator rejects text with more than two sentences, a missing callsign, "would have prevented" or "channel". The display then shows the ledger's own rule text, labelled as a fallback.

In the three demo scenarios the grammar types every line. So Nemotron's work in those runs is the explainer, and the residue path is exercised by `tools/nightshift_smoke.py` and by the tests.

**Which model handled which step.** Quote from `samples/open_agent/traces/<scenario>.jsonl` after the live recording: `python3 tools/show_trace.py samples/open_agent/traces/SIM_E1_CROSS_DURING_ROLLOUT.jsonl --calls`. *Filled in from the recorded trace on the Spark: model_reported = `<from trace>`.*

**Meterless: not integrated.** The chosen engine was H-MEM as runway memory. It is not integrated because:
- No Meterless SDK is installable (`pip` finds no distribution `meterless`).
- The Meterless docs were not reachable from the build session.
- No key or SDK name was supplied.

What runs is `LocalRunwayMemory`. It has the same interface (`put` / `open_items` / `close`), stores every ledger reservation and open hold-short obligation keyed by runway, and feeds the Evaluator's ledger-consistency step and the Explainer's facts. Trace lines say `engine: local.runway_memory`. `tests/test_meterless_engine.py` skips the live test with that reason (`runs/tests/meterless_*.txt`). No Meterless fixture exists, so no Meterless claim is made.

**Zetaris: not integrated.** No verified client exists: the docs host was unreachable from the build session and no endpoint or credentials were supplied. The table design is ready, and `tools/zetaris_load.py --dry-run` writes it locally:
- `gmaps_identifiers`: XSIM plus the KBOS graph; KLGA is never loaded.
- `gmaps_transmissions`: the scripted SIM_E* lines.
- `gmaps_ledger`: rows the agent writes per run.

The agent reads identifiers and transmissions, and writes ledger rows, through `LocalStore`. `ZetarisStore()` raises `NotIntegrated`.

**Where NVIDIA contributes.** Nemotron does the residue parse and the explanations, served via Nightshift. The DGX Spark runtime (vLLM, the Raleigh configurations A/B, Nemotron 3.5 Lightning local tool calling) is **pre-existing** and labelled so in the README.

**What is unique.**
- The cloud model never has the last word. Every answer passes a deterministic grounding rule against the transmission text and the airport's identifier table.
- A failed or ungrounded answer becomes a visible escalation or a labelled fallback, never a silent guess.
- Every step is a trace line that names the model that handled it, and that trace is also the offline fixture judges can replay with no key.

## Models that do the work

| model | where it runs | role in the loop | status |
|---|---|---|---|
| **gmaps-parser**: Qwen3-4B-Instruct-2507 + LoRA ([model](https://huggingface.co/darkstar008/gmaps-parser-qwen3-4b-lora), [data](https://huggingface.co/datasets/darkstar008/gmaps-parser-qwen3-4b-lora-data)) | local vLLM on the DGX Spark (`GMAPS_PROVIDER=local`, `LocalLLM`) | types the transmissions the grammar cannot (configuration A) | **pre-existing**, published before this hackathon |
| **NVIDIA Nemotron 30B** (intended: `nemotron-3-nano-30b-a3b-nvfp4`, served locally by vLLM) | Nightshift (`GMAPS_PROVIDER=nightshift`) | residue parse in the agent loop, two-sentence explanation of every warning | **new**; Nightshift live run: not run |

The Raleigh 33B cap does not apply in this hackathon, so the local 4B parser and Nemotron via Nightshift are one system here.

Pre-existing parser results (SIMULATED held-out sets, same verifier; from the published card, copied to `docs/model_card/`):

| held-out set | n | untuned base | grammar | **gmaps-parser** |
|---|---|---|---|---|
| Whisper text (heldout4_asr), record exact | 138 | 0.254 | 0.377 | **0.652** |
| blind scripted (heldout5_script), record exact | 414 | 0.365 | 0.773 | **0.906** |
| Whisper text, correct AND accepted by the runtime | 138 | 0.239 | 0.355 | **0.572** |

- **Paired comparison on Whisper text:** +39.9 points, 95 % CI [+31.9, +48.6], McNemar p = 5.6e-17 (`docs/PAIRED_STATS.md`).
- **Correct and accepted are reported separately:** a perfect answer passes the grounding rule 67.4 % of the time on Whisper text.
- **A second adapter on all 7,065 pairs for 2 epochs scored lower** (0.609 / 0.674 / 0.761, `eval/results/brev/`). It is reported, not submitted.

**New this week: Nemotron vs the fine-tuned 4B.** `eval/run_provider_compare.py --set heldout4_asr --provider nightshift` runs the same 138 transmissions with the same prompt and the same verifier (`gym/resources_servers/gmaps_parser/app.py`). It writes `eval/results/open_agent_2026/nemotron_vs_parser_heldout4_asr.json`, and `tools/release_charts.py --open-agent-compare` draws the chart from that JSON.
- **Nightshift live run: not run** (Nightshift not listening on the Spark, no runtime key). No number is claimed.
- The verdict sentence below is filled in only from the JSON:
  - If Nemotron scores higher: *the fine-tune still runs locally for privacy and latency, and Nemotron takes the residue and the explanation.*
  - If the 4B scores higher: *that is why the specialized small model stays in the loop, and Nemotron does planning and explanation.*
- About half of heldout4 uses synthetic KLGA identifiers. `--exclude-airport KLGA` scores the comparison without them, with every row on the same tasks.

## If registered in Nightshift
If Nightshift's Providers tab accepts a custom OpenAI-compatible endpoint, register the Spark's vLLM so every model call goes through Nightshift. This requires the Spark's port to be reachable by Nightshift:
```
Nightshift custom provider: OpenAI-compatible, base URL http://<spark-address>:8001/v1, model gmaps-parser
GMAPS: python3 eval/run_provider_compare.py --set heldout4_asr --provider nightshift --model <id Nightshift lists for gmaps-parser>
```
If this is not done, the 4B is served locally by vLLM (unchanged), and Nightshift is the primary provider for all Nemotron calls.

## Deck and video notes (for Franco)
- **Slide "Two small models, one loop."**
  - Left: the fine-tuned 4B with `docs/model_card/gmaps-parser/assets/base_vs_adapter.svg` and the HF link.
  - Right: Nemotron 30B via Nightshift with one trace line showing `model_reported` (`python3 tools/show_trace.py samples/open_agent/traces/SIM_E1_CROSS_DURING_ROLLOUT.jsonl --calls`).
  - Centre: the ledger as the shared state.
- **Video, 20 seconds on the fine-tune:**
  - the paired-bars chart;
  - the HF model page;
  - one live residue parse (`python3 tools/nightshift_smoke.py`) with the trace showing which model took it.

## (c) Sample mode (no credentials, no network)
```
python3 demo/run_open_agent.py --scenario all --provider sample
env -i PATH=$PATH python3 demo/run_open_agent.py --provider sample --scenario SIM_E3_STOP_NOT_OBSERVED   # unplug test
python3 tools/show_trace.py runs/traces/<run_id>.jsonl
python3 tests/run_all.py
cd ui_scaffold && python3 -m http.server 8765 --bind 127.0.0.1   # open demo.html?s=SIM_E1_CROSS_DURING_ROLLOUT
```
Sample mode replays the model answers recorded in `samples/open_agent/traces/`, matched by the sha256 of the exact request. A request that was never recorded returns `provider_unavailable (not recorded)`, and the loop takes its labelled fallback, exactly as it would if the provider were down.

Live mode: export `NIGHTSHIFT_BASE_URL`, `NIGHTSHIFT_API_KEY`, `NIGHTSHIFT_MODEL`, then run `demo/record_open_agent.sh`. That script runs the smoke test, records all three scenarios, replays them offline and runs the tests.

## (d) Traces
- `runs/traces/<run_id>.jsonl`: every run (gitignored).
- `samples/open_agent/traces/<scenario>.jsonl`: the recorded live runs (committed).
- `runs/smoke/nightshift_<ts>.txt`: the smoke test.
- `runs/tests/`: test outputs.

Fields per line: ts, run_id, scenario, step, role, provider, model_requested, model_reported, latency_ms, prompt_tokens, completion_tokens, outcome (accepted / rejected / retried / escalated / unavailable / ok), grounding, plus request_sha and response on model calls.

## (e) Limits
- Simulated traffic, synthetic phraseology, simulated positions. The look-ahead runs only on the fictional SIM-1 model. The LaGuardia view (`ui_scaffold/lga_scene.html`) uses the real LGA layout (OSM, ODbL) with the fictional SIM scenarios placed on it, and depicts no real event. Its look-ahead is the SIM-1 computation drawn on LGA geometry; its ledger runs on LGA identifiers.
- Decision support only: alerts go to the display, never to a radio frequency, and no model has shell access. Tools are bounded to parse, ingest, look-ahead, list warnings, explain and post alert.
- **Not integrated:** Meterless (H-MEM), Zetaris.
- **Nightshift:** built, tested against a local mock server for code paths only, and Nightshift live run: not run.
- The Planner, Evaluator and Risk roles are deterministic code by design (safety). The model-backed roles are the residue parse and the Explainer.
- Pre-existing test status in the build container: `tests/run_all.py` → 82 passed, 37 skipped, 1 FAILED. The failing check reads a private labels file outside the repo (`~/gmaps_venue/labels/real_clip_sets.json`). New tests: 12 passed, 2 skipped (live Meterless, live Zetaris), plus the live Nightshift smoke skipped without a key (`runs/tests/`).

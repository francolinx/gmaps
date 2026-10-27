# HackOS submission form: copy-paste answers (Open Agent Hackathon 2026 · Wildcard [Tinkerer])

Fill the bracketed items from your live Spark run (`runs/smoke/nightshift_*.txt`, `samples/open_agent/traces/*.jsonl`). Do not paste anything that is not true at submission time.

## Project name
GMAPS: Ground Movement Awareness and Prediction System

## One-line description
A controller-side cross-check for the tower. It turns radio transmissions into a ledger of who is cleared to do what on each runway, then warns, with both authorizations named and a two-sentence explanation written by Nemotron via Meterless Nightshift, when two clearances or an open obligation hold the same runway at the same time.

## Track
Wildcard [Tinkerer]: Bring Your Own Project

## Problem and users
- **User:** tower and ground controllers.
- **Problem:** runway incursions start when two recorded authorizations overlap on one runway, or when an instruction such as "stop" or "hold short" is not read back. Today the only cross-check is the controller's own memory and eyes.
- **What GMAPS does:** it keeps a structured record of every clearance, flags the overlap the moment it exists, and says why.
- **Scope:** decision support only. GMAPS never transmits, and the controller decides. Everything shown is SIMULATED.

## What is new in this hackathon (scored work)
New work is commit range `e97a9a1..HEAD` on `main`.
1. **Agent loop** (`agent/open_agent_loop.py`) with interdependent roles over a shared ledger:
   - Planner: grammar first; residue (what the grammar cannot type) goes to Nemotron; look-ahead only for a shared zone.
   - Executor: runs the bounded tools.
   - Evaluator: applies a grounding rule to every model answer, retries once with the airport vocabulary, then escalates "needs readback".
   - Risk: SIM-1 look-ahead; "prediction unavailable" when positions are stale.
   - Explainer: Nemotron writes the two sentences, and the Evaluator checks them deterministically.
   - Critic: a second Nemotron call reviews the draft against the warning facts. If it rejects the draft, the Explainer revises once with that feedback; if still rejected, the labelled ledger text is shown.
   - Alert: display only.
2. **Meterless Nightshift as the AI provider** (`lib/gmaps_core/providers/nightshift.py`): OpenAI-compatible, local Console URL, runtime key from the environment, `meterless-app` / `meterless-scope` headers, one retry, a typed "unavailable" result instead of a guess.
3. **Traces** (`lib/gmaps_core/trace.py`, `tools/show_trace.py`): one JSON line per step and per model call, with `model_requested` and `model_reported`.
4. **Sample mode** that replays recorded Nightshift answers by request hash, with no credentials and no network.
5. **Animated LaGuardia view** (`ui_scaffold/lga_scene.html`): AS-IS vs WITH GMAPS side by side on the real KLGA layout (OSM). The ledger re-runs on LGA identifiers.
6. **Local-execution proof** (`tools/local_proof.py`): wraps a live run and records every `connect()` of the GMAPS processes (exact), every non-loopback connection on the machine, and GPU utilization, power and memory from `nvidia-smi`, with the model calls marked on the chart. Evidence: not run (it wraps the Nightshift live run, which did not run).
7. **Nemotron vs the fine-tuned 4B parser** on the same 138 held-out transmissions, scored by the same verifier (`eval/run_provider_compare.py`).

## Pre-existing components (declare)
Everything in commit `e97a9a1` and earlier (the public GMAPS snapshot of 18 Oct 2026), including:
- the grammar parser, the ledger and its rules, the SIM-1 simulator and look-ahead;
- the airport identifier data and diagrams;
- the fine-tuned Qwen3-4B LoRA parser (`gmaps-parser`, published on Hugging Face), its dataset and evaluation;
- the vLLM / DGX Spark runtime;
- the original demo page.

## Sponsor technology explanation
- **Meterless Nightshift (primary AI provider):** every model call in the agent loop goes to Nightshift's OpenAI-compatible endpoint, at `NIGHTSHIFT_BASE_URL` (default `http://127.0.0.1:8088/v1`) with the runtime key in `NIGHTSHIFT_API_KEY`. The response's `model` field is logged as `model_reported` on every trace line. Smoke test: **not run** (Nightshift was not listening on the Spark).
- **NVIDIA:** Nemotron (`nemotron-3-nano-30b-a3b-nvfp4`) runs locally on the DGX Spark under vLLM; it was not connected through Nightshift at submission time.
  - **Live in the agent loop:** **Nightshift live run: not run.** The loop is wired for Nemotron to write the explanation of every warning and parse the transmissions the grammar cannot type, but Nightshift was not running, so no trace line carries a live `model_reported`. The recorded run fell back to the labelled ledger text (`provider_unavailable`).
  - **Comparison:** on 138 held-out transmissions, the Nemotron comparison was **not run** (it needs Nightshift); the fine-tuned 4B scores 0.652.
  - **Pre-existing (Raleigh):** the DGX Spark runtime, and local Nemotron 3.5 Lightning tool calling.
- **Meterless Engine:** Not integrated. A local runway memory with the same role runs in its place (`lib/gmaps_core/engines/runway_memory.py`, tested).
- **Zetaris:** Not integrated. The table design (identifiers, transmissions, ledger rows) and an idempotent loader are ready (`tools/zetaris_load.py --dry-run`). The agent reads and writes through a local store with the same interface.
- **What is unique:** the cloud model never has the last word. Every Nemotron answer passes a deterministic grounding rule against the transmission text and the airport's identifier table. A failed or ungrounded answer becomes a visible escalation or a labelled fallback, never a guess. The trace that proves which model answered is also the offline fixture judges replay.

## How to run (judges)
```
git clone https://github.com/francolinx/gmaps && cd gmaps
python3 demo/run_open_agent.py --scenario all --provider sample      # no credentials, no network
python3 tests/run_all.py
cd ui_scaffold && python3 -m http.server 8765   # open lga_scene.html
```
Live: install Meterless Nightshift, add NVIDIA in Providers, then `export NIGHTSHIFT_API_KEY=...` and run `demo/record_open_agent.sh`.

## Demo video script (about 2 min 30 s)
1. **0:00–0:20 · The problem.** "A controller clears a landing on runway 4, then a crossing at Golf. Today nothing cross-checks the two." Show `lga_scene.html`, scenario 1, AS-IS on the left.
2. **0:20–1:10 · Side by side.** Play at 2×. At +28 s the WITH GMAPS view turns 04/22 red: R1 CONFLICT names both authorizations. The look-ahead projects the overlap, and the simulated encounter comes at +54 s, 26 s after the first alert. Switch to scenario 4 (feed loss): "prediction unavailable, not 'no conflict'".
3. **1:10–1:45 · The agents.** In a terminal, run `demo/run_open_agent.py --provider nightshift --scenario SIM_E3_STOP_NOT_OBSERVED`, then `tools/show_trace.py ... --calls`. Nightshift live run: not run, so point at the explainer line showing `provider_unavailable` and the labelled fallback, the evaluator line "accepted", and the R13 escalation.
4. **1:45–2:05 · The fine-tune.** Show the paired-bars chart (0.254 → 0.652) and the HF page, (the Nemotron-vs-4B comparison did not run).
5. **2:05–2:30 · Sponsors and limits.** Nightshift is the provider, and NVIDIA Nemotron does the explanations. Then say plainly which sponsor pieces are not integrated. Everything is simulated, decision support only.

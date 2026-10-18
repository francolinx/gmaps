# NeMo Gym environment: `gmaps_parser`

Built against **nemo-gym 0.7.0** (`pip index versions nemo-gym`: 0.7.0 is current; the Spark's
`~/gymvenv` had 0.4.0 — upgrade it). Layout is what `gym env init --environment gmaps_parser --profile custom-gym-verifier`
produces, with GMAPS's own verifier in place of the string-match placeholder. SIMULATED data only.

## What the environment is

| piece | what |
|---|---|
| task | one transcribed ATC transmission, sent with the SAME system prompt the parser trains and serves with (`llm_fallback` SYSTEM + airport JSON schema + "Output JSON only.") |
| action | the policy's text; expected: `{"speaker_role","callsign","runway","instruction","taxiways"}` |
| verifier | `resources_servers/gmaps_parser/app.py`: **reward = record exact match** against scripted-scenario gold (1.0 / 0.0); `reward_components` = per-field exact match + `json_valid` + `groundable` |
| `groundable` | 1.0 when the policy's JSON also passes `venue/llm_fallback.py`'s grounding against the transmission (runway number heard, flight number heard incl. 7110.65 group form, taxiway words heard, airline named) — i.e. the runtime would have **accepted** it. Correctness and acceptance are scored separately on purpose |
| data | `environments/gmaps_parser/data/*.jsonl`, built by `gym/build_dataset.py` from a held-out split; the policy sees `responses_create_params` only; gold rides along for the verifier. `gold_rule: fixed` = callsign-aware taxiway gold (DECISIONS N19); `legacy` = the evaluator's current rule |

The verifier never reads `eval/truth`, `eval/labels` or any split. `gym/build_dataset.py` is the only eval-side reader.

## Verified here (CPU, no model)

- `cd gym && ~/gymvenv/bin/python -m pytest resources_servers/gmaps_parser/tests` → NeMo Gym's own verifier contract test passes
  (full reward, zero reward for a hallucinated runway, zero for "Delta → taxiway D", zero for non-JSON, fenced JSON parses, malformed request).
- `gym env validate --environment gmaps_parser --search-dir $PWD/gym` → no configuration errors.
- `gym/run_offline_policy.py` (same verifier, no servers): `gold` → 1.0, `empty` → 0.0, `grammar` → 0.38 record-exact on heldout4 Whisper text (0.73 on scripted text; runway spellings `04`/`4` normalized as in the evaluator).
- **Finding W2:** with a PERFECT answer, `groundable` is only **0.67 on unprompted Whisper text** and **0.84 on prompted** (heldout4).
  Whisper writes "cleared to left", "Never 1558" (Endeavor), "Tier 2700" (Frontier), "4-speed 7508" (Spirit): the grounding
  rule then rejects a correct parse. The runtime ceiling is the grounding, not only the model. Report "correct" and
  "correct AND accepted" side by side; a traffic list (the validator supports it) recovers the airline cases.

## On the Spark (Saturday)

```bash
~/gymvenv/bin/pip install -U nemo-gym            # 0.7.0
python3 gym/build_dataset.py --split heldout5 --text script       # rebuilds with ChartIdentifiers' prompt (same as serving)
python3 gym/build_dataset.py --split heldout4 --text asr
python3 gym/build_dataset.py --split heldout4 --text asr_prompt

# quick path, same verifier, no Gym servers (base / tuned / 35B through the endpoint they are served on):
~/gymvenv/bin/python gym/run_offline_policy.py --input gym/environments/gmaps_parser/data/heldout4_asr.jsonl --policy grammar
~/gymvenv/bin/python gym/run_offline_policy.py --input gym/environments/gmaps_parser/data/heldout4_asr.jsonl --policy endpoint --base-url http://127.0.0.1:8000/v1 --model nvidia/Qwen3.6-35B-A3B-NVFP4
~/gymvenv/bin/python gym/run_offline_policy.py --input gym/environments/gmaps_parser/data/heldout4_asr.jsonl --policy endpoint --base-url http://127.0.0.1:8001/v1 --model base
~/gymvenv/bin/python gym/run_offline_policy.py --input gym/environments/gmaps_parser/data/heldout4_asr.jsonl --policy endpoint --base-url http://127.0.0.1:8001/v1 --model gmaps-parser

# the orchestrated rollouts (this is the "used NeMo Gym" artifact): resources server + simple agent + Gym's vllm_model
# proxy -> your vLLM. VERIFIED in the cloud against a mock policy (gym/mock_policy_server.py): 690 rollouts in 36 s,
# per-set numbers identical to the offline runner (grammar heldout4_asr 0.377 / groundable 0.558 both ways).
cd gym
~/gymvenv/bin/gym eval run --environment gmaps_parser --search-dir "$PWD" \
  --model-type vllm_model --model gmaps-parser --model-url http://127.0.0.1:8001/v1 --model-api-key none \
  --split validation --output rollouts/validation_gmaps-parser.jsonl --temperature 0 --max-output-tokens 160 --concurrency 4
~/gymvenv/bin/python aggregate_by_set.py rollouts/validation_gmaps-parser.jsonl     # splits the one file back into the 3 sets
#   same for --model base (port 8001) and --model nvidia/Qwen3.6-35B-A3B-NVFP4 --model-url http://127.0.0.1:8000/v1
```

Flag facts learned the hard way (0.7.0): `--model-type vllm_model` is the one for an ALREADY-RUNNING vLLM (`local_vllm_model_proxy`
proxies to a vLLM that Gym itself launches: wrong here); `--model-api-key` is required (any string); `--input` is refused when Gym
serves the servers itself, so use `--split validation`, which collates all three validation datasets into one run (hence
`aggregate_by_set.py`); `--limit N` takes the first N of the collated split (heldout5_script first). A hybrid-thinking base
(Qwen3-1.7B) needs thinking off: add `++policy_model.responses_api_models.vllm_model.chat_template_kwargs.enable_thinking=false`;
Qwen3-4B-Instruct-2507 has no thinking mode. The run leaves `gym/environment_servers/` (a 1.1 GB venv), `*_prepare.jsonl`,
`*_metrics.json` and `.venv.setup.lock` behind: all gitignored. Rollout rows carry `split`, `text_source`, `gold`, `predicted`,
`reward_components`, `grounding_reason`. The offline runner stays the fallback and scores identically.

## What to claim

"GMAPS's validator is the Gym verifier; here are rollouts for grammar / base / tuned / 35B on the same held-out tasks, with
correctness and runtime acceptance reported separately." Say **no RL training was performed** unless an Unsloth/NeMo-RL
GRPO run using this reward actually happened. Known fixtures are regression cases; heldout5 was generated after the
freeze and is the blind set.

**Spark note (Oct 17):** `127.0.0.1:11000`, nemo-gym's default head-server port, is held by a system service on the Spark. Symptom: `Head server finished unexpectedly`, with `[Errno 98] ... ('127.0.0.1', 11000)` in the log. Add `--config gym/configs/head_port_11500.yaml` (resolved from the repo root; inside `gym/` use `configs/head_port_11500.yaml`) to every `gym eval run`.

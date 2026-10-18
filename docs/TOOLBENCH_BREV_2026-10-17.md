# Tool-loop bench on Brev (L40S): procedures v3–v6, Qwen3-4B vs Qwen2.5-7B

SIMULATED. Fictional airport XSIM / SIM-1, episode `SIM_E1_CROSS_DURING_ROLLOUT`, bench `venue/tool_loop_bench.py`, models served
by vLLM v0.8.5.post1 on 127.0.0.1:8001 on a Brev L40S (host driver CUDA 12.7). Procedures are the commits `2a743ab` (v4),
`9c3253e` (v5), `08aba53` (v6); v3 is the procedure those three build on. **No prompt revisions were made after these runs.**

Raw results (force-added; `gym/rollouts/` is otherwise ignored):
`eval/results/toolbench/toolbench_base_v3_20261017-185304.json`, `toolbench_base_v4_20261017-190003.json`, `toolbench_base_v5_20261017-191556.json`,
`toolbench_qwen25-7b_v3_20261017-193041.json`, `toolbench_qwen25-7b_v6_20261017-193813.json`. Five files, not six: there is no sixth
`toolbench_*` summary on this box (the lone `trace_20261017-195741_positive_0.jsonl` is a single trace with no summary, so it is not reported).

`base` = Qwen3-4B-Instruct-2507; `qwen25-7b` = Qwen2.5-7B-Instruct. Both are under the 33B cap.

## Results (pass = every check in the run passed)

Recoveries are `recovered_after_error / faults_injected`. Recoveries can exceed injected faults because the model also recovers from
errors it caused itself (v4's invalid calls).

| Model | Proc. | Scenario | Runs | Pass | Ingest recall | Alerts exact | Invalid call rate | Recoveries / injected | Refused reposts |
|---|---|---|---|---|---|---|---|---|---|
| base 4B | v3 | no_feed | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 0 |
| base 4B | v3 | positive | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 0 |
| base 4B | v3 | stale | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 0 |
| base 4B | v3 | transient | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 2/2 | 0 |
| base 4B | v4 | no_feed | 2 | 0.00 | 0.80 | 0.00 | 0.12 | 4/0 | 0 |
| base 4B | v4 | positive | 2 | 0.50 | 0.80 | 0.50 | 0.10 | 3/0 | 0 |
| base 4B | v4 | stale | 2 | 0.00 | 0.60 | 0.00 | 0.13 | 4/0 | 0 |
| base 4B | v4 | transient | 2 | 0.00 | 0.60 | 0.00 | 0.12 | 6/2 | 0 |
| base 4B | v5 | no_feed | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 0 |
| base 4B | v5 | positive | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 0 |
| base 4B | v5 | stale | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 0 |
| base 4B | v5 | transient | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 2/2 | 0 |
| qwen25-7b | v3 | no_feed | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 2 |
| qwen25-7b | v3 | positive | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 2 |
| qwen25-7b | v3 | stale | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 2 |
| qwen25-7b | v3 | transient | 2 | 0.00 | 1.00 | 1.00 | 0.00 | 2/2 | 2 |
| qwen25-7b | v6 | no_feed | 3 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 3 |
| qwen25-7b | v6 | positive | 3 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 3 |
| qwen25-7b | v6 | stale | 3 | 0.00 | 1.00 | 1.00 | 0.00 | 0/0 | 3 |
| qwen25-7b | v6 | transient | 3 | 0.00 | 0.80 | 0.00 | 0.00 | 0/3 | 0 |

No run refused a retry of an unavailable look-ahead (refused_retries 0 everywhere).

## Failure mode per model × procedure (from the per-turn calls)

- **4B v3:** skips the look-ahead on quiet turns. It calls only `parse_transmission` + `ledger_ingest` at t=0/3/24/31 and reaches the
  look-ahead only through `post_alert` at t=28 (`lookahead_every_turn` fails in 8/8 runs; everything else passes).
- **4B v4** ("the third call is always post_alert or lookahead"): invents event ids. Ingest recall drops to 0.6–0.8, so the alert is missed
  in 7 of 8 runs.
- **4B v5** (step 4 mandatory, event_id only from `parse_transmission`): same as v3. It still skips the look-ahead on quiet turns (8/8).
- **7B v3:** re-posts the open warning after the readback. At t=31 it calls `post_alert` again for the conflict already posted at
  t=28; the bench refuses it (`no_refused_reposts` fails in 8/8). Otherwise it is clean.
- **7B v6** (v3 + "open_warning_ids are already-handled warnings"): the same re-post at t=31 in 9/9 non-transient runs. Under
  `transient` it does not retry the failed `ledger_ingest` at t=0 (0/3 recovered), so the landing clearance is never in the ledger
  and the alert is missed (ingest recall 0.80, alerts exact 0.00).

## Conclusion

No small model passes the procedure on Brev. The 4B never makes look-ahead a habit; the 7B re-posts and, with v6, stops retrying.
CLAIMS_LEDGER row 7 stays **not supported** under the cap on Brev. The last candidate is Lightning option B on the Spark tonight.

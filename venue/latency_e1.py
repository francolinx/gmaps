"""E1 latency breakdown: where the clip-to-alert time goes, per stage, over N demo runs.

  python3 venue/latency_e1.py RUN_ID [RUN_ID ...] [--metrics-before F --metrics-after F] [--out docs/LATENCY_E1.md]

Reads only ~/gmaps_venue/log (agent turns, MCP tool-call log, ASR results) and, if given, two snapshots of vLLM's
read-only Prometheus /metrics. Never eval/ truth or labels. Stages for the alert turn (t=28 transmission):
  segmentation   clips are pre-cut (owned re-voiced clips): 0 measured; a live VAD end-of-speech wait is a design budget
  whisper        decode time of the t=28 clip (asr_results.json asr_s; the model is already loaded)
  agent start    host -> sandbox exec -> OpenClaw CLI -> first LLM request (before the first tool call, minus LLM time)
  LLM steps      each model round trip before a tool call (the gap between tool calls), until post_alert
  tools (rules)  parse / ledger_ingest (rules) / lookahead / list / post_alert server time (ms in the tool-call log)
  delivery       post_alert is a dry run: not measured
"""
import argparse, json, re, statistics
from pathlib import Path

LOG = Path.home() / 'gmaps_venue' / 'log'


def jl(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def metric_sums(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r'^(vllm:[a-z0-9_]+)\{[^}]*\} ([0-9.e+-]+)$', line)
        if m and (m.group(1).endswith(('_sum', '_count', '_total'))):
            out[m.group(1)] = out.get(m.group(1), 0.0) + float(m.group(2))
    return out


def breakdown(run):
    turns = jl(LOG / f'agent_turns_{run}.jsonl'); calls = [c for c in jl(LOG / f'mcp_calls_{run}.jsonl') if c.get('tool')]
    t = next(x for x in turns if x['t'] == 28)
    t0 = t['epoch_start']; t1 = t0 + t['wall_s']
    cs = sorted((c for c in calls if t0 <= c['t'] <= t1), key=lambda c: c['t'])
    alert = next((c for c in cs if c['tool'] == 'post_alert' and c.get('ok')), None)
    upto = [c for c in cs if c['t'] <= alert['t']] if alert else cs
    gaps, prev_end = [], None
    for c in upto:
        start = c['t']
        gaps.append(start - (prev_end if prev_end is not None else t0))
        prev_end = start + (c.get('ms') or 0) / 1000
    first = gaps[0] if gaps else None
    tool_ms = {}
    for c in upto:
        tool_ms[c['tool']] = round(tool_ms.get(c['tool'], 0) + (c.get('ms') or 0), 1)
    return {'run': run, 'asr_s': t.get('asr_s'), 'turn_wall_s': t['wall_s'],
            'to_first_tool_s': round(first, 2) if first is not None else None, 'llm_gaps_s': [round(g, 2) for g in gaps[1:]],
            'steps_to_alert': len(upto), 'failed_calls_before_alert': sum(1 for c in upto if not c.get('ok')),
            'tool_ms': tool_ms, 'tools_s': round(sum(tool_ms.values()) / 1000, 3),
            'alert_done_s': round(alert['t'] + alert['ms'] / 1000 - t0, 2) if alert else None,
            'after_alert_s': round(t1 - (alert['t'] + alert['ms'] / 1000), 2) if alert else None,
            'clip_to_alert_s': round((t.get('asr_s') or 0) + alert['t'] + alert['ms'] / 1000 - t0, 2) if alert else None,
            'calls': [c['tool'] + ('' if c.get('ok') else ' (FAILED)') for c in upto]}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('runs', nargs='+'); ap.add_argument('--metrics-before'); ap.add_argument('--metrics-after')
    ap.add_argument('--exec-overhead', type=float, nargs='*', help='measured sandbox exec + openclaw CLI start (s), idle system'); ap.add_argument('--out')
    a = ap.parse_args()
    rows = [breakdown(r) for r in a.runs]
    med = lambda k: statistics.median([r[k] for r in rows if r[k] is not None])
    llm_all = [g for r in rows for g in r['llm_gaps_s']]
    L = ['# E1 latency breakdown: clip to alert (t=28 crossing clearance)', '',
         f'{len(rows)} runs of `venue/demo_e1.sh` (Whisper input, full stack: NemoClaw sandbox -> OpenClaw `gmaps` agent -> Qwen3.6 on vLLM -> GMAPS MCP tools). Script: `venue/latency_e1.py`. vLLM unchanged.', '',
         '| run | clip→alert s | Whisper s | to first tool call s | LLM steps to alert | LLM gaps s | tools s | failed calls | after alert s | turn wall s |', '|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        L.append(f"| {r['run']} | **{r['clip_to_alert_s']}** | {r['asr_s']} | {r['to_first_tool_s']} | {r['steps_to_alert']} | {', '.join(map(str, r['llm_gaps_s']))} | {r['tools_s']} | {r['failed_calls_before_alert']} | {r['after_alert_s']} | {r['turn_wall_s']} |")
    L += ['', f"**Medians:** clip→alert **{med('clip_to_alert_s')} s**; Whisper {med('asr_s')} s; host→first tool call {med('to_first_tool_s')} s; "
          f"LLM gap per step {statistics.median(llm_all) if llm_all else '–'} s × {med('steps_to_alert')} steps; tool execution {med('tools_s')} s.", '']
    if a.metrics_before and a.metrics_after:
        b, e = metric_sums(a.metrics_before), metric_sums(a.metrics_after); d = {k: e.get(k, 0) - b.get(k, 0) for k in e}
        n = d.get('vllm:e2e_request_latency_seconds_count', 0) or 1
        per = lambda k: d.get(k, 0) / n
        L += ['## vLLM side (read-only /metrics, delta over the runs)', '',
              f"- requests: {n:.0f}; mean end-to-end {per('vllm:e2e_request_latency_seconds_sum'):.2f} s; time to first token {per('vllm:time_to_first_token_seconds_sum'):.2f} s; "
              f"prefill {per('vllm:request_prefill_time_seconds_sum'):.2f} s; decode {per('vllm:request_decode_time_seconds_sum'):.2f} s; queue {per('vllm:request_queue_time_seconds_sum'):.3f} s",
              f"- tokens per request: prompt {per('vllm:prompt_tokens_total'):.0f} (prefix-cache hit {100 * d.get('vllm:prefix_cache_hits_total', 0) / max(1, d.get('vllm:prefix_cache_queries_total', 1)):.0f} %), generated {per('vllm:generation_tokens_total'):.0f}", '']
    if a.exec_overhead:
        L += [f"Sandbox exec + OpenClaw CLI start alone (idle, `nemoclaw my-assistant exec -- openclaw --version`): {', '.join(f'{x:.2f}' for x in a.exec_overhead)} s (median {statistics.median(a.exec_overhead):.2f} s).", '']
    L += ['Tool calls before the alert (run 1): ' + ' → '.join(rows[0]['calls']), '']
    text = '\n'.join(L) + '\n'
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    print(json.dumps(rows, indent=1))


if __name__ == '__main__':
    main()

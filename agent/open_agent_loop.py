"""GMAPS open agent loop (Open Agent Hackathon 2026). SIMULATED: fictional SIM-1 map, scripted radio, simulated positions.

Distinct roles over one shared state (the ledger, plus the runway memory engine):
  Planner    decides the steps for each transmission: grammar first; residue (what the grammar cannot type) to the model
             provider (Nemotron via Nightshift); look-ahead only when a shared zone is involved (two movers with surfaces).
  Executor   runs the bounded tools only: parse, ledger_ingest, lookahead, open_warnings, explain, post_alert. No shell.
  Evaluator  applies the grounding rule to every model answer and checks ledger consistency (identifiers from the data store,
             open obligations from runway memory). A rejected residue parse is retried ONCE with the airport vocabulary prompt;
             a second rejection ESCALATES ('needs readback', shown to the controller) instead of guessing.
  Risk       runs the SIM-1 look-ahead each second while a shared zone is watched; when positions are stale or missing it
             returns 'prediction unavailable', which is not 'no conflict'.
  Explainer  the model writes two plain sentences for each new warning; the Evaluator checks them deterministically;
  Critic     a second model call reviews the draft against the facts; one revision round with its feedback; on rejection or
             provider failure the ledger's own rule text is shown, labelled as such (fallback).
  Alert      posts to the display only (GMAPS never transmits on a radio frequency).
A warning means two recorded authorizations, or an open obligation, at time t. Decision support only; the controller decides.
Every transition is one trace line (gmaps_core.trace). The predictor never reads the evaluation truth or label folders.
"""
import hashlib, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'tools'))
from gmaps_core.parser import parse                                   # noqa: E402
from gmaps_core.ledger import Ledger                                  # noqa: E402
from gmaps_core.sim.simmap import SimAirport                          # noqa: E402
from gmaps_core.sim.predict import predict                            # noqa: E402
from gmaps_core.grounding import check_residue, check_explanation     # noqa: E402
from gmaps_core.providers.base import Unavailable                     # noqa: E402
from run_sim_episode import intents_from                              # noqa: E402

SCENARIOS = ('SIM_E1_CROSS_DURING_ROLLOUT', 'SIM_E3_STOP_NOT_OBSERVED', 'SIM_E5_HOLD_NOT_SLOWING')
ICAO = 'XSIM'
POSTED_SEVERITIES = ('CONFLICT', 'CAUTION')


def _h(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()[:16]


class OpenAgent:
    def __init__(self, scenario, provider, store, memory, trace, t_end=100, transmissions=None):
        """transmissions: optional list of {t, speaker, text} (a judge's own radio lines); then the scenario only supplies the
        simulated positions (or none, if scenario is None: the look-ahead then reports 'prediction unavailable')."""
        self.scenario, self.llm, self.store, self.mem, self.tr, self.t_end = scenario, provider, store, memory, trace, t_end
        self.custom = transmissions
        self.ap = SimAirport()
        self.led = Ledger(self.ap)
        self.ids = store.identifiers(ICAO)
        obs_file = ROOT / f'episodes/{scenario}.obs.json' if scenario else None
        ob = json.loads(obs_file.read_text()) if obs_file and obs_file.exists() else {'tracks': {}, 'packets': []}   # simulated sensor packets (runtime input)
        self.tracks = {tid: dict(meta, packets=[p for p in ob['packets'] if p['track_id'] == tid]) for tid, meta in ob['tracks'].items()}
        self.by_cs = {meta['callsign']: self.tracks[tid]['packets'] for tid, meta in ob['tracks'].items()}
        self.zone_watch, self.last_risk, self.posted, self.ledger_hashes = False, None, set(), {}
        self.display, self.heard, self.model_calls = [], [], []

    # ---------------------------------------------------------------- roles
    def planner(self, line, ev):
        residue = ev.get('parse_path') == 'residue' or ev.get('speech_act') == 'unknown'
        steps = (['residue_parse', 'evaluate_grounding'] if residue else []) + ['ledger_ingest', 'evaluate_ledger', 'check_shared_zone']
        self.tr.log('plan', 'planner', 'ok', t=line['t'], text=line['text'], grammar={'speech_act': ev['speech_act'], 'kind': ev['kind'],
                    'parse_path': ev['parse_path']}, steps=steps, reason='grammar could not type it' if residue else 'grammar typed it')
        return steps

    def _call(self, step, role, fn, *a):
        out = fn(*a)
        call = out['_call'] if isinstance(out, dict) and '_call' in out else (out.call if isinstance(out, Unavailable) else getattr(self.llm, 'last_call', {}))
        self.model_calls.append({k: call.get(k) for k in ('provider', 'model_reported', 'latency_ms', 'replayed_from')} | {'step': step})
        return out, call

    def residue(self, line, ev):
        """Executor + Evaluator: model parse, grounding, one retry with the vocabulary prompt, then escalate."""
        vocab = dict(self.ids, callsigns=sorted(self.led.actors))
        for attempt, voc in ((1, None), (2, vocab)):
            d, call = self._call('residue_parse', 'executor', self.llm.parse_residue, line['text'], ICAO, voc)
            ok, verdict = check_residue(d, line['text'], self.ids)
            unavailable = d.get('error') == 'provider_unavailable'
            outcome = 'accepted' if ok else ('unavailable' if unavailable else ('retried' if attempt == 1 else 'rejected'))
            self.tr.log_call('residue_parse', 'executor', call, outcome, grounding=verdict, t=line['t'], attempt=attempt,
                             answer={k: v for k, v in d.items() if k != '_call'})
            self.tr.log('evaluate_grounding', 'evaluator', 'accepted' if ok else 'rejected', grounding=verdict, t=line['t'], attempt=attempt)
            if ok:
                return self._event_from_residue(ev, d)
            if unavailable:
                break
        self.tr.log('escalate', 'evaluator', 'escalated', t=line['t'], text=line['text'],
                    reason='residue not typed after one retry' if not unavailable else 'provider unavailable for the residue parse')
        self.post_alert(line['t'], 'needs_readback:' + str(line['t']), 'ESCALATION', 'Needs readback: transmission not typed',
                        f'GMAPS could not type "{line["text"]}" with grounded identifiers, so nothing was recorded for it. '
                        'Confirm it on frequency; the ledger may be missing this instruction.', 'evaluator (no model text)', None)
        return None

    def _event_from_residue(self, ev, d):
        rw = d.get('runway')
        return dict(ev, speech_act=d['speech_act'], kind=d['kind'], actor_candidates=list(d['callsigns']), runway=rw,
                    runway_end_candidates=self.ap.runway_end_candidates(rw) if rw else [], crossing_at=d.get('crossing_at'),
                    route=d.get('route'), parse_path=d['parse_path'])

    def evaluate_ledger(self, t, ev):
        """Ledger consistency: every active reservation sits on an identifier the store knows; report the runway's open items."""
        bad = [r['reservation_id'] for r in self.led.reservations
               if r['status'] == 'active' and str(r['runway_end']).lstrip('0') not in {x.lstrip('0') for x in self.ids['runways']}]
        rwy = (self.ap.physical_runway(ev['runway_end_candidates'][0]) if ev.get('runway_end_candidates') else None)
        open_items = self.mem.open_items(rwy) if rwy else []
        self.tr.log('evaluate_ledger', 'evaluator', 'rejected' if bad else 'accepted', t=t, inconsistent=bad, runway=rwy,
                    engine=self.mem.name, open_items_on_runway=[f"{i['kind']}:{i['callsign']}@{i['t']}" for i in open_items])

    def sync_memory_and_store(self, t):
        for r in self.led.reservations:
            self.mem.put(r['physical'], r['reservation_id'], {'kind': r['type'], 'callsign': r['callsign'], 'status': r['status'], 't': r['t']})
        for p in self.led.pending:
            h = p.get('hold') or {}
            if h.get('runway') and p.get('physical'):
                self.mem.put(p['physical'], 'hold:' + p['event_id'], {'kind': 'HOLD_SHORT', 'callsign': p['callsign'], 'status': 'open', 't': p['t']})
        rows = []
        for typ, recs, key in (('reservation', self.led.reservations, 'reservation_id'), ('warning', self.led.warnings, 'warning_id')):
            for r in recs:
                hh = _h(r)
                if self.ledger_hashes.get(r[key]) != hh:
                    self.ledger_hashes[r[key]] = hh
                    rows.append({'run_id': self.tr.run_id, 'seq': len(self.ledger_hashes), 't': t, 'record_type': typ, 'record_id': r[key],
                                 'callsign': r.get('callsign') or (r.get('authorization_a') or {}).get('callsign'),
                                 'runway': r.get('physical') or r.get('runway'), 'status': r['status'], 'body_json': json.dumps(r, sort_keys=True, default=str)})
        if rows:
            self.store.append_ledger(self.tr.run_id, rows)
            self.tr.log('ledger_write', 'store', 'ok', t=t, store=self.store.name, rows=len(rows), records=[r['record_id'] for r in rows])

    def risk(self, as_of):
        intents = intents_from(self.led, self.by_cs, as_of)
        if not self.zone_watch:
            movers = [cs for cs, it in intents.items() if it['surface']]
            if len(movers) >= 2:
                self.zone_watch = True
                self.tr.log('shared_zone', 'planner', 'ok', t=as_of, movers=movers, decision='look-ahead on (shared zone XSIM:ZONE:Z1)')
            else:
                return None
        comb = predict(as_of, self.tracks, intents, 'combined')
        raised = [c for c in comb['candidates'] if c.get('raised')]
        unav = comb['unavailable']
        status = 'prediction unavailable' if unav and not raised else ('raised' if raised else 'no projected overlap')
        summary = {'status': status, 'raised': sorted((c['type'], tuple(c['callsigns'])) for c in raised), 'unavailable': [u['reason'] for u in unav]}
        if summary != self.last_risk:
            self.last_risk = summary
            self.tr.log('lookahead', 'risk', 'unavailable' if status == 'prediction unavailable' else 'ok', t=as_of, status=status,
                        raised=[{'type': c['type'], 'callsigns': c['callsigns'], 'lead_s': c.get('lead_s'), 'basis': c.get('basis')} for c in raised],
                        unavailable=unav, note='prediction unavailable is not "no conflict"' if unav else None)
        return comb

    def explainer(self, facts, fallback_text, fallback_label='ledger rule text'):
        """Explainer drafts -> Evaluator (deterministic check) -> Critic (model) reviews against the facts; one revision round
        with the critic's issue as feedback; anything still rejected falls back to the labelled rule text."""
        model_facts = {k: v for k, v in facts.items() if k != 'alert_id'}   # ids come from process-wide counters: kept out so replays match
        feedback = None
        for rnd in (1, 2):
            out, call = self._call('explain', 'explainer', self.llm.explain_warning, model_facts, feedback)
            if isinstance(out, Unavailable):
                self.tr.log_call('explain', 'explainer', call, 'unavailable', grounding=None, t=facts.get('t'), alert=facts['alert_id'], round=rnd)
                return fallback_text, f'{fallback_label} (provider unavailable: {out.reason[:80]})', call.get('model_reported')
            ok, verdict = check_explanation(out, facts)
            self.tr.log_call('explain', 'explainer', call, 'accepted' if ok else 'rejected', grounding=verdict, t=facts.get('t'), alert=facts['alert_id'], round=rnd)
            self.tr.log('evaluate_explanation', 'evaluator', 'accepted' if ok else 'rejected', grounding=verdict, t=facts.get('t'), round=rnd)
            if not ok:
                return fallback_text, f'{fallback_label} (model answer rejected: {verdict})', call.get('model_reported')
            crit, ccall = self._call('critique', 'critic', self.llm.critique_explanation, model_facts, out)
            if isinstance(crit, Unavailable):   # the deterministic check passed; the critic could not review: shown, and labelled so
                self.tr.log_call('critique', 'critic', ccall, 'unavailable', grounding=None, t=facts.get('t'), alert=facts['alert_id'], round=rnd)
                return out, f"{call.get('provider')} · {call.get('model_reported') or call.get('model_requested')} (critic unavailable)", call.get('model_reported')
            self.tr.log_call('critique', 'critic', ccall, 'accepted' if crit['supported'] else 'retried' if rnd == 1 else 'rejected',
                             grounding=crit['issue'] or 'supported', t=facts.get('t'), alert=facts['alert_id'], round=rnd)
            if crit['supported']:
                tag = ' · revised after critique' if rnd == 2 else ''
                return out, f"{call.get('provider')} · {call.get('model_reported') or call.get('model_requested')} · critic: supported{tag}", call.get('model_reported')
            feedback = crit['issue'] or 'a claim is not supported by the facts'
        return fallback_text, f'{fallback_label} (critic rejected the revision: {feedback[:80]})', call.get('model_reported')

    def post_alert(self, t, alert_id, severity, headline, explanation, source, model_reported):
        self.display.append({'t': t, 'alert_id': alert_id, 'severity': severity, 'headline': headline, 'explanation': explanation,
                             'explanation_source': source, 'model_reported': model_reported})
        self.tr.log('post_alert', 'alert', 'ok', t=t, alert=alert_id, severity=severity, headline=headline, target='display (never a radio frequency)')

    def alerts(self, as_of, comb):
        for w in self.led.warnings:
            if w['status'] != 'open' or w['t_fired'] > as_of or w['warning_id'] in self.posted or w['severity'] not in POSTED_SEVERITIES:
                continue
            self.posted.add(w['warning_id'])
            auth = [a for a in (w.get('authorization_a'), w.get('authorization_b')) if a]
            rwy = w.get('runway')
            facts = {'alert_id': w['warning_id'], 'source': 'ledger', 'rule_id': w['rule_id'], 'rule_name': w['name'], 'severity': w['severity'],
                     'runway': rwy, 't': w['t_fired'], 'callsigns': [a['callsign'] for a in auth],
                     'authorizations': [{k: a.get(k) for k in ('callsign', 'type', 'runway_end', 't_issued', 'acknowledged')} for a in auth],
                     'open_items_on_runway': [{k: i[k] for k in ('kind', 'callsign', 't', 'status')} for i in self.mem.open_items(rwy)],
                     'simulated': True}
            text, src, mr = self.explainer(facts, w['explain'])
            self.post_alert(w['t_fired'], w['warning_id'], w['severity'], f"{w['rule_id']} {w['name']} · runway {rwy}", text, src, mr)
        for c in (comb or {}).get('candidates', []):
            key = ('lookahead', c['type'], tuple(c['callsigns']))
            if not c.get('raised') or key in self.posted:
                continue
            self.posted.add(key)
            aid = f"lookahead:{c['type']}:{'+'.join(c['callsigns'])}@{as_of}"
            facts = {'alert_id': aid, 'source': 'SIM-1 look-ahead (simulated positions, fictional map)', 'type': c['type'], 'zone': c['zone'],
                     't': as_of, 'callsigns': list(c['callsigns']), 'overlap_window_s': c.get('overlap'), 'lead_s': c.get('lead_s'),
                     'basis': c.get('basis'), 'simulated': True}
            fallback = f"Look-ahead ({c['type'].replace('_', ' ')}) at zone {c['zone']} for {', '.join(c['callsigns'])} at t=+{as_of} s: " + '; '.join(c.get('basis') or ['projected occupancy windows overlap']) + '.'
            text, src, mr = self.explainer(facts, fallback, 'look-ahead basis text')
            self.post_alert(as_of, aid, 'LOOKAHEAD', f"look-ahead: {c['type'].replace('_', ' ')} · {c['zone']}", text, src, mr)

    # ---------------------------------------------------------------- loop
    def run(self):
        self.tr.log('start', 'planner', 'ok', provider=self.llm.name, model_requested=getattr(self.llm, 'model', None), store=self.store.name,
                    engine=self.mem.name, note='SIMULATED: fictional SIM-1 map, scripted radio, simulated positions')
        lines = (sorted(({'t': float(x['t']), 'speaker': x.get('speaker', 'ATC'), 'text': x['text']} for x in self.custom), key=lambda x: x['t'])
                 if self.custom is not None else list(self.store.transmissions(self.scenario)))
        self.tr.log('read_transmissions', 'store', 'ok', store=self.store.name, n=len(lines))
        t0 = time.perf_counter()
        for as_of in range(0, self.t_end + 1):
            while lines and lines[0]['t'] <= as_of:
                line = lines.pop(0)
                hint = 'ATC' if line['speaker'] == 'ATC' else 'PILOT'
                ev = parse(line['text'], ICAO, speaker_hint=hint, resolver=self.ap, event_time=line['t'],
                           source={'id': f"{self.scenario}_{line['t']}.wav", 'start_s': line['t'], 'end_s': None})
                self.tr.log('grammar_parse', 'executor', 'ok', t=line['t'], tool='parse_transmission', kind=ev['kind'], speech_act=ev['speech_act'])
                steps = self.planner(line, ev)
                if 'residue_parse' in steps:
                    ev = self.residue(line, ev)
                if ev is not None:
                    res = self.led.ingest(ev, line['t'])
                    self.tr.log('ledger_ingest', 'executor', 'ok', t=line['t'], tool='ledger_ingest', event_id=ev['event_id'], kind=ev['kind'],
                                parse_path=ev['parse_path'], open_warnings=[w['warning_id'] for w in self.led.warnings if w['status'] == 'open'])
                    self.sync_memory_and_store(line['t'])
                    self.evaluate_ledger(line['t'], ev)
                self.heard.append({'t': line['t'], 'speaker': line['speaker'], 'text': line['text'],
                                   'kind': ev['kind'] if ev else None, 'parse_path': ev['parse_path'] if ev else 'escalated'})
            self.led.tick(as_of)
            self.sync_memory_and_store(as_of)
            comb = self.risk(as_of)
            self.alerts(as_of, comb)
        lat = sorted(c['latency_ms'] for c in self.model_calls if c.get('latency_ms') is not None)
        summary = {'scenario': self.scenario, 'run_id': self.tr.run_id, 'provider': self.llm.name, 'store': self.store.name, 'engine': self.mem.name,
                   'model_calls': len(self.model_calls), 'model_reported': sorted({c['model_reported'] for c in self.model_calls if c.get('model_reported')}),
                   'latency_ms_median': (lat[len(lat) // 2] if len(lat) % 2 else (lat[len(lat) // 2 - 1] + lat[len(lat) // 2]) / 2) if lat else None,
                   'latency_ms_all': lat, 'alerts': len(self.display), 'loop_wall_s': round(time.perf_counter() - t0, 2)}
        self.tr.log('end', 'planner', 'ok', **{k: v for k, v in summary.items() if k not in ('scenario', 'run_id')})
        return {'note': 'SIMULATED: fictional SIM-1 map, scripted radio, simulated positions. Decision support only.',
                'summary': summary, 'transcript': self.heard, 'alerts': self.display, 'trace': str(self.tr.path.relative_to(ROOT)) if self.tr.path.is_relative_to(ROOT) else str(self.tr.path),
                'ledger': {'warnings': self.led.warnings}}

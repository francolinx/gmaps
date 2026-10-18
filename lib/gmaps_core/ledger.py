"""Clearance-state ledger and runway-reservation rules (prepared library, stdlib only).

Five state kinds are kept SEPARATE per actor (Eichelberger):
  authorized   - what a controller instruction recorded (never 'what happened')
  acknowledged - what a readback confirmed (or 'unknown' / 'discrepant' / 'not_heard')
  reported     - what a pilot/vehicle said about itself
  inferred     - what this engine derived (always labelled, e.g. 'exit instruction issued')
  observed     - what an independent surveillance/motion source said (never from voice)

Runways are reserved resources. A reservation is created when an authorization is RECORDED and is
released only by a recorded release event (go-around, crossing complete, takeoff/landing reported or
observed complete, exit instruction). A stop/cancel does NOT release a reservation until acknowledged.

A fired rule means: at time t the ledger held two authorizations that need the same protected runway
(or intersecting runways). It does not mean a collision would have happened, and the ledger never
claims it would have prevented one.

Rule IDs and citations follow data/specs/compat_matrix.json (JO 7110.65BB, fetched Oct 2, 2026).
"""
import functools
import itertools
import json
import re
import sys

_wid = itertools.count(1)
_rid = itertools.count(1)
RESERVATION_FOR = {'LANDING_CLEARANCE': 'LANDING', 'TAKEOFF_CLEARANCE': 'TAKEOFF', 'LUAW': 'LUAW', 'CROSS': 'CROSSING'}
CITE = {
    'R1': '3-1-3; 3-7-2', 'R2': '3-1-3; 3-9-10', 'R3': '3-9-6; 3-10-3', 'R4': '3-9 intersecting runway operations; 3-10-4',
    'R5': '3-9-4; 3-10-5', 'R6': '3-7-2', 'R7': '3-7-2 (read back hold instructions)', 'R8': '3-1-3', 'R9': '3-9-4',
    'R10': '3-1-3; 3-10-5', 'R11': '3-7-2', 'R13': 'stop/cancel acknowledgement (engine rule)', 'R14': '2-4-3a,b (read back items correct; hold short read back)',
    'R15': '2-4-3b, 2-4-3 NOTE 1c (runway and hold short in the read back)', 'R16': '2-4-3a (read back items correct)',
    'R17': 'engine rule (N7): an earlier clearance with no release heard is kept, unverified'}
NAME = {
    'R1': 'crossing vs active landing', 'R2': 'crossing vs active takeoff', 'R3': 'takeoff vs landing, same runway',
    'R4': 'takeoff vs landing, intersecting runways', 'R5': 'line up and wait vs landing, same runway',
    'R6': 'route word crosses a runway with no hold-short or crossing clearance', 'R7': 'runway hold-short not read back',
    'R8': 'crossing not closed out', 'R9': 'line up and wait timer', 'R10': 'occupied runway vs landing or takeoff',
    'R11': 'two crossings at the same point', 'R13': 'stop/cancel unacknowledged', 'R14': 'readback mismatch (candidate, shadow)',
    'R15': 'incomplete readback', 'R16': 'readback mismatch, low ASR confidence',
    'R17': 'possible conflict with an unverified earlier clearance'}
# Franco, Oct 9: alert categories are scored separately. 'conflict' = two recorded authorizations; 'missed_readback' = an open
# read-back obligation past its timeout; 'readback_mismatch' = a read back with a wrong or missing runway / hold.
CATEGORY = {'R7': 'missed_readback', 'R14': 'readback_mismatch', 'R15': 'incomplete_readback', 'R16': 'readback_mismatch_low_confidence',
            'R17': 'unverified_conflict'}
# Franco, Oct 9 (round 4): readback-mismatch candidates run in SHADOW mode (logged, never shown to a controller) until real-clip
# precision is >= 0.8; incomplete readbacks and low-confidence mismatches are information only.
SHADOW_CATEGORIES = {'readback_mismatch'}
INFO_CATEGORIES = {'incomplete_readback', 'readback_mismatch_low_confidence'}
TIMERS = {'R7': 15, 'R8': 90, 'R9': 90, 'R13': 10}
# Franco, Oct 10 (N6, revised N7): expiry never silently stops conflict checking. A reservation with no release heard within
# EXPIRY_S[type] SOFT-expires to 'expired_unverified': it stays in every conflict check, and a conflict with it raises R17
# ADVISORY (category unverified_conflict, scored on its own; Oct 11: ONE per runway, "N unverified earlier clearances on runway
# X", updated in place, per-reservation detail inside). It is
# dropped only by a release that is heard, or by the HARD limit (HARD_LIMIT_S after the clearance), and both are logged.
# A crossing whose readback (hold) was never acknowledged, and any reservation with an unacknowledged stop, never soft-expires.
# The runtime passes data/specs/runtime_config.json reservation_expiry_s / reservation_hard_limit_s. None/{} = no expiry.
EXPIRY_S = {'LANDING': 360, 'TAKEOFF': 300, 'LUAW': 300, 'CROSSING': 180}
HARD_LIMIT_S = 1200
SOFT_RECHECK_S = 30            # an exempt reservation is looked at again this often (it soft-expires once the exemption ends)
# Franco, Oct 10 (N6, policy N8; N13 Oct 11): cap on open warnings. At the cap the oldest ADVISORY-LEVEL item that is not
# conflict-level (an R17, or a reminder/advised item standing only on expired_unverified reservations) is evicted first, then
# shadow/info items never shown to a controller. A CONFLICT/CAUTION warning is never evicted: with only those open, nothing is
# evicted, the new warning is admitted and a loud "SYSTEM DEGRADED" health alarm is raised (stderr, notes, cap_events,
# ledger.health). None = no cap.
MAX_OPEN_WARNINGS = 200
# Franco, Oct 11 (N13, stricter): a conflict-level warning (CONFLICT/CAUTION) is NEVER evicted at the cap. Instead it AUTO-CLOSES
# (lifecycle, not eviction; status 'auto_closed', logged) once every clearance it stands on is expired_unverified and it has been
# open >= AUTO_CLOSE_S. A later conflict with those clearances still raises the per-runway R17 advisory.
CONFLICT_LEVEL = {'CONFLICT', 'CAUTION'}
AUTO_CLOSE_S = 120
# Franco, Oct 11 (N12): only the last HISTORY_KEEP closed warnings, notes, events, closed reservations, expiry/cap records and
# pending readbacks are kept in memory; open warnings and live reservations are never pruned. Every pruned item is handed to
# Ledger.archive (the runtime writes it to the rotating call log). None = keep everything.
HISTORY_KEEP = 1000
ACTOR_STATE_KEEP = 50          # per callsign and state kind (only the latest is read)
# Callsigns: at most HISTORY_KEEP are kept; the earliest first heard go first, never one with a live reservation, a pending readback
# or an open warning (callsign repair matches the most recent anyway).
CLOSED = ('released', 'expired')
UNVERIFIED = 'expired_unverified'
STATE_KINDS = ('authorized', 'acknowledged', 'reported', 'inferred', 'observed')


def hold_text(h):
    if not h:
        return ''
    where = f"runway {h['runway']}" if h.get('runway') else (h.get('taxiway') or '?')
    return 'hold short of ' + where + (f" at {h['at']}" if h.get('at') else '') + (f" on runway {h['on_runway']}" if h.get('on_runway') else '')


def describe(ev):
    parts = [ev['kind'].replace('_', ' ').lower()]
    if ev.get('runway'):
        parts.append('rwy ' + ev['runway'])
    if ev.get('route'):
        parts.append('via ' + '-'.join(ev['route']))
    if ev.get('crossing_at'):
        parts.append('at ' + ev['crossing_at'])
    for h in ev.get('hold_constraints') or []:
        parts.append(hold_text(h))
    return ', '.join(parts)


def _same_runway(a, b):
    """'22' and '22L' can be the same runway (a side not heard is ambiguous, not discrepant); '22L' vs '22R' or 13 vs 31 are not."""
    ma, mb = re.fullmatch(r'0*(\d+)([LRC]?)', str(a)), re.fullmatch(r'0*(\d+)([LRC]?)', str(b))
    if not (ma and mb):
        return a == b
    return ma.group(1) == mb.group(1) and (not ma.group(2) or not mb.group(2) or ma.group(2) == mb.group(2))


class Ledger:
    def __init__(self, airport, readback_window_s=30, timers=None, readback_conf_min=None, expiry=EXPIRY_S, max_open_warnings=MAX_OPEN_WARNINGS, dedup=True,
                 hard_limit_s=HARD_LIMIT_S, history_keep=HISTORY_KEEP, auto_close_s=AUTO_CLOSE_S):
        self.ap = airport
        self.expiry = dict(expiry or {})            # reservation type -> seconds with no release before it expires
        self.max_open = max_open_warnings
        self.dedup = dedup                          # one open warning per conflict, updated in place
        self._open = {}                             # warning_id -> open warning (index; self.warnings keeps every warning)
        self._by_key, self._key_of = {}, {}         # conflict key <-> open warning id (dedup index)
        self.hard_limit = hard_limit_s              # seconds after the clearance when an unreleased reservation is dropped (logged)
        self.expiries, self.cap_events = [], []     # every soft expiry / hard drop / release-after-unverified; every cap event
        self.health = {'degraded': False, 'since': None, 'reason': None, 'over_cap_admissions': 0}
        self._res = {}                              # reservation_id -> reservation
        self._r17 = {}                              # runway key -> the ONE open R17 advisory on that runway (N7 aggregate, Oct 11)
        self.auto_close_s = auto_close_s
        self.history_keep = history_keep
        self.pruned = {k: 0 for k in ('warnings', 'notes', 'events', 'reservations', 'expiries', 'cap_events', 'pending')}
        self.expiry_counts, self.cap_counts = {}, {}                     # kind -> count of every expiry record ever logged (N12: exact after pruning)
        self.archive = None                         # callable(list_name, items): where pruned history goes (the call log)
        self.rb_conf_min = readback_conf_min   # Whisper word confidence a different runway must reach to be a mismatch candidate
        self.T = dict(TIMERS, **(timers or {}))   # Franco, Oct 9: the readback timeout (R7) is a config value
        self.inter = airport.intersecting_runways()
        self.window = readback_window_s
        self.events, self.reservations, self.warnings, self.notes = [], [], [], []
        self.actors = {}
        self.pending = []          # instructions awaiting readback
        self.timers = []           # (due_t, rule, payload)
        self.last_addressed = None
        self.last_speaker = None
        self.now = None

    # ------------------------------------------------------------------ helpers
    def _actor(self, cs):
        if cs not in self.actors:
            self.actors[cs] = {k: [] for k in STATE_KINDS}
        return self.actors[cs]

    def _state(self, cs, kind, value, ev, t):
        rec = {'value': value, 't': t, 'event_id': ev['event_id'] if ev else None}
        self._actor(cs)[kind].append(rec)
        return rec

    def _resolve_callsigns(self, ev):
        out = []
        for c in ev['actor_candidates']:
            if c.startswith('?'):
                tail = c[1:]
                known = [k for k in reversed(list(self.actors)) if k.endswith(tail)]
                if known:
                    out.append(known[0])
                    self.notes.append({'t': ev['event_time'], 'event_id': ev['event_id'],
                                       'note': f'abbreviated callsign {tail} resolved to {known[0]} (most recent match; heuristic)'})
                else:
                    out.append(c)
            else:
                out.append(c)
        return out

    def _physical(self, rwy):
        ends = self.ap.runway_end_candidates(rwy) if rwy else []
        phys = sorted({self.ap.physical_runway(e) for e in ends})
        return ends, phys

    def _active(self, **kw):
        return [r for r in self.reservations if r['status'] not in CLOSED and all(r.get(k) == v for k, v in kw.items())]

    def _snapshot(self, cs):
        a = self.actors.get(cs, {k: [] for k in STATE_KINDS})
        return {k: (a[k][-1]['value'] if a[k] else None) for k in STATE_KINDS}

    def _warn(self, rule, severity, t, runway, a, b, explain, clears, extra=None):
        w = {'warning_id': f'w{next(_wid):04d}', 'rule_id': rule, 'name': NAME[rule], 'severity': severity, 'category': CATEGORY.get(rule, 'conflict'), 'runway': runway,
             't_fired': t, 'status': 'open', 'cleared_at': None, 'cleared_by': None,
             'authorization_a': a, 'authorization_b': b,
             'state_snapshot': {x['callsign']: self._snapshot(x['callsign']) for x in (a, b) if x and x.get('callsign')},
             'explain': explain, 'what_would_clear_it': clears, 'cite': 'JO 7110.65BB ' + CITE[rule],
             'claim_limit': 'Two recorded authorizations / an open obligation at time t. Not a collision prediction.'}
        if extra:
            w.update(extra)
        rids = [x.get('reservation_id') for x in (a, b) if x]
        if rids and all(rids):
            w['basis'] = [rids]                     # the reservation pair(s) this warning stands on (N6: expiry closes only these)
        key = (rule, severity, json.dumps(runway), tuple(sorted(f"{x.get('callsign')}|{x.get('type')}" for x in (a, b) if x)))
        if self.dedup:
            same = self._open.get(self._by_key.get(key))
            if same is not None:                    # the same conflict again (e.g. a repeated clearance): update in place, no new warning
                same.update(authorization_a=a, authorization_b=b, state_snapshot=w['state_snapshot'], explain=explain, t_updated=t,
                            repeat_count=same.get('repeat_count', 0) + 1)
                same['t_repeats'] = (same.get('t_repeats') or [])[-19:] + [t]   # when the same conflict was raised again (last 20)
                if 'basis' in w:
                    same.setdefault('basis', []).append(rids)
                return same
        if self.max_open is not None and len(self._open) >= self.max_open:
            self._at_cap(rule, t)
        self.warnings.append(w)
        self._open[w['warning_id']] = w
        self._by_key[key], self._key_of[w['warning_id']] = w['warning_id'], key
        return w

    def _advisory_level(self, w):
        """N8: an R17 advisory, or a warning whose every basis pair includes an expired_unverified reservation."""
        if w['rule_id'] == 'R17':
            return True
        return bool(w.get('basis')) and all(any((self._res.get(rid) or {}).get('status') == UNVERIFIED for rid in pair) for pair in w['basis'])

    def _at_cap(self, rule, t):
        """N8 (Franco, Oct 10; N13 Oct 11): evict the oldest advisory-level item that is not conflict-level, else the oldest
        shadow/info item; never a CONFLICT/CAUTION warning. With only those open, evict nothing (the new warning is still admitted) and raise the SYSTEM DEGRADED alarm."""
        opn = list(self._open.values())
        tiers = (('advisory_level', [o for o in opn if self._advisory_level(o) and o['severity'] not in CONFLICT_LEVEL]),   # N13: never a conflict
                 ('shadow_or_info', [o for o in opn if o.get('category') in SHADOW_CATEGORIES | INFO_CATEGORIES]))
        for tier, cand in tiers:
            if cand:
                old = min(cand, key=lambda o: o['t_fired'])
                self._close(old, t, f'closed: open-warning cap {self.max_open} reached (oldest {tier} item closed to admit {rule})', 'closed_over_cap')
                rec = {'t': t, 'kind': 'open_warning_cap_evicted', 'tier': tier, 'cap': self.max_open, 'closed_warning_id': old['warning_id'],
                       'closed_rule': old['rule_id'], 'new_rule': rule}
                self._log_cap(rec)
                self.notes.append({'t': t, 'event_id': None, 'note': f"OPEN-WARNING CAP {self.max_open} REACHED: {tier} item {old['warning_id']} ({old['rule_id']}) closed to admit a new {rule}"})
                sys.stderr.write(f"GMAPS LEDGER: OPEN-WARNING CAP {self.max_open} REACHED at t={t}: closed {tier} item {old['warning_id']} ({old['rule_id']}) to admit {rule}.\n")
                return
        self.health['over_cap_admissions'] += 1
        rec = {'t': t, 'kind': 'system_degraded', 'cap': self.max_open, 'open': len(self._open) + 1, 'new_rule': rule,
               'reason': f'open-warning cap {self.max_open} reached with only conflict-level warnings open; nothing evicted, new {rule} admitted'}
        self._log_cap(rec)
        if not self.health['degraded']:
            self.health.update(degraded=True, since=t, reason=rec['reason'])
            self.notes.append({'t': t, 'event_id': None, 'note': 'SYSTEM DEGRADED: ' + rec['reason']})
            sys.stderr.write(f"GMAPS LEDGER: SYSTEM DEGRADED at t={t}: {rec['reason']}. Every open warning is still kept and shown.\n")

    def _close(self, w, t, by, status='cleared'):
        w.update(status=status, cleared_at=t, cleared_by=by)
        self._open.pop(w['warning_id'], None)
        if self._r17.get(json.dumps(w['runway'])) == w['warning_id']:
            del self._r17[json.dumps(w['runway'])]
        k = self._key_of.pop(w['warning_id'], None)
        if k is not None and self._by_key.get(k) == w['warning_id']:
            del self._by_key[k]
        if self.health['degraded'] and len(self._open) < self.max_open:
            rec = {'t': t, 'kind': 'system_recovered', 'cap': self.max_open, 'open': len(self._open), 'degraded_since': self.health['since']}
            self._log_cap(rec)
            self.health.update(degraded=False, since=None, reason=None)
            self.notes.append({'t': t, 'event_id': None, 'note': f"system recovered: open warnings {len(self._open)} below the cap {self.max_open}"})
            sys.stderr.write(f"GMAPS LEDGER: system recovered at t={t}: open warnings {len(self._open)} < cap {self.max_open}.\n")

    def _clear(self, pred, t, by):
        for w in list(self._open.values()):
            if pred(w):
                self._close(w, t, by)

    def open_warnings(self):
        return list(self._open.values())

    def _drop_basis(self, r, t, by, status):
        """Reservation r is gone: remove it from every open warning standing on it; a warning with no pair left closes."""
        for w in list(self._open.values()):
            if 'basis' in w and any(r['reservation_id'] in pair for pair in w['basis']):
                w['basis'] = [pair for pair in w['basis'] if r['reservation_id'] not in pair]
                if not w['basis']:
                    self._close(w, t, by, status)
                elif w['rule_id'] == 'R17' and 'unverified_detail' in w:
                    self._r17_refresh(w)

    @staticmethod
    def _ref(r):
        return {'callsign': r['callsign'], 'type': r['type'], 'runway_end': r['runway_end'], 'physical_runway': r['physical'],
                't_issued': r['t'], 'event_id': r['event_id'], 'acknowledged': r['ack'], 'reservation_id': r['reservation_id']}

    # ------------------------------------------------------------------ reservations
    def _reserve(self, cs, rtype, rwy, ev, t, extra=None):
        ends, phys = self._physical(rwy)
        if not phys:
            self.notes.append({'t': t, 'event_id': ev['event_id'], 'note': f'runway {rwy} not resolved at {self.ap.icao}; no reservation made'})
            return None
        r = {'reservation_id': f'r{next(_rid):04d}', 'callsign': cs, 'type': rtype, 'runway_end': rwy, 'physical': phys[0],
             'physical_candidates': phys, 't': t, 'event_id': ev['event_id'], 'status': 'active', 'ack': 'pending',
             'released_at': None, 'released_by': None, 'advisory': ev.get('traffic_advisory'), 'basis': 'authorized'}
        if extra:
            r.update(extra)
        self.reservations.append(r)
        self._res[r['reservation_id']] = r
        self._evaluate(r, t)
        if rtype == 'CROSSING':
            self.timers.append((t + self.T['R8'], 'R8', r['reservation_id']))
        if rtype == 'LUAW':
            self.timers.append((t + self.T['R9'], 'R9', r['reservation_id']))
        if rtype in self.expiry:
            self.timers.append((t + self.expiry[rtype], 'EXPIRE', r['reservation_id']))
            if self.hard_limit:
                self.timers.append((t + self.hard_limit, 'HARD', r['reservation_id']))
        return r

    def _release(self, r, t, by):
        if r['status'] == UNVERIFIED:   # N7: a release heard after the soft expiry is logged too
            self._log_expiry({'t': t, 'kind': 'released_after_expired_unverified', 'reservation_id': r['reservation_id'], 'callsign': r['callsign'],
                                  'type': r['type'], 'runway_end': r['runway_end'], 't_issued': r['t'], 'after_s': round(t - r['t'], 1), 'by': by,
                                  'closed_warnings': []})
        r.update(status='released', released_at=t, released_by=by)
        self._drop_basis(r, t, by, 'cleared')
        self._clear(lambda w: 'basis' not in w and any(x and x.get('event_id') == r['event_id'] and x.get('callsign') == r['callsign']
                                                       for x in (w['authorization_a'], w['authorization_b'])), t, by)

    def _soft_exempt(self, r):
        if r.get('cancel_status') == 'unacknowledged':
            return 'stop/cancel not acknowledged'
        if r['type'] == 'CROSSING' and r.get('ack') != 'acknowledged':
            return 'crossing (and its hold) not read back / acknowledged'
        return None

    def _soft_expire(self, r, t):
        """N7 (Franco, Oct 10): no release heard within expiry[type] s -> expired_unverified. Logged, never silent. The reservation
        stays in every conflict check (a new conflict with it raises R17 ADVISORY); open warnings standing on it stay open."""
        why = self._soft_exempt(r)
        if why:
            if r.get('soft_expiry_held') != why:
                r['soft_expiry_held'] = why
                self.notes.append({'t': t, 'event_id': r['event_id'], 'note': f"{r['callsign']} {r['type'].lower()} runway {r['runway_end']}: no release heard "
                                   f"in {round(t - r['t'], 1)} s, NOT soft-expired ({why}); kept active until a release or the hard limit"})
            self.timers.append((t + SOFT_RECHECK_S, 'EXPIRE', r['reservation_id']))
            return
        age = round(t - r['t'], 1)
        by = (f"expired_unverified: no release heard for {r['callsign']} {r['type'].lower()} runway {r['runway_end']} in {age} s; "
              f"still checked for conflicts (advisory) until a release is heard" + (f" or the hard limit ({self.hard_limit} s)" if self.hard_limit else ''))
        r.update(status=UNVERIFIED, soft_expired_at=t, soft_expired_by=by)
        now_unv = sorted(w['warning_id'] for w in self._open.values() if 'basis' in w and any(r['reservation_id'] in p for p in w['basis']) and self._advisory_level(w))
        for wid in now_unv:
            self._open[wid].setdefault('unverified_since', t)
            w = self._open[wid]
            if w['severity'] in CONFLICT_LEVEL and self.auto_close_s is not None and self._all_unverified(w):
                self.timers.append((max(t, w['t_fired'] + self.auto_close_s), 'AUTOCLOSE', wid))
        self._log_expiry({'t': t, 'kind': 'expired_unverified', 'reservation_id': r['reservation_id'], 'callsign': r['callsign'], 'type': r['type'],
                              'runway_end': r['runway_end'], 't_issued': r['t'], 'after_s': age, 'closed_warnings': [], 'now_unverified_warnings': now_unv})
        self.notes.append({'t': t, 'event_id': r['event_id'], 'note': by + (f"; open warnings now unverified: {', '.join(now_unv)}" if now_unv else '') + ' (state unknown, not observed clear)'})

    def _hard_drop(self, r, t):
        """N7: the hard limit. Dropped from conflict checks; logged; closes only the warnings that stood solely on it."""
        age = round(t - r['t'], 1)
        by = f"hard_drop_without_release: no release heard for {r['callsign']} {r['type'].lower()} runway {r['runway_end']} in {age} s (hard limit {self.hard_limit} s)"
        r.update(status='expired', released_at=t, released_by=by)
        before = set(self._open)
        self._drop_basis(r, t, by, 'expired')
        closed = sorted(before - set(self._open))
        self._log_expiry({'t': t, 'kind': 'hard_drop_without_release', 'reservation_id': r['reservation_id'], 'callsign': r['callsign'], 'type': r['type'],
                              'runway_end': r['runway_end'], 't_issued': r['t'], 'after_s': age, 'closed_warnings': closed})
        self.notes.append({'t': t, 'event_id': r['event_id'], 'note': by + (f"; closed {', '.join(closed)}" if closed else '') + ' (state unknown, not observed clear)'})

    def _warn_unverified(self, o, rule, severity, t, runway, a, b, explain, clears, extra=None):
        """N7 (aggregated Oct 11): a conflict whose earlier side is an expired_unverified reservation o. ONE R17 ADVISORY per runway
        ("N unverified earlier clearances on runway X"), updated in place when a new clearance arrives; the per-reservation
        detail is inside it (unverified_detail). Never dropped: it closes only when every pair it stands on is released/dropped."""
        item = {'t': t, 'unverified_reservation': o['reservation_id'], 'unverified_callsign': o['callsign'], 'unverified_type': o['type'],
                'unverified_runway_end': o['runway_end'], 'unverified_t_issued': o['t'], 'expired_unverified_since': o.get('soft_expired_at'),
                'no_release_heard_s': round(t - o['t'], 1), 'new_clearance': {k: b.get(k) for k in ('callsign', 'type', 'runway_end', 't_issued', 'reservation_id')},
                'underlying_rule': rule, 'underlying_severity': severity, 'underlying_name': NAME[rule], 'explain': explain,
                'pair': [a.get('reservation_id'), b.get('reservation_id')]}
        rk = json.dumps(runway)
        w = self._open.get(self._r17.get(rk)) if self.dedup else None
        if w is None:
            w = self._warn('R17', 'ADVISORY', t, runway, a, b, explain, clears,
                           extra=dict(extra or {}, underlying_rule=rule, underlying_severity=severity, unverified_reservation=o['reservation_id'],
                                      unverified_detail=[]))
            if self.dedup:
                self._r17[rk] = w['warning_id']
        else:
            w.update(authorization_a=a, authorization_b=b, t_updated=t, repeat_count=w.get('repeat_count', 0) + 1,
                     underlying_rule=rule, underlying_severity=severity, unverified_reservation=o['reservation_id'])
            w['t_repeats'] = (w.get('t_repeats') or [])[-19:] + [t]
            if item['pair'] not in w.setdefault('basis', []):
                w['basis'].append(item['pair'])
        w['unverified_detail'] = [d for d in w.get('unverified_detail', []) if d['pair'] != item['pair']] + [item]
        self._r17_refresh(w)
        return w

    def _r17_refresh(self, w):
        """Name, explanation and counts of the per-runway R17 from its detail (only pairs it still stands on)."""
        live = [d for d in w['unverified_detail'] if d['pair'] in (w.get('basis') or [])]
        w['unverified_detail'] = live
        res = list(dict.fromkeys(d['unverified_reservation'] for d in live))
        rwy = '/'.join(x.split(':')[-1] for x in w['runway']) if isinstance(w['runway'], list) else str(w['runway']).split(':')[-1]
        n = len(res)
        w['name'] = f"{n} unverified earlier clearance{'s' if n != 1 else ''} on runway {rwy}"
        w['unverified_count'] = n
        w['underlying_rules'] = sorted({d['underlying_rule'] for d in live})
        per = '; '.join(f"{d['unverified_callsign']} {d['unverified_type'].lower()} runway {d['unverified_runway_end']} (t={d['unverified_t_issued']}, "
                        f"no release heard, expired_unverified since t={d['expired_unverified_since']}) vs {d['new_clearance']['callsign']} "
                        f"{(d['new_clearance']['type'] or '').lower()} (t={d['new_clearance']['t_issued']}): {d['underlying_rule']} {d['underlying_name']}"
                        for d in live)
        w['explain'] = (f"Possible conflict with {n} unverified earlier clearance{'s' if n != 1 else ''} on runway {rwy} (state unknown, "
                        f"not observed clear): {per}.")
        w['what_would_clear_it'] = 'a release heard for ' + ', '.join(dict.fromkeys(d['unverified_callsign'] for d in live)) + ', or the new clearance released'

    def _all_unverified(self, w):
        """N13: every reservation of every pair this warning stands on is expired_unverified."""
        return bool(w.get('basis')) and all(len(p) == 2 and all((self._res.get(rid) or {}).get('status') == UNVERIFIED for rid in p) for p in w['basis'])

    def _auto_close(self, w, t):
        age = round(t - w['t_fired'], 1)
        a, b = w.get('authorization_a') or {}, w.get('authorization_b') or {}
        by = f"auto-closed: both clearances expired_unverified (open {age} s; no release heard for {a.get('callsign')} or {b.get('callsign')})"
        self._close(w, t, by, 'auto_closed')
        w['auto_close_reason'] = 'both clearances expired_unverified'
        self._log_expiry({'t': t, 'kind': 'conflict_auto_closed', 'reason': 'both clearances expired_unverified', 'warning_id': w['warning_id'],
                              'rule_id': w['rule_id'], 'severity': w['severity'], 'callsign': f"{a.get('callsign')}/{b.get('callsign')}", 'type': w['rule_id'],
                              'runway_end': '/'.join(x for x in (a.get('runway_end'), b.get('runway_end')) if x), 'after_s': age, 'closed_warnings': [w['warning_id']]})
        self.notes.append({'t': t, 'event_id': None, 'note': f"{w['warning_id']} ({w['rule_id']} {w['severity']}) {by}. A new conflict with either clearance "
                                                             'raises the R17 advisory for its runway.'})

    def _log_cap(self, rec):
        self.cap_events.append(rec)
        self.cap_counts[rec['kind']] = self.cap_counts.get(rec['kind'], 0) + 1

    def _log_expiry(self, rec):
        self.expiries.append(rec)
        self.expiry_counts[rec['kind']] = self.expiry_counts.get(rec['kind'], 0) + 1   # exact even after pruning

    # ------------------------------------------------------------------ bounded history (N12)
    def total(self, name):
        """Items ever appended to list `name` (kept + pruned)."""
        return len(getattr(self, name)) + self.pruned[name]

    def since(self, name, total_before):
        """Items appended to list `name` after total(name) was total_before (pruning only removes old items)."""
        k = self.total(name) - total_before
        lst = getattr(self, name)
        return lst[len(lst) - k:] if k > 0 else []

    def _drop(self, name, items):
        if items:
            self.pruned[name] += len(items)
            if self.archive:
                self.archive(name, items)

    def _prune(self):
        k = self.history_keep
        if not k:
            return
        for name in ('notes', 'events', 'expiries', 'cap_events', 'pending'):
            lst = getattr(self, name)
            if len(lst) > k:
                cut = lst[:len(lst) - k]
                del lst[:len(lst) - k]
                self._drop(name, cut)
        if len(self.warnings) - len(self._open) > k:
            closed = [w for w in self.warnings if w['warning_id'] not in self._open]
            gone = {w['warning_id'] for w in closed[:len(closed) - k]}
            self.warnings = [w for w in self.warnings if w['warning_id'] not in gone]
            self._drop('warnings', [w for w in closed if w['warning_id'] in gone])
        n_closed = sum(1 for r in self.reservations if r['status'] in CLOSED)
        if n_closed > k:
            closed = [r for r in self.reservations if r['status'] in CLOSED]
            gone = {r['reservation_id'] for r in closed[:n_closed - k]}
            self.reservations = [r for r in self.reservations if r['reservation_id'] not in gone]
            for rid in gone:
                self._res.pop(rid, None)
            self._drop('reservations', [r for r in closed if r['reservation_id'] in gone])
        if len(self.actors) > k:
            keep = {r['callsign'] for r in self._active()} | {p['callsign'] for p in self.pending} | {
                x['callsign'] for w in self.open_warnings() for x in (w.get('authorization_a'), w.get('authorization_b')) if x and x.get('callsign')}
            for cs in [c for c in self.actors if c not in keep][:len(self.actors) - k]:
                del self.actors[cs]
                self.pruned['actors'] = self.pruned.get('actors', 0) + 1
        for a in self.actors.values():
            for kind, lst in a.items():
                if len(lst) > ACTOR_STATE_KEEP:
                    del lst[:len(lst) - ACTOR_STATE_KEEP]

    def _evaluate(self, r, t):
        for o in self._active():
            if o is r or o['callsign'] == r['callsign']:
                continue
            W = functools.partial(self._warn_unverified, o) if o['status'] == UNVERIFIED else self._warn
            same = o['physical'] == r['physical']
            pair = tuple(sorted((o['physical'], r['physical'])))
            inter = pair in self.inter
            types = {o['type'], r['type']}
            a, b = self._ref(o), self._ref(r)
            if same and types == {'LANDING', 'CROSSING'}:
                sev = 'CAUTION' if (self._of(r, o, 'CROSSING').get('advisory')) else 'CONFLICT'
                land, cross = self._of(r, o, 'LANDING'), self._of(r, o, 'CROSSING')
                W('R1', sev, t, r['physical'], a, b,
                           f"Runway {land['runway_end']} has a recorded landing authorization for {land['callsign']} (t={land['t']}) "
                           f"and a crossing authorization for {cross['callsign']} at {cross.get('at') or '?'} (t={cross['t']}). "
                           f"Both need the {r['physical'].split(':')[-1]} protected area.",
                           f"{cross['callsign']} reports clear of runway, or {land['callsign']} go-around / landing complete")
            elif same and types == {'TAKEOFF', 'CROSSING'}:
                W('R2', 'CONFLICT', t, r['physical'], a, b,
                           f"Takeoff authorization and crossing authorization recorded on the same runway ({r['physical'].split(':')[-1]}).",
                           'crossing complete, or takeoff cancelled and acknowledged')
            elif same and types == {'TAKEOFF', 'LANDING'}:
                tk = self._of(r, o, 'TAKEOFF')
                sev = 'CAUTION' if tk.get('advisory') else 'CONFLICT'
                W('R3', sev, t, r['physical'], a, b,
                           'Takeoff and landing authorizations recorded on the same physical runway.',
                           'one authorization released (go-around, takeoff cancelled, or operation complete)')
            elif inter and types == {'TAKEOFF', 'LANDING'}:
                second = r
                sev = 'ADVISED' if second.get('advisory') else 'CONFLICT'
                node = [n for n in self.ap.nodes.values() if n['kind'] == 'runway_intersection' and set(pair) <= set(n['surface_ids'])]
                W('R4', sev, t, list(pair), a, b,
                           f"Runways {o['runway_end']} and {r['runway_end']} intersect (draft graph node {node[0]['id'] if node else '?'}). "
                           f"A {r['type'].lower()} authorization for {r['callsign']} was recorded at t={r['t']} while a "
                           f"{o['type'].lower()} authorization for {o['callsign']} (t={o['t']}) is still live"
                           + (f"; traffic information was included ('{second['advisory']}')." if second.get('advisory') else '; no traffic information was included in the second clearance.'),
                           f"{o['callsign']} or {r['callsign']} authorization released (go-around, takeoff cancelled and acknowledged, operation complete)",
                           extra={'graph_basis': node[0]['id'] if node else None})
            elif same and types == {'LUAW', 'LANDING'}:
                land = self._of(r, o, 'LANDING')
                adv = (land.get('advisory') or '').upper()
                sev = 'CAUTION' if 'HOLDING' in adv or 'POSITION' in adv else 'CONFLICT'
                W('R5', sev, t, r['physical'], a, b,
                           'Line up and wait and a landing authorization recorded on the same runway.',
                           'takeoff issued and airborne, or landing authorization withdrawn')
            elif same and 'OCCUPYING' in types and types & {'LANDING', 'TAKEOFF'}:
                occ = self._of(r, o, 'OCCUPYING')
                W('R10', 'CONFLICT', t, r['physical'], a, b,
                           f"{occ['callsign']} is recorded as occupying runway {occ['runway_end']} ({occ['basis']}) while a "
                           f"{(r if r is not occ else o)['type'].lower()} authorization is recorded on the same runway.",
                           f"{occ['callsign']} reported/observed clear of the runway")
            elif same and types == {'CROSSING'} and o.get('at') == r.get('at'):
                W('R11', 'CAUTION', t, r['physical'], a, b, 'Two crossing authorizations at the same point.',
                           'first crossing reported complete')

    @staticmethod
    def _of(r, o, typ):
        return r if r['type'] == typ else o

    # ------------------------------------------------------------------ timers
    def tick(self, t):
        self.now = t
        while True:   # timers queued by a timer and already due (e.g. AUTOCLOSE at a soft expiry) run in the same tick
            due = [x for x in self.timers if x[0] <= t]
            if not due:
                break
            self.timers = [x for x in self.timers if x[0] > t]
            self._run_timers(due)
        self._prune()

    def _run_timers(self, due):
        for due_t, rule, payload in sorted(due, key=lambda x: x[0]):
            if rule in ('R8', 'R9'):
                r = self._res.get(payload)
                if r and r['status'] not in CLOSED:
                    if rule == 'R8':
                        self._warn('R8', 'REMINDER', due_t, r['physical'], self._ref(r), None,
                                   f"Crossing authorization for {r['callsign']} on {r['runway_end']} is {self.T['R8']} s old with no "
                                   'crossing-complete / clear-of-runway report. The runway stays reserved.',
                                   'crossing complete reported')
                    else:
                        self._warn('R9', 'REMINDER', due_t, r['physical'], self._ref(r), None,
                                   f"{r['callsign']} has been in line up and wait for {self.T['R9']} s with no further instruction.",
                                   'takeoff clearance or further instruction')
            elif rule == 'R7':
                p = payload
                if not p.get('acknowledged'):
                    self._warn('R7', 'CAUTION', due_t, p['physical'], {'callsign': p['callsign'], 'type': 'HOLD_SHORT', 't_issued': p['t'],
                               'event_id': p['event_id'], 'hold': p['hold']}, None,
                               f"No readback naming the runway hold ({p['hold_text']}) was heard from {p['callsign']} within {self.T['R7']} s.",
                               'a readback that names the hold')
            elif rule == 'EXPIRE':
                r = self._res.get(payload)
                if r and r['status'] not in CLOSED and r['status'] != UNVERIFIED:
                    self._soft_expire(r, due_t)
            elif rule == 'HARD':
                r = self._res.get(payload)
                if r and r['status'] not in CLOSED:
                    self._hard_drop(r, due_t)
            elif rule == 'AUTOCLOSE':
                w = self._open.get(payload)
                if w and w['severity'] in CONFLICT_LEVEL and self._all_unverified(w) and due_t - w['t_fired'] >= self.auto_close_s:
                    self._auto_close(w, due_t)
            elif rule == 'R13':
                r = next((x for x in self.reservations if x['reservation_id'] == payload), None)
                dup = any(w['rule_id'] == 'R13' and w['authorization_a'].get('event_id') == (r or {}).get('event_id') for w in self._open.values())
                if dup:
                    for w in list(self._open.values()):
                        if w['rule_id'] == 'R13' and w['authorization_a'].get('event_id') == r['event_id']:
                            w.setdefault('repeat_stop_calls_t', []).append(r['cancel_t'])
                elif r and r.get('cancel_status') == 'unacknowledged':
                    self._warn('R13', 'CAUTION', due_t, r['physical'], self._ref(r), None,
                               f"Stop/cancel issued to {r['callsign']} at t={r['cancel_t']}; no acknowledgement heard in {self.T['R13']} s. "
                               'The reservation is kept: the engine does not assume the stop took effect.',
                               f"{r['callsign']} acknowledges, or an observed/reported stop")

    # ------------------------------------------------------------------ ingest
    def ingest(self, ev, t=None):
        t = ev['event_time'] if t is None else t
        self.tick(t)
        self.events.append(ev)
        cs_list = list(dict.fromkeys(self._resolve_callsigns(ev)))   # 'Truck 1, stop, Truck 1, stop' -> one actor
        act, kind = ev['speech_act'], ev['kind']
        if act == 'instruction_or_readback':
            act = self._disambiguate(ev, cs_list, t)
            ev['speech_act_resolved'] = act
        if kind == 'REPORT_HOLDING_SHORT' and act == 'report' and any(
                p['callsign'] in cs_list and p['kind'] in ('TAXI', 'HOLD_SHORT') and (p.get('hold') or {}).get('runway')
                and p.get('acknowledged') not in (True, 'superseded') and t - p['t'] <= self.window for p in self.pending):
            act = 'readback'   # 2-4-3b: 'holding short of runway X' right after a recorded hold short is its read back
            ev['speech_act_resolved'] = act
        if not cs_list and act == 'readback' and self.last_addressed:
            cs_list = [self.last_addressed]
            self.notes.append({'t': t, 'event_id': ev['event_id'], 'note': f'no callsign heard; readback attributed to last addressed {self.last_addressed} (heuristic)'})
        if not cs_list and act == 'instruction' and self.last_speaker and t - self.last_speaker[1] <= 20:
            cs_list = [self.last_speaker[0]]
            self.notes.append({'t': t, 'event_id': ev['event_id'], 'note': f'instruction with no callsign attributed to the last station heard, {cs_list[0]} (heuristic)'})
        if not cs_list and act == 'report' and kind == 'CROSSING_COMPLETE':
            open_x = self._active(type='CROSSING')
            if len(open_x) == 1:
                cs_list = [open_x[0]['callsign']]
                self.notes.append({'t': t, 'event_id': ev['event_id'], 'note': f'crossing complete with no callsign applied to the only open crossing ({cs_list[0]})'})
        if act == 'instruction' and cs_list:
            self.last_addressed = cs_list[-1]
        if act in ('readback', 'report', 'request') and cs_list:
            self.last_speaker = (cs_list[-1], t)
        for cs in cs_list or [None]:
            if cs is None:
                if act != 'unknown':
                    self.notes.append({'t': t, 'event_id': ev['event_id'], 'note': f'{kind} with no attributable callsign; recorded only'})
                continue
            getattr(self, '_on_' + act, self._on_unknown)(cs, ev, t)
        self._prune()
        return ev

    def _disambiguate(self, ev, cs_list, t):
        cs = cs_list[-1] if cs_list else self.last_addressed
        for p in reversed(self.pending):
            fam = p['kind'] == ev['kind'] or {p['kind'], ev['kind']} <= {'TAXI', 'HOLD_SHORT'}
            if p['callsign'] == cs and t - p['t'] <= self.window and fam and p.get('acknowledged') not in (True, 'superseded'):
                return 'readback'
        return 'instruction'

    def _on_unknown(self, cs, ev, t):
        if ev['speech_act'] in ('unknown',):
            return

    def _on_request(self, cs, ev, t):
        self._state(cs, 'reported', f"requests {ev['kind'].lower()}" + (f" runway {ev['runway']} at {ev['crossing_at']}" if ev.get('runway') else ''), ev, t)

    def _on_instruction(self, cs, ev, t):
        kind = ev['kind']
        self._state(cs, 'authorized', describe(ev), ev, t)
        if kind in RESERVATION_FOR:
            if kind == 'TAKEOFF_CLEARANCE':
                for r in self._active(callsign=cs, type='LUAW'):
                    self._release(r, t, 'takeoff clearance issued')
            self._reserve(cs, RESERVATION_FOR[kind], ev.get('runway'), ev, t, extra={'at': ev.get('crossing_at')})
            self.pending.append({'callsign': cs, 'kind': kind, 't': t, 'event_id': ev['event_id'], 'runway': ev.get('runway'),
                                 'hold': None, 'crossing_at': ev.get('crossing_at')})
        elif kind in ('TAXI', 'HOLD_SHORT'):
            # Franco, Oct 9: a later GROUND TAXI CLEARANCE releases this aircraft's landing (inferred clear, not observed);
            # an exit instruction alone ("turn right at E, contact ground") only sets 'exiting' and keeps the reservation
            if kind == 'TAXI' and not any(h.get('on_runway') for h in ev['hold_constraints']):
                is_taxi_clearance = bool(re.search(r'\bTAXI(?:ING)?\b|\bPROCEED VIA\b', ev.get('normalized_text') or ''))   # 3-7-2a: taxi / continue taxiing / proceed via
                for r in self._active(callsign=cs, type='LANDING'):
                    if is_taxi_clearance:
                        self._release(r, t, 'ground taxi clearance issued (inferred clear of the runway; not observed)')
                        self._state(cs, 'inferred', f"taxi clearance issued; runway {r['runway_end']} assumed vacated (not observed)", ev, t)
                    else:
                        self._state(cs, 'inferred', f"exiting runway {r['runway_end']} (exit instruction issued; landing reservation kept until a clear report or a taxi clearance)", ev, t)
            for r in self._active(callsign=cs, type='OCCUPYING'):
                if not any(h.get('on_runway') for h in ev['hold_constraints']):
                    self._release(r, t, 'exit/taxi instruction issued (inferred clear; not observed)')
                    self._state(cs, 'inferred', f"exit instruction issued; runway {r['runway_end']} assumed vacating (not observed)", ev, t)
            for p in self.pending:
                if p['callsign'] == cs and p['kind'] in ('TAXI', 'HOLD_SHORT') and not p.get('acknowledged'):
                    p['superseded_by'] = ev['event_id']
                    p['acknowledged'] = 'superseded'
                    ev['supersedes_event_id'] = p['event_id']
                    self._clear(lambda w, p=p: w['rule_id'] == 'R7' and w['authorization_a'].get('event_id') == p['event_id'], t, 'superseded by a new instruction')
            for h in ev['hold_constraints']:
                if h.get('on_runway'):
                    self._reserve(cs, 'OCCUPYING', h['on_runway'], ev, t, extra={'basis': 'authorized: hold on runway'})
            pend = {'callsign': cs, 'kind': kind, 't': t, 'event_id': ev['event_id'], 'runway': ev.get('runway'),
                    'route': ev.get('route'), 'hold': (ev['hold_constraints'] or [None])[0]}
            self.pending.append(pend)
            hold = pend['hold'] or {}
            if hold.get('runway'):
                _, phys = self._physical(hold['runway'])
                pend.update(physical=phys[0] if phys else None, hold_text=f"hold short runway {hold['runway']}" + (f" at {hold['at']}" if hold.get('at') else ''))
                self.timers.append((t + self.T['R7'], 'R7', pend))
            if kind == 'TAXI' and ev.get('route'):
                self._route_check(cs, ev, t)
        elif kind == 'CANCEL':
            rs = self._active(callsign=cs)
            if not rs:
                self.notes.append({'t': t, 'event_id': ev['event_id'], 'note': f'stop/cancel to {cs}: no recorded reservation for {cs}'})
            for r in rs:
                r.update(cancel_status='unacknowledged', cancel_t=t, status='cancel_unacknowledged')
                self.timers.append((t + self.T['R13'], 'R13', r['reservation_id']))
        elif kind == 'GO_AROUND':
            rs = self._active(callsign=cs, type='LANDING')
            if not rs:
                prior = [r for r in self.reservations if r['callsign'] == cs and r['type'] == 'LANDING']
                self.notes.append({'t': t, 'event_id': ev['event_id'], 'note': f'go-around for {cs}: ' +
                                   ('landing authorization already released' if prior else 'no recorded landing authorization (not captured)')})
            for r in rs:
                self._release(r, t, f'go-around instructed to {cs}')
        elif kind == 'FREQ_CHANGE':
            self._state(cs, 'authorized', f"contact {ev.get('normalized_text')}", ev, t)

    def _route_check(self, cs, ev, t):
        rep = self.ap.route_runway_check(ev['route'])
        ev['route_check'] = rep
        held = set()
        for h in ev['hold_constraints']:
            if h.get('runway'):
                held |= set(self._physical(h['runway'])[1])
        crossing_ok = {r['physical'] for r in self._active(callsign=cs, type='CROSSING')}
        for item in rep:
            for x in item['crossings']:
                if x['runway_id'] not in held and x['runway_id'] not in crossing_ok:
                    self._warn('R6', 'CAUTION', t, x['runway_id'], {'callsign': cs, 'type': 'TAXI', 't_issued': t, 'event_id': ev['event_id'],
                               'route': ev['route']}, None,
                               f"Route word {item['word']} meets runway {x['runway_id'].split(':')[-1]} at {x['node_id']} on the review-pending "
                               f"draft graph, and no hold-short or crossing clearance for that runway was recorded. Direction of travel on "
                               f"{item['word']} is not established in v1, so this is a possible crossing, not a traced one.",
                               'hold-short for that runway issued and read back, or crossing clearance',
                               extra={'graph_basis': x['node_id'], 'graph_status': 'draft, expert review pending'})
        unk = [i['word'] for i in rep if i['status'] == 'no_crossing_data_in_draft_graph']
        if unk:
            self.notes.append({'t': t, 'event_id': ev['event_id'],
                               'note': f"route words {', '.join(unk)}: no reviewed runway-crossing data in the draft graph; hold coverage not checkable (unresolved, not 'clear')"})

    def _on_readback(self, cs, ev, t):
        p = next((p for p in reversed(self.pending) if p['callsign'] == cs and t - p['t'] <= self.window
                  and p.get('acknowledged') not in ('superseded',) and (p['kind'] == ev['kind'] or {p['kind'], ev['kind']} <= {'TAXI', 'HOLD_SHORT', 'REPORT_HOLDING_SHORT'})), None)
        if p is None:
            self._state(cs, 'reported', f"readback-like transmission with no matching recorded instruction: {ev['normalized_text']}", ev, t)
            return
        issues = []
        if p.get('runway') and ev.get('runway') and not _same_runway(p['runway'], ev['runway']):
            issues.append(('discrepant_runway', f"instruction runway {p['runway']}, readback runway {ev['runway']}"))
        elif p.get('runway') and not ev.get('runway') and p['kind'] in RESERVATION_FOR:   # 2-4-3 NOTE 1c: the runway is read back
            issues.append(('missing_runway', f"instruction runway {p['runway']}, no runway in the readback"))
        ph = p.get('hold') or {}
        rh = (ev['hold_constraints'] or [None])[0] or {}
        if ph.get('runway'):
            if not rh:
                issues.append(('hold_not_heard', 'readback clipped before the hold' if ev.get('clipped') else 'readback did not include the hold'))
            elif rh.get('runway') and rh['runway'] != ph['runway']:
                issues.append(('discrepant_hold', f"hold short runway {ph['runway']} instructed, readback says runway {rh['runway']}"))
        if not issues:
            p['acknowledged'] = True
            self._state(cs, 'acknowledged', 'read back: ' + describe(ev), ev, t)
            for r in self.reservations:
                if r['event_id'] == p['event_id'] and r['ack'] == 'pending':
                    r['ack'] = 'acknowledged'
            self._clear(lambda w: w['rule_id'] in ('R7', 'R14', 'R15', 'R16') and w['authorization_a'].get('callsign') == cs, t, f"readback {ev['event_id']} named the hold")
            return
        for code, text in issues:
            # Franco, Oct 9 (round 4): a MISSING runway/hold is an incomplete readback (R15, info). Only an explicit, DIFFERENT
            # runway/hold heard with Whisper word confidence >= readback_conf_min is a mismatch candidate (R14, shadow);
            # below that, or with no confidence available, it is R16 (info).
            self._state(cs, 'acknowledged', f'{code}: {text}', ev, t)
            a_ = {'callsign': cs, 'type': p['kind'], 't_issued': p['t'], 'event_id': p['event_id'], **({'hold': ph} if ph else {})}
            b_ = {'callsign': cs, 'type': 'READBACK', 't_issued': t, 'event_id': ev['event_id']}
            conf = ev.get('runway_confidence')
            if code in ('discrepant_hold', 'discrepant_runway'):
                p['acknowledged'] = 'discrepant'
                strong = conf is not None and self.rb_conf_min is not None and conf >= self.rb_conf_min
                self._warn('R14' if strong else 'R16', 'CAUTION' if strong else 'INFO', t, p.get('physical'), a_, b_,
                           f'Readback differs from the recorded instruction: {text}. Whisper confidence on the read-back runway: '
                           + (f'{conf:.2f}' if conf is not None else 'not available') + (f' (threshold {self.rb_conf_min}).' if self.rb_conf_min is not None else '.'),
                           'controller corrects; correct readback heard', extra={'mismatch': code, 'runway_confidence': conf})
            elif code in ('hold_not_heard', 'missing_runway') and (ph.get('runway') or code == 'missing_runway'):
                p['acknowledged'] = 'not_heard'
                self._warn('R15', 'INFO', t, p.get('physical'), a_, b_,
                           (f"{p.get('hold_text', 'runway hold')} not heard in the readback ({text}). Acknowledged state for the hold = UNKNOWN, not 'acknowledged'."
                            if code == 'hold_not_heard' else f"The readback did not name the runway ({text})."),
                           'a readback that names the runway' + (' and the hold' if ph.get('runway') else ''),
                           extra={'mismatch': code, 'parse_confidence_heuristic': ev['uncertainty']['confidence_heuristic']})
        # drop the pending R7 timer for this instruction: the readback was heard, the issue is now explicit
        p['acknowledged'] = p.get('acknowledged') or 'issue'

    def _on_report(self, cs, ev, t):
        kind = ev['kind']
        self._state(cs, 'reported', describe(ev).replace('report ', ''), ev, t)
        if kind == 'REPORT_REJECTED_TAKEOFF':
            for r in self._active(callsign=cs, type='TAKEOFF'):
                self._release(r, t, 'rejected takeoff reported')
            rwy = ev.get('runway') or next((r['runway_end'] for r in reversed(self.reservations) if r['callsign'] == cs and r['type'] == 'TAKEOFF'), None)
            if rwy:
                self._reserve(cs, 'OCCUPYING', rwy, ev, t, extra={'basis': 'reported: rejected takeoff, still on runway'})
        elif kind == 'GO_AROUND':
            for r in self._active(callsign=cs, type='LANDING'):
                self._release(r, t, 'go-around reported')
        elif kind == 'CROSSING_COMPLETE':
            # another aircraft's open crossing is closed only by a report with NO callsign heard (attributed in ingest);
            # a named aircraft's 'clear of the runway' never closes someone else's crossing (Franco, Oct 9)
            rs = self._active(callsign=cs, type='CROSSING') or ([r for r in self._active(type='CROSSING')] if ev.get('runway') is None and not ev.get('actor_candidates') else [])
            for r in rs[:1]:
                self._release(r, t, 'crossing complete reported')
            if not rs:   # Franco, Oct 9: the landing aircraft's own "clear of the runway" report releases its landing (reported, not observed)
                rwy = ev.get('runway')
                for r in self._active(callsign=cs, type='LANDING'):
                    if rwy is None or str(r.get('runway_end', '')).lstrip('0') == str(rwy).lstrip('0') or rwy in str(r.get('physical', '')):
                        self._release(r, t, 'pilot reported clear of the runway (reported, not observed)')
        elif kind == 'REPORT_HOLDING_POSITION':
            for r in self.reservations:
                if r['callsign'] == cs and r.get('cancel_status') == 'unacknowledged':
                    r['cancel_status'] = 'acknowledged'
                    self._clear(lambda w: w['rule_id'] == 'R13' and w['authorization_a'].get('callsign') == cs, t, 'stop acknowledged')
            if not any(r['callsign'] == cs for r in self.reservations):
                self.notes.append({'t': t, 'event_id': ev['event_id'], 'note': f'{cs} reports holding position (acknowledges stop)'})

    def observe(self, callsign, text, t, source='surveillance'):
        """Record an independent observation (ADS-B/ASDE-X/simulated track summary). Never derived from voice."""
        self.tick(t)
        self._state(callsign, 'observed', f'{text} [{source}]', None, t)

    # ------------------------------------------------------------------ export
    def export(self):
        return {'airport': self.ap.version, 'events': self.events, 'reservations': self.reservations,
                'warnings': self.warnings, 'notes': self.notes, 'actors': self.actors,
                'expiries': self.expiries, 'cap_events': self.cap_events, 'health': self.health, 'history_pruned': dict(self.pruned),
                'claim_limit': 'Recorded authorizations and open obligations only; no prevention claims.'}

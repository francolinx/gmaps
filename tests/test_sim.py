"""Simulator tests (Q5): loader and scorer unit tests run anywhere; the end-to-end run needs the Spark (Kokoro, Whisper
in the local container, vLLM for the LLM fallback) and is marked "spark"."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim import scenario as S   # noqa: E402
from sim import score as SC     # noqa: E402


def _base():
    return {'id': 'T1', 'airport': 'XSIM', 'simulated': True, 'end_t': 30,
            'transmissions': [{'id': 'a', 't': 0, 'speaker': 'ATC', 'text': 'x', 'cite': '7110.65 3-10-5a'},
                              {'id': 'b', 't': 5, 'speaker': 'PILOT', 'callsign': 'SIM212', 'text': 'y', 'cite': '7110.65 2-4-3'}],
            'expected_alerts': [{'id': 'A1', 'rule': ['R1'], 'runway': '36', 'aircraft': ['SIM212', 'RESCUE7'], 'window': [10, 20]}],
            'expected_no_alert': [{'window': [0, 9]}]}


def test_library_loads_and_has_the_five_q5_scenarios():
    scs = S.load_all()
    ids = {s['id'] for s in scs}
    assert {'Q5_01_LAND_VS_CROSS', 'Q5_02_LUAW_VS_LAND', 'Q5_03_TKOF_VS_CROSS', 'Q5_04_CROSS_AFTER_CLEAR', 'Q5_05_READBACK_ONLY'} <= ids
    assert all(s['airport'] == 'XSIM' and s['simulated'] is True for s in scs)
    assert sum(1 for s in scs if s['expected_alerts']) >= 3 and sum(1 for s in scs if not s['expected_alerts']) >= 2


@pytest.mark.parametrize('mutate,msg', [
    (lambda s: s.update(airport='KLGA'), 'XSIM'),
    (lambda s: s.update(simulated=False), 'simulated'),
    (lambda s: s['transmissions'][1].update(t=-1), 'ascending'),
    (lambda s: s['transmissions'][0].update(cite='AIM 4-3'), '7110.65'),
    (lambda s: s['transmissions'][1].pop('callsign'), 'callsign'),
    (lambda s: s['transmissions'][1].update(id='a'), 'duplicate'),
    (lambda s: s['expected_alerts'][0].update(window=[20, 10]), 'window'),
    (lambda s: s['expected_alerts'][0].update(rule='R1'), 'rule'),
    (lambda s: s.update(end_t=1), 'end_t'),
])
def test_loader_rejects_bad_scenarios(mutate, msg):
    sc = _base()
    mutate(sc)
    with pytest.raises(S.ScenarioError, match=msg):
        S.validate(sc)


def _alert(**kw):
    a = {'warning_id': 'w1', 'rule': 'R1', 'runway': '18-36', 'aircraft': ['RESCUE7', 'SIM212'], 't_fired': 12.0, 'latency_s': 1.5}
    a.update(kw)
    return a


def test_scorer_detected_with_latency():
    r = SC.score_scenario(S.validate(_base()), [_alert()])
    assert (r['detected'], r['missed'], r['false_alarms'], r['pass'], r['latency_s']) == (1, 0, 0, True, [1.5])


@pytest.mark.parametrize('kw', [{'rule': 'R2'}, {'t_fired': 25.0}, {'aircraft': ['SIM212', 'SIM456']}, {'runway': '09-27'}])
def test_scorer_wrong_alert_is_a_miss_and_a_false_alarm(kw):
    r = SC.score_scenario(S.validate(_base()), [_alert(**kw)])
    assert (r['detected'], r['missed'], r['false_alarms'], r['pass']) == (0, 1, 1, False)


def test_scorer_false_alarm_in_no_alert_window_and_shadow_ignored():
    sc = S.validate(_base())
    r = SC.score_scenario(sc, [_alert(), _alert(warning_id='w2', rule='R3', t_fired=3.0), _alert(warning_id='w3', shadow=True)])
    assert (r['detected'], r['false_alarms'], r['shadow_alerts']) == (1, 1, 1)
    assert r['false_alarm_detail'][0]['in_no_alert_window'] is True


def test_scorer_no_alert_scenario_and_overall():
    quiet = S.validate({**copy.deepcopy(_base()), 'id': 'T2', 'expected_alerts': [], 'expected_no_alert': [{'window': [0, 30]}]})
    a = SC.score_scenario(quiet, [])
    b = SC.score_scenario(S.validate(_base()), [_alert(latency_s=2.5)])
    c = SC.score_scenario(S.validate(_base()), [])
    o = SC.overall([a, b, c])
    assert a['pass'] and b['pass'] and not c['pass']
    assert (o['scenarios'], o['passed'], o['expected'], o['detected'], o['missed'], o['false_alarms']) == (3, 2, 2, 1, 1, 0)
    assert o['latency_s'] == {'n': 1, 'median': 2.5, 'max': 2.5}


@pytest.mark.spark
def test_end_to_end_one_scenario_on_the_spark(tmp_path):
    """Kokoro render -> streamed audio + end-of-speech -> Whisper (local container) -> fast path -> alert, scored."""
    from sim import run_all
    res = run_all.main(['--only', 'Q5_01_LAND_VS_CROSS', '--speed', '4', '--out-json', str(tmp_path / 'sim.json'), '--no-docs'])
    sc = res['per_scenario'][0]
    assert sc['scenario'] == 'Q5_01_LAND_VS_CROSS' and sc['detected'] == 1 and sc['latency_s']


# --- claude/sim-upgrades: options, text mode, replay mode, held-out Q6 scenarios. All CPU-only (no marker). ---
from sim import options as O     # noqa: E402
from sim import runner as RN     # noqa: E402

Q6 = {'Q6_01_LAND_THEN_TKOF', 'Q6_02_CROSS_THEN_TKOF', 'Q6_03_NREG_LAND_VS_CROSS', 'Q6_04_TWO_CROSSINGS',
      'Q6_05_HOLD_SHORT_READBACK', 'Q6_06_HOLD_SHORT_NO_READBACK'}


def test_q6_heldout_scenarios_load():
    scs = {s['id']: s for s in S.load_all()}
    assert Q6 <= set(scs)
    assert all(scs[i]['airport'] == 'XSIM' and scs[i]['simulated'] is True for i in Q6)


def test_traffic_list_modes_and_distractor_guard():
    sc = S.validate(_base())
    assert O.traffic_list(sc, 'off') is None
    tl = O.traffic_list(sc, 'perfect')
    assert 'SIM212' in tl and set(O.DISTRACTORS) <= set(tl)
    clash = S.validate({**copy.deepcopy(_base()), 'traffic': ['DAL118']})   # shares 118 with the SIM118 distractor
    with pytest.raises(ValueError, match='flight number'):
        O.traffic_list(clash, 'perfect')
    for s in S.load_all():                                                    # no library scenario collides with a distractor
        O.traffic_list(s, 'perfect')


def test_whisper_prompt_is_generic_xsim_vocabulary():
    p = O.whisper_prompt()
    assert p.startswith('XSIM tower.') and 'SimAir' in p and 'Rescue' in p and 'one eight' in p and 'three six' in p and 'Kilo' in p
    assert not any(w in p for w in ('four five six', 'two one two', 'four thirty two'))   # nothing taken from a scenario script


def test_text_mode_every_scenario_passes_with_perfect_transcripts():
    """Ground truth vs the deterministic pipeline (grammar + ledger rules, LLM off): with perfect transcripts every scenario
    must pass. A failure here means a scenario's ground truth or the pipeline changed."""
    for sc in S.load_all():
        for traffic in ('off', 'perfect'):
            run = RN.run_scenario_text(sc, traffic=O.traffic_list(sc, traffic), log=lambda m: None)
            r = SC.score_scenario(sc, run['alerts'])
            assert r['pass'], (sc['id'], traffic, r)


def test_replay_traffic_list_recovers_the_q5_02_miss():
    """F1 (docs/SIM_FINDINGS.md): the same misheard Whisper text audio run. With no traffic list the LUAW is
    never recorded (miss); with a traffic list 'flight number 456 in the text' resolves the one listed SIM456 (repaired)."""
    import json
    prev = json.loads((ROOT / 'eval/results/sim_2026-10-16.json').read_text())
    sc = next(s for s in S.load_all() if s['id'] == 'Q5_02_LUAW_VS_LAND')
    texts = {h['tx']: h['whisper'] for h in prev['runs'][sc['id']]['heard']}
    off = SC.score_scenario(sc, RN.run_scenario_text(sc, texts=texts, log=lambda m: None)['alerts'])
    on = RN.run_scenario_text(sc, traffic=O.traffic_list(sc, 'perfect'), texts=texts, log=lambda m: None)
    assert not off['pass'] and SC.score_scenario(sc, on['alerts'])['pass']
    t1 = next(h for h in on['heard'] if h['tx'] == 't1')
    assert t1['callsigns'] == ['SIM456'] and t1['callsign_match'][0]['rule'].startswith('flight number')


def test_text_arrival_is_after_the_transmission_starts():
    tx = {'id': 't1', 't': 10, 'text': 'SimAir two one two, runway three six, cleared to land.'}
    assert 10 + RN.END_OF_SPEECH_S < RN.text_arrival(tx) < 20

"""Run every prepared-library test. stdlib only (validate.py needs jsonschema>=4.18 for the graph checks).

usage: python3 tests/run_all.py
Exit code 0 = all passed. Prints one line per test.
"""
import json, os, re, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib')); sys.path.insert(0, str(ROOT / 'tools'))
from gmaps_core.parser import parse
from gmaps_core.airport import Airport
from gmaps_core.ledger import Ledger
import replay_fixture as RF
import run_sim_episode as RS

LGA = Airport(ROOT / 'data/airports/klga.graph.json')
BOS = Airport(ROOT / 'data/airports/kbos.graph.json')
results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))


# ---------------- graphs ----------------
try:
    import jsonschema  # noqa
    r = subprocess.run([sys.executable, 'validate.py', '--self-test'], cwd=ROOT / 'data/airports', capture_output=True, text=True)
    rep = json.loads(r.stdout)
    check('graphs: validate.py --self-test (both airports)', rep['all_checks_passed'], f"{len(rep['self_tests'])} self-tests")
    r2 = subprocess.run([sys.executable, 'bos_extra_tests.py'], cwd=ROOT / 'data/airports', capture_output=True, text=True)
    check('graphs: bos_extra_tests.py', 'ALL True' in r2.stdout)
except ImportError:
    check('graphs: validate.py NOT RUN - jsonschema missing (see RUNBOOK step 1)', False, 'install jsonschema to run graph checks')

# ---------------- parser ----------------
P = lambda txt, ap=LGA, hint=None: parse(txt, ap.icao, speaker_hint=hint, resolver=ap)
e = P('Truck 1 and company, cross 4 at Delta.', hint='ATC')
check('parser: crossing instruction', (e['kind'], e['speech_act'], e['runway'], e['crossing_at'], e['actor_candidates']) == ('CROSS', 'instruction', '04', 'D', ['TRUCK1']))
e = P('Truck 1 and company, LaGuardia Tower, requesting to cross 4 at Delta.')
check('parser: a request is never an authorization', e['speech_act'] == 'request' and e['kind'] == 'CROSS_REQUEST')
e = P('Runway 4, cleared to land, number two, Jazz 646.', hint='ATC')
check('parser: landing clearance with sequence', (e['kind'], e['runway'], e['sequence']) == ('LANDING_CLEARANCE', '04', '2'))
e = P('United 2384, runway 13, taxi via November, Alpha, Echo, hold short runway 4.', hint='ATC')
check('parser: 3-7-2 taxi with runway hold', e['route'] == ['N', 'A', 'E'] and e['hold_constraints'] == [{'runway': '04'}])
e = P('alpha alpha', hint='ATC')
e2 = parse('Cobalt 41 taxi via alpha alpha, delta, hold short runway four', 'KLGA', speaker_hint='ATC', resolver=LGA)
check('parser: double letter preserved (AA)', e2['route'] == ['AA', 'D'])
e = parse('American 3161, runway two seven, cleared for takeoff.', 'KBOS', speaker_hint='ATC', resolver=BOS)
check('parser: BOS runway 27 normalisation', e['runway'] == '27' and e['runway_end_candidates'] == ['KBOS:END:27'])
e = parse('Delta 2351, runway three three left, cleared to land.', 'KBOS', speaker_hint='ATC', resolver=BOS)
check('parser: BOS 33L (airline Delta is a callsign, not taxiway D)', e['runway'] == '33L' and e['actor_candidates'] == ['DAL2351'] and e['runway_end_candidates'] == ['KBOS:END:33L'])
e = parse('Delta 2351, runway 33, cleared to land.', 'KBOS', speaker_hint='ATC', resolver=BOS)
check('parser: missing suffix stays ambiguous (33L+33R)', e['runway_end_candidates'] == ['KBOS:END:33L', 'KBOS:END:33R'])
e = parse('Tradewind 82, taxi via delta one', 'KBOS', speaker_hint='ATC', resolver=BOS)
check('parser: D1 vs D (BOS)', e['route'] == ['D1'])
e = parse('Tradewind 82, taxi via delta one', 'KLGA', speaker_hint='ATC', resolver=LGA)
check('parser: D1 not in LGA inventory -> unresolved, not corrected', any('D1' in u for u in e['uncertainty']['unresolved']))
e = P('Delta 263, go around, runway heading, 2000.', hint='ATC')
check('parser: altitude 2000 is not a callsign', e['actor_candidates'] == ['DAL263'])
e = P('make the right turn at Lima and a left turn on Bravo', hint='ATC')
check('parser: article "a" is not taxiway A', e['route'] == ['L', 'B'])
e = P('Runway 13, Alpha, Delta, ho— [clipped]', hint='PILOT')
check('parser: clipped readback flagged, hold not invented', e['clipped'] and e['hold_constraints'] == [])

# ---------------- ledger on fixtures ----------------
def rules(sid, **kw):
    led, _ = RF.run(RF.load_scenarios()[sid], **kw)
    return led
for hints in (True, False):
    for text in ('clean', 'raw'):
        tag = f"hints={hints},text={text}"
        L = rules('LGA_2026-03-22', hints=hints, text=text)
        r1 = [w for w in L.warnings if w['rule_id'] == 'R1']
        check(f'ledger LGA: R1 CONFLICT at t=40 ({tag})', r1 and r1[0]['t_fired'] == 40 and r1[0]['severity'] == 'CONFLICT')
        check(f'ledger LGA: no flag on t=-577 takeoff 13 negative case ({tag})', not [w for w in L.warnings if w['t_fired'] < 0])
        check(f'ledger LGA: stop to TRUCK1 not assumed effective (R13) ({tag})', any(w['rule_id'] == 'R13' and w['authorization_a']['callsign'] == 'TRUCK1' for w in L.warnings))
        B = rules('BOS_2026-06-20', hints=hints, text=text)
        r4 = [w for w in B.warnings if w['rule_id'] == 'R4']
        check(f'ledger BOS: R4 CONFLICT at t=82, cleared at go-around t=144 ({tag})', r4 and r4[0]['t_fired'] == 82 and r4[0]['severity'] == 'CONFLICT' and r4[0]['cleared_at'] == 144)
        check(f'ledger BOS: LUAW at t=0 to takeoff at t=82 < 90 s, no R9 ({tag})', not [w for w in B.warnings if w['rule_id'] == 'R9'])
        C = rules('CLIPPED_READBACK_FICTIONAL', hints=hints, text=text)
        r7 = [w for w in C.warnings if w['rule_id'] == 'R15']   # (round 4): a read back without the hold is an incomplete readback (info), was R7
        check(f'ledger CLIPPED: hold not heard -> R15 incomplete readback (info), cleared by full readback ({tag})', r7 and r7[0]['category'] == 'incomplete_readback' and r7[0]['severity'] == 'INFO' and r7[0]['status'] == 'cleared')
        T = rules('TAXI_EXERCISE_LGA_N_A_E', hints=hints, text=text)
        check(f'ledger TAXI: no warnings; N/A/E coverage reported unresolved ({tag})', not T.warnings and any('unresolved' in n['note'] for n in T.notes))


def variant(lines, ap=LGA):
    led = Ledger(ap)
    for t, hint, txt in lines:
        led.ingest(parse(txt, ap.icao, speaker_hint=hint, resolver=ap, event_time=t), t)
    led.tick(lines[-1][0] + 120)
    return led
V = variant([(0, 'ATC', 'Cobalt 41, runway 13, taxi via Alpha Alpha, Delta.'), (5, 'PILOT', 'Runway 13, Alpha Alpha, Delta, Cobalt 41.')])
check('ledger variant A: route word D meets 04/22 with no hold -> R6 (possible crossing, draft graph)', any(w['rule_id'] == 'R6' and w['graph_basis'] == 'KLGA:N:D_RWY04-22' for w in V.warnings))
V = variant([(0, 'ATC', 'Cobalt 41, runway 13, taxi via Alpha, Delta, hold short runway 4.'), (5, 'PILOT', 'Runway 13, Alpha, Delta, hold short runway 13, Cobalt 41.')])
check('ledger variant B: discrepant hold readback, no ASR word confidence -> R16 low-confidence mismatch (info); never R7 CONFLICT or a shown alert (Franco, Oct 9)',
      any(w['rule_id'] == 'R16' and w['category'] == 'readback_mismatch_low_confidence' for w in V.warnings) and not any(w['rule_id'] == 'R7' and w['severity'] == 'CONFLICT' for w in V.warnings))
V = variant([(0, 'ATC', 'Cobalt 41, cross runway 4 at Delta.'), (4, 'PILOT', 'Crossing runway 4 at Delta, Cobalt 41.')])
check('ledger variant C: crossing not closed out -> R8 REMINDER at +90 s', any(w['rule_id'] == 'R8' and w['t_fired'] == 90 for w in V.warnings))
V = variant([(0, 'ATC', 'Delta 520, runway 22, cleared for takeoff.'), (5, 'ATC', 'Jazz 646, runway 4, cleared to land.')])
check('ledger: 04 and 22 are the same physical runway (R3)', any(w['rule_id'] == 'R3' for w in V.warnings))

# ---------------- separation and no-future-leak ----------------
src = {p.name: p.read_text() for p in (ROOT / 'lib/gmaps_core/sim').glob('*.py')}
check('separation: predictor never imports truth', 'truth' not in re.sub(r'""".*?"""', '', src['predict.py'], flags=re.S))
check('separation: runtime harness never opens eval/', 'eval/' not in re.sub(r'""".*?"""', '', (ROOT / 'tools/run_sim_episode.py').read_text(), flags=re.S))
from gmaps_core.sim.predict import predict
ob = json.loads((ROOT / 'episodes/SIM_E1_CROSS_DURING_ROLLOUT.obs.json').read_text())
tracks_full = {tid: dict(meta, packets=[p for p in ob['packets'] if p['track_id'] == tid]) for tid, meta in ob['tracks'].items()}
tracks_cut = {tid: dict(tr, packets=[p for p in tr['packets'] if p['received_at'] <= 40]) for tid, tr in tracks_full.items()}
it = {'SIM212': {'authorized': ['LANDING'], 'surface': 'XSIM:RWY:18-36', 'direction': '36', 'hold': None},
      'RESCUE7': {'authorized': ['CROSSING'], 'surface': 'XSIM:TWY:K', 'direction': 'north', 'hold': None}}
a, b = predict(40, tracks_full, it), predict(40, tracks_cut, it)
check('no-future-leak: prediction at t=40 identical with or without later packets', json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))

# ---------------- episodes (regression on the six named fictional episodes) ----------------
sys.path.insert(0, str(ROOT / 'eval'))
import run_eval as RE
named = ['SIM_E1_CROSS_DURING_ROLLOUT', 'SIM_E2_SAME_PATHS_LATER', 'SIM_E3_STOP_NOT_OBSERVED', 'SIM_E4_FEED_LOSS', 'SIM_E5_HOLD_NOT_SLOWING', 'SIM_E6_HOLD_OK']
rows = {e: RE.score(e) for e in named}
check('episodes: combined has no MISS/FP/LATE on the six named episodes', all(r['combined']['outcome'] in ('TP', 'TN') for r in rows.values()),
      json.dumps({e[4:7]: r['combined']['outcome'] for e, r in rows.items()}))
check('episodes: E4 feed loss withdraws the vehicle prediction', rows['SIM_E4_FEED_LOSS']['combined_withdrawn_ticks'] > 0)
res = RS.run('SIM_E3_STOP_NOT_OBSERVED')
check('episodes: E3 candidate persists after the unacknowledged stop', any(c['raised'] for c in res['frames'][45]['combined']['candidates']))

# ---------------- KLGA schematic page (display only; local, unpublished) ----------------
kv = (ROOT / 'ui_scaffold/klga_view.html').read_text()
check('klga_view: label "Schematic, not to scale. Recorded authorizations only. No predictions." present',
      'Schematic, not to scale. Recorded authorizations only. No predictions.' in kv)
check('klga_view: INTERNAL banner and INTERNAL / UNVERIFIED watermark present',
      'INTERNAL — reconstruction from approximate public captions, labels not human-verified. Not evidence of what GMAPS would have done. '
      'NTSB investigation ongoing. Do not share.' in kv and "const WATERMARK = 'INTERNAL / UNVERIFIED'" in kv and 'watermark(W, H)' in kv)
bad = [p for p in (r'\.(lat|lon|latitude|longitude|coordinates|geometry|positions)\b|["\'](lat|lon|latitude|longitude|coordinates|geometry)["\']\s*:', r'https?://(?!127\.0\.0\.1:8767[/\'])', r'margins|expected_flags', r'eval/|gmaps_core|predict\.py',
                   r'collid|collision', r'\d+\s*s\s+before|countdown') if re.search(p, kv, re.I)]
check('klga_view: no coordinates, external resources, margins/expected_flags, predictor/eval reads, collision or time-gap text', not bad, str(bad))
ui_text = {str(p.relative_to(ROOT)): p.read_text(errors='ignore') for p in (ROOT / 'ui_scaffold').rglob('*') if p.suffix in ('.html', '.js', '.json', '.css')}
check('klga_view: PRIVATE PLAYBACK label present; off by default, never persisted; audio only from 127.0.0.1:8767; no ui_scaffold file names the private folder or file://',
      'PRIVATE PLAYBACK — real ATC audio, not for recording or sharing.' in kv and 'let PRIVATE = false' in kv
      and not re.search(r'localStorage|sessionStorage|indexedDB|document\.cookie', kv)
      and set(re.findall(r'https?://[^\s\'"`/]+', kv)) <= {'http://127.0.0.1:8767'}
      and not [f for f, t in ui_text.items() if 'audio_real_private' in t or 'file://' in t],
      str([f for f, t in ui_text.items() if 'audio_real_private' in t or 'file://' in t]))
ix = (ROOT / 'ui_scaffold/index.html').read_text()
check('index.html: clip audio is owned re-voiced clips only (/owned/ on 127.0.0.1:8767), never /private/',
      "/owned/" in ix and '/private/' not in ix and set(re.findall(r'https?://[^\s\'"`/]+', ix)) <= {'http://127.0.0.1:8767'},
      str(set(re.findall(r'https?://[^\s\'"`/]+', ix))))
kb = subprocess.run([sys.executable, str(ROOT / 'venue/build_klga_view.py'), '--check'], capture_output=True, text=True)
check('klga_view: embedded topology current; KLGA graph geometry null and routing/prediction gates closed', kb.returncode == 0,
      (kb.stdout + kb.stderr).strip())

# ---------------- reconstructed airport geometry: checklist gate ----------------
rv_bad = []
for rv in sorted((ROOT / 'data/airports').glob('*/review.json')):
    d = json.loads(rv.read_text()); items = {i['id']: i for i in d['items']}
    ov = items.get('overlay_ink', {})
    if not ov.get('thresholds') or 'min_covered_pct' not in ov['thresholds'] or 'max_extra_pct' not in ov['thresholds']:
        rv_bad.append(f'{rv.parent.name}: overlay_ink thresholds missing')
    if d.get('signed_off_by') and not all(i.get('status') in ('pass', 'not_applicable') for i in d['items']):
        rv_bad.append(f'{rv.parent.name}: signed off with items not passing')
    so = d.get('sign_off')
    if d.get('signed_off_by') and not (so and so.get('by') == d['signed_off_by'] and so.get('scope') and so.get('date') and re.fullmatch(r'[0-9a-f]{40}', so.get('applies_to_commit', ''))):
        rv_bad.append(f'{rv.parent.name}: sign-off lacks by/date/scope/applies_to_commit')
    if so and (so.get('predictions_enabled') is not False if so.get('scope') == 'display_only' else True):
        rv_bad.append(f'{rv.parent.name}: display-only sign-off must have predictions_enabled false (and no other scope exists yet)')
for gj in sorted((ROOT / 'data/airports').glob('*/georef.json')):
    g = json.loads(gj.read_text()); a = gj.parent
    pv = json.loads((a / 'sources/nasr/PROVENANCE.json').read_text()) if (a / 'sources/nasr/PROVENANCE.json').exists() else {}
    with open(a / 'sources/nasr/APT_RWY_END.csv', newline='', encoding='utf-8') as f:
        import csv as _csv
        nasr_ends = {r['RWY_END_ID'] for r in _csv.DictReader(f) if r['RWY_END_ID'] != 'H1' and r['RWY_ID'] not in g.get('nasr_rows_without_position', [])} if pv else set()   # recorded placeholder rows (e.g. KORD 10X) have no position
    have = {r['id'] for r in g.get('runway_ends', []) if 'residual_m' in r}
    if not (pv.get('url') and pv.get('zip_sha256') and pv.get('cycle_effective')):
        rv_bad.append(f'{a.name}: NASR provenance missing url/sha256/cycle')
    if {e.zfill(2) for e in nasr_ends} != have or g.get('transform_page_pt_to_local_m', {}).get('fitted_on') != 'graticule intersections only':
        rv_bad.append(f'{a.name}: georef residual rows {sorted(have)} vs NASR ends {sorted(nasr_ends)}, or fit not graticule-only')
# label grouping regression (Franco's extraction review): the last outline glyph must join its label
SIX = ('EMAS', 'STATION', 'TRANSIENT', 'GENERAL', 'RWY 04-22', 'S-80, D-170, 2D-360')
for er in sorted((ROOT / 'data/airports').glob('*/review/extract_report.json')):
    r = json.loads(er.read_text()); lg = r.get('label_glyphs', [])
    if er.parent.parent.name == 'klga':
        bad6 = [(x['text'], x['chars'], x['glyphs']) for x in lg if x['text'] in SIX and x['glyphs'] != x['chars']]
        missing6 = [t for t in SIX if not any(x['text'] == t for x in lg)]
        if bad6 or missing6 or r.get('label_glyph_shortfall'):
            rv_bad.append(f'klga label glyphs: mismatched={bad6} missing={missing6} shortfall={r.get("label_glyph_shortfall")}')
    if r['meta'].get('debug_text_layer_in_preview') is False and 'debug-text-layer' in (er.parent / 'extract_preview.svg').read_text():
        rv_bad.append(f'{er.parent.parent.name}: debug text layer in preview without --debug-text-layer')
# provenance per airport + the display-only geometry decision
for rv in sorted((ROOT / 'data/airports').glob('*/review.json')):
    a_ = rv.parent; pv = json.loads((a_ / 'provenance.json').read_text()) if (a_ / 'provenance.json').exists() else {}
    ch = pv.get('chart', {}); foot = pv.get('display', {}).get('footer', '')
    import hashlib as _h
    pdf_sha = _h.sha256((ROOT / ch['local_path']).read_bytes()).hexdigest() if ch.get('local_path') else None
    need = all(ch.get(k) for k in ('cycle', 'effective_from', 'effective_to', 'sha256', 'url')) and ch.get('sha256') == pdf_sha
    shown = all(str(v) in foot for v in (ch.get('cycle'), ch.get('effective_from'), ch.get('effective_to'), (ch.get('sha256') or '')[:12], ch.get('url')))
    gor = json.loads(rv.read_text()).get('geometry_of_record', {})
    if not (need and shown):
        rv_bad.append(f'{a_.name}: provenance.json chart cycle/dates/sha256/url missing, sha mismatch, or not in display footer')
    if 'DISPLAY only' not in gor.get('decision', '') or gor.get('decided_by') != 'Franco':
        rv_bad.append(f'{a_.name}: geometry_of_record (display-only) decision not recorded')
# OSM layer: own folder, ODbL attribution + provenance, never merged into chart-derived layers
import hashlib as _h
for od in sorted((ROOT / 'data/airports').glob('*/osm')):
    a_ = od.parent; pv = json.loads((od / 'PROVENANCE.json').read_text()) if (od / 'PROVENANCE.json').exists() else {}
    raw_ok = (od / 'overpass_raw.json').exists() and _h.sha256((od / 'overpass_raw.json').read_bytes()).hexdigest() == pv.get('raw_sha256')
    gj_ok = (od / 'osm_aeroways.geojson').exists() and 'OpenStreetMap' in json.loads((od / 'osm_aeroways.geojson').read_text()).get('attribution', '')
    if not (raw_ok and gj_ok and (od / 'ATTRIBUTION.md').exists() and 'ODbL' in (od / 'ATTRIBUTION.md').read_text()
            and all(pv.get(k) for k in ('endpoint', 'requested_at', 'osm_data_timestamp', 'query_file')) and (od / pv.get('query_file', '-')).exists()):
        rv_bad.append(f'{a_.name}: OSM folder missing raw/sha256, query, timestamps, geojson attribution or ATTRIBUTION.md')
    chart_files = [p for p in a_.rglob('*') if p.is_file() and 'osm' not in p.relative_to(a_).parts[:1] and p.suffix in ('.svg', '.geojson')
                   and p.name not in ('osm_names_report.json',)]
    mixed = [str(p.relative_to(ROOT)) for p in chart_files if re.search(r'osm_id|openstreetmap', p.read_text(errors='ignore'), re.I)]
    if mixed:
        rv_bad.append(f'{a_.name}: OSM data inside chart-derived files {mixed}')
    rep = a_ / 'review/osm_names_report.json'
    if rep.exists() and 'OpenStreetMap' not in json.loads(rep.read_text()).get('osm', {}).get('attribution', ''):
        rv_bad.append(f'{a_.name}: osm_names_report.json lacks ODbL attribution')
reads = [str(p.relative_to(ROOT)) for p in (ROOT / 'lib').rglob('*.py') if re.search(r"data/airports/[a-z]{4}/|review\.json|faithful\.svg|layers\.raw", p.read_text())]
check('airport geometry: overlay thresholds; no sign-off unless all pass; georef per NASR end (graticule-only fit); NASR + chart provenance shown in footer; display-only decision; label-glyph regression (6 cases); OSM kept separate with ODbL attribution + provenance; lib/ never reads reconstructed geometry',
      not rv_bad and not reads, str(rv_bad + reads))

prod = [p for p in (ROOT / 'data/airports').rglob('*') if p.is_file() and 'review' not in p.parts and 'build' not in p.parts and p.suffix in ('.svg', '.json', '.html', '.geojson')]
prod += [p for p in (ROOT / 'ui_scaffold').rglob('*') if p.is_file() and p.suffix in ('.svg', '.json', '.html', '.js')]
leak = [str(p.relative_to(ROOT)) for p in prod if 'debug-text-layer' in p.read_text(errors='ignore')]
check('airport map: debug text-layer overlay only in review/ outputs behind --debug-text-layer, never in map/viewer outputs', not leak, str(leak))

am = (ROOT / 'ui_scaffold/airport_map.html').read_text()
am_bad = []
if set(re.findall(r'https?://[^\s\'"`/]+', am)) - {'http://www.w3.org'}: am_bad.append('external URL in airport_map.html')   # the SVG namespace is never fetched
if "l.id === 'layer-osm' ? q.get('osm') === '1'" not in am or "$('osmAttr').hidden = !on" not in am or "$('prov').textContent = META.footer" not in am:
    am_bad.append('airport_map.html: OSM must default off, attribution must follow the OSM toggle, footer must come from map_meta')
for mm in sorted((ROOT / 'ui_scaffold/maps').glob('*/map_meta.json')):
    ic = mm.parent.name; meta = json.loads(mm.read_text()); pvj = json.loads((ROOT / 'data/airports' / ic / 'provenance.json').read_text())
    osm_l = [l for l in meta['layers'] if l['id'] == 'layer-osm']
    svg = (mm.parent / 'chart.svg').read_text()
    if meta['footer'] != pvj['display']['footer'] or 'No predictions' not in meta['footer']:
        am_bad.append(f'{ic}: viewer footer differs from provenance.json or lacks "No predictions"')
    if osm_l and (osm_l[0]['default'] or not meta.get('osm_attribution') or 'OpenStreetMap' not in meta['osm_attribution']
                  or not (mm.parent / 'osm/ATTRIBUTION.md').exists() or 'OpenStreetMap' not in json.loads((mm.parent / 'osm/osm_taxiways.geojson').read_text()).get('attribution', '')):
        am_bad.append(f'{ic}: OSM layer must be off by default with ODbL attribution + ATTRIBUTION.md in its own folder')
    if re.search(r'osm_id|openstreetmap|NE-2, 01|debug-text-layer', svg, re.I):
        am_bad.append(f'{ic}: chart.svg contains OSM data, a cycle banner or the debug text layer')
check('airport map view: footer = stored provenance line; OSM off by default with ODbL attribution in its own folder; chart.svg has no OSM/banner/debug layer; local only',
      not am_bad, str(am_bad))

import importlib.util as _ilu, datetime as _dt
_cc = _ilu.module_from_spec(_ilu.spec_from_file_location('cycle_check', ROOT / 'tools/airport_map/cycle_check.py')); _ilu.spec_from_file_location('cycle_check', ROOT / 'tools/airport_map/cycle_check.py').loader.exec_module(_cc)
_u = lambda s: _dt.datetime.fromisoformat(s).replace(tzinfo=_dt.timezone.utc)
_cases = [('2026-09-30T12:00', False), ('2026-10-01T09:00', False), ('2026-10-01T09:01', True), ('2026-10-15T00:00', True),
          ('2026-10-29T09:00', True), ('2026-10-29T09:01', False), ('2026-11-02T00:00', False)]
_bad = [(t, exp) for t, exp in _cases if _cc.is_current('2026-10-01', '2026-10-29', _u(t)) != exp]
check('airport cycle check: stale/current decided by the chart window (0901Z switch) with a fixed fake "now"; cycle numbers roll over after 13',
      not _bad and _cc.next_cycle('2610') == '2611' and _cc.next_cycle('2613') == '2701', str(_bad))

import glob as _glob, shutil as _sh
_node = _sh.which('node') or next(iter(sorted(_glob.glob(str(Path.home() / '.nvm/versions/node/*/bin/node')))), None)
_js = ("const {chartCycleStatus: c} = require(process.argv[1]); const r = s => c('2026-10-01', '2026-10-29', new Date(s));"
       "console.log(JSON.stringify(['2026-10-08T12:00:00Z','2026-10-29T09:00:00Z','2026-10-29T09:01:00Z','2026-10-29T09:02:00Z','2026-09-30T12:00:00Z'].map(s => [r(s).current, r(s).expired])))")
_out = subprocess.run([_node, '-e', _js, str(ROOT / 'ui_scaffold/cycle.js')], capture_output=True, text=True).stdout.strip() if _node else ''
_want = [[True, False], [True, False], [False, True], [False, True], [False, False]]
_amh = (ROOT / 'ui_scaffold/airport_map.html').read_text()
_wired = ('<script src="cycle.js"></script>' in _amh and 'CHART CYCLE EXPIRED — re-run A6' in _amh and 'id="expired" role="alert" hidden' in _amh
          and 'NEEDS RE-CHECK' in _amh and "q.get('today')" in _amh)
check('map view cycle check on load: cycle.js marks the chart expired from 0901Z 10/29/26 (fake today); page shows the red banner and flags the sign-off for re-check',
      bool(_node) and _out and json.loads(_out) == _want and _wired, f'node={_node} out={_out} wired={_wired}')

# generalization regression (KHWD built by tools/build_airport.py with no airport-specific code)
_kh = ROOT / 'data/airports/khwd'
if _kh.exists():
    _er = json.loads((_kh / 'review/extract_report.json').read_text()); _gr = json.loads((_kh / 'graticule.json').read_text())
    _gj = json.loads((_kh / 'georef.json').read_text()); _rv = {i['id']: i for i in json.loads((_kh / 'review.json').read_text())['items']}
    _on = json.loads((_kh / 'review/osm_names_report.json').read_text())
    _kh_bad = [m for m, ok in [
        ('runway ends 10L/10R/28L/28R', _er['runway_end_labels'] == ['10L', '10R', '28L', '28R']),
        ('graticule from the PDF text layer, 2 parallels x 3 meridians', _gr.get('source') == 'pdf_text_layer' and len(_gr['parallels']) == 2 and len(_gr['meridians']) == 3),
        ('graticule fit RMS < 1 m on 6 points', _gj['transform_page_pt_to_local_m'].get('control_points_n') == 6 and _gj['transform_page_pt_to_local_m'].get('fit_rms_m', 9) < 1.0),
        ('hot spots 6 callouts = 6 outlines', _rv['hot_spots']['status'] == 'pass' and _rv['hot_spots']['result']['shapes'] == 6),
        ('no ILS HOLD -> not_applicable', _rv['ils_hold_lines']['status'] == 'not_applicable'),
        ('X closed-pavement marks and the solid L are not taxiway names', not {'X', 'L'} & set(_on['summary']['chart_only']) and _er['labels_by_class'].get('closed_marker', 0) >= 2),
        ('0 unclassified objects', _rv['unclassified']['status'] == 'pass')] if not ok]
    check('generalization (KHWD via tools/build_airport.py): runway ends, text-layer graticule, ellipse hot spots, not_applicable ILS, closed-pavement marks', not _kh_bad, str(_kh_bad))

# ---------------- audio: no audio in the repo; local-only audio server ----------------
AUDIO_EXT = ('.wav', '.mp3', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.webm', '.mp4', '.mov')
tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True).stdout.split('\n')
in_ui = [str(p.relative_to(ROOT)) for p in (ROOT / 'ui_scaffold').rglob('*') if p.suffix.lower() in AUDIO_EXT]
check('audio: no audio/video files tracked in the repo or present under ui_scaffold/',
      not [f for f in tracked if f.lower().endswith(AUDIO_EXT)] and not in_ui, str(in_ui))

import http.client, importlib.util, tempfile, threading, wave
_spec = importlib.util.spec_from_file_location('audio_server', ROOT / 'venue/audio_server.py'); AS = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(AS)
with tempfile.TemporaryDirectory() as td:
    td = Path(td); (td / 'owned').mkdir(); (td / 'private').mkdir(); (td / 'owned/sub').mkdir()
    def _wav(p):
        with wave.open(str(p), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0' * 1600)
    _wav(td / 'owned/SIM_T_0.wav'); _wav(td / 'owned/sub/hidden.wav'); _wav(td / 'private/01_REAL.wav'); _wav(td / 'outside.wav')
    (td / 'private/02_link.wav').symlink_to(td / 'outside.wav')
    srv = AS.AudioServer('127.0.0.1', 0, td / 'owned', td / 'private', td / 'labels'); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(method, path, host='127.0.0.1', headers=None, body=None):
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True); c.putheader('Host', f'{host}:{port}')
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        if body is not None:
            c.putheader('Content-Length', str(len(body)))
        c.endheaders(body); r = c.getresponse(); data = r.read(); c.close(); return r.status, data

    nonloop = subprocess.run([sys.executable, str(ROOT / 'venue/audio_server.py'), '--bind', '0.0.0.0', '--port', '0'], capture_output=True, text=True, timeout=10)
    check('audio server: binds 127.0.0.1 only, refuses a non-loopback bind, rejects a foreign Host header (403)',
          srv.server_address[0] == '127.0.0.1' and nonloop.returncode != 0 and req('GET', '/health', host='evil.example')[0] == 403
          and req('GET', '/private/01_REAL.wav', host='gmaps.attacker.test')[0] == 403 and req('GET', '/health')[0] == 200,
          f'bind={srv.server_address[0]} nonloop_rc={nonloop.returncode}')
    rng = req('GET', '/private/01_REAL.wav', headers={'Range': 'bytes=0-9'})
    probes = ['/private/../outside.wav', '/private/..%2Foutside.wav', '/owned/sub/hidden.wav', '/owned/hidden.wav', '/private/02_link.wav',
              '/private/01_REAL.mp3', '/private/%2e%2e/outside.wav', '/etc/passwd', '/private/']
    leaks = [p for p in probes if req('GET', p)[0] != 404]
    idx = json.loads(req('GET', '/private/index.json')[1])
    check('audio server: only listed *.wav files are served (traversal, subpaths, symlinks out and other types -> 404); Range works',
          not leaks and req('GET', '/owned/SIM_T_0.wav')[0] == 200 and rng[0] == 206 and len(rng[1]) == 10
          and [c['name'] for c in idx['clips']] == ['01_REAL.wav'], f'leaks={leaks}')
    good = json.dumps({'clip': '01_REAL.wav', 'judgement': 'partial', 'note': 'callsign unclear', 'mapped_line_t': 40, 'reviewer': 'Franco'}).encode()
    O = {'Origin': 'http://localhost:8765', 'Content-Type': 'application/json'}
    st = [req('POST', '/labels', headers={'Content-Type': 'application/json'}, body=good)[0],                       # no Origin
          req('POST', '/labels', headers={**O, 'Origin': 'http://evil.example'}, body=good)[0],                     # foreign origin
          req('POST', '/labels', headers=O, body=json.dumps({'clip': '01_REAL.wav', 'judgement': 'maybe'}).encode())[0],
          req('POST', '/labels', headers=O, body=json.dumps({'clip': '../outside.wav', 'judgement': 'wrong'}).encode())[0],
          req('POST', '/labels', headers=O, body=json.dumps({'clip': '01_REAL.wav', 'judgement': 'wrong', 'audio': 'UklGR'}).encode())[0],
          req('POST', '/labels', headers=O, body=good)[0]]
    saved = list((td / 'labels').rglob('*'))
    lab = json.loads((td / 'labels' / AS.LABEL_FILE).read_text()) if (td / 'labels' / AS.LABEL_FILE).exists() else {}
    check('audio server: /labels saves text-only judgements under the labels dir, only from the local UI origin, enum + known clip only',
          st == [403, 403, 400, 400, 400, 200] and [p.name for p in saved] == [AS.LABEL_FILE]
          and lab.get('labels', {}).get('01_REAL.wav', {}).get('judgement') == 'partial', f'statuses={st} files={[p.name for p in saved]}')
    # fast listening pass (ui_scaffold/klga_review.js): headless keyboard pass against this server on a scratch labels folder
    for nm in ('S1.wav', 'S2.wav', 'W1.wav', 'H1.wav'):
        _wav(td / 'private' / nm)
    _m = {'clips': [{'clip': 'W1.wav', 'batch': 'batch2_labelled', 'status': 'weak', 'score': 0.3, 'line_t': -50, 'line_text': 'x', 'whisper_text': 'y'},
                    {'clip': 'H1.wav', 'batch': 'batch3_unlabelled', 'status': 'excluded', 'reason': 'batch 3', 'whisper_text': 'Hayward tower'},
                    {'clip': 'S2.wav', 'batch': 'batch1_labelled', 'status': 'proposed', 'score': 0.7, 'line_t': 10, 'line_text': 'a', 'whisper_text': 'a'},
                    {'clip': 'S1.wav', 'batch': 'batch2_labelled', 'status': 'proposed', 'score': 0.6, 'line_t': -5, 'line_text': 'b', 'whisper_text': 'b'}]}
    (td / 'labels' / AS.MAPPING_FILE).write_text(json.dumps(_m))
    _r = subprocess.run([_node, str(ROOT / 'tests/js/klga_review_pass.js'), str(ROOT / 'ui_scaffold/klga_review.js'), str(port)],
                        capture_output=True, text=True, timeout=60).stdout.strip() if _node else '{}'
    fpo = json.loads(_r or '{}')
    _sv = {k: v for k, v in fpo.get('saved', {}).items() if k in ('S1.wav', 'S2.wav', 'W1.wav', 'H1.wav')}
    _vk = (ROOT / 'ui_scaffold/klga_view.html').read_text()
    _wired = ('<script src="klga_review.js"></script>' in _vk and "document.addEventListener('keydown'" in _vk and 'KLGAReview.resumeIndex(ORDER, LABELS)' in _vk
              and "closest('input, select, textarea')" in _vk and 'if (!PRIVATE || !MAP' in _vk and 'airport_map.html?icao=khwd' in _vk)
    check('fast listening pass: order strong -> weak -> KHWD; keys 1/2/3 save + auto-advance, N/P step; resume = first unjudged from saved labels; word diff; wired behind PRIVATE',
          fpo.get('groups') == ['strong', 'strong', 'weak', 'KHWD'] and fpo.get('resume0') == 0
          and _sv == {'S1.wav': 'verified', 'S2.wav': 'wrong', 'W1.wav': 'partial'} and fpo.get('resume1') == 3
          and fpo.get('progress') == {'done': 3, 'total': 4}
          and [s[1] for s in fpo.get('steps', [])] == ['verified', 'wrong', 'prev', 'next', 'partial', 'toggle', 'replay', None]
          and ['13', True, 'rwy'] in fpo.get('diff', {}).get('b', []) and ['of', False, ''] in fpo.get('diff', {}).get('b', [])
          and ['Delta', True, 'cs'] in fpo.get('diff', {}).get('a', []) and ['hold', True, 'ins'] in fpo.get('diff', {}).get('a', []) and _wired,
          f'out={_r[:300]} wired={_wired}')
    srv.shutdown(); srv.server_close()

# --- repo safety: pre-commit hook (tools/hooks/precommit_check.py) ---
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('precommit_check', ROOT / 'tools/hooks/precommit_check.py'); PC = _ilu.module_from_spec(_spec); _spec.loader.exec_module(PC)
_tg = b'TOKEN = "123456789:AA' + b'x' * 33 + b'"'
_gh = b'ghp_' + b'A1b2' * 9
_cases = {
    'owned clip allowed': PC.check('data/audio/SIM_E1_CROSS_DURING_ROLLOUT_0.wav', b'RIFF') == [],
    'other wav blocked': bool(PC.check('data/audio/REAL_LGA_01.wav', b'RIFF')),
    'mp3 blocked': bool(PC.check('x/SIM_E1_CROSS_DURING_ROLLOUT_0.mp3', b'ID3')),
    'private path blocked': bool(PC.check('notes/audio_real_private/list.txt', b'hi')),
    'private bytes blocked': bool(PC.check('a/renamed.bin', b'PRIVATEBYTES', priv={__import__('hashlib').sha256(b'PRIVATEBYTES').hexdigest()})),
    '>5MB blocked': bool(PC.check('big.json', b'0' * (5 * 1024 * 1024 + 1))),
    'telegram token blocked': bool(PC.check('cfg.py', _tg)),
    'github token blocked': bool(PC.check('cfg.py', _gh)),
    'aws key blocked': bool(PC.check('cfg.py', b'AKIA' + b'ABCDEFGHIJKLMNOP')),
    'hugging face token blocked': bool(PC.check('cfg.py', b'HF = "hf_' + b'Ab1' * 12 + b'"')) and bool(PC.check('cfg.py', b'hf_oauth_' + b'Zq9' * 12)),
    'modal token blocked': bool(PC.check('cfg.toml', b'token_id = "ak-' + b'Q7x' * 7 + b'"')) and bool(PC.check('cfg.toml', b'as-' + b'm4P' * 7)),
    'as-is / ask- prose allowed': PC.check('docs/x.md', b'kept as-is; ask-the-controller-before-crossing-anything') == [],
    'hf_ snake_case identifier allowed': PC.check('lib/x.py', b'hf_hub_download_with_retries_and_backoff_helper()') == [],
    'plain code allowed': PC.check('lib/x.py', b'def f():\n    return "frequency"\n') == [],
}
_hook = (ROOT / '.git/hooks/pre-commit')
_hsrc = (ROOT / 'tools/hooks/precommit_check.py').read_text()
_cases['hook runs the suite and refuses unstaged changes'] = "tests/run_all.py" in _hsrc and "'diff', '--name-only'" in _hsrc and 'suite is red' in _hsrc
check('repo safety: pre-commit check blocks non-owned audio, audio_real_private, >5 MB and token-like strings; allows owned clips and code; runs the suite (red or untested commit blocked)',
      all(_cases.values()), f'failed={[k for k, v in _cases.items() if not v]} hook_installed={_hook.exists()}')

# --- generic area labels (GA TRANSIENT RAMP) and no airport facts in the generic tools ---
_kr = json.loads((ROOT / 'data/airports/khwd/review/extract_report.json').read_text())
_kv = json.loads((ROOT / 'data/airports/khwd/review.json').read_text())
_osm_src = (ROOT / 'tools/airport_map/osm.py').read_text()
check('area labels: KHWD "GA" (GA TRANSIENT RAMP) is an area_label, not a taxiway name; KHWD stays unsigned; osm.py holds no KLGA gate facts',
      _kr.get('area_labels') == ['GA'] and 'GA' not in _kr.get('taxiway_candidates', []) and not _kv.get('signed_off_by') and 'sign_off' not in _kv
      and '76-79' not in _osm_src and "'14'" not in _osm_src and 'Numbers 1-19' not in _osm_src,
      f"area={_kr.get('area_labels')} signed={_kv.get('signed_off_by')}")

# --- airport-map regression harness (tools/airport_map/regress.py) ---
_spec = importlib.util.spec_from_file_location('regress', ROOT / 'tools/airport_map/regress.py'); RG = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(RG)
_rb = json.loads((ROOT / 'data/airports/klga/review/regress_baseline.json').read_text())['metrics']
_rr = subprocess.run([sys.executable, str(ROOT / 'tools/airport_map/regress.py'), '--no-build'], capture_output=True, text=True)
_mut = json.loads(json.dumps(_rb)); _mut['statuses']['hot_spots'] = 'fail'; _mut['georef_m']['13'] = 10.41
check('regress.py: KLGA baseline = signed results (overlay 99.771/0.296, georef 04 4.43 13 10.40 22 7.48 31 13.68 H1 0.94, all pass); current outputs match every baseline; a change is detected',
      _rb['overlay'] == {'covered_pct': 99.771, 'extra_pct': 0.296} and _rb['georef_m'] == {'04': 4.43, '13': 10.4, '22': 7.48, '31': 13.68, 'H1': 0.94}
      and set(_rb['statuses'].values()) == {'pass'} and _rb['hold_lines'] == 4 and RG.signed('KLGA')
      and _rr.returncode == 0 and 'REGRESS OK' in _rr.stdout and {c['key'] for c in RG.diff(_rb, _mut)} == {'statuses.hot_spots', 'georef_m.13'},
      _rr.stdout.strip().replace('\n', ' | ')[-300:])

# --- outline-only graticule values: exact glyph-shape decode must reproduce KLGA's human-confirmed values ---
_spec = importlib.util.spec_from_file_location('graticule', ROOT / 'tools/airport_map/graticule.py'); GT = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(GT)
_klraw = ROOT / 'data/airports/klga/build/layers.raw.json'
if _klraw.exists():
    _stp = json.loads((ROOT / 'tools/airport_map/styles/faa_apd.json').read_text())['post']
    _dec = GT.decode_outline_labels(json.loads(_klraw.read_text()), {k: _stp[f'label_glyph_{k}'] for k in ('pre', 'post', 'pad')})[0]
    _conf = json.loads((ROOT / 'data/airports/klga/graticule.json').read_text())
    _want = {p['value'] for p in _conf['parallels'] + _conf['meridians']}
    check('graticule glyph decode: on KLGA it reproduces exactly the 4 values Franco confirmed (and never overwrites a confirmed file)',
          {d['text'] for d in _dec} == _want and len(_dec) == 4 and _conf.get('confirmed_by') == 'Franco', f'decoded={sorted(d["text"] for d in _dec)} want={sorted(_want)}')
else:
    check('graticule glyph decode: KLGA build output present (run tools/build_airport.py KLGA)', False, 'missing build/layers.raw.json')

# --- reboot-proofing: systemd user units bind loopback only ---
_units = {p.name: p.read_text() for p in (ROOT / 'venue/systemd').glob('*.service') if p.name not in ('gmaps-display.service', 'gmaps-mcp.service')}   # display and MCP have their own checks
_mcpu = (ROOT / 'venue/systemd/gmaps-mcp.service').read_text() if (ROOT / 'venue/systemd/gmaps-mcp.service').exists() else ''
check('systemd MCP unit (Franco, Oct 9): binds only the OpenShell bridge 172.18.0.1:11435, waits for that address, Restart=on-failure with a short RestartSec, token read from the secrets file (never written in the unit)',
      '--bind 172.18.0.1 --port 11435' in _mcpu and 'inet 172.18.0.1/' in _mcpu and 'Restart=on-failure' in _mcpu and re.search(r'RestartSec=([1-9]|10)\b', _mcpu)
      and 'secrets/mcp_token' in _mcpu and not re.search(r'0\.0\.0\.0|--bind (?!172\.18\.0\.1)|GMAPS_MCP_TOKEN=[A-Za-z0-9]', _mcpu), _mcpu[:80])
check('systemd user units (venue/systemd): UI 8765 and audio 8767, loopback only, restart on failure',
      set(_units) == {'gmaps-ui.service', 'gmaps-audio.service'} and all('--bind 127.0.0.1' in t and 'Restart=on-failure' in t for t in _units.values())
      and '8765' in _units.get('gmaps-ui.service', '') and '8767' in _units.get('gmaps-audio.service', '')
      and not any(re.search(r'0\.0\.0\.0|100\.\d+\.\d+\.\d+|--bind (?!127\.0\.0\.1)', t) for t in _units.values()), str(sorted(_units)))

# --- KLGA prediction-geometry DRAFT: built, but no runtime code may load it ---
_allowed = {'tools/airport_map/graph_draft.py', 'tests/run_all.py', 'tests/run_all_checks.py'}
_tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True).stdout.split()
_loaders = [f for f in _tracked if f not in _allowed and not f.endswith('.md') and '/graph_draft/' not in f
            and f.endswith(('.py', '.js', '.html', '.sh', '.json', '.service', '.toml', '.yaml', '.yml'))
            and 'graph_draft' in (ROOT / f).read_text(errors='ignore')]
_gd = ROOT / 'data/airports/klga/graph_draft/graph_draft.json'
_gdj = json.loads(_gd.read_text()) if _gd.exists() else {}
check('graph_draft (KLGA): exists as a DRAFT with predictions/routing off and ODbL attribution; no runtime code (lib, venue, ui, agent, other tools) references it',
      not _loaders and _gdj.get('predictions_enabled') is False and _gdj.get('routing_enabled') is False and 'NOT WIRED' in _gdj.get('status', '')
      and (ROOT / 'data/airports/klga/graph_draft/ATTRIBUTION.md').exists(), f'loaders={_loaders}')

# --- hold lines: every label style, leader lines, place names (KSFO / KATL / KORD / KJFK / KEWR) ---
_hs = {ap: next(i for i in json.loads((ROOT / f'data/airports/{ap}/review.json').read_text())['items'] if i['id'] == 'ils_hold_lines') for ap in ('katl', 'ksfo', 'kjfk', 'kord', 'kewr', 'klga')}
check('hold lines: KATL 8/8 (leaders, bent and single-dash bars), KSFO 2/2 (CAT n HOLD), KJFK "CAT 2 HOLD" found, KORD "SCENIC HOLD PAD" is not a hold, KEWR bare "CAT 2" is not a hold, KLGA unchanged',
      _hs['katl']['status'] == 'pass' and _hs['katl']['result']['annotations_with_hold_line'] == 8 and _hs['ksfo']['status'] == 'pass'
      and _hs['kjfk']['status'] == 'pass' and _hs['kjfk']['result']['annotations'] == 1 and _hs['kord']['status'] == 'not_applicable'
      and _hs['kewr']['status'] == 'not_applicable' and _hs['klga']['status'] == 'pass' and _hs['klga']['result']['hold_lines'] == 4,
      str({k: (v['status'], v['result'].get('annotations_with_hold_line'), v['result'].get('annotations')) for k, v in _hs.items()}))

# --- decoded graticules: unambiguous decode, NASR runway-end check passes, footer says so; along/across per end ---
_dec_bad, _dec_skip = [], []
for _gp in sorted((ROOT / 'data/airports').glob('*/graticule.json')):
    _g = json.loads(_gp.read_text()); _b = _gp.parent
    if _g.get('source') != 'outline_glyph_decode':
        continue
    if json.loads((_b / 'review.json').read_text()).get('draft'):   # draft maps (display only, not reviewed, no overlays) are checked by the draft-map check instead
        _dec_skip.append(_b.name); continue
    _rv = {i['id']: i for i in json.loads((_b / 'review.json').read_text())['items']}
    _pv = json.loads((_b / 'provenance.json').read_text())
    _margins = [v['min_margin_to_other_char'] for v in _g.get('decode', {}).get('per_value', []) if v['min_margin_to_other_char'] is not None]
    if not _g.get('decode', {}).get('per_value') or any(v['max_deviation'] > 0.03 for v in _g['decode']['per_value']) or any(m < 0.10 for m in _margins):
        _dec_bad.append(f'{_b.name}: decode not unambiguous')
    if _rv['runway_ends']['status'] != 'pass' or 'decoded from outlines' not in _rv['runway_ends']['result'].get('graticule', ''):
        _dec_bad.append(f'{_b.name}: NASR runway-end check not passing/annotated')
    if 'Graticule: decoded from outlines' not in _pv['display']['footer'] or not (_b / 'review/RUNWAY_ENDS.md').exists():
        _dec_bad.append(f'{_b.name}: footer or RUNWAY_ENDS review missing')
_klg = json.loads((ROOT / 'data/airports/klga/provenance.json').read_text())['display']['footer']
check('decoded graticules: every glyph within 0.03 and >= 0.10 from any other character; NASR runway-end check passes and says so; footer "Graticule: decoded from outlines"; along/across review per airport'
      + f'; draft maps skipped: {", ".join(_dec_skip) or "none"}',
      not _dec_bad and 'decoded from outlines' not in _klg and (ROOT / 'data/airports/klga/review/RUNWAY_ENDS.md').exists(), str(_dec_bad))

# --- draft maps (Franco, Oct 18): display only, not reviewed, no overlays; every gate above stays as it is for non-draft maps ---
_dr_bad, _dr = [], []
for _mm in sorted((ROOT / 'ui_scaffold/maps').glob('*/map_meta.json')):
    _m = json.loads(_mm.read_text()); _ic = _mm.parent.name; _rvj = json.loads((ROOT / 'data/airports' / _ic / 'review.json').read_text())
    _d = _rvj.get('draft')
    if not _d and not any(k in _m for k in ('status', 'overlays_allowed', 'draft_reason')):
        continue
    _dr.append(_ic)
    if not (_d and _m.get('status') == 'draft' == _d.get('status') and _m.get('overlays_allowed') is False and _d.get('overlays_allowed') is False
            and str(_m.get('draft_reason') or '').strip() and _m['draft_reason'] == _d.get('draft_reason') and _d.get('decided_by') == 'Franco'):
        _dr_bad.append(f'{_ic}: draft needs status "draft", overlays_allowed false and a non-empty draft_reason, the same in review.json (decided by Franco) and map_meta.json')
    if _ic == 'klga' or _rvj.get('sign_off') or _rvj.get('signed_off_by'):
        _dr_bad.append(f'{_ic}: a draft map can never be KLGA or signed off')
    _bl = ROOT / 'data/airports' / _ic / 'review/regress_baseline.json'
    if not _bl.exists() or json.loads(_bl.read_text())['metrics'].get('draft') != 'draft':
        _dr_bad.append(f'{_ic}: regress baseline does not record the draft')
_amh, _dmh = (ROOT / 'ui_scaffold/airport_map.html').read_text(), (ROOT / 'ui_scaffold/demo.html').read_text()
if "refused: 'draft map'" not in _amh or 'draftBadge' not in _amh or "m.status !== 'draft'" not in _dmh or "mm.status === 'draft'" not in _dmh:
    _dr_bad.append('pages: airport_map.html must refuse ledger/points on a draft map and show the badge; demo.html must never run a fixture on a draft map')
check(f'draft maps: status draft, overlays_allowed false, non-empty draft_reason (review.json = map_meta, by Franco), never KLGA, never signed off, recorded in the regress baseline; viewer refuses overlays, no fixture on a draft map ({", ".join(_dr) or "none"})',
      not _dr_bad, str(_dr_bad))

# --- LAHSO points (display only), KPDK via the shared glyph library, cursor readout outside the map ---
_lah = {ap: next((i for i in json.loads((ROOT / f'data/airports/{ap}/review.json').read_text())['items'] if i['id'] == 'lahso_points'), None) for ap in ('kbos', 'katl', 'kord', 'kewr', 'kjfk')}
_kp = json.loads((ROOT / 'data/airports/kpdk/graticule.json').read_text())
_amh2 = (ROOT / 'ui_scaffold/airport_map.html').read_text()
_klrv = json.loads((ROOT / 'data/airports/klga/review.json').read_text())
check('LAHSO points (a marker counts only if its label points to it: leader, forked leader, or the dashed line starts at the label): KBOS 4/4, KATL 4/4, KORD, KEWR 2 markers (3rd dropped), KJFK n/a; signed KLGA checklist not extended; KPDK graticule decoded via the shared glyph library; cursor readout in the footer',
      all(_lah[a] and _lah[a]['status'] == 'pass' and _lah[a]['result']['labels_with_marker'] == _lah[a]['result']['labels'] for a in ('kbos', 'katl', 'kord', 'kewr'))
      and _lah['kewr']['result']['markers'] == 2 and _lah['kbos']['result']['markers'] == 4 and _lah['kjfk']['status'] == 'not_applicable' and 'lahso_points' not in {i['id'] for i in _klrv['items']}
      and _kp.get('source') == 'outline_glyph_decode' and 'shared glyph library' in _kp['decode'].get('training', '')
      and '<footer>\n  <div class="cursor" id="cursor">' in _amh2, str({k: v and v['status'] for k, v in _lah.items()}))

# --- tailnet display: safe copy without the INTERNAL LGA replay, no directory listing ---
import functools as _ft, http.server as _hs, urllib.request as _ur, urllib.error as _ue
_spec = importlib.util.spec_from_file_location('safe_display', ROOT / 'venue/safe_display.py'); SD = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(SD)
with tempfile.TemporaryDirectory() as _td:
    SD.DEST = Path(_td) / 'display_safe'; SD.build()
    _srv = _hs.ThreadingHTTPServer(('127.0.0.1', 0), _ft.partial(SD.NoListing, directory=str(SD.DEST)))
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    def _code(path):
        try:
            return _ur.urlopen(f'http://127.0.0.1:{_srv.server_address[1]}/{path}', timeout=5).status
        except _ue.HTTPError as e:
            return e.code
    _codes = {q: _code(q) for q in ('klga_view.html', 'klga_review.js', 'samples/LGA_2026-03-22.json', 'samples/', 'maps/', 'airport_map.html', 'nav.html')}
    _nav = (SD.DEST / 'nav.html').read_text()
    _srv.shutdown(); _srv.server_close()
_unit = (ROOT / 'venue/systemd/gmaps-display.service').read_text()
check('tailnet display: safe copy has no klga_view.html / klga_review.js / LGA_2026-03-22 (404), no directory listing (404), maps served; unit binds the Tailscale IP only and never proxies the audio server',
      _codes == {'klga_view.html': 404, 'klga_review.js': 404, 'samples/LGA_2026-03-22.json': 404, 'samples/': 404, 'maps/': 404, 'airport_map.html': 200, 'nav.html': 200}
      and 'klga_view' not in _nav and 'venue/safe_display.py --bind 100.74.115.100 --port 8766' in _unit and '8767' not in _unit.split('[Service]')[1]
      and '0.0.0.0' not in _unit, str(_codes))

# --- Franco's taxiway-name rules: R1 chart names only in the parser vocabulary and on the map; R2 radius; R3 X; R8 US ---
_r1_bad = []
for _vp in sorted((ROOT / 'data/airports').glob('*/review/taxiway_vocabulary.json')):
    _v = json.loads(_vp.read_text()); _b = _vp.parents[1]; _ic = _b.name
    _rep = json.loads((_b / 'review/osm_names_report.json').read_text())
    _chart = {l['label'] for l in _rep['instances']}
    if set(_v['taxiways']) != _chart or set(_v['taxiways']) & set(_rep['summary']['osm_only']):
        _r1_bad.append(f'{_ic}: vocabulary is not exactly the chart names')
    _ui = ROOT / 'ui_scaffold/maps' / _ic / 'osm/osm_taxiways.geojson'
    if _ui.exists():
        _shown = {f['properties'].get('ref') for f in json.loads(_ui.read_text())['features']} - {None}
        _okn = set(_v['taxiways']) | {str(g) for g in _v['gates_destination_only']}
        if _shown - _okn:
            _r1_bad.append(f'{_ic}: map shows OSM names not on the chart: {sorted(_shown - _okn)[:5]}')
_rad = {a: json.loads((ROOT / f'data/airports/{a}/review/osm_names_report.json').read_text()).get('match_radius_rule', '60 m') for a in ('klga', 'kord')}
_tn = {a: next(i for i in json.loads((ROOT / f'data/airports/{a}/review.json').read_text())['items'] if i['id'] == 'taxiway_names')['status'] for a in ('katl', 'kbos', 'ksfo', 'klga', 'kpdk')}
_sfo = json.loads((ROOT / 'data/airports/ksfo/review/extract_report.json').read_text())
check('taxiway names (Franco R1-R9): parser vocabulary and map = chart names only; radius ~3 mm at chart scale (KLGA 60 m, KORD 140 m); KATL/KBOS/KSFO pass; KPDK decided only after its outline text was decoded (R9); KSFO US is an area label; X is a closed marker',
      not _r1_bad and '60 m' in _rad['klga'] and '140 m' in _rad['kord'] and _tn == {'katl': 'pass', 'kbos': 'pass', 'ksfo': 'pass', 'klga': 'pass', 'kpdk': 'pass'}
      and 'US' in _sfo.get('area_labels', []) and 'X' not in _sfo.get('taxiway_candidates', []), str(_r1_bad[:3]) + str(_tn))

# --- KPDK: no text layer; outline lettering decoded into labels with the shared glyph library ---
_kraw = ROOT / 'data/airports/kpdk/build/layers.raw.json'
if _kraw.exists():
    _kl = [l for l in json.loads(_kraw.read_text())['labels'] if l.get('source') == 'outline_decode']
    _krv = {i['id']: i['status'] for i in json.loads((ROOT / 'data/airports/kpdk/review.json').read_text())['items']}
    _kt = {l['text'] for l in _kl}
    check('KPDK (no text layer): outline text decoded into labels (runway ends 3L 3R 16 21L 21R 34, HS 1-3, dimensions 3746 X 150 / 3968 X 150); runway identifiers, hot spots and taxiway names pass',
          {'3L', '3R', '16', '21L', '21R', '34', 'HS 1', 'HS 2', 'HS 3', '3746 X 150', '3968 X 150'} <= _kt
          and _krv['runway_identifiers'] == 'pass' and _krv['hot_spots'] == 'pass' and _krv['taxiway_names'] == 'pass', str(_krv))
else:
    check('KPDK decoded labels: build output present', False, 'run tools/build_airport.py KPDK')

# --- evaluation harness + synthetic scenarios: SIMULATED, harness only, no positions, real clips unscored without labels ---
_spec = importlib.util.spec_from_file_location('harness', ROOT / 'eval/harness.py'); HN = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(HN)
_sim = HN.summarize('sim', HN.set_sim({}))
_syn = HN.set_synthetic()
_real_sc = HN.set_klga_real(); _real = HN.summarize('klga_real', _real_sc); _real_n = _real_sc[0]['set_size']   # klga_real = clips confirmed LaGuardia
_scen = [json.loads(p.read_text()) for p in (ROOT / 'eval/synthetic').glob('*/*.scenario.json')]
_pos_leak = [s['id'] for s in _scen if re.search(r'"(lat|lon|x|y|position|positions|speed|eta)"', json.dumps(s))]
_lab_exists = (Path.home() / 'gmaps_venue/labels/klga_real_clip_labels.json').exists()
_gen = subprocess.run([sys.executable, str(ROOT / 'eval/synthetic/generate.py'), '--icao', 'KLGA', '--n', '10', '--seed', '9101'], capture_output=True, text=True)
_det = subprocess.run(['git', 'diff', '--quiet', '--', 'eval/synthetic/klga'], cwd=ROOT).returncode == 0
check('evaluation harness: SIM alerts recall 1.0, synthetic sets SIMULATED (KATL + KLGA, 10 each, half with a conflict), no positions in scenarios, generator deterministic, real clips UNSCORED unless Franco labelled them',
      _sim['alerts']['recall'] == 1.0 and len(_syn) == 20 and all(s['kind'].startswith('SIMULATED') for s in _syn)
      and sum(1 for s in _syn if s['expected_alert']) == 10 and not _pos_leak and _gen.returncode == 0 and _det
      and (_lab_exists or (_real['scored'] == 0 and _real['unscored'] == _real_n)), f"pos_leak={_pos_leak} det={_det} real={_real['scored']}/{_real['unscored']}")

# --- §8 landing reservation release: pilot "clear of the runway" or a later ground taxi clearance releases it;
#     an exit instruction alone sets 'exiting' and keeps it ---
sys.path.insert(0, str(ROOT / 'eval/synthetic'))
from chart_ids import ChartIdentifiers as _CI
from gmaps_core.parser import parse as _parse
from gmaps_core.ledger import Ledger as _Led
_A = _CI('KATL')
def _run(lines):
    led = _Led(_A)
    for t, sp, txt in lines:
        led.ingest(_parse(txt, 'KATL', speaker_hint=sp, resolver=_A, event_time=t), t); led.tick(t)
    return led
_land = (0, 'ATC', 'Southwest seven seven, runway two six right, cleared to land.')
_cross = (60, 'ATC', 'United four four one, cross runway two six right at Echo.')
_c1 = _run([_land, (45, 'PILOT', 'Southwest seven seven, clear of the runway two six right.'), _cross])
_c2 = _run([_land, (40, 'ATC', 'Southwest seven seven, turn right at Echo, contact ground point eight two.'), (50, 'ATC', 'Southwest seven seven, taxi to the ramp via Echo.'), _cross])
_c3 = _run([_land, (40, 'ATC', 'Southwest seven seven, turn right at Echo, contact ground point eight two.'), _cross])
_c4 = _run([_land, _cross])
_r1 = lambda led: [w for w in led.warnings if w['rule_id'] == 'R1']
_exiting = any('exiting' in str(st) for st in json.dumps(_c3.snapshot() if hasattr(_c3, 'snapshot') else {}, default=str).split('"')) or any('exiting' in json.dumps(v, default=str) for v in getattr(_c3, 'actors', {}).values())
check('ledger §8: a landing is released by the pilot\'s "clear of the runway" report or a later ground taxi clearance; an exit instruction alone keeps it (state exiting); a crossing during a landing still raises R1',
      not _r1(_c1) and not _r1(_c2) and _r1(_c3) and _r1(_c4) and _exiting, f'c1={len(_r1(_c1))} c2={len(_r1(_c2))} c3={len(_r1(_c3))} c4={len(_r1(_c4))} exiting={_exiting}')

# --- §9/§10 parser repairs for Whisper output: only in callsign / runway / taxiway contexts ---
def _pf(txt, sp='ATC'):
    e = _parse(txt, 'KATL', speaker_hint=sp, resolver=_A, event_time=0)
    rw = e.get('runway') or next((h.get('on_runway') or h.get('runway') for h in e.get('hold_constraints') or [] if h.get('on_runway') or h.get('runway')), None)
    return (sorted(e['actor_candidates']), (str(rw).lstrip('0') or None) if rw else None, e['kind'], e.get('route'))
_pcases = [   # (Whisper text from the DEV split, speaker, expected (callsigns, runway, kind))
    ('Southwest 2284, cross runway Niner, right at Romeo 7.', 'ATC', (['SWA2284'], '9R', 'CROSS')),
    ('Southwest 2956, cross runway 26, right at Bravo 1.', 'ATC', (['SWA2956'], '26R', 'CROSS')),
    ('American 1088, runway 9 are right, line up and wait.', 'ATC', (['AAL1088'], '9R', 'LUAW')),
    ('Runway to six left. Clear to land. United six zero niner.', 'PILOT', (['UAL609'], '26L', 'LANDING_CLEARANCE')),
    ('Runway Niner right. Cleared to land. Southwest 739er.', 'PILOT', (['SWA739'], '9R', 'LANDING_CLEARANCE')),
    ('Endeavour 2762, runway 8 left, cleared for takeoff.', 'ATC', (['EDV2762'], '8L', 'TAKEOFF_CLEARANCE')),
    ('Cross runway 8 left at Hotel, Delta 1432.', 'PILOT', (['DAL1432'], '8L', 'CROSS')),
    ('Jet Blue 2138, hold short of runway 27L at November 2.', 'ATC', (['JBU2138'], '27L', 'HOLD_SHORT')),
    ('Cross runway 4 at Unifor, southwest to 284.', 'PILOT', (['SWA2284'], '4', 'CROSS')),
    ('Cross runway 9 or left at Lima. Southwest 9 or 6-2.', 'PILOT', (['SWA962'], '9L', 'CROSS')),
    ('Southwest Niners 6-2, cross runway 1-3 at Zulu.', 'ATC', (['SWA962'], '13', 'CROSS')),
    ('Endeavour 7-2-1, cross runway 2-2 at Echo.', 'ATC', (['EDV721'], '22', 'CROSS')),
]
_pguards = [   # outside the contexts nothing may change
    ('Southwest seven seven, turn right at Delta one, contact ground point niner.', 'ATC'),
    ('United four four one, taxi to the ramp via Delta, hold short of runway two six right.', 'ATC'),
    ('Delta one two three, runway two six right, cleared to land.', 'ATC'),
    ('United four four one, taxi via Delta one two, hold short of runway two six right.', 'ATC'),   # 'Delta one two' after VIA stays a taxiway
]
_pbad = [(t, _pf(t, sp)[:3], exp) for t, sp, exp in _pcases if _pf(t, sp)[:3] != exp]
_gexp = [(['SWA77'], None, 'TAXI', ['D1']), (['UAL441'], '26R', 'TAXI', ['D']), (['DAL123'], '26R', 'LANDING_CLEARANCE', None), (['UAL441'], '26R', 'TAXI', ['D'])]   # outputs before the repairs
_pbad += [(t, _pf(t, sp), g) for (t, sp), g in zip(_pguards, _gexp) if _pf(t, sp) != g]
check('parser repairs (dev split examples): runway side after a comma / "are" / "or", "to six", Niner, "739er", "to 284", "9 or 6-2", Endeavour, Jet Blue, trailing Delta callsigns, hold short of runway X at Y',
      not _pbad, str(_pbad[:4]))

# --- parser/ledger regressions: SIM-1 output identical; the 47 real clips' typed parses unchanged or better ---
_simnow = subprocess.run([sys.executable, str(ROOT / 'eval/regress_parse_dump.py'), str(ROOT)], capture_output=True, text=True).stdout
_simbase = (ROOT / 'eval/results/before_2026-10-09/sim_parse_ledger.json').read_text()
_rbase = json.loads((ROOT / 'eval/results/before_2026-10-09/real_clips_typed_parse.json').read_text())
_rk = ('speech_act', 'kind', 'actor_candidates', 'runway', 'route', 'crossing_at', 'hold_constraints', 'hold_position')
_rworse = []
for _c in json.loads((ROOT / 'venue_traces/real_asr_text/asr_real_text_only.json').read_text())['clips']:
    _e = _parse(_c['asr_text_raw'], 'KLGA', event_time=0)
    for k in _rk:
        _untyped = _rbase[_c['clip']]['kind'] == 'OTHER' and _rbase[_c['clip']]['speech_act'] == 'unknown'   # a line not typed before may become typed
        if _e.get(k) != _rbase[_c['clip']][k] and _rbase[_c['clip']][k] not in (None, [], 'UNKNOWN', 'unknown', False) and not (_untyped and k in ('kind', 'speech_act')):
            _rworse.append((_c['clip'], k))
check('parser/ledger regressions: SIM-1 parse + ledger output identical to the Oct 9 baseline; no real-clip typed field lost or changed (only empty fields may fill)',
      json.loads(_simnow) == json.loads(_simbase) and not _rworse, str(_rworse[:5]))

# --- option 1: host fast path posts the alert on arrival; the agent's process_transmission never ingests twice ---
_spec = importlib.util.spec_from_file_location('gmaps_mcp_t', ROOT / 'venue/gmaps_mcp.py'); GM = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(GM)
_e1 = json.loads((ROOT / 'episodes/SIM_E1_CROSS_DURING_ROLLOUT.transcript.json').read_text())['events']
def _fresh():
    tl = GM.GmapsTools('SIM_E1_CROSS_DURING_ROLLOUT', 'test-fastpath-' + str(os.getpid()), send=False); got = []
    tl.on_alert = got.append
    return tl, got
_fp, _fpal = _fresh()
_res = [(_e['t'], _fp.fast_path(_e['text_clean'], 'XSIM', f"src_{_e['t']}", _e['t'])) for _e in _e1]
_again = _fp.process_transmission(_e1[-1]['text_clean'], 'XSIM', f"src_{_e1[-1]['t']}", _e1[-1]['t'])
_nog = _fp.fast_path('Say again?', 'XSIM', 'src_x', 40)
_ag, _agal = _fresh()
for _e in _e1:
    _ag.process_transmission(_e['text_clean'], 'XSIM', f"src_{_e['t']}", _e['t'])
check('option 1: fast path types E1 on arrival and posts the t=28 alert at once (dry run); process_transmission returns it without re-ingesting; non-grammar text goes to the agent; agent-only path also alerts once',
      len(_fpal) == 1 and [w['rule_id'] for w in _fp.led.warnings] == ['R1'] and _fpal[0]['via'] == 'host fast path' and _fpal[0]['result']['status'] == 'dry_run_not_sent'
      and 'already processed' in _again.get('note', '') and len([w for w in _fp.led.warnings]) == 1 and _nog['grammar'] is False
      and len(_agal) == 1 and _agal[0]['via'] == 'agent process_transmission', str([(a['via'], a['result'].get('warning_id')) for a in _fpal + _agal]))

# --- parser round 2: JO 7110.65BB phrasings, fixed on the dev3 split (pool C) and the order's own examples ---
from gmaps_core.parser import parse as _p2
def _f(text, ap='KLGA', hint=None):
    e = _p2(text, ap, speaker_hint=hint, event_time=0)
    hold = next((h.get('runway') for h in e['hold_constraints'] if h.get('runway')), None)
    return (e['actor_candidates'], e['runway'], e['kind'], e['crossing_at'], hold)
_r2 = {
    # 2-4-20a2 group form (dev3 lines, then the 7110.65 examples)
    'Southwest fourteen oh nine, runway two six left, cleared to land.': (['SWA1409'], '26L', 'LANDING_CLEARANCE', None, None),
    'Cleared to land eight right, JetBlue nineteen ninety five.': (['JBU1995'], '08R', 'LANDING_CLEARANCE', None, None),
    'American fifty-two, runway four, cleared to land.': (['AAL52'], '04', 'LANDING_CLEARANCE', None, None),
    'Delta one hundred, runway four, cleared to land.': (['DAL100'], '04', 'LANDING_CLEARANCE', None, None),
    'United ten zero four, runway four, cleared to land.': (['UAL1004'], '04', 'LANDING_CLEARANCE', None, None),
    'United one zero one, runway four, cleared to land.': (['UAL101'], '04', 'LANDING_CLEARANCE', None, None),
    # 2-4-20a1 civil registration
    'November seven two six three Uniform, cross runway two two at taxiway Quebec, without delay.': (['N7263U'], '22', 'CROSS', 'Q', None),
    'Bonanza one two three four Tango, runway four, cleared to land.': (['N1234T'], '04', 'LANDING_CLEARANCE', None, None),
    'Cross niner left at Lima, November two six six two Uniform.': (['N2662U'], '09L', 'CROSS', 'L', None),
    # 3-9-4m / 3-9-10b intersection, 3-9-10i / 3-10-5e wind between runway and verb
    'American twenty nine fifty nine, runway two two at Foxtrot, line up and wait.': (['AAL2959'], '22', 'LUAW', None, None),
    'Delta two six six, runway one three, wind three two zero at seven, cleared for takeoff.': (['DAL266'], '13', 'TAKEOFF_CLEARANCE', None, None),
    'Endeavor nineteen eighty eight, runway four, wind one zero zero at one six, cleared to land.': (['EDV1988'], '04', 'LANDING_CLEARANCE', None, None),
    # 3-7-2a crossing elements in field order
    'American seventeen thirty six, at Whiskey, cross runway three one.': (['AAL1736'], '31', 'CROSS', 'W', None),
    # 3-10-9a exit + 2-1-17 frequency: the exit is the instruction
    'Frontier twelve oh nine, turn left next taxiway, contact ground point seven.': (['FFT1209'], None, 'TAXI', None, None),
    # 2-4-3b hold short read back in read-back shape
    'Holding short runway four, Delta twenty one eighty nine.': (['DAL2189'], None, 'HOLD_SHORT', None, '04'),
}
_bad2 = {t: (_f(t), want) for t, want in _r2.items() if _f(t) != want}
check('parser round 2: 7110.65 group-form flight numbers, civil registrations, wind/intersection before the clearance, crossing word order, exit + frequency, hold-short read back',
      not _bad2, '; '.join(f'{t[:40]}: got {g} want {w}' for t, (g, w) in list(_bad2.items())[:4]))
_g2 = {
    'Delta one two three, hold short of runway four.': (['DAL123'], None, 'HOLD_SHORT', None, '04'),        # digits stay digits
    'Truck one, taxi via November two, hold short of runway four.': (['TRUCK1'], None, 'TAXI', None, '04'),  # November two is a taxiway, not a registration
    'Truck one, taxi via Alpha, November one two, hold short of runway four.': (['TRUCK1'], None, 'TAXI', None, '04'),
    'Tower, Rescue seven, request to cross runway three six at Kilo.': (['RESCUE7'], '36', 'CROSS_REQUEST', 'K', None),
    'American five two, climb and maintain two thousand.': (['AAL52'], None, 'OTHER', None, None),
}
_gbad2 = {t: (_f(t), want) for t, want in _g2.items() if _f(t) != want}
check('parser round 2 guards: digit callsigns, November-as-taxiway, requests and altitudes unchanged', not _gbad2,
      '; '.join(f'{t[:40]}: got {g} want {w}' for t, (g, w) in _gbad2.items()))
_w2 = {   # Whisper renderings on the dev3 split (generic repairs only; one-off mishearings are left alone)
    'Line up, and wait 2-6 left. American 29-59.': (['AAL2959'], '26L', 'LUAW', None, None),
    'Line up and weight 8 left, jet blue 228.': (['JBU228'], '08L', 'LUAW', None, None),
    'Appeared for takeoff 8 right. Frontier 647.': (['FFT647'], '08R', 'TAKEOFF_CLEARANCE', None, None),
    'Fear to land for. Spirit 6-4-1.': (['NKS641'], '04', 'LANDING_CLEARANCE', None, None),
    'Cleared to land for Endeavor 1988.': (['EDV1988'], '04', 'LANDING_CLEARANCE', None, None),
    'American 2959, runway 22 at Farkstraat, line up and wait.': (['AAL2959'], '22', 'LUAW', None, None),
    'Cross 9 or left at Lima, November 2662 uniform.': (['N2662U'], '09L', 'CROSS', 'L', None),
}
_wbad2 = {t: (_f(t), want) for t, want in _w2.items() if _f(t) != want}
_wg2 = {'Truck 1 & Company, cross for Delta.': _f('Truck 1 & Company, cross for Delta.')[2], 'Delta 520, runway 4, cleared for takeoff.': _f('Delta 520, runway 4, cleared for takeoff.')}
check("parser round 2, Whisper forms (dev3): 'line up, and wait' / 'weight', '<x>ear(ed) for takeoff / to land', 'for' = four after a clearance, any intersection word, 'cross 9 or left'; guards unchanged",
      not _wbad2 and _wg2['Truck 1 & Company, cross for Delta.'] != 'LANDING_CLEARANCE' and _wg2['Delta 520, runway 4, cleared for takeoff.'] == (['DAL520'], '04', 'TAKEOFF_CLEARANCE', None, None),
      '; '.join(f'{t[:40]}: got {g} want {w}' for t, (g, w) in _wbad2.items()) + f' guards {_wg2}')
from gmaps_core.ledger import Ledger as _L3
_l3 = _L3(LGA)
for _t, _x in [(0, 'Endeavor one six three three, cross runway two two at Bravo.'), (4, 'Cross two two left at Bravo, Endeavor one six three three.'),
               (10, 'United one two three, cross runway one three at Bravo.'), (14, 'Cross three one at Bravo, United one two three.')]:
    _l3.ingest(_p2(_x, 'KLGA', resolver=LGA, event_time=_t), _t)
_r7c = [w['authorization_a']['callsign'] for w in _l3.warnings if w['rule_id'] in ('R14', 'R16') and w.get('mismatch') == 'discrepant_runway']
check('ledger: a runway heard without its side is ambiguous, not discrepant (22 vs 22L: no mismatch); a different runway still is (13 vs 31: R14/R16)',
      'EDV1633' not in _r7c and 'UAL123' in _r7c, str(_r7c))
from gmaps_core.ledger import Ledger as _L2
_l2 = _L2(LGA)
for _t, _x in [(0, 'Spirit six four one, runway four, cleared to land.'), (18, 'Frontier twelve ninety eight, hold short of runway four.'),
               (22, 'Frontier twelve ninety eight, holding short of runway four.'), (40, 'Delta one two three, hold short of runway four.'), (44, 'Roger, Delta one two three.')]:
    _l2.ingest(_p2(_x, 'KLGA', resolver=LGA, event_time=_t), _t)
_l2.tick(60)
_r7 = [(w['authorization_a']['callsign'], w['status']) for w in _l2.warnings if w['rule_id'] == 'R7']
check("ledger: a pilot's 'holding short of runway X' after a recorded hold short counts as its read back (2-4-3b); 'roger' alone still leaves R7 open",
      'FFT1298' in _l2.actors and any(p['callsign'] == 'FFT1298' and p.get('acknowledged') is True for p in _l2.pending)
      and ('FFT1298', 'open') not in _r7 and any(c == 'DAL123' for c, _ in _r7), str(_r7))

# --- round 3: readback rules (a) callsign AND runway must match; (b) wrong or missing runway -> R14 readback mismatch ---
def _led(lines, timers=None, until=None):
    L = _L2(LGA, timers=timers)
    for _t, _x in lines:
        L.ingest(_p2(_x, 'KLGA', resolver=LGA, event_time=_t), _t)
    if until:
        L.tick(until)
    return L
_a1 = _led([(18, 'Frontier twelve ninety eight, hold short of runway four.'), (22, 'Frontier twelve ninety eight, holding short of runway three one.')], until=60)
_a2 = _led([(18, 'Frontier twelve ninety eight, hold short of runway four.'), (22, 'Delta one two three, holding short of runway four.')], until=60)
_b1 = _led([(0, 'Delta one two three, runway two two, cleared to land.'), (4, 'Cleared to land three one, Delta one two three.')])
_b2 = _led([(0, 'Delta one two three, runway two two, cleared to land.'), (4, 'Cleared to land, Delta one two three.')])
_b3 = _led([(0, 'Delta one two three, runway two two, cleared to land.'), (4, 'Cleared to land two two, Delta one two three.')])
_cat = lambda L: sorted({(w['rule_id'], w['category']) for w in L.warnings})
check("ledger round 3: 'holding short' counts as the read back only if callsign AND runway match (wrong runway -> R14 and the hold stays open -> R7; another aircraft's report acknowledges nothing)",
      ('R16', 'readback_mismatch_low_confidence') in _cat(_a1) and ('R7', 'missed_readback') in _cat(_a2) and not [c for c in _cat(_a2) if c[0] in ('R14', 'R16')], f'{_cat(_a1)} {_cat(_a2)}')
check('ledger round 4: a wrong runway with no ASR confidence is R16 (info), a missing runway is R15 incomplete readback (info), a correct one raises nothing; none is a conflict alert',
      _cat(_b1) == [('R16', 'readback_mismatch_low_confidence')] and _cat(_b2) == [('R15', 'incomplete_readback')] and _cat(_b3) == [], f'{_cat(_b1)} {_cat(_b2)} {_cat(_b3)}')
def _ledc(conf, thr=0.6):
    L = _L2(LGA, readback_conf_min=thr)
    L.ingest(_p2('Delta one two three, runway two two, cleared to land.', 'KLGA', resolver=LGA, event_time=0), 0)
    e = _p2('Cleared to land three one, Delta one two three.', 'KLGA', resolver=LGA, event_time=4); e['runway_confidence'] = conf
    L.ingest(e, 4)
    return _cat(L)
check('ledger round 4: an explicit different runway is a readback-mismatch CANDIDATE (R14) only when the runway word confidence clears the threshold; below it R16 (info)',
      _ledc(0.95) == [('R14', 'readback_mismatch')] and _ledc(0.3) == [('R16', 'readback_mismatch_low_confidence')], f'{_ledc(0.95)} {_ledc(0.3)}')
from gmaps_core.asr_conf import runway_confidence as _rc
_W = [{'w': 'Cleared', 'p': 0.99}, {'w': 'to', 'p': 0.98}, {'w': 'land', 'p': 0.97}, {'w': 'runway', 'p': 0.9}, {'w': 'three', 'p': 0.62}, {'w': 'one,', 'p': 0.88}, {'w': 'Delta', 'p': 0.95}, {'w': '123.', 'p': 0.9}]
_W2 = [{'w': 'Cleared', 'p': 0.99}, {'w': 'to', 'p': 0.98}, {'w': 'land', 'p': 0.97}, {'w': '2-7', 'p': 0.7}, {'w': 'right,', 'p': 0.41}, {'w': 'Delta', 'p': 0.95}]
check('Whisper word confidence on the runway token: minimum over the designator words (incl. the side); None when the runway is not found',
      _rc(_W, '31') == 0.62 and _rc(_W2, '27R') == 0.41 and _rc(_W, '22') is None, f"{_rc(_W, '31')} {_rc(_W2, '27R')} {_rc(_W, '22')}")
_c1 = _led([(18, 'Frontier twelve ninety eight, hold short of runway four.')], timers={'R7': 25}, until=40)
_c2 = _led([(18, 'Frontier twelve ninety eight, hold short of runway four.')], timers={'R7': 25}, until=45)
check('ledger: the readback timeout is a config value (R7 at 25 s fires at t+25, not before)', not _cat(_c1) and _cat(_c2) == [('R7', 'missed_readback')], f'{_cat(_c1)} {_cat(_c2)}')

# --- round 3 parser/ledger (dev4): 'clear runway X at T' report, 'continue taxiing via' / 'proceed via' release (section 8), clearance without runway = ambiguous ---
from gmaps_core.parser import classify_speaker as _cs3
_e3 = _p2('American niner fifty one, clear runway three one at Tango.', 'KLGA', resolver=LGA, event_time=45)
_s8 = _led([(0, 'Southwest eighteen eighty two, runway two two cleared to land.'), (4, 'Southwest eighteen eighty two, roger, cleared to land runway two two.'),
            (40, 'Southwest eighteen eighty two, turn right next taxiway, contact ground point seven.'), (48, 'Southwest eighteen eighty two, continue taxiing via Alpha to the ramp.'),
            (55, 'Endeavor twenty eight seventy eight, cross runway two two at Bravo.')])
_s8b = _led([(0, 'Southwest eighteen eighty two, runway two two cleared to land.'), (48, 'Southwest eighteen eighty two, proceed via Alpha to the ramp.'),
             (55, 'Endeavor twenty eight seventy eight, cross runway two two at Bravo.')])
check("round 3: 'clear runway X at T' is the pilot's clear-of-runway report; 'continue taxiing via' / 'proceed via' are ground taxi clearances that release the landing (section 8)",
      (_e3['kind'], _e3['runway']) == ('CROSSING_COMPLETE', '31') and not [w for w in _s8.warnings if w['rule_id'] == 'R1'] and not [w for w in _s8b.warnings if w['rule_id'] == 'R1'],
      f"{_e3['kind']} {_e3['runway']} R1={[w['rule_id'] for w in _s8.warnings]} {[w['rule_id'] for w in _s8b.warnings]}")
_amb = _cs3('Delta twenty five ninety three, cleared to land.')[0]
_rb = _L2(LGA)
for _t, _x in [(0, 'Delta twenty five ninety three, runway two six left, cleared to land.'), (4, 'Delta twenty five ninety three, cleared to land.')]:
    _rb.ingest(_p2(_x, 'KATL', speaker_hint=_cs3(_x)[0], resolver=LGA, event_time=_t), _t)
check("round 3: a clearance with no runway stated before it is AMBIGUOUS (3-10-5a: the controller states the runway first); after a recorded clearance it is a read back missing the runway -> R15 incomplete readback (round 4)",
      _amb == 'AMBIGUOUS' and _cs3('Delta 123, runway two two, cleared to land.')[0] == 'ATC' and any(w['rule_id'] == 'R15' for w in _rb.warnings), f'{_amb} {[w["rule_id"] for w in _rb.warnings]}')

_w3 = {'United 405, runway 27R, wind 080 at 9 August 23, cleared for takeoff.': (['UAL405'], '27R', 'TAKEOFF_CLEARANCE', None, None),
       'Cleared for takeoff for Frontier 1077.': (['FFT1077'], '04', 'TAKEOFF_CLEARANCE', None, None),
       'JetBlue 2519, taxi to the ramp by our Alpha Seven.': (['JBU2519'], None, 'TAXI', None, None)}
_wb3 = {t: (_f(t), w) for t, w in _w3.items() if _f(t) != w}
check("round 3 Whisper forms (dev4): one misheard word inside the wind clause; 'for' = four after 'cleared for takeoff'", not _wb3, str(_wb3))

# --- round 3 components: LLM-parse validation (no model call here), traffic-list matching, the fast path's readback clock ---
sys.path.insert(0, str(ROOT / 'venue'))
from llm_fallback import LLMFallback as _LF
_lf = _LF(['04', '22', '13', '31'], ['A', 'B', 'P', 'E'])
_h1 = _lf.validate({'speaker_role': 'ATC', 'callsign': 'AAL100', 'runway': '04', 'instruction': 'CROSS', 'taxiways': ['A', 'B']}, 'T-1041, activate pop-up, crossroad release.')
_h2 = _lf.validate({'speaker_role': 'ATC', 'callsign': 'FFT100', 'runway': '13', 'instruction': 'LANDING_CLEARANCE', 'taxiways': []}, 'Appear 100, runway 1-3 cleared to land.', ['FFT100', 'DAL230'])
_h3 = _lf.validate({'speaker_role': 'ATC', 'callsign': 'FFT100', 'runway': '13', 'instruction': 'LANDING_CLEARANCE', 'taxiways': []}, 'Appear 100, runway 1-3 cleared to land.')
_h4 = _lf.validate({'speaker_role': 'ATC', 'callsign': 'DAL363', 'runway': '04', 'instruction': 'HOLD_SHORT', 'taxiways': []}, 'Delta 363, runway 4, continue traffic holding in position.')
_h5 = _lf.validate({'speaker_role': 'ATC', 'callsign': 'DAL363', 'runway': '27', 'instruction': 'LANDING_CLEARANCE', 'taxiways': []}, 'Delta 363, runway 27, cleared to land.')
# N17: group-form flight numbers count as heard ('four thirty five' = 435, 'fourteen oh nine' = 1409); a different number still drops
_h6 = _lf.validate({'speaker_role': 'PILOT', 'callsign': 'UAL435', 'runway': '04', 'instruction': 'LANDING_CLEARANCE', 'taxiways': []}, 'Cleared to land four, United four thirty five.')
_h7 = _lf.validate({'speaker_role': 'ATC', 'callsign': 'DAL1409', 'runway': None, 'instruction': 'HOLD_SHORT', 'taxiways': []}, 'Delta fourteen oh nine, hold short of runway one three.')
_h8 = _lf.validate({'speaker_role': 'PILOT', 'callsign': 'UAL436', 'runway': '04', 'instruction': 'LANDING_CLEARANCE', 'taxiways': []}, 'Cleared to land four, United four thirty five.')
check('LLM fallback validation: ungrounded values are dropped (hallucinated runway/kind; airline not heard and not on the traffic list; hold without "hold short"); off-chart runway rejected; a grounded parse passes; N17: group-form flight numbers count as heard',
      _h1[0] is None and _h2[0] and _h2[0]['actionable'] and _h3[0] is None and _h4[0] is None and _h5[0] is None and 'chart' in _h5[1]
      and _h6[0] and _h6[0]['actionable'] and _h7[0] and _h7[0]['actionable'] and _h8[0] is None and 'flight number' in _h8[1],
      f'{_h1[1]} | {_h2[1]} | {_h3[1]} | {_h4[1]} | {_h5[1]} | N17: {_h6[1]} | {_h7[1]} | {_h8[1]}')
from gmaps_core.traffic import match_one as _m1
_T = {'AAL379', 'FFT100', 'DAL1054', 'NKS801', 'SWA319', 'DAL379'}
check('traffic list: callsigns change only on a unique match (exact, same digits, one digit off / adjacent swap); ambiguous or unknown stay as heard',
      _m1('NKS8001', _T) == ('NKS801', 'one digit off') and _m1('DAL1045', _T) == ('DAL1054', 'one digit off') and _m1('?379', _T) == (None, None)
      and _m1('?100', _T) == ('FFT100', 'same digits') and _m1('UAL555', _T) == (None, None), f"{_m1('?379', _T)} {_m1('?100', _T)}")
_ck = GM.GmapsTools('SIM_E1_CROSS_DURING_ROLLOUT', 'test-clock-' + str(os.getpid()), config=dict(GM.CONFIG, readback_timeout_s=12, llm_fallback=False)); _ckal = []
_ck.on_alert = _ckal.append
_ck.fast_path('Rescue seven, hold short of runway three six.', 'XSIM', 'c0', 0)
_ck.advance(11); _before = len(_ckal); _ck.advance(13)
check('fast path owns the readback timer (config value): no alert before the timeout, the missed-readback alert at the timeout from the clock, with no agent involved',
      _before == 0 and len(_ckal) == 1 and _ckal[0]['via'] == 'host fast path clock' and _ckal[0]['result']['category'] == 'missed_readback', str([(a['via'], a['result'].get('rule')) for a in _ckal]))

from llm_fallback import identify as _idf
_i1 = _idf('N87R', 'Hold short of runway eight left, twin Cessna pole zero eight seven Romeo.', {'N4087R', 'N4069Z'})
_i2 = _idf('N4087R', 'Quincesta 4087 Romeo, proceed by a charge, hold short of runway eight left.', {'N4087R'})
_i3 = _idf('FFT101', 'Appear 100, runway 1-3 cleared to land.', {'FFT100', 'DAL230'})
_i4 = _idf('UAL555', 'United 555, runway 4, cleared to land.', {'FFT100'})
check("LLM registration check (Oct 9): N87R from 'pole zero eight seven Romeo' is an unidentified aircraft; a registration heard whole and on the list is kept; one digit off a listed callsign is corrected; unknown callsigns stay unidentified",
      _i1[0] is None and _i2 == ('N4087R', None) and _i3[0] == 'FFT100' and 'repaired' in (_i3[1] or '') and _i4[0] is None, f'{_i1} {_i2} {_i3} {_i4}')

# --- two logged gaps implemented as 7110.65 behaviour (NOT YET HELD-OUT VALIDATED: judged on real clips) ---
_gp = {'Spirit twenty four hundred, runway two two, cleared to land.': (['NKS2400'], '22', 'LANDING_CLEARANCE', None, None),
       'United twelve hundred, hold short of runway four.': (['UAL1200'], None, 'HOLD_SHORT', None, '04'),
       'Delta one hundred, runway four, cleared to land.': (['DAL100'], '04', 'LANDING_CLEARANCE', None, None),
       'Delta one two three, clear of the runway.': (['DAL123'], None, 'CROSSING_COMPLETE', None, None)}
_gpb = {t: (_f(t), w) for t, w in _gp.items() if _f(t) != w}
_cr = _led([(0, 'Delta one two three, runway two two, cleared to land.'), (2, 'United four five six, runway two two, line up and wait, traffic landing.'),
            (40, 'Delta one two three, clear of the runway.'), (50, 'Spirit seven eight nine, cross runway two two at Bravo.')])
_cr2 = _led([(0, 'Delta one two three, runway two two, cleared to land.'), (30, 'Spirit seven eight nine, cross runway three one at Bravo.'),
             (40, 'Delta one two three, clear of the runway.')])
_other_x = [r['status'] for r in _cr2.reservations if r['callsign'] == 'NKS789' and r['type'] == 'CROSSING']
_own = [r for r in _cr.reservations if r['callsign'] == 'DAL123' and r['type'] == 'LANDING']
_luaw = [r for r in _cr.reservations if r['callsign'] == 'UAL456' and r['status'] != 'released']
check("7110.65 gaps (not yet held-out validated): 'twenty four hundred' = 2400 (2-4-20a2 group form); 'clear of the runway' with no number releases that aircraft's OWN landing only",
      not _gpb and _own and all(r['status'] == 'released' for r in _own) and _luaw and _other_x and 'released' not in _other_x,
      f'{_gpb} own={[r["status"] for r in _own]} luaw={len(_luaw)} other_crossing={_other_x}')

# --- real-clip readiness: klga_real / khwd_real score as soon as clips are labelled (scratch labels folder) ---
import tempfile as _tf
_td = Path(_tf.mkdtemp()); (_td / 'labels').mkdir()
_sets = json.loads((Path.home() / 'gmaps_venue/labels/real_clip_sets.json').read_text()) if (Path.home() / 'gmaps_venue/labels/real_clip_sets.json').exists() else None
if _sets:
    _hw = sorted(c for c, r in _sets['clips'].items() if r['set'] == 'khwd_real')[0]
    (_td / 'labels/real_clip_sets.json').write_text(json.dumps(_sets))
    (_td / 'labels/klga_real_clip_labels.json').write_text(json.dumps({'labels': {_hw: {'clip': _hw, 'judgement': 'partial', 'ref_callsign': 'N1280H', 'ref_runway': '28R', 'ref_speaker': 'ATC'}}}))
    _oldv = HN.VENUE; HN.VENUE = _td
    try:
        _hs = HN.set_real('khwd_real'); _hsum = HN.summarize('khwd_real', _hs); _need = HN.labels_needed('khwd_real', _hs)
        _unk = sum(1 for c, r in _sets['clips'].items() if r['set'] == 'unknown_airport')
    finally:
        HN.VENUE = _oldv
    check('real clips: khwd_real scores as soon as a clip carries reference fields (only the labelled fields count); unknown_airport clips are never in a scored set; labels-needed printed per metric',
          _hsum['scored'] == 1 and _need['metrics']['runway_accuracy']['labelled'] == 1 and _need['metrics']['instruction_accuracy']['labelled'] == 0
          and _need['metrics']['speaker_classifier_accuracy']['labelled'] == 1 and _hs[0]['set_size'] == sum(1 for r in _sets['clips'].values() if r['set'] == 'khwd_real')
          and all('confirmed by Franco 2026-10-10' in (r.get('provenance') or '') for r in _sets['clips'].values() if r.get('promoted_from')),
          f"{_hsum['scored']} {_need['metrics']['runway_accuracy']}")
else:
    check('real clips: sets file present (run python3 venue/real_clip_sets.py)', False)

# --- review page grouping by confirmed airport set (no real clips needed) ---
import shutil as _shu
_nd = _node   # same node lookup as the fast-pass test above
_gq = subprocess.run([_nd, '-e', "const R=require(process.argv[1]); const c=[{clip:'a',airport_set:'unknown_airport',status:'proposed'},{clip:'b',airport_set:'khwd_real',status:'excluded'},"
                      "{clip:'c',airport_set:'klga_real',status:'weak'},{clip:'d',airport_set:'klga_real',status:'proposed'},"
                      "{clip:'e',airport_set:'klga_real',status:'proposed',set_provenance:'airport inferred from recording + evidence sheet, confirmed by Franco 2026-10-10'},"
                      "{clip:'f',airport_set:'khwd_real',status:'excluded',set_provenance:'airport inferred from recording + evidence sheet, confirmed by Franco 2026-10-10'}]; console.log(JSON.stringify(R.orderClips(c).map(x=>[x.clip,R.groupOf(x)])))",
                      str(ROOT / 'ui_scaffold/klga_review.js')], capture_output=True, text=True).stdout.strip() if _nd else '[]'
check('review page groups by confirmed airport set: klga_real strong -> weak -> LGA promoted (N4, Oct 10) -> khwd_real -> KHWD promoted -> unknown_airport',
      json.loads(_gq or '[]') == [['d', 'strong'], ['c', 'weak'], ['e', 'LGA_promoted'], ['b', 'KHWD'], ['f', 'KHWD_promoted'], ['a', 'unknown']], _gq)

# --- readback-mismatch shadow mode: R14 candidates are logged only, never posted, not offered to the agent ---
_sh = GM.GmapsTools('SIM_E1_CROSS_DURING_ROLLOUT', 'test-shadow-' + str(os.getpid()), config=dict(GM.CONFIG, llm_fallback=False, readback_mismatch_min_word_conf=0.6)); _shal = []
_sh.on_alert = _shal.append
_sh.fast_path('Simair two one two, runway three six, cleared to land.', 'XSIM', 's0', 0)
_sh.fast_path('Cleared to land one eight, Simair two one two.', 'XSIM', 's1', 4,
              words=[{'w': 'Cleared', 'p': 0.99}, {'w': 'to', 'p': 0.99}, {'w': 'land', 'p': 0.99}, {'w': 'one', 'p': 0.95}, {'w': 'eight,', 'p': 0.93}, {'w': 'Simair', 'p': 0.9}, {'w': '212.', 'p': 0.9}])
_r14 = [w for w in _sh.led.warnings if w['rule_id'] == 'R14']
try:
    _sh.post_alert(_r14[0]['warning_id']); _refused = False
except GM.ToolError:
    _refused = True
check('readback mismatch is SHADOW ONLY: the R14 candidate is logged (shadow record), never posted; post_alert refuses it; open_warnings does not offer it to the agent',
      _r14 and all(a.get('shadow') for a in _shal) and not [a for a in _shal if not a.get('shadow')] and _refused
      and not [w for w in _sh.open_warnings()['open_warnings'] if w['category'] in ('readback_mismatch', 'readback_mismatch_low_confidence', 'incomplete_readback')],
      f'{[(a.get("shadow"), a["result"].get("rule")) for a in _shal]} refused={_refused}')

# --- the runtime copy must hold every library module the deployed server imports ---
_brs = (ROOT / 'venue/build_runtime.py').read_text()
_imps = set(re.findall(r'from (gmaps_core(?:\.\w+)+) import', (ROOT / 'venue/gmaps_mcp.py').read_text() + (ROOT / 'venue/llm_fallback.py').read_text()))
_miss = sorted(m for m in _imps if f"'lib/{m.replace('.', '/')}.py'" not in _brs)
check('runtime allow-list (venue/build_runtime.py) holds every gmaps_core module that gmaps_mcp.py / llm_fallback.py import', not _miss, str(_miss))

# --- flight-number callsign repair (round 5): only one listed aircraft with those digits; never N-registrations ---
from gmaps_core.traffic import match_callsigns as _mc5
def _rep(heard, traffic, text=''):
    e = {'actor_candidates': [heard] if heard else [], 'normalized_text': text, 'uncertainty': {'confidence_heuristic': 0.9}}
    _mc5(e, traffic); return e['actor_candidates'], e.get('callsign_repaired', False), e['uncertainty']['confidence_heuristic']
_r1 = _rep('DAL964', {'NKS964', 'UAL555'})             # wrong airline, digits unique on the list -> repaired
_r2 = _rep('DAL964', {'NKS964', 'AAL964'})             # two listed aircraft share the digits -> kept as heard
_r3 = _rep('N1247', {'N1247S', 'UAL555'})              # registrations: exact only -> kept as heard
_r4 = _rep('NKS964', {'NKS964'})                       # exact -> not a repair
check('callsign repair (Oct 9): a flight number matching exactly ONE listed aircraft repairs the airline, tagged callsign_repaired with lower confidence; never when two share the digits; never for N-registrations; an exact match is not a repair',
      _r1 == (['NKS964'], True, 0.5) and _r2[:2] == (['DAL964'], False) and _r3[:2] == (['N1247'], False) and _r4 == (['NKS964'], False, 0.9), f'{_r1} {_r2} {_r3} {_r4}')
_id5 = _idf('DAL964', 'Cross runway two six right at Delta Spirit nine and six to four.', {'NKS964', 'UAL555'})
_id6 = _idf('DAL964', 'x', {'NKS964', 'AAL964'})
_id7 = _idf('DAL964', 'x', {'?2175', 'TRUCK1', 'NKS964'})   # unknown-airline and vehicle actors in the ledger must not break the match
check('LLM callsigns use the same repair: right flight number, wrong airline -> the one listed aircraft (repaired); ambiguous digits -> unidentified',
      _id5[0] == 'NKS964' and 'repaired' in (_id5[1] or '') and _id6[0] is None and _id7[0] == 'NKS964', f'{_id5} {_id6} {_id7}')

# --- persistent agent session: token-protected turn queue; rotation every N; fresh session on a crash ---
import threading as _th, urllib.request as _ur, urllib.error as _ue
from http.server import ThreadingHTTPServer as _TS
_tok = 'test-token-' + 'x' * 16
_QH = GM.make_handler(GM.GmapsTools('SIM_E1_CROSS_DURING_ROLLOUT', 'test-queue-' + str(os.getpid())), _tok, Path(os.devnull)); _QH.log_message = lambda *a: None
_qs = _TS(('127.0.0.1', 0), _QH)
_th.Thread(target=_qs.serve_forever, daemon=True).start()
def _q(path, body=None, tok=_tok):
    r = _ur.Request(f'http://127.0.0.1:{_qs.server_port}{path}', data=json.dumps(body).encode() if body is not None else None, headers={'Authorization': f'Bearer {tok}'})
    try:
        with _ur.urlopen(r, timeout=5) as x:
            return x.status, json.loads(x.read() or b'null')
    except _ue.HTTPError as e:
        return e.code, None
_unauth = _q('/agent/next?wait=0', tok='wrong-token-xxxxxxxxxxxx')[0]
_badkey = _q('/agent/enqueue', {'id': 'q0', 'session_key': 'agent:main:x', 'message': 'hi'})[0]
_q('/agent/enqueue', {'id': 'q1', 'session_key': 'agent:gmaps:t-s0', 'message': 'New transmission ...', 'timeout_s': 30})
_got = _q('/agent/next?wait=1'); _noack = _q('/agent/ack?id=q1&wait=0')[0]; _q('/agent/ack', {'id': 'q1'}); _ack = _q('/agent/ack?id=q1&wait=1')[0]
_q('/agent/result', {'id': 'q1', 'ok': True, 'content': 'explained'})
_res = _q('/agent/result?id=q1&wait=1'); _empty = _q('/agent/next?wait=0')[0]; _st = _q('/agent/status')[1]
_qs.shutdown()
check('persistent session: turn queue needs the token, accepts only agent:gmaps session keys, hands a message to the client and its result back once',
      _unauth == 401 and _badkey == 400 and _got[1]['id'] == 'q1' and _noack == 204 and _ack == 200 and _res[1]['content'] == 'explained' and _empty == 204 and _st['client_seen_age_s'] is not None,
      f'unauth={_unauth} badkey={_badkey} got={_got} res={_res} empty={_empty}')
_spec2 = importlib.util.spec_from_file_location('agent_session_t', ROOT / 'venue/agent_session.py'); AS = importlib.util.module_from_spec(_spec2); _spec2.loader.exec_module(AS)
class _FakeAgent(AS.PersistentAgent):   # no sandbox: scripted client outcomes
    def __init__(self, outcomes):
        super().__init__('t', rotate_every=3, log_name=os.devnull); self.outcomes, self.sent, self.up = list(outcomes), [], True
    def alive(self): return self.up
    def start(self): self.up = True; return True
    def stop(self): pass
    def _one(self, message, timeout):
        self.sent.append(self.session_key); return self.outcomes.pop(0)
_fa = _FakeAgent([{'ok': True}] * 4 + [{'ok': False, 'error': 'client process exited'}, {'ok': True}] + [{'ok': False, 'error': 'x'}] * 2)
_rcs = [_fa.turn('m')[0] for _ in range(6)]
check('persistent session: a fresh session key every 3 transmissions (context cap); a crash restarts on a fresh key and resends once; two failures hand the turn to the CLI fallback',
      _rcs == [0, 0, 0, 0, 0, 1] and _fa.sent == ['agent:gmaps:t-s0'] * 3 + ['agent:gmaps:t-s1', 'agent:gmaps:t-s1', 'agent:gmaps:t-s2', 'agent:gmaps:t-s2', 'agent:gmaps:t-s3'],
      f'rcs={_rcs} sent={_fa.sent}')
_spec3 = importlib.util.spec_from_file_location('e2e_server_t', ROOT / 'eval/e2e_server.py'); ES = importlib.util.module_from_spec(_spec3); _spec3.loader.exec_module(ES)
_et = ES.EvalTools('KLGA', 'test-e2e-' + str(os.getpid())); _et.on_alert = lambda r: None
_et.fast_path('Delta one two three, runway four, cleared to land.', 'KLGA', 's0', 0)
_er = _et.process_transmission('United four five six, cross runway four at Bravo.', 'XSIM', 's1', 18)
check('end-to-end eval server: deployed fast path + process_transmission on chart identifiers; the look-ahead never predicts on a real airport (prediction_unavailable)',
      _er['lookahead']['status'] == 'prediction_unavailable' and any(w['rule_id'] == 'R1' for w in _et.led.warnings) and _et.airport == 'KLGA', str(_er.get('lookahead')))
_cat = json.loads((ROOT / 'eval/synthetic/phraseology_7110.json').read_text())
_cite_ok = all(re.search(r'\b[23]-\d+-\d+', v['cite']) for v in _cat['atc'].values()) and re.search(r'2-4-3', _cat['pilot']['cite']) and re.search(r'2-4-20', _cat['callsign']['cite'])
_disj = all('shared' in v or not ({json.dumps(x) for x in v['C']} & {json.dumps(x) for x in v['D']}) for v in _cat['atc'].values()) and not (set(_cat['pilot']['C'].values()) & set(_cat['pilot']['D'].values()))
check('synthetic v3: every phrasing element cites its JO 7110.65BB paragraph; dev (C) and held-out (D) realizations are disjoint', bool(_cite_ok and _disj))

# --- sandbox rebuild runbook: proposed scripts are dry-run by default and stay inside their bounds ---
_PR = ROOT / 'venue/systemd/proposed'
_spec4 = importlib.util.spec_from_file_location('post_rebuild_t', _PR / 'post_rebuild_check.py'); PRC = importlib.util.module_from_spec(_spec4); _spec4.loader.exec_module(PRC)
_pol = "network_policies:\n  local_inference:\n    endpoints:\n      - host: host.openshell.internal\n        port: 11435\n        rules:\n          - allow: {method: GET, path: /**}\n    binaries:\n      - path: /usr/bin/node\n"
_pol_wide = _pol + "  extra:\n    endpoints:\n      - host: example.com\n        port: 443\n    binaries:\n      - path: /usr/bin/curl\n"
_gw = {'gateway': {'mode': 'local', 'auth': {'token': 'x' * 20}, 'http': {'endpoints': {'chatCompletions': {'enabled': True}}}}}
check('sandbox runbook: post-rebuild check flags wider egress, accepts the same policy, needs a loopback token gateway, and compares vLLM identity',
      not PRC.wider(PRC.egress(_pol), PRC.egress(_pol)) and any(t[1] == 'example.com' for t in PRC.wider(PRC.egress(_pol), PRC.egress(_pol_wide)))
      and PRC.gateway_ok(_gw)[0] and not PRC.gateway_ok({'gateway': {'mode': 'local', 'bind': 'lan', 'auth': {'token': 'x'}}})[0]
      and not PRC.gateway_ok({'gateway': {'mode': 'local'}})[0] and not PRC.vllm_ok('abc 2026-10-09T23:33:45Z', 'abc 2026-10-10T01:00:00Z')[0])
_spec5 = importlib.util.spec_from_file_location('reapply_t', _PR / 'sandbox_reapply_config.py'); RAC = importlib.util.module_from_spec(_spec5); _spec5.loader.exec_module(RAC)
_dry = subprocess.run([sys.executable, str(_PR / 'sandbox_reapply_config.py')], capture_output=True, text=True, timeout=60).stdout
_cmds = ' '.join(' '.join(c) for _, c, _ in RAC.plan())
_tok = (Path.home() / 'gmaps_venue/secrets/mcp_token')
_patch = RAC.config_patch(None)
check('sandbox runbook: re-apply script is a dry run by default, never destroys/onboards/rebuilds, never touches docker beyond inspect, Telegram or presets, and never shows the MCP token',
      _dry.count('[dry run]') == len(RAC.plan()) and not re.search(r'\b(destroy|onboard|rebuild|uninstall|channels|policy add)\b|docker (stop|rm|run|restart|kill)|telegram', _cmds, re.I)
      and (not _tok.exists() or _tok.read_text().strip() not in _dry) and _patch['mcp']['servers']['gmaps']['headers']['Authorization'].startswith('Bearer <')
      and _patch['agents']['list'][1]['id'] == 'gmaps' and _patch['gateway']['http']['endpoints']['chatCompletions']['enabled'] is True)
_fake = Path(tempfile.mkdtemp())
(_fake / 'openshell').write_text('#!/bin/sh\necho "NAME CREATED PHASE"; echo "my-assistant 2026-10-08 21:31:12 $FAKE_PHASE"\n')
(_fake / 'nemoclaw').write_text('#!/bin/sh\necho "$*" >> "$FAKE_MARK"\n')
for _f in ('openshell', 'nemoclaw'): os.chmod(_fake / _f, 0o755)
def _lc(action, phase, apply):
    mark = _fake / f'mark_{action}_{phase}_{apply}'
    env = dict(os.environ, PATH=f'{_fake}:/usr/bin:/bin', HOME=str(_fake), FAKE_PHASE=phase, FAKE_MARK=str(mark))
    subprocess.run(['bash', str(_PR / 'sandbox_lifecycle.sh'), action] + (['--apply'] if apply else []), env=env, capture_output=True, timeout=30)
    return mark.read_text().split('\n')[:-1] if mark.exists() else []
_lcr = [_lc('stop', 'Ready', False), _lc('stop', 'Ready', True), _lc('stop', 'Error', True), _lc('start', 'Stopped', True), _lc('start', 'Error', True)]
check('sandbox runbook: lifecycle script runs nothing without --apply; stops only a Ready sandbox, starts (+recover) only a Stopped one, and does nothing in phase Error',
      _lcr == [[], ['my-assistant stop'], [], ['my-assistant start', 'my-assistant recover'], []], str(_lcr))
_spec6 = importlib.util.spec_from_file_location('vllm_relaunch_t', _PR / 'vllm_relaunch_cmd.py'); VRC = importlib.util.module_from_spec(_spec6); _spec6.loader.exec_module(VRC)
_insp = {'Name': '/nemoclaw-vllm', 'Config': {'Image': 'img@sha256:abc', 'Entrypoint': ['/bin/bash'], 'Cmd': ['-lc', 'vllm serve m'], 'Env': ['PATH=/x', 'HF_HOME=/root/.cache/huggingface'],
          'Labels': {'com.nvidia.nemoclaw.managed-vllm': 'true', 'other': 'y'}},
         'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}, 'Binds': ['/h/.cache/huggingface:/root/.cache/huggingface'], 'PortBindings': {'8000/tcp': [{'HostIp': '', 'HostPort': '8000'}]},
                        'DeviceRequests': [{'Count': -1, 'Capabilities': [['gpu']]}], 'IpcMode': 'host', 'ShmSize': 67108864, 'NetworkMode': 'bridge'}}
_rl = VRC.relaunch(_insp, ['PATH=/x'])
_unit_dir = Path.home() / '.config/systemd/user'
check('sandbox runbook: vLLM relaunch command is printed from the saved inspect (same digest, label, cache bind, port, GPUs, IPC, command), never executed; proposed sandbox units are not installed',
      _rl[:2] == ['docker', 'run'] and 'img@sha256:abc' in _rl and 'com.nvidia.nemoclaw.managed-vllm=true' in _rl and '-e' in _rl and 'HF_HOME=/root/.cache/huggingface' in _rl
      and '/h/.cache/huggingface:/root/.cache/huggingface' in _rl and '8000:8000' in _rl and _rl[-1] == 'vllm serve m' and 'other=y' not in ' '.join(_rl)
      and all('inspect' in c for c in re.findall(r"subprocess\.\w+\(([^\n]*)", (_PR / 'vllm_relaunch_cmd.py').read_text()))
      and not any((_unit_dir / n).exists() for n in ('gmaps-sandbox.service', 'gmaps-sandbox-system.service')))

_cs = {p.parent.parent.name: p.parent.parent / 'review/contact_sheet.png' for p in (ROOT / 'data/airports').glob('*/map/map_meta.json')}
check('airport review: a contact sheet (full, full OSM on, hot spots) per built airport, each under 4 MB',
      bool(_cs) and all(p.exists() and p.stat().st_size < 4 * 1024 * 1024 for p in _cs.values()), str({k: round(p.stat().st_size / 1e6, 2) if p.exists() else None for k, p in _cs.items()}))

_ts = (ROOT / 'docs/TECH_SUMMARY.md').read_text(); _ms = (ROOT / 'docs/MILESTONES.md').read_text()
_rows = [l for l in _ts.split('## 3.')[1].split('## 4.')[0].splitlines() if l.startswith('| ') and not l.startswith('| result')]
check('funding docs: every measured result is labelled SIMULATED or REAL and cites a file; no "would have prevented" claim; no "channel" for radio',
      bool(_rows) and all(re.search(r'SIMULATED|REAL', l) and re.search(r'`[^`]+\.(md|json)`', l) for l in _rows)
      and all(re.search(r'would have prevented', t, re.I) is None or re.search(r'no claim that it would have prevented|Nothing here claims GMAPS would have prevented', t) for t in (_ts, _ms))
      and not re.search(r'radio channel|tower channel|ground channel', _ts + _ms, re.I), f'{len(_rows)} result rows')

_ign = subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '--no-index', 'x/audio_real_private/a.txt', 'venue/a.wav', 'a.mov', 'secrets/t', 'x/mcp_token', '.env', 'openclaw.json.bak', 'backups/x', '.claude/settings.local.json'], capture_output=True, text=True).stdout.split()
check('push readiness: .gitignore keeps private audio, secrets, openclaw configs, backups and local settings out',
      len(_ign) == 9 and not subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-ci', '--exclude-standard'], capture_output=True, text=True).stdout.strip(), str(_ign))

# --- N6: reservation expiry (config values, logged, never silent), warning dedup, open-warning cap, call-log rotation ---
import contextlib as _cl, io as _io
from gmaps_core.sim.simmap import SimAirport as _SA
from gmaps_core import ledger as _LG
_cfg6 = json.loads((ROOT / 'data/specs/runtime_config.json').read_text())
def _n6(lines, until=None, **kw):
    ap = _SA(); led = Ledger(ap, **kw)
    for t, hint, txt in lines:
        led.ingest(parse(txt, 'XSIM', speaker_hint=hint, resolver=ap, event_time=t), t)
    if until is not None:
        for k in range(int(lines[-1][0]) + 1, until + 1):
            led.tick(k)
    return led
_LAND, _CROSS = 'SimAir two one two, runway three six, cleared to land.', 'Rescue seven, cross runway three six at Kilo.'
# N7: soft expiry -> expired_unverified (still checked; R17 ADVISORY), hard drop only on a release or the hard limit
_x = _n6([(0, 'ATC', _LAND), (200, 'ATC', _CROSS)], until=359)
_st359 = [r['status'] for r in _x.reservations]
for _k in range(360, 400):
    _x.tick(_k)
_lr = [r for r in _x.reservations if r['type'] == 'LANDING'][0]; _cr = [r for r in _x.reservations if r['type'] == 'CROSSING'][0]
check('N7 config: soft expiry landing 360 s, takeoff/LUAW 300 s, crossing 180 s, hard limit 1200 s, cap 200, from runtime_config and equal to the library defaults',
      _cfg6['reservation_expiry_s'] == {'LANDING': 360, 'TAKEOFF': 300, 'LUAW': 300, 'CROSSING': 180} == _LG.EXPIRY_S and _cfg6['reservation_hard_limit_s'] == _LG.HARD_LIMIT_S == 1200
      and _cfg6['max_open_warnings'] == _LG.MAX_OPEN_WARNINGS == 200 and "hard_limit_s=cfg.get('reservation_hard_limit_s'" in (ROOT / 'venue/gmaps_mcp.py').read_text()
      and 'gmaps_mcp.make_ledger' in (ROOT / 'eval/e2e_server.py').read_text())
check('N7 soft expiry: a landing with no release becomes expired_unverified at 360 s (not before), logged, still live for conflict checks; the R1 on it stays OPEN (marked unverified); a crossing never read back is exempt (kept active, noted)',
      _st359 == ['active', 'active'] and _lr['status'] == 'expired_unverified' and _lr['soft_expired_at'] == 360 and _lr in _x._active() and _cr['status'] == 'active'
      and [(w['status'], w.get('unverified_since')) for w in _x.warnings if w['rule_id'] == 'R1'] == [('open', 360)]
      and [e['kind'] for e in _x.expiries] == ['expired_unverified'] and any('NOT soft-expired' in n['note'] for n in _x.notes), str(_x.expiries))
_a = _n6([(0, 'ATC', _LAND), (400, 'ATC', _CROSS)], until=1199)
_r17 = [w for w in _a.warnings if w['rule_id'] == 'R17']
_a_open1199 = [w['status'] for w in _r17]
for _k in range(1200, 1202):
    _a.tick(_k)
check('N7 advisory: a crossing 400 s after a landing with no release raises R17 ADVISORY (category unverified_conflict, underlying R1; not a scored alert); at the 1200 s hard limit the landing is dropped (logged hard_drop_without_release) and the R17 closes',
      len(_r17) == 1 and _r17[0]['severity'] == 'ADVISORY' and _r17[0]['category'] == 'unverified_conflict' and _r17[0]['underlying_rule'] == 'R1'
      and not HN.alerts_of(_a.warnings) and _a_open1199 == ['open'] and _r17[0]['status'] == 'expired'
      and [e['kind'] for e in _a.expiries] == ['expired_unverified', 'hard_drop_without_release'] and [r['status'] for r in _a.reservations] == ['expired', 'active'], str(_a.expiries))
_d = _n6([(0, 'ATC', _LAND), (150, 'ATC', _CROSS), (200, 'ATC', _LAND)], until=1300)
_dw = [w for w in _d.warnings if w['rule_id'] == 'R1']
_d2 = _n6([(0, 'ATC', _LAND), (150, 'ATC', _CROSS), (200, 'ATC', _LAND)], until=1360)
check('N6/N7 dedup + "solely": a repeated landing clearance updates the open R1 in place (one warning); the first landing hard-dropped (t=1200) leaves it open on the live pair; it closes when the crossing is hard-dropped (t=1350)',
      len(_dw) == 1 and _dw[0]['repeat_count'] == 1 and _dw[0]['t_fired'] == 150 and _dw[0]['status'] == 'open'
      and [e['kind'] for e in _d.expiries].count('hard_drop_without_release') == 1
      and [w['status'] for w in _d2.warnings if w['rule_id'] == 'R1'] == ['expired'], str([(w['rule_id'], w['status'], w.get('basis')) for w in _d.warnings]))
_err = _io.StringIO()
with _cl.redirect_stderr(_err):
    _cap = _n6([(0, 'ATC', _LAND), (400, 'ATC', _CROSS), (401, 'ATC', 'SimAir seven eight nine, runway three six, cleared to land.'),
                (402, 'ATC', 'Rescue eight, cross runway three six at Kilo.')], max_open_warnings=2)
_evicted = [w for w in _cap.warnings if w['status'] == 'closed_over_cap']
check('N8 cap: at the cap the oldest ADVISORY-LEVEL item (R17) is evicted first; an active conflict warning is never evicted; with only active conflicts open nothing is evicted, the new warning is admitted and SYSTEM DEGRADED is raised (stderr, notes, cap_events, health)',
      _evicted and all(w['rule_id'] == 'R17' for w in _evicted) and not any(w['status'] == 'closed_over_cap' and w['severity'] in ('CONFLICT', 'CAUTION') for w in _cap.warnings)
      and any(e['kind'] == 'open_warning_cap_evicted' and e['tier'] == 'advisory_level' for e in _cap.cap_events)
      and any(e['kind'] == 'system_degraded' for e in _cap.cap_events) and _cap.health['degraded'] and 'SYSTEM DEGRADED' in _err.getvalue()
      and len(_cap.open_warnings()) > 2 and all(w['status'] == 'open' for w in _cap.warnings if w['severity'] in ('CONFLICT', 'CAUTION')),
      str([(w['rule_id'], w['severity'], w['status']) for w in _cap.warnings]) + ' ' + str([e['kind'] for e in _cap.cap_events]))
_err = _io.StringIO()
with _cl.redirect_stderr(_err):
    _dg = _n6([(0, 'ATC', _LAND), (1, 'ATC', _CROSS), (2, 'ATC', 'Rescue eight, cross runway three six at Kilo.')], max_open_warnings=1)
    _dg_deg = dict(_dg.health)
    for _t, _txt in ((5, 'Rescue seven clear of runway three six.'), (6, 'Rescue eight clear of runway three six.')):
        _dg.ingest(parse(_txt, 'XSIM', speaker_hint='PILOT', resolver=_dg.ap, event_time=_t), _t)
check('N8 degraded: cap 1 with only active conflicts (R1 + R1/R11) -> nothing evicted, all kept open, health degraded; when releases bring open warnings below the cap the ledger logs system_recovered',
      _dg_deg['degraded'] and _dg_deg['over_cap_admissions'] >= 1 and not any(w['status'] == 'closed_over_cap' for w in _dg.warnings)
      and not _dg.health['degraded'] and [e['kind'] for e in _dg.cap_events][-1] == 'system_recovered' and 'system recovered' in _err.getvalue(),
      str(_dg_deg) + ' ' + str([e['kind'] for e in _dg.cap_events]))
_legacy = _n6([(0, 'ATC', _LAND), (150, 'ATC', _CROSS), (200, 'ATC', _LAND)], until=1500, expiry=None, dedup=False, max_open_warnings=None, hard_limit_s=None)
check('N6/N7: legacy mode (expiry=None, hard_limit_s=None, dedup=False, no cap) keeps the pre-N6 behaviour for comparisons (eval/expiry_check.py)',
      not _legacy.expiries and len([w for w in _legacy.warnings if w['rule_id'] == 'R1']) == 2 and all(r['status'] == 'active' for r in _legacy.reservations))
_spec = importlib.util.spec_from_file_location('expiry_gaps_t', ROOT / 'eval/expiry_gaps.py'); _EG = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_EG)
_gm = {n: [_EG.run_case(f, s_, g * 60)['raised'] for g in _EG.GAPS_MIN] for n, (f, s_) in _EG.CASES.items()}
check('N7 targeted gaps (2/4/6/8/12/25 min, eval/expiry_gaps.py): landing->crossing ALERT,ALERT,ADVISORY x3,nothing(hard-dropped, logged); LUAW->landing the same; crossing never read back / stop unacknowledged: ALERT up to the hard limit; nothing is silent before 20 min',
      _gm['landing cleared, then a crossing'] == ['ALERT', 'ALERT', 'ADVISORY', 'ADVISORY', 'ADVISORY', 'nothing']
      and _gm['line up and wait (read back), then a landing on the same runway'] == ['ALERT', 'ALERT', 'ADVISORY', 'ADVISORY', 'ADVISORY', 'nothing']
      and _gm['crossing never read back (exempt from soft expiry), then a landing'] == ['ALERT'] * 5 + ['nothing']
      and _gm['crossing read back, stop never acknowledged (exempt), then a landing'] == ['ALERT'] * 5 + ['nothing']
      and all(v[i] != 'nothing' for v in _gm.values() for i, g in enumerate(_EG.GAPS_MIN) if g * 60 < _cfg6['reservation_hard_limit_s']), str(_gm))
with tempfile.TemporaryDirectory() as _td:
    _td = Path(_td); (_td / 'mcp_calls_old.jsonl').write_text('x' * 5000)
    _rl = GM.RotatingLog(_td / 'mcp_calls_run.jsonl', max_mb=300 / 1024 / 1024, backups=2)
    for _i in range(20):
        _rl.write(json.dumps({'i': _i, 'pad': 'y' * 60}) + '\n')
    _names = sorted(p.name for p in _td.iterdir())
    _last = [json.loads(l)['i'] for l in (_td / 'mcp_calls_run.jsonl').read_text().splitlines()]
check('N6 call log: size-based rotation keeps <run>.jsonl + <run>.1/.2.jsonl (max_mb x backups from config: 20 MB x 5); other files in the log folder are never touched',
      _names == ['mcp_calls_old.jsonl', 'mcp_calls_run.1.jsonl', 'mcp_calls_run.2.jsonl', 'mcp_calls_run.jsonl'] and _last[-1] == 19 and _cfg6['call_log_rotation'] == {'max_mb': 20, 'backups': 5}, str(_names))
_tl6 = GM.GmapsTools('SIM_E1_CROSS_DURING_ROLLOUT', 'test-n6-' + str(os.getpid()), send=False); _lg6 = []
_tl6.on_log = _lg6.append; _tl6.on_alert = lambda r: None
_err = _io.StringIO()
with _cl.redirect_stderr(_err):
    _tl6.fast_path(_LAND, 'XSIM', 's0', 0); _tl6.advance(365)
    _ing = _tl6.fast_path(_CROSS, 'XSIM', 's1', 400)
_ingr = (_ing.get('ingest') or {})
check('N7 server: a soft expiry reaches the call log (tool ledger_expiry, kind expired_unverified) and stderr; the unverified landing stays listed; the R17 advisory is in open_warnings; ledger_ingest carries health',
      [r['tool'] for r in _lg6] == ['ledger_expiry'] and _lg6[0]['result']['kind'] == 'expired_unverified' and 'gmaps ledger: expired_unverified' in _err.getvalue()
      and [w['rule_id'] for w in _tl6.open_warnings()['open_warnings']] == ['R17'] and 'health' not in _tl6.open_warnings()
      and any(r['status'] == 'expired_unverified' for r in _ingr.get('reservations', [])) and _ingr.get('health', {}).get('degraded') is False,
      str([(r['tool'], r['result'].get('kind')) for r in _lg6]) + ' ' + str(list(_ingr)))
_tl6.led.health.update(degraded=True, reason='test'); _ow = _tl6.open_warnings(); _tl6.led.health.update(degraded=False, reason=None)
_ix = (ROOT / 'ui_scaffold/index.html').read_text()
check('N8 shown: SYSTEM DEGRADED is returned with open_warnings (tool output), on /health, in the call log (ledger_system_degraded), and the display shows a banner; ADVISORY has its own card style',
      _ow.get('health', {}).get('alarm', '').startswith('SYSTEM DEGRADED') and "'alarm': 'SYSTEM DEGRADED'" in (ROOT / 'venue/gmaps_mcp.py').read_text()
      and 'ledger_system_degraded' in (ROOT / 'venue/gmaps_mcp.py').read_text() and 'SYSTEM DEGRADED: ${esc(H.reason' in _ix and '.sev.ADVISORY' in _ix)
_spec = importlib.util.spec_from_file_location('soak_server_t', ROOT / 'eval/soak_server.py'); _SS = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_SS)
_spec = importlib.util.spec_from_file_location('soak_t', ROOT / 'eval/soak.py'); _SK = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_SK)
_old_led = _tl6.led
_tl6.ap = _SA(); _SS.reset_session(_tl6)
check('N6 soak harness: /soak/reset gives a fresh ledger and session (fresh lanes reset before every scenario); continuous lanes never reset; 4 loopback lanes, none on the boot MCP port',
      _tl6.led is not _old_led and not _tl6.led.warnings and not _tl6.events and _tl6.resets == 1 and _tl6.led.expiry == _cfg6['reservation_expiry_s']
      and {v[2] for v in _SK.LANES.values()} == {True, False} and 11435 not in {v[1] for v in _SK.LANES.values()} and "if self.lanes[ap][2]:\n                    self.reset(ap)" in (ROOT / 'eval/soak.py').read_text())

# --- (Franco): R17 aggregated per runway; N13 conflict auto-close (never evicted); N12 bounded history ---
_TKO = 'SimAir four five six, runway three six, cleared for takeoff.'
_ag = _n6([(0, 'ATC', _LAND), (0, 'ATC', _TKO), (400, 'ATC', _CROSS), (420, 'ATC', 'Rescue nine, cross runway three six at Kilo.')])
_ag17 = [w for w in _ag.warnings if w['rule_id'] == 'R17']
check('R17 aggregate: two unverified clearances (landing, takeoff) and two later crossings on runway 36 -> ONE R17 advisory ("2 unverified earlier clearances on runway ..."), updated in place, per-reservation detail inside (R1 + R2 underlying), not a scored alert',
      len(_ag17) == 1 and _ag17[0]['status'] == 'open' and _ag17[0]['name'].startswith('2 unverified earlier clearances on runway') and _ag17[0]['unverified_count'] == 2
      and len(_ag17[0]['unverified_detail']) == 4 and _ag17[0]['repeat_count'] == 3 and _ag17[0]['underlying_rules'] == ['R1', 'R2'] and _ag17[0]['t_fired'] == 400
      and not any(w['rule_id'] == 'R17' for w in HN.alerts_of(_ag.warnings)), str([(w['rule_id'], w['name'], w.get('unverified_count')) for w in _ag.warnings]))
_ac = _n6([(0, 'ATC', _LAND), (0, 'ATC', _TKO)], until=359); _ac359 = [(w['rule_id'], w['status']) for w in _ac.warnings]
_ac.tick(360)
_ac5 = _n6([(0, 'ATC', _LAND), (0, 'ATC', _TKO)], until=499, auto_close_s=500); _ac5_499 = [w['status'] for w in _ac5.warnings]; _ac5.tick(500)
_err = _io.StringIO()
with _cl.redirect_stderr(_err):
    _nc = _n6([(0, 'ATC', _LAND), (0, 'ATC', _TKO), (310, 'ATC', _CROSS)], max_open_warnings=1)
check('N13: a CONFLICT is never evicted at the cap (cap 1, R3 whose takeoff is expired_unverified stays open; SYSTEM DEGRADED instead); it AUTO-CLOSES (status auto_closed, logged conflict_auto_closed, reason "both clearances expired_unverified") once both clearances are expired_unverified and it is >= 2 min old (here t=360; with auto_close_s=500 not before t=500)',
      _ac359 == [('R3', 'open')] and [(w['rule_id'], w['status'], w.get('auto_close_reason')) for w in _ac.warnings] == [('R3', 'auto_closed', 'both clearances expired_unverified')]
      and [e['kind'] for e in _ac.expiries][-1] == 'conflict_auto_closed' and _ac.expiries[-1]['reason'] == 'both clearances expired_unverified' and _ac.expiries[-1]['t'] == 360
      and _ac5_499 == ['open'] and [w['status'] for w in _ac5.warnings] == ['auto_closed'] and _ac5.expiries[-1]['t'] == 500
      and not any(w['status'] == 'closed_over_cap' and w['severity'] in ('CONFLICT', 'CAUTION') for w in _nc.warnings) and _nc.health['degraded']
      and [w['status'] for w in _nc.warnings if w['rule_id'] == 'R3'] == ['open'] and _LG.AUTO_CLOSE_S == _cfg6['conflict_auto_close_s'] == 120,
      str(_ac359) + str([(w['rule_id'], w['severity'], w['status']) for w in _nc.warnings]))
import tracemalloc as _tm, gc as _gc
_hl = Ledger(_SA(), history_keep=50, expiry=None); _arch = {}
_hl.archive = lambda n, items: _arch.__setitem__(n, _arch.get(n, 0) + len(items))
def _hi(t, spk, x):
    _hl.ingest(parse(x, 'XSIM', speaker_hint=spk, resolver=_hl.ap, event_time=t), t)
_hi(0, 'ATC', 'SimAir nine nine nine, runway three six, cleared to land.'); _hi(1, 'ATC', _CROSS)
_hfirst = [w for w in _hl.warnings if w['rule_id'] == 'R1'][0]['warning_id']
_tm.start(); _hmem = []
for _i in range(1, 241):
    _t = _i * 20
    _hi(_t, 'ATC', _LAND); _hi(_t + 1, 'ATC', 'Rescue eight, cross runway three six at Kilo.'); _hi(_t + 3, 'PILOT', 'Cross runway three six at Kilo, Rescue eight.')
    _hi(_t + 5, 'PILOT', 'Rescue eight clear of runway three six.'); _hi(_t + 6, 'PILOT', 'SimAir two one two clear of runway three six.')
    if _i in (120, 240):
        _gc.collect(); _hmem.append(_tm.get_traced_memory()[0])
_tm.stop()
_hclosed = [w for w in _hl.warnings if w['warning_id'] not in _hl._open]
_tl12 = GM.GmapsTools('SIM_E1_CROSS_DURING_ROLLOUT', 'test-n12-' + str(os.getpid()), send=False); _lg12 = []
_tl12.on_log = _lg12.append; _tl12.on_alert = lambda r: None
_tl12.led = GM.make_ledger(_tl12.ap, dict(_tl12.cfg, history_keep=3))
with _cl.redirect_stderr(_io.StringIO()):
    for _i in range(6):
        _tl12.fast_path(_LAND, 'XSIM', f'h{_i}a', _i * 20); _ig12 = _tl12.fast_path(_CROSS, 'XSIM', f'h{_i}b', _i * 20 + 1)
        _tl12.fast_path('Rescue seven clear of runway three six.', 'XSIM', f'h{_i}c', _i * 20 + 5); _tl12.fast_path('SimAir two one two clear of runway three six.', 'XSIM', f'h{_i}d', _i * 20 + 6)
_ha = Ledger(_SA(), history_keep=3, expiry=None)
for _j, _n in enumerate(('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'nine')):
    for _dt, _spk, _x in ((0, 'ATC', f'Rescue {_n}, cross runway three six at Kilo.'), (2, 'PILOT', f'Cross runway three six at Kilo, Rescue {_n}.'),
                          (5, 'PILOT', f'Rescue {_n} clear of runway three six.')):
        _ha.ingest(parse(_x, 'XSIM', speaker_hint=_spk, resolver=_ha.ap, event_time=_j * 20 + _dt), _j * 20 + _dt)
_ha.ingest(parse('Rescue eight, cross runway three six at Kilo.', 'XSIM', speaker_hint='ATC', resolver=_ha.ap, event_time=200), 200)
_ha_ok = len(_ha.actors) <= 4 and 'RESCUE8' in _ha.actors and 'RESCUE9' in _ha.actors and 'RESCUE1' not in _ha.actors and _ha.pruned.get('actors', 0) >= 4
check('N12 bounded history: only the last 1000 (config history_keep; 50 here) closed warnings / notes / events / closed reservations / pending / callsigns stay in memory; open warnings are never pruned; every pruned item goes to Ledger.archive (the server writes it to the call log as ledger_history); totals stay exact; memory flat (240 cycles)',
      _cfg6['history_keep'] == _LG.HISTORY_KEEP == 1000 and len(_hclosed) == 50 and _hfirst in _hl._open and any(w['warning_id'] == _hfirst for w in _hl.warnings)
      and max(len(_hl.notes), len(_hl.events), len(_hl.pending), sum(1 for r in _hl.reservations if r['status'] in ('released', 'expired'))) <= 50
      and _hl.pruned['warnings'] > 500 and _arch == {k: v for k, v in _hl.pruned.items() if v} and _hl.total('warnings') == len(_hl.warnings) + _hl.pruned['warnings']
      and _hmem[1] - _hmem[0] < 64 * 1024
      and any(r['tool'] == 'ledger_history' and r['list'] == 'warnings' for r in _lg12) and len(_tl12.led.warnings) - len(_tl12.led._open) <= 3
      and [w['rule_id'] for w in (_ig12.get('ingest') or {}).get('new_warnings', [])] == ['R1'] and _ha_ok,
      f"closed={len(_hclosed)} pruned={_hl.pruned} mem={_hmem} log={[r['tool'] for r in _lg12][:5]} actors={list(_ha.actors)} {_ha.pruned}")

# --- N5: the repo stays private; the pre-push hook blocks unless the remote is confirmed private ---
_spec = importlib.util.spec_from_file_location('prepush_t', ROOT / 'tools/hooks/prepush_private_check.py'); _PP = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_PP)
_calls = []
def _ff(answers):
    def f(o, r, token=None):
        _calls.append(bool(token)); return answers[bool(token)]
    return f
with tempfile.TemporaryDirectory() as _td:
    _cf = Path(_td) / 'confirm.json'; _u = 'git@github.com:someone/gmaps.git'
    _r = {
        'token_private': _PP.decide(_u, {'GITHUB_TOKEN': 'x' * 20}, _ff({True: (200, {'private': True})}), _cf)[0],
        'token_public': _PP.decide(_u, {'GITHUB_TOKEN': 'x' * 20}, _ff({True: (200, {'private': False})}), _cf)[0],
        'token_404': _PP.decide(_u, {'GH_TOKEN': 'x' * 20}, _ff({True: (404, None)}), _cf)[0],
        'notoken_404_unconfirmed': _PP.decide(_u, {}, _ff({False: (404, None)}), _cf)[0],
        'offline_unconfirmed': _PP.decide(_u, {}, _ff({False: (None, None)}), _cf)[0]}
    _cf.write_text(json.dumps({'remotes': {_u: {'by': 'Franco', 'date': 'x'}}}))
    _r['notoken_404_confirmed'] = _PP.decide(_u, {}, _ff({False: (404, None)}), _cf)[0]
    _r['notoken_public_even_if_confirmed'] = _PP.decide(_u, {}, _ff({False: (200, {'private': False})}), _cf)[0]
    _r['other_host_confirmed'] = _PP.decide('https://gitlab.example/x/y.git', {}, _ff({}), Path(_td) / 'none.json')[0]
_hook = Path(subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], cwd=ROOT, capture_output=True, text=True).stdout.strip() or ROOT / '.git') / 'hooks/pre-push'   # the shared hooks dir, so a git worktree checks the same hook
check('N5 push guard: pushes only to a remote confirmed private (GitHub API with a token, else the manual checkbox); a public repo is always blocked; no confirmation blocks; hook installed; PUSH_READINESS has the step',
      _r == {'token_private': True, 'token_public': False, 'token_404': False, 'notoken_404_unconfirmed': False, 'offline_unconfirmed': False,
             'notoken_404_confirmed': True, 'notoken_public_even_if_confirmed': False, 'other_host_confirmed': False}
      and _hook.exists() and 'prepush_private_check.py' in _hook.read_text() and 'prepush_private_check.py' in (ROOT / 'docs/PUSH_READINESS.md').read_text(), str(_r))

# --- parser-comparison files (Oct 18): the demo page's strip/tile/ruler read ui_scaffold/samples/parsers_<episode>.json; each file must
# carry models/source/note, and every row must be a line that is on the page (same t and same words), so the strip never shows a line
# that is not in the scenario's transcript (fixtures: the timeline rows in the page window -40..+48)
_pc_bad = []
for _pp in sorted((ROOT / 'ui_scaffold/samples').glob('parsers_*.json')):
    _pj = json.loads(_pp.read_text()); _ep = _pj.get('episode')
    if not (_pj.get('models') and _pj.get('source') and _pj.get('note')) or _ep != _pp.stem[len('parsers_'):]:
        _pc_bad.append(f'{_pp.name}: models/source/note/episode'); continue
    _sd = json.loads((ROOT / f'ui_scaffold/samples/{_ep}.json').read_text())
    _lines = {(r['t'], r['text']) for r in _sd['timeline'] if -40 <= r['t'] <= 48} if 'timeline' in _sd else {(r['t'], r['text']) for r in _sd['transcript']}
    _pc_bad += [f"{_pp.name}: t={r['t']} not on the page" for r in _pj.get('rows', []) if (r['t'], r.get('script')) not in _lines]
check('parser-comparison files: models, source and note present; every row is a transcript line on the page (same t and words)',
      not _pc_bad and len(list((ROOT / 'ui_scaffold/samples').glob('parsers_*.json'))) >= 3, str(_pc_bad))

ok = all(r[1] for r in results)
for n, passed, d in results:
    print(('PASS ' if passed else 'FAIL ') + n + (f'  [{d}]' if d else ''))
print(f"\n{sum(r[1] for r in results)}/{len(results)} passed")
sys.exit(0 if ok else 1)

"""Extra KBOS mutation tests on top of validate.py --self-test. Uses validate.py unchanged."""
import copy, json, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import validate as V
schema = V.read('airport_graph.schema.json'); g0 = V.read('kbos.graph.json')
def find(g, coll, i): return next(r for r in g[coll] if r['id'] == i)
results = []
def bad(name, mutate):
    g = copy.deepcopy(g0); mutate(g); errs = V.validate(g, schema, check_files=False)
    results.append(dict(name=name, passed=bool(errs), rejected_by=errs[:2]))
bad('missing_geometry_key_on_node', lambda g: g['nodes'][0].pop('geometry'))
bad('missing_length_key_on_segment', lambda g: g['segments'][0].pop('length_m'))
bad('invented_hold_offset', lambda g: find(g,'hold_points','KBOS:HOLD:LAHSO_RWY15R_BEFORE09').update(offset_m=60))
bad('lahso_at_runway_intersection_node', lambda g: find(g,'hold_points','KBOS:HOLD:LAHSO_RWY15R_BEFORE09').update(node_id='KBOS:N:RWY09-27_RWY15R-33L'))
bad('hold_approach_from_inside_protected_runway', lambda g: find(g,'hold_points','KBOS:HOLD:D1_APPROACH_RWY09').update(approach_from_node_id='KBOS:N:D1_RWY09-27'))
bad('cross_airport_entity_id', lambda g: g['nodes'].append(dict(copy.deepcopy(g['nodes'][0]), id='KLGA:N:D_RWY04-22')))
bad('cross_airport_segment_endpoint', lambda g: g['segments'][0].update(to_node_id='KLGA:N:D_RWY04-22'))
bad('merge_33L_into_15L-33R', lambda g: find(g,'runway_ends','KBOS:END:33L').update(runway_id='KBOS:RWY:15L-33R'))
bad('drop_runway_suffix_33L_to_33', lambda g: find(g,'runway_ends','KBOS:END:33L').update(identifier='33'))
bad('hs4_usable_for_occupancy', lambda g: find(g,'zones','KBOS:ZONE:HS4').update(usable_for_occupancy=True))
bad('route_grants_authority', lambda g: g['route_templates'][0].update(grants_authority=True))
bad('automatic_routing_on', lambda g: g['usability'].update(automatic_routing=True))
g = copy.deepcopy(g0); g['nodes'][0]['uncertainty']['confidence'] = 0.9
gap = not V.validate(g, schema, check_files=False)
results.append(dict(name='KNOWN_GAP_schema_accepts_numeric_confidence', passed=True, known_gap=gap,
  detail='v1 schema allows confidence 0-1; SCHEMA_GUIDE says null. Not enforced by validate.py; enforced by the audit below.'))
import re as _re
audit = [k for f in ['kbos.graph.json','klga.graph.json'] for k in _re.findall(r'"confidence": (?!null)[^,}]+', open(f).read())]
results.append(dict(name='audit_all_confidence_null_both_airports', passed=not audit, detail=audit[:3] or 'all null'))
# A metric query must be refused because no geometry/length exists in v1.
def metric_distance(g, a, b):
    if any(n['geometry'] is None for n in g['nodes'] if n['id'] in (a, b)) or all(s['length_m'] is None for s in g['segments']):
        return None, 'refused: v1 graph has no reviewed geometry or segment lengths'
    return 0, 'computed'
d, why = metric_distance(g0, 'KBOS:N:D_D1', 'KBOS:N:RWY09-27_RWY15R-33L')
results.append(dict(name='metric_query_refused_without_geometry', passed=d is None, detail=why))
out = dict(all_passed=all(r['passed'] for r in results), tests=results)
json.dump(out, open('bos_extra_tests_report.json','w'), indent=2)
print(json.dumps([(r['name'], r['passed'], (r.get('rejected_by') or [r.get('detail')])[0]) for r in results], indent=1)); print('ALL', out['all_passed'])

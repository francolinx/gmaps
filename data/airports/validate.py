"""Validate GMAPS review artifacts. Requires jsonschema>=4.18; no network calls."""
import argparse
import copy
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
COLLECTIONS = ['surfaces', 'runway_ends', 'nodes', 'segments', 'hold_points', 'zones', 'route_templates']

def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))

def validate(g, schema, check_files=True):
    errors = [f"schema:{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(g)]
    if errors:
        return errors
    def require(condition, message):
        if not condition:
            errors.append(message)
    records = [r for key in COLLECTIONS for r in g[key]]
    ids = [r['id'] for r in records] + [r['id'] for r in g['issues']]
    require(len(ids) == len(set(ids)), 'duplicate entity ID')
    require(all(i.startswith(g['airport']['icao'] + ':') for i in ids), 'wrong airport namespace')
    sources = {s['id']: s for s in g['sources']}
    require(len(sources) == len(g['sources']), 'duplicate source ID')
    for s in sources.values():
        if s['effective_from'] and s['effective_to']:
            require(s['effective_from'] < s['effective_to'], 'source date range reversed')
        if s['date_status'] == 'within_printed_range':
            require(bool(s['effective_from'] and s['effective_to']), 'missing effective dates')
            if s['effective_from'] and s['effective_to']:
                require(s['effective_from'] <= s['retrieved_on'] <= s['effective_to'], 'retrieval date outside claimed printed range')
        if check_files and s['local_path']:
            path = (ROOT / s['local_path']).resolve()
            require(path.is_relative_to(ROOT), 'source path escapes artifact folder')
            if not path.is_relative_to(ROOT):
                continue
            require(path.is_file(), 'missing source file: ' + s['local_path'])
            if path.is_file():
                require(hashlib.sha256(path.read_bytes()).hexdigest() == s['sha256'], 'source hash mismatch: ' + s['id'])
    for r in records:
        for e in r['evidence']:
            require(e['source_id'] in sources, 'dangling evidence source: ' + r['id'])
            require(bool(e['locator'].strip()), 'empty evidence locator')
        if r['review']['status'] == 'accepted':
            require(bool(r['review']['reviewer'] and r['review']['reviewed_at']), 'accepted without named reviewer and time')
    surfaces = {s['id']: s for s in g['surfaces']}
    ends = {e['id']: e for e in g['runway_ends']}
    nodes = {n['id']: n for n in g['nodes']}
    segments = {s['id']: s for s in g['segments']}
    holds = {h['id']: h for h in g['hold_points']}
    for e in ends.values():
        r = surfaces.get(e['runway_id'])
        reciprocal = ends.get(e['reciprocal_end_id'])
        require(bool(r and r['kind'] == 'runway'), 'end references non-runway')
        require(bool(reciprocal), 'missing reciprocal runway end')
        if reciprocal:
            require(reciprocal['reciprocal_end_id'] == e['id'], 'non-mutual reciprocal')
            require(reciprocal['runway_id'] == e['runway_id'], 'reciprocal physical runway mismatch')
            n = int(re.match(r'\d+', e['identifier'])[0])
            other = int(re.match(r'\d+', reciprocal['identifier'])[0])
            suffix = re.sub(r'\d', '', e['identifier'])
            other_suffix = re.sub(r'\d', '', reciprocal['identifier'])
            require(((n + 17) % 36) + 1 == other, 'bad reciprocal runway number')
            require({'L':'R','R':'L','C':'C','':''}[suffix] == other_suffix, 'bad reciprocal runway suffix')
    for s in surfaces.values():
        if s['kind'] == 'runway':
            found = [e['identifier'] for e in ends.values() if e['runway_id'] == s['id']]
            require(sorted(found) == sorted(s['identifier'].split('/')), 'runway must have exactly its two named ends')
    for n in nodes.values():
        require(all(s in surfaces and surfaces[s]['kind'] != 'named_area' for s in n['surface_ids']), 'node has unknown or non-pavement surface')
    for s in segments.values():
        require(s['from_node_id'] != s['to_node_id'], 'segment self-loop')
        require(s['surface_id'] in surfaces, 'unknown segment surface')
        for endpoint in ['from_node_id', 'to_node_id']:
            n = nodes.get(s[endpoint])
            require(bool(n), 'dangling segment endpoint')
            if n:
                require(s['surface_id'] in n['surface_ids'], 'segment surface absent from endpoint')
    for h in holds.values():
        require(h['protected_surface_id'] in surfaces, 'unknown hold protected surface')
        require(h['approach_surface_id'] in surfaces, 'unknown hold approach surface')
        if h['approach_segment_id']:
            s = segments.get(h['approach_segment_id'])
            require(bool(s), 'unknown hold approach segment')
            if s:
                require(s['surface_id'] == h['approach_surface_id'], 'hold approach surface mismatch')
                require(h['approach_from_node_id'] in [s['from_node_id'], s['to_node_id']], 'hold side is not an approach endpoint')
                if h['approach_from_node_id'] in nodes:
                    require(h['protected_surface_id'] not in nodes[h['approach_from_node_id']]['surface_ids'], 'hold approach incorrectly starts inside protected runway')
    for z in g['zones']:
        require(all(s in surfaces for s in z['surface_ids']), 'unknown zone surface')
        require(all(n in nodes for n in z['node_ids']), 'unknown zone node')
    for r in g['route_templates']:
        require(all(s in surfaces for s in r['surface_sequence']), 'unknown route surface')
        require(all(h in holds for h in r['hold_point_ids']), 'unknown route hold')
        if r['route_status'] == 'draft_connected_fragment':
            require(len(r['node_sequence']) == len(r['segment_sequence']) + 1, 'route sequence length mismatch')
            walk_surfaces = []
            for i, seg_id in enumerate(r['segment_sequence']):
                s = segments.get(seg_id)
                require(bool(s), 'unknown route segment')
                if s and i + 1 < len(r['node_sequence']):
                    require(set(r['node_sequence'][i:i+2]) == {s['from_node_id'], s['to_node_id']}, 'disconnected route walk')
                    if not walk_surfaces or walk_surfaces[-1] != s['surface_id']:
                        walk_surfaces.append(s['surface_id'])
            require(walk_surfaces == r['surface_sequence'], 'route surface order mismatch')
        else:
            require(not r['node_sequence'] and not r['segment_sequence'], 'unresolved route must not pretend to have an executable walk')
    for i in g['issues']:
        require(all(e in ids for e in i['entity_ids']), 'unknown issue entity')
    return errors

def resolve(airport, kind, text, aliases):
    token = ' '.join(text.lower().split())
    entries = [e for e in aliases['entries'] if e['airport'] == airport and e['kind'] == kind]
    exact = sorted({e['entity_id'] for e in entries if token in e['aliases']})
    if exact or kind != 'runway_end':
        return exact
    # A missing suffix yields candidates, never a silent selection.
    return sorted({e['entity_id'] for e in entries for a in e['aliases'] if any(a == token + ' ' + suffix for suffix in ['left','right','center'])})

def self_test(schema, graphs):
    tests = []
    def bad(name, mutate):
        g = copy.deepcopy(graphs[0]); mutate(g)
        errors = validate(g, schema, check_files=False)
        tests.append(dict(name=name,passed=bool(errors),rejected_by=errors[:2]))
    bad('invented_geometry', lambda g: g['nodes'][0].update(geometry={'x':12,'y':13}))
    bad('guessed_segment_length', lambda g: g['segments'][0].update(length_m=50))
    bad('prediction_enabled_on_draft', lambda g: g['usability'].update(metric_prediction=True))
    bad('routing_enabled_on_draft', lambda g: g['segments'][0].update(routing_enabled=True))
    bad('duplicate_entity_id', lambda g: g['nodes'].append(copy.deepcopy(g['nodes'][0])))
    bad('cross_airport_endpoint', lambda g: g['segments'][0].update(to_node_id='KLGA:N:missing'))
    bad('disconnected_route', lambda g: g['route_templates'][0]['node_sequence'].__setitem__(1,g['nodes'][-1]['id']))
    bad('unknown_source', lambda g: g['nodes'][0]['evidence'][0].update(source_id='not-present'))
    bad('wrong_reciprocal_suffix', lambda g: g['runway_ends'][1].update(identifier='22L'))
    bad('reversed_effective_dates', lambda g: g['sources'][0].update(effective_from='2026-11-01'))
    bad('hold_as_runway_center_node', lambda g: g['hold_points'][0].update(node_id=g['nodes'][0]['id']))
    bad('invented_review_acceptance', lambda g: g['nodes'][0]['review'].update(status='accepted'))
    aliases = read('identifier_aliases.json')
    known = {r['id'] for g in graphs for k in ['surfaces','runway_ends'] for r in g[k]}
    tests.append(dict(name='alias_targets_exist', passed=all(e['entity_id'] in known for e in aliases['entries'])))
    for c in read('resolution_examples.json')['cases']:
        if 'expected_kind' in c:
            got = resolve(c['airport'],c['expected_kind'],c['input'],aliases)
            tests.append(dict(name=c['id'],passed=sorted(c['expected_candidates']) == got,actual=got))
    return tests

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--self-test',action='store_true');args=parser.parse_args()
    schema=read('airport_graph.schema.json'); Draft202012Validator.check_schema(schema)
    graphs=[read('kbos.graph.json'),read('klga.graph.json')]
    results=[dict(airport=g['airport']['icao'],errors=validate(g,schema),counts={k:len(g[k]) for k in COLLECTIONS},expert_review='pending',metric_prediction_ready=False) for g in graphs]
    tests=self_test(schema,graphs) if args.self_test else []
    report=dict(checked_at=datetime.now(timezone.utc).isoformat(),schema_valid=True,airports=results,self_tests=tests,all_checks_passed=all(not r['errors'] for r in results) and all(t['passed'] for t in tests),meaning='Artifact integrity and conservative draft gates only; not expert verification of airport topology or operational suitability.')
    # rewrite the tracked report only when its content changes; a timestamp-only refresh is not a change
    out=ROOT/'validation_report.json'
    try: prev=json.loads(out.read_text(encoding='utf-8'))
    except (OSError,ValueError): prev=None
    if not isinstance(prev,dict) or {k:v for k,v in prev.items() if k!='checked_at'} != {k:v for k,v in report.items() if k!='checked_at'}:
        out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report['all_checks_passed'] else 1)

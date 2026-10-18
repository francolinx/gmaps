"""KLGA-only wrapper around the supplied validate.py (unchanged).
kbos.graph.json and sources/bos_airport_diagram.pdf were not supplied, so KBOS checks are skipped and reported."""
import json
from datetime import datetime, timezone
from jsonschema import Draft202012Validator
import validate as V

schema = V.read('airport_graph.schema.json'); Draft202012Validator.check_schema(schema)
lga = V.read('klga.graph.json')
errors = V.validate(lga, schema, check_files=True)
# Supplied mutation tests mutate graphs[0]; run them against KLGA.
tests = [t for t in V.self_test(schema, [lga]) if t['name'] != 'alias_targets_exist']
aliases = V.read('identifier_aliases.json')
known = {r['id'] for k in ['surfaces', 'runway_ends'] for r in lga[k]}
lga_alias = [e for e in aliases['entries'] if e['airport'] == 'KLGA']
tests.append(dict(name='alias_targets_exist_KLGA_only', passed=all(e['entity_id'] in known for e in lga_alias),
                  checked=len(lga_alias)))
not_run = []
for t in list(tests):
    if t['name'].startswith('bos_') or t['name'] == 'cross_airport_D1':
        not_run.append(t['name'] + ' (KBOS fixture; resolver ran on alias file only, KBOS graph absent)')
report = dict(
    checked_at=datetime.now(timezone.utc).isoformat(),
    validator='validate.py (unchanged) via validate_klga_only.py',
    airport='KLGA', dataset_version=lga.get('dataset_version'),
    errors=errors, counts={k: len(lga[k]) for k in V.COLLECTIONS + ['sources', 'issues']},
    self_tests=tests,
    not_run=['KBOS graph validation (kbos.graph.json not supplied)',
             'KBOS source hash (sources/bos_airport_diagram.pdf not supplied)',
             'alias_targets_exist across KBOS entries (KBOS graph absent)'],
    resolver_only_notes=not_run,
    all_klga_checks_passed=not errors and all(t['passed'] for t in tests),
    meaning='Artifact integrity and conservative draft gates only; not expert verification of airport topology or operational suitability.')
open('validation_report.json', 'w').write(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))

"""LaGuardia animated view (ui_scaffold/lga_scene.html + ui_scaffold/lga/, built by tools/build_lga_scene.py)."""
import json, re, subprocess, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LGA = ROOT / 'ui_scaffold' / 'lga'


class LgaSceneTests(unittest.TestCase):
    def test_build_is_current(self):
        before = {p.name: p.read_bytes() for p in LGA.glob('*.json')}
        subprocess.run([sys.executable, str(ROOT / 'tools/build_lga_scene.py')], check=True, capture_output=True)
        after = {p.name: p.read_bytes() for p in LGA.glob('*.json')}
        self.assertEqual(before, after, 'ui_scaffold/lga/ is stale: run python3 tools/build_lga_scene.py')

    def test_ledger_on_lga_ids_matches_sim1_and_holds_are_real(self):
        A = json.loads((LGA / 'airport.json').read_text())
        self.assertTrue(all(h['osm'] for h in A['placement']['holds'].values()), A['placement']['holds'])
        self.assertEqual(A['placement']['taxiway'], 'G')
        for s in json.loads((LGA / 'index.json').read_text()):
            self.assertTrue(s['ledger_same_as_sim1'], s['id'])
            d = json.loads((LGA / f"{s['id']}.json").read_text())
            self.assertTrue(d['SIMULATED'])
            self.assertTrue(all('Kilo' not in x['text'] and 'three six' not in x['text'] for x in d['transcript']))
            x0, y0, x1, y1 = A['bounds']
            for tr in d['tracks'].values():
                for _, x, y, *_ in tr['samples']:
                    self.assertTrue(x0 - 300 <= x <= x1 + 300 and y0 - 300 <= y <= y1 + 300, (s['id'], x, y))

    def test_page_labels_and_claims(self):
        h = (ROOT / 'ui_scaffold/lga_scene.html').read_text()
        self.assertIn('SIMULATED · FICTIONAL TRAFFIC · REAL LAYOUT', h)
        self.assertIn('A.attribution', h)                       # OSM attribution shown in the footer
        self.assertIn('not "no conflict"', h)
        self.assertNotRegex(h, r'(?i)would have prevented|\bchannel\b')
        self.assertNotRegex(h, r'eval/(truth|labels)|https?://(?!www\.openstreetmap)')   # no external resources


if __name__ == '__main__':
    unittest.main(verbosity=2)

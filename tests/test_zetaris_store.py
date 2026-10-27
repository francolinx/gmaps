"""Data store round trip (Open Agent Hackathon 2026).
live:  ZetarisStore. Status: NOT INTEGRATED (no verified client, docs unreachable from the build session, no credentials):
       SKIPS with the store's own reason. No Zetaris fixture exists, so nothing here claims Zetaris works.
local: LocalStore round trip (what the agent runs with GMAPS_DATA=local, the default)."""
import shutil, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.data.zetaris_store import LocalStore, ZetarisStore, NotIntegrated


class StoreTests(unittest.TestCase):
    def test_live_zetaris_roundtrip(self):
        try:
            ZetarisStore()
        except NotIntegrated as e:
            print(f'\nSKIP live Zetaris: {e}', file=sys.stderr)
            self.skipTest(str(e))
        self.fail('ZetarisStore constructed but no live round trip is written yet')

    def test_local_roundtrip(self):
        d = Path(tempfile.mkdtemp())
        try:
            s = LocalStore(ledger_dir=d)
            rec = {'run_id': 'rt', 'seq': 1, 't': 28, 'record_type': 'warning', 'record_id': 'w0001', 'callsign': 'SIM212',
                   'runway': 'XSIM:RWY:18-36', 'status': 'open', 'body_json': '{}'}
            s.append_ledger('rt', [rec])
            self.assertEqual(s.read_ledger('rt'), [rec])
            self.assertEqual(s.transmissions('SIM_E1_CROSS_DURING_ROLLOUT')[3]['text'], 'Rescue seven, cross runway three six at Kilo.')
        finally:
            shutil.rmtree(d)


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Runway memory engine tests (Open Agent Hackathon 2026).

live:     Meterless H-MEM through gmaps_core.engines.meterless_hmem. Status: NOT INTEGRATED (no SDK on PyPI, docs unreachable from
          the build session, no key supplied), so this test SKIPS and prints the adapter's own reason. There is no recorded
          Meterless response in the repo, so there is no Meterless fixture test either: nothing here claims Meterless works.
fallback: LocalRunwayMemory, the engine the agent actually runs (trace lines 'engine': 'local.runway_memory').
"""
import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.engines.runway_memory import LocalRunwayMemory
from gmaps_core.engines.meterless_hmem import MeterlessHMEM, NotIntegrated


class MeterlessEngineTests(unittest.TestCase):
    def test_live_meterless_hmem(self):
        try:
            MeterlessHMEM()
        except NotIntegrated as e:
            print(f'\nSKIP live Meterless H-MEM: {e}', file=sys.stderr)
            self.skipTest(str(e))
        self.fail('MeterlessHMEM constructed but no live assertion is written yet')

    def test_local_fallback_runway_memory(self):
        m = LocalRunwayMemory()
        m.put('XSIM:RWY:18-36', 'r1', {'kind': 'LANDING', 'callsign': 'SIM212', 'status': 'active', 't': 0})
        m.put('XSIM:RWY:18-36', 'hold:e2', {'kind': 'HOLD_SHORT', 'callsign': 'RESCUE7', 'status': 'open', 't': 18})
        m.put('XSIM:RWY:18-36', 'r3', {'kind': 'CROSSING', 'callsign': 'RESCUE7', 'status': 'released', 't': 5})
        self.assertEqual([i['item_id'] for i in m.open_items('XSIM:RWY:18-36')], ['r1', 'hold:e2'])
        m.close('XSIM:RWY:18-36', 'r1', 40)
        self.assertEqual([i['item_id'] for i in m.open_items('XSIM:RWY:18-36')], ['hold:e2'])
        self.assertEqual(m.open_items('XSIM:RWY:09-27'), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)

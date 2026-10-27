"""run.py entry point: structured input -> structured output, judge-supplied radio text works, bad input is a clear 400."""
import os, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['SAMPLE_MODE'] = 'true'
import run as R


class RunEntryTests(unittest.TestCase):
    def test_custom_transmissions_raise_r2(self):
        code, out = R.run({'transmissions': [{'t': 0, 'speaker': 'ATC', 'text': 'SimAir four five one, runway one eight, cleared for takeoff.'},
                                             {'t': 12, 'speaker': 'ATC', 'text': 'Truck two, cross runway one eight at Kilo.'}]})
        self.assertEqual(code, 200)
        self.assertEqual([a['headline'].split()[0] for a in out['alerts']], ['R2'])
        self.assertTrue((ROOT / out['trace']).exists())

    def test_different_inputs_give_different_outputs(self):
        a = R.run({'scenario': 'SIM_E1_CROSS_DURING_ROLLOUT'})[1]
        b = R.run({'scenario': 'SIM_E6_HOLD_OK'})[1]
        self.assertNotEqual([x['headline'] for x in a['alerts']], [x['headline'] for x in b['alerts']])

    def test_bad_input_is_400(self):
        self.assertEqual(R.run({'transmissions': 'bad'})[0], 400)
        self.assertEqual(R.run({'scenario': 'NOPE'})[0], 400)
        self.assertEqual(R.run({})[0], 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)

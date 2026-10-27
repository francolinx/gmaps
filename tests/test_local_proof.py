"""tools/local_proof.py: the connection auditor sees every connect() and the verdict flags a non-loopback target."""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LocalProofTests(unittest.TestCase):
    def run_proof(self, code):
        d = Path(tempfile.mkdtemp())
        try:
            (d / 'probe.py').write_text(code)
            before = set((ROOT / 'runs/proof').glob('local_*.json')) if (ROOT / 'runs/proof').exists() else set()
            subprocess.run([sys.executable, str(ROOT / 'tools/local_proof.py'), '--interval', '0.2', '--', str(d / 'probe.py')], capture_output=True, text=True, timeout=60)
            new = sorted(set((ROOT / 'runs/proof').glob('local_*.json')) - before)
            rec = json.loads(new[-1].read_text())
            for p in new:   # test artifacts are not evidence: remove them
                for q in (p, p.with_suffix('.html'), p.parent / p.name.replace('local_', 'connects_').replace('.json', '.jsonl')):
                    q.unlink(missing_ok=True)
            return rec
        finally:
            shutil.rmtree(d)

    def test_loopback_only_passes(self):
        rec = self.run_proof("import socket\ns=socket.socket(); s.settimeout(1)\ntry: s.connect(('127.0.0.1', 9))\nexcept OSError: pass\n")
        self.assertTrue(rec['verdict']['process_tree_loopback_only'])
        self.assertEqual(rec['verdict']['python_connect_targets'], ['127.0.0.1:9'])

    def test_outbound_is_flagged(self):
        rec = self.run_proof("import socket\ns=socket.socket(); s.settimeout(1)\ntry: s.connect(('1.1.1.1', 443))\nexcept OSError: pass\n")
        self.assertFalse(rec['verdict']['process_tree_loopback_only'])
        self.assertIn('1.1.1.1:443', rec['verdict']['python_connect_targets'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

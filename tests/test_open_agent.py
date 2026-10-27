"""Open Agent Hackathon 2026 tests (stdlib unittest; also run by tests/run_all.py).

The Nightshift client is exercised against a LOCAL MOCK OpenAI-compatible server (127.0.0.1, model id 'MOCK-not-nemotron'):
that tests our code paths (retry on 5xx, typed unavailable, model_reported from the body, record -> offline replay), NOT
Nightshift. The live Nightshift check is tools/nightshift_smoke.py (skipped here without NIGHTSHIFT_API_KEY).
"""
import json, os, re, shutil, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT))
from gmaps_core.providers.nightshift import NightshiftLLM
from gmaps_core.providers.sample import SampleLLM
from gmaps_core.providers.base import Unavailable
from gmaps_core.grounding import check_residue, check_explanation
from gmaps_core.trace import TraceLogger, read
from gmaps_core.data.zetaris_store import LocalStore, ZetarisStore, NotIntegrated as ZNI
from gmaps_core.engines.runway_memory import LocalRunwayMemory
from gmaps_core.adapters import LocalLLM
from agent.open_agent_loop import OpenAgent

MOCK_MODEL = 'MOCK-not-nemotron'


class Mock(BaseHTTPRequestHandler):
    fail_next = 0          # respond 503 this many times first
    mode = 'good'          # good | bad_explain | bad_parse
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Mock.calls.append({'auth': self.headers.get('Authorization'), 'app': self.headers.get('meterless-app'), 'body': body})
        if Mock.fail_next > 0:
            Mock.fail_next -= 1
            self.send_response(503); self.end_headers(); self.wfile.write(b'busy'); return
        user = body['messages'][-1]['content']
        if 'Transmission:' in user:
            if Mock.mode == 'bad_parse':
                content = json.dumps({'speech_act': 'instruction', 'kind': 'CROSS', 'callsigns': ['RESCUE7'], 'runway': '27', 'route': None,
                                      'hold_short': None, 'crossing_at': 'K', 'confidence_note': ''})
            else:
                content = json.dumps({'speech_act': 'instruction', 'kind': 'CROSS', 'callsigns': ['RESCUE7'], 'runway': '36', 'route': None,
                                      'hold_short': None, 'crossing_at': 'K', 'confidence_note': ''})
        else:
            facts = json.loads(user.split('\n', 1)[1])
            cs = ' and '.join(facts['callsigns'])
            content = (f'Recorded authorizations for {cs} share runway 36 at t+{facts["t"]}. The rule flags them because both hold the same runway at this time.'
                       if Mock.mode != 'bad_explain' else f'{cs}. This would have prevented it. Three. Four.')
        out = {'model': MOCK_MODEL, 'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 11, 'completion_tokens': 7}}
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(json.dumps(out).encode())


class OpenAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(('127.0.0.1', 0), Mock)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.srv.server_port}/v1'
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        Mock.fail_next, Mock.mode, Mock.calls = 0, 'good', []

    def llm(self):
        return NightshiftLLM(base_url=self.url, api_key='fake-key', model='requested-model', timeout=5)

    def test_retry_once_on_5xx_then_model_reported(self):
        Mock.fail_next = 1
        d = self.llm().parse_residue('Rescue seven uh go ahead and get across three six at kilo', 'XSIM')
        self.assertEqual(d['_call']['model_reported'], MOCK_MODEL)
        self.assertEqual(d['_call']['attempts'], 2)
        self.assertEqual(Mock.calls[-1]['body']['temperature'], 0)
        self.assertEqual(Mock.calls[-1]['auth'], 'Bearer fake-key')
        self.assertEqual(Mock.calls[-1]['app'], 'gmaps')

    def test_two_5xx_is_typed_unavailable_not_a_guess(self):
        Mock.fail_next = 2
        d = self.llm().parse_residue('anything', 'XSIM')
        self.assertEqual(d['error'], 'provider_unavailable')
        self.assertEqual(d['kind'], 'OTHER')
        self.assertIsInstance(self.llm().explain_warning({'callsigns': []}) if (setattr(Mock, 'fail_next', 2) or True) else None, Unavailable)

    def test_local_llm_still_refuses_cloud(self):
        with self.assertRaises(ValueError):
            LocalLLM('https://api.example.com/v1')

    def test_grounding_rejects_unheard_runway_and_bad_explanation(self):
        ids = {'runways': ['18', '36'], 'taxiways': ['K']}
        self.assertFalse(check_residue({'speech_act': 'instruction', 'kind': 'CROSS', 'callsigns': ['RESCUE7'], 'runway': '27', 'crossing_at': 'K'},
                                       'Rescue seven cross three six at kilo', ids)[0])
        self.assertTrue(check_residue({'speech_act': 'instruction', 'kind': 'CROSS', 'callsigns': ['RESCUE7'], 'runway': '36', 'crossing_at': 'K'},
                                      'Rescue seven uh go ahead and get across three six at kilo', ids)[0])
        self.assertFalse(check_explanation('RESCUE7 and SIM212. This would have prevented it.', {'callsigns': ['RESCUE7', 'SIM212']})[0])
        self.assertFalse(check_explanation('Use another channel for RESCUE7 SIM212.', {'callsigns': ['RESCUE7', 'SIM212']})[0])

    def run_agent(self, scen, prov, run_id):
        tr = TraceLogger(run_id, scenario=scen, out_dir=self.tmp)
        return OpenAgent(scen, prov, LocalStore(ledger_dir=self.tmp / 'ledger'), LocalRunwayMemory(), tr).run(), tr

    def test_agent_explainer_accepted_then_offline_replay_matches(self):
        out, tr = self.run_agent('SIM_E3_STOP_NOT_OBSERVED', self.llm(), 'mock-e3')
        srcs = [a['explanation_source'] for a in out['alerts']]
        self.assertTrue(out['alerts'] and all(MOCK_MODEL in s for s in srcs), srcs)
        rec = self.tmp / 'rec'; rec.mkdir(exist_ok=True)
        shutil.copy(tr.path, rec / 'SIM_E3_STOP_NOT_OBSERVED.jsonl')
        replay, tr2 = self.run_agent('SIM_E3_STOP_NOT_OBSERVED', SampleLLM(traces_dir=rec), 'replay-e3')
        self.assertEqual([a['explanation'] for a in replay['alerts']], [a['explanation'] for a in out['alerts']])
        calls = [r for r in read(tr2.path) if r['step'] == 'explain']
        self.assertTrue(calls and all(r['replayed_from'] and r['model_reported'] == MOCK_MODEL and r['latency_ms'] is None for r in calls))

    def test_rejected_explanation_falls_back_to_ledger_text_labelled(self):
        Mock.mode = 'bad_explain'
        out, tr = self.run_agent('SIM_E1_CROSS_DURING_ROLLOUT', self.llm(), 'mock-e1-bad')
        self.assertTrue(all('model answer rejected' in a['explanation_source'] for a in out['alerts']))
        self.assertTrue(any(r['step'] == 'evaluate_explanation' and r['outcome'] == 'rejected' for r in read(tr.path)))

    def test_residue_retry_then_escalate(self):
        Mock.mode = 'bad_parse'
        ag = OpenAgent('SIM_E1_CROSS_DURING_ROLLOUT', self.llm(), LocalStore(ledger_dir=self.tmp / 'ledger'), LocalRunwayMemory(),
                       TraceLogger('mock-residue', out_dir=self.tmp))
        from gmaps_core.parser import parse
        line = {'t': 5, 'text': 'Rescue seven uh go ahead and get across three six at kilo'}
        self.assertIsNone(ag.residue(line, parse(line['text'], 'XSIM')))
        steps = [(r['step'], r['outcome']) for r in read(ag.tr.path)]
        self.assertIn(('residue_parse', 'retried'), steps); self.assertIn(('escalate', 'escalated'), steps)
        self.assertIn('Allowed identifiers', Mock.calls[-1]['body']['messages'][-1]['content'])
        self.assertEqual(ag.display[-1]['severity'], 'ESCALATION')

    def test_sample_mode_unrecorded_request_is_unavailable(self):
        r = SampleLLM(traces_dir=self.tmp / 'empty').chat([{'role': 'user', 'content': 'x'}])
        self.assertFalse(r['ok']); self.assertIn('not recorded', r['error'])

    def test_store_roundtrip_and_klga_excluded(self):
        s = LocalStore(ledger_dir=self.tmp / 'ledger')
        s.append_ledger('rt', [{'record_id': 'r1', 'status': 'active'}])
        self.assertEqual(s.read_ledger('rt'), [{'record_id': 'r1', 'status': 'active'}])
        self.assertEqual(s.identifiers('KLGA'), {'runways': [], 'taxiways': []})
        self.assertEqual(s.identifiers('XSIM'), {'runways': ['18', '36'], 'taxiways': ['K']})
        with self.assertRaises(ZNI):
            ZetarisStore()

    def test_new_code_never_reads_truth_or_labels_and_no_secrets(self):
        files = [ROOT / 'agent/open_agent_loop.py', ROOT / 'demo/run_open_agent.py'] + list((ROOT / 'lib/gmaps_core').glob('*/*.py')) + [ROOT / 'lib/gmaps_core/trace.py', ROOT / 'lib/gmaps_core/grounding.py']
        for f in files:
            src = f.read_text()
            self.assertNotRegex(src, r'eval/(truth|labels)', str(f))
            self.assertNotRegex(src, r'sk-[A-Za-z0-9]{8,}', str(f))


if __name__ == '__main__':
    unittest.main(verbosity=2)

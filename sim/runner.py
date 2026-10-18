"""Simulator runner: scenario -> synthetic radio audio -> streamed in real time -> end of speech -> Whisper -> the deployed fast
path (grammar + LLM fallback + ledger rules + clock) -> alerts with timestamps. SIMULATED; fictional XSIM only.

Live path used (the same code the deployed tool server runs for real audio, minus HTTP):
  audio stream (20 ms frames, real time / --speed) -> end of speech: the squelch closes (no carrier for HANG_S after the last
  voiced frame) -> Whisper large-v3-turbo (one warm worker in the demo's offline throwaway container, no prompt, as E1)
  -> venue/gmaps_mcp.GmapsTools.fast_path(text, 'XSIM', ...) on arrival -> post_alert dry run (on_alert) ; a clock thread calls
  GmapsTools.advance() every clock_period_s of scenario time, as the server's clock thread does (timers: readback, reminders).
Options (claude/sim-upgrades, sim/options.py): a traffic list, a round-3 Whisper initial prompt, the LLM fallback on/off,
and two no-audio modes for any CPU box: TEXT (script text) and REPLAY (the words Whisper heard in an earlier audio run).
"""
import array, copy, hashlib, json, math, os, subprocess, sys, threading, time, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'venue'), str(ROOT / 'lib'), str(ROOT / 'tools')]
import gmaps_mcp as GM   # noqa: E402

CACHE = Path.home() / 'gmaps_venue' / 'sim_cache'          # outside the repo: synthetic audio is never committed
TTS_PY = Path.home() / 'gmaps_models' / 'tts-venv' / 'bin' / 'python'
WHISPER = Path.home() / 'gmaps_models' / 'whisper-large-v3-turbo'
IMG = 'sha256:46591c6e4a018d8d197fa246b1e3d682c907654aab4e9402302abb3e6a7dd916'   # existing nemoclaw vLLM image (as venue/demo.sh)
SR, FRAME_S, THR_RMS, HANG_S = 16000, 0.02, 0.001 * 32768, 0.30   # 16-bit PCM, stdlib only. Squelch: carrier noise inside a transmission stays far above 0.001 FS; between transmissions it is digital silence
ATC_VOICE, PILOT_VOICES = 'am_michael', ['am_adam', 'af_bella', 'bm_lewis', 'am_eric', 'af_nicole', 'am_liam', 'bf_emma', 'am_onyx']


def voice_for(tx):
    if tx['speaker'] == 'ATC':
        return ATC_VOICE
    return PILOT_VOICES[int(hashlib.sha1(tx['callsign'].encode()).hexdigest(), 16) % len(PILOT_VOICES)]


def clip_path(tx):
    v = voice_for(tx)
    h = hashlib.sha1(f"{tx['text']}|{v}|1.15|dev".encode()).hexdigest()[:16]
    return CACHE / 'audio' / f'{h}.wav', v, int(h[:8], 16)


def render(scenarios, log=print):
    jobs = []
    for sc in scenarios:
        for tx in sc['transmissions']:
            p, v, seed = clip_path(tx)
            jobs.append({'text': tx['text'], 'voice': v, 'speed': 1.15, 'seed': seed, 'out': str(p)})
    (CACHE / 'audio').mkdir(parents=True, exist_ok=True)
    jf = CACHE / f'render_jobs_{os.getpid()}.json'
    jf.write_text(json.dumps(jobs))
    t0 = time.time()
    r = subprocess.run([str(TTS_PY), str(ROOT / 'sim/render_tts.py'), str(jf)], capture_output=True, text=True, timeout=900)
    if r.returncode:
        raise RuntimeError(f'render failed: {r.stderr[-800:]}')
    log(f'render: {r.stdout.strip()} in {time.time() - t0:.1f} s (Kokoro, local; cache {CACHE / "audio"})')


def load_clip(tx):
    with wave.open(str(clip_path(tx)[0]), 'rb') as w:
        assert w.getframerate() == SR and w.getsampwidth() == 2 and w.getnchannels() == 1
        return array.array('h', w.readframes(w.getnframes()))


def write_wav(path, samples):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(samples.tobytes())


class Whisper:
    """One warm Whisper worker in a throwaway offline container (--rm, --network none, read-only mounts). Stopped by a STOP file.
    prompt: optional round-3 initial prompt (sim/options.whisper_prompt), passed to the worker as GMAPS_SIM_PROMPT."""
    def __init__(self, log=print, prompt=None):
        self.work = CACHE / f"work_{time.strftime('%Y%m%d-%H%M%S')}_{os.getpid()}"
        (self.work / 'in').mkdir(parents=True)
        (self.work / 'out').mkdir()
        os.chmod(self.work, 0o777); os.chmod(self.work / 'in', 0o777); os.chmod(self.work / 'out', 0o777)
        self.n = 0
        self.prompt = prompt
        cmd = ['docker', 'run', '--rm', '--network', 'none', '--gpus', 'all', '--ipc=host', '-e', 'HF_HUB_OFFLINE=1', '-e', 'TRANSFORMERS_OFFLINE=1',
               '-e', 'HOME=/tmp', *(['-e', f'GMAPS_SIM_PROMPT={prompt}'] if prompt else []),
               '--user', f'{os.getuid()}:{os.getgid()}', '-v', f'{ROOT}:/gmaps:ro', '-v', f'{WHISPER}:/models/whisper:ro',
               '-v', f'{self.work}:/work', '--entrypoint', 'python3', IMG, '/gmaps/sim/whisper_worker.py']
        self.log_f = open(self.work / 'worker.log', 'w')
        t0 = time.time()
        self.p = subprocess.Popen(cmd, stdout=self.log_f, stderr=subprocess.STDOUT)
        while not (self.work / 'out' / 'READY').exists():
            if self.p.poll() is not None or time.time() - t0 > 600:
                raise RuntimeError(f'Whisper worker did not start; see {self.work / "worker.log"}')
            time.sleep(0.2)
        log(f'whisper: worker ready in {time.time() - t0:.1f} s (large-v3-turbo, offline container, warm, '
            f'{"round-3 initial prompt" if prompt else "no prompt"})')

    def transcribe(self, audio):
        self.n += 1
        name = f'{self.n:05d}'
        part = self.work / 'in' / f'{name}.part'
        write_wav(part, audio)
        part.rename(self.work / 'in' / f'{name}.wav')
        out = self.work / 'out' / f'{name}.json'
        t0 = time.time()
        while not out.exists():
            if self.p.poll() is not None or time.time() - t0 > 120:
                raise RuntimeError('Whisper worker stopped or timed out')
            time.sleep(0.005)
        return json.loads(out.read_text())

    def close(self):
        (self.work / 'STOP').write_text('stop\n')
        try:
            self.p.wait(timeout=60)
        finally:
            self.log_f.close()


def stream_until_end_of_speech(audio, start_wall, speed):
    """Feed 20 ms frames at real time (/speed); after the clip, the squelch is closed (silence). End of speech = HANG_S of
    no carrier after the last voiced frame. Returns (segment, speech_end_wall, endpoint_wall)."""
    n = int(SR * FRAME_S)
    frames = [audio[i:i + n] for i in range(0, len(audio), n)]
    silence = array.array('h', bytes(2 * n))
    hang = int(round(HANG_S / FRAME_S))
    last_voiced, i = None, 0
    while True:
        due = start_wall + i * FRAME_S / speed
        d = due - time.time()
        if d > 0:
            time.sleep(d)
        f = frames[i] if i < len(frames) else silence
        if math.sqrt(sum(x * x for x in f) / max(1, len(f))) > THR_RMS:
            last_voiced = i
        if last_voiced is not None and i - last_voiced >= hang:
            seg = array.array('h')
            for fr in frames[:last_voiced + 1]:
                seg.extend(fr)
            return seg, start_wall + (last_voiced + 1) * FRAME_S / speed, time.time()
        i += 1
        if i > len(frames) + 10 * hang:
            raise RuntimeError('no speech detected in clip')


def make_tools(sc, mode, traffic=None, llm=True, llm_endpoint=None):
    """GmapsTools as the deployed server builds it. llm: the local-LLM parse fallback, on in the server's main() unless
    --no-llm-fallback. traffic: an optional traffic list (sim/options.traffic_list); None = no list, as the server today.
    llm_endpoint: optional (base_url, model) for the fallback instead of runtime_config's (sim only; None = the server's)."""
    cfg = None
    if llm_endpoint:
        cfg = copy.deepcopy(GM.CONFIG)
        cfg['llm']['base_url'], cfg['llm']['model'] = llm_endpoint
    tools = GM.GmapsTools('SIM_E1_CROSS_DURING_ROLLOUT', f"sim-{sc['id']}-{os.getpid()}", send=False,
                          input_mode=f'SIMULATED self-run simulator ({mode})', config=cfg)
    tools.on_log = None
    if llm:
        tools.enable_llm(sorted(e for r in GM.SIM_MAP['runways'].values() for e in r['ends']), sorted(k.split(':')[-1] for k in GM.SIM_MAP['taxiways']))
    if traffic:
        tools.traffic = set(traffic)
    return tools


def capture_alerts(tools, state, alerts):
    """Record every post_alert (dry run) with the warning's rule, pair, runway and scenario time; latency only with a wall clock."""
    def on_alert(r):
        res = r.get('result') or {}
        w = next((x for x in tools.led.warnings if x['warning_id'] == res.get('warning_id')), {})
        pair = [x.get('callsign') for x in (w.get('authorization_a'), w.get('authorization_b')) if x and x.get('callsign')]
        end = state.get('tx_end_wall')
        alerts.append({'warning_id': res.get('warning_id'), 'rule': res.get('rule') or w.get('rule_id'), 'severity': w.get('severity'),
                       'runway': w.get('runway'), 'aircraft': pair, 't_fired': res.get('t_fired', w.get('t_fired')),
                       'shadow': bool(r.get('shadow')), 'via': r.get('via'), 'alert_wall': r.get('t') if end else None, 'after_tx': state['tx'],
                       'latency_s': round(r['t'] - end, 3) if end and r.get('t') else None})
    tools.on_alert = on_alert


def _heard(tx, t_arr, text, r, asr_s=None, endpoint_wait_s=None):
    ev = r.get('event') or {}
    return {'tx': tx['id'], 't_script': tx['t'], 't_arrival': t_arr, 'script': tx['text'], 'whisper': text, 'asr_s': asr_s,
            'path': 'grammar' if r.get('grammar') else ('llm' if r.get('llm_parse') else 'left to the agent (off in the simulator)'),
            'llm': r.get('llm'), 'endpoint_wait_s': endpoint_wait_s, 'grammar': r.get('grammar'), 'kind': ev.get('kind'),
            'callsigns': ev.get('actor_candidates'), 'runway': ev.get('runway'), 'callsign_match': ev.get('callsign_match')}


WORDS_PER_S, END_OF_SPEECH_S, ASR_S = 2.8, HANG_S, 0.15   # text mode: when a transmission would arrive (speech + squelch + ASR)


def text_arrival(tx):
    """Text mode: scenario time a transmission's text reaches the fast path, as in the audio run (deterministic estimate)."""
    return round(tx['t'] + len(tx['text'].split()) / WORDS_PER_S + END_OF_SPEECH_S + ASR_S, 2)


def run_scenario_text(sc, traffic=None, llm=False, log=print, texts=None, llm_endpoint=None):
    """TEXT MODE (claude/sim-upgrades): the script text goes straight to the deployed fast path on a simulated scenario clock.
    No audio, no Whisper, no wall clock: runs on any CPU box in a second. It checks a scenario's ground truth against the
    deterministic pipeline with perfect transcripts; it says nothing about speech recognition or latency.
    texts: optional {tx id: text} to use instead of the script, e.g. what Whisper heard in an earlier audio run (REPLAY mode:
    the same misheard words, re-run with different options such as a traffic list). The arrival times stay the text-mode ones."""
    texts = texts or {}
    tools = make_tools(sc, 'replayed Whisper text, no audio' if texts else 'script text, no audio', traffic, llm, llm_endpoint)
    alerts, heard, state = [], [], {'tx_end_wall': None, 'tx': None}
    capture_alerts(tools, state, alerts)
    period = tools.cfg.get('clock_period_s', 1.0)
    timeline = [(text_arrival(tx), 1, tx) for tx in sc['transmissions']]
    n = 1
    while n * period <= sc['end_t']:
        timeline.append((round(n * period, 2), 0, None))
        n += 1
    for t, _, tx in sorted(timeline, key=lambda x: (x[0], x[1])):
        if tx is None:
            tools.advance(t, via='sim clock')
            continue
        state['tx'] = tx['id']
        text = texts.get(tx['id']) or tx['text']
        r = tools.fast_path(text, 'XSIM', f"{sc['id']}-{tx['id']}", t)
        heard.append(_heard(tx, t, text if tx['id'] in texts else '(script text)', r))
        log(f"  {sc['id']} {tx['id']} t={t:6.2f}  {'replayed' if tx['id'] in texts else 'script'}: {text!r} -> {heard[-1]['path']}: "
            f"{heard[-1]['kind'] or r.get('kind') or '-'} {heard[-1]['callsigns'] or ''}")
    for al in alerts:
        log(f"  {sc['id']} ALERT {al['rule']} {al['severity']} {al['aircraft']} t_fired={al['t_fired']}" + (' (shadow, not shown)' if al['shadow'] else ''))
    return {'alerts': alerts, 'heard': heard}


def run_scenario(sc, whisper, speed=1.0, log=print, traffic=None, llm=True, llm_endpoint=None):
    tools = make_tools(sc, 'synthetic audio', traffic, llm, llm_endpoint)
    lock = threading.Lock()
    alerts, heard, state = [], [], {'tx_end_wall': None, 'tx': None}
    w0 = time.time() + 0.5
    now_s = lambda: (time.time() - w0) * speed
    capture_alerts(tools, state, alerts)
    stop = threading.Event()

    def clock():
        while not stop.wait(tools.cfg.get('clock_period_s', 1.0) / speed):
            with lock:
                tools.advance(now_s(), via='sim clock')
    th = threading.Thread(target=clock, daemon=True)
    th.start()
    try:
        for tx in sc['transmissions']:
            seg, speech_end, endpoint = stream_until_end_of_speech(load_clip(tx), w0 + tx['t'] / speed, speed)
            state['tx_end_wall'], state['tx'] = speech_end, tx['id']
            a = whisper.transcribe(seg)
            with lock:
                t_arr = round(now_s(), 2)
                r = tools.fast_path(a['text'] or '(empty)', 'XSIM', f"{sc['id']}-{tx['id']}", t_arr)
            heard.append(_heard(tx, t_arr, a['text'], r, a['asr_s'], round(endpoint - speech_end, 3)))
            log(f"  {sc['id']} {tx['id']} t={t_arr:6.2f}  heard: {a['text']!r}  -> {heard[-1]['path']}: {heard[-1]['kind'] or r.get('kind') or '-'}")
        end_wall = w0 + sc['end_t'] / speed
        while time.time() < end_wall:
            time.sleep(min(0.2, max(0.0, end_wall - time.time())))
    finally:
        stop.set()
        th.join(timeout=5)
    for al in alerts:
        log(f"  {sc['id']} ALERT {al['rule']} {al['severity']} {al['aircraft']} t_fired={al['t_fired']} latency={al['latency_s']} s"
            + (' (shadow, not shown)' if al['shadow'] else ''))
    return {'alerts': alerts, 'heard': heard}

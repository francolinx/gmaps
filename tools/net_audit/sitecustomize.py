"""Connection auditor injected by tools/local_proof.py (on PYTHONPATH of the audited run only).

Wraps socket.socket.connect / connect_ex so every outbound connection made by any Python process of the run is appended to
$GMAPS_NET_AUDIT as one JSON line {ts, pid, family, host, port}. Exact, not sampled. Does not change behaviour."""
import json, os, socket, time

_PATH = os.environ.get('GMAPS_NET_AUDIT')
if _PATH:
    _orig_connect, _orig_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _log(sock, address):
        try:
            host, port = (address[0], address[1]) if isinstance(address, tuple) else (str(address), None)
            with open(_PATH, 'a') as f:
                f.write(json.dumps({'ts': time.time(), 'pid': os.getpid(), 'family': str(sock.family).replace('AddressFamily.', ''),
                                    'host': host, 'port': port}) + '\n')
        except Exception:
            pass

    def connect(self, address):
        _log(self, address)
        return _orig_connect(self, address)

    def connect_ex(self, address):
        _log(self, address)
        return _orig_connect_ex(self, address)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex

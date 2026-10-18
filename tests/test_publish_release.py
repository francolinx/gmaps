"""CPU check of tools/publish_release.py: the dry-run bundle builds clean, and the deny-lists catch what they must.

  python3 tests/test_publish_release.py
"""
import contextlib, io, json, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))
import publish_release as PR   # noqa: E402


def main():
    out = Path(tempfile.mkdtemp(prefix='release_test_'))
    r = subprocess.run([sys.executable, str(REPO / 'tools/publish_release.py'), '--dry-run', '--name', 't', '--out', str(out)], capture_output=True, text=True)
    bundle = out / 't'
    manifest = json.loads((bundle / 'MANIFEST.json').read_text()) if (bundle / 'MANIFEST.json').exists() else {'files': {}}
    checks = {
        'dry run exits 0 and reports clean': r.returncode == 0 and 'Bundle is clean' in r.stdout,
        'bundle has dataset, code, cards, manifest': all((bundle / p).exists() for p in ('README.md', 'dataset/README.md', 'code/README.md', 'MANIFEST.json', 'dataset/train/pairs_asr.jsonl', 'code/train/local_lora.py')),
        'no bundled path trips the deny-list': not any(PR.DENY_PATH.search(p) for p in manifest['files']),
        'no audio file in the bundle': not any(p.lower().endswith(PR.PC.AUDIO_EXT) for p in manifest['files']),
        'deny-list catches INTERNAL / real audio / LGA fixture / truth / labels': all(PR.DENY_PATH.search(p) for p in (
            'docs/INTERNAL_lga_overlay.md', 'audio_real_private/x.wav', 'fixtures/LGA_2026-03-22.json', 'eval/truth/e1.json', 'eval/labels/a.json', 'data/transcripts/real/x.txt')),
        'deny-list lets the allowed files through': not any(PR.DENY_PATH.search(p) for p in ('train/pairs_asr.jsonl', 'code/lib/gmaps_core/parser.py', 'dataset/eval/heldout5_script.jsonl')),
        'deny-text catches INTERNAL replay banners and the forbidden claim': all(any(rx.search(t) for rx in PR.DENY_TEXT) for t in (
            b'INTERNAL: LGA 2026-03-22 replay, do not publish', b'this would have prevented LaGuardia', b'clip from VASAviation')),
        'model card says SIMULATED and decision support': all(s in (bundle / 'README.md').read_text() for s in ('SIMULATED', 'the controller decides')),
    }
    ok = True
    for k, v in checks.items():
        print(('PASS ' if v else 'FAIL ') + k); ok &= bool(v)
    if not ok:
        print(r.stdout[-1500:], r.stderr[-1500:])
    print(f'{sum(checks.values())}/{len(checks)} passed')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()

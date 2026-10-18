"""Every number with a % or a decimal in the release cards' prose and captions must appear in docs/SCOREBOARD.md,
docs/PAIRED_STATS.md or docs/CLAIMS_LEDGER.md. Rows of the "Exact values" table are skipped (copied from the scoreboard by tools/publish_release.py); every other table is checked.
A percentage also matches as a scoreboard fraction (65.2 % <-> 0.652).

  python3 tools/card_numbers_check.py [--bundle ~/gmaps_venue/release/gmaps-parser-qwen3-4b-lora]   -> CARD NUMBERS OK
"""
import argparse, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCES = ['docs/SCOREBOARD.md', 'docs/PAIRED_STATS.md', 'docs/CLAIMS_LEDGER.md']
NUM = re.compile(r'(?<![\w.])(\d+(?:\.\d+)+|\d+(?=\s*%))(\s*%)?')


def prose(md):
    """(line number, text) for every line outside the front matter and the copied "Exact values" table."""
    out, fm, copied = [], False, False
    for i, l in enumerate(md.splitlines(), 1):
        if l.strip() == '---' and (i == 1 or fm):
            fm = i == 1; continue
        if l.startswith('#'):
            copied = l.startswith('### Exact values')
        if fm or not l.strip() or (copied and l.startswith('|')):
            continue
        out.append((i, l))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', default=str(Path.home() / 'gmaps_venue/release/gmaps-parser-qwen3-4b-lora'))
    a = ap.parse_args(argv)
    src = '\n'.join((REPO / s).read_text() for s in SOURCES)
    missing = []
    for card in ('README.md', 'dataset/README.md'):
        for i, line in prose((Path(a.bundle).expanduser() / card).read_text()):
            for m in NUM.finditer(line):
                n, pct = m.group(1), bool(m.group(2))
                forms = {n}
                if pct and re.fullmatch(r'\d+(\.\d)?', n):
                    forms.add(f'{float(n) / 100:.3f}')
                if not any(re.search(rf'(?<![\d.]){re.escape(f)}0*(?![\d])', src) for f in forms):
                    missing.append(f'{card}:{i}: {m.group(0).strip()} | {line.strip()}')
    for x in missing:
        print('NOT FOUND ' + x)
    print('CARD NUMBERS OK' if not missing else f'CARD NUMBERS: {len(missing)} not found')
    return 0 if not missing else 1


if __name__ == '__main__':
    sys.exit(main())

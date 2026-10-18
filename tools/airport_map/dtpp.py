"""FAA d-TPP helpers: the cycle in effect, an airport's airport-diagram (APD) record, and the PDF download. Stdlib only.

  python3 tools/airport_map/dtpp.py --icao KHWD        # downloads the APD into data/airports/sources/, writes <icao>/source_chart.json

Cycle in effect: probe the d-TPP metafile for candidate cycle numbers and keep the one whose from/to window (0901Z)
contains now. Metafiles (~16 MB) are cached outside the repo in ~/.cache/gmaps/dtpp/.
"""
import argparse, datetime, hashlib, json, sys, time, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CACHE = Path.home() / '.cache/gmaps/dtpp'
UA = {'User-Agent': 'gmaps-airport-map/0.1 (local research)'}


def get(url, timeout=180):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def metafile(cycle):
    p = CACHE / f'{cycle}_d-TPP_Metafile.xml'
    if not p.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        p.write_bytes(get(f'https://aeronav.faa.gov/d-tpp/{cycle}/xml_data/d-TPP_Metafile.xml'))
    return p


def parse_edate(s):   # '0901Z  10/01/26' -> aware datetime
    hhmm, mdy = s.split()
    return datetime.datetime.strptime(mdy + hhmm[:4], '%m/%d/%y%H%M').replace(tzinfo=datetime.timezone.utc)


def candidate_cycles(now):
    """Cycle numbers to probe: this year's and next year's 01..13 around today's position (no fixed calendar assumed)."""
    yy = now.year % 100
    out = [f'{yy:02d}{n:02d}' for n in range(1, 14)] + [f'{(yy + 1) % 100:02d}01', f'{(yy - 1) % 100:02d}13']
    guess = max(1, min(13, (now.timetuple().tm_yday // 28) + 1))
    return sorted(out, key=lambda c: abs(int(c[2:]) - guess) if c[:2] == f'{yy:02d}' else 99)


def cycle_in_effect(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for c in candidate_cycles(now):
        try:
            it = ET.iterparse(metafile(c), events=('start',))   # keep a reference: a temporary iterator closes the file
            root_attrs = next(it)[1].attrib
        except Exception:
            continue
        if parse_edate(root_attrs['from_edate']) <= now < parse_edate(root_attrs['to_edate']):
            return c, root_attrs
    raise SystemExit('no d-TPP cycle in effect found')


def apd_record(cycle, icao):
    root = ET.parse(metafile(cycle)).getroot()
    for ap in root.iter('airport_name'):
        if ap.get('icao_ident') == icao:
            for r in ap.findall('record'):
                if (r.findtext('chart_code') or '').strip() == 'APD':
                    return ap.attrib, {e.tag: (e.text or '').strip() for e in r}
            return ap.attrib, None
    return None, None


def fetch_diagram(icao):
    cyc, attrs = cycle_in_effect()
    ap, rec = apd_record(cyc, icao)
    if not rec:
        raise SystemExit(f'{icao}: no APD (airport diagram) in d-TPP cycle {cyc}')
    url = f'https://aeronav.faa.gov/d-tpp/{cyc}/{rec["pdf_name"]}'
    pdf = get(url)
    local = REPO / 'data/airports/sources' / f'{ap["apt_ident"].lower()}_faa_{cyc}_{rec["pdf_name"].rsplit(".", 1)[0]}.pdf'
    local.parent.mkdir(parents=True, exist_ok=True); local.write_bytes(pdf)
    f, t = parse_edate(attrs['from_edate']), parse_edate(attrs['to_edate'])
    src = {'id': f'{ap["apt_ident"]}-FAA-{cyc}', 'kind': 'faa_diagram', 'title': f'FAA {ap["ID"].title()} airport diagram, cycle {cyc}, {rec["pdf_name"]}',
           'uri': url, 'local_path': str(local.relative_to(REPO / 'data/airports')), 'sha256': hashlib.sha256(pdf).hexdigest(),
           'retrieved_on': time.strftime('%Y-%m-%d'), 'effective_from': f.date().isoformat(), 'effective_to': t.date().isoformat(),
           'dtpp_cycle': cyc, 'dtpp_metafile': f'https://aeronav.faa.gov/d-tpp/{cyc}/xml_data/d-TPP_Metafile.xml', 'apd_record': rec,
           'airport': {k: ap.get(k) for k in ('ID', 'apt_ident', 'icao_ident', 'alnum')}, 'chart_code': None}   # chart_code read from the PDF by provenance.py
    out = REPO / 'data/airports' / icao.lower(); out.mkdir(parents=True, exist_ok=True)
    old_p = out / 'source_chart.json'
    old = json.loads(old_p.read_text()) if old_p.exists() else {}
    if old.get('sha256') == src['sha256'] and old.get('dtpp_cycle') == cyc:   # same PDF, same cycle: keep the original retrieval record
        src = old
    else:
        old_p.write_text(json.dumps(src, indent=1))
    print(json.dumps({k: src[k] for k in ('title', 'uri', 'local_path', 'sha256', 'effective_from', 'effective_to')}, indent=1))
    return src


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True)
    fetch_diagram(ap.parse_args().icao.upper())

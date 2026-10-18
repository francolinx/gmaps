"""FAA NASR helpers: the subscription cycle in effect, the APT CSV extract (cached outside the repo), and the airport's
rows. Only the rows used are committed (Franco D2). Stdlib only.

  python3 tools/airport_map/nasr.py --icao KHWD      # -> data/airports/<icao>/sources/nasr/{APT_BASE,APT_RWY,APT_RWY_END}.csv + PROVENANCE.json
"""
import argparse, csv, datetime, hashlib, io, json, re, time, urllib.request, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CACHE = Path.home() / '.cache/gmaps/nasr'
INDEX = 'https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/'
UA = {'User-Agent': 'Mozilla/5.0 (gmaps-airport-map/0.1 local research)'}
FILES = ('APT_BASE.csv', 'APT_RWY.csv', 'APT_RWY_END.csv')


def get(url, timeout=300):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def cycle_in_effect(today=None):
    today = today or datetime.date.today()
    dates = sorted({datetime.date.fromisoformat(d) for d in re.findall(r'NASR_Subscription/(\d{4}-\d{2}-\d{2})', get(INDEX).decode('utf-8', 'ignore'))})
    eff = [d for d in dates if d <= today]
    if not eff:
        raise SystemExit('no NASR cycle in effect listed')
    return eff[-1]


def csv_zip(cycle):
    p = CACHE / f'{cycle.isoformat()}_APT_CSV.zip'
    url = f'https://nfdc.faa.gov/webContent/28DaySub/extra/{cycle.strftime("%d_%b_%Y")}_APT_CSV.zip'
    if not p.exists():
        CACHE.mkdir(parents=True, exist_ok=True); p.write_bytes(get(url))
    return p, url


def extract(icao, arpt_id=None):
    cyc = cycle_in_effect()
    zp, url = csv_zip(cyc)
    z = zipfile.ZipFile(zp)
    arpt = arpt_id or (icao[1:] if len(icao) == 4 and icao[0] == 'K' else icao)
    out = REPO / 'data/airports' / icao.lower() / 'sources/nasr'; out.mkdir(parents=True, exist_ok=True)
    prov = {'source': 'FAA NASR 28-day subscription, CSV extract', 'cycle_effective': cyc.isoformat(), 'url': url,
            'index_page': INDEX + cyc.isoformat(), 'zip_sha256': hashlib.sha256(zp.read_bytes()).hexdigest(), 'zip_bytes': zp.stat().st_size,
            'retrieved_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'filter': f"ARPT_ID == '{arpt}'", 'licence': 'US Government work (public domain)', 'files': {}}
    for name in FILES:
        raw = z.read(name)
        rows = list(csv.reader(io.StringIO(raw.decode('latin-1'))))
        hdr = rows[0]; i = hdr.index('ARPT_ID'); keep = [r for r in rows[1:] if r[i] == arpt]
        if not keep:
            raise SystemExit(f'NASR {name}: no rows for ARPT_ID {arpt}')
        buf = io.StringIO(); w = csv.writer(buf, lineterminator='\n'); w.writerow(hdr); w.writerows(keep)
        (out / name).write_text(buf.getvalue(), encoding='utf-8')
        prov['files'][name] = {'rows_kept': len(keep), 'source_member_sha256': hashlib.sha256(raw).hexdigest(), 'kept_sha256': hashlib.sha256(buf.getvalue().encode()).hexdigest()}
    old = json.loads((out / 'PROVENANCE.json').read_text()) if (out / 'PROVENANCE.json').exists() else {}
    same = old.get('zip_sha256') == prov['zip_sha256'] and all(old.get('files', {}).get(k, {}).get('kept_sha256') == v['kept_sha256'] for k, v in prov['files'].items())
    if not same:   # unchanged source and rows: keep the original retrieval record
        (out / 'PROVENANCE.json').write_text(json.dumps(prov, indent=1))
    print(json.dumps({'cycle': prov['cycle_effective'], 'rows': {k: v['rows_kept'] for k, v in prov['files'].items()}}))
    return prov


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True); ap.add_argument('--arpt-id')
    a = ap.parse_args(); extract(a.icao.upper(), a.arpt_id)

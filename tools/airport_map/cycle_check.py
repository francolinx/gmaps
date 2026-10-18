"""A6: cycle-current check for an airport's FAA diagram against the FAA d-TPP cycle. Stdlib only.

  python3 tools/airport_map/cycle_check.py --icao KLGA [--today ]

Pass when ALL hold:
  1. today (UTC) is inside the chart's effective window (d-TPP cycles change at 0901Z on the effective date);
  2. the FAA d-TPP metafile for that cycle lists this airport's APD (airport diagram) with the same PDF file name;
  3. the PDF that FAA serves at the recorded URL has the same sha256 as the local copy.
Also reports the next cycle: whether its metafile is published, the APD record, and FAA's useraction code for it
('' = unchanged, 'C' = changed, 'A' = added, 'D' = deleted). Stale (today past effective_to) fails.
Writes data/airports/<icao>/review/cycle_check.json and scores review.json item cycle_current.
"""
import argparse, datetime, hashlib, json, re, sys, time, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UA = {'User-Agent': 'gmaps-airport-map/0.1 (local research)'}
WARN_DAYS = 7


def cycle_window(effective_from, effective_to):
    """d-TPP validity: from 0901Z on effective_from until 0901Z on effective_to (UTC)."""
    f = datetime.datetime.fromisoformat(effective_from).replace(hour=9, minute=1, tzinfo=datetime.timezone.utc)
    t = datetime.datetime.fromisoformat(effective_to).replace(hour=9, minute=1, tzinfo=datetime.timezone.utc)
    return f, t


def is_current(effective_from, effective_to, now):
    """True if `now` (aware datetime) is inside the chart's effective window. Pure; used by tests with a fake now."""
    f, t = cycle_window(effective_from, effective_to)
    return f <= now < t


def next_cycle(cycle):
    yy, nn = int(cycle[:2]), int(cycle[2:])
    return f'{yy:02d}{nn + 1:02d}' if nn < 13 else f'{yy + 1:02d}01'   # 13 AIRAC cycles a year; probed, not assumed


def get(url, timeout=120):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def apd_record(meta_xml, icao):
    root = ET.fromstring(meta_xml)
    for ap in root.iter('airport_name'):
        if ap.get('icao_ident') == icao:
            for r in ap.findall('record'):
                if (r.findtext('chart_code') or '').strip() == 'APD':
                    return root.attrib, {e.tag: (e.text or '').strip() for e in r}
    return root.attrib, None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True); ap.add_argument('--today', help='YYYY-MM-DD (UTC noon) for testing')
    a = ap.parse_args()
    icao = a.icao.upper(); base = REPO / 'data/airports' / icao.lower()
    ch = json.loads((base / 'provenance.json').read_text())['chart']
    now = (datetime.datetime.fromisoformat(a.today).replace(hour=12, tzinfo=datetime.timezone.utc) if a.today
           else datetime.datetime.now(datetime.timezone.utc))
    cyc, pdf_name = ch['cycle'], ch['url'].rsplit('/', 1)[-1]
    res = {'icao': icao, 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'now_utc': now.isoformat(), 'chart': {k: ch[k] for k in ('cycle', 'effective_from', 'effective_to', 'url', 'sha256')}}
    in_window = is_current(ch['effective_from'], ch['effective_to'], now)
    days_left = (cycle_window(ch['effective_from'], ch['effective_to'])[1] - now).total_seconds() / 86400
    res['in_effective_window'] = in_window; res['days_left'] = round(days_left, 2)
    meta_url = f'https://aeronav.faa.gov/d-tpp/{cyc}/xml_data/d-TPP_Metafile.xml'
    attrs, rec = apd_record(get(meta_url), icao)
    res['faa_cycle'] = {'metafile': meta_url, 'cycle': attrs.get('cycle'), 'from_edate': attrs.get('from_edate'), 'to_edate': attrs.get('to_edate'), 'apd_record': rec}
    listed = bool(rec) and rec.get('pdf_name', '').upper() == pdf_name.upper() and attrs.get('cycle') == cyc
    served = get(ch['url'])
    served_sha = hashlib.sha256(served).hexdigest()
    res['served_pdf'] = {'url': ch['url'], 'sha256': served_sha, 'bytes': len(served), 'matches_local': served_sha == ch['sha256']}
    nxt = next_cycle(cyc)
    try:
        nattrs, nrec = apd_record(get(f'https://aeronav.faa.gov/d-tpp/{nxt}/xml_data/d-TPP_Metafile.xml'), icao)
        res['next_cycle'] = {'cycle': nattrs.get('cycle'), 'from_edate': nattrs.get('from_edate'), 'to_edate': nattrs.get('to_edate'), 'published': True,
                             'apd_record': nrec, 'useraction': None if nrec is None else nrec.get('useraction'),
                             'apd_changed': None if nrec is None else nrec.get('useraction', '') != ''}
    except Exception as ex:
        res['next_cycle'] = {'cycle': nxt, 'published': False, 'error': f'{type(ex).__name__} {getattr(ex, "code", "")}'.strip()}
    ok = in_window and listed and served_sha == ch['sha256']
    warnings = []
    if not in_window:
        warnings.append('STALE: today is outside the chart effective window; rebuild from the current cycle')
    elif days_left <= WARN_DAYS:
        warnings.append(f'cycle ends in {days_left:.1f} days')
    if res['next_cycle'].get('apd_changed'):
        warnings.append(f'next cycle {nxt} changes the airport diagram (useraction {res["next_cycle"]["useraction"]!r}); rebuild when it takes effect')
    res['pass'] = ok; res['warnings'] = warnings
    (base / 'review/cycle_check.json').write_text(json.dumps(res, indent=1))
    rv_path = base / 'review.json'; rv = json.loads(rv_path.read_text())
    item = next(i for i in rv['items'] if i['id'] == 'cycle_current')
    item.update(by='tools/airport_map/cycle_check.py', status='pass' if ok else 'fail',
                result={'cycle': cyc, 'in_effective_window': in_window, 'days_left': res['days_left'], 'faa_lists_same_pdf': listed,
                        'served_pdf_matches_local_sha256': served_sha == ch['sha256'], 'next_cycle': res['next_cycle'].get('cycle'),
                        'next_cycle_apd_changed': res['next_cycle'].get('apd_changed'), 'warnings': warnings, 'checked_at': res['checked_at']})
    rv_path.write_text(json.dumps(rv, indent=1, ensure_ascii=False))
    print(json.dumps({k: res[k] for k in ('in_effective_window', 'days_left', 'faa_cycle', 'served_pdf', 'next_cycle', 'pass', 'warnings')}, indent=1))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

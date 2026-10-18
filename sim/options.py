"""Simulator run options (claude/sim-upgrades): the two round-3 mechanisms the first run (F1/F2 in docs/SIM_FINDINGS.md)
did not use, so a run can measure whether they close the callsign misses. Both are OFF by default, so the default run is the
same as the Q5 run.

  traffic list   (--traffic perfect)  the callsigns on frequency, as an ADS-B / flight-plan feed would give them. It is an
                 observation, never voice-derived: gmaps_core.traffic.match_callsigns rewrites a heard callsign only on a unique
                 hit, tags it callsign_repaired and lowers its confidence. Here: every callsign in the scenario's script plus a
                 fixed set of distractors (other traffic on frequency). This is the "perfect" list of the eval harness (no
                 aircraft missing from the feed); a scenario may add more with an optional `traffic:` field.
  Whisper prompt (--whisper-prompt)   the round-3 initial prompt (eval/synthetic/asr_dir.asr_prompt) built for the fictional
                 XSIM airport: every airline telephony name the parser knows (including the fictional SimAir), the ground
                 vehicle words, XSIM's runway designators and NATO taxiway words, and the standard clearance phrases. The same
                 prompt for every scenario: nothing in it is taken from a scenario's script.

Neither option changes the parser, the ledger or the LLM-fallback validation; they only change what those receive.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'lib') not in sys.path:
    sys.path.insert(0, str(ROOT / 'lib'))

# Other traffic on frequency. Chosen so no distractor shares a flight number with a scenario callsign (SIM212, SIM456,
# N6648J, DAL432, RESCUE7, RESCUE9, TRUCK3 ...) and no two distractors share one: a 'same digits' match stays unique.
DISTRACTORS = ['SIM118', 'SIM350', 'SIM907', 'DAL1520', 'AAL2959', 'N312AB', 'OPS2']

DIG = dict(zip('0123456789', 'zero one two three four five six seven eight niner'.split()))


def scenario_callsigns(sc):
    """Callsigns named in the scenario: every pilot/vehicle transmission's callsign, plus an optional `traffic:` list."""
    out = [tx['callsign'] for tx in sc['transmissions'] if tx.get('callsign')]
    out += list(sc.get('traffic') or [])
    return list(dict.fromkeys(out))


def traffic_list(sc, mode):
    """mode 'off' -> None (no list, as the deployed server today); 'perfect' -> scenario callsigns + DISTRACTORS."""
    if mode in (None, 'off'):
        return None
    if mode != 'perfect':
        raise ValueError(f'unknown traffic mode {mode!r}')
    own = scenario_callsigns(sc)
    nums = lambda cs: re.sub(r'\D', '', cs)
    clash = [d for d in DISTRACTORS if any(nums(d) == nums(c) for c in own)]
    if clash:   # never let a distractor make a scenario aircraft's flight number ambiguous
        raise ValueError(f"{sc['id']}: distractor(s) {clash} share a flight number with a scenario callsign")
    return sorted(set(own) | set(DISTRACTORS))


def whisper_prompt(runway_ends=('18', '36'), icao='XSIM'):
    """Round-3 style initial prompt for the fictional airport (one short ATC-style paragraph). Not scenario-specific."""
    from gmaps_core.parser import AIRLINES, VEHICLES
    names = [{'SIMAIR': 'SimAir', 'JETBLUE': 'JetBlue', 'AIRCANADA': 'Air Canada'}.get(k, k.title()) for k in AIRLINES]
    vehicles = sorted({k.title() for k in VEHICLES})
    say = lambda e: ' '.join(DIG[c] for c in re.match(r'0?(\d+)', e).group(1)) + {'L': ' left', 'R': ' right', 'C': ' center'}.get(e[-1], '')
    nato = ('Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliett Kilo Lima Mike November Oscar Papa Quebec Romeo '
            'Sierra Tango Uniform Victor Whiskey X-ray Yankee Zulu')
    return (f"{icao} tower. {', '.join(names)}. {', '.join(vehicles)}. Runway {', runway '.join(say(e) for e in runway_ends)}. "
            f"Taxiway {nato}. Cleared to land, cleared for takeoff, line up and wait, hold short, cross runway, go around, roger, wilco.")

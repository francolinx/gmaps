"""NeMo Gym resources server for the GMAPS parser task. SIMULATED data only.

One episode = one transcribed ATC transmission -> the policy answers with the llm_fallback JSON
{speaker_role, callsign, runway, instruction, taxiways}. The verifier is GMAPS's own evidence rule, not a judge model:
  reward            1.0 when every field matches the gold exactly (record exact match), else 0.0
  reward_components per-field exact match (speaker_role, callsign, runway, instruction, taxiways), json_valid, groundable
  groundable        1.0 when the policy's JSON also passes venue/llm_fallback.py's grounding against the transmission text
                    (runway number heard, flight number heard incl. 7110.65 group form, taxiway words heard, airline named),
                    i.e. the runtime would have accepted it. Scored separately from correctness on purpose.
Gold travels in the task row (built by gym/build_dataset.py from a held-out split); the policy never sees it and this
server never reads eval/truth, eval/labels or any split itself. The task's `gold_rule` says which taxiway gold rule
built it (legacy = the evaluator's rule today; fixed = callsign-aware, DECISIONS N19).

Layout follows `gym env init --profile custom-gym-verifier` of nemo-gym 0.7.0 (verified here). Not run on the Spark yet.
"""
import json
import re
import sys
from pathlib import Path
from typing import Any, ClassVar, Optional

from pydantic import ConfigDict

from nemo_gym.base_resources_server import (
    BaseMultiRewardVerifyResponse,
    BaseResourcesServerConfig,
    BaseVerifyRequest,
    ReverifyMode,
    SimpleResourcesServer,
)
from nemo_gym.verifier_fixture import VerifierFixture

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / 'venue', REPO / 'lib'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

FIELDS = ('speaker_role', 'callsign', 'runway', 'instruction', 'taxiways')
_FB_CACHE: dict = {}


def airport_lists(icao: str):
    """Runway-end identifiers and chart taxiway names: ChartIdentifiers when the airport build exists (the Spark), else the
    committed review files. Same fallback as train/augment_asr_noise.py."""
    base = REPO / 'data/airports' / icao.lower()
    try:
        sys.path.insert(0, str(REPO / 'eval/synthetic'))
        from chart_ids import ChartIdentifiers  # noqa: WPS433
        A = ChartIdentifiers(icao)
        return sorted({e['identifier'] for e in A.ends.values()}), sorted(A.vocab)
    except Exception:
        ends = json.loads((base / 'review/RUNWAY_ENDS.json').read_text())['ends']
        vocab = json.loads((base / 'review/taxiway_vocabulary.json').read_text())['taxiways']
        return sorted(e['id'] for e in ends), sorted(vocab)


def fallback_for(icao: str):
    if icao not in _FB_CACHE:
        import llm_fallback as LF  # noqa: WPS433
        runways, vocab = airport_lists(icao)
        _FB_CACHE[icao] = LF.LLMFallback(runways, vocab)
    return _FB_CACHE[icao]


def extract_json(text: str) -> Optional[dict]:
    """The first JSON object in the policy's text (code fences tolerated). None when there is none."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t, flags=re.I | re.S)
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except Exception:
        pass
    m = re.search(r'\{.*\}', t, flags=re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def norm_runway(x):
    """'04' and '4' are the same runway end (the schema allows both spellings); same rule as eval/parser_baselines.py."""
    if x is None:
        return None
    try:
        from gmaps_core.normalize import canonical_runway  # noqa: WPS433
        c = canonical_runway(str(x))
    except Exception:
        c = None
    return (c or str(x).lstrip('0') or '0').upper()


def field_matches(pred: Optional[dict], gold: dict) -> dict:
    if not isinstance(pred, dict):
        return {k: 0.0 for k in FIELDS}
    out = {}
    for k in FIELDS:
        if k == 'taxiways':
            out[k] = float(list(pred.get(k) or []) == list(gold.get(k) or []))
        elif k == 'runway':
            out[k] = float(norm_runway(pred.get(k)) == norm_runway(gold.get(k)))
        else:
            out[k] = float(pred.get(k) == gold.get(k))
    return out


class GmapsParserResourcesServerConfig(BaseResourcesServerConfig):
    REVERIFY_MODE: ClassVar[ReverifyMode] = ReverifyMode.STATELESS


class GmapsParserVerifyRequest(BaseVerifyRequest):
    model_config = ConfigDict(extra="allow")
    gold: dict
    airport: str
    transmission: str


class GmapsParserVerifyResponse(BaseMultiRewardVerifyResponse):
    model_config = ConfigDict(extra="allow")
    record_exact: float
    json_valid: float
    groundable: float
    grounding_reason: Optional[str] = None
    predicted: Optional[dict] = None


class GmapsParserVerifier:
    async def verify(self, body: GmapsParserVerifyRequest) -> GmapsParserVerifyResponse:
        text = body.response.output_text
        pred = extract_json(text)
        comps = field_matches(pred, body.gold)
        record = float(all(v == 1.0 for v in comps.values()))
        json_valid = float(pred is not None)
        groundable, reason = 0.0, 'no json'
        if pred is not None:
            try:
                fields, reason = fallback_for(body.airport).validate(dict(pred), body.transmission)
                groundable = float(fields is not None and (fields.get('actionable') or reason == 'no actionable content'))
            except Exception as e:  # a malformed prediction is the policy's fault, not an infrastructure failure
                groundable, reason = 0.0, f'validate error: {type(e).__name__}'
        comps.update({'json_valid': json_valid, 'groundable': groundable})
        return GmapsParserVerifyResponse(**body.model_dump(), reward=record, reward_components=comps, record_exact=record,
                                         json_valid=json_valid, groundable=groundable, grounding_reason=reason, predicted=pred)


class GmapsParserResourcesServer(GmapsParserVerifier, SimpleResourcesServer):
    ray_enabled = False

    config: GmapsParserResourcesServerConfig


VERIFIER_FIXTURE = VerifierFixture(
    server_factory=GmapsParserVerifier,
    request_model=GmapsParserVerifyRequest,
    cases_path=Path(__file__).parent / "tests" / "verifier_cases.jsonl",
)


if __name__ == "__main__":
    GmapsParserResourcesServer.run_webserver()

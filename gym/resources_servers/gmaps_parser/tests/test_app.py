import asyncio

from nemo_gym.verifier_fixture import exercise_verifier_fixture

from ..app import VERIFIER_FIXTURE


def test_verifier_fixture() -> None:
    asyncio.run(exercise_verifier_fixture(VERIFIER_FIXTURE, reward_range=(0.0, 1.0), higher_is_better=True, determinism="unknown"))

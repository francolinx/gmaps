"""Meterless H-MEM as runway memory: NOT INTEGRATED.

The Meterless SDK is not on PyPI (pip: no distribution 'meterless'), its documentation host was not reachable from the build
session, and no key or SDK name was supplied, so there is no verified H-MEM client. This class is the wiring point (same
interface as LocalRunwayMemory: put / open_items / close); it raises NotIntegrated rather than imitating Meterless.
tests/test_meterless_engine.py skips the live test with that reason; the agent uses LocalRunwayMemory and its trace says
'engine: local.runway_memory'.
"""
import os


class NotIntegrated(RuntimeError):
    pass


class MeterlessHMEM:
    name = 'meterless.h-mem'

    def __init__(self):
        try:
            import meterless  # noqa: F401  (package name unverified)
        except ImportError:
            raise NotIntegrated('Meterless H-MEM is not integrated: no SDK installed (pip has no "meterless") and no docs reachable')
        if not os.environ.get('METERLESS_API_KEY'):
            raise NotIntegrated('Meterless H-MEM: METERLESS_API_KEY not set')
        raise NotIntegrated('Meterless H-MEM: SDK present but the adapter is not written against verified docs yet')

"""Runway memory engine (Open Agent Hackathon 2026). GMAPS_MEMORY=local|meterless. See runway_memory.py."""
import os


def get_runway_memory(name=None):
    name = name or os.environ.get('GMAPS_MEMORY', 'local')
    from .runway_memory import LocalRunwayMemory
    if name == 'meterless':
        from .meterless_hmem import MeterlessHMEM
        return MeterlessHMEM()
    return LocalRunwayMemory()

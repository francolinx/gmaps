"""Data layer (Open Agent Hackathon 2026): GMAPS_DATA=local|zetaris. See zetaris_store.py."""
import os


def get_store(name=None):
    name = name or os.environ.get('GMAPS_DATA', 'local')
    from .zetaris_store import LocalStore, ZetarisStore
    if name == 'zetaris':
        return ZetarisStore()
    if name == 'local':
        return LocalStore()
    raise ValueError(f'unknown data store {name!r} (local | zetaris)')

"""Runway memory: each ledger reservation and open obligation stored as a memory item keyed by runway; the Evaluator and
the Explainer retrieve the open items for a runway when a new clearance on it arrives.

Interface (the Meterless H-MEM adapter implements the same three methods):
  put(runway, item_id, item)      upsert one item (item: dict with at least 'kind', 'callsign', 'status', 't')
  open_items(runway)              items whose status is open / active / unacknowledged, oldest first
  close(runway, item_id, t)       mark an item closed
LocalRunwayMemory is the in-process fallback; it is what runs unless GMAPS_MEMORY=meterless.
"""
OPEN = {'open', 'active', 'unacknowledged', 'pending', 'expired_unverified'}


class LocalRunwayMemory:
    name = 'local.runway_memory'

    def __init__(self):
        self.items = {}

    def put(self, runway, item_id, item):
        self.items.setdefault(runway, {})[item_id] = dict(item, item_id=item_id, runway=runway)

    def open_items(self, runway):
        return sorted((i for i in self.items.get(runway, {}).values() if i.get('status') in OPEN), key=lambda i: (i.get('t') or 0, i['item_id']))

    def close(self, runway, item_id, t):
        it = self.items.get(runway, {}).get(item_id)
        if it:
            it.update(status='closed', closed_at=t)

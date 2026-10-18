// Chart-cycle status, offline: same rule as tools/airport_map/cycle_check.py is_current().
// FAA d-TPP charts are valid from 0901Z on effective_from until 0901Z on effective_to (UTC).
function chartCycleStatus(effectiveFrom, effectiveTo, now) {
  const at0901 = d => new Date(d + 'T09:01:00Z');
  const from = at0901(effectiveFrom), to = at0901(effectiveTo);
  return { current: now >= from && now < to, expired: now >= to, notYetEffective: now < from,
           daysLeft: (to - now) / 86400000, from: from.toISOString(), to: to.toISOString() };
}
if (typeof module !== 'undefined') module.exports = { chartCycleStatus };

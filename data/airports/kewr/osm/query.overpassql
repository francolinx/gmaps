[out:json][timeout:90][bbox:40.6634,-74.1927,40.7164,-74.1445];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

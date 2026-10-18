[out:json][timeout:90][bbox:37.5943,-122.4054,37.6407,-122.3451];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

[out:json][timeout:90][bbox:37.6423,-122.1418,37.6745,-122.1007];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

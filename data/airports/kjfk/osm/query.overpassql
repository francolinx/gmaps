[out:json][timeout:90][bbox:40.61,-73.8287,40.6698,-73.7429];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

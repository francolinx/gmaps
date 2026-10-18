[out:json][timeout:90][bbox:40.7600,-73.8950,40.7920,-73.8480];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

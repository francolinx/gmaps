[out:json][timeout:90][bbox:33.6083,-84.46,33.6615,-84.3935];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

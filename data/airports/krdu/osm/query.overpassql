[out:json][timeout:90][bbox:35.8526,-78.814,35.9058,-78.7622];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

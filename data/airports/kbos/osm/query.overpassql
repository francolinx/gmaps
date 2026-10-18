[out:json][timeout:90][bbox:42.3366,-71.0353,42.3903,-70.9757];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

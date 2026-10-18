[out:json][timeout:90][bbox:33.8566,-84.3182,33.8962,-84.2851];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;

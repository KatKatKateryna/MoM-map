// ── URL hash  (#zoom/lat/lng) ─────────────────────────────────────────────────
function parseHash() {
  const m = /^#([\d.]+)\/([-\d.]+)\/([-\d.]+)$/.exec(location.hash);
  if (!m) return null;
  const zoom = parseFloat(m[1]), lat = parseFloat(m[2]), lng = parseFloat(m[3]);
  if (isNaN(zoom) || isNaN(lat) || isNaN(lng)) return null;
  return { zoom, lat, lng };
}

function updateHash() {
  const { lng, lat } = map.getCenter();
  const zoom = map.getZoom();
  history.replaceState(null, '', `${location.pathname}${location.search}#${zoom.toFixed(2)}/${lat.toFixed(4)}/${lng.toFixed(4)}`);
}

window.addEventListener('hashchange', () => {
  const view = parseHash();
  if (view) map.easeTo({ center: [view.lng, view.lat], zoom: view.zoom });
});

const initialView = parseHash() || DEFAULT_VIEW;

const ALERT_COLOR = {
  Warning:     '#f57c00',
  Watch:       '#fbc02d',
  Advisory:    '#fff176',
  Information: '#43a047',
};
const NO_DATA_COLOR = '#e0e0e0';

// Layer metadata key of extra layers drawn above the watershed layers
const ABOVE_WATERSHEDS = 'mom:above-watersheds';

// ── PMTiles protocol ──────────────────────────────────────────────────────────
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile.bind(protocol));

// ── Map ───────────────────────────────────────────────────────────────────────
const map = new maplibregl.Map({
  container: 'map',
  style: `https://tiles.openfreemap.org/styles/${DARK_THEME ? 'dark' : 'positron'}`,
  center: [initialView.lng, initialView.lat],
  zoom: initialView.zoom,
  minZoom: 2,
  maxZoom: 10,
  dragRotate: false,
  pitchWithRotate: false,
  maxPitch: 0,
});

// Positron's water is grey; tint it light blue (the dark theme keeps its own colors)
const WATER_COLOR = '#bbdbe4';
const WATERWAY_COLOR = '#8ec2d7';
map.on('style.load', () => {
  if (!DARK_THEME) {
    map.setPaintProperty('water', 'fill-color', WATER_COLOR);
    map.setPaintProperty('waterway', 'line-color', WATERWAY_COLOR);
  }
  // No ocean / sea / gulf / strait names (lake names stay); the label layers differ per style
  for (const id of ['water_name_point_label', 'water_name_line_label', 'water_name']) {
    if (!map.getLayer(id)) continue;
    map.setFilter(id, ['all', map.getFilter(id), ['!', ['in', ['get', 'class'], ['literal', ['ocean', 'sea', 'bay', 'strait']]]]]);
  }
});

map.touchZoomRotate.disableRotation();
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
map.on('moveend', updateHash);
function preferredScaleUnit() {
  try {
    const ms = new Intl.Locale(navigator.language).measurementSystem;
    return (ms === 'us' || ms === 'uksystem') ? 'imperial' : 'metric';
  } catch (_) { return 'metric'; }
}
map.addControl(new maplibregl.ScaleControl({ unit: preferredScaleUnit() }), 'bottom-left');

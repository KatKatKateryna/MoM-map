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

// ── PMTiles protocol ──────────────────────────────────────────────────────────
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile.bind(protocol));

// ── Map ───────────────────────────────────────────────────────────────────────
const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      osm: {
        type: 'raster',
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxzoom: 19,
      },
    },
    layers: [
      { id: 'osm', type: 'raster', source: 'osm' },
    ],
  },
  center: [initialView.lng, initialView.lat],
  zoom: initialView.zoom,
  minZoom: 2,
  maxZoom: 9,
  dragRotate: false,
  pitchWithRotate: false,
  maxPitch: 0,
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

// ── GloFAS gauges (point pmtiles in data/tiles/gauges, update_gauges.py) ──────
// Only with SHOW_GAUGES. One snapshot per day, listed in data/tiles/gauges/metadata.json;
// the map shows the one of the watershed snapshot picked in the header dropdown
// (SNAPSHOT_PICK_EVENT). Drawn on top of every layer, with its own legend toggle above the
// Alerts one.
const GAUGES_DIR = 'data/tiles/gauges/';
// Alert_level: the highest GloFAS return period threshold exceeded
const GAUGE_LEVELS = [
  [3, '#b71c1c', 'Above 20-year threshold'],
  [2, '#f57c00', 'Above 5-year threshold'],
  [1, '#fbc02d', 'Above 2-year threshold'],
  [0, '#9e9e9e', 'Below 2-year threshold'],
];
// Every column of the gauge files, the forecast ones first; missing values show as No data
const GAUGE_FIELDS = ['Alert_level', 'Days_until_peak', 'GloFAS_2yr', 'GloFAS_5yr', 'GloFAS_20yr',
  'pfaf_id', 'Lat', 'Lon', 'Upstream area', 'Forecast Date', 'max_EPS',
  'Point No', 'Station', 'Basin', 'Country'];

// Like the watershed snapshots, every gauge snapshot gets its source and layer set up
// front, ids `gauges-${index}`, and a switch loads the new one transparent before the
// swap, so the points do not blink
let gaugeSnapshots = []; // index 0 = most recent
let currentGaugeLayer = null;
let pendingGaugeLayer = null;
let gaugeToggle = null;
let gaugeDescription = null;

const gaugeVisibility = () => gaugeToggle.checked ? 'visible' : 'none';

// The newest gauge snapshot not after `time`: the newest one when `time` is past them all,
// the oldest one when it is before them all. updated_at ('YYYY-MM-DD HH:MM UTC') sorts as text
function gaugeSnapshotFor(time) {
  return gaugeSnapshots.find(s => time == null || s.updated_at <= time) ?? gaugeSnapshots.at(-1);
}

function setGaugesShown(layer, shown) {
  map.setPaintProperty(layer, 'circle-opacity', shown ? 1 : 0);
  map.setPaintProperty(layer, 'circle-stroke-opacity', shown ? 1 : 0);
}

// Every pick in the header dropdown (watersheds.js selectSnapshot); picks before
// initGauges are not lost, it starts with pickedSnapshotTime's gauges
document.addEventListener(SNAPSHOT_PICK_EVENT, () => { if (gaugeSnapshots.length) showGauges(); });

function showGauges() {
  const snap = gaugeSnapshotFor(pickedSnapshotTime);
  const layer = `${GAUGES_LAYER}-${snap.index}`;
  // A newer pick replaces a snapshot that is still loading
  if (pendingGaugeLayer && pendingGaugeLayer !== layer) {
    map.setLayoutProperty(pendingGaugeLayer, 'visibility', 'none');
  }
  pendingGaugeLayer = layer === currentGaugeLayer ? null : layer;
  if (!pendingGaugeLayer) return;

  const swap = () => {
    if (pendingGaugeLayer !== layer) return map.off('idle', swap);
    if (gaugeToggle.checked && !map.isSourceLoaded(layer)) return;
    map.off('idle', swap);

    // Show the new snapshot and hide the old one in the same frame
    if (currentGaugeLayer) popup.remove();
    const oldLayer = currentGaugeLayer;
    currentGaugeLayer = layer;
    pendingGaugeLayer = null;
    setGaugesShown(layer, true);
    map.setLayoutProperty(layer, 'visibility', gaugeVisibility());
    gaugeDescription.textContent = `River gauge forecast of ${formatUpdatedAt(snap.updated_at)}.`;
    if (oldLayer) {
      setGaugesShown(oldLayer, false);
      map.setLayoutProperty(oldLayer, 'visibility', 'none');
    }
  };
  // Drawn while still transparent, since tiles only load for visible layers; with the
  // gauges toggled off there is nothing to load, so it swaps at once
  loadSource(layer).then(() => {
    if (pendingGaugeLayer !== layer) return;
    if (!gaugeToggle.checked) return swap();
    map.setLayoutProperty(layer, 'visibility', 'visible');
    map.on('idle', swap);
  });
}

function addGaugeLayer(snap) {
  const id = `${GAUGES_LAYER}-${snap.index}`;
  // No url yet, so nothing is fetched until the snapshot is picked (loadSource)
  map.addSource(id, { type: 'vector' });
  snapshotUrls[id] = `pmtiles://${GAUGES_DIR}${snap.file}`;
  map.addLayer({
    id,
    type: 'circle',
    source: id,
    'source-layer': 'gauges',
    metadata: { [GAUGES_KIND]: true },
    layout: {
      visibility: 'none',
      // Higher alerts drawn over lower ones
      'circle-sort-key': ['coalesce', ['to-number', ['get', 'Alert_level']], -1],
    },
    paint: {
      'circle-color': ['match', ['to-number', ['get', 'Alert_level']],
        ...GAUGE_LEVELS.flatMap(([level, color]) => [level, color]), NO_DATA_COLOR],
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 4, 6, 6, 10, 9],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 2, 1, 6, 1.5, 10, 2],
      // Transparent until shown, without transitions (as WS_SHOWN_PAINT)
      'circle-opacity': 0,
      'circle-opacity-transition': { duration: 0 },
      'circle-stroke-opacity': 0,
      'circle-stroke-opacity-transition': { duration: 0 },
    },
  });
  // Only the snapshot on screen takes clicks, not one still loading transparent
  map.on('click', id, e => { if (id === currentGaugeLayer) onGaugeClick(e); });
  map.on('mouseenter', id, () => { if (id === currentGaugeLayer) map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
}

function initGauges(snapshots) {
  gaugeSnapshots = snapshots.slice().sort((a, b) => a.index - b.index);
  const section = document.createElement('section');
  section.className = 'side-panel__section';
  section.innerHTML = `
    <label class="side-panel__heading legend-toggle">
      <input type="checkbox" checked>
      <span>GloFAS gauges</span>
    </label>
    <p class="legend-description"></p>
    <div class="legend-body">${GAUGE_LEVELS.map(([, color, label]) =>
      `<div class="leg"><div class="swatch" style="background:${color}"></div>${label}</div>`).join('')}</div>`;
  document.getElementById('alerts-section').before(section);
  gaugeToggle = section.querySelector('input');
  gaugeDescription = section.querySelector('.legend-description');

  gaugeToggle.addEventListener('change', () => {
    if (!gaugeToggle.checked) popup.remove();
    if (currentGaugeLayer) map.setLayoutProperty(currentGaugeLayer, 'visibility', gaugeVisibility());
    section.classList.toggle('legend-off', !gaugeToggle.checked);
  });

  for (const snap of gaugeSnapshots) addGaugeLayer(snap);
  showGauges();
}

function onGaugeClick(e) {
  const p = e.features[0].properties;
  const level = GAUGE_LEVELS.find(([l]) => l === Number(p.Alert_level));
  const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const fmt = (k, v) => {
    if (v == null || v === '') return 'No data';
    if (k === 'Upstream area') return `${Number(v).toLocaleString()} km²`;
    return v;
  };
  const rows = GAUGE_FIELDS.map(k =>
    `<div class="pop-row"><span class="pop-key">${esc(k)}</span><span>${esc(fmt(k, p[k]))}</span></div>`
  ).join('');
  // remove() first: the closed watershed popup clears its highlight
  popup.remove();
  popup.setLngLat(e.lngLat).setHTML(`
    <div class="pop-title">${esc(p.Station || 'Gauge')}</div>
    ${level ? `<div><span class="pop-badge" style="background:${level[1]}">${esc(level[2])}</span></div>` : ''}
    ${rows}
  `).addTo(map);
}

if (SHOW_GAUGES) {
  fetch(`${GAUGES_DIR}metadata.json`)
    .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
    .then(meta => {
      if (!meta.snapshots?.length) return;
      mapLoaded.then(() => initGauges(meta.snapshots));
    })
    .catch(err => console.warn('Could not load gauge metadata:', err));
}

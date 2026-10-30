// ── GloFAS gauges (point pmtiles in data/tiles/gauges, update_gauges.py) ──────
// Only with SHOW_GAUGES. One snapshot per day, listed in data/tiles/gauges/metadata.json;
// the map shows the newest one. Drawn on top of every layer, with its own legend toggle
// above the Alerts one.
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

function initGauges(snapshots) {
  // index 0 = most recent
  const snap = snapshots.slice().sort((a, b) => a.index - b.index)[0];
  const section = document.createElement('section');
  section.className = 'side-panel__section';
  section.innerHTML = `
    <label class="side-panel__heading legend-toggle">
      <input type="checkbox" checked>
      <span>GloFAS gauges</span>
    </label>
    <p class="legend-description">River gauge forecast of ${formatUpdatedAt(snap.updated_at)}.</p>
    <div class="legend-body">${GAUGE_LEVELS.map(([, color, label]) =>
      `<div class="leg"><div class="swatch" style="background:${color}"></div>${label}</div>`).join('')}</div>`;
  document.getElementById('alerts-section').before(section);
  const toggle = section.querySelector('input');
  const visibility = () => toggle.checked ? 'visible' : 'none';

  toggle.addEventListener('change', () => {
    if (!toggle.checked) popup.remove();
    if (map.getLayer(GAUGES_LAYER)) map.setLayoutProperty(GAUGES_LAYER, 'visibility', visibility());
    section.classList.toggle('legend-off', !toggle.checked);
  });

  map.addSource(GAUGES_LAYER, { type: 'vector', url: `pmtiles://${GAUGES_DIR}${snap.file}` });
  map.addLayer({
    id: GAUGES_LAYER,
    type: 'circle',
    source: GAUGES_LAYER,
    'source-layer': 'gauges',
    layout: {
      visibility: visibility(),
      // Higher alerts drawn over lower ones
      'circle-sort-key': ['coalesce', ['to-number', ['get', 'Alert_level']], -1],
    },
    paint: {
      'circle-color': ['match', ['to-number', ['get', 'Alert_level']],
        ...GAUGE_LEVELS.flatMap(([level, color]) => [level, color]), NO_DATA_COLOR],
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 4, 6, 6, 10, 9],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 2, 1, 6, 1.5, 10, 2],
    },
  });
}

map.on('click', GAUGES_LAYER, (e) => {
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
});
map.on('mouseenter', GAUGES_LAYER, () => { map.getCanvas().style.cursor = 'pointer'; });
map.on('mouseleave', GAUGES_LAYER, () => { map.getCanvas().style.cursor = ''; });

if (SHOW_GAUGES) {
  fetch(`${GAUGES_DIR}metadata.json`)
    .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
    .then(meta => {
      if (!meta.snapshots?.length) return;
      mapLoaded.then(() => initGauges(meta.snapshots));
    })
    .catch(err => console.warn('Could not load gauge metadata:', err));
}

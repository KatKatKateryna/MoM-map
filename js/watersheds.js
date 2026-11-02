// ── Metadata (updated-at + legend) ────────────────────────────────────────────
function formatUpdatedAt(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) UTC$/.exec(s);
  if (!m) return s;
  const [, y, mo, d, h, mi] = m;
  const date = new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi));
  const dateStr = date.toLocaleDateString(undefined, { dateStyle: 'medium', timeZone: 'UTC' });
  const timeStr = date.toLocaleTimeString(undefined, { timeStyle: 'short', timeZone: 'UTC', hourCycle: 'h23' });
  return `${dateStr} at ${timeStr} UTC`;
}

// ── Snapshots (pmtiles source swap driven by the header dropdown) ─────────────
const OUTLINE_COLOR = [
  'match', ['get', 'Alert'],
  'Warning',     '#e65100',
  'Watch',       '#f57f17',
  'Advisory',    '#f9a825',
  'Information', '#2e7d32',
  '#bdbdbd'
];
const OUTLINE_WIDTH = ['interpolate', ['linear'], ['zoom'], 4, 0.3, 8, 0.8, 12, 1.5];
// A shown snapshot's borders flash white and wider, then fade to normal within FLASH_MS
const FLASH_WIDTH = ['interpolate', ['linear'], ['zoom'], 4, 0.75, 8, 1.5, 12, 2.5];
const FLASH_OPACITY = 0.25;
const FLASH_MS = 1000;

// Every snapshot gets its source and layer set up front, ids `ws-${index}-${name}`:
// switching then only changes visibility and paint, since adding layers would make
// MapLibre re-parse tiles and blink. A source gets its url, and loads, on its first pick
const WS_LAYERS = ['hit', 'highlight-outline', 'highlight', 'flash', 'outline', 'fill'];
// Layers are transparent until their snapshot is shown. Without transitions: the
// default 300ms fade-in would show neither snapshot while the old one is hidden
const WS_SHOWN_PAINT = { fill: ['fill-opacity', 0.55], outline: ['line-opacity', 1] };
// Tags watershed layers 'fill' or 'top', so extra layers can go below or between them
const WS_KIND = 'mom:ws';
const firstWsLayer = kind => map.getStyle().layers.find(l => l.metadata?.[WS_KIND] === kind)?.id;
const alertsToggle = document.getElementById('alerts-toggle');

let currentSource = null;
let pendingSource = null;
// updated_at of the snapshot picked in the dropdown, announced with SNAPSHOT_PICK_EVENT on
// document; gauges.js shows the gauges of that date. An event, not a call into gauges.js,
// since the first pick can come before that script has run
let pickedSnapshotTime = null;
const SNAPSHOT_PICK_EVENT = 'mom:snapshot-pick';
const snapshotUrls = {}; // source -> pmtiles url
const sourceReady = {};  // source -> promise, resolved once the source has its url's tile list

// Gives a source its url on the snapshot's first pick
function loadSource(source) {
  return sourceReady[source] ??= new Promise(resolve => {
    const onData = e => {
      if (e.sourceId !== source || e.sourceDataType !== 'metadata') return;
      map.off('sourcedata', onData);
      resolve();
    };
    map.on('sourcedata', onData);
    map.getSource(source).setUrl(snapshotUrls[source]);
  });
}

// Id of a layer of the snapshot on screen, e.g. wsLayer('fill')
const wsLayer = name => currentSource && `${currentSource}-${name}`;

function setSnapshotVisibility(source, visibility) {
  for (const name of WS_LAYERS) map.setLayoutProperty(`${source}-${name}`, 'visibility', visibility);
}

function setSnapshotShown(source, shown) {
  for (const [name, [prop, value]] of Object.entries(WS_SHOWN_PAINT)) {
    map.setPaintProperty(`${source}-${name}`, prop, shown ? value : 0);
  }
}

// The Alerts checkbox shows or hides every watershed layer, the click layer too
function applyAlertsVisibility() {
  if (currentSource) setSnapshotVisibility(currentSource, alertsToggle.checked ? 'visible' : 'none');
  document.getElementById('alerts-section').classList.toggle('legend-off', !alertsToggle.checked);
}

alertsToggle.addEventListener('change', () => {
  if (!alertsToggle.checked) popup.remove();
  applyAlertsVisibility();
});

function addSnapshotLayers(source) {
  // Only the colored fill goes under extra layers drawn above the watersheds
  // (extra-sources.js `above`); borders, highlight and the click layer stay on top
  const fillBeforeId = map.getStyle().layers.find(l => l.metadata?.[ABOVE_WATERSHEDS])?.id
    ?? firstWsLayer('top') ?? topBeforeId();
  const beforeId = topBeforeId();
  const fill = { layout: { visibility: 'none' }, metadata: { [WS_KIND]: 'fill' } };
  const top = { layout: { visibility: 'none' }, metadata: { [WS_KIND]: 'top' } };

  map.addLayer({
    ...fill,
    id: `${source}-fill`,
    type: 'fill',
    source,
    'source-layer': 'watersheds',
    paint: {
      'fill-color': [
        'match', ['get', 'Alert'],
        'Warning',     '#f57c00',
        'Watch',       '#fbc02d',
        'Advisory',    '#f0f250',
        'Information', '#43a047',
        NO_DATA_COLOR
      ],
      'fill-opacity': 0,
      'fill-opacity-transition': { duration: 0 },
    },
  }, fillBeforeId);
  map.addLayer({
    ...top,
    id: `${source}-outline`,
    type: 'line',
    source,
    'source-layer': 'watersheds',
    paint: {
      'line-color': OUTLINE_COLOR,
      'line-width': OUTLINE_WIDTH,
      'line-opacity': 0,
      'line-opacity-transition': { duration: 0 },
    },
  }, beforeId);
  // White copy of the borders over the colored ones, only seen during flashOutline
  map.addLayer({
    ...top,
    id: `${source}-flash`,
    type: 'line',
    source,
    'source-layer': 'watersheds',
    paint: { 'line-color': '#ffffff', 'line-width': OUTLINE_WIDTH, 'line-opacity': 0 },
  }, beforeId);
  map.addLayer({
    ...top,
    id: `${source}-highlight`,
    type: 'fill',
    source,
    'source-layer': 'watersheds',
    filter: ['==', 'pfaf_id', ''],
    paint: { 'fill-color': '#ffffff', 'fill-opacity': 0.35 },
  }, beforeId);
  map.addLayer({
    ...top,
    id: `${source}-highlight-outline`,
    type: 'line',
    source,
    'source-layer': 'watersheds',
    filter: ['==', 'pfaf_id', ''],
    paint: { 'line-color': '#ffffff', 'line-width': 2.5 },
  }, beforeId);
  // Invisible top fill that takes hover and clicks, so whatever is drawn
  // between it and ws-fill does not get in the way
  map.addLayer({
    ...top,
    id: `${source}-hit`,
    type: 'fill',
    source,
    'source-layer': 'watersheds',
    paint: { 'fill-color': '#000000', 'fill-opacity': 0 },
  }, beforeId);
  map.on('click', `${source}-hit`, onWatershedClick);
  map.on('mouseenter', `${source}-hit`, showPointer);
  map.on('mouseleave', `${source}-hit`, hidePointer);
}

// Shows the flash layer at once, then once that frame is drawn lets it fade and narrow
// back to the normal borders. Only opacity and a zoom-based width change: the colored
// border's per-alert color could not transition, and changing it would re-parse tiles
function flashOutline(source) {
  const id = `${source}-flash`;
  const set = (opacity, width, duration) => {
    map.setPaintProperty(id, 'line-opacity-transition', { duration });
    map.setPaintProperty(id, 'line-width-transition', { duration });
    map.setPaintProperty(id, 'line-opacity', opacity);
    map.setPaintProperty(id, 'line-width', width);
  };
  set(FLASH_OPACITY, FLASH_WIDTH, 0);
  map.once('render', () => set(0, OUTLINE_WIDTH, FLASH_MS));
}

function selectSnapshot(entry) {
  const source = `ws-${entry.index}`;
  pickedSnapshotTime = entry.updated_at;
  document.dispatchEvent(new Event(SNAPSHOT_PICK_EVENT));
  // A newer pick replaces a snapshot that is still loading
  if (pendingSource && pendingSource !== source) setSnapshotVisibility(pendingSource, 'none');
  pendingSource = source === currentSource ? null : source;
  if (!pendingSource) return;

  const swap = () => {
    if (pendingSource !== source) return map.off('idle', swap);
    if (!map.isSourceLoaded(source)) return;
    map.off('idle', swap);

    // Show the new snapshot and hide the old one in the same frame
    popup.remove();
    const oldSource = currentSource;
    currentSource = source;
    pendingSource = null;
    setSnapshotShown(source, true);
    flashOutline(source);
    applyAlertsVisibility();
    if (oldSource) {
      setSnapshotShown(oldSource, false);
      setSnapshotVisibility(oldSource, 'none');
    }
  };
  // Drawn while still transparent, since tiles only load for visible layers; not before
  // the source has its tile list, or MapLibre asks it for tiles it has no url for
  loadSource(source).then(() => {
    if (pendingSource !== source) return;
    setSnapshotVisibility(source, 'visible');
    map.on('idle', swap);
  });
}

function initSnapshots(meta) {
  const snapshots = (meta.snapshots || []).slice().sort((a, b) => a.index - b.index).slice(0, MAX_SNAPSHOTS);
  if (!snapshots.length) return;

  const selector = document.getElementById('snapshot-selector');
  const select = document.getElementById('snapshot-select');
  for (const entry of snapshots) {
    const opt = document.createElement('option');
    opt.value = entry.index;
    opt.textContent = formatUpdatedAt(entry.updated_at);
    select.appendChild(opt);
  }
  for (const entry of snapshots) {
    const source = `ws-${entry.index}`;
    // No url yet, so nothing is fetched until the snapshot is picked
    map.addSource(source, { type: 'vector' }); // no `tiles` either: it would override the url's
    snapshotUrls[source] = `pmtiles://${entry.url}`;
    addSnapshotLayers(source);
  }
  select.value = snapshots[0].index; // index 0 = most recent
  selector.classList.add('ready');
  selectSnapshot(snapshots[0]);

  select.addEventListener('change', () => {
    const entry = snapshots.find(s => s.index === +select.value);
    selectSnapshot(entry);
  });
}

// Snapshots live in the MoM_Alerts repo; raw.githubusercontent.com serves them
// with CORS and range requests, which pmtiles needs
const TILES_BASE_URL = 'https://raw.githubusercontent.com/Blu-H/MoM_Alerts/main/Alerts/tiles/';

fetch(new URL('metadata.json', TILES_BASE_URL))
  .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
  .then(meta => {
    // metadata.json lists files relative to its own folder: turn them into full URLs
    for (const s of meta.snapshots || []) s.url = new URL(s.file, TILES_BASE_URL).href;
    mapLoaded.then(() => initSnapshots(meta));
    updateLegend();
  })
  .catch(err => console.warn('Could not load snapshot metadata:', err));

function updateLegend() {
  const order = ['Warning', 'Watch', 'Advisory', 'Information'];
  const el = document.getElementById('legend');
  el.innerHTML = '';

  for (const level of order) {
    const color = ALERT_COLOR[level];
    el.innerHTML += `<div class="leg"><div class="swatch" style="background:${color}"></div>${level}</div>`;
  }
  el.innerHTML += `<div class="leg"><div class="swatch" style="background:#e0e0e0;border-color:#aaa"></div>No data</div>`;
}

// ── Click popup ───────────────────────────────────────────────────────────────
const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '340px', closeOnClick: false });

// A click on a gauge point opens the gauge's popup instead (gauges.js)
const gaugeAt = point => currentGaugeLayer &&
  map.queryRenderedFeatures(point, { layers: [currentGaugeLayer] }).length > 0;

function onWatershedClick(e) {
  if (gaugeAt(e.point)) return;
  const p = e.features[0].properties;
  const alert = p.Alert || 'No data';
  const color = ALERT_COLOR[alert] ?? '#999';

  const isShown = ([, v]) => v != null && v !== 'null' && v !== 'None' && v !== '';

  const watershedRows = [
    ['Watershed ID',  p.pfaf_id],
    ['Name',          p.name],
    ['Regions',       p.Admin1_names],
    ['Watershed area', p.area_km2_x != null ? `${Math.round(p.area_km2_x).toLocaleString()} km²` : null],
    ['Riverine risk', p.rfr_score_x != null ? `${p.rfr_score_x} / 100` : null],
    ['Coastal risk',  p.cfr_score_x != null ? `${p.cfr_score_x} / 100` : null],
  ].filter(isShown);

  const snapshotRows = [
    ['Status',        p.Status],
    ['Hazard score',  p.Hazard_Score != null ? `${p.Hazard_Score} / 100` : null],
  ].filter(isShown);

  const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const rowsHtml = rows => rows.map(([k, v]) =>
    `<div class="pop-row"><span class="pop-key">${esc(k)}</span><span>${esc(v)}</span></div>`
  ).join('');

  popup.setLngLat(e.lngLat).setHTML(`
    <div class="pop-title">${esc(p.name || 'Watershed')}</div>
    <div><span class="pop-badge" style="background:${color}">${esc(alert)}</span></div>
    ${rowsHtml(watershedRows)}
    ${watershedRows.length && snapshotRows.length ? '<hr class="pop-divider">' : ''}
    ${rowsHtml(snapshotRows)}
  `).addTo(map);

  map.setFilter(wsLayer('highlight'), ['==', 'pfaf_id', p.pfaf_id]);
  map.setFilter(wsLayer('highlight-outline'), ['==', 'pfaf_id', p.pfaf_id]);
}

popup.on('close', () => {
  if (!currentSource) return;
  map.setFilter(wsLayer('highlight'), ['==', 'pfaf_id', '']);
  map.setFilter(wsLayer('highlight-outline'), ['==', 'pfaf_id', '']);
});

map.on('click', (e) => {
  const features = currentSource ? map.queryRenderedFeatures(e.point, { layers: [wsLayer('hit')] }) : [];
  if (!features.length && !gaugeAt(e.point)) popup.remove();
});

function showPointer() { map.getCanvas().style.cursor = 'pointer'; }
function hidePointer() { map.getCanvas().style.cursor = ''; }

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
function addSnapshotLayers(entry) {
  for (const id of ['ws-highlight-outline', 'ws-highlight', 'ws-outline', 'ws-fill']) {
    if (map.getLayer(id)) map.removeLayer(id);
  }
  if (map.getSource('ws')) map.removeSource('ws');

  map.addSource('ws', { type: 'vector', url: `pmtiles://data/tiles/${entry.file}` });

  map.addLayer({
    id: 'ws-fill',
    type: 'fill',
    source: 'ws',
    'source-layer': 'watersheds',
    paint: {
      'fill-color': [
        'match', ['get', 'alert'],
        'Warning',     '#f57c00',
        'Watch',       '#fbc02d',
        'Advisory',    '#fff176',
        'Information', '#43a047',
        NO_DATA_COLOR
      ],
      'fill-opacity': 0.55,
    },
  });
  map.addLayer({
    id: 'ws-outline',
    type: 'line',
    source: 'ws',
    'source-layer': 'watersheds',
    paint: {
      'line-color': [
        'match', ['get', 'alert'],
        'Warning',     '#e65100',
        'Watch',       '#f57f17',
        'Advisory',    '#f9a825',
        'Information', '#2e7d32',
        '#bdbdbd'
      ],
      'line-width': ['interpolate', ['linear'], ['zoom'], 4, 0.3, 8, 0.8, 12, 1.5],
    },
  });
  map.addLayer({
    id: 'ws-highlight',
    type: 'fill',
    source: 'ws',
    'source-layer': 'watersheds',
    filter: ['==', 'pfaf_id', ''],
    paint: { 'fill-color': '#ffffff', 'fill-opacity': 0.35 },
  });
  map.addLayer({
    id: 'ws-highlight-outline',
    type: 'line',
    source: 'ws',
    'source-layer': 'watersheds',
    filter: ['==', 'pfaf_id', ''],
    paint: { 'line-color': '#ffffff', 'line-width': 2.5 },
  });
}

function selectSnapshot(entry) {
  popup.remove();
  addSnapshotLayers(entry);
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
  select.value = snapshots[0].index; // index 0 = most recent
  selector.classList.add('ready');
  selectSnapshot(snapshots[0]);

  select.addEventListener('change', () => {
    const entry = snapshots.find(s => s.index === +select.value);
    selectSnapshot(entry);
  });
}

fetch('data/tiles/metadata.json')
  .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
  .then(meta => {
    const run = () => initSnapshots(meta);
    if (map.loaded()) run(); else map.once('load', run);
    updateLegend();
  })
  .catch(err => console.warn('Could not load snapshot metadata:', err));

function updateLegend() {
  const order = ['Warning', 'Watch', 'Advisory', 'Information'];
  const el = document.getElementById('legend');
  el.innerHTML = '<h4>Alert Level</h4>';

  for (const level of order) {
    const color = ALERT_COLOR[level];
    el.innerHTML += `<div class="leg"><div class="swatch" style="background:${color}"></div>${level}</div>`;
  }
  el.innerHTML += `<div class="leg"><div class="swatch" style="background:#e0e0e0;border-color:#aaa"></div>No data</div>`;
}

// ── Click popup ───────────────────────────────────────────────────────────────
const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '340px', closeOnClick: false });

map.on('click', 'ws-fill', (e) => {
  const p = e.features[0].properties;
  const alert = p.alert || 'No data';
  const color = ALERT_COLOR[alert] ?? '#999';

  const isShown = ([, v]) => v != null && v !== 'null' && v !== 'None' && v !== '';

  const watershedRows = [
    ['Watershed ID',  p.pfaf_id],
    ['Name',          p.name],
    ['Regions',       p.regions],
    ['Watershed area', p.area_km2 != null ? `${Math.round(p.area_km2).toLocaleString()} km²` : null],
    ['Riverine risk', p.riverine_risk != null ? `${p.riverine_risk} / 100` : null],
    ['Coastal risk',  p.coastal_risk != null ? `${p.coastal_risk} / 100` : null],
  ].filter(isShown);

  const snapshotRows = [
    ['Status',        p.status],
    ['Days to peak',  p.days_until_peak != null ? p.days_until_peak : null],
    ['Hazard score',  p.hazard_score != null ? `${p.hazard_score} / 100` : null],
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

  map.setFilter('ws-highlight', ['==', 'pfaf_id', p.pfaf_id]);
  map.setFilter('ws-highlight-outline', ['==', 'pfaf_id', p.pfaf_id]);
});

popup.on('close', () => {
  map.setFilter('ws-highlight', ['==', 'pfaf_id', '']);
  map.setFilter('ws-highlight-outline', ['==', 'pfaf_id', '']);
});

map.on('click', (e) => {
  const features = map.queryRenderedFeatures(e.point, { layers: ['ws-fill'] });
  if (!features.length) popup.remove();
});

map.on('mouseenter', 'ws-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
map.on('mouseleave', 'ws-fill', () => { map.getCanvas().style.cursor = ''; });

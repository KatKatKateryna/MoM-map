// ── Extra sources (raster or vector pmtiles listed in EXTRA_SOURCES) ──────────
// Tile type and vector layer names are read from each archive's header, so an
// entry can be just a path. Extra layers are drawn below the watershed layers.
// `color` (vector only) is either a single color or a data-driven style:
//   { gradient: 'prop', stops: [0, '#ffffcc', 100, '#bd0026'] }       numeric, interpolated
//   { category: 'prop', values: { A: '#e41a1c', B: '#377eb8' }, other: '#ccc' }
// stops / values / other are optional: missing stops spread GRADIENT_RAMP over the
// property's min..max, missing values give each known value a CATEGORY_PALETTE
// color (both read from the tippecanoe tilestats in the archive metadata).
// `name` sets the legend title (defaults to the archive's name or file name);
// for raster sources `color` is only the legend swatch and `brightness` (0..1)
// darkens the tiles' own colors. `url` can be a list of archives (e.g. a raster
// split by region): they are drawn as one layer with one legend entry and toggle.
const GRADIENT_RAMP = ['#ffffcc', '#fd8d3c', '#bd0026'];
const CATEGORY_PALETTE = ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', '#ffff33', '#a65628', '#f781bf', '#999999'];

// Turns a `color` spec into concrete stops / values, filling in the defaults
function resolveColor(color, stats) {
  if (typeof color === 'string') return { kind: 'single', color };
  const other = color.other ?? NO_DATA_COLOR;
  if (color.gradient) {
    let stops = color.stops;
    if (!stops) {
      const attr = stats?.find(a => a.attribute === color.gradient);
      const min = attr?.min ?? 0;
      const max = attr?.max > min ? attr.max : min + 1;
      stops = GRADIENT_RAMP.flatMap((c, k) => [min + (max - min) * k / (GRADIENT_RAMP.length - 1), c]);
    }
    return { kind: 'gradient', property: color.gradient, stops, other };
  }
  if (color.category) {
    let values = color.values;
    if (!values) {
      const known = stats?.find(a => a.attribute === color.category)?.values ?? [];
      if (!known.length) console.warn(`No tilestats values for "${color.category}"; all features get the "other" color`);
      values = Object.fromEntries(known.map((v, k) => [v, CATEGORY_PALETTE[k % CATEGORY_PALETTE.length]]));
    }
    return { kind: 'category', property: color.category, values, other };
  }
  throw new Error(`Unknown color spec: ${JSON.stringify(color)}`);
}

function colorExpr(c) {
  if (c.kind === 'single') return c.color;
  if (c.kind === 'gradient') {
    return ['case', ['has', c.property],
      ['interpolate', ['linear'], ['to-number', ['get', c.property]], ...c.stops], c.other];
  }
  const pairs = Object.entries(c.values).flat();
  if (!pairs.length) return c.other;
  return ['match', ['to-string', ['get', c.property]], ...pairs, c.other];
}

function renderExtraLegend(section, title, c, layerIds) {
  const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const fmt = v => v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  const leg = (color, label) => `<div class="leg"><div class="swatch" style="background:${color}"></div>${esc(label)}</div>`;

  let body = '';
  if (c.kind === 'gradient') {
    const min = c.stops[0], max = c.stops[c.stops.length - 2];
    const pct = v => max > min ? (v - min) / (max - min) * 100 : 0;
    const css = [];
    for (let k = 0; k < c.stops.length; k += 2) css.push(`${c.stops[k + 1]} ${pct(c.stops[k])}%`);
    body = `<div class="gradient-bar" style="background:linear-gradient(to right, ${css.join(', ')})"></div>
      <div class="gradient-labels"><span>${fmt(min)}</span><span>${fmt(max)}</span></div>`;
  } else if (c.kind === 'category') {
    body = Object.entries(c.values).map(([v, color]) => leg(color, v)).join('') + leg(c.other, 'Other');
  }

  section.innerHTML = `
    <label class="side-panel__heading legend-toggle">
      <input type="checkbox" checked>
      ${c.kind === 'single' ? `<span class="swatch" style="background:${c.color}"></span>` : ''}
      <span>${esc(title)}</span>
    </label>
    <div class="legend-body">${body}</div>`;
  section.querySelector('input').addEventListener('change', e => {
    const visibility = e.target.checked ? 'visible' : 'none';
    for (const layerId of layerIds) map.setLayoutProperty(layerId, 'visibility', visibility);
    section.classList.toggle('legend-off', !e.target.checked);
  });
  section.hidden = false;
}

async function addExtraSource(entry, i, section) {
  const { url, name, opacity = 0.7, color: colorSpec = '#4a7fb5', brightness } = typeof entry === 'string' ? { url: entry } : entry;
  // Several archives (e.g. a raster split by region) share one legend and toggle
  const urls = [].concat(url);
  const archives = urls.map(u => new pmtiles.PMTiles(u));
  archives.forEach(a => protocol.add(a));
  const headers = await Promise.all(archives.map(a => a.getHeader()));
  const metadatas = await Promise.all(archives.map(async a => await a.getMetadata() ?? {}));
  const title = name ?? metadatas[0].name ?? urls[0].split('/').pop().replace(/\.pmtiles$/, '');
  const beforeId = map.getLayer('ws-fill') ? 'ws-fill' : undefined;
  const layerIds = [];
  let legendColor;

  urls.forEach((url, k) => {
    const header = headers[k], metadata = metadatas[k];
    const id = urls.length > 1 ? `extra-${i}-${k}` : `extra-${i}`;

    if (header.tileType === pmtiles.TileType.Mvt) {
      const { vector_layers = [], tilestats } = metadata;
      map.addSource(id, { type: 'vector', url: `pmtiles://${url}` });
      for (const { id: sourceLayer } of vector_layers) {
        const stats = tilestats?.layers?.find(l => l.layer === sourceLayer)?.attributes;
        const resolved = resolveColor(colorSpec, stats);
        legendColor ??= resolved;
        const color = colorExpr(resolved);
        const common = { source: id, 'source-layer': sourceLayer };
        const layers = [
          { ...common, id: `${id}-${sourceLayer}-fill`, type: 'fill',
            filter: ['==', ['geometry-type'], 'Polygon'],
            paint: { 'fill-color': color, 'fill-opacity': opacity * 0.5 } },
          { ...common, id: `${id}-${sourceLayer}-line`, type: 'line',
            filter: ['in', ['geometry-type'], ['literal', ['LineString', 'Polygon']]],
            paint: { 'line-color': color, 'line-opacity': opacity, 'line-width': 1 } },
          { ...common, id: `${id}-${sourceLayer}-point`, type: 'circle',
            filter: ['==', ['geometry-type'], 'Point'],
            paint: { 'circle-color': color, 'circle-opacity': opacity, 'circle-radius': 3 } },
        ];
        for (const layer of layers) { map.addLayer(layer, beforeId); layerIds.push(layer.id); }
      }
    } else {
      map.addSource(id, { type: 'raster', url: `pmtiles://${url}`, tileSize: 256 });
      // brightness (0..1) darkens the archive's own colors
      const paint = { 'raster-opacity': opacity };
      if (brightness != null) paint['raster-brightness-max'] = brightness;
      map.addLayer({ id, type: 'raster', source: id, paint }, beforeId);
      layerIds.push(id);
      legendColor = { kind: 'single', color: typeof colorSpec === 'string' ? colorSpec : '#4a7fb5' };
    }
  });

  renderExtraLegend(section, title, legendColor ?? resolveColor(colorSpec), layerIds);
}

// Legend sections are created up front so they keep the EXTRA_SOURCES order
// whichever archive answers first
function loadExtraSources() {
  const footer = document.querySelector('#panel .side-panel__section--bottom');
  EXTRA_SOURCES.forEach((entry, i) => {
    const section = document.createElement('section');
    section.className = 'side-panel__section';
    section.hidden = true;
    footer.before(section);
    addExtraSource(entry, i, section).catch(err => {
      section.remove();
      console.warn('Could not load extra source:', entry, err);
    });
  });
}
if (map.loaded()) loadExtraSources(); else map.once('load', loadExtraSources);

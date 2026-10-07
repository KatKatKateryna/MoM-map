// ── Extra sources (raster or vector pmtiles listed in EXTRA_SOURCES) ──────────
// Tile type and vector layer names are read from each archive's header, so an
// entry can be just a path. Extra layers are drawn below the watershed layers, in
// EXTRA_SOURCES order (the first at the bottom) whichever archive loads first; the
// legend lists them the other way round, top layer first.
// `color` (vector only) is either a single color or a data-driven style:
//   { gradient: 'prop', stops: [0, '#ffffcc', 100, '#bd0026'] }       numeric, interpolated
//   { category: 'prop', values: { A: '#e41a1c', B: '#377eb8' }, other: '#ccc' }
// stops / values / other are optional: missing stops spread GRADIENT_RAMP over the
// property's min..max, missing values give each known value a CATEGORY_PALETTE
// color (both read from the tippecanoe tilestats in the archive metadata).
// `name` sets the legend title (defaults to the archive's name or file name) and
// `description` an optional short text shown under it;
// for raster sources `color` is only the legend swatch, `brightness` (0..1)
// darkens the tiles' own colors and `brightnessMin` (0..1) lightens them (each
// channel becomes min + value * (max - min)) and `resampling: 'nearest'` keeps pixels sharp
// when zoomed in past the archive's max zoom (default 'linear' blends them). `url` can be a list of archives (e.g. a raster
// split by region): they are drawn as one layer with one legend entry and toggle.
// `above: true` draws the layer over the watershed fill instead of under it; the
// watershed borders, highlight and click layer stay on top.
// `visible: false` starts the layer hidden, with its legend toggle off.
// `values` is for value rasters (scripts/build_value_pmtiles.py: values stored as terrain-RGB):
// they are colored at runtime and get a min-max slider that hides cells outside the range:
//   values: { stops: [1, '#f2f0f7', 1000, '#54278f'], scale: 'log', min, max, unit: 'people',
//             initialMin, initialMax, decimals }
// stops: value, color pairs (ascending); scale 'log', 'linear' or 'pow' (with exponent,
// default 3: quicker through low values than linear, gentler than log) spaces the legend
// bar and the slider; min / max bound the slider (default: first / last stop); initialMin /
// initialMax set where its handles start (default: the ends, which show everything).
// Slider values are rounded down to `decimals` decimal places (default 0: whole numbers),
// then to 2 significant digits. `slider: false` drops the slider and shows the first stop's
// color as a swatch (e.g. for a raster of 1s), or with `labels` (one per stop) one legend
// entry per stop, for classes: values: { stops: [1, '#ccc', 2, '#e6550d'], labels: ['A', 'B'],
// slider: false }; a null label leaves that class out of the legend.
// Layer metadata key holding the EXTRA_SOURCES index, to insert layers in that order
const EXTRA_ORDER = 'mom:extra-order';
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

// ── Value rasters ─────────────────────────────────────────────────────────────
// valueScale (archive metadata `value_scale`) maps a value to what the tiles store:
// for 'log' the index of the value on a log scale (scripts/build_value_pmtiles.py)
function resolveValues(values, valueScale) {
  const { stops, scale = 'linear', exponent = 3, unit = '', initialMin, initialMax, decimals = 0 } = values;
  const log = valueScale?.type === 'log';
  const toStored = log
    ? v => Math.log(v) / Math.log(valueScale.ratio) + valueScale.offset
    : v => v;
  return { kind: 'values', stops, scale, exponent, unit, toStored, initialMin, initialMax, decimals,
    // Lowest stored value that is data: tiles store 0 for no data
    lowestStored: log ? 0.5 : Number.MIN_VALUE,
    min: values.min ?? stops[0], max: values.max ?? stops[stops.length - 2] };
}

// Position (0..1) of a value along the legend bar / slider, and back
function toUnit(c, v) {
  if (c.scale === 'log') return (Math.log(v) - Math.log(c.min)) / (Math.log(c.max) - Math.log(c.min));
  const t = (v - c.min) / (c.max - c.min);
  return c.scale === 'pow' ? Math.max(t, 0) ** (1 / c.exponent) : t;
}
function fromUnit(c, u) {
  if (c.scale === 'log') return Math.exp(Math.log(c.min) + u * (Math.log(c.max) - Math.log(c.min)));
  return c.min + (c.scale === 'pow' ? u ** c.exponent : u) * (c.max - c.min);
}

const hexToRgb = h => [1, 3, 5].map(k => parseInt(h.slice(k, k + 2), 16));
// The ramp's color at v, interpolated linearly between stops like MapLibre does
function rampColor(stops, v) {
  if (v <= stops[0]) return stops[1];
  for (let k = 2; k < stops.length; k += 2) {
    if (v <= stops[k]) {
      const t = (v - stops[k - 2]) / (stops[k] - stops[k - 2]);
      const a = hexToRgb(stops[k - 1]), b = hexToRgb(stops[k + 1]);
      return `rgb(${a.map((x, n) => Math.round(x + (b[n] - x) * t)).join(',')})`;
    }
  }
  return stops[stops.length - 1];
}

// color-relief color: the ramp between values lo and hi, transparent outside it
// (lo 0 shows every cell with data). Works on stored values (['elevation'] of the
// tiles), so stops and bounds are converted
function reliefColor(c, lo, hi) {
  const stops = c.stops.map((x, k) => k % 2 ? x : c.toStored(x));
  const sLo = lo > 0 ? c.toStored(lo) : c.lowestStored, sHi = c.toStored(hi);
  // MapLibre stores stop positions at 1/256 precision: keep the edge stops apart
  const eps = 1 / 32;
  const inner = [];
  for (let k = 0; k < stops.length; k += 2) {
    if (stops[k] > sLo && stops[k] < sHi) inner.push(stops[k], stops[k + 1]);
  }
  return ['interpolate', ['linear'], ['elevation'],
    sLo - eps, 'rgba(0,0,0,0)', sLo, rampColor(stops, sLo),
    ...inner,
    sHi, rampColor(stops, sHi), sHi + eps, 'rgba(0,0,0,0)'];
}

// Slider values: rounded down to `decimals` places (smaller values round to 0), then
// to 2 significant digits
const sliderValue = (v, decimals = 0) => {
  const f = 10 ** decimals;
  return v < 1 / f ? 0 : Number((Math.floor(v * f) / f).toPrecision(2));
};
const fmtValue = (v, decimals = 0) => v.toLocaleString(undefined, v >= 1000
  ? { notation: 'compact', maximumFractionDigits: 1 }
  : { maximumFractionDigits: decimals });

const BAR_SAMPLES = 48;

// Legend body of a value raster: the ramp with two trim handles on it; the trimmed-off
// ends of the ramp are greyed out
function valuesLegend(c, layerIds, esc) {
  // The bar is sampled with the map's own coloring (interpolated on stored values), so
  // each point of it has exactly the color of the cells at the value under it
  const stored = c.stops.map((x, k) => k % 2 ? x : c.toStored(x));
  const css = [];
  for (let k = 0; k <= BAR_SAMPLES; k++) {
    const u = k / BAR_SAMPLES;
    css.push(`${rampColor(stored, c.toStored(fromUnit(c, u)))} ${(u * 100).toFixed(1)}%`);
  }
  const body = document.createElement('div');
  body.innerHTML = `
    <div class="range-trim">
      <div class="gradient-bar" style="background:linear-gradient(to right, ${css.join(', ')})"></div>
      <div class="trim-mask trim-mask--lo"></div>
      <div class="trim-mask trim-mask--hi"></div>
      <input type="range" min="0" max="1000" value="0" aria-label="Hide values below">
      <input type="range" min="0" max="1000" value="1000" aria-label="Hide values above">
    </div>
    <div class="gradient-labels"><span></span><span></span></div>`;
  const [loInput, hiInput] = body.querySelectorAll('input');
  const [loLabel, hiLabel] = body.querySelectorAll('.gradient-labels span');
  const [loMask, hiMask] = body.querySelectorAll('.trim-mask');
  const unit = c.unit ? ` ${esc(c.unit)}` : '';
  // Start positions; the ends show everything
  if (c.initialMin != null) loInput.value = Math.round(toUnit(c, Math.max(c.initialMin, c.min)) * 1000);
  if (c.initialMax != null) hiInput.value = Math.round(toUnit(c, Math.min(c.initialMax, c.max)) * 1000);
  const update = () => {
    // Handles cannot cross
    if (+loInput.value > +hiInput.value) {
      if (document.activeElement === loInput) loInput.value = hiInput.value; else hiInput.value = loInput.value;
    }
    // The left end shows every cell with data (also those below the slider minimum)
    const lo = +loInput.value === 0 ? 0 : sliderValue(fromUnit(c, loInput.value / 1000), c.decimals);
    const hi = sliderValue(fromUnit(c, hiInput.value / 1000), c.decimals);
    loMask.style.width = `${loInput.value / 10}%`;
    hiMask.style.width = `${100 - hiInput.value / 10}%`;
    loLabel.innerHTML = `${+loInput.value === 0 ? '' : '≥ '}${fmtValue(lo, c.decimals)}${unit}`;
    hiLabel.innerHTML = `${+hiInput.value === 1000 ? '' : '≤ '}${fmtValue(hi, c.decimals)}${+hiInput.value === 1000 ? '+' : ''}${unit}`;
    // The top of the slider shows everything above it too
    const color = reliefColor(c, lo, +hiInput.value === 1000 ? 1e9 : hi);
    for (const layerId of layerIds) map.setPaintProperty(layerId, 'color-relief-color', color);
  };
  loInput.addEventListener('input', update);
  hiInput.addEventListener('input', update);
  update();
  return body;
}

function renderExtraLegend(section, title, c, layerIds, description, visible) {
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
  } else if (c.kind === 'list') {
    body = c.items.map(([color, label]) => leg(color, label)).join('');
  }

  section.innerHTML = `
    <label class="side-panel__heading legend-toggle">
      <input type="checkbox"${visible ? ' checked' : ''}>
      ${c.kind === 'single' ? `<span class="swatch" style="background:${c.color}"></span>` : ''}
      <span>${esc(title)}</span>
    </label>
    ${description ? `<p class="legend-description">${esc(description)}</p>` : ''}
    <div class="legend-body">${body}</div>`;
  if (c.kind === 'values') section.querySelector('.legend-body').appendChild(valuesLegend(c, layerIds, esc));
  section.querySelector('input').addEventListener('change', e => {
    const visibility = e.target.checked ? 'visible' : 'none';
    for (const layerId of layerIds) map.setLayoutProperty(layerId, 'visibility', visibility);
    section.classList.toggle('legend-off', !e.target.checked);
  });
  section.classList.toggle('legend-off', !visible);
  section.hidden = false;
}

async function addExtraSource(entry, i, section) {
  const { url, name, description, opacity = 0.7, color: colorSpec = '#4a7fb5', brightness, brightnessMin, resampling, above = false, visible = true, values } = typeof entry === 'string' ? { url: entry } : entry;
  // Several archives (e.g. a raster split by region) share one legend and toggle
  const urls = [].concat(url);
  const archives = urls.map(u => new pmtiles.PMTiles(u));
  archives.forEach(a => protocol.add(a));
  const headers = await Promise.all(archives.map(a => a.getHeader()));
  const metadatas = await Promise.all(archives.map(async a => await a.getMetadata() ?? {}));
  const title = name ?? metadatas[0].name ?? urls[0].split('/').pop().replace(/\.pmtiles$/, '');
  // Below the watershed layers, or with `above` over the watershed fill but under its
  // borders (tagged, so watersheds.js keeps re-adding the fill underneath); either
  // way under the later EXTRA_SOURCES entries already added
  const later = map.getStyle().layers.find(l =>
    l.metadata?.[EXTRA_ORDER] > i && !!l.metadata[ABOVE_WATERSHEDS] === above)?.id;
  const beforeId = later ?? (above
    ? (map.getLayer('ws-outline') ? 'ws-outline' : undefined)
    : (map.getLayer('ws-fill') ? 'ws-fill' : undefined));
  const tag = {
    layout: { visibility: visible ? 'visible' : 'none' },
    metadata: { [EXTRA_ORDER]: i, ...(above && { [ABOVE_WATERSHEDS]: true }) },
  };
  const layerIds = [];
  let legendColor;
  const valueRamp = values && resolveValues(values, metadatas[0].value_scale);

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
        for (const layer of layers) { map.addLayer({ ...layer, ...tag }, beforeId); layerIds.push(layer.id); }
      }
    } else if (valueRamp) {
      // Values as terrain-RGB, colored (and filtered by the legend slider) at runtime
      map.addSource(id, { type: 'raster-dem', url: `pmtiles://${url}`, tileSize: 256, encoding: metadata.encoding ?? 'terrarium' });
      map.addLayer({ id, type: 'color-relief', source: id, ...tag,
        // nearest: color each data pixel as is; linear blends neighbours (also with
        // empty cells), which turns filtered pixels into blobs and stripes
        paint: { 'color-relief-color': reliefColor(valueRamp, valueRamp.min, 1e9), 'color-relief-opacity': opacity,
          resampling: 'nearest' } }, beforeId);
      layerIds.push(id);
      legendColor = values.slider !== false ? valueRamp
        : values.labels ? { kind: 'list', items: values.labels.map((label, k) => [values.stops[2 * k + 1], label])
            .filter(([, label]) => label != null) }
        : { kind: 'single', color: values.stops[1] };
    } else {
      map.addSource(id, { type: 'raster', url: `pmtiles://${url}`, tileSize: 256 });
      // brightness (0..1) darkens the archive's own colors, brightnessMin (0..1) lightens them
      const paint = { 'raster-opacity': opacity };
      if (brightness != null) paint['raster-brightness-max'] = brightness;
      if (brightnessMin != null) paint['raster-brightness-min'] = brightnessMin;
      if (resampling) paint['raster-resampling'] = resampling;
      map.addLayer({ id, type: 'raster', source: id, paint, ...tag }, beforeId);
      layerIds.push(id);
      legendColor = { kind: 'single', color: typeof colorSpec === 'string' ? colorSpec : '#4a7fb5' };
    }
  });

  renderExtraLegend(section, title, legendColor ?? resolveColor(colorSpec), layerIds, description, visible);
}

// Legend sections are created up front, top layer (last entry) first, so they keep
// that order whichever archive answers first
function loadExtraSources() {
  const footer = document.querySelector('#panel .side-panel__section--bottom');
  [...EXTRA_SOURCES.entries()].reverse().forEach(([i, entry]) => {
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

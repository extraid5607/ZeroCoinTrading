// Small helpers shared by every view.
// Rule: text that comes from the API is only ever inserted with textContent (through h()),
// never as HTML.

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'on') for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

export function mount(el, ...kids) {
  while (el.firstChild) el.removeChild(el.firstChild);
  append(el, kids);
  return el;
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ---------------------------------------------------------------- numbers and dates
const MINUS = '−';

/** Price with sensible decimals for any size of coin (BTC 64,210.50 ... PEPE 0.00001234). */
export function price(n) {
  if (n === null || n === undefined || !Number.isFinite(n)) return '–';
  const a = Math.abs(n);
  const d = a >= 1000 ? 2 : a >= 1 ? 4 : a >= 0.1 ? 5 : a >= 0.001 ? 6 : 8;
  let s = n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  if (s.includes('.')) {
    const keep = a >= 1 ? 2 : 0;
    const [i, f] = s.split('.');
    s = keep ? `${i}.${f.replace(/0+$/, '').padEnd(keep, '0')}` : (f.replace(/0+$/, '') ? `${i}.${f.replace(/0+$/, '')}` : i);
  }
  return s;
}

export function pct(n, { sign = false, decimals = 1 } = {}) {
  if (n === null || n === undefined || !Number.isFinite(n)) return '–';
  const abs = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  if (n < 0 && Number(abs.replace(/[,.]/g, '')) !== 0) return `${MINUS}${abs}%`;
  return `${sign && n > 0 ? '+' : ''}${abs}%`;
}

export function compactUsd(n) {
  if (!Number.isFinite(n)) return '–';
  const a = Math.abs(n);
  if (a >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "10 Oct 2026" for daily candles, "10 Oct, 08:00 UTC" for intraday. */
export function date(ts, intraday = false) {
  if (!ts) return '–';
  const d = new Date(ts * 1000);
  const base = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  if (intraday) return `${base}, ${String(d.getUTCHours()).padStart(2, '0')}:00 UTC`;
  return `${base} ${d.getUTCFullYear()}`;
}

export function candlesText(n, tf) {
  const unit = tf === '1d' ? 'day' : tf === '4h' ? '4-hour candle' : 'hour candle';
  if (tf === '1d') return n === 1 ? '1 day' : `${n} days`;
  return n === 1 ? `1 ${unit}` : `${n} ${unit}s`;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ---------------------------------------------------------------- icons (drawn with the DOM, no HTML strings)
const SVG = 'http://www.w3.org/2000/svg';
const ICONS = {
  scanner: [['circle', { cx: 11, cy: 11, r: 6.5 }], ['path', { d: 'M16 16l5 5' }]],
  coin: [['path', { d: 'M6 8v10M6 4v2M12 6v6M12 16v4M18 10v8M18 4v3' }], ['rect', { x: 4, y: 8, width: 4, height: 7, rx: 1 }],
         ['rect', { x: 10, y: 9, width: 4, height: 7, rx: 1 }], ['rect', { x: 16, y: 8, width: 4, height: 8, rx: 1 }]],
  star: [['path', { d: 'M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9 6.8 19.7l1-5.9L3.5 9.7l5.9-.8z' }]],
  more: [['circle', { cx: 5, cy: 12, r: 1.6 }], ['circle', { cx: 12, cy: 12, r: 1.6 }], ['circle', { cx: 19, cy: 12, r: 1.6 }]],
  sun: [['circle', { cx: 12, cy: 12, r: 4 }], ['path', { d: 'M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2' }]],
  moon: [['path', { d: 'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z' }]],
  back: [['path', { d: 'M15 5l-7 7 7 7' }]],
  activity: [['path', { d: 'M22 12h-4l-3 9L9 3l-3 9H2' }]],
};

export function icon(name, filled = false) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '22');
  svg.setAttribute('height', '22');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'icon' + (filled ? ' filled' : ''));
  for (const [tag, attrs] of ICONS[name] || []) {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.append(el);
  }
  return svg;
}

/** A row of mutually exclusive buttons. */
export function segmented(options, value, onChange, label) {
  const box = h('div', { class: 'seg', role: 'group', 'aria-label': label });
  for (const [val, text] of options) {
    box.append(h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(val === value),
      on: {
        click: () => {
          for (const b of box.children) b.setAttribute('aria-pressed', 'false');
          box.children[options.findIndex((o) => o[0] === val)].setAttribute('aria-pressed', 'true');
          onChange(val);
        },
      },
    }, text));
  }
  return box;
}

export function errorBox(message, retry) {
  return h('div', { class: 'note note-error', role: 'alert' },
    h('p', null, message),
    retry ? h('button', { type: 'button', class: 'btn', on: { click: retry } }, 'Try again') : null);
}

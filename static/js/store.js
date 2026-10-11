// Small client-side state. Browser storage can be blocked (private windows, strict settings), so every
// read and write is wrapped and the site works without it (values then live only until the page closes).
const memory = {};

function load(key, fallback) {
  try {
    const raw = localStorage.getItem('zc_' + key);
    if (raw !== null) return JSON.parse(raw);
  } catch { /* blocked or corrupt */ }
  return key in memory ? memory[key] : fallback;
}

function save(key, value) {
  memory[key] = value;
  try { localStorage.setItem('zc_' + key, JSON.stringify(value)); } catch { /* blocked */ }
}

export const state = {
  meta: null,
  filters: { tf: load('tf', '1d'), pattern: '', tier: '', sector: '', q: '' },
  lastCoin: load('lastCoin', null),
  detailTf: null,
};

export function setTf(tf) { state.filters.tf = tf; save('tf', tf); }
export function setLastCoin(base) { state.lastCoin = base; save('lastCoin', base); }

// ---- watchlist (saved in this browser) ----------------------------------------------------------------
const MAX_WATCH = 60;

export function watchlist() {
  const v = load('watch', []);
  return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, MAX_WATCH) : [];
}

export function isWatched(base) { return watchlist().includes(base); }

/** Returns the new state, or null if the list is full. */
export function toggleWatch(base) {
  const list = watchlist();
  const i = list.indexOf(base);
  if (i >= 0) { list.splice(i, 1); save('watch', list); return false; }
  if (list.length >= MAX_WATCH) return null;
  list.push(base);
  save('watch', list);
  return true;
}

// ---- theme --------------------------------------------------------------------------------------------------
export function currentTheme() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t === 'light' || t === 'dark') return t;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function setTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('zc_theme', t); } catch { /* blocked */ }
}

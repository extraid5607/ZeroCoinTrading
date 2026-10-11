// App shell: theme button, bottom navigation, hash routing, and one place to show errors.
import { h, mount, icon, errorBox } from './util.js';
import { get } from './api.js';
import { state, currentTheme, setTheme } from './store.js';
import * as scanner from './views/scanner.js';
import * as detail from './views/detail.js';
import * as watchlist from './views/watchlist.js';
import * as trading from './views/trading.js';
import * as more from './views/more.js';

const view = document.getElementById('view');
const nav = document.getElementById('nav');
const ROUTES = { scanner, coin: detail, watchlist, trading, more };
let cleanup = null;
let token = 0;

function parse() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query = ''] = raw.split('?');
  const [name = 'scanner', arg = ''] = path.split('/');
  let decoded = '';
  try { decoded = decodeURIComponent(arg); } catch { decoded = ''; }
  return { name: ROUTES[name] ? name : 'scanner', arg: decoded, params: new URLSearchParams(query) };
}

function drawNav(active) {
  const items = [
    ['scanner', 'Scanner', 'scanner', '#/scanner'],
    ['coin', 'Chart', 'coin', state.lastCoin ? `#/coin/${state.lastCoin}` : '#/coin'],
    ['watchlist', 'Watchlist', 'star', '#/watchlist'],
    ['trading', 'Auto-Trade', 'activity', '#/trading'],
    ['more', 'More', 'more', '#/more'],
  ];
  mount(nav, items.map(([id, label, ic, href]) =>
    h('a', { href, 'aria-current': id === active ? 'page' : null }, icon(ic), h('span', null, label))));
}

function drawThemeButton() {
  const btn = document.getElementById('theme-toggle');
  mount(btn, icon(currentTheme() === 'dark' ? 'sun' : 'moon'));
  btn.title = currentTheme() === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
}

async function render() {
  const route = parse();
  const mod = ROUTES[route.name];
  const mine = ++token;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  view.className = route.name === 'coin' || route.name === 'more' ? 'wide' : '';
  mount(view, h('p', { class: 'loading' }, 'Loading…'));
  drawNav(route.name);
  try {
    const result = await mod.render(view, { route, ok: () => mine === token });
    if (mine === token) cleanup = result || null;
    else if (typeof result === 'function') result();
  } catch (err) {
    if (mine === token) mount(view, errorBox(err && err.message ? err.message : 'Something went wrong.', render));
  }
  if (mine === token) { window.scrollTo(0, 0); }
}

document.getElementById('theme-toggle').addEventListener('click', () => {
  setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  drawThemeButton();
  if (parse().name === 'coin') render();            // the chart reads its colours when it is created
});

window.addEventListener('hashchange', () => { render(); view.focus({ preventScroll: true }); });

async function updateLiveFeedStatus() {
  try {
    const h = await get('/api/health');
    const titleEl = document.getElementById('live-feed-title');
    const detailsEl = document.getElementById('live-feed-details');
    if (!titleEl || !detailsEl) return;
    const coins = h.universe ? h.universe.coins : 0;
    const phase = h.phase || 'idle';
    titleEl.textContent = `CCXT Real-Time (${coins} coins)`;
    detailsEl.textContent = `Status: ${phase.charAt(0).toUpperCase() + phase.slice(1)}`;
  } catch {
    /* ignore network glitch */
  }
}

async function boot() {
  drawThemeButton();
  drawNav(parse().name);
  try {
    state.meta = await get('/api/meta');
  } catch (err) {
    mount(view, errorBox(err.message || 'Cannot load the site.', boot));
    return;
  }
  if (!state.meta.timeframes.includes(state.filters.tf)) state.filters.tf = state.meta.timeframes[0];
  if (!location.hash) history.replaceState(null, '', '#/scanner');
  updateLiveFeedStatus();
  setInterval(updateLiveFeedStatus, 15000);
  render();
}

boot();

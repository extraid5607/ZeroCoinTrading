import { h, mount, segmented, errorBox, debounce, date } from '../util.js';
import { get, post } from '../api.js';
import { state, setTf } from '../store.js';

const TIER_TEXT = { A: 'A · deep', B: 'B · medium', C: 'C · thin' };
const REFRESH_MS = 30 * 1000;

export async function render(root, { ok }) {
  const f = state.filters;
  const meta = state.meta;
  const summary = h('p', { class: 'count-line' });
  const list = h('div');
  const btc = h('div');

  const scanBtn = h('button', {
    type: 'button',
    class: 'btn',
    style: 'padding: 4px 12px; font-size: 0.82rem; min-height: 32px; border-radius: 8px;',
    on: {
      click: async () => {
        scanBtn.disabled = true;
        scanBtn.textContent = 'Scanning…';
        try {
          await post('/api/scan/trigger', {}, { tf: f.tf });
        } catch { /* ignore */ }
        await load();
        scanBtn.disabled = false;
        scanBtn.textContent = '↻ Scan Now';
      }
    }
  }, '↻ Scan Now');

  const select = (label, key, options) => {
    const el = h('select', { class: 'field', 'aria-label': label, on: { change: () => { f[key] = el.value; load(); } } },
      options.map(([v, t]) => h('option', { value: v, selected: v === f[key] }, t)));
    return el;
  };
  const search = h('input', {
    class: 'field', type: 'search', placeholder: 'Search coin', maxlength: '15', 'aria-label': 'Search a coin',
    autocomplete: 'off', value: f.q,
    on: { input: debounce(() => { search.value = search.value.replace(/[^A-Za-z0-9]/g, ''); f.q = search.value; load(); }, 250) },
  });
  const patterns = [['', 'All patterns'], ...Object.entries(meta.patterns)];
  const tiers = [['', 'All liquidity'], ...meta.tiers.map((t) => [t, TIER_TEXT[t]])];
  const sectors = [['', 'All sectors'], ...meta.sectors.map((s) => [s, s])];

  const filters = h('div', { class: 'filters' },
    meta.timeframes.length > 1
      ? segmented(meta.timeframes.map((t) => [t, t.toUpperCase()]), f.tf, (v) => { setTf(v); load(); }, 'Timeframe')
      : null,
    h('div', { class: 'filter-row' }, select('Pattern', 'pattern', patterns), select('Liquidity tier', 'tier', tiers)),
    h('div', { class: 'filter-row' }, select('Sector', 'sector', sectors), search));

  mount(root,
    h('div', { class: 'page-head', style: 'display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px;' },
      h('div', null, h('h1', null, 'Live Pattern Scanner'), summary),
      scanBtn),
    btc, filters, list);

  async function loadBtc() {
    try {
      const m = await get('/api/market');
      if (!ok()) return;
      mount(btc, h('div', { class: 'btc-strip' },
        h('span', { class: `dot ${m.state}` }),
        h('p', null, h('strong', null, `Bitcoin trend: ${m.state}. `), m.text)));
    } catch { /* the banner is optional */ }
  }

  async function load() {
    try {
      const d = await get('/api/scan', { tf: f.tf, pattern: f.pattern, tier: f.tier, sector: f.sector, q: f.q });
      if (!ok()) return;
      const filtered = f.pattern || f.tier || f.sector || f.q;
      if (!d.latest_ts) {
        summary.textContent = '';
        mount(list, h('div', { class: 'note' },
          h('p', null, 'The first scan has not finished yet.'),
          h('p', { class: 'muted' }, 'The site downloads coin history and scans it when it starts. Come back in a few minutes.'),
          h('button', { type: 'button', class: 'btn', on: { click: load } }, 'Check again')));
        return;
      }
      summary.textContent = `${d.count} ${d.count === 1 ? 'coin' : 'coins'}${filtered ? ` of ${d.total}` : ''} with a pattern · latest ${f.tf.toUpperCase()} candle ${date(d.latest_ts, f.tf !== '1d')}`;
      if (!d.items.length) {
        mount(list, h('p', { class: 'empty' }, filtered ? 'No coins match these filters.' : 'No coins are on a pattern right now.'));
        return;
      }
      mount(list, h('div', { class: 'rows', role: 'list' }, d.items.map(row)));
    } catch (err) {
      if (ok()) mount(list, errorBox(err.message, load));
    }
  }

  function row(x) {
    const short = x.direction === 'short';
    return h('a', { class: 'row', role: 'listitem', href: `#/coin/${x.base}?tf=${f.tf}` },
      h('span', { class: 'coin-cell' }, h('span', { class: 'coin-sym', text: x.base }), h('span', { class: 'coin-name', text: x.name })),
      h('span', { class: short ? 'pat-cell short' : 'pat-cell' },
        h('span', { class: 'pat-main', text: x.pattern_name }),
        x.also.length ? h('span', { class: 'also', text: `+ ${x.also.join(', ')}` }) : null),
      h('span', { class: 'row-end' },
        h('span', { class: x.tier === 'C' ? 'chip warn' : 'chip', title: TIER_TEXT[x.tier], text: x.tier }),
        h('span', { class: `pill ${x.badge}`, text: x.badge === 'new' ? 'New' : 'Active' })));
  }

  loadBtc();
  await load();
  const timer = setInterval(() => { load(); loadBtc(); }, REFRESH_MS);
  return () => clearInterval(timer);
}

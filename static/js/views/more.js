// More tab: sector heatmap, Bitcoin trend, pattern statistics, settings, data notes.
import { h, mount, segmented, errorBox, price, pct, date } from '../util.js';
import { get } from '../api.js';
import { state, currentTheme, setTheme } from '../store.js';

const PERIOD_FULL = { day: '24 hours', week: '7 days', month: '30 days' };
const SCALE = { day: 6, week: 15, month: 30 };           // % move that gives the strongest tile colour

export async function render(root, { ok }) {
  let period = 'day';
  const heat = h('div');
  const btc = h('section', { class: 'card' });
  const stats = h('section', { class: 'section' });
  mount(root, h('div', { class: 'page-head' }, h('h1', null, 'More')),
    h('div', { class: 'stack' },
      h('section', { class: 'section', style: { marginTop: 0 } },
        h('div', { class: 'tf-row' }, h('h2', null, 'Sector heatmap'),
          segmented([['day', 'Day'], ['week', 'Week'], ['month', 'Month']], period, (v) => { period = v; loadHeat(); }, 'Heatmap period')),
        heat),
      btc, stats, settings(), about()));

  function tile(s) {
    const strength = Math.min(1, Math.abs(s.avg_change) / SCALE[period]);
    const colour = s.avg_change >= 0 ? 'var(--up)' : 'var(--down)';
    return h('div', { class: 'tile', style: { background: `color-mix(in srgb, ${colour} ${Math.round(12 + strength * 48)}%, var(--panel))` } },
      h('span', { class: 't-name', text: s.sector }),
      h('span', { class: 't-val', text: pct(s.avg_change, { sign: true }) }),
      h('span', { class: 't-sub', text: `${s.count} coins · best ${s.best.base} ${pct(s.best.change, { sign: true, decimals: 0 })}` }));
  }

  async function loadHeat() {
    try {
      const d = await get('/api/sectors', { period });
      if (!ok()) return;
      mount(heat, d.sectors.length
        ? [h('div', { class: 'heat' }, d.sectors.map(tile)),
          h('p', { class: 'small muted', style: { marginTop: '8px' } },
            `Average move of the coins in each sector over ${PERIOD_FULL[period]}, up to ${date(d.latest_ts)}. Sectors with fewer than 3 coins are left out.`)]
        : h('p', { class: 'muted' }, 'The heatmap appears after the first scan.'));
    } catch (err) { if (ok()) mount(heat, errorBox(err.message, loadHeat)); }
  }

  async function loadBtc() {
    try {
      const m = await get('/api/market');
      if (!ok()) return;
      mount(btc, h('div', { class: 'card-head' }, h('h2', null, 'Bitcoin trend'),
        h('span', { class: 'chip' }, m.state)),
      h('p', { class: 'muted', text: m.text }),
      m.sma50 ? h('dl', { class: 'facts' },
        h('div', null, h('dt', null, 'Close'), h('dd', null, price(m.close))),
        h('div', null, h('dt', null, '50-day average'), h('dd', null, price(m.sma50))),
        h('div', null, h('dt', null, '200-day average'), h('dd', null, price(m.sma200))),
        h('div', null, h('dt', null, '7-day change'), h('dd', { class: m.change7d >= 0 ? 'pos' : 'neg' }, pct(m.change7d, { sign: true })))) : null);
    } catch { mount(btc); }
  }

  async function loadStats() {
    const tf = state.filters.tf;
    try {
      const d = await get('/api/stats/patterns', { tf });
      if (!ok()) return;
      const any = d.patterns.some((p) => p.closed > 0);
      mount(stats, h('h2', null, `Pattern statistics (${tf.toUpperCase()})`),
        h('p', { class: 'muted' }, 'How past breakouts ended. A breakout counts as finished when a candle closes below its trailing stop. Return is measured from the first entry.'),
        h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
          h('thead', null, h('tr', null, ['Pattern', 'Finished', 'Win rate', 'Median return', 'Running'].map((t, i) => h('th', { class: i ? 'n' : null, text: t })))),
          h('tbody', null, d.patterns.map((p) => h('tr', null,
            h('td', { text: p.pattern_name }),
            h('td', { class: 'n', text: String(p.closed) }),
            h('td', { class: 'n', text: p.win_rate === null ? '–' : pct(p.win_rate, { decimals: 0 }) }),
            h('td', { class: 'n', text: p.median_return === null ? '–' : pct(p.median_return, { sign: true }) }),
            h('td', { class: 'n', text: String(p.active) })))))),
        d.patterns.some((p) => p.small_sample)
          ? h('p', { class: 'note note-warn small' }, any ? 'Small sample: patterns with fewer than 30 finished breakouts can change a lot as more arrive.' : 'No breakout has finished yet, so there are no win rates to show.') : null,
        h('p', { class: 'small muted' }, d.note));
    } catch (err) { if (ok()) mount(stats, errorBox(err.message, loadStats)); }
  }

  function settings() {
    const meta = state.meta;
    return h('section', { class: 'section' }, h('h2', null, 'Settings'),
      h('div', { class: 'card' },
        h('div', { class: 'theme-row' }, h('span', null, 'Theme'),
          segmented([['dark', 'Dark'], ['light', 'Light']], currentTheme(), (v) => setTheme(v), 'Theme')),
        h('p', { class: 'small muted', style: { marginTop: '12px' } },
          meta.require_volume
            ? 'Volume rule: a breakout only counts when its candle has at least 1.5 times the average volume of the last 20 candles. This is set on the server.'
            : 'Volume rule: off. Breakouts count without a volume check. This is set on the server.')));
  }

  function about() {
    return h('section', { class: 'section' }, h('h2', null, 'About the data'),
      h('div', { class: 'card' },
        h('p', null, 'ZeroCoin scans spot USDT markets from public exchange data. Each coin uses one exchange for its whole history, so prices can differ a little from your exchange.'),
        h('p', { class: 'small muted' }, 'Liquidity tiers: A is above $20M traded per day, B is $5M to $20M, C is $1M to $5M. Tier C breakouts are noisier and easier to manipulate.'),
        h('p', { class: 'small muted' }, 'Educational tool, not financial advice. Crypto is risky. Past patterns do not predict future results.'),
        h('p', { class: 'small' }, h('a', { href: '/api/health' }, 'Data status'))));
  }

  await Promise.all([loadHeat(), loadBtc(), loadStats()]);
  return null;
}

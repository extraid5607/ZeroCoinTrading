// Watchlist tab: coins saved in this browser, with their current pattern.
import { h, mount, icon, errorBox, pct, candlesText } from '../util.js';
import { get } from '../api.js';
import { state, watchlist, toggleWatch } from '../store.js';

export async function render(root, { ok }) {
  const tf = state.filters.tf;
  const body = h('div');
  mount(root, h('div', { class: 'page-head' }, h('h1', null, 'Watchlist'),
    h('p', { class: 'muted' }, 'Coins you saved. They are stored in this browser only.')), body);

  async function load() {
    const saved = watchlist();
    if (!saved.length) {
      mount(body, h('div', { class: 'note' },
        h('p', null, 'Nothing saved yet.'),
        h('p', { class: 'muted' }, 'Open a coin and tap the star to keep an eye on it.'),
        h('a', { class: 'btn primary', href: '#/scanner' }, 'Open the Scanner')));
      return;
    }
    let data;
    try { data = await get('/api/watchlist', { bases: saved.join(',') }); } catch (err) { mount(body, errorBox(err.message, load)); return; }
    if (!ok()) return;
    const byBase = Object.fromEntries(data.items.map((x) => [x.coin.base, x]));
    const remove = (b) => { toggleWatch(b); load(); };
    const rows = saved.map((b) => {
      const item = byBase[b];
      const star = h('button', { type: 'button', class: 'rm', 'aria-label': `Remove ${b} from the watchlist`, on: { click: (e) => { e.preventDefault(); e.stopPropagation(); remove(b); } } }, icon('star', true));
      if (!item) {
        return h('div', { class: 'row watch-row' }, h('span', { class: 'coin-cell' }, h('span', { class: 'coin-sym', text: b })),
          h('span', { class: 'no-pattern' }, 'Not tracked any more'), h('span', { class: 'row-end' }, star));
      }
      const t = item.timeframes[tf] || {};
      const c = t.chain; const w = t.warning;
      return h('a', { class: 'row watch-row', href: `#/coin/${b}?tf=${tf}` },
        h('span', { class: 'coin-cell' }, h('span', { class: 'coin-sym', text: b }), h('span', { class: 'coin-name', text: item.coin.name })),
        c ? h('span', { class: 'pat-cell' }, h('span', { class: 'pat-main', text: c.pattern_name }),
          h('span', { class: 'also', text: `${pct(c.return_pct, { sign: true })} since entry · ${candlesText(c.age_candles, tf)}` }))
          : w ? h('span', { class: 'pat-cell short' }, h('span', { class: 'pat-main', text: w.pattern_name }), h('span', { class: 'also', text: 'Breakdown warning' }))
            : h('span', { class: 'no-pattern' }, 'No pattern now'),
        h('span', { class: 'row-end' }, c ? h('span', { class: `pill ${c.badge}`, text: c.badge === 'new' ? 'New' : 'Active' }) : null, star));
    });
    mount(body, h('div', { class: 'rows' }, rows),
      data.missing.length ? h('p', { class: 'small muted', style: { marginTop: '10px' } },
        `${data.missing.length} saved ${data.missing.length === 1 ? 'coin is' : 'coins are'} no longer in the scanned list.`) : null);
  }
  await load();
  return null;
}

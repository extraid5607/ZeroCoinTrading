// Coin detail tab: chart with level / stop / target lines, pattern card, chain card, past signals.
import { h, mount, icon, segmented, errorBox, price, pct, date, compactUsd, candlesText, cssVar } from '../util.js';
import { get } from '../api.js';
import { state, setLastCoin, setTf, isWatched, toggleWatch } from '../store.js';
import { createCoinChart } from '../chart.js';

const TIER_NOTE = {
  C: 'Thin liquidity: signals on this coin are noisier and easier to manipulate. Treat them with extra care.',
};

export async function render(root, { route, ok }) {
  const base = (route.arg || state.lastCoin || '').toUpperCase();
  if (!base) {
    mount(root, h('div', { class: 'page-head' }, h('h1', null, 'Chart')),
      h('div', { class: 'note' }, h('p', null, 'Pick a coin from the Scanner to see its chart here.'),
        h('a', { class: 'btn primary', href: '#/scanner' }, 'Open the Scanner')));
    return null;
  }
  const info = await get(`/api/coins/${encodeURIComponent(base)}`);
  if (!ok()) return null;
  setLastCoin(info.coin.base);
  const tfs = state.meta.timeframes;
  let tf = route.params.get('tf');
  if (!tfs.includes(tf)) tf = tfs.includes(state.detailTf) ? state.detailTf : state.filters.tf;
  const coin = info.coin;

  const status = h('p', { class: 'small muted', role: 'status', 'aria-live': 'polite' });
  const star = h('button', { type: 'button', class: 'star', 'aria-label': 'Save to watchlist', 'aria-pressed': String(isWatched(coin.base)),
    on: { click: () => {
      const now = toggleWatch(coin.base);
      if (now === null) { status.textContent = 'Your watchlist is full (60 coins). Remove one first.'; return; }
      star.setAttribute('aria-pressed', String(now));
      star.replaceChildren(icon('star', now));
      status.textContent = now ? `${coin.base} saved to your watchlist.` : `${coin.base} removed from your watchlist.`;
    } } }, icon('star', isWatched(coin.base)));

  const tagsEl = h('div', { class: 'tags' });
  const cards = h('div', { class: 'stack' });
  const chartBox = h('div', { class: 'chart' });
  const legend = h('div', { class: 'legend' });
  const history = h('div', { class: 'stack' });
  let chart = null;

  const livePriceEl = h('div', { class: 'live-ticker-strip', style: 'display: flex; align-items: center; gap: 8px; margin: 4px 0;' },
    h('span', { class: 'pulse-dot' }),
    h('span', { class: 'live-price-val', style: 'font-weight: 700; font-size: 1.15rem;', text: price(coin.last_price) }),
    h('span', { class: 'live-change-val', style: 'font-size: 0.85rem; font-weight: 600;' }),
    h('span', { class: 'live-pill', style: 'font-size: 0.65rem; padding: 2px 6px;' }, 'LIVE CCXT'));

  mount(root,
    h('div', { class: 'detail-head' },
      h('a', { class: 'back', href: '#/scanner', 'aria-label': 'Back to the Scanner' }, icon('back')),
      h('div', { class: 'detail-title' },
        h('h1', { text: coin.base }),
        h('p', { class: 'sub', text: `${coin.name} · ${coin.symbol} on ${coin.venue}` }),
        livePriceEl,
        tagsEl),
      star),
    status,
    h('div', { class: 'stack' },
      coin.tier === 'C' ? h('div', { class: 'note note-warn' }, h('p', { text: TIER_NOTE.C })) : null,
      tfs.length > 1 ? h('div', { class: 'tf-row' }, segmented(tfs.map((t) => [t, t.toUpperCase()]), tf, (v) => { tf = v; state.detailTf = v; setTf(v); loadTf(); }, 'Timeframe')) : null,
      h('div', { class: 'chart-wrap' }, chartBox, legend),
      cards,
      history,
      h('p', { class: 'disclaimer' },
        `Prices come from the ${coin.venue} spot ${coin.symbol} market and may differ from your exchange. `,
        'Educational tool, not financial advice. Crypto is risky.')));

  function drawTags(t) {
    mount(tagsEl,
      h('span', { class: coin.tier === 'C' ? 'chip warn' : 'chip', title: 'Liquidity tier' }, `Tier ${coin.tier}`),
      h('span', { class: 'chip' }, coin.sector),
      h('span', { class: 'chip' }, `${compactUsd(coin.quote_volume)} / day`),
      t && t.short_history ? h('span', { class: 'chip warn', title: 'Under a year of history: 365-day levels are not available' }, 'Short history') : null);
  }

  function facts(items) {
    return h('dl', { class: 'facts' }, items.filter(Boolean).map(([k, v, cls]) =>
      h('div', null, h('dt', { text: k }), h('dd', { class: cls || null, text: v }))));
  }

  function ladder(c) {
    const vals = [c.stop, c.entry_price, c.last_close, c.target];
    const lo = Math.min(...vals); const hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08 || hi * 0.02;
    const a = lo - pad; const b = hi + pad;
    const at = (v) => `${((v - a) / (b - a)) * 100}%`;
    const mark = (cls, v) => h('span', { class: `ladder-mark ${cls}`, style: { left: at(v) } });
    const label = (pos, v, text) => h('span', { class: `ladder-label ${pos}`, style: { left: at(v) }, text });
    return h('div', { class: 'ladder', role: 'img', 'aria-label': `Stop ${price(c.stop)}, entry ${price(c.entry_price)}, now ${price(c.last_close)}, target ${price(c.target)}` },
      h('span', { class: 'ladder-track' }),
      h('span', { class: 'ladder-fill', style: { left: at(Math.min(c.entry_price, c.last_close)), width: `${Math.abs(((c.last_close - c.entry_price) / (b - a)) * 100)}%` } }),
      mark('stop', c.stop), mark('', c.entry_price), mark('now', c.last_close), mark('target', c.target),
      label('down', c.stop, 'Stop'), label('up', c.entry_price, 'Entry'), label('down', c.last_close, 'Now'), label('up', c.target, 'Target'));
  }

  function drawCards(t, intraday) {
    const out = [];
    const c = t.chain;
    if (c) {
      const risk = ((c.entry_price - c.stop) / c.entry_price) * 100;
      const reward = ((c.target - c.entry_price) / c.entry_price) * 100;
      out.push(h('section', { class: 'card', 'aria-labelledby': 'chain-h' },
        h('div', { class: 'card-head' },
          h('div', null, h('h2', { id: 'chain-h' }, 'Active breakout'), h('p', { class: 'muted small', text: c.pattern_name })),
          h('span', { class: `pill ${c.badge}`, text: c.badge === 'new' ? 'New' : 'Active' })),
        ladder(c),
        facts([
          ['Entry (first signal)', price(c.entry_price)],
          ['Now', price(c.last_close)],
          ['Return since entry', pct(c.return_pct, { sign: true }), c.return_pct >= 0 ? 'pos' : 'neg'],
          ['Level broken', price(c.level)],
          ['Stop (trails up)', price(c.stop)],
          ['Target', price(c.target)],
          ['Risk to stop', pct(risk)],
          ['Reward to target', pct(reward)],
          ['Time in trade', candlesText(c.age_candles, tf)],
          ['Signals in chain', String(c.signal_count)],
          ['Entry date', date(c.entry_ts, intraday)],
          ['Initial stop', price(c.initial_stop)],
        ])));
    }
    const w = t.warning;
    if (w) {
      out.push(h('section', { class: 'card', 'aria-labelledby': 'warn-h' },
        h('div', { class: 'card-head' },
          h('div', null, h('h2', { id: 'warn-h' }, 'Breakdown warning'), h('p', { class: 'muted small', text: w.pattern_name })),
          h('span', { class: `pill ${w.badge}`, text: w.badge === 'new' ? 'New' : 'Active' })),
        h('p', { class: 'muted' }, 'Price closed below its 365-day low. This is a caution sign, not a buy signal.'),
        facts([
          ['Level broken', price(w.level)], ['Close on signal', price(w.entry)], ['Stop (above)', price(w.stop)],
          ['Volume vs average', `${w.volume_ratio.toFixed(1)}×`], ['Signal date', date(w.candle_ts, intraday)],
        ])));
    }
    if (!c && !w) {
      const s = t.latest_signal;
      out.push(h('section', { class: 'card' },
        h('h2', null, 'No active pattern'),
        h('p', { class: 'muted' }, t.candles < 30 ? 'There is not enough history for this coin yet.'
          : s ? `The last signal was ${s.pattern_name} on ${date(s.candle_ts, intraday)}. Its breakout is no longer running.`
            : 'This coin has not triggered any pattern in the stored history.')));
    }
    mount(cards, out);
  }

  async function loadTf() {
    const t = info.timeframes[tf];
    drawTags(t);
    const intraday = tf !== '1d';
    if (!t) { mount(cards, h('div', { class: 'note' }, h('p', null, 'This timeframe is not available for this coin.'))); return; }
    drawCards(t, intraday);
    try {
      const [cd, sg] = await Promise.all([
        get(`/api/coins/${encodeURIComponent(coin.base)}/candles`, { tf, limit: 500 }),
        get(`/api/signals/${encodeURIComponent(coin.base)}`, { tf }),
      ]);
      if (!ok()) return;
      if (!chart) chart = createCoinChart(chartBox);
      chart.setData(cd.candles, intraday);
      const lines = [];
      const ink = cssVar('--ink-2'); const up = cssVar('--chart-up'); const down = cssVar('--chart-down'); const brand = cssVar('--brand');
      const legendItems = [];
      const ref = t.chain || t.warning;
      if (t.chain) {
        const c = t.chain;
        lines.push({ price: c.level, color: brand, dashed: true, title: 'Level' }, { price: c.entry_price, color: ink, title: 'Entry' },
          { price: c.stop, color: down, width: 2, title: 'Stop' }, { price: c.target, color: up, dashed: true, title: 'Target' });
        legendItems.push(['Level broken', brand, true], ['Entry', ink], ['Stop', down], ['Target', up, true]);
      } else if (t.warning) {
        const w = t.warning;
        lines.push({ price: w.level, color: down, dashed: true, title: '365-day low' }, { price: w.stop, color: ink, title: 'Stop' });
        legendItems.push(['365-day low', down, true], ['Stop', ink]);
      }
      chart.setLines(lines);
      chart.setMarkers(sg.signals.map((x) => ({
        time: x.candle_ts, position: x.direction === 'long' ? 'belowBar' : 'aboveBar',
        color: x.direction === 'long' ? up : down, shape: x.direction === 'long' ? 'arrowUp' : 'arrowDown', text: shortName(x.pattern),
      })));
      mount(legend, legendItems.map(([text, color, dashed]) =>
        h('span', null, h('span', { class: dashed ? 'sw dashed' : 'sw', style: { borderTopColor: color } }), text)),
      h('span', null, 'Arrows mark candles where a signal fired'));
      void ref;
      drawHistory(sg, intraday);
    } catch (err) {
      if (ok()) mount(history, errorBox(err.message, loadTf));
    }
  }

  function shortName(p) { return { ath: 'ATH', high365: '365H', low365: '365L', w: 'W', range: 'Range' }[p] || p; }

  function drawHistory(sg, intraday) {
    const closed = sg.chains.filter((c) => c.status === 'stopped');
    mount(history,
      h('section', { class: 'card' },
        h('h2', null, 'Past signals'),
        sg.signals.length
          ? h('div', { class: 'sig-list' }, sg.signals.slice(0, 12).map((x) => h('div', { class: 'sig' },
            h('span', { class: 'd', text: date(x.candle_ts, intraday) }),
            h('span', { text: x.pattern_name }),
            h('span', { class: x.direction === 'long' ? 'pos' : 'neg', text: x.direction === 'long' ? 'Long' : 'Warning' }))))
          : h('p', { class: 'muted', style: { marginTop: '8px' } }, 'No signals in the stored history.')),
      closed.length ? h('section', { class: 'card' },
        h('h2', null, 'Finished breakouts'),
        h('div', { class: 'sig-list' }, closed.slice(0, 10).map((c) => h('div', { class: 'sig' },
          h('span', { class: 'd', text: date(c.entry_ts, intraday) }),
          h('span', { text: `${c.pattern_name}, ${candlesText(c.age_candles, tf)}` }),
          h('span', { class: c.return_pct >= 0 ? 'pos' : 'neg', text: pct(c.return_pct, { sign: true }) }))))) : null);
  }

  async function fetchLiveTicker() {
    try {
      const data = await get(`/api/coins/${encodeURIComponent(coin.base)}/live`);
      if (!ok() || !data || !data.last) return;
      const priceVal = livePriceEl.querySelector('.live-price-val');
      if (priceVal) priceVal.textContent = price(data.last);
      const chEl = livePriceEl.querySelector('.live-change-val');
      if (chEl && data.change_24h_pct !== undefined && data.change_24h_pct !== null) {
        const pctVal = Number(data.change_24h_pct);
        chEl.textContent = `${pctVal >= 0 ? '+' : ''}${pctVal.toFixed(2)}% (24h)`;
        chEl.style.color = pctVal >= 0 ? 'var(--up)' : 'var(--down)';
      }
    } catch { /* ignore */ }
  }

  fetchLiveTicker();
  const liveTimer = setInterval(fetchLiveTicker, 8000);

  await loadTf();
  return () => {
    if (chart) chart.destroy();
    clearInterval(liveTimer);
  };
}

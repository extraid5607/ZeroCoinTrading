// Candlestick chart (TradingView Lightweight Charts, loaded as a global) with lines for the level,
// entry, stop and target, and markers where signals fired.
import { cssVar } from './util.js';

const LWC = () => window.LightweightCharts;

function decimals(p) {
  return p >= 1000 ? 2 : p >= 1 ? 4 : p >= 0.1 ? 5 : p >= 0.001 ? 6 : 8;
}

export function createCoinChart(container) {
  const L = LWC();
  const chart = L.createChart(container, {
    autoSize: true,
    // Pin the locale: an odd navigator.language would otherwise make the library draw an empty chart.
    localization: { locale: 'en-US' },
    layout: { background: { type: 'solid', color: cssVar('--chart-bg') }, textColor: cssVar('--ink-2'),
              fontFamily: cssVar('--font') || 'sans-serif', fontSize: 12 },
    grid: { vertLines: { color: cssVar('--grid') }, horzLines: { color: cssVar('--grid') } },
    rightPriceScale: { borderColor: cssVar('--line'), scaleMargins: { top: 0.08, bottom: 0.22 } },
    timeScale: { borderColor: cssVar('--line'), timeVisible: false, rightOffset: 6 },
    crosshair: { mode: L.CrosshairMode.Normal },
  });
  const candles = chart.addSeries(L.CandlestickSeries, {
    upColor: cssVar('--chart-up'), downColor: cssVar('--chart-down'),
    wickUpColor: cssVar('--chart-up'), wickDownColor: cssVar('--chart-down'), borderVisible: false,
  });
  const volume = chart.addSeries(L.HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  let lines = [];
  let markerApi = null;

  return {
    setData(rows, intraday) {
      const up = cssVar('--chart-up'); const down = cssVar('--chart-down');
      const bars = []; const vols = [];
      let prev = -1;
      for (const r of rows) {
        const t = Math.floor(r[0]);
        if (t <= prev) continue;
        prev = t;
        bars.push({ time: t, open: r[1], high: r[2], low: r[3], close: r[4] });
        vols.push({ time: t, value: r[5], color: (r[4] >= r[1] ? up : down) + '66' });
      }
      const ref = bars.length ? bars[bars.length - 1].close : 1;
      const d = decimals(ref);
      candles.applyOptions({ priceFormat: { type: 'price', precision: d, minMove: Math.pow(10, -d) } });
      chart.applyOptions({ timeScale: { timeVisible: !!intraday } });
      candles.setData(bars);
      volume.setData(vols);
      chart.timeScale().fitContent();
      chart.timeScale().scrollToPosition(4, false);
    },
    setLines(spec) {
      for (const l of lines) candles.removePriceLine(l);
      lines = spec.filter((s) => Number.isFinite(s.price)).map((s) => candles.createPriceLine({
        price: s.price, color: s.color, lineWidth: s.width || 1, lineStyle: s.dashed ? L.LineStyle.Dashed : L.LineStyle.Solid,
        axisLabelVisible: true, title: s.title,
      }));
    },
    setMarkers(list) {
      const sorted = [...list].sort((a, b) => a.time - b.time);
      if (!markerApi) markerApi = L.createSeriesMarkers(candles, sorted);
      else markerApi.setMarkers(sorted);
    },
    destroy() { chart.remove(); },
  };
}

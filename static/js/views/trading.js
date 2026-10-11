// Auto-Trading & Delta Exchange Execution View: Dual Sections for Paper & Real Trading
import { h, mount, errorBox, price, pct, date, segmented } from '../util.js';
import { get, post } from '../api.js';

let activeSection = 'live'; // 'live' or 'paper'

export async function render(root, { ok }) {
  const container = h('div', { class: 'stack' });

  mount(root,
    h('div', { class: 'page-head' },
      h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' } },
        h('div', null,
          h('h1', null, 'Automated Trading'),
          h('p', { class: 'muted' }, 'Delta Exchange Live Execution & Paper Trading Simulation')
        ),
        h('div', { id: 'status-badge-wrap' })
      )
    ),
    container
  );

  async function load() {
    let res;
    try {
      res = await get('/api/trading/overview');
    } catch (err) {
      if (!ok()) return;
      mount(container, errorBox(err.message, load));
      return;
    }
    if (!ok()) return;

    const { overview, open_positions, closed_positions } = res;
    const isAuto = overview.auto_trade;
    const engineMode = overview.mode; // 'paper' or 'live'

    // Status Badge & Master Auto-Trade Toggle
    const badgeWrap = document.getElementById('status-badge-wrap');
    if (badgeWrap) {
      const toggleBtn = h('button', {
        type: 'button',
        class: `btn ${isAuto ? 'primary' : ''}`,
        style: { fontSize: '0.88rem', padding: '6px 14px' },
        on: {
          click: async () => {
            toggleBtn.disabled = true;
            try {
              await post('/api/trading/toggle', { enabled: !isAuto });
              load();
            } catch (e) {
              alert(e.message);
              toggleBtn.disabled = false;
            }
          }
        }
      }, isAuto ? 'Auto-Trading: ON' : 'Auto-Trading: OFF');

      const modeSelect = h('select', {
        class: 'field',
        style: { width: 'auto', minHeight: '34px', fontSize: '0.85rem', padding: '0 8px', marginLeft: '8px' },
        on: {
          change: async (e) => {
            const nextMode = e.target.value;
            if ((nextMode === 'live' || nextMode === 'both') && !confirm('Enable REAL Delta Exchange order execution? Real trades will be punched.')) {
              e.target.value = engineMode;
              return;
            }
            await post('/api/trading/config', { mode: nextMode });
            load();
          }
        }
      },
        h('option', { value: 'paper', selected: engineMode === 'paper' }, 'Target: Paper Only'),
        h('option', { value: 'live', selected: engineMode === 'live' }, 'Target: Real Delta Only'),
        h('option', { value: 'both', selected: engineMode === 'both' }, 'Target: BOTH (Real + Paper) ⚡')
      );

      mount(badgeWrap, toggleBtn, modeSelect);
    }

    // 1. Two Section Tabs (Real Trading vs Paper Trading)
    const sectionNav = segmented([
      ['live', '🟢 Real Trading (Delta India)'],
      ['paper', '📝 Paper Trading (Simulated)'],
    ], activeSection, (val) => {
      activeSection = val;
      renderCurrentSection();
    }, 'Trading Sections');

    const sectionContainer = h('div', { class: 'stack' });

    function renderCurrentSection() {
      const isLiveView = activeSection === 'live';
      const stats = isLiveView ? (overview.live || {}) : (overview.paper || {});
      const sectionPositions = open_positions.filter((p) => p.mode === activeSection);
      const sectionHistory = closed_positions.filter((p) => p.mode === activeSection);

      // Mode Switch Action Banner if viewing section is not currently targeted by the engine
      let engineBanner = null;
      if (engineMode !== activeSection) {
        engineBanner = h('div', { class: 'note note-warn', style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' } },
          h('span', null,
            `Scanner is currently routed to `,
            h('strong', null, engineMode.toUpperCase()),
            ` trading. Breakout trades will enter `,
            h('strong', null, engineMode),
            ` mode.`
          ),
          h('button', {
            type: 'button',
            class: 'btn',
            style: { padding: '4px 12px', minHeight: '32px', fontSize: '0.82rem' },
            on: {
              click: async () => {
                if (activeSection === 'live' && !confirm('Route automated execution to LIVE Delta Exchange? Real money trades will be punched.')) {
                  return;
                }
                await post('/api/trading/config', { mode: activeSection });
                load();
              }
            }
          }, `Switch Auto-Trading to ${isLiveView ? 'Real Delta' : 'Paper'}`)
        );
      }

      // Stats Grid for Selected Section
      const isProfitable = (stats.unrealized_pnl || 0) >= 0;
      const statsGrid = h('div', { class: 'facts card', style: { marginTop: '0', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' } },
        h('div', null,
          h('dt', null, isLiveView ? 'Delta Equity' : 'Simulated Equity'),
          h('dd', null, `$${price(stats.equity || 0)}`)
        ),
        h('div', null,
          h('dt', null, isLiveView ? 'Delta Free Margin' : 'Available Balance'),
          h('dd', null, `$${price(stats.balance || 0)}`)
        ),
        h('div', null,
          h('dt', null, 'Unrealized PnL'),
          h('dd', { class: isProfitable ? 'pos' : 'neg' }, `${isProfitable ? '+' : ''}$${price(stats.unrealized_pnl || 0)}`)
        ),
        h('div', null,
          h('dt', null, 'Realized PnL'),
          h('dd', { class: (stats.realized_pnl || 0) >= 0 ? 'pos' : 'neg' }, `${(stats.realized_pnl || 0) >= 0 ? '+' : ''}$${price(stats.realized_pnl || 0)}`)
        ),
        h('div', null,
          h('dt', null, 'Win Rate'),
          h('dd', null, `${stats.win_rate || 0}% (${stats.total_trades || 0} trades)`)
        ),
        h('div', null,
          h('dt', null, 'Status'),
          h('dd', { style: overview.live_error ? { color: 'var(--down)', fontSize: '0.8rem' } : {} },
            isLiveView ? (overview.live_error || (overview.delta_connected ? 'Connected (India)' : 'Not Connected')) : 'Active ($10K Sim)'
          )
        )
      );

      // Open Positions for this Section
      const openCard = h('div', { class: 'card' },
        h('div', { class: 'card-head' },
          h('h2', null, `${isLiveView ? 'Real Delta' : 'Paper'} Positions (${sectionPositions.length}/${overview.max_positions})`),
          sectionPositions.length > 0 ? h('button', {
            type: 'button',
            class: 'btn',
            style: { color: 'var(--down)', borderColor: 'var(--down)', fontSize: '0.8rem', minHeight: '30px', padding: '0 10px' },
            on: {
              click: async () => {
                if (confirm(`Panic-close all ${activeSection} positions?`)) {
                  await post('/api/trading/positions/close_all');
                  load();
                }
              }
            }
          }, 'Panic Close All') : null
        )
      );

      if (sectionPositions.length === 0) {
        openCard.append(h('p', { class: 'muted small', style: { padding: '12px 0' } },
          `No ${activeSection} positions currently open. Breakout signals matching your settings will be auto-filled.`
        ));
      } else {
        const posRows = sectionPositions.map((p) => {
          const pnlPos = p.pnl >= 0;
          return h('tr', null,
            h('td', null,
              h('a', { href: `#/coin/${p.base}`, style: { fontWeight: '700', textDecoration: 'none' } }, p.base),
              h('span', { class: 'chip', style: { marginLeft: '6px' } }, p.pattern.toUpperCase())
            ),
            h('td', null, `$${price(p.entry_price)}`),
            h('td', null, `$${price(p.current_price)}`),
            h('td', { style: { color: 'var(--down)' } }, `$${price(p.stop_loss)}`),
            h('td', { style: { color: 'var(--up)' } }, `$${price(p.take_profit)}`),
            h('td', { class: `n ${pnlPos ? 'pos' : 'neg'}`, style: { fontWeight: '600' } },
              `${pnlPos ? '+' : ''}$${price(p.pnl)} (${pct(p.pnl_pct, { sign: true })})`
            ),
            h('td', { class: 'n' },
              h('button', {
                type: 'button',
                class: 'btn',
                style: { minHeight: '28px', padding: '0 8px', fontSize: '0.78rem' },
                on: {
                  click: async () => {
                    await post(`/api/trading/positions/${p.id}/close`);
                    load();
                  }
                }
              }, 'Close')
            )
          );
        });

        openCard.append(h('div', { class: 'table-wrap' },
          h('table', { class: 'table' },
            h('thead', null,
              h('tr', null,
                h('th', null, 'Coin'),
                h('th', null, 'Entry'),
                h('th', null, 'Current'),
                h('th', null, 'Stop Loss'),
                h('th', null, 'Take Profit'),
                h('th', { class: 'n' }, 'Unrealized PnL'),
                h('th', { class: 'n' }, 'Action')
              )
            ),
            h('tbody', null, ...posRows)
          )
        ));
      }

      // History Card for this Section
      const historyCard = h('div', { class: 'card' },
        h('div', { class: 'card-head' },
          h('h2', null, `${isLiveView ? 'Real Delta' : 'Paper'} Trade History`),
          !isLiveView ? h('button', {
            type: 'button',
            class: 'btn',
            style: { fontSize: '0.8rem', minHeight: '30px', padding: '0 10px' },
            on: {
              click: async () => {
                if (confirm('Reset paper wallet balance back to $10,000 and clear paper trade history?')) {
                  await post('/api/trading/paper/reset');
                  load();
                }
              }
            }
          }, 'Reset Paper Wallet') : null
        )
      );

      if (sectionHistory.length === 0) {
        historyCard.append(h('p', { class: 'muted small', style: { padding: '12px 0' } }, `No ${activeSection} trade history recorded yet.`));
      } else {
        const histRows = sectionHistory.map((p) => {
          const pnlPos = p.pnl >= 0;
          return h('tr', null,
            h('td', null,
              h('span', { style: { fontWeight: '600' } }, p.base),
              h('span', { class: 'chip', style: { marginLeft: '6px' } }, p.pattern)
            ),
            h('td', null, `$${price(p.entry_price)}`),
            h('td', null, p.close_price ? `$${price(p.close_price)}` : '–'),
            h('td', { class: `n ${pnlPos ? 'pos' : 'neg'}`, style: { fontWeight: '600' } },
              `${pnlPos ? '+' : ''}$${price(p.pnl)}`
            ),
            h('td', null, h('span', { class: 'small muted' }, p.exit_reason || p.status)),
            h('td', { class: 'n small muted' }, date(p.closed_at || p.updated_at, true))
          );
        });

        historyCard.append(h('div', { class: 'table-wrap' },
          h('table', { class: 'table' },
            h('thead', null,
              h('tr', null,
                h('th', null, 'Coin'),
                h('th', null, 'Entry'),
                h('th', null, 'Exit Price'),
                h('th', { class: 'n' }, 'Realized PnL'),
                h('th', null, 'Reason'),
                h('th', { class: 'n' }, 'Date')
              )
            ),
            h('tbody', null, ...histRows)
          )
        ));
      }

      mount(sectionContainer, engineBanner, statsGrid, openCard, historyCard);
    }

    renderCurrentSection();
    mount(container, sectionNav, sectionContainer);
  }

  await load();
  const timer = setInterval(() => {
    if (ok()) load();
    else clearInterval(timer);
  }, 10000);

  return () => clearInterval(timer);
}

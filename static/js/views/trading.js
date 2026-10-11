// Auto-Trading & Delta Exchange Execution View
import { h, mount, errorBox, price, pct, date } from '../util.js';
import { get, post } from '../api.js';

export async function render(root, { ok }) {
  const container = h('div', { class: 'stack' });

  mount(root,
    h('div', { class: 'page-head' },
      h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' } },
        h('div', null,
          h('h1', null, 'Automated Trading'),
          h('p', { class: 'muted' }, 'Delta Exchange Connector & Paper Trading Simulation')
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

    // Status Badge & Master Auto-Trade Toggle
    const badgeWrap = document.getElementById('status-badge-wrap');
    if (badgeWrap) {
      const isAuto = overview.auto_trade;
      const isLive = overview.mode === 'live';

      const toggleBtn = h('button', {
        type: 'button',
        class: `btn ${isAuto ? 'primary' : ''}`,
        style: { fontSize: '0.9rem', padding: '6px 14px' },
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

      const modeBtn = h('button', {
        type: 'button',
        class: `pill ${isLive ? 'old' : 'new'}`,
        style: { marginLeft: '8px', cursor: overview.delta_connected ? 'pointer' : 'default', border: 'none' },
        title: overview.delta_connected ? 'Click to toggle between Paper and Live Delta execution' : 'Delta credentials required for Live mode',
        on: {
          click: async () => {
            if (!overview.delta_connected) {
              alert('Delta API credentials are not detected. Operating in Paper mode.');
              return;
            }
            const nextMode = isLive ? 'paper' : 'live';
            if (nextMode === 'live' && !confirm('Switch to LIVE Delta Exchange execution? Real trades with your Delta account balance will be placed.')) {
              return;
            }
            await post('/api/trading/config', { mode: nextMode });
            load();
          }
        }
      }, isLive ? 'LIVE (Delta)' : 'PAPER (Simulated)');

      mount(badgeWrap, toggleBtn, modeBtn);
    }

    // Top Stats Cards
    const isProfitable = overview.unrealized_pnl >= 0;
    const statsGrid = h('div', { class: 'facts card', style: { marginTop: '0', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' } },
      h('div', null,
        h('dt', null, 'Account Equity'),
        h('dd', null, `$${price(overview.equity)}`)
      ),
      h('div', null,
        h('dt', null, 'Available Balance'),
        h('dd', null, `$${price(overview.balance)}`)
      ),
      h('div', null,
        h('dt', null, 'Unrealized PnL'),
        h('dd', { class: isProfitable ? 'pos' : 'neg' }, `${isProfitable ? '+' : ''}$${price(overview.unrealized_pnl)}`)
      ),
      h('div', null,
        h('dt', null, 'Realized PnL'),
        h('dd', { class: overview.realized_pnl >= 0 ? 'pos' : 'neg' }, `${overview.realized_pnl >= 0 ? '+' : ''}$${price(overview.realized_pnl)}`)
      ),
      h('div', null,
        h('dt', null, 'Win Rate'),
        h('dd', null, `${overview.win_rate}% (${overview.total_trades} trades)`)
      ),
      h('div', null,
        h('dt', null, 'Delta Auth'),
        h('dd', null, overview.delta_connected ? 'Connected' : 'Not configured (Paper)')
      )
    );

    // Active Positions Card
    const openCard = h('div', { class: 'card' },
      h('div', { class: 'card-head' },
        h('h2', null, `Active Positions (${open_positions.length}/${overview.max_positions})`),
        open_positions.length > 0 ? h('button', {
          type: 'button',
          class: 'btn',
          style: { color: 'var(--down)', borderColor: 'var(--down)', fontSize: '0.8rem', minHeight: '30px', padding: '0 10px' },
          on: {
            click: async () => {
              if (confirm('Are you sure you want to panic-close ALL open positions?')) {
                await post('/api/trading/positions/close_all');
                load();
              }
            }
          }
        }, 'Panic Close All') : null
      )
    );

    if (open_positions.length === 0) {
      openCard.append(h('p', { class: 'muted small', style: { padding: '12px 0' } },
        'No positions currently open. Breakout signals matching your settings will be auto-filled.'
      ));
    } else {
      const posRows = open_positions.map((p) => {
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

    // Trade History / Audit Log Card
    const historyCard = h('div', { class: 'card' },
      h('div', { class: 'card-head' },
        h('h2', null, 'Trade History (Past Closes)'),
        h('button', {
          type: 'button',
          class: 'btn',
          style: { fontSize: '0.8rem', minHeight: '30px', padding: '0 10px' },
          on: {
            click: async () => {
              if (confirm('Reset paper wallet balance back to $10,000 and clear history?')) {
                await post('/api/trading/paper/reset');
                load();
              }
            }
          }
        }, 'Reset Paper Wallet')
      )
    );

    if (closed_positions.length === 0) {
      historyCard.append(h('p', { class: 'muted small', style: { padding: '12px 0' } }, 'No trade history recorded yet.'));
    } else {
      const histRows = closed_positions.map((p) => {
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

    mount(container, statsGrid, openCard, historyCard);
  }

  await load();
  const timer = setInterval(() => {
    if (ok()) load();
    else clearInterval(timer);
  }, 10000);

  return () => clearInterval(timer);
}

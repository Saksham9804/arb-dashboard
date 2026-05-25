import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell
} from 'recharts';

const API = process.env.REACT_APP_API_URL || 'http://localhost:8000';
const WS  = API.replace(/^http/, 'ws') + '/ws';

// ── Design tokens ─────────────────────────────────────────────────────────────
const C = {
  bg:      '#050508',
  surface: '#0d0d14',
  border:  '#1a1a2e',
  accent:  '#00ff88',
  accent2: '#ff3366',
  accent3: '#7c3aed',
  text:    '#e8e8f0',
  muted:   '#555570',
  card:    'rgba(13,13,20,0.9)',
};

const css = `
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: ${C.bg}; color: ${C.text}; font-family: 'Syne', sans-serif; min-height: 100vh; }
  ::-webkit-scrollbar { width: 4px; } ::-webkit-scrollbar-track { background: ${C.surface}; }
  ::-webkit-scrollbar-thumb { background: ${C.border}; border-radius: 2px; }

  .grid-bg {
    position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background-image:
      linear-gradient(rgba(0,255,136,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,136,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
  }

  .app { position: relative; z-index: 1; padding: 24px; max-width: 1400px; margin: 0 auto; }

  .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 32px; }
  .logo { font-family: 'Space Mono', monospace; font-size: 22px; font-weight: 700; letter-spacing: -1px; }
  .logo span { color: ${C.accent}; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: ${C.accent};
    box-shadow: 0 0 12px ${C.accent}; animation: pulse 2s infinite; display: inline-block; margin-right: 8px; }
  .status-dot.dead { background: ${C.accent2}; box-shadow: 0 0 12px ${C.accent2}; animation: none; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

  .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat-card {
    background: ${C.card}; border: 1px solid ${C.border}; border-radius: 12px; padding: 20px;
    position: relative; overflow: hidden;
    transition: border-color 0.2s;
  }
  .stat-card:hover { border-color: ${C.accent}44; }
  .stat-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, var(--accent-color, ${C.accent}), transparent);
  }
  .stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: ${C.muted}; margin-bottom: 8px; }
  .stat-value { font-family: 'Space Mono', monospace; font-size: 28px; font-weight: 700; }
  .stat-sub { font-size: 12px; color: ${C.muted}; margin-top: 4px; font-family: 'Space Mono', monospace; }

  .charts-row { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 24px; }
  @media (max-width: 900px) { .charts-row { grid-template-columns: 1fr; } }

  .panel {
    background: ${C.card}; border: 1px solid ${C.border}; border-radius: 12px; padding: 20px;
  }
  .panel-title { font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: ${C.muted}; margin-bottom: 16px; }

  .trades-table { width: 100%; border-collapse: collapse; }
  .trades-table th { font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; color: ${C.muted};
    padding: 8px 12px; text-align: left; border-bottom: 1px solid ${C.border}; }
  .trades-table td { padding: 10px 12px; font-family: 'Space Mono', monospace; font-size: 12px;
    border-bottom: 1px solid ${C.border}22; }
  .trades-table tr:hover td { background: rgba(0,255,136,0.03); }
  .trade-row { animation: fadeIn 0.4s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px; }
  .badge-dry { background: ${C.accent3}22; color: ${C.accent3}; border: 1px solid ${C.accent3}44; }
  .badge-live { background: ${C.accent}22; color: ${C.accent}; border: 1px solid ${C.accent}44; }

  .profit-pos { color: ${C.accent}; }
  .profit-neg { color: ${C.accent2}; }

  .spread-bar { display: flex; align-items: center; gap: 8px; }
  .spread-fill { height: 4px; border-radius: 2px; background: ${C.accent}; min-width: 2px; transition: width 0.3s; }

  .live-ticker {
    display: flex; gap: 12px; overflow-x: auto; margin-bottom: 24px; padding-bottom: 4px;
  }
  .ticker-chip {
    flex-shrink: 0; background: ${C.surface}; border: 1px solid ${C.border};
    border-radius: 8px; padding: 10px 16px; font-family: 'Space Mono', monospace; font-size: 12px;
    transition: border-color 0.2s;
  }
  .ticker-chip.active { border-color: ${C.accent}66; }
  .ticker-sym { font-size: 10px; color: ${C.muted}; letter-spacing: 1px; text-transform: uppercase; }
  .ticker-spread { font-size: 16px; font-weight: 700; margin-top: 2px; }
  .ticker-spread.pos { color: ${C.accent}; }
  .ticker-spread.neg { color: ${C.accent2}; }

  .empty { text-align: center; padding: 40px; color: ${C.muted}; font-size: 13px; }
  .mono { font-family: 'Space Mono', monospace; }
`;

// ── Custom tooltip ────────────────────────────────────────────────────────────
const ChartTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 14px', fontSize: 12, fontFamily: 'Space Mono, monospace' }}>
      <div style={{ color: C.muted, marginBottom: 4 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>
          {p.name}: {typeof p.value === 'number' ? p.value.toFixed(4) : p.value}
        </div>
      ))}
    </div>
  );
};

// ── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [summary, setSummary]   = useState({ total_trades:0, total_profit_usdt:0, win_rate:0, trades_24h:0, profit_24h:0 });
  const [trades, setTrades]     = useState([]);
  const [chart, setChart]       = useState([]);
  const [tickers, setTickers]   = useState({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);

  const fetchAll = useCallback(async () => {
    try {
      const [s, t, c] = await Promise.all([
        fetch(`${API}/api/summary`).then(r => r.json()),
        fetch(`${API}/api/trades?limit=50`).then(r => r.json()),
        fetch(`${API}/api/profit-chart`).then(r => r.json()),
      ]);
      setSummary(s);
      setTrades(t);
      setChart(c);
    } catch(e) { /* silently fail */ }
  }, []);

  // WebSocket for live updates
  useEffect(() => {
    const connect = () => {
      try {
        const ws = new WebSocket(WS);
        wsRef.current = ws;
        ws.onopen  = () => setConnected(true);
        ws.onclose = () => { setConnected(false); setTimeout(connect, 3000); };
        ws.onmessage = (e) => {
          const msg = JSON.parse(e.data);
          if (msg.type === 'trade') {
            setTrades(prev => [msg.data, ...prev].slice(0, 50));
            setSummary(prev => ({
              ...prev,
              total_trades: prev.total_trades + 1,
              total_profit_usdt: +(prev.total_profit_usdt + msg.data.profit_usdt).toFixed(4),
            }));
            setChart(prev => [...prev, {
              time: new Date(msg.data.timestamp * 1000).toLocaleTimeString(),
              profit: +(prev.reduce((a,b)=>a+(b.trade_profit||0),0) + msg.data.profit_usdt).toFixed(4),
              trade_profit: msg.data.profit_usdt,
              symbol: msg.data.symbol,
            }].slice(-100));
          }
          if (msg.type === 'tick') {
            const d = msg.data;
            setTickers(prev => ({ ...prev, [d.symbol]: d }));
          }
        };
      } catch(e) {}
    };
    connect();
    fetchAll();
    const interval = setInterval(fetchAll, 30000);
    return () => { clearInterval(interval); wsRef.current?.close(); };
  }, [fetchAll]);

  const fmtTime = (ts) => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('en-IN', { hour12: false });
  };

  const fmtProfit = (v) => (
    <span className={v >= 0 ? 'profit-pos' : 'profit-neg'}>
      {v >= 0 ? '+' : ''}{v.toFixed(4)} USDT
    </span>
  );

  // Spread color for bar chart
  const spreadColor = (spread) => spread > 0.3 ? C.accent : spread > 0.1 ? '#ffcc00' : C.muted;

  const statCards = [
    { label: 'Total Profit', value: `${summary.total_profit_usdt >= 0 ? '+' : ''}${summary.total_profit_usdt.toFixed(4)}`, sub: 'USDT', accent: C.accent },
    { label: 'Total Trades', value: summary.total_trades, sub: 'executions', accent: C.accent3 },
    { label: 'Win Rate', value: `${summary.win_rate}%`, sub: 'profitable', accent: '#ffcc00' },
    { label: '24h Trades', value: summary.trades_24h, sub: `${summary.profit_24h >= 0 ? '+' : ''}${summary.profit_24h?.toFixed(4)} USDT`, accent: C.accent2 },
  ];

  return (
    <>
      <style>{css}</style>
      <div className="grid-bg" />
      <div className="app">

        {/* Header */}
        <div className="header">
          <div className="logo">ARB<span>.</span>BOT</div>
          <div style={{ fontSize: 13, color: C.muted }}>
            <span className={`status-dot${connected ? '' : ' dead'}`} />
            {connected ? 'LIVE' : 'CONNECTING...'}
          </div>
        </div>

        {/* Stats */}
        <div className="stats-row">
          {statCards.map((c, i) => (
            <div className="stat-card" key={i} style={{ '--accent-color': c.accent }}>
              <div className="stat-label">{c.label}</div>
              <div className="stat-value" style={{ color: c.accent }}>{c.value}</div>
              <div className="stat-sub">{c.sub}</div>
            </div>
          ))}
        </div>

        {/* Live tickers */}
        {Object.keys(tickers).length > 0 && (
          <div className="live-ticker">
            {Object.entries(tickers).map(([sym, tick]) => (
              <div key={sym} className={`ticker-chip ${Math.abs(tick.spread_pct) > 0.1 ? 'active' : ''}`}>
                <div className="ticker-sym">{sym}</div>
                <div className={`ticker-spread ${tick.spread_pct > 0 ? 'pos' : 'neg'}`}>
                  {tick.spread_pct > 0 ? '+' : ''}{tick.spread_pct.toFixed(3)}%
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Charts */}
        <div className="charts-row">
          <div className="panel">
            <div className="panel-title">Cumulative Profit (USDT)</div>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={chart} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="profitGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.accent} stopOpacity={0.3}/>
                    <stop offset="95%" stopColor={C.accent} stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="time" tick={{ fill: C.muted, fontSize: 10, fontFamily: 'Space Mono' }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                <YAxis tick={{ fill: C.muted, fontSize: 10, fontFamily: 'Space Mono' }} tickLine={false} axisLine={false} />
                <Tooltip content={<ChartTooltip />} />
                <Area type="monotone" dataKey="profit" stroke={C.accent} strokeWidth={2}
                  fill="url(#profitGrad)" name="Cumulative" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="panel">
            <div className="panel-title">Per-Trade Profit</div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={chart.slice(-30)} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <XAxis dataKey="symbol" tick={{ fill: C.muted, fontSize: 9, fontFamily: 'Space Mono' }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: C.muted, fontSize: 10, fontFamily: 'Space Mono' }} tickLine={false} axisLine={false} />
                <Tooltip content={<ChartTooltip />} />
                <Bar dataKey="trade_profit" name="Profit" radius={[3,3,0,0]}>
                  {chart.slice(-30).map((entry, i) => (
                    <Cell key={i} fill={entry.trade_profit >= 0 ? C.accent : C.accent2} fillOpacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Trade log */}
        <div className="panel">
          <div className="panel-title">Trade Log</div>
          {trades.length === 0 ? (
            <div className="empty">No trades yet — bot is scanning for opportunities…</div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="trades-table">
                <thead>
                  <tr>
                    <th>Time</th><th>Symbol</th><th>Buy</th><th>Sell</th>
                    <th>Spread</th><th>Qty</th><th>Profit</th><th>Mode</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t, i) => (
                    <tr key={i} className="trade-row">
                      <td style={{ color: C.muted }}>{fmtTime(t.timestamp)}</td>
                      <td style={{ color: C.text, fontWeight: 700 }}>{t.symbol}</td>
                      <td>{t.buy_exchange} <span style={{ color: C.muted }}>@ {t.buy_price.toFixed(4)}</span></td>
                      <td>{t.sell_exchange} <span style={{ color: C.muted }}>@ {t.sell_price.toFixed(4)}</span></td>
                      <td>
                        <div className="spread-bar">
                          <div className="spread-fill" style={{ width: `${Math.min(t.spread_pct * 30, 60)}px`, background: spreadColor(t.spread_pct) }} />
                          <span style={{ color: spreadColor(t.spread_pct) }}>{t.spread_pct.toFixed(3)}%</span>
                        </div>
                      </td>
                      <td>{t.qty.toFixed(4)}</td>
                      <td>{fmtProfit(t.profit_usdt)}</td>
                      <td><span className={`badge badge-${t.dry_run ? 'dry' : 'live'}`}>{t.dry_run ? 'DRY' : 'LIVE'}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

      </div>
    </>
  );
}

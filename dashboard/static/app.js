/* XYZStocksUS dashboard — vanilla JS, no framework. */
const ARABIC = {
  HALAL: '🟢 HALAL',
  MIXED: '🟡 MIXED',
  HARAM: '🔴 HARAM',
  PENDING: '⚪ PENDING',
};

function fmtMoney(v) {
  if (v == null) return '—';
  if (typeof v === 'number') return '$' + v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return '$' + v;
}

function fmtPct(v) {
  if (v == null) return '—';
  return (v * 100).toFixed(0) + '%';
}

async function loadBTC() {
  try {
    const r = await fetch('/api/btc');
    const d = await r.json();
    document.getElementById('btc-price').textContent = d.price ? fmtMoney(d.price) : '—';
    document.getElementById('btc-meta').textContent =
      `${d.regime || '—'} · sma20 ${d.sma_20 ? d.sma_20.toFixed(0) : '—'} · sma50 ${d.sma_50 ? d.sma_50.toFixed(0) : '—'}`;
  } catch (e) { /* offline */ }
}

async function loadMarket() {
  try {
    const r = await fetch('/api/market');
    const d = await r.json();
    document.getElementById('market-status').textContent = d.label || '—';
    document.getElementById('market-note').textContent = d.note || (d.is_open ? 'open' : 'closed');
    const pill = document.getElementById('market-pill');
    if (pill) pill.textContent = d.label || '—';
  } catch (e) {}
}

async function loadCost() {
  try {
    const r = await fetch('/api/cost');
    const d = await r.json();
    document.getElementById('cost-figs').textContent =
      `$${d.today_usd.toFixed(2)} / $${d.month_usd.toFixed(2)}`;
    document.getElementById('cost-meta').textContent =
      `deep ${d.deep_count_today}/30 · ${d.quick_only ? 'quick-only' : 'normal'}`;
    const pill = document.getElementById('cost-pill');
    if (pill) pill.textContent = `$${d.today_usd.toFixed(2)} today`;
  } catch (e) {}
}

async function loadWatchlist() {
  try {
    const r = await fetch('/api/watchlist');
    const d = await r.json();
    const root = document.getElementById('watchlist');
    if (!root) return;
    const html = d.sectors.map(sec => {
      const tiles = sec.stocks.map(s => {
        const cls = (s.sharia_status || 'PENDING').toLowerCase();
        const score = s.score != null ? s.score.toFixed(0) : '—';
        const enabled = s.enabled ? '' : ' (off)';
        return `<div class="tile">
          <span class="sym">${s.symbol}${enabled}</span>
          <span class="badge ${cls}">${ARABIC[s.sharia_status] || s.sharia_status}</span>
          <span class="score">score ${score}</span>
        </div>`;
      }).join('');
      return `<div class="sector">${sec.sector}</div>${tiles}`;
    }).join('');
    root.innerHTML = html;
  } catch (e) {
    document.getElementById('watchlist').textContent = 'Error loading watchlist.';
  }
}

async function loadSignals() {
  try {
    const r = await fetch('/api/signals?limit=15');
    const d = await r.json();
    const root = document.getElementById('signals');
    if (!root) return;
    if (!d.signals.length) { root.innerHTML = '<li>None yet.</li>'; return; }
    root.innerHTML = d.signals.map(s => {
      const ts = (s.timestamp || '').slice(0, 16).replace('T', ' ');
      const cls = (s.sharia_status || 'PENDING').toLowerCase();
      const conf = s.confidence != null ? `${Math.round(s.confidence * 100)}%` : '—';
      return `<li><span><span class="badge ${cls}">${(s.sharia_status||'').slice(0,3)}</span>
        <strong>${s.symbol}</strong> ${s.decision} ${conf}</span>
        <span class="muted">${ts}</span></li>`;
    }).join('');
  } catch (e) {}
}

async function loadMacro() {
  try {
    const r = await fetch('/api/macro');
    const d = await r.json();
    const root = document.getElementById('macro');
    if (!root) return;
    if (!d.quotes.length) { root.innerHTML = '<li>No quotes yet.</li>'; return; }
    root.innerHTML = d.quotes.slice(0, 12).map(q => {
      const date = (q.date || '').slice(0, 10);
      const sentIcon = q.sentiment === 'HAWKISH' ? '🦅' : q.sentiment === 'DOVISH' ? '🕊️' : '·';
      const text = (q.quote_text || '').slice(0, 140);
      return `<li><span>${sentIcon} <strong>${q.speaker}</strong> · ${text}</span>
        <span class="muted">${date}</span></li>`;
    }).join('');
  } catch (e) {}
}

function tickFooter() {
  document.getElementById('ts').textContent = new Date().toISOString().slice(0, 19).replace('T', ' ');
}

async function refresh() {
  await Promise.all([loadBTC(), loadMarket(), loadCost(), loadWatchlist(), loadSignals(), loadMacro()]);
  tickFooter();
}

function startSSE() {
  if (!('EventSource' in window)) return;
  try {
    const es = new EventSource('/api/stream');
    es.addEventListener('signal', () => loadSignals());
    es.addEventListener('btc', () => loadBTC());
    es.addEventListener('cost', () => loadCost());
    es.addEventListener('hello', () => {});
    es.onerror = () => { /* will auto-reconnect */ };
  } catch (e) {}
}

document.addEventListener('DOMContentLoaded', () => {
  refresh();
  setInterval(refresh, 60_000);
  startSSE();
});

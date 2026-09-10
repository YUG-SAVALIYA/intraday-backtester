/**
 * BreakoutBT — Fast Frontend Application
 */

const API = "http://localhost:8001";
let equityChart = null;

// ─────────────────────────────────────────────────────────────────────────────
// API helpers
// ─────────────────────────────────────────────────────────────────────────────
async function api(method, path, body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`${res.status}: ${err}`);
  }
  return res.json();
}

function setStatus(text, ok = true) {
  document.getElementById("status-text").textContent = text;
  document.getElementById("status-dot").className = "status-dot " + (ok ? "ok" : "err");
}

async function checkHealth() {
  try {
    await api("GET", "/api/health");
    setStatus("Connected", true);
  } catch {
    setStatus("Backend offline", false);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Run Backtest
// ─────────────────────────────────────────────────────────────────────────────
document.getElementById("btn-run").addEventListener("click", async () => {
  const btn = document.getElementById("btn-run");
  const overlay = document.getElementById("loading-overlay");
  const loadingText = document.getElementById("loading-text");
  
  btn.disabled = true;
  overlay.classList.remove("hidden");
  document.getElementById("welcome-message").classList.add("hidden");
  document.getElementById("results-content").classList.add("hidden");

  try {
    // 1. Fetch universe symbols
    const universe = document.getElementById("cfg-universe").value;
    loadingText.textContent = `Fetching ${universe} symbols...`;
    
    const uniData = await api("GET", `/api/universe/${universe}`);
    const symbols = uniData.symbols;
    
    loadingText.textContent = `Running backtest on ${symbols.length} symbols...`;
    
    // 2. Build config using default base + user overrides
    const baseConfig = await api("GET", "/api/config/defaults");
    
    const peEnabled = document.getElementById("cfg-partial-exit-enabled")?.checked || false;
    const peFirstPct = parseFloat(document.getElementById("cfg-partial-first-pct")?.value || 50.0);
    const peSecondPct = Math.max(0, 100.0 - peFirstPct);
    const peFirstTime = document.getElementById("cfg-partial-first-time")?.value || "09:15";
    const peSecondTime = document.getElementById("cfg-partial-second-time")?.value || "15:00";
    const fullExitTime = document.getElementById("cfg-exit-time")?.value || "09:15";

    if (peEnabled && peFirstTime >= peSecondTime) {
      alert(`Invalid partial exit times: Leg 1 (${peFirstTime}) must be strictly earlier than Leg 2 (${peSecondTime}).`);
      loadingModal.style.display = "none";
      return;
    }

    const signalTime = document.getElementById("cfg-signal-time")?.value || "15:20";

    const config = {
      ...baseConfig,
      signal_time: signalTime,
      strategy: {
        ...baseConfig.strategy,
        min_range_pct: parseFloat(document.getElementById("cfg-range").value),
        max_range_pct: parseFloat(document.getElementById("cfg-max-range")?.value || 0) || 0.0,
        min_body_pct: parseFloat(document.getElementById("cfg-body").value),
        max_body_pct: parseFloat(document.getElementById("cfg-max-body")?.value || 0) || 0.0,
        min_close_loc_pct: parseFloat(document.getElementById("cfg-close-loc")?.value || 90.0),
        max_close_loc_pct: parseFloat(document.getElementById("cfg-max-close-loc")?.value || 0) || 0.0,
        volume_lookback: parseInt(document.getElementById("cfg-vol-lookback").value, 10),
        volume_multiplier: parseFloat(document.getElementById("cfg-vol-mult").value),
        prev_close_filter_enabled: document.getElementById("cfg-prev-close-filter")?.checked || false,
        prev_close_ref: document.getElementById("cfg-prev-close-ref")?.value || "candle_1525",
        prev_close_filter_mode: document.getElementById("cfg-prev-close-mode")?.value || "less_than_or_equal",
        max_prev_close_pct: document.getElementById("cfg-max-prev-close-pct")?.value !== "" && !isNaN(parseFloat(document.getElementById("cfg-max-prev-close-pct")?.value))
          ? parseFloat(document.getElementById("cfg-max-prev-close-pct").value)
          : 20.0,
        min_prev_close_pct: (document.getElementById("cfg-prev-close-mode")?.value !== "less_than_or_equal" && document.getElementById("cfg-min-prev-close-pct")?.value !== "" && !isNaN(parseFloat(document.getElementById("cfg-min-prev-close-pct")?.value)))
          ? parseFloat(document.getElementById("cfg-min-prev-close-pct").value)
          : null,
      },
      execution: {
        ...baseConfig.execution,
        exit_time: fullExitTime,
        entry_time: signalTime,
      },
      sizing: {
        ...baseConfig.sizing,
        max_volume_pct: parseFloat(document.getElementById("cfg-vol-pct").value)
      },
      partial_exit: {
        partial_exit_enabled: peEnabled,
        partial_exit_first_pct: peFirstPct,
        partial_exit_first_time: peFirstTime,
        partial_exit_second_pct: peSecondPct,
        partial_exit_second_time: peSecondTime,
      },
      start_date: document.getElementById("cfg-start").value || null,
      end_date: document.getElementById("cfg-end").value || null,
      symbols: symbols
    };

    // 3. Start Backtest Job with Live Progress Polling
    const t0 = performance.now();
    loadingText.textContent = "Starting backtest job...";
    const { job_id } = await api("POST", "/api/backtest/run", { config });

    let done = false;
    let result = null;
    while (!done) {
      await new Promise(r => setTimeout(r, 600));
      const job = await api("GET", `/api/jobs/${job_id}`);
      if (job.status === "done") {
        done = true;
        result = job.result;
      } else if (job.status === "error") {
        throw new Error(job.error || "Backtest failed");
      } else {
        if (job.progress) {
          loadingText.textContent = job.progress;
        }
      }
    }

    const elapsed = ((performance.now() - t0) / 1000).toFixed(2);
    
    // 4. Display Results
    displayResults(result, elapsed);
    fetchPastRuns(); // refresh past runs list

  } catch (e) {
    alert("Error: " + e.message);
  } finally {
    btn.disabled = false;
    overlay.classList.add("hidden");
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Formatters & Color Helpers
// ─────────────────────────────────────────────────────────────────────────────
const fmt = (v, d = 2) => Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtINR = (v, d = 0) => "₹" + Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });

function fmtPnl(val, isCurrency = true) {
  const num = Number(val || 0);
  const isPos = num > 0.0001;
  const isNeg = num < -0.0001;
  const cls = isPos ? 'profit' : (isNeg ? 'loss' : 'neutral');
  const sign = isPos ? '+' : (isNeg ? '-' : '');
  const absVal = Math.abs(num);
  const formatted = isCurrency ? `₹${fmt(absVal, 2)}` : fmt(absVal, 2);
  return `<span class="${cls} font-mono font-bold">${sign}${formatted}</span>`;
}

function fmtPct(val) {
  const num = Number(val || 0);
  const isPos = num > 0.0001;
  const isNeg = num < -0.0001;
  const cls = isPos ? 'profit' : (isNeg ? 'loss' : 'neutral');
  const sign = isPos ? '+' : (isNeg ? '-' : '');
  return `<span class="${cls} font-mono font-bold">${sign}${Math.abs(num).toFixed(2)}%</span>`;
}

function fmtDd(val) {
  const num = Math.abs(Number(val || 0));
  const cls = num > 0.001 ? 'loss' : 'neutral';
  return `<span class="${cls} font-mono font-bold">-${num.toFixed(2)}%</span>`;
}

function badgePnl(val, isCurrency = true) {
  const num = Number(val || 0);
  const isPos = num > 0.0001;
  const isNeg = num < -0.0001;
  const cls = isPos ? 'badge-profit' : (isNeg ? 'badge-loss' : 'badge-neutral');
  const sign = isPos ? '+' : (isNeg ? '-' : '');
  const absVal = Math.abs(num);
  const formatted = isCurrency ? `₹${fmt(absVal, 0)}` : fmt(absVal, 2);
  return `<span class="badge ${cls}">${sign}${formatted}</span>`;
}

// Global store for ledger and trades data
let currentDailyData = [];
let currentTradesData = [];
let currentMetrics = {};
let currentSignalTime = "15:20";
let expandedDates = new Set();
let ledgerPage = 1;
let tradesPage = 1;

// ─────────────────────────────────────────────────────────────────────────────
// Display Results
// ─────────────────────────────────────────────────────────────────────────────
function displayResults(result, elapsedSec) {
  const m = result.metrics;
  currentMetrics = m;
  currentDailyData = result.daily || [];
  currentTradesData = result.trades || [];
  currentSignalTime = result.config?.signal_time || result.config?.execution?.entry_time || "15:20";
  expandedDates.clear();
  ledgerPage = 1;
  tradesPage = 1;

  // Sync dropdown and column header to signal time
  const selSignal = document.getElementById("cfg-signal-time");
  if (selSignal && (result.config?.signal_time || result.config?.execution?.entry_time)) {
    selSignal.value = currentSignalTime;
  }
  const thBuys = document.getElementById("th-buys-label");
  if (thBuys) thBuys.textContent = `Buys (${currentSignalTime})`;
  
  document.getElementById("results-content").classList.remove("hidden");
  
  // Top KPIs
  document.getElementById("kpi-trades").textContent = m.n_trades;
  
  const wrEl = document.getElementById("kpi-winrate");
  wrEl.textContent = fmt(m.win_rate_pct) + "%";
  wrEl.className = "kpi-value " + (m.win_rate_pct >= 50 ? "profit" : "loss");
  
  const retEl = document.getElementById("kpi-return");
  retEl.innerHTML = fmtPct(m.total_return_pct);
  retEl.className = "kpi-value " + (m.total_return_pct >= 0 ? "profit" : "loss");
  
  const pnlEl = document.getElementById("kpi-pnl");
  pnlEl.innerHTML = fmtPnl(m.total_net_pnl);
  pnlEl.className = "kpi-value " + (m.total_net_pnl >= 0 ? "profit" : "loss");
  
  const ddEl = document.getElementById("kpi-dd");
  ddEl.innerHTML = fmtDd(m.max_drawdown_pct);
  ddEl.className = "kpi-value loss";

  // Advanced KPIs
  document.getElementById("kpi-generated").textContent = m.total_signals_generated || 0;
  document.getElementById("kpi-rejected").textContent = m.total_signals_rejected || 0;
  document.getElementById("kpi-wins").textContent = m.n_wins;
  document.getElementById("kpi-losses").textContent = m.n_losses;
  
  const pfEl = document.getElementById("kpi-profit-factor");
  pfEl.textContent = m.profit_factor;
  pfEl.className = "kpi-value " + (m.profit_factor >= 1.0 ? "profit" : "loss");
  
  const avgRetEl = document.getElementById("kpi-avg-return");
  avgRetEl.innerHTML = fmtPct(m.avg_return_pct_per_trade);
  avgRetEl.className = "kpi-value " + (m.avg_return_pct_per_trade >= 0 ? "profit" : "loss");

  // Theoretical KPIs
  document.getElementById("kpi-theoretical-trades").textContent = m.theoretical_total_trades || 0;
  
  const thWrEl = document.getElementById("kpi-theoretical-winrate");
  thWrEl.textContent = fmt(m.theoretical_win_rate_pct || 0) + "%";
  thWrEl.className = "kpi-value " + ((m.theoretical_win_rate_pct || 0) >= 50 ? "profit" : "loss");
  
  const thAvgEl = document.getElementById("kpi-theoretical-avg");
  thAvgEl.innerHTML = fmtPct(m.theoretical_avg_return_pct || 0);
  thAvgEl.className = "kpi-value " + ((m.theoretical_avg_return_pct || 0) >= 0 ? "profit" : "loss");

  const savedFile = result.saved_output_file ? ` • Saved: output/${result.saved_output_file.split(/[\\/]/).pop()}` : '';
  setStatus(`Completed in ${elapsedSec}s${savedFile}`, true);

  // Equity Chart
  renderEquityChart(result.equity_dates, result.daily);
  
  // Periodic Returns
  renderPeriodicTables(m.periodic_returns);

  // Day-Wise Ledger
  initDailyLedger(result.daily, m);

  // Full Trade Log
  initTradesTable(result.trades);
}

// ─────────────────────────────────────────────────────────────────────────────
// Periodic Returns Tables
// ─────────────────────────────────────────────────────────────────────────────
function renderPeriodicTables(pr) {
  if (!pr) return;
  const render = (id, data) => {
    const tbody = document.querySelector(`#${id} tbody`);
    if (!tbody) return;
    if (!data || data.length === 0) {
      tbody.innerHTML = "<tr><td colspan='2' class='text-center neutral'>No data</td></tr>";
      return;
    }
    tbody.innerHTML = [...data].reverse().map(d => `
      <tr>
        <td>${d.period}</td>
        <td class="text-right">${fmtPct(d.return_pct)}</td>
      </tr>
    `).join('');
  };
  render("table-yearly", pr.yearly);
  render("table-monthly", pr.monthly);
  render("table-weekly", pr.weekly);
  render("table-daily", pr.daily);
}

// ─────────────────────────────────────────────────────────────────────────────
// Day-Wise Portfolio Ledger
// ─────────────────────────────────────────────────────────────────────────────
function initDailyLedger(daily, metrics) {
  if (!daily || daily.length === 0) return;

  // Summary strip values
  const initCapEl = document.getElementById("strip-init-cap");
  if (initCapEl) initCapEl.textContent = fmtINR(metrics.initial_capital);
  
  const finalEqEl = document.getElementById("strip-final-eq");
  if (finalEqEl) {
    finalEqEl.textContent = fmtINR(metrics.final_equity);
    finalEqEl.className = "strip-value " + (metrics.final_equity >= metrics.initial_capital ? "profit" : "loss");
  }

  const peakEq = Math.max(...daily.map(d => d.equity));
  const peakEqEl = document.getElementById("strip-peak-eq");
  if (peakEqEl) peakEqEl.textContent = fmtINR(peakEq);

  const maxGain = Math.max(...daily.map(d => d.daily_pnl));
  const maxGainEl = document.getElementById("strip-max-gain");
  if (maxGainEl) {
    maxGainEl.innerHTML = maxGain > 0 ? `+${fmtINR(maxGain)}` : "₹0";
    maxGainEl.className = "strip-value profit";
  }

  const maxDd = Math.min(...daily.map(d => d.drawdown_pct));
  const maxDdEl = document.getElementById("strip-max-dd");
  if (maxDdEl) {
    maxDdEl.innerHTML = fmtDd(maxDd);
    maxDdEl.className = "strip-value loss";
  }

  // Setup search, filter, and pagination listeners
  const searchInput = document.getElementById("ledger-search");
  const activeOnlyToggle = document.getElementById("ledger-active-only");
  const prevBtn = document.getElementById("ledger-prev-btn");
  const nextBtn = document.getElementById("ledger-next-btn");
  const sizeSelect = document.getElementById("ledger-page-size");

  if (searchInput) searchInput.oninput = () => { ledgerPage = 1; renderLedgerRows(); };
  if (activeOnlyToggle) activeOnlyToggle.onchange = () => { ledgerPage = 1; renderLedgerRows(); };
  if (prevBtn) prevBtn.onclick = () => { if (ledgerPage > 1) { ledgerPage--; renderLedgerRows(); } };
  if (nextBtn) nextBtn.onclick = () => { ledgerPage++; renderLedgerRows(); };
  if (sizeSelect) sizeSelect.onchange = () => { ledgerPage = 1; renderLedgerRows(); };

  renderLedgerRows();
}

function renderLedgerRows() {
  const tbody = document.querySelector("#table-ledger tbody");
  if (!tbody) return;

  const query = (document.getElementById("ledger-search")?.value || "").trim().toLowerCase();
  const activeOnly = document.getElementById("ledger-active-only")?.checked ?? true;
  const pageSizeVal = document.getElementById("ledger-page-size")?.value || "100";

  let filtered = currentDailyData.filter(d => {
    const hasBuys = d.buys && d.buys.length > 0;
    const hasSells = d.sells && d.sells.length > 0;
    const hasHoldings = d.holdings && d.holdings.length > 0;
    const isActive = hasBuys || hasSells || hasHoldings;

    if (activeOnly && !isActive) return false;

    if (query) {
      const matchesDate = d.date.includes(query);
      const matchesBuy = hasBuys && d.buys.some(b => b.symbol.toLowerCase().includes(query));
      const matchesSell = hasSells && d.sells.some(s => s.symbol.toLowerCase().includes(query));
      const matchesHold = hasHoldings && d.holdings.some(h => h.symbol.toLowerCase().includes(query));
      if (!matchesDate && !matchesBuy && !matchesSell && !matchesHold) return false;
    }

    return true;
  });

  const totalRows = filtered.length;
  const pageSize = pageSizeVal === "all" ? totalRows : parseInt(pageSizeVal, 10);
  const totalPages = Math.max(1, Math.ceil(totalRows / (pageSize || 1)));

  if (ledgerPage > totalPages) ledgerPage = totalPages;
  if (ledgerPage < 1) ledgerPage = 1;

  const countPill = document.getElementById("ledger-count-pill");
  if (countPill) countPill.textContent = `${totalRows} of ${currentDailyData.length} Days`;
  const pageInd = document.getElementById("ledger-page-indicator");
  if (pageInd) pageInd.textContent = `Page ${ledgerPage} / ${totalPages}`;
  const prevBtn = document.getElementById("ledger-prev-btn");
  if (prevBtn) prevBtn.disabled = ledgerPage <= 1;
  const nextBtn = document.getElementById("ledger-next-btn");
  if (nextBtn) nextBtn.disabled = ledgerPage >= totalPages;

  const startIdx = (ledgerPage - 1) * pageSize;
  const pageRows = filtered.slice(startIdx, startIdx + pageSize);

  if (pageRows.length === 0) {
    tbody.innerHTML = "<tr><td colspan='10' class='text-center' style='padding: 24px; color: var(--text-muted);'>No ledger records match the selected filter</td></tr>";
    return;
  }

  let html = "";
  pageRows.forEach(d => {
    const isExpanded = expandedDates.has(d.date);
    const buysCount = (d.buys && d.buys.length) || 0;
    const sellsCount = (d.sells && d.sells.length) || 0;
    const holdsCount = (d.holdings && d.holdings.length) || 0;

    // Buys badge
    let buysBadge = `<span class="badge badge-neutral">—</span>`;
    if (buysCount > 0) {
      buysBadge = `<span class="badge badge-buy">${buysCount} Buy${buysCount > 1 ? 's' : ''}</span>`;
    }

    // Sells badge with realized PnL
    let sellsBadge = `<span class="badge badge-neutral">—</span>`;
    if (sellsCount > 0) {
      const daySellPnl = d.sells.reduce((acc, s) => acc + (s.net_pnl || 0), 0);
      const sellCls = daySellPnl >= 0 ? 'badge-profit' : 'badge-loss';
      const sign = daySellPnl >= 0 ? '+' : '-';
      sellsBadge = `<span class="badge ${sellCls}">${sellsCount} Sell${sellsCount > 1 ? 's' : ''} (${sign}₹${fmt(Math.abs(daySellPnl), 0)})</span>`;
    }

    // Holdings badge
    let holdsBadge = `<span class="badge badge-neutral">—</span>`;
    if (holdsCount > 0) {
      const holdVal = d.holdings.reduce((acc, h) => acc + (h.current_value || 0), 0);
      holdsBadge = `<span class="badge badge-hold">${holdsCount} Open (₹${fmt(holdVal, 0)})</span>`;
    }

    // Invested cap %
    const invPct = d.equity > 0 ? ((d.invested_capital / d.equity) * 100).toFixed(1) : 0;

    html += `
      <tr class="clickable-row ${isExpanded ? 'active-expanded' : ''}" onclick="toggleLedgerDetail('${d.date}')">
        <td><strong>${d.date}</strong></td>
        <td class="text-right font-mono font-bold">${fmtINR(d.equity, 2)}</td>
        <td class="text-right font-mono">${fmtINR(d.cash, 2)}</td>
        <td class="text-right font-mono">${fmtINR(d.invested_capital, 2)} <span class="neutral" style="font-size:11px">(${invPct}%)</span></td>
        <td class="text-right">${fmtPnl(d.daily_pnl, true)} <span style="font-size:11px">(${fmtPct(d.daily_pnl_pct)})</span></td>
        <td class="text-right">${fmtDd(d.drawdown_pct)}</td>
        <td class="text-center">${buysBadge}</td>
        <td class="text-center">${sellsBadge}</td>
        <td class="text-center">${holdsBadge}</td>
        <td class="text-center">
          <button class="badge ${isExpanded ? 'badge-buy' : 'badge-neutral'}" style="cursor:pointer;" onclick="event.stopPropagation(); toggleLedgerDetail('${d.date}')">
            ${isExpanded ? 'Hide ▲' : 'View ▼'}
          </button>
        </td>
      </tr>
    `;

    if (isExpanded) {
      html += renderLedgerDetailRow(d);
    }
  });

  tbody.innerHTML = html;
}

function toggleLedgerDetail(date) {
  if (expandedDates.has(date)) {
    expandedDates.delete(date);
  } else {
    expandedDates.add(date);
  }
  renderLedgerRows();
}

function renderLedgerDetailRow(d) {
  const cashPct = d.equity > 0 ? Math.min(100, Math.max(0, (d.cash / d.equity) * 100)) : 100;
  const invPct = 100 - cashPct;

  // Buys items
  let buysHtml = "";
  if (!d.buys || d.buys.length === 0) {
    buysHtml = `<div class="detail-empty">No buys executed on this day (${currentSignalTime} IST)</div>`;
  } else {
    buysHtml = d.buys.map(b => `
      <div class="detail-item">
        <div>
          <span class="font-bold" style="color:#60a5fa">${b.symbol}</span>
          <span style="color:var(--text-muted); margin-left:4px">Qty: ${b.qty} @ ₹${fmt(b.entry_price)} <span class="neutral" style="font-size:10px">${b.entry_time || currentSignalTime}</span></span>
        </div>
        <div class="text-right">
          <div class="font-mono font-bold">${fmtINR(b.trade_value)}</div>
          <div style="font-size:11px; color:var(--text-muted)">Own: ₹${fmt(b.own_capital_used, 0)} | Fees: ₹${fmt(b.entry_fees, 2)}</div>
        </div>
      </div>
    `).join('');
  }

  // Sells items
  let sellsHtml = "";
  if (!d.sells || d.sells.length === 0) {
    sellsHtml = `<div class="detail-empty">No positions exited on this day</div>`;
  } else {
    sellsHtml = d.sells.map(s => `
      <div class="detail-item">
        <div>
          <span class="font-bold" style="color:#c084fc">${s.symbol}</span>
          <span class="badge" style="font-size:10px; margin-left:4px; background:rgba(192,132,252,0.15); color:#c084fc;">${s.exit_time || "09:15"}</span>
          <span style="color:var(--text-muted); margin-left:4px">Qty: ${s.qty} (₹${fmt(s.entry_price)} → ₹${fmt(s.exit_price)})</span>
        </div>
        <div class="text-right">
          <div>${fmtPnl(s.net_pnl, true)} (${fmtPct(s.return_pct)})</div>
          <div style="font-size:11px; color:var(--text-muted)">Fees: ₹${fmt(s.total_fees || 0, 2)}</div>
        </div>
      </div>
    `).join('');
  }

  // Holdings items
  let holdsHtml = "";
  if (!d.holdings || d.holdings.length === 0) {
    holdsHtml = `<div class="detail-empty">No open positions held overnight</div>`;
  } else {
    holdsHtml = d.holdings.map(h => `
      <div class="detail-item">
        <div>
          <span class="font-bold" style="color:#facc15">${h.symbol}</span>
          <span style="color:var(--text-muted); margin-left:4px">Qty: ${h.qty} @ Entry ₹${fmt(h.entry_price)}</span>
        </div>
        <div class="text-right">
          <div>Val: <span class="font-mono font-bold">${fmtINR(h.current_value)}</span> (Close: ₹${fmt(h.current_price)})</div>
          <div>Unrealized: ${fmtPnl(h.unrealized_pnl, true)} (${fmtPct(h.unrealized_return_pct)})</div>
        </div>
      </div>
    `).join('');
  }

  return `
    <tr class="expanded-row">
      <td colspan="10" style="padding: 0;">
        <div class="detail-row-container">
          <!-- Capital Allocation Bar -->
          <div class="alloc-bar-wrapper" style="margin-bottom: 16px;">
            <div class="alloc-bar-labels">
              <span><strong>Available Cash:</strong> ${fmtINR(d.cash, 2)} (${cashPct.toFixed(1)}%)</span>
              <span><strong>Invested Capital:</strong> ${fmtINR(d.invested_capital, 2)} (${invPct.toFixed(1)}%)</span>
            </div>
            <div class="alloc-bar">
              <div class="alloc-bar-fill-cash" style="width: ${cashPct}%;"></div>
              <div class="alloc-bar-fill-invested" style="width: ${invPct}%;"></div>
            </div>
          </div>

          <!-- 3-Column Breakdown -->
          <div class="detail-grid">
            <!-- Buys Today -->
            <div class="detail-card">
              <div class="detail-card-header">
                <span class="detail-card-title">🛒 Buys Today (${currentSignalTime} IST)</span>
                <span class="badge badge-buy">${(d.buys && d.buys.length) || 0}</span>
              </div>
              <div class="detail-list">${buysHtml}</div>
            </div>

            <!-- Sells Today -->
            <div class="detail-card">
              <div class="detail-card-header">
                <span class="detail-card-title">💰 Sells Today (09:15 IST)</span>
                <span class="badge badge-sell">${(d.sells && d.sells.length) || 0}</span>
              </div>
              <div class="detail-list">${sellsHtml}</div>
            </div>

            <!-- Overnight Holdings -->
            <div class="detail-card">
              <div class="detail-card-header">
                <span class="detail-card-title">📦 Overnight Holdings</span>
                <span class="badge badge-hold">${(d.holdings && d.holdings.length) || 0}</span>
              </div>
              <div class="detail-list">${holdsHtml}</div>
            </div>
          </div>
        </div>
      </td>
    </tr>
  `;
}

// ─────────────────────────────────────────────────────────────────────────────
// Full Trades Table with Pagination & Filter
// ─────────────────────────────────────────────────────────────────────────────
function initTradesTable(trades) {
  currentTradesData = trades || [];
  tradesPage = 1;

  const searchInput = document.getElementById("trades-search");
  const prevBtn = document.getElementById("trades-prev-btn");
  const nextBtn = document.getElementById("trades-next-btn");
  const sizeSelect = document.getElementById("trades-page-size");

  if (searchInput) searchInput.oninput = () => { tradesPage = 1; renderTradesRows(); };
  if (prevBtn) prevBtn.onclick = () => { if (tradesPage > 1) { tradesPage--; renderTradesRows(); } };
  if (nextBtn) nextBtn.onclick = () => { tradesPage++; renderTradesRows(); };
  if (sizeSelect) sizeSelect.onchange = () => { tradesPage = 1; renderTradesRows(); };

  renderTradesRows();
}

function renderTradesRows() {
  const tbody = document.querySelector("#table-trades tbody");
  if (!tbody) return;

  const query = (document.getElementById("trades-search")?.value || "").trim().toLowerCase();
  const pageSizeVal = document.getElementById("trades-page-size")?.value || "100";

  let filtered = currentTradesData.filter(t => {
    if (!query) return true;
    return t.symbol.toLowerCase().includes(query) || t.entry_date.includes(query) || t.exit_date.includes(query);
  });

  const totalRows = filtered.length;
  const pageSize = pageSizeVal === "all" ? totalRows : parseInt(pageSizeVal, 10);
  const totalPages = Math.max(1, Math.ceil(totalRows / (pageSize || 1)));

  if (tradesPage > totalPages) tradesPage = totalPages;
  if (tradesPage < 1) tradesPage = 1;

  const countPill = document.getElementById("trades-count-pill");
  if (countPill) countPill.textContent = `${totalRows} of ${currentTradesData.length} Trades`;
  const pageInd = document.getElementById("trades-page-indicator");
  if (pageInd) pageInd.textContent = `Page ${tradesPage} / ${totalPages}`;
  const prevBtn = document.getElementById("trades-prev-btn");
  if (prevBtn) prevBtn.disabled = tradesPage <= 1;
  const nextBtn = document.getElementById("trades-next-btn");
  if (nextBtn) nextBtn.disabled = tradesPage >= totalPages;

  const startIdx = (tradesPage - 1) * pageSize;
  const pageRows = filtered.slice(startIdx, startIdx + pageSize);

  if (pageRows.length === 0) {
    tbody.innerHTML = "<tr><td colspan='10' class='text-center' style='padding: 24px; color: var(--text-muted);'>No trades match the selected filter</td></tr>";
    return;
  }

  tbody.innerHTML = pageRows.map(t => {
    const isWin = t.net_pnl > 0;
    const badgeCls = isWin ? "badge-profit" : "badge-loss";
    const distBadge = (t.prev_close_dist_pct != null)
      ? `<span class="badge" style="font-size:10px; background:rgba(255,255,255,0.06); color:#a0aec0; margin-left:4px; cursor:help;" 
          title="Distance from Yesterday: ${t.prev_close_dist_pct >= 0 ? '+' : ''}${t.prev_close_dist_pct}%\nRange: ${t.breakout_metrics?.range_pct != null ? t.breakout_metrics.range_pct + '%' : '-'}\nBody: ${t.breakout_metrics?.body_pct != null ? t.breakout_metrics.body_pct + '%' : '-'}\nClose Loc: ${t.breakout_metrics?.close_loc_pct != null ? t.breakout_metrics.close_loc_pct + '%' : '-'}\nVol: ${t.breakout_metrics?.volume_multiple != null ? t.breakout_metrics.volume_multiple + 'x' : '-'}">
          ${t.prev_close_dist_pct >= 0 ? '+' : ''}${t.prev_close_dist_pct.toFixed(1)}%
        </span>`
      : '';
    const exitTimeStr = t.exits && t.exits.length > 1 
      ? `<span class="badge" style="background:rgba(41,98,255,0.15); color:#2962ff; font-size:10px; cursor:help;" title="${t.exits.map((e, idx) => `Leg ${idx+1}: ${e.qty} shares @ ₹${fmt(e.exit_price)} (${e.exit_time || 'IST'})`).join(' | ')}">PARTIAL</span>`
      : (t.exit_reason === 'backtest_end' ? `${t.exit_time || currentSignalTime} (End)` : (t.exit_time || (t.exits && t.exits[0] ? t.exits[0].exit_time : (t.exit_reason ? t.exit_reason.replace('exit_', '') : '09:15'))));
    return `
      <tr>
        <td><strong>${t.symbol}</strong> <span class="badge ${badgeCls}">${isWin ? 'WIN' : 'LOSS'}</span>${distBadge}</td>
        <td>${t.entry_date} <span class="neutral" style="font-size:11px">${t.entry_time || currentSignalTime}</span></td>
        <td>
          ${t.exit_date} 
          <span class="neutral" style="font-size:11px">${exitTimeStr}</span>
        </td>
        <td class="font-mono font-bold">${t.qty}</td>
        <td class="font-mono">₹${fmt(t.entry_price)}</td>
        <td class="font-mono">₹${fmt(t.exit_price)}</td>
        <td class="text-right">${fmtPnl(t.gross_pnl, true)}</td>
        <td class="text-right font-mono">₹${fmt(t.entry_fees + t.exit_fees)}</td>
        <td class="text-right">${fmtPnl(t.net_pnl, true)}</td>
        <td class="text-right">${fmtPct(t.return_pct)}</td>
      </tr>
    `;
  }).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// Equity Chart
// ─────────────────────────────────────────────────────────────────────────────
function renderEquityChart(dates, daily) {
  const el = document.getElementById("equity-chart");
  
  if (equityChart) {
    equityChart.remove();
    equityChart = null;
  }
  
  if (!dates || !daily || dates.length === 0) {
    el.innerHTML = "<div style='padding:40px;text-align:center;color:#666'>No data available</div>";
    return;
  }
  
  equityChart = LightweightCharts.createChart(el, {
    layout: { background: { color: "#131722" }, textColor: "#8892b0" },
    grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
    timeScale: { borderColor: "#242938", timeVisible: true },
    rightPriceScale: { borderColor: "#242938" },
    crosshair: { mode: 1 },
    width: el.offsetWidth,
    height: 380
  });

  const series = equityChart.addAreaSeries({
    topColor: "rgba(16, 185, 129, 0.35)",
    bottomColor: "rgba(16, 185, 129, 0.02)",
    lineColor: "#10b981",
    lineWidth: 2,
  });

  const data = dates.map((d, i) => ({
    time: d,
    value: daily[i].equity,
  }));
  
  series.setData(data);
  equityChart.timeScale().fitContent();
}

// Window resize handler for chart
window.addEventListener("resize", () => {
  if (equityChart) {
    const el = document.getElementById("equity-chart");
    equityChart.applyOptions({ width: el.offsetWidth });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Past Runs — Data Store & Helpers
// ─────────────────────────────────────────────────────────────────────────────
let _allRuns = [];      // full list returned by /api/runs
let _filteredRuns = []; // after search filter

function setPastRunStatus(msg, isErr = false) {
  const el = document.getElementById("past-run-status");
  if (el) {
    el.textContent = msg;
    el.style.color = isErr ? "var(--loss)" : "var(--text-muted)";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Fetch & Populate Past Runs
// ─────────────────────────────────────────────────────────────────────────────
async function fetchPastRuns() {
  try {
    const data = await api("GET", "/api/runs");
    _allRuns = data.runs || [];
    _filteredRuns = _allRuns;

    // ── Populate sidebar dropdown ────────────────────────────────────────────
    const sel = document.getElementById("select-past-run");
    sel.innerHTML = `<option value="">-- Select a run (${_allRuns.length} found) --</option>`;
    _allRuns.forEach(r => {
      const ret = Number(r.total_return_pct || 0).toFixed(1);
      const sign = ret >= 0 ? "+" : "";
      const label = `${r.timestamp}  |  ${r.n_trades} trades  |  ${sign}${ret}%`;
      const opt = document.createElement("option");
      opt.value = r.filename;
      opt.textContent = label;
      sel.appendChild(opt);
    });

    // ── Populate modal table ─────────────────────────────────────────────────
    renderModalRunsTable(_allRuns);

    if (_allRuns.length === 0) {
      setPastRunStatus("No past runs found.");
    } else {
      setPastRunStatus(`${_allRuns.length} run(s) available.`);
    }
  } catch (e) {
    setPastRunStatus("Could not load past runs: " + e.message, true);
  }
}

function renderModalRunsTable(runs) {
  const tbody = document.getElementById("modal-runs-tbody");
  const emptyEl = document.getElementById("modal-empty");
  if (!tbody) return;

  if (!runs || runs.length === 0) {
    tbody.innerHTML = "";
    emptyEl && emptyEl.classList.remove("hidden");
    return;
  }
  emptyEl && emptyEl.classList.add("hidden");

  tbody.innerHTML = runs.map(r => {
    const ret = Number(r.total_return_pct || 0);
    const retCls = ret >= 0 ? "profit" : "loss";
    const retSign = ret >= 0 ? "+" : "";
    const pnl = Number(r.total_net_pnl || 0);
    const pnlCls = pnl >= 0 ? "profit" : "loss";
    const pnlSign = pnl >= 0 ? "+" : "-";
    const ddVal = Math.abs(Number(r.max_drawdown_pct || 0));
    const sizeStr = r.size_kb >= 1024
      ? (r.size_kb / 1024).toFixed(1) + " MB"
      : r.size_kb + " KB";

    return `
      <tr>
        <td class="font-mono" style="font-size:12px;">${r.timestamp}</td>
        <td class="text-right font-mono">${r.n_trades}</td>
        <td class="text-right"><span class="${retCls} font-mono font-bold">${retSign}${ret.toFixed(2)}%</span></td>
        <td class="text-right"><span class="${pnlCls} font-mono font-bold">${pnlSign}₹${Math.abs(pnl).toLocaleString("en-IN", {maximumFractionDigits: 0})}</span></td>
        <td class="text-right font-mono">${Number(r.win_rate_pct || 0).toFixed(1)}%</td>
        <td class="text-right"><span class="loss font-mono">-${ddVal.toFixed(2)}%</span></td>
        <td class="text-right font-mono">${r.symbols_count}</td>
        <td class="text-right font-mono" style="color:var(--text-muted);font-size:11px;">${sizeStr}</td>
        <td class="text-center">
          <button class="btn-load-run" data-file="${r.filename}">Load</button>
        </td>
      </tr>`;
  }).join("");

  // Wire up load buttons inside table
  tbody.querySelectorAll(".btn-load-run").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const filename = btn.getAttribute("data-file");
      closeModal();
      loadPastRun(filename);
    });
  });

  // Row click also loads
  tbody.querySelectorAll("tr").forEach(row => {
    row.addEventListener("click", () => {
      const btn = row.querySelector(".btn-load-run");
      if (btn) {
        closeModal();
        loadPastRun(btn.getAttribute("data-file"));
      }
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Load a Past Run by filename
// ─────────────────────────────────────────────────────────────────────────────
async function loadPastRun(filename) {
  if (!filename) return;

  const overlay = document.getElementById("loading-overlay");
  const loadingText = document.getElementById("loading-text");
  overlay.classList.remove("hidden");
  document.getElementById("welcome-message").classList.add("hidden");
  loadingText.textContent = `Loading run: ${filename}…`;

  try {
    const data = await api("GET", `/api/runs/${encodeURIComponent(filename)}`);
    displayResults(data, 0);
    setStatus(`Loaded: ${filename}`, true);
    setPastRunStatus(`Loaded: ${filename}`);
  } catch (e) {
    alert("Failed to load run: " + e.message);
    setPastRunStatus("Load failed: " + e.message, true);
  } finally {
    overlay.classList.add("hidden");
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Local JSON File Upload
// ─────────────────────────────────────────────────────────────────────────────
document.getElementById("file-upload-run").addEventListener("change", function () {
  const file = this.files[0];
  if (!file) return;
  setPastRunStatus(`Reading ${file.name}…`);
  const reader = new FileReader();
  reader.onload = function (e) {
    try {
      const data = JSON.parse(e.target.result);
      document.getElementById("welcome-message").classList.add("hidden");
      displayResults(data, 0);
      setStatus(`Loaded file: ${file.name}`, true);
      setPastRunStatus(`Loaded: ${file.name}`);
    } catch (err) {
      setPastRunStatus("Invalid JSON file: " + err.message, true);
    }
  };
  reader.onerror = () => setPastRunStatus("Failed to read file.", true);
  reader.readAsText(file);
  this.value = ""; // reset so same file can be re-uploaded
});

// ─────────────────────────────────────────────────────────────────────────────
// Modal Open / Close / Search / Refresh
// ─────────────────────────────────────────────────────────────────────────────
function openModal() {
  document.getElementById("modal-past-runs").classList.remove("hidden");
  document.getElementById("modal-search").value = "";
  renderModalRunsTable(_allRuns);
  _filteredRuns = _allRuns;
}

function closeModal() {
  document.getElementById("modal-past-runs").classList.add("hidden");
}

document.getElementById("btn-browse-runs").addEventListener("click", openModal);
document.getElementById("btn-close-modal").addEventListener("click", closeModal);

// Close modal when clicking outside the content card
document.getElementById("modal-past-runs").addEventListener("click", function (e) {
  if (e.target === this) closeModal();
});

// Escape key closes modal
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeModal();
});

// Refresh button
document.getElementById("btn-refresh-runs").addEventListener("click", async () => {
  const btn = document.getElementById("btn-refresh-runs");
  btn.style.transform = "rotate(360deg)";
  btn.style.transition = "transform 0.4s ease";
  await fetchPastRuns();
  renderModalRunsTable(_filteredRuns);
  setTimeout(() => { btn.style.transform = ""; btn.style.transition = ""; }, 450);
});

// Search / filter
document.getElementById("modal-search").addEventListener("input", function () {
  const q = this.value.toLowerCase().trim();
  _filteredRuns = q
    ? _allRuns.filter(r =>
        r.timestamp.toLowerCase().includes(q) ||
        r.filename.toLowerCase().includes(q) ||
        String(r.n_trades).includes(q) ||
        String(r.total_return_pct).includes(q)
      )
    : _allRuns;
  renderModalRunsTable(_filteredRuns);
});

// ─────────────────────────────────────────────────────────────────────────────
// Sidebar — Select + Load button wiring
// ─────────────────────────────────────────────────────────────────────────────
document.getElementById("select-past-run").addEventListener("change", function () {
  document.getElementById("btn-load-past-run").disabled = !this.value;
});

document.getElementById("btn-load-past-run").addEventListener("click", () => {
  const sel = document.getElementById("select-past-run");
  if (sel.value) loadPastRun(sel.value);
});

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────
checkHealth();
setInterval(checkHealth, 5000);
fetchPastRuns(); // Load list of past runs on startup

// Partial exit UI wiring
const peCheckbox = document.getElementById("cfg-partial-exit-enabled");
const peDetails = document.getElementById("partial-exit-details");
const peFirstInput = document.getElementById("cfg-partial-first-pct");
const peSecondInput = document.getElementById("cfg-partial-second-pct");
const peFirstTime = document.getElementById("cfg-partial-first-time");
const peSecondTime = document.getElementById("cfg-partial-second-time");
const peWarning = document.getElementById("partial-exit-warning");
const fullExitGroup = document.getElementById("group-full-exit-time");

function validateExitTimes() {
  if (!peCheckbox || !peCheckbox.checked) {
    if (peWarning) peWarning.style.display = "none";
    if (fullExitGroup) fullExitGroup.style.opacity = "1";
    return true;
  }
  if (fullExitGroup) fullExitGroup.style.opacity = "0.4";
  if (peFirstTime && peSecondTime) {
    const t1 = peFirstTime.value;
    const t2 = peSecondTime.value;
    const invalid = t1 >= t2;
    if (peWarning) peWarning.style.display = invalid ? "block" : "none";
    return !invalid;
  }
  return true;
}

if (peCheckbox && peDetails) {
  peCheckbox.addEventListener("change", () => {
    peDetails.style.display = peCheckbox.checked ? "block" : "none";
    validateExitTimes();
  });
}

if (peFirstInput && peSecondInput) {
  peFirstInput.addEventListener("input", () => {
    let v = parseFloat(peFirstInput.value);
    if (isNaN(v)) v = 50;
    if (v < 1) v = 1;
    if (v > 99) v = 99;
    peSecondInput.value = (100 - v).toFixed(0);
  });
}

if (peFirstTime && peSecondTime) {
  peFirstTime.addEventListener("change", validateExitTimes);
  peSecondTime.addEventListener("change", validateExitTimes);
}

validateExitTimes();

const sigTimeEl = document.getElementById("cfg-signal-time");
if (sigTimeEl) {
  sigTimeEl.addEventListener("change", (e) => {
    currentSignalTime = e.target.value;
    const thBuys = document.getElementById("th-buys-label");
    if (thBuys) thBuys.textContent = `Buys (${currentSignalTime})`;
  });
}

window.togglePrevCloseFilter = function(checked) {
  const details = document.getElementById("prev-close-details");
  if (details) {
    details.style.opacity = checked ? "1" : "0.75";
  }
  updatePrevCloseUI();
};

window.setPrevClosePct = function(pct) {
  const chk = document.getElementById("cfg-prev-close-filter");
  if (chk) chk.checked = true;
  const maxInput = document.getElementById("cfg-max-prev-close-pct");
  if (maxInput) {
    maxInput.value = pct;
  }
  updatePrevCloseUI();
};

window.updatePrevCloseUI = function() {
  const chk = document.getElementById("cfg-prev-close-filter");
  const modeEl = document.getElementById("cfg-prev-close-mode");
  const badge = document.getElementById("prev-close-badge");
  const groupMin = document.getElementById("group-min-prev-close");
  const groupMax = document.getElementById("group-max-prev-close");
  const minInp = document.getElementById("cfg-min-prev-close-pct");
  const maxInp = document.getElementById("cfg-max-prev-close-pct");

  if (!modeEl || !badge) return;

  const mode = modeEl.value;
  const isEnabled = chk ? chk.checked : false;

  if (mode === "less_than_or_equal") {
    if (groupMin) groupMin.style.display = "none";
    if (groupMax) groupMax.style.display = "block";
    const maxVal = maxInp && maxInp.value !== "" ? maxInp.value : "20";
    if (isEnabled) {
      badge.textContent = `ACTIVE (≤ ${maxVal}%)`;
      badge.style.background = "rgba(16,185,129,0.15)";
      badge.style.color = "#10b981";
    } else {
      badge.textContent = `OFF (≤ ${maxVal}%)`;
      badge.style.background = "rgba(255,255,255,0.08)";
      badge.style.color = "#8899a6";
    }
  } else if (mode === "greater_than_or_equal") {
    if (groupMin) groupMin.style.display = "block";
    if (groupMax) groupMax.style.display = "none";
    const minVal = minInp && minInp.value !== "" ? minInp.value : "0";
    if (isEnabled) {
      badge.textContent = `ACTIVE (≥ ${minVal}%)`;
      badge.style.background = "rgba(16,185,129,0.15)";
      badge.style.color = "#10b981";
    } else {
      badge.textContent = `OFF (≥ ${minVal}%)`;
      badge.style.background = "rgba(255,255,255,0.08)";
      badge.style.color = "#8899a6";
    }
  } else if (mode === "between") {
    if (groupMin) groupMin.style.display = "block";
    if (groupMax) groupMax.style.display = "block";
    const minVal = minInp && minInp.value !== "" ? minInp.value : "0";
    const maxVal = maxInp && maxInp.value !== "" ? maxInp.value : "20";
    if (isEnabled) {
      badge.textContent = `ACTIVE (${minVal}% to ${maxVal}%)`;
      badge.style.background = "rgba(16,185,129,0.15)";
      badge.style.color = "#10b981";
    } else {
      badge.textContent = `OFF (${minVal}% to ${maxVal}%)`;
      badge.style.background = "rgba(255,255,255,0.08)";
      badge.style.color = "#8899a6";
    }
  }
};

const prevCloseCheckbox = document.getElementById("cfg-prev-close-filter");
if (prevCloseCheckbox) {
  prevCloseCheckbox.addEventListener("change", () => {
    window.togglePrevCloseFilter(prevCloseCheckbox.checked);
  });
}

const prevCloseMode = document.getElementById("cfg-prev-close-mode");
if (prevCloseMode) prevCloseMode.addEventListener("change", () => {
  const chk = document.getElementById("cfg-prev-close-filter");
  if (chk) chk.checked = true;
  window.updatePrevCloseUI();
});
const minPrevInput = document.getElementById("cfg-min-prev-close-pct");
if (minPrevInput) minPrevInput.addEventListener("input", () => {
  const chk = document.getElementById("cfg-prev-close-filter");
  if (chk) chk.checked = true;
  window.updatePrevCloseUI();
});
const maxPrevInput = document.getElementById("cfg-max-prev-close-pct");
if (maxPrevInput) maxPrevInput.addEventListener("input", () => {
  const chk = document.getElementById("cfg-prev-close-filter");
  if (chk) chk.checked = true;
  window.updatePrevCloseUI();
});
window.updatePrevCloseUI();

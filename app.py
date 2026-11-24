"""CM3 Intraday Dashboard — Flask web service."""
import os
import io
import json
import hashlib
import hmac
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_file, render_template_string
from dotenv import load_dotenv
load_dotenv()

from config import API_SECRET
from data_fetch import fetch_meta_spend, fetch_shopify_orders
from cm3_engine import build_campaign_dict, process_orders, build_tables

app = Flask(__name__)

_cache = {}
_cache_lock = threading.Lock()

def _ist_now():
    from datetime import timezone
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))

def _run_cm3(date_str=None):
    now = _ist_now()
    if date_str is None:
        date_str = now.strftime('%Y-%m-%d')
    yest_str = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"[CM3] Fetching data for {date_str}...")
    spend = fetch_meta_spend(date_str)
    print(f"[CM3] Spend rows: {len(spend)}")

    campaigns = build_campaign_dict(spend)
    print(f"[CM3] Campaigns: {len(campaigns)}")

    today_orders = fetch_shopify_orders(date_str)
    print(f"[CM3] Today orders: {len(today_orders)}")
    today_df = process_orders(today_orders, campaigns, date_str)
    print(f"[CM3] Today matched: {len(today_df)}")

    yest_orders = fetch_shopify_orders(yest_str)
    print(f"[CM3] Yesterday orders: {len(yest_orders)}")
    yest_df = process_orders(yest_orders, campaigns, yest_str)
    print(f"[CM3] Yesterday matched: {len(yest_df)}")

    tables = build_tables(today_df, yest_df, spend, campaigns, date_str, yest_str)
    return tables


def get_cm3(date_str=None):
    now = _ist_now()
    if date_str is None:
        date_str = now.strftime('%Y-%m-%d')

    with _cache_lock:
        cached = _cache.get(date_str)
        if cached and (time.time() - cached['ts']) < 300:
            return cached['data']

    data = _run_cm3(date_str)

    with _cache_lock:
        _cache[date_str] = {'data': data, 'ts': time.time()}

    return data


DASHBOARD_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CM3 Intraday</title>
<style>
:root {
  --bg: #f5f6fa;
  --bg2: #ffffff;
  --bg3: #ebedf3;
  --fg: #1a1d27;
  --fg2: #6b7085;
  --accent: #4a6adf;
  --accent2: #3a56c5;
  --green: #16a34a;
  --red: #dc2626;
  --yellow: #d97706;
  --border: #d5d8e3;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1117; --bg2: #1a1d27; --bg3: #242836;
    --fg: #e1e4ed; --fg2: #8b90a0;
    --accent: #6c8cff; --accent2: #4a6adf;
    --green: #34d399; --red: #f87171; --yellow: #fbbf24;
    --border: #2a2e3d; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #0f1117; --bg2: #1a1d27; --bg3: #242836;
  --fg: #e1e4ed; --fg2: #8b90a0;
  --accent: #6c8cff; --accent2: #4a6adf;
  --green: #34d399; --red: #f87171; --yellow: #fbbf24;
  --border: #2a2e3d; color-scheme: dark;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--fg); min-height: 100vh; }
.container { max-width: 1200px; margin: 0 auto; padding: 16px; }

/* Header */
.header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; padding: 16px 20px; background: var(--bg2); border-radius: 12px; border: 1px solid var(--border); }
.header h1 { font-size: 1.3rem; font-weight: 700; }
.header h1 span { color: var(--accent); }
.header-right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.date-pick { background: var(--bg3); border: 1px solid var(--border); color: var(--fg); padding: 6px 12px; border-radius: 8px; font-size: 0.85rem; }
.btn { padding: 8px 16px; border-radius: 8px; border: none; cursor: pointer; font-size: 0.85rem; font-weight: 600; transition: all 0.2s; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: var(--accent2); }
.btn-secondary { background: var(--bg3); color: var(--fg); border: 1px solid var(--border); }
.btn-secondary:hover { background: var(--border); }

/* Stats bar */
.stats-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
.stat-card { background: var(--bg2); padding: 14px 16px; border-radius: 10px; border: 1px solid var(--border); }
.stat-card .label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--fg2); margin-bottom: 4px; }
.stat-card .value { font-size: 1.2rem; font-weight: 700; }

/* Table sections */
.section { margin-bottom: 28px; }
.section-title { font-size: 1rem; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
.section-title .badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 999px; background: var(--accent); color: #fff; font-weight: 600; }

.tbl-wrap { overflow-x: auto; border-radius: 10px; border: 1px solid var(--border); }
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
thead th { background: var(--bg3); padding: 10px 12px; text-align: left; font-weight: 600; color: var(--fg2); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; position: sticky; top: 0; }
thead th.num { text-align: right; }
tbody td { padding: 8px 12px; border-top: 1px solid var(--border); white-space: nowrap; }
tbody td.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:hover { background: rgba(108, 140, 255, 0.06); }
tfoot td { padding: 10px 12px; border-top: 2px solid var(--border); font-weight: 700; background: var(--bg3); }
tfoot td.num { text-align: right; }

.up { color: var(--green); }
.down { color: var(--red); }
.even { color: var(--fg2); }

/* Loading */
.loading { display: flex; align-items: center; justify-content: center; padding: 60px 20px; flex-direction: column; gap: 16px; }
.spinner { width: 36px; height: 36px; border: 3px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.loading-text { color: var(--fg2); font-size: 0.9rem; }

/* Footer */
.footer { text-align: center; padding: 20px; color: var(--fg2); font-size: 0.75rem; border-top: 1px solid var(--border); margin-top: 20px; }

@media (max-width: 600px) {
  .header { flex-direction: column; align-items: flex-start; }
  .stats-bar { grid-template-columns: repeat(2, 1fr); }
  table { font-size: 0.75rem; }
  thead th, tbody td, tfoot td { padding: 6px 8px; }
}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>CM3 <span>Intraday</span></h1>
    <div class="header-right">
      <input type="date" id="datePick" class="date-pick">
      <button class="btn btn-primary" onclick="loadData()">Refresh</button>
      <button class="btn btn-secondary" onclick="downloadExcel()">Download Excel</button>
      <button class="btn btn-secondary" id="themeBtn" onclick="toggleTheme()" title="Toggle theme">☀️</button>
    </div>
  </div>
  <div id="content">
    <div class="loading">
      <div class="spinner"></div>
      <div class="loading-text">Fetching Meta spend & Shopify orders...</div>
    </div>
  </div>
  <div class="footer">CM3 Intraday Dashboard &bull; Data: Meta Marketing API + Shopify</div>
</div>

<script>
const F = n => n != null ? n.toLocaleString('en-IN') : '—';

function datePick() {
  return document.getElementById('datePick').value || '';
}

async function loadData() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><div class="loading-text">Fetching Meta spend & Shopify orders...</div></div>';
  try {
    const d = datePick();
    const url = d ? `/api/cm3?date=${d}` : '/api/cm3';
    const r = await fetch(url);
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    render(j);
  } catch(e) {
    el.innerHTML = `<div class="loading"><div class="loading-text" style="color:var(--red)">Error: ${e.message}</div></div>`;
  }
}

function downloadExcel() {
  const d = datePick();
  const url = d ? `/api/cm3-excel?date=${d}` : '/api/cm3-excel';
  window.open(url, '_blank');
}

function render(d) {
  const el = document.getElementById('content');
  const h = d.header;
  let html = '';

  // Stats bar
  html += '<div class="stats-bar">';
  html += `<div class="stat-card"><div class="label">Date</div><div class="value">${h.date}</div></div>`;
  html += `<div class="stat-card"><div class="label">Last Order</div><div class="value">${h.till}</div></div>`;
  html += `<div class="stat-card"><div class="label">Total Spend</div><div class="value">₹${F(h.spend_total)}</div></div>`;
  for (const [k,v] of Object.entries(h.spend_parts||{})) {
    html += `<div class="stat-card"><div class="label">${k}</div><div class="value">₹${F(v)}</div></div>`;
  }
  html += '</div>';

  // Table 1: Arjuna Tea
  html += matrixTable(d.table1, 'Arjuna Tea (Core)');
  // Table 2: Black Leaf Tea
  html += matrixTable(d.table2, 'Black Leaf Tea');
  // Table 3: State Summary
  html += stateTable(d.table3);
  // Table 4: Account Summary
  html += accountTable(d.table4);
  // Table 5: Growth
  html += growthTable(d.table5, d.today_label, d.yest_label);
  // Table 6: Hourly
  html += hourlyTable(d.table6, d.today_label, d.yest_label);

  el.innerHTML = html;
}

function matrixTable(t, title) {
  if (!t.rows.length && t.totals.u === 0) return '';
  let h = `<div class="section"><div class="section-title">${title} <span class="badge">${t.totals.u} units</span></div>`;
  h += '<div class="tbl-wrap"><table><thead><tr>';
  h += '<th>Segment</th><th class="num">Orders</th><th class="num">Units</th><th class="num">CPT</th><th class="num">Gross</th><th class="num">Raw Spend</th><th>Account</th>';
  h += '</tr></thead><tbody>';
  for (const r of t.rows) {
    h += `<tr><td>${r.segment}</td><td class="num">${r.orders}</td><td class="num">${r.units}</td><td class="num"><b>₹${F(r.cpt)}</b></td><td class="num">₹${F(r.gross)}</td><td class="num">₹${F(r.raw_spend)}</td><td>${r.account}</td></tr>`;
  }
  h += '</tbody><tfoot><tr>';
  h += `<td><b>Total</b></td><td class="num">${t.totals.o}</td><td class="num">${t.totals.u}</td><td class="num"><b>₹${F(t.totals.cpt)}</b></td><td class="num">₹${F(Math.round(t.totals.g))}</td><td class="num">₹${F(Math.round(t.totals.s))}</td><td></td>`;
  h += '</tr></tfoot></table></div></div>';
  return h;
}

function stateTable(t) {
  if (!t.rows.length) return '';
  let h = '<div class="section"><div class="section-title">State Summary (Core) <span class="badge">' + t.totals.units + ' units</span></div>';
  h += '<div class="tbl-wrap"><table><thead><tr><th>#</th><th>State</th><th class="num">Orders</th><th class="num">Units</th><th class="num">CPT</th><th class="num">Gross</th><th class="num">Spend</th></tr></thead><tbody>';
  for (const r of t.rows) {
    h += `<tr><td>${r.rank}</td><td>${r.state}</td><td class="num">${r.orders}</td><td class="num">${r.units}</td><td class="num"><b>₹${F(r.cpt)}</b></td><td class="num">₹${F(r.gross)}</td><td class="num">₹${F(r.raw_spend)}</td></tr>`;
  }
  h += `</tbody><tfoot><tr><td></td><td><b>Total</b></td><td class="num">${t.totals.orders}</td><td class="num">${t.totals.units}</td><td class="num"><b>₹${F(t.totals.cpt)}</b></td><td class="num">₹${F(Math.round(t.totals.gross))}</td><td class="num">₹${F(Math.round(t.totals.raw_spend))}</td></tr></tfoot></table></div></div>`;
  return h;
}

function accountTable(t) {
  if (!t.rows.length) return '';
  let h = '<div class="section"><div class="section-title">Account Summary (Core) <span class="badge">' + t.totals.units + ' units</span></div>';
  h += '<div class="tbl-wrap"><table><thead><tr><th>Account</th><th class="num">Orders</th><th class="num">Units</th><th class="num">CPT</th><th class="num">Gross</th><th class="num">Spend</th></tr></thead><tbody>';
  for (const r of t.rows) {
    h += `<tr><td>${r.account}</td><td class="num">${r.orders}</td><td class="num">${r.units}</td><td class="num"><b>₹${F(r.cpt)}</b></td><td class="num">₹${F(r.gross)}</td><td class="num">₹${F(r.raw_spend)}</td></tr>`;
  }
  h += `</tbody><tfoot><tr><td><b>Total</b></td><td class="num">${t.totals.orders}</td><td class="num">${t.totals.units}</td><td class="num"><b>₹${F(t.totals.cpt)}</b></td><td class="num">₹${F(Math.round(t.totals.gross))}</td><td class="num">₹${F(Math.round(t.totals.raw_spend))}</td></tr></tfoot></table></div></div>`;
  return h;
}

function growthTable(t, today, yest) {
  if (!t.rows.length) return '';
  let h = '<div class="section"><div class="section-title">Account Growth (Core Units)</div>';
  h += `<div class="tbl-wrap"><table><thead><tr><th>Account</th><th class="num">${yest}</th><th class="num">${today}</th><th class="num">Growth</th></tr></thead><tbody>`;
  for (const r of t.rows) {
    const cls = r.growth.startsWith('+') ? 'up' : r.growth.startsWith('-') ? 'down' : 'even';
    const bold = r.account === 'Overall' ? 'font-weight:700' : '';
    h += `<tr style="${bold}"><td>${r.account}</td><td class="num">${r.units_yest}</td><td class="num">${r.units_today}</td><td class="num ${cls}">${r.growth}</td></tr>`;
  }
  h += '</tbody></table></div></div>';
  return h;
}

function hourlyTable(t, today, yest) {
  if (!t.rows.length) return '';
  let h = '<div class="section"><div class="section-title">Hour-by-Hour (Core)</div>';
  h += `<div class="tbl-wrap"><table><thead><tr><th>Slot</th><th class="num">${yest} Units</th><th class="num">${today} Units</th><th class="num">%</th><th>Trend</th><th class="num">${yest} Orders</th><th class="num">${today} Orders</th><th class="num">%</th><th>Trend</th></tr></thead><tbody>`;
  for (const r of t.rows) {
    const uc = r.units_trend === 'Up' ? 'up' : r.units_trend === 'Down' ? 'down' : 'even';
    const oc = r.orders_trend === 'Up' ? 'up' : r.orders_trend === 'Down' ? 'down' : 'even';
    h += `<tr><td>${r.slot}</td><td class="num">${r.units_yest}</td><td class="num">${r.units_today}</td><td class="num ${uc}">${r.units_pct}</td><td class="${uc}">${r.units_trend}</td><td class="num">${r.orders_yest}</td><td class="num">${r.orders_today}</td><td class="num ${oc}">${r.orders_pct}</td><td class="${oc}">${r.orders_trend}</td></tr>`;
  }
  const tt = t.totals;
  const uc2 = tt.units_trend === 'Up' ? 'up' : tt.units_trend === 'Down' ? 'down' : 'even';
  const oc2 = tt.orders_trend === 'Up' ? 'up' : tt.orders_trend === 'Down' ? 'down' : 'even';
  h += `</tbody><tfoot><tr><td><b>Total</b></td><td class="num">${tt.units_yest}</td><td class="num">${tt.units_today}</td><td class="num ${uc2}"><b>${tt.units_pct}</b></td><td class="${uc2}">${tt.units_trend}</td><td class="num">${tt.orders_yest}</td><td class="num">${tt.orders_today}</td><td class="num ${oc2}"><b>${tt.orders_pct}</b></td><td class="${oc2}">${tt.orders_trend}</td></tr></tfoot></table></div></div>`;
  return h;
}

// Theme
function toggleTheme() {
  const root = document.documentElement;
  const cur = root.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  try { localStorage.setItem('cm3-theme', next); } catch(e) {}
  document.getElementById('themeBtn').textContent = next === 'dark' ? '☀️' : '🌙';
}
try {
  const saved = localStorage.getItem('cm3-theme');
  if (saved) {
    document.documentElement.setAttribute('data-theme', saved);
    document.getElementById('themeBtn').textContent = saved === 'dark' ? '☀️' : '🌙';
  }
} catch(e) {}

// Init
document.getElementById('datePick').valueAsDate = new Date();
loadData();
</script>
</body>
</html>'''


@app.route('/')
def index():
    return DASHBOARD_HTML


@app.route('/api/cm3')
def api_cm3():
    date_str = request.args.get('date')
    if date_str:
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format, use YYYY-MM-DD'}), 400

    try:
        data = get_cm3(date_str)
        return jsonify(data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/cm3-excel')
def api_cm3_excel():
    import pandas as pd
    date_str = request.args.get('date')
    if date_str:
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date'}), 400

    try:
        data = get_cm3(date_str)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        # Sheet 1: Arjuna Tea
        t1 = data['table1']
        if t1['rows']:
            df1 = pd.DataFrame(t1['rows'])
            df1.to_excel(writer, sheet_name='Arjuna Tea', index=False)

        # Sheet 2: Black Leaf Tea
        t2 = data['table2']
        if t2['rows']:
            df2 = pd.DataFrame(t2['rows'])
            df2.to_excel(writer, sheet_name='Black Leaf Tea', index=False)

        # Sheet 3: State Summary
        t3 = data['table3']
        if t3['rows']:
            df3 = pd.DataFrame(t3['rows'])
            df3.to_excel(writer, sheet_name='State Summary', index=False)

        # Sheet 4: Account Summary
        t4 = data['table4']
        if t4['rows']:
            df4 = pd.DataFrame(t4['rows'])
            df4.to_excel(writer, sheet_name='Account Summary', index=False)

        # Sheet 5: Growth
        t5 = data['table5']
        if t5['rows']:
            df5 = pd.DataFrame(t5['rows'])
            df5.to_excel(writer, sheet_name='Growth', index=False)

        # Sheet 6: Hourly
        t6 = data['table6']
        if t6['rows']:
            df6 = pd.DataFrame(t6['rows'])
            df6.to_excel(writer, sheet_name='Hourly', index=False)

    buf.seek(0)
    date_label = date_str or _ist_now().strftime('%Y-%m-%d')
    return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=f'CM3_Report_{date_label}.xlsx')


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': _ist_now().isoformat()})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)

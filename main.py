import os
import logging
import json
import threading
import time
import requests
import websocket
import ssl
import urllib3
import atexit
import signal
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

                                                                              
logging.getLogger("werkzeug").setLevel(logging.ERROR)
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

                                                                                
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
FILES = {
    'hyperhub': os.path.join(DATA_DIR, 'data_hyperhub.json'),
    'altare':   os.path.join(DATA_DIR, 'data_altare.json'),
    'overnode': os.path.join(DATA_DIR, 'data_overnode.json'),
}

for path in FILES.values():
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump([], f)

def read_data(tool):
    try:
        with open(FILES[tool], 'r') as f:
            return json.load(f)
    except Exception:
        return []

_file_lock = threading.Lock()

def write_data(tool, data):
    with _file_lock:
        with open(FILES[tool], 'w') as f:
            json.dump(data, f, indent=4)

                                                                               
from collections import deque
MAX_LOGS  = 300
_log_lock = threading.Lock()
                                             
                                                                            
_log_seq  = {'hyperhub': 0, 'altare': 0, 'overnode': 0}                     
app_logs  = {
    'hyperhub': deque(maxlen=MAX_LOGS),
    'altare':   deque(maxlen=MAX_LOGS),
    'overnode': deque(maxlen=MAX_LOGS),
}

                                                                                
import queue as _queue
_client_queues     = []
_client_queues_lock = threading.Lock()

def add_log(tool, msg):
    if tool not in app_logs:
        return
    ts = time.time()
    with _log_lock:
        seq = _log_seq[tool]
        _log_seq[tool] += 1
        entry = {'seq': seq, 'timestamp': ts, 'message': msg}
        app_logs[tool].append(entry)
                                               
    payload = json.dumps({'tool': tool, 'seq': seq, 'timestamp': ts, 'message': msg})
    with _client_queues_lock:
        dead = []
        for q in _client_queues:
            try:
                q.put_nowait(payload)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _client_queues.remove(q)
    if any(k in msg.lower() for k in ('error', 'fail', 'exception', 'lỗi', 'stuck', 'warn', 'timeout')):
        print(f"[{time.strftime('%H:%M:%S')}] [{tool.upper()}] {msg}")

                                                                                
app_state = {'hyperhub': {}, 'altare': {}, 'overnode': {}}

def get_account_state(tool, email):
    if email not in app_state[tool]:
        app_state[tool][email] = {
            'running': False,
            'balance': 0.0,
            'stop_event': threading.Event(),
        }
    return app_state[tool][email]

                                                                                
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoLab AFK Manager</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:        #070910;
    --surface:   #0d1117;
    --surface2:  #161b22;
    --border:    #21262d;
    --border2:   #30363d;
    --text:      #e6edf3;
    --muted:     #7d8590;
    --accent-hh: #58a6ff;
    --accent-al: #bc8cff;
    --accent-on: #3fb950;
    --accent-err:#f85149;
    --accent-warn:#d29922;
    --font: 'JetBrains Mono', monospace;
    --font2: 'Space Mono', monospace;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    min-height: 100vh;
    overflow-x: hidden;
  }

  
  body::before {
    content: '';
    position: fixed; inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.03) 2px,
      rgba(0,0,0,0.03) 4px
    );
    pointer-events: none;
    z-index: 9999;
  }

  
  .wrapper {
    max-width: 1080px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
  }

  
  header {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
  }
  .logo {
    width: 36px; height: 36px;
    background: linear-gradient(135deg, var(--accent-hh), var(--accent-al));
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.1rem;
    flex-shrink: 0;
  }
  header h1 {
    font-family: var(--font2);
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--text);
  }
  header p {
    font-size: 0.75rem;
    color: var(--muted);
    margin-top: 2px;
  }
  .status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent-on);
    margin-left: auto;
    box-shadow: 0 0 8px var(--accent-on);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.4; }
  }

  
  .tabs {
    display: flex;
    gap: 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    background: var(--surface);
  }
  .tab-btn {
    flex: 1;
    background: transparent;
    color: var(--muted);
    font-family: var(--font);
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    padding: 0.7rem 0.5rem;
    border: none;
    border-right: 1px solid var(--border);
    cursor: pointer;
    transition: all 0.15s;
    text-transform: uppercase;
    position: relative;
  }
  .tab-btn:last-child { border-right: none; }
  .tab-btn:hover { background: var(--surface2); color: var(--text); }
  .tab-btn.active { color: var(--text); background: var(--surface2); }
  .tab-btn.active::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 2px;
  }

  
  .grid {
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: 1rem;
  }
  @media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }

  
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.25rem;
  }
  .panel-title {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .panel-title::before {
    content: '';
    width: 3px; height: 12px;
    border-radius: 2px;
    background: currentColor;
  }

  
  .form-group {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    margin-bottom: 0.75rem;
  }
  .input-label {
    font-size: 0.7rem;
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  input[type=text], input[type=password] {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-family: var(--font);
    font-size: 0.85rem;
    padding: 0.65rem 0.9rem;
    transition: border-color 0.15s, box-shadow 0.15s;
    outline: none;
  }
  input:focus {
    border-color: var(--accent-hh);
    box-shadow: 0 0 0 3px rgba(88,166,255,0.1);
  }

  .btn-primary {
    width: 100%;
    background: var(--accent-hh);
    color: #000;
    font-family: var(--font);
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 0.7rem;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    transition: opacity 0.15s, transform 0.1s;
    margin-top: 0.5rem;
  }
  .btn-primary:hover   { opacity: 0.85; }
  .btn-primary:active  { transform: scale(0.98); }
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }

  
  .account-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-height: 280px;
    overflow-y: auto;
    padding-right: 2px;
  }
  .account-list::-webkit-scrollbar { width: 4px; }
  .account-list::-webkit-scrollbar-track { background: transparent; }
  .account-list::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }

  .acc-item {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.7rem 0.9rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    transition: border-color 0.15s;
  }
  .acc-item:hover { border-color: var(--border2); }
  .acc-item.running { border-left: 2px solid var(--accent-on); }
  .acc-item.paused  { border-left: 2px solid var(--accent-warn); }

  .acc-info { flex: 1; min-width: 0; }
  .acc-email {
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .acc-balance {
    font-size: 0.72rem;
    color: var(--muted);
    margin-top: 3px;
  }
  .acc-balance span { color: var(--accent-on); }

  .acc-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .btn-sm {
    background: var(--surface2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 4px 10px;
    font-family: var(--font);
    font-size: 0.7rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .btn-sm:hover { border-color: var(--border2); }
  .btn-run  { color: var(--accent-on);  border-color: rgba(63,185,80,0.3); }
  .btn-run:hover  { background: rgba(63,185,80,0.1); }
  .btn-pause { color: var(--accent-warn); border-color: rgba(210,153,34,0.3); }
  .btn-pause:hover { background: rgba(210,153,34,0.1); }
  .btn-del  { color: var(--accent-err); border-color: rgba(248,81,73,0.3); }
  .btn-del:hover  { background: rgba(248,81,73,0.1); }

  .empty-state {
    text-align: center;
    color: var(--muted);
    font-size: 0.78rem;
    padding: 2rem 0;
  }

  
  .terminal {
    background: #000;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  .term-header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0.6rem 1rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .term-dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    background: var(--border2);
  }
  .term-dot.red   { background: #ff5f56; }
  .term-dot.yel   { background: #ffbd2e; }
  .term-dot.grn   { background: #27c93f; }
  .term-title {
    font-size: 0.7rem;
    color: var(--muted);
    letter-spacing: 0.08em;
    margin-left: auto;
  }
  .term-body {
    padding: 1rem;
    height: 300px;
    overflow-y: scroll;
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 0.8rem;
    line-height: 1.6;
    scrollbar-width: none;
  }
  .term-body::-webkit-scrollbar { display: none; }
  .term-body::-webkit-scrollbar { width: 4px; }
  .term-body::-webkit-scrollbar-track { background: transparent; }
  .term-body::-webkit-scrollbar-thumb { background: #222; border-radius: 2px; }

  .log-line { display: flex; gap: 0.6rem; }
  .log-ts   { color: #3d4451; flex-shrink: 0; }
  .log-msg  { color: #4ec994; word-break: break-all; }
  .log-msg.err  { color: var(--accent-err); }
  .log-msg.warn { color: var(--accent-warn); }
  .log-msg.info { color: var(--accent-hh); }
  .log-msg.sys  { color: var(--muted); }

  
  .toast {
    position: fixed;
    bottom: 1.5rem; right: 1.5rem;
    background: var(--surface2);
    border: 1px solid var(--border2);
    border-radius: 8px;
    padding: 0.75rem 1.25rem;
    font-size: 0.82rem;
    color: var(--text);
    transform: translateY(120%);
    opacity: 0;
    transition: all 0.25s cubic-bezier(0.4,0,0.2,1);
    z-index: 10000;
    max-width: 320px;
    border-left: 3px solid var(--accent-on);
  }
  .toast.show { transform: translateY(0); opacity: 1; }
  .toast.err  { border-left-color: var(--accent-err); }
</style>
</head>
<body>
<div class="wrapper">

  <!-- Header -->
  <header>
    <div class="logo">⚡</div>
    <div>
      <h1>AUTOLAB AFK MANAGER</h1>
      <p>Multi-platform AFK automation system</p>
    </div>
    <div class="status-dot" title="Server Online"></div>
  </header>

  <!-- Tabs -->
  <div class="tabs">
    <button class="tab-btn active" id="tab-hyperhub" onclick="switchTab('hyperhub')">⬡ Hyper-Hub</button>
    <button class="tab-btn" id="tab-altare"   onclick="switchTab('altare')">◈ Altare</button>
    <button class="tab-btn" id="tab-overnode" onclick="switchTab('overnode')">◉ Overnode</button>
  </div>

  <!-- Middle Grid -->
  <div class="grid">
    <!-- Add Account Panel -->
    <div class="panel">
      <div class="panel-title" id="panel-title-form">Add Account</div>

      <form onsubmit="addAccount(event)">
        <input type="hidden" id="current-tool" value="hyperhub">

        <div id="ep-inputs">
          <div class="form-group">
            <label class="input-label">Email</label>
            <input type="text" id="email" placeholder="user@example.com" autocomplete="off">
          </div>
          <div class="form-group">
            <label class="input-label">Password</label>
            <input type="password" id="password" placeholder="••••••••" autocomplete="off">
          </div>
        </div>

        <div id="ck-input" style="display:none;">
          <div class="form-group">
            <label class="input-label">Cookie</label>
            <input type="text" id="cookie" placeholder="session=..." autocomplete="off">
          </div>
        </div>

        <button type="submit" class="btn-primary" id="submit-btn">Login &amp; Start AFK</button>
      </form>
    </div>

    <!-- Manager Panel -->
    <div class="panel">
      <div class="panel-title" id="panel-title-mgr">Account Manager</div>
      <div class="account-list" id="account-list">
        <div class="empty-state">No accounts yet.</div>
      </div>
    </div>
  </div>

  <!-- Terminal -->
  <div class="terminal">
    <div class="term-header">
      <div class="term-dot red"></div>
      <div class="term-dot yel"></div>
      <div class="term-dot grn"></div>
      <div class="term-title" id="term-title">HYPERHUB — CONSOLE</div>
    </div>
    <div class="term-body" id="terminal">
      <div class="log-line">
        <span class="log-ts">[system]</span>
        <span class="log-msg sys">Ready. Add an account to start.</span>
      </div>
    </div>
  </div>

</div>

<div class="toast" id="toast"></div>

<script>
  let currentTool = 'hyperhub';

  const lastSeq = { hyperhub: -1, altare: -1, overnode: -1 };

  const TOOL_COLORS = {
    hyperhub: 'var(--accent-hh)',
    altare:   'var(--accent-al)',
    overnode: 'var(--accent-on)',
  };

  document.addEventListener('DOMContentLoaded', async () => {
    loadAccounts();

    await Promise.all(['hyperhub','altare','overnode'].map(t => seedBuffer(t)));

    replayLogs(currentTool);

    startSSE();
  });

  function switchTab(tool) {
    currentTool = tool;
    document.getElementById('current-tool').value = tool;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(`tab-${tool}`).classList.add('active');
    document.getElementById('term-title').textContent = `${tool.toUpperCase()} — CONSOLE`;

    const epInputs = document.getElementById('ep-inputs');
    const ckInput  = document.getElementById('ck-input');
    if (tool === 'overnode') {
      epInputs.style.display = 'none';
      ckInput.style.display  = 'block';
      document.getElementById('cookie').value = '';
    } else {
      epInputs.style.display = 'block';
      ckInput.style.display  = 'none';
      document.getElementById('email').value    = '';
      document.getElementById('password').value = '';
    }

    loadAccounts();

    const term = document.getElementById('terminal');
    term.innerHTML = '';
    replayLogs(tool);
  }

  async function addAccount(e) {
    e.preventDefault();
    const btn = document.getElementById('submit-btn');
    btn.textContent = 'Processing…';
    btn.disabled = true;

    const payload = { tool: currentTool };

    if (currentTool === 'overnode') {
      const cookie = document.getElementById('cookie').value.trim();
      if (!cookie) { toast('Please enter a Cookie', true); resetBtn(btn); return; }
      payload.cookie = cookie;
      payload.email  = 'Cookie_' + Math.floor(Math.random() * 9000 + 1000);
    } else {
      const email    = document.getElementById('email').value.trim();
      const password = document.getElementById('password').value.trim();
      if (!email || !password) { toast('Please fill in all fields', true); resetBtn(btn); return; }
      payload.email    = email;
      payload.password = password;
    }

    try {
      const res  = await fetch('/api/add_account', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      const data = await res.json();
      if (data.success) {
        toast('Account added successfully!');
        loadAccounts();
        if (currentTool === 'overnode') document.getElementById('cookie').value = '';
        else { document.getElementById('email').value = ''; document.getElementById('password').value = ''; }
      } else {
        toast(data.message || 'Error occurred', true);
      }
    } catch (err) {
      toast('Server connection error', true);
    } finally {
      resetBtn(btn);
    }
  }

  async function loadAccounts() {
    try {
      const res      = await fetch(`/api/get_accounts?tool=${currentTool}`);
      const accounts = await res.json();
      const listEl   = document.getElementById('account-list');
      listEl.innerHTML = '';

      if (!accounts || accounts.length === 0) {
        listEl.innerHTML = '<div class="empty-state">No accounts yet.</div>';
        return;
      }

      accounts.forEach((acc, idx) => {
        const item = document.createElement('div');
        item.className = `acc-item ${acc.running ? 'running' : 'paused'}`;

        const name    = currentTool === 'overnode' ? (acc.email || 'Cookie Auth') : acc.email;
        const unit    = currentTool === 'hyperhub' ? 'XPL' : currentTool === 'altare' ? 'CR' : 'coins';

        const rawBal  = liveBalances[acc.email] !== undefined ? liveBalances[acc.email] : acc.balance;
        const bal     = rawBal !== undefined ? parseFloat(rawBal).toFixed(4) : '0.0000';

        item.innerHTML = `
          <div class="acc-info">
            <div class="acc-email">${name}</div>
            <div class="acc-balance">Balance: <span>${bal} ${unit}</span></div>
          </div>
          <div class="acc-actions">
            <button class="btn-sm ${acc.running ? 'btn-pause' : 'btn-run'}"
              onclick="toggleAccount('${currentTool}','${acc.email}')">
              ${acc.running ? 'Pause' : 'Run'}
            </button>
            <button class="btn-sm btn-del" onclick="deleteAccount('${currentTool}',${idx},'${acc.email}')">Del</button>
          </div>
        `;
        listEl.appendChild(item);
      });
    } catch (err) {
      console.error('loadAccounts failed', err);
    }
  }

  async function toggleAccount(tool, email) {
    const res    = await fetch('/api/toggle', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({tool, email}) });
    const result = await res.json();
    if (result.success) loadAccounts();
    else toast('Toggle failed', true);
  }

  async function deleteAccount(tool, index, email) {
    if (!confirm(`Delete account: ${email}?`)) return;
    const res  = await fetch('/api/delete_account', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({tool, index, email}) });
    const data = await res.json();
    if (data.success) { toast('Account deleted'); loadAccounts(); }
    else toast(data.message || 'Delete failed', true);
  }

  function resetBtn(btn) {
    btn.textContent = 'Login & Start AFK';
    btn.disabled = false;
  }

  function toast(msg, isErr = false) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className   = 'toast show' + (isErr ? ' err' : '');
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.classList.remove('show'); }, 3200);
  }

  const liveBalances = {};

  function extractBalanceFromLog(msg) {

    const emailMatch = msg.match(/^\[([^\]]+)\]/);
    if (!emailMatch) return;
    const email = emailMatch[1];

    let balance = null;

    const incrMatch = msg.match(/increased:\s*[\d.]+\s*[→>]\s*([\d.]+)/i);
    if (incrMatch) balance = parseFloat(incrMatch[1]);

    if (balance === null) {
      const balMatch = msg.match(/Balance:\s*([\d.]+)/i);
      if (balMatch) balance = parseFloat(balMatch[1]);
    }

    if (balance === null) {
      const curMatch = msg.match(/Current:\s*([\d.]+)/i);
      if (curMatch) balance = parseFloat(curMatch[1]);
    }

    if (balance !== null && !isNaN(balance)) {
      liveBalances[email] = balance;
      updateBalanceDOM(email, balance);
    }
  }

  function updateBalanceDOM(email, balance) {

    const items = document.querySelectorAll('.acc-item');
    const unit  = currentTool === 'hyperhub' ? 'XPL' : currentTool === 'altare' ? 'CR' : 'coins';
    items.forEach(item => {
      const emailEl = item.querySelector('.acc-email');
      if (emailEl && emailEl.textContent.trim() === email) {
        const balEl = item.querySelector('.acc-balance span');
        if (balEl) balEl.textContent = `${balance.toFixed(4)} ${unit}`;
      }
    });
  }

  const logBuffer = { hyperhub: [], altare: [], overnode: [] };
  const MAX_BUFFER = 300;

  function renderLine(log, term) {
    const div = document.createElement('div');
    div.className = 'log-line';
    const ts  = new Date(log.timestamp * 1000).toLocaleTimeString('en-GB');
    const msg = log.message;
    let cls = '';
    if (/error|lỗi|failed|exception/i.test(msg))      cls = 'err';
    else if (/warn|stuck|conflict/i.test(msg))         cls = 'warn';
    else if (/login|connect|start|success/i.test(msg)) cls = 'info';
    div.innerHTML = `<span class="log-ts">[${ts}]</span><span class="log-msg ${cls}">${msg}</span>`;
    term.appendChild(div);
    term.scrollTop = term.scrollHeight;
  }

  async function seedBuffer(tool) {
    try {
      const res  = await fetch(`/api/logs?tool=${tool}&after=-1`);
      const data = await res.json();
      if (!data.logs) return;
      data.logs.forEach(log => {
        log.tool = tool;
        if (lastSeq[tool] >= log.seq) return;
        lastSeq[tool] = log.seq;
        logBuffer[tool].push(log);
        if (logBuffer[tool].length > MAX_BUFFER) logBuffer[tool].shift();
        extractBalanceFromLog(log.message);
      });
    } catch(e) {}
  }

  function replayLogs(tool) {
    const term = document.getElementById('terminal');
    logBuffer[tool].forEach(log => renderLine(log, term));
    term.scrollTop = term.scrollHeight;
  }

  let _evtSource = null;

  function startSSE() {
    if (_evtSource) { _evtSource.close(); _evtSource = null; }
    _evtSource = new EventSource('/api/stream_logs');

    _evtSource.onmessage = function(e) {
      if (!e.data || e.data.trim() === '') return;
      let log;
      try { log = JSON.parse(e.data); } catch { return; }

      const tool = log.tool;
      if (!tool || !logBuffer[tool]) return;

      if (lastSeq[tool] >= log.seq) return;
      lastSeq[tool] = log.seq;

      logBuffer[tool].push(log);
      if (logBuffer[tool].length > MAX_BUFFER) logBuffer[tool].shift();

      if (currentTool === tool) {
        renderLine(log, document.getElementById('terminal'));
      }
      extractBalanceFromLog(log.message);
    };

    _evtSource.onerror = function() {
      if (_evtSource) { _evtSource.close(); _evtSource = null; }
      setTimeout(startSSE, 3000);
    };
  }

  setInterval(loadAccounts, 15000);
</script>
</body>
</html>
"""

                                                                                
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/get_accounts')
def get_accounts():
    tool = request.args.get('tool')
    if tool not in FILES:
        return jsonify([])
    accounts = read_data(tool)
    for acc in accounts:
        email = acc.get('email', '')
        st    = app_state[tool].get(email, {})
        acc['running'] = st.get('running', False)
        acc['balance'] = st.get('balance', 0.0)
        acc.pop('password', None)
        acc.pop('cookie', None)
    return jsonify(accounts)

@app.route('/api/add_account', methods=['POST'])
def add_account():
    data = request.json
    tool = data.get('tool')
    if tool not in FILES:
        return jsonify({'success': False, 'message': 'Invalid tool'})

    accounts = read_data(tool)
    email    = data.get('email', '')

                    
    if any(a.get('email') == email for a in accounts):
        return jsonify({'success': False, 'message': 'Account already exists'})

    if tool == 'overnode':
        new_acc = {'email': email, 'cookie': data.get('cookie')}

    elif tool == 'altare':
                                                                       
        password = data.get('password', '')
        try:
            login_r = requests.post(
                'https://api.altare.sh/api/auth/login',
                headers={
                    'Content-Type': 'application/json',
                    'Accept':       'application/json',
                    'Origin':       'https://altare.sh',
                    'User-Agent':   'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                },
                json={'identifier': email, 'password': password},
                timeout=15,
            )
            if login_r.status_code != 200:
                return jsonify({'success': False, 'message': f'Login failed: HTTP {login_r.status_code}'})

            token = f"Bearer {login_r.json().get('token', '')}"
            if not token or token == 'Bearer ':
                return jsonify({'success': False, 'message': 'No token returned'})

                           
            t_r = requests.get(
                'https://api.altare.sh/api/tenants',
                headers={'Authorization': token, 'Accept': 'application/json',
                         'Origin': 'https://altare.sh'},
                timeout=10,
            )
            tenant_id = ''
            if t_r.status_code == 200 and t_r.json().get('items'):
                tenant_id = t_r.json()['items'][0].get('id', '')

            if not tenant_id:
                return jsonify({'success': False, 'message': 'No tenant ID found for this account'})

            new_acc = {
                'email':     email,
                'password':  password,
                'token':     token,
                'tenant_id': tenant_id,
            }
        except Exception as e:
            return jsonify({'success': False, 'message': f'Login error: {e}'})

    else:
        new_acc = {'email': email, 'password': data.get('password', '')}

    accounts.append(new_acc)
    write_data(tool, accounts)
    add_log(tool, f"Account added: {email}")
    start_worker_thread(tool, new_acc)
    return jsonify({'success': True})

@app.route('/api/delete_account', methods=['POST'])
def delete_account():
    data  = request.json
    tool  = data.get('tool')
    idx   = data.get('index')
    email = data.get('email')
    try:
        if tool in FILES and isinstance(idx, int):
            accounts = read_data(tool)
            if 0 <= idx < len(accounts):
                del_email = accounts[idx].get('email', email)
                stop_worker_thread(tool, del_email)
                app_state[tool].pop(del_email, None)
                accounts.pop(idx)
                write_data(tool, accounts)
                add_log(tool, f"Account deleted: {del_email}")
                return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Index out of bounds'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/toggle', methods=['POST'])
def toggle_account():
    data  = request.json
    tool  = data.get('tool')
    email = data.get('email')
    if tool not in app_state or email not in app_state[tool]:
        return jsonify({'success': False})
    st = app_state[tool][email]
    if st['running']:
        st['running'] = False
        st['stop_event'].set()
        add_log(tool, f"[{email}] AFK paused by user.")
    else:
        st['running'] = True
        st['stop_event'].clear()
        add_log(tool, f"[{email}] AFK resumed by user.")
        acc = next((a for a in read_data(tool) if a.get('email') == email), None)
        if acc:
            start_worker_thread(tool, acc)
    return jsonify({'success': True})

@app.route('/api/logs')
def get_logs():
    """Fallback HTTP endpoint — dùng khi SSE không available"""
    tool       = request.args.get('tool', 'hyperhub')
    after_seq  = int(request.args.get('after', -1))
    if tool not in app_logs:
        return jsonify({'logs': [], 'last_seq': 0})
    with _log_lock:
        entries  = [e for e in app_logs[tool] if e['seq'] > after_seq]
        next_seq = _log_seq[tool] - 1
    return jsonify({'logs': entries, 'last_seq': next_seq})

@app.route('/api/stream_logs')
def stream_logs():
    """SSE endpoint — server push, không polling"""
    from flask import Response
                                                                             
    tool = request.args.get('tool', '')

    def event_stream(tool):
        q = _queue.Queue(maxsize=200)
        with _client_queues_lock:
            _client_queues.append(q)
                                                                  
                                                
        try:
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f"data: {data}\n\n"
                except _queue.Empty:
                    yield f": ping\n\n"             
        except GeneratorExit:
            pass
        finally:
            with _client_queues_lock:
                if q in _client_queues:
                    _client_queues.remove(q)
    return Response(
        event_stream(tool),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection':        'keep-alive',
        }
    )

@app.route('/api/add_log', methods=['POST'])
def handle_add_log():
    data = request.json
    tool = data.get('tool')
    msg  = data.get('message', '')
    if msg and tool:
        add_log(tool, f"[WebUI] {msg}")
    return jsonify({'success': True})

                                                                                
def start_worker_thread(tool, acc):
    email = acc.get('email')
    if not email:
        return
    st = get_account_state(tool, email)
    if st['running']:
        return
    st['stop_event'].clear()
    st['running'] = True
    targets = {'hyperhub': hyperhub_worker, 'altare': altare_worker, 'overnode': overnode_worker}
    if tool not in targets:
        return
    t = threading.Thread(target=targets[tool], args=(acc, st), daemon=True)
    st['thread'] = t
    t.start()

def stop_worker_thread(tool, email):
    if tool in app_state and email in app_state[tool]:
        st = app_state[tool][email]
        st['running'] = False
        st['stop_event'].set()

                                                                                
def make_altare_headers(token, tenant_id=''):
    h = {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Origin': 'https://altare.sh',
        'Referer': 'https://altare.sh/billing',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    if tenant_id:
        h['altare-selected-tenant-id'] = tenant_id
    return h

def altare_worker(account, state):
    """
    Port đầy đủ từ altare_farm.py gốc:
    - SSE stream để giữ kết nối ổn định
    - Heartbeat mỗi 30s
    - Stats + stuck detection mỗi 120s
    - Token refresh mỗi 30 phút
    """
    ident     = account['email']
    password  = account.get('password', '')
    tenant_id = account.get('tenant_id', '')

    BASE_API  = 'https://api.altare.sh'
    BASE_WEB  = 'https://altare.sh'

    def headers(token='', with_tenant=True):
        h = {
            'Authorization': token or account.get('token', ''),
            'Content-Type':  'application/json',
            'Accept':        'application/json',
            'Origin':        BASE_WEB,
            'Referer':       f'{BASE_WEB}/billing',
            'User-Agent':    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        if with_tenant and tenant_id:
            h['altare-selected-tenant-id'] = tenant_id
        return h

    def alive():
        return state['running'] and not state['stop_event'].is_set()

    def get_balance():
        try:
            r = requests.get(f'{BASE_API}/api/tenants', headers=headers(), timeout=10)
            if r.status_code == 200:
                for item in r.json().get('items', []):
                    if item.get('id') == tenant_id:
                        cents = item.get('creditsCents')
                        return round(cents / 100, 4) if cents is not None else None
        except Exception:
            pass
        return None

    def afk_start(retries=3):
        for _ in range(retries):
            try:
                r = requests.post(f'{BASE_API}/api/tenants/{tenant_id}/rewards/afk/start',
                                  headers=headers(), json={}, timeout=10)
                if r.status_code in (200, 201, 204):
                    return True
            except Exception:
                pass
            afk_stop()
            time.sleep(5)
        return False

    def afk_stop():
        try:
            requests.post(f'{BASE_API}/api/tenants/{tenant_id}/rewards/afk/stop',
                          headers=headers(), json={}, timeout=10)
        except Exception:
            pass

    def heartbeat():
        try:
            r = requests.post(f'{BASE_API}/api/tenants/{tenant_id}/rewards/afk/heartbeat',
                              headers=headers(), json={}, timeout=10)
            return r.status_code in (200, 201, 204)
        except Exception:
            return False

    if not tenant_id:
        add_log('altare', f'[{ident}] Missing tenant_id — aborting')
        state['running'] = False
        return

                                                                               
    afk_stop()
    time.sleep(0.5)
    if afk_start():
        add_log('altare', f'[{ident}] AFK started ✓')
    else:
        add_log('altare', f'[{ident}] AFK start failed')

                                                                              
    def sse_loop():
        first = True
        while alive() and state.get('is_farming', True):
            try:
                token = account.get('token', '')
                raw   = token.replace('Bearer ', '')
                url   = f'{BASE_API}/subscribe?token={raw}'
                h     = headers()
                h['Accept']         = 'text/event-stream'
                h['Cache-Control']  = 'no-cache'
                with requests.get(url, headers=h, stream=True, timeout=(10, None)) as r:
                    if r.status_code == 200:
                        if first:
                            add_log('altare', f'[{ident}] SSE stream connected ✓')
                            first = False
                        for _ in r.iter_lines(chunk_size=1):
                            if not alive() or not state.get('is_farming', True):
                                break
                    else:
                        first = True
                        time.sleep(10)
            except Exception:
                first = True
                time.sleep(10)
            time.sleep(3)

                                                                               
    def heartbeat_loop():
        while alive():
            if state.get('is_farming', True):
                heartbeat()
            for _ in range(30):
                if not alive():
                    break
                time.sleep(1)

                                                                               
    def stats_loop():
        credits_start = None
        last_balance  = None
        stuck_count   = 0
        while alive():
            if not state.get('is_farming', True):
                time.sleep(5)
                continue
            bal = get_balance()
            if bal is not None:
                state['balance'] = bal
                if credits_start is None:
                    credits_start = bal
                earned = round(bal - credits_start, 4)
                if bal != last_balance:
                    add_log('altare', f'[{ident}] +{earned:g} CR | Balance: {bal:g}')
                    stuck_count = 0
                    last_balance = bal
                else:
                    stuck_count += 1
                    if stuck_count >= 3:
                        add_log('altare', f'[{ident}] Balance stuck {stuck_count}x — resetting AFK...')
                        state['is_farming'] = False
                        afk_stop()
                        time.sleep(5)
                        if afk_start():
                            state['is_farming'] = True
                            stuck_count = 0
                            credits_start = bal
                        else:
                            add_log('altare', f'[{ident}] Reset failed, retrying next cycle')
            for _ in range(120):
                if not alive():
                    break
                time.sleep(1)

                                                                              
    def token_refresh_loop():
        while alive():
            for _ in range(1800):
                if not alive():
                    break
                time.sleep(1)
            if not alive() or not password:
                continue
            try:
                r = requests.post(f'{BASE_API}/api/auth/login',
                                  headers=headers(token=''),
                                  json={'identifier': ident, 'password': password},
                                  timeout=10)
                if r.status_code == 200:
                    new_tok = f"Bearer {r.json().get('token')}"
                    account['token'] = new_tok
            except Exception:
                pass

                               
    state['is_farming'] = True
    for fn in (sse_loop, heartbeat_loop, stats_loop, token_refresh_loop):
        threading.Thread(target=fn, daemon=True).start()

                                          
    while alive():
        time.sleep(1)

             
    state['is_farming'] = False
    afk_stop()

                                                                                
                                                 
                                                           
                                                    
                                                                                          
                                                                     
                                                                                
                                 
                                                                               
                        
                           
def hyperhub_worker(account, state):
    ident    = account['email']
    password = account['password']

    BASE_URL   = 'https://hyper-hub.nl'
    WS_URL     = 'wss://hyper-hub.nl/ws'
    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    pass                

    session     = requests.Session()
    cookies_str = ''

    def sleep_interruptible(secs):
        for _ in range(secs):
            if not state['running'] or state['stop_event'].is_set():
                return False
            time.sleep(1)
        return True

    def do_login():
        nonlocal cookies_str, session
        session = requests.Session()
        try:
            r = session.post(
                f'{BASE_URL}/auth/login',
                json={'email': ident, 'password': password},
                headers={'User-Agent': USER_AGENT, 'Content-Type': 'application/json'},
                timeout=15, verify=False,
            )
            if r.status_code == 200:
                cookies_str = '; '.join(f'{c.name}={c.value}' for c in session.cookies)
                add_log('hyperhub', f'[{ident}] Login successful ✓')
                return True
            add_log('hyperhub', f'[{ident}] Login failed — HTTP {r.status_code}')
        except Exception as e:
            add_log('hyperhub', f'[{ident}] Login error: {e}')
        cookies_str = ''
        return False

    def get_balance():
        try:
            r = session.get(f'{BASE_URL}/wallet/balance', timeout=10, verify=False)
            if r.status_code == 200:
                return float(r.json().get('XPL', 0.0))
        except Exception:
            pass
        return None

                                                                               
    if not do_login():
        if not sleep_interruptible(60):
            return

                                                                               
    while state['running'] and not state['stop_event'].is_set():
        if not cookies_str:
            if not do_login():
                if not sleep_interruptible(60):
                    break
                continue

                                    
        close_info = {'code': None, 'conflict': False, 'expired': False}

        def on_open(ws):
            pass           

        last_nri  = [99999]  # Khởi tạo cao để detection đầu tiên hoạt động
        last_bal  = [state.get('balance', 0.0)]

        def on_message(ws, raw):
            try:
                data = json.loads(raw)
                if data.get('type') == 'afk_state':
                    cpm = data.get('coinsPerMinute', 0)
                    nri = data.get('nextRewardIn', 0)
                    state['coins_per_min'] = cpm

                                                               
                    if last_nri[0] is not None and last_nri[0] <= 3000 and nri > 5000:
                        bal = get_balance()
                        if bal is not None:
                            state['balance'] = bal
                            if bal > last_bal[0]:
                                inc = round(bal - last_bal[0], 2)
                                add_log('hyperhub', f'[{ident}] +{inc} XPL | Balance: {bal}')
                            last_bal[0] = bal

                    last_nri[0] = nri
            except Exception:
                pass

        def on_error(ws, err):
                                                                        
                                                                    
            try:
                raw = err.args[0] if err.args else b''
                if isinstance(raw, (bytes, bytearray)) and len(raw) >= 2:
                    code = int.from_bytes(raw[:2], 'big')
                    close_info['code'] = code
                    if code == 4002:
                        close_info['conflict'] = True
                        pass        
                        return
                    if code == 4001:
                        close_info['expired'] = True
                        pass        
                        return
            except Exception:
                pass
            if 'already connected' in str(err).lower():
                close_info['conflict'] = True
                pass        
            else:
                add_log('hyperhub', f'[{ident}] WS error: {err}')

        def on_close(ws, code, msg):
            if code == 4002: close_info['conflict'] = True
            elif code == 4001: close_info['expired'] = True
            pass            

        ws_app = websocket.WebSocketApp(
            WS_URL,
            header={'User-Agent': USER_AGENT, 'Cookie': cookies_str, 'Origin': 'https://hyper-hub.nl/'},
            on_open=on_open, on_message=on_message,
            on_error=on_error, on_close=on_close,
        )

                                                     
        recycle_timer = threading.Timer(300, ws_app.close)
        recycle_timer.daemon = True

        wst = threading.Thread(
            target=ws_app.run_forever,
            kwargs={'sslopt': {'cert_reqs': ssl.CERT_NONE}, 'ping_interval': 30},
            daemon=True,
        )
        wst.start()
        recycle_timer.start()

                                                                     
        bal = get_balance()
        if bal is not None:
            state['balance'] = bal
            last_bal[0] = bal  # sync để lần đầu reward không tính từ 0

                                                                               
        while wst.is_alive() and state['running'] and not state['stop_event'].is_set():
            time.sleep(1)

        recycle_timer.cancel()
        ws_app.close()

        if not state['running'] or state['stop_event'].is_set():
            break

                                                                              
        if close_info['conflict']:
                                                              
                                                                          
                                                                            
            pass              
            if not sleep_interruptible(8):
                break
        elif close_info['expired']:
            pass                   
            cookies_str = ''
            if not sleep_interruptible(5):
                break
        else:
                                 
            if not sleep_interruptible(5):
                break

                                                                                 
def overnode_worker(account, state):
    ident  = account['email']
    cookie = account['cookie']
    HOST   = 'console.overnode.fr'
    WS_URL = f'wss://{HOST}/api/afk/ws'
    ORIGIN = f'https://{HOST}'

    http_session = requests.Session()
    http_session.headers.update({
        'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept':          'application/json',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer':         f'{ORIGIN}/wallet',
        'Origin':          ORIGIN,
        'Cookie':          cookie,
    })
    for part in cookie.split(';'):
        if '=' in part:
            k, v = part.strip().split('=', 1)
            http_session.cookies.set(k, v)

    initial_balance  = [None]
    last_nri         = [None]   # lastNextRewardIn (ms) — dùng detect reward giống source gốc
    total_earned     = [0.0]

    def get_balance():
        try:
            r = http_session.get(f'https://{HOST}/api/wallet/balance', timeout=10, verify=False)
            if r.status_code == 200:
                return float(r.json().get('balance', 0.0))
        except Exception:
            pass
        return None

    def on_open(ws):
        add_log('overnode', f'[{ident}] WS connected 🟢')
        bal = get_balance()
        if bal is not None:
            initial_balance[0] = bal
            state['balance']   = bal
            add_log('overnode', f'[{ident}] Balance: {bal} coins')

    def on_message(ws, message):
        try:
            data = json.loads(message)
            if data.get('type') != 'afk_state':
                return

            cpm = data.get('coinsPerMinute', 0)
            nri = data.get('nextRewardIn', 0)   # milliseconds đến reward kế tiếp

            # ─ Detect reward: nextRewardIn tăng đột biến (reset sau khi phát thưởng)
            # Logic y hệt source JS gốc:
            #   if (lastNextRewardIn !== null && nextRewardIn > lastNextRewardIn + 5000)
            if last_nri[0] is not None and nri > last_nri[0] + 5000:
                total_earned[0] = round(total_earned[0] + cpm, 4)
                # Lấy balance thực sau mỗi reward
                bal = get_balance()
                if bal is not None:
                    state['balance'] = bal
                    if initial_balance[0] is None:
                        initial_balance[0] = bal
                    gained = round(bal - initial_balance[0], 4)
                    add_log('overnode', f'[{ident}] +{cpm} coins | Balance: {bal} | Total earned: {total_earned[0]}')
                else:
                    add_log('overnode', f'[{ident}] +{cpm} coins/min | Total earned: {total_earned[0]}')

            last_nri[0] = nri
        except Exception:
            pass

    close_code = [None]

    def on_error(ws, error):
        add_log('overnode', f'[{ident}] WS error: {error}')

    def on_close(ws, code, reason):
        close_code[0] = code
        add_log('overnode', f'[{ident}] WS closed (code={code})')

    while state['running'] and not state['stop_event'].is_set():
        close_code[0] = None
        last_nri[0]   = None

        ws_headers = {
            'Host':                     HOST,
            'Origin':                   ORIGIN,
            'Referer':                  f'{ORIGIN}/afk',
            'Cookie':                   cookie,
            'User-Agent':               'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language':          'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
            'Pragma':                   'no-cache',
            'Cache-Control':            'no-cache',
            'Sec-WebSocket-Version':    '13',
            'Sec-Fetch-Dest':           'websocket',
            'Sec-Fetch-Mode':           'websocket',
            'Sec-Fetch-Site':           'same-origin',
        }

        ws_app = websocket.WebSocketApp(
            WS_URL,
            header=ws_headers,
            on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close,
        )
        wst = threading.Thread(
            target=ws_app.run_forever,
            kwargs={'sslopt': {'cert_reqs': ssl.CERT_NONE}, 'ping_interval': 30, 'ping_timeout': 10},
            daemon=True,
        )
        wst.start()

        while wst.is_alive() and state['running'] and not state['stop_event'].is_set():
            time.sleep(1)

        ws_app.close()
        if not state['running'] or state['stop_event'].is_set():
            break

        code = close_code[0]
        if code == 4001:
            add_log('overnode', f'[{ident}] Session hết hạn (4001) — cần cookie mới')
            break
        elif code == 4003:
            add_log('overnode', f'[{ident}] Server suspended (4003)')
            break
        elif code == 4002:
            add_log('overnode', f'[{ident}] Session trùng (4002) — reconnect sau 15s...')
            for _ in range(15):
                if not state['running'] or state['stop_event'].is_set(): break
                time.sleep(1)
        else:
            add_log('overnode', f'[{ident}] Reconnecting in 10s...')
            for _ in range(10):
                if not state['running'] or state['stop_event'].is_set(): break
                time.sleep(1)

                                                                                
def start_afk_services():
    for tool in ('hyperhub', 'altare', 'overnode'):
        add_log(tool, 'AutoLab server started.')
        for acc in read_data(tool):
            start_worker_thread(tool, acc)

def cleanup(*_):
    print('\n[SYSTEM] Shutting down...')
    for tool, accounts in app_state.items():
        for email, st in accounts.items():
            st['running'] = False
            st['stop_event'].set()
    time.sleep(1)
    sys.exit(0)

atexit.register(cleanup)
signal.signal(signal.SIGINT,  cleanup)
signal.signal(signal.SIGTERM, cleanup)

                                                                               
def on_starting(server=None):
    """Gunicorn server hook"""
    start_afk_services()

if __name__ == '__main__':
                                         
                                                                             
    start_afk_services()
    try:
        import gunicorn
        print("[INFO] Gunicorn detected — run via: gunicorn --worker-class gevent --workers 2 --bind 0.0.0.0:5000 main:app")
    except ImportError:
        pass
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

import requests
import time
import threading
import sys
import json
import os
import glob
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
if not os.path.exists(CONFIG_DIR):
    os.makedirs(CONFIG_DIR)

BASE = "https://altare.sh"

app_state = {
    "sessions": {},
    "logs": {}
}

def init_client_state(client_id):
    if client_id not in app_state["sessions"]:
        app_state["sessions"][client_id] = {}
    if client_id not in app_state["logs"]:
        app_state["logs"][client_id] = []

def get_config_path(client_id):
    clean_id = "".join(c for c in client_id if c.isalnum() or c in ('_', '-'))
    return os.path.join(CONFIG_DIR, f"{clean_id}.json")

def load_config(client_id):
    config = {
        "accounts": [],
        "heartbeat_interval": 30,
        "stats_interval": 60
    }
    
    path = get_config_path(client_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                config.update(loaded)
        except Exception:
            pass
    return config

def save_config(client_id, config):
    path = get_config_path(client_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def add_log(client_id, msg, ident="SYSTEM"):
    init_client_state(client_id)
    ts = datetime.now().strftime("%H:%M:%S")
    short_ident = ident.split('@')[0] if '@' in ident else ident[:10]
    log_str = f"[{ts}] [{short_ident}] {msg}"
    
    print(f"[{client_id[:8]}] {log_str}")
    
    app_state["logs"][client_id].append(log_str)
    if len(app_state["logs"][client_id]) > 100:
        app_state["logs"][client_id].pop(0)

def make_headers(token, tenant_id=""):
    h = {
        "Authorization": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://altare.sh",
        "Referer": "https://altare.sh/billing",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    if tenant_id:
        h["altare-selected-tenant-id"] = tenant_id
    return h

def get_balance(headers, tenant_id):
    try:
        r = requests.get(f"{BASE}/api/tenants", headers=headers, timeout=10)
        if r.status_code == 200:
            items = r.json().get("items", [])
            for item in items:
                if item.get("id") == tenant_id:
                    cents = item.get("creditsCents")
                    return round(cents / 100, 2) if cents is not None else None
    except:
        pass
    return None

def start_afk_session(client_id, headers, tenant_id, ident):
    try:
        r = requests.post(f"{BASE}/api/tenants/{tenant_id}/rewards/afk/start", headers=headers, json={}, timeout=10)
        if r.status_code in (200, 201, 204):
            add_log(client_id, "▶ Kích hoạt AFK thành công", ident)
            return True
        else:
            add_log(client_id, f"Lỗi kích hoạt AFK: {r.status_code}", ident)
    except Exception as e:
        add_log(client_id, f"Ngoại lệ khi start AFK: {e}", ident)
    return False

def stop_afk_session(client_id, headers, tenant_id, ident):
    try:
        r = requests.post(f"{BASE}/api/tenants/{tenant_id}/rewards/afk/stop", headers=headers, json={}, timeout=10)
        add_log(client_id, f"⏸ Đã Dừng AFK ({r.status_code})", ident)
    except Exception as e:
        add_log(client_id, f"Lỗi khi Stop AFK: {e}", ident)

def send_heartbeat(headers, tenant_id):
    try:
        r = requests.post(f"{BASE}/api/tenants/{tenant_id}/rewards/afk/heartbeat", headers=headers, json={}, timeout=10)
        return r.status_code in (200, 201, 204)
    except:
        return False

def sse_loop(client_id, acc):
    token = acc["token"]
    ident = acc["identifier"]
    tenant_id = acc["tenant_id"]
    raw = token.replace("Bearer ", "")
    url = f"https://api.altare.sh/subscribe?token={raw}"
    sse_headers = make_headers(token)
    sse_headers["Accept"] = "text/event-stream"
    sse_headers["Cache-Control"] = "no-cache"
    
    first_connect = True
    while app_state["sessions"].get(client_id, {}).get(tenant_id, {}).get("running", False):
        if not app_state["sessions"][client_id][tenant_id].get("is_farming", False):
            first_connect = True
            time.sleep(3)
            continue
            
        try:
            with requests.get(url, headers=sse_headers, stream=True, timeout=(10, None)) as r:
                if r.status_code == 200:
                    if first_connect:
                        add_log(client_id, "Kết nối SSE ổn định ✓", ident)
                        first_connect = False
                    for line in r.iter_lines(chunk_size=1):
                        if not app_state["sessions"].get(client_id, {}).get(tenant_id, {}).get("running", False) or \
                           not app_state["sessions"][client_id][tenant_id].get("is_farming", False):
                            break
                else:
                    first_connect = True
                    time.sleep(10)
        except:
            first_connect = True
            time.sleep(10)
        time.sleep(3)

def heartbeat_loop(client_id, cfg, acc):
    interval = cfg.get("heartbeat_interval", 30)
    headers = make_headers(acc["token"], acc["tenant_id"])
    tenant_id = acc["tenant_id"]
    
    while app_state["sessions"].get(client_id, {}).get(tenant_id, {}).get("running", False):
        if app_state["sessions"][client_id][tenant_id].get("is_farming", False):
            send_heartbeat(headers, tenant_id)
        time.sleep(interval)

def stats_loop(client_id, cfg, acc):
    interval = cfg.get("stats_interval", 60)
    headers = make_headers(acc["token"], acc["tenant_id"])
    tenant_id = acc["tenant_id"]
    ident = acc["identifier"]
    
    credits_start = 0
    stuck_count = 0
    last_balance = None
    
    while app_state["sessions"].get(client_id, {}).get(tenant_id, {}).get("running", False):
        if not app_state["sessions"][client_id][tenant_id].get("is_farming", False):
            time.sleep(5)
            continue
            
        bal = get_balance(headers, tenant_id)
        if bal is not None:
            app_state["sessions"][client_id][tenant_id]["balance"] = bal
            
            if not credits_start:
                credits_start = bal
            earned = round(bal - credits_start, 4)
            add_log(client_id, f"💰 Balance: {bal:.4f} cr | Earned: +{earned:.4f} cr", ident)
            
            if last_balance is not None and bal == last_balance:
                stuck_count += 1
                if stuck_count >= 3:
                    add_log(client_id, f"⚠️ Balance kẹt {stuck_count} lần. Tự Reset AFK...", ident)
                    stop_afk_session(client_id, headers, tenant_id, ident)
                    time.sleep(3)
                    start_afk_session(client_id, headers, tenant_id, ident)
                    stuck_count = 0
            else:
                stuck_count = 0
                last_balance = bal
        time.sleep(interval)

def start_account_threads(client_id, cfg, acc):
    init_client_state(client_id)
    tenant_id = acc["tenant_id"]
    headers = make_headers(acc["token"], tenant_id)
    
    init_bal = get_balance(headers, tenant_id)
    
    app_state["sessions"][client_id][tenant_id] = {
        "running": True, 
        "is_farming": True,
        "balance": init_bal if init_bal is not None else 0.0
    }
    
    start_afk_session(client_id, headers, tenant_id, acc["identifier"])
    
    threading.Thread(target=sse_loop, args=(client_id, acc), daemon=True).start()
    threading.Thread(target=heartbeat_loop, args=(client_id, cfg, acc), daemon=True).start()
    threading.Thread(target=stats_loop, args=(client_id, cfg, acc), daemon=True).start()

def stop_account_threads(client_id, acc, remove_completely=False):
    tenant_id = acc["tenant_id"]
    if tenant_id in app_state["sessions"].get(client_id, {}):
        app_state["sessions"][client_id][tenant_id]["is_farming"] = False
        headers = make_headers(acc["token"], tenant_id)
        stop_afk_session(client_id, headers, tenant_id, acc["identifier"])
        
        if remove_completely:
            app_state["sessions"][client_id][tenant_id]["running"] = False
            add_log(client_id, "Đã Xóa tài khoản khỏi hệ thống.", acc["identifier"])

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Altare Auto Farmer (100% Isolated)</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
        body {
            font-family: 'Inter', sans-serif;
            background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 50%, #bae6fd 100%);
            min-height: 100vh;
            color: #0f172a;
        }
        .glass {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.8);
            border-radius: 16px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.05);
        }
        .console-box {
            background: rgba(15, 23, 42, 0.85);
            color: #38bdf8;
            font-family: 'Courier New', Courier, monospace;
            backdrop-filter: blur(8px);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 12px;
        }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(56, 189, 248, 0.5); border-radius: 10px; }
    </style>
</head>
<body class="p-6">

    <div class="max-w-6xl mx-auto space-y-6">
        <div class="glass p-6 flex justify-between items-center">
            <div>
                <h1 class="text-2xl font-bold text-sky-600">Altare Web Farmer</h1>
                <p class="text-sm text-slate-500">Auto Treo Altare| Không trộn dữ liệu</p>
            </div>
            <div class="text-right">
                <div id="clientDisplay" class="text-xs font-mono text-slate-500 mb-1">Thiết bị: Đang khởi tạo...</div>
                <div id="ipDisplay" class="text-sm font-semibold text-sky-700 bg-white/50 px-3 py-1 rounded-full inline-block shadow-sm">Đang lấy IP...</div>
                <div id="browserCheck" class="text-xs text-emerald-600 mt-1 font-medium">✓ Trình duyệt hợp lệ</div>
            </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="glass p-6 h-fit">
                <h2 class="text-lg font-semibold text-slate-700 mb-4 flex justify-between items-center">
                    Thêm Tài Khoản (Máy này)
                    <span id="accCount" class="text-xs bg-sky-100 text-sky-600 px-2 py-1 rounded-full font-bold shadow-sm">0/4</span>
                </h2>
                
                <form id="addForm" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-slate-600 mb-1">Email / Username</label>
                        <input type="text" id="identifier" required class="w-full px-4 py-2 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-400 bg-white/70" placeholder="user@example.com">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-slate-600 mb-1">Password</label>
                        <input type="password" id="password" required class="w-full px-4 py-2 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-400 bg-white/70" placeholder="••••••••">
                    </div>
                    <button type="submit" id="addBtn" class="w-full bg-sky-500 hover:bg-sky-600 text-white font-medium py-2 rounded-lg transition-colors shadow-md shadow-sky-200">
                        Thêm & Bắt đầu Farm
                    </button>
                </form>
                <div id="addMsg" class="mt-3 text-sm text-center hidden"></div>
            </div>

            <div class="glass p-6 md:col-span-2">
                <h2 class="text-lg font-semibold text-slate-700 mb-4">Danh Sách Tài Khoản Của Bạn</h2>
                <div id="accList" class="space-y-3"></div>
            </div>
        </div>

        <div class="console-box p-4 h-64 overflow-y-auto" id="consoleLog">
            <div>[Hệ Thống] Đang tải log...</div>
        </div>
    </div>

    <script>
        const MAX_ACCOUNTS = 4;
        let isProxy = false;
        
        function getClientId() {
            let cid = localStorage.getItem('altare_client_id');
            if (!cid) {
                cid = 'client_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();
                localStorage.setItem('altare_client_id', cid);
            }
            return cid;
        }
        const CLIENT_ID = getClientId();
        document.getElementById('clientDisplay').innerText = `Thiết bị: ${CLIENT_ID.substring(0,14)}`;

        async function runChecks() {
            if (navigator.webdriver || window._phantom || window.callPhantom) {
                document.getElementById('browserCheck').innerHTML = '⚠️ Phát hiện Bot/Proxy!';
                document.getElementById('browserCheck').className = 'text-xs text-red-600 mt-1 font-medium';
                isProxy = true;
            }
            try {
                const res = await fetch('https://api.ipify.org?format=json');
                const data = await res.json();
                document.getElementById('ipDisplay').innerText = `IP: ${data.ip}`;
            } catch (e) {
                document.getElementById('ipDisplay').innerText = `IP: ⚠️ Lỗi kết nối`;
            }
        }

        async function fetchState() {
            try {
                const res = await fetch(`/api/state?client_id=${CLIENT_ID}`);
                const data = await res.json();
                
                const accList = document.getElementById('accList');
                accList.innerHTML = '';
                
                document.getElementById('accCount').innerText = `${data.accounts.length}/${MAX_ACCOUNTS}`;
                document.getElementById('addBtn').disabled = data.accounts.length >= MAX_ACCOUNTS || isProxy;
                if(data.accounts.length >= MAX_ACCOUNTS) {
                    document.getElementById('addBtn').classList.replace('bg-sky-500', 'bg-slate-400');
                    document.getElementById('addBtn').innerText = "Đã đạt giới hạn 4 acc cho máy này";
                } else {
                    document.getElementById('addBtn').classList.replace('bg-slate-400', 'bg-sky-500');
                    document.getElementById('addBtn').innerText = "Thêm & Bắt đầu Farm";
                }

                if(data.accounts.length === 0) {
                    accList.innerHTML = '<div class="text-center text-slate-400 py-6 italic">Chưa có tài khoản nào được thêm trên máy này.</div>';
                }

                data.accounts.forEach(acc => {
                    const session = data.sessions[acc.tenant_id] || {};
                    const isFarming = session.is_farming;
                    const balanceText = session.balance !== undefined ? `${session.balance.toFixed(4)} cr` : "Đang tải...";
                    
                    const statusDot = isFarming 
                        ? '<span class="relative flex h-3 w-3"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span><span class="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span></span>' 
                        : '<span class="h-3 w-3 rounded-full bg-amber-500"></span>';
                    
                    const toggleBtn = isFarming
                        ? `<button onclick="toggleAction('${acc.tenant_id}', 'pause')" class="text-xs font-medium bg-amber-100 hover:bg-amber-200 text-amber-700 px-3 py-1.5 rounded-md transition-colors shadow-sm">⏸ Dừng</button>`
                        : `<button onclick="toggleAction('${acc.tenant_id}', 'play')" class="text-xs font-medium bg-emerald-100 hover:bg-emerald-200 text-emerald-700 px-3 py-1.5 rounded-md transition-colors shadow-sm">▶ Tiếp tục</button>`;

                    const el = document.createElement('div');
                    el.className = 'flex items-center justify-between p-3.5 bg-white/60 rounded-xl border border-white shadow-sm';
                    el.innerHTML = `
                        <div class="flex items-center gap-3 w-full">
                            ${statusDot}
                            <div class="flex-1">
                                <p class="font-bold text-slate-700 text-sm">${acc.identifier}</p>
                                <div class="flex items-center gap-2 mt-0.5">
                                    <span class="text-xs text-slate-500 font-mono bg-slate-100 px-1.5 py-0.5 rounded">ID: ${acc.tenant_id.substring(0,8)}</span>
                                    <span class="text-xs font-bold text-sky-600 bg-sky-50 px-2 py-0.5 rounded border border-sky-100">Bal: ${balanceText}</span>
                                </div>
                            </div>
                            <div class="flex gap-2">
                                ${toggleBtn}
                                <button onclick="removeAcc('${acc.identifier}')" class="text-xs font-medium bg-red-100 hover:bg-red-200 text-red-700 px-3 py-1.5 rounded-md transition-colors shadow-sm">🗑 Xóa</button>
                            </div>
                        </div>
                    `;
                    accList.appendChild(el);
                });

                const logBox = document.getElementById('consoleLog');
                const isScrolledToBottom = logBox.scrollHeight - logBox.clientHeight <= logBox.scrollTop + 10;
                
                logBox.innerHTML = data.logs.map(l => `<div>${l}</div>`).join('');
                if (data.logs.length === 0) logBox.innerHTML = '<div>[Hệ Thống] Chưa có log nào. Thêm tài khoản để bắt đầu!</div>';
                
                if (isScrolledToBottom) {
                    logBox.scrollTop = logBox.scrollHeight;
                }

            } catch (e) {
                console.error(e);
            }
        }

        document.getElementById('addForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            if(isProxy) return alert("Hệ thống từ chối kết nối do phát hiện hành vi Proxy/Bot.");

            const btn = document.getElementById('addBtn');
            const msg = document.getElementById('addMsg');
            btn.innerText = "Đang kết nối...";
            btn.disabled = true;

            const payload = {
                client_id: CLIENT_ID,
                identifier: document.getElementById('identifier').value,
                password: document.getElementById('password').value
            };

            try {
                const res = await fetch('/api/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const result = await res.json();
                
                msg.classList.remove('hidden');
                if(result.success) {
                    msg.className = "mt-3 text-sm text-center text-emerald-600 font-bold";
                    msg.innerText = "Thêm thành công!";
                    document.getElementById('addForm').reset();
                    fetchState();
                } else {
                    msg.className = "mt-3 text-sm text-center text-red-500 font-bold";
                    msg.innerText = result.error || "Đăng nhập thất bại.";
                }
            } catch(e) {
                msg.classList.remove('hidden');
                msg.innerText = "Lỗi kết nối máy chủ cục bộ.";
            }

            btn.innerText = "Thêm & Bắt đầu Farm";
            btn.disabled = false;
        });

        async function toggleAction(tenantId, action) {
            await fetch('/api/toggle', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ client_id: CLIENT_ID, tenant_id: tenantId, action: action })
            });
            fetchState();
        }

        async function removeAcc(identifier) {
            if(!confirm(`Xóa hoàn toàn tài khoản ${identifier} khỏi thiết bị này?`)) return;
            await fetch('/api/remove', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ client_id: CLIENT_ID, identifier: identifier })
            });
            fetchState();
        }

        runChecks();
        fetchState();
        setInterval(fetchState, 2000);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/state', methods=['GET'])
def get_state():
    client_id = request.args.get('client_id')
    if not client_id:
        return jsonify({"accounts": [], "sessions": {}, "logs": []})
        
    init_client_state(client_id)
    cfg = load_config(client_id)
    
    return jsonify({
        "accounts": cfg.get("accounts", []),
        "sessions": app_state["sessions"].get(client_id, {}),
        "logs": app_state["logs"].get(client_id, [])
    })

@app.route('/api/toggle', methods=['POST'])
def toggle_account():
    data = request.json
    client_id = data.get("client_id")
    tenant_id = data.get("tenant_id")
    action = data.get("action")
    
    if not client_id: return jsonify({"success": False})
    
    cfg = load_config(client_id)
    acc = next((a for a in cfg.get("accounts", []) if a["tenant_id"] == tenant_id), None)
    
    if acc and tenant_id in app_state["sessions"].get(client_id, {}):
        headers = make_headers(acc["token"], tenant_id)
        if action == 'pause':
            app_state["sessions"][client_id][tenant_id]["is_farming"] = False
            stop_afk_session(client_id, headers, tenant_id, acc["identifier"])
        elif action == 'play':
            app_state["sessions"][client_id][tenant_id]["is_farming"] = True
            start_afk_session(client_id, headers, tenant_id, acc["identifier"])
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/api/add', methods=['POST'])
def add_account():
    data = request.json
    client_id = data.get("client_id")
    identifier = data.get("identifier")
    password = data.get("password")
    
    if not client_id: return jsonify({"success": False, "error": "Thiếu Client ID."})
    
    cfg = load_config(client_id)
    accounts = cfg.get("accounts", [])
    
    if len(accounts) >= 4:
        return jsonify({"success": False, "error": "Đã đạt giới hạn 4 tài khoản trên máy này."})
        
    for acc in accounts:
        if acc["identifier"] == identifier:
            return jsonify({"success": False, "error": "Tài khoản đã tồn tại trên máy này."})

    url_login = 'https://api.altare.sh/api/auth/login'
    url_tenants = 'https://api.altare.sh/api/tenants'
    headers = make_headers("")
    
    try:
        res = requests.post(url_login, headers=headers, json={"identifier": identifier, "password": password})
        if res.status_code == 200:
            token = f"Bearer {res.json().get('token')}"
            
            h_tenant = make_headers(token)
            t_res = requests.get(url_tenants, headers=h_tenant)
            tenant_id = ""
            if t_res.status_code == 200 and t_res.json().get("items"):
                tenant_id = t_res.json()["items"][0].get("id")
            
            if not tenant_id:
                return jsonify({"success": False, "error": "Tài khoản không có Tenant ID."})
            
            new_acc = {
                "identifier": identifier,
                "token": token,
                "tenant_id": tenant_id
            }
            
            accounts.append(new_acc)
            cfg["accounts"] = accounts
            save_config(client_id, cfg)
            
            start_account_threads(client_id, cfg, new_acc)
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Sai email hoặc mật khẩu."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/remove', methods=['POST'])
def remove_account():
    data = request.json
    client_id = data.get("client_id")
    identifier = data.get("identifier")
    
    if not client_id: return jsonify({"success": False})
    
    cfg = load_config(client_id)
    accounts = cfg.get("accounts", [])
    
    for acc in accounts:
        if acc["identifier"] == identifier:
            stop_account_threads(client_id, acc, remove_completely=True)
            break
            
    cfg["accounts"] = [a for a in accounts if a["identifier"] != identifier]
    save_config(client_id, cfg)
    return jsonify({"success": True})

if __name__ == "__main__":
    for config_file in glob.glob(os.path.join(CONFIG_DIR, "*.json")):
        client_id = os.path.basename(config_file).replace(".json", "")
        config_data = load_config(client_id)
        
        for account in config_data.get("accounts", []):
            start_account_threads(client_id, config_data, account)
            
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

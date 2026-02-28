"""
╔══════════════════════════════════════════════════════════════╗
║   NIFTY OPTIONS PAPER TRADING & BACKTESTING APP             ║
║   Backend: Flask + Firstock API + SQLite                    ║
╚══════════════════════════════════════════════════════════════╝
"""

from flask import Flask, jsonify, request, render_template, session
from flask_cors import CORS
import sqlite3, json, hashlib, os, math, random
from datetime import datetime, timedelta
from scipy.stats import norm
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production-123")
CORS(app)

DB = "tradeapp.db"

# ── Firstock API Config ─────────────────────────────────────────
FIRSTOCK_BASE = "https://api.firstock.in/V2"

# ── Database Setup ──────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            api_key TEXT,
            api_secret TEXT,
            user_id TEXT,
            paper_capital REAL DEFAULT 500000,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            instrument TEXT,
            direction TEXT,
            strike REAL,
            expiry TEXT,
            option_type TEXT,
            lots INTEGER,
            entry_price REAL,
            exit_price REAL,
            entry_time TEXT,
            exit_time TEXT,
            status TEXT DEFAULT 'OPEN',
            pnl REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            notes TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            config TEXT,
            metrics TEXT,
            trades TEXT,
            equity_curve TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()

init_db()

# ── Helpers ─────────────────────────────────────────────────────
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def current_user():
    uid = session.get("user_id")
    if not uid: return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(user) if user else None

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Black-Scholes Pricing ───────────────────────────────────────
def black_scholes(S, K, T, r, sigma, option_type="call"):
    if T <= 0:
        return max(S - K, 0) if option_type == "call" else max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "call":
        price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return max(round(price, 2), 0.05)

def bs_greeks(S, K, T, r, sigma, option_type="call"):
    if T <= 0: return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    delta = norm.cdf(d1) if option_type == "call" else norm.cdf(d1) - 1
    gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    theta = (-(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
             - r * K * math.exp(-r * T) * (norm.cdf(d2) if option_type == "call" else norm.cdf(-d2))) / 365
    vega  = S * norm.pdf(d1) * math.sqrt(T) / 100
    return {"delta": round(delta,4), "gamma": round(gamma,6),
            "theta": round(theta,2), "vega": round(vega,2)}

# ── Firstock API Client ─────────────────────────────────────────
def firstock_login(user_id, password, api_key, vendor_code, imei):
    """Login to Firstock and get session token."""
    payload = {
        "userId": user_id,
        "password": password,
        "TOTP": "",
        "vendorCode": vendor_code,
        "apiKey": api_key,
        "imei": imei
    }
    try:
        r = requests.post(f"{FIRSTOCK_BASE}/login", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"status": "failed", "error": str(e)}

def firstock_get_quote(token, exchange, tradingsymbol):
    """Get live quote from Firstock."""
    payload = {"userId": token, "exchange": exchange, "tradingSymbol": tradingsymbol}
    try:
        r = requests.post(f"{FIRSTOCK_BASE}/getQuote", json=payload, timeout=5)
        return r.json()
    except Exception as e:
        return {"status": "failed", "error": str(e)}

def firstock_option_chain(token, symbol, expiry_date, strike_price):
    """Get option chain from Firstock."""
    payload = {
        "userId": token,
        "exchange": "NFO",
        "tradingSymbol": symbol,
        "expiryDate": expiry_date,
        "strikePrice": str(strike_price),
        "optionType": "PE"
    }
    try:
        r = requests.post(f"{FIRSTOCK_BASE}/optionChain", json=payload, timeout=8)
        return r.json()
    except Exception as e:
        return {"status": "failed", "error": str(e)}

# ── Simulated Market Data (fallback when API not connected) ─────
def get_simulated_price(symbol):
    bases = {
        "NIFTY": 22500, "BANKNIFTY": 48200,
        "SENSEX": 73500, "FINNIFTY": 21800
    }
    base = bases.get(symbol, 22500)
    noise = random.gauss(0, base * 0.002)
    return round(base + noise, 2)

def generate_option_chain(symbol, spot_price, expiry_days=7):
    """Generate realistic option chain using Black-Scholes."""
    r = 0.07
    iv_base = {"NIFTY": 0.14, "BANKNIFTY": 0.18, "SENSEX": 0.15, "FINNIFTY": 0.16}.get(symbol, 0.15)
    T = expiry_days / 365

    # ATM strike rounded to nearest 50
    atm = round(spot_price / 50) * 50
    strikes = [atm + i * 50 for i in range(-10, 11)]

    chain = []
    for K in strikes:
        moneyness = (K - spot_price) / spot_price
        iv_smile = iv_base * (1 + 0.5 * moneyness**2 + 0.1 * abs(moneyness))

        call_price = black_scholes(spot_price, K, T, r, iv_smile, "call")
        put_price  = black_scholes(spot_price, K, T, r, iv_smile, "put")
        call_greeks = bs_greeks(spot_price, K, T, r, iv_smile, "call")
        put_greeks  = bs_greeks(spot_price, K, T, r, iv_smile, "put")

        call_oi = int(random.uniform(50000, 500000))
        put_oi  = int(random.uniform(50000, 500000))

        chain.append({
            "strike": K,
            "call_price": call_price,
            "call_iv": round(iv_smile * 100, 2),
            "call_delta": call_greeks["delta"],
            "call_gamma": call_greeks["gamma"],
            "call_theta": call_greeks["theta"],
            "call_vega":  call_greeks["vega"],
            "call_oi":    call_oi,
            "call_volume": int(call_oi * random.uniform(0.1, 0.4)),
            "put_price":  put_price,
            "put_iv":     round(iv_smile * 100, 2),
            "put_delta":  put_greeks["delta"],
            "put_gamma":  put_greeks["gamma"],
            "put_theta":  put_greeks["theta"],
            "put_vega":   put_greeks["vega"],
            "put_oi":     put_oi,
            "put_volume": int(put_oi * random.uniform(0.1, 0.4)),
            "is_atm":     K == atm,
        })
    return chain

# ── Backtesting Engine ──────────────────────────────────────────
def generate_price_series(symbol, days=365):
    base = {"NIFTY": 22000, "BANKNIFTY": 48000, "SENSEX": 72000,
            "FINNIFTY": 21000}.get(symbol, 22000)
    prices = [base]
    for _ in range(days):
        ret = random.gauss(0.0004, 0.012)
        prices.append(max(prices[-1] * (1 + ret), base * 0.5))
    return prices

def calc_rsi(closes, period=14):
    rsi = [None] * period
    gains = losses = 0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i-1]
        if diff > 0: gains += diff
        else: losses -= diff
    gains /= period; losses /= period
    rsi.append(100 if losses == 0 else 100 - 100 / (1 + gains / losses))
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i-1]
        g = diff if diff > 0 else 0
        l = -diff if diff < 0 else 0
        gains = (gains * (period - 1) + g) / period
        losses = (losses * (period - 1) + l) / period
        rsi.append(100 if losses == 0 else 100 - 100 / (1 + gains / losses))
    return rsi

def calc_ema(closes, period):
    k = 2 / (period + 1)
    ema = [closes[0]]
    for c in closes[1:]:
        ema.append(c * k + ema[-1] * (1 - k))
    return ema

def run_backtest_engine(config):
    symbol    = config.get("symbol", "NIFTY")
    days      = int(config.get("days", 365))
    capital   = float(config.get("capital", 500000))
    iv        = float(config.get("iv", 20)) / 100
    dte       = int(config.get("dte", 21))
    otm_pct   = float(config.get("otm_pct", 1)) / 100
    pt        = float(config.get("profit_target", 50)) / 100
    sl        = float(config.get("stop_loss", 40)) / 100
    risk_pct  = float(config.get("risk_pct", 2)) / 100
    rsi_bull  = float(config.get("rsi_bull", 55))
    rsi_bear  = float(config.get("rsi_bear", 45))
    opt_type  = config.get("opt_type", "both")
    r         = 0.07
    lot_size  = {"NIFTY": 50, "BANKNIFTY": 15, "SENSEX": 10, "FINNIFTY": 40}.get(symbol, 50)

    closes = generate_price_series(symbol, days)
    rsi    = calc_rsi(closes)
    ema20  = calc_ema(closes, 20)
    ema50  = calc_ema(closes, 50)

    trades = []
    equity = capital
    equity_curve = [capital]

    for i in range(50, len(closes) - 1):
        if rsi[i] is None: continue
        signal = 0
        if rsi[i] > rsi_bull and ema20[i] > ema50[i]:
            signal = 1
        elif rsi[i] < rsi_bear and ema20[i] < ema50[i]:
            signal = -1
        if signal == 0:
            equity_curve.append(round(equity, 2))
            continue

        direction = None
        if signal == 1 and opt_type in ("both", "call"): direction = "call"
        elif signal == -1 and opt_type in ("both", "put"): direction = "put"
        if not direction:
            equity_curve.append(round(equity, 2))
            continue

        entry = closes[i + 1]
        strike = entry * (1 + otm_pct if direction == "call" else 1 - otm_pct)
        strike = round(strike / 50) * 50
        T_in   = dte / 365
        prem_in = black_scholes(entry, strike, T_in, r, iv, direction)
        budget  = equity * risk_pct
        lots    = max(1, int(budget / (prem_in * lot_size)))

        exit_idx = None; exit_prem = prem_in; exit_reason = "Expiry"
        for j in range(i + 2, min(i + dte + 1, len(closes))):
            T_out = max((dte - (j - i)) / 365, 0)
            prem_out = black_scholes(closes[j], strike, T_out, r, iv, direction)
            chg = (prem_out - prem_in) / prem_in
            if chg >= pt:
                exit_idx = j; exit_prem = prem_out; exit_reason = f"Profit Target +{int(pt*100)}%"; break
            if chg <= -sl:
                exit_idx = j; exit_prem = prem_out; exit_reason = f"Stop Loss -{int(sl*100)}%"; break
            if j == min(i + dte, len(closes) - 1):
                exit_idx = j; exit_prem = prem_out; exit_reason = "Expiry"; break

        if exit_idx:
            pnl = (exit_prem - prem_in) * lots * lot_size
            pnl_pct = (exit_prem - prem_in) / prem_in * 100
            equity += pnl
            trades.append({
                "direction": direction.upper(),
                "entry_price": round(entry),
                "strike": round(strike),
                "prem_in": round(prem_in, 2),
                "prem_out": round(exit_prem, 2),
                "lots": lots,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 1),
                "exit_reason": exit_reason,
                "day_in": i,
                "day_out": exit_idx
            })
        equity_curve.append(round(equity, 2))

    pnls   = [t["pnl"] for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total  = sum(pnls)
    wr     = len(wins) / len(pnls) * 100 if pnls else 0
    gp     = sum(wins); gl = abs(sum(losses))
    pf     = round(gp / gl, 2) if gl > 0 else 99

    cum = 0; peak = 0; max_dd = 0
    for p in pnls:
        cum += p
        if cum > peak: peak = cum
        dd = cum - peak
        if dd < max_dd: max_dd = dd

    metrics = {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "total_pnl": round(total, 2),
        "total_return_pct": round(total / capital * 100, 2),
        "profit_factor": pf,
        "max_drawdown": round(max_dd, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "rr_ratio": round(abs(sum(wins) / len(wins) / (sum(losses) / len(losses))), 2) if wins and losses else 0,
        "final_equity": round(equity, 2),
    }
    return {"metrics": metrics, "trades": trades[:50], "equity_curve": equity_curve[::5]}

# ════════════════════════════════════════════════════
#  ROUTES — AUTH
# ════════════════════════════════════════════════════

@app.route("/api/register", methods=["POST"])
def register():
    d = request.json
    username = d.get("username", "").strip()
    password = d.get("password", "")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                     (username, hash_pw(password)))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        session["user_id"] = user["id"]
        return jsonify({"success": True, "username": username})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already taken"}), 409
    finally:
        conn.close()

@app.route("/api/login", methods=["POST"])
def login():
    d = request.json
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=? AND password=?",
                        (d.get("username"), hash_pw(d.get("password","")))).fetchone()
    conn.close()
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401
    session["user_id"] = user["id"]
    return jsonify({"success": True, "username": user["username"],
                    "capital": user["paper_capital"]})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/api/me")
def me():
    u = current_user()
    if not u: return jsonify({"error": "Not authenticated"}), 401
    return jsonify({"username": u["username"], "capital": u["paper_capital"],
                    "has_api": bool(u.get("api_key"))})

# ════════════════════════════════════════════════════
#  ROUTES — FIRSTOCK API SETUP
# ════════════════════════════════════════════════════

@app.route("/api/broker/connect", methods=["POST"])
@require_auth
def broker_connect():
    d = request.json
    u = current_user()
    conn = get_db()
    conn.execute("UPDATE users SET api_key=?, api_secret=?, user_id=? WHERE id=?",
                 (d.get("api_key"), d.get("api_secret"),
                  d.get("firstock_user_id"), u["id"]))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Broker credentials saved"})

@app.route("/api/broker/status")
@require_auth
def broker_status():
    u = current_user()
    connected = bool(u.get("api_key"))
    return jsonify({"connected": connected,
                    "mode": "live" if connected else "simulated"})

# ════════════════════════════════════════════════════
#  ROUTES — MARKET DATA
# ════════════════════════════════════════════════════

@app.route("/api/quote/<symbol>")
@require_auth
def get_quote(symbol):
    u = current_user()
    if u.get("api_key"):
        # Try live Firstock data
        result = firstock_get_quote(u["api_key"], "NFO", symbol)
        if result.get("status") == "success":
            return jsonify(result)
    # Fallback: simulated
    price = get_simulated_price(symbol)
    change = round(random.gauss(0, price * 0.005), 2)
    return jsonify({
        "symbol": symbol,
        "ltp": price,
        "change": change,
        "change_pct": round(change / price * 100, 2),
        "open": round(price * (1 + random.uniform(-0.005, 0.005)), 2),
        "high": round(price * (1 + random.uniform(0, 0.012)), 2),
        "low":  round(price * (1 - random.uniform(0, 0.012)), 2),
        "volume": random.randint(1000000, 5000000),
        "mode": "simulated"
    })

@app.route("/api/option-chain/<symbol>")
@require_auth
def option_chain(symbol):
    expiry_days = int(request.args.get("expiry_days", 7))
    spot = get_simulated_price(symbol)
    chain = generate_option_chain(symbol, spot, expiry_days)
    return jsonify({
        "symbol": symbol,
        "spot": spot,
        "expiry_days": expiry_days,
        "chain": chain,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/api/indices")
@require_auth
def get_indices():
    symbols = ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"]
    result = []
    for s in symbols:
        price = get_simulated_price(s)
        chg = round(random.gauss(0, price * 0.004), 2)
        result.append({
            "symbol": s, "ltp": price,
            "change": chg, "change_pct": round(chg/price*100, 2)
        })
    return jsonify(result)

# ════════════════════════════════════════════════════
#  ROUTES — PAPER TRADING
# ════════════════════════════════════════════════════

@app.route("/api/paper/buy", methods=["POST"])
@require_auth
def paper_buy():
    u = current_user()
    d = request.json
    symbol     = d.get("symbol")
    option_type= d.get("option_type", "CE")
    strike     = float(d.get("strike"))
    expiry     = d.get("expiry")
    lots       = int(d.get("lots", 1))
    lot_size   = {"NIFTY": 50, "BANKNIFTY": 15, "SENSEX": 10, "FINNIFTY": 40}.get(symbol, 50)
    direction  = "call" if option_type == "CE" else "put"
    spot       = get_simulated_price(symbol)
    expiry_days= int(d.get("expiry_days", 7))
    iv         = float(d.get("iv", 15)) / 100
    T          = expiry_days / 365
    price      = black_scholes(spot, strike, T, 0.07, iv, direction)
    cost       = price * lots * lot_size

    conn = get_db()
    cap  = conn.execute("SELECT paper_capital FROM users WHERE id=?", (u["id"],)).fetchone()["paper_capital"]
    if cost > cap:
        conn.close()
        return jsonify({"error": f"Insufficient capital. Need ₹{cost:,.0f}, have ₹{cap:,.0f}"}), 400

    conn.execute("UPDATE users SET paper_capital=? WHERE id=?", (cap - cost, u["id"]))
    conn.execute("""INSERT INTO paper_trades
        (user_id,symbol,instrument,direction,strike,expiry,option_type,lots,entry_price,entry_time,status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (u["id"], symbol, f"{symbol}{strike}{option_type}", direction,
         strike, expiry, option_type, lots, price,
         datetime.now().isoformat(), "OPEN"))
    conn.commit()
    conn.close()
    return jsonify({
        "success": True,
        "message": f"Bought {lots} lot(s) {symbol} {strike} {option_type} @ ₹{price:.2f}",
        "premium": price, "cost": round(cost, 2),
        "remaining_capital": round(cap - cost, 2)
    })

@app.route("/api/paper/close/<int:trade_id>", methods=["POST"])
@require_auth
def paper_close(trade_id):
    u = current_user()
    conn = get_db()
    trade = conn.execute("SELECT * FROM paper_trades WHERE id=? AND user_id=? AND status='OPEN'",
                         (trade_id, u["id"])).fetchone()
    if not trade:
        conn.close()
        return jsonify({"error": "Trade not found"}), 404
    trade = dict(trade)

    spot = get_simulated_price(trade["symbol"])
    expiry_days = max(1, (datetime.fromisoformat(trade["expiry"]) - datetime.now()).days) if trade["expiry"] else 1
    T = expiry_days / 365
    exit_price = black_scholes(spot, trade["strike"], T, 0.07, 0.15, trade["direction"])
    lot_size   = {"NIFTY": 50, "BANKNIFTY": 15, "SENSEX": 10, "FINNIFTY": 40}.get(trade["symbol"], 50)
    pnl        = (exit_price - trade["entry_price"]) * trade["lots"] * lot_size
    pnl_pct    = (exit_price - trade["entry_price"]) / trade["entry_price"] * 100
    proceeds   = exit_price * trade["lots"] * lot_size

    cap = conn.execute("SELECT paper_capital FROM users WHERE id=?", (u["id"],)).fetchone()["paper_capital"]
    conn.execute("UPDATE users SET paper_capital=? WHERE id=?", (cap + proceeds, u["id"]))
    conn.execute("""UPDATE paper_trades SET status='CLOSED', exit_price=?, exit_time=?, pnl=?, pnl_pct=?
                    WHERE id=?""", (exit_price, datetime.now().isoformat(),
                                   round(pnl, 2), round(pnl_pct, 2), trade_id))
    conn.commit()
    conn.close()
    return jsonify({
        "success": True,
        "exit_price": round(exit_price, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "message": f"Closed trade. P&L: {'+'if pnl>=0 else ''}₹{pnl:,.0f}"
    })

@app.route("/api/paper/positions")
@require_auth
def paper_positions():
    u = current_user()
    conn = get_db()
    trades = conn.execute(
        "SELECT * FROM paper_trades WHERE user_id=? ORDER BY entry_time DESC",
        (u["id"],)).fetchall()
    cap = conn.execute("SELECT paper_capital FROM users WHERE id=?", (u["id"],)).fetchone()["paper_capital"]
    conn.close()

    open_trades = []; closed_trades = []
    for t in trades:
        t = dict(t)
        if t["status"] == "OPEN":
            spot = get_simulated_price(t["symbol"])
            expiry_days = 7
            T = expiry_days / 365
            curr_price = black_scholes(spot, t["strike"], T, 0.07, 0.15, t["direction"])
            lot_size = {"NIFTY": 50, "BANKNIFTY": 15, "SENSEX": 10, "FINNIFTY": 40}.get(t["symbol"], 50)
            t["current_price"] = round(curr_price, 2)
            t["unrealized_pnl"] = round((curr_price - t["entry_price"]) * t["lots"] * lot_size, 2)
            t["unrealized_pct"] = round((curr_price - t["entry_price"]) / t["entry_price"] * 100, 2)
            open_trades.append(t)
        else:
            closed_trades.append(t)

    total_realized = sum(t["pnl"] for t in closed_trades if t["pnl"])
    total_unrealized = sum(t["unrealized_pnl"] for t in open_trades)
    return jsonify({
        "capital": round(cap, 2),
        "open": open_trades,
        "closed": closed_trades[:20],
        "total_realized_pnl": round(total_realized, 2),
        "total_unrealized_pnl": round(total_unrealized, 2),
    })

@app.route("/api/paper/reset", methods=["POST"])
@require_auth
def paper_reset():
    u = current_user()
    capital = float(request.json.get("capital", 500000))
    conn = get_db()
    conn.execute("UPDATE users SET paper_capital=? WHERE id=?", (capital, u["id"]))
    conn.execute("DELETE FROM paper_trades WHERE user_id=?", (u["id"],))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "capital": capital})

# ════════════════════════════════════════════════════
#  ROUTES — BACKTESTING
# ════════════════════════════════════════════════════

@app.route("/api/backtest/run", methods=["POST"])
@require_auth
def run_backtest():
    u = current_user()
    config = request.json
    results = run_backtest_engine(config)
    name = config.get("name", f"Backtest {datetime.now().strftime('%d %b %H:%M')}")
    conn = get_db()
    conn.execute("""INSERT INTO backtest_results (user_id, name, config, metrics, trades, equity_curve)
                    VALUES (?,?,?,?,?,?)""",
                 (u["id"], name, json.dumps(config), json.dumps(results["metrics"]),
                  json.dumps(results["trades"]), json.dumps(results["equity_curve"])))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "name": name, **results})

@app.route("/api/backtest/history")
@require_auth
def backtest_history():
    u = current_user()
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, metrics, created_at FROM backtest_results WHERE user_id=? ORDER BY created_at DESC LIMIT 10",
        (u["id"],)).fetchall()
    conn.close()
    return jsonify([{**dict(r), "metrics": json.loads(r["metrics"])} for r in rows])

@app.route("/api/backtest/<int:bt_id>")
@require_auth
def get_backtest(bt_id):
    u = current_user()
    conn = get_db()
    row = conn.execute("SELECT * FROM backtest_results WHERE id=? AND user_id=?",
                       (bt_id, u["id"])).fetchone()
    conn.close()
    if not row: return jsonify({"error": "Not found"}), 404
    row = dict(row)
    return jsonify({**row,
                    "metrics": json.loads(row["metrics"]),
                    "trades": json.loads(row["trades"]),
                    "equity_curve": json.loads(row["equity_curve"]),
                    "config": json.loads(row["config"])})

# ════════════════════════════════════════════════════
#  MAIN ROUTE
# ════════════════════════════════════════════════════

@app.route("/")
@app.route("/<path:path>")
def index(path=""):
    return render_template("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

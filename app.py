"""
╔══════════════════════════════════════════════════════════════╗
║   TRADEFORGE — NSE Options Paper Trading & Backtesting      ║
║   Backend: Flask + Firstock API + SQLite                    ║
╚══════════════════════════════════════════════════════════════╝
"""

from flask import Flask, jsonify, request, render_template, session
from flask_cors import CORS
import sqlite3, json, hashlib, os, math, random, uuid
from datetime import datetime, timedelta, date
from scipy.stats import norm
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production-123")
CORS(app)

DB = "tradeapp.db"
FIRSTOCK_BASE = "https://api.firstock.in/V2"
LOT_SIZES = {"NIFTY": 75, "BANKNIFTY": 30, "SENSEX": 20, "FINNIFTY": 65}

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
            firstock_user_id TEXT,
            firstock_token TEXT,
            paper_capital REAL DEFAULT 500000,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT, instrument TEXT, direction TEXT,
            strike REAL, expiry TEXT, option_type TEXT, lots INTEGER,
            entry_price REAL, exit_price REAL, entry_time TEXT, exit_time TEXT,
            status TEXT DEFAULT 'OPEN', pnl REAL DEFAULT 0, pnl_pct REAL DEFAULT 0, notes TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, name TEXT, share_token TEXT UNIQUE,
            config TEXT, metrics TEXT, trades TEXT, equity_curve TEXT,
            day_wise TEXT DEFAULT '{}', monthly TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS saved_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, name TEXT, config TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()

init_db()

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
    vega = S * norm.pdf(d1) * math.sqrt(T) / 100
    return {"delta": round(delta,4), "gamma": round(gamma,6), "theta": round(theta,2), "vega": round(vega,2)}

def get_simulated_price(symbol):
    bases = {"NIFTY": 22500, "BANKNIFTY": 48200, "SENSEX": 73500, "FINNIFTY": 21800}
    base = bases.get(symbol, 22500)
    return round(base + random.gauss(0, base * 0.002), 2)

def generate_option_chain(symbol, spot_price, expiry_days=7):
    r = 0.07
    iv_base = {"NIFTY": 0.14, "BANKNIFTY": 0.18, "SENSEX": 0.15, "FINNIFTY": 0.16}.get(symbol, 0.15)
    T = expiry_days / 365
    step = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100, "FINNIFTY": 50}.get(symbol, 50)
    atm = round(spot_price / step) * step
    chain = []
    for K in [atm + i * step for i in range(-10, 11)]:
        moneyness = (K - spot_price) / spot_price
        iv = iv_base * (1 + 0.5 * moneyness**2 + 0.1 * abs(moneyness))
        cp = black_scholes(spot_price, K, T, r, iv, "call")
        pp = black_scholes(spot_price, K, T, r, iv, "put")
        cg = bs_greeks(spot_price, K, T, r, iv, "call")
        pg = bs_greeks(spot_price, K, T, r, iv, "put")
        co = int(random.uniform(50000, 500000)); po = int(random.uniform(50000, 500000))
        chain.append({"strike": K, "call_price": cp, "call_iv": round(iv*100,2),
            "call_delta": cg["delta"], "call_gamma": cg["gamma"], "call_theta": cg["theta"], "call_vega": cg["vega"],
            "call_oi": co, "call_volume": int(co*random.uniform(0.1,0.4)),
            "put_price": pp, "put_iv": round(iv*100,2),
            "put_delta": pg["delta"], "put_gamma": pg["gamma"], "put_theta": pg["theta"], "put_vega": pg["vega"],
            "put_oi": po, "put_volume": int(po*random.uniform(0.1,0.4)), "is_atm": K == atm})
    return chain

def get_nearest_expiry(ref_date_str, expiry_type="Weekly"):
    ref = datetime.strptime(ref_date_str, "%Y-%m-%d")
    if expiry_type == "Weekly":
        days_ahead = (3 - ref.weekday()) % 7
        if days_ahead == 0: days_ahead = 7
        return (ref + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    else:
        y, m = ref.year, ref.month
        nm = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        ld = nm - timedelta(days=1)
        db = (ld.weekday() - 3) % 7
        expiry = datetime.combine(ld - timedelta(days=db), datetime.min.time())
        if expiry <= ref:
            m2 = m + 1 if m < 12 else 1; y2 = y + 1 if m == 12 else y
            nm2 = date(y2 + 1, 1, 1) if m2 == 12 else date(y2, m2 + 1, 1)
            ld2 = nm2 - timedelta(days=1)
            db2 = (ld2.weekday() - 3) % 7
            expiry = datetime.combine(ld2 - timedelta(days=db2), datetime.min.time())
        return expiry.strftime("%Y-%m-%d")

def run_backtest_engine(config):
    symbol = config.get("symbol", "NIFTY")
    from_date = config.get("from_date", (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"))
    to_date = config.get("to_date", datetime.now().strftime("%Y-%m-%d"))
    capital = float(config.get("capital", 500000))
    expiry_type = config.get("expiry_type", "Weekly")
    trade_type = config.get("trade_type", "Intraday")
    slippage = float(config.get("slippage", 0))
    strategy_tp = config.get("strategy_tp")
    strategy_sl = config.get("strategy_sl")
    days_to_trade = config.get("days_to_trade", [0, 1, 2, 3, 4])
    range_breakout = config.get("range_breakout", False)
    legs = config.get("legs", [{"action":"BUY","option_type":"CE","closest_premium":150,"lots":1,"target":30,"sl":10,"tsl":0}])
    r_rate = 0.07
    lot_size = LOT_SIZES.get(symbol, 75)
    step = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100, "FINNIFTY": 50}.get(symbol, 50)

    # Generate price series
    current_dt = datetime.strptime(from_date, "%Y-%m-%d")
    end_dt = datetime.strptime(to_date, "%Y-%m-%d")
    base_p = {"NIFTY": 22000, "BANKNIFTY": 48000, "SENSEX": 72000, "FINNIFTY": 21000}.get(symbol, 22000)
    price_series = []
    price = base_p
    while current_dt <= end_dt:
        if current_dt.weekday() < 5:
            ret = random.gauss(0.0003, 0.011)
            close_p = round(price * (1 + ret), 2)
            high_p = round(max(price, close_p) * (1 + random.uniform(0, 0.008)), 2)
            low_p = round(min(price, close_p) * (1 - random.uniform(0, 0.008)), 2)
            price_series.append({"date": current_dt.strftime("%Y-%m-%d"), "day": current_dt.strftime("%a"),
                "open": price, "high": high_p, "low": low_p, "close": close_p,
                "vix": round(random.uniform(11, 22), 2), "weekday": current_dt.weekday()})
            price = close_p
        current_dt += timedelta(days=1)

    trades = []; equity = capital; equity_curve = []; day_pnls = {}

    for bar in price_series:
        if bar["weekday"] not in days_to_trade: continue
        day_date = bar["date"]; spot = bar["close"]
        atm = round(spot / step) * step

        if range_breakout and (bar["high"] - bar["low"]) < spot * 0.003:
            day_pnls[day_date] = 0
            equity_curve.append({"date": day_date, "equity": round(equity, 2)})
            continue

        expiry_date = get_nearest_expiry(day_date, expiry_type)
        expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        trade_dt = datetime.strptime(day_date, "%Y-%m-%d")
        dte = max((expiry_dt - trade_dt).days, 0)
        T_entry = max(dte / 365, 0.001)

        day_total = 0
        day_trades = []

        for leg in legs:
            action = leg.get("action", "BUY")
            opt_type = leg.get("option_type", "CE")
            cp_target = float(leg.get("closest_premium", 150))
            n_lots = int(leg.get("lots", 1))
            leg_tp = float(leg.get("target", 30))
            leg_sl = float(leg.get("sl", 10))
            iv = 0.15

            best_strike = atm; best_diff = float("inf")
            for offset in range(-20, 21):
                k = atm + offset * step
                p = black_scholes(spot, k, T_entry, r_rate, iv, "call" if opt_type == "CE" else "put")
                diff = abs(p - cp_target)
                if diff < best_diff:
                    best_diff = diff; best_strike = k

            entry_p = round(black_scholes(spot, best_strike, T_entry, r_rate, iv,
                                          "call" if opt_type == "CE" else "put") + slippage, 2)

            exit_spot = bar["close"] * (1 + random.gauss(0, 0.002)) if trade_type == "Intraday" else spot
            T_exit = max(T_entry - 0.5/365, 0) if trade_type == "Intraday" else 0
            exit_p = round(black_scholes(exit_spot, best_strike, T_exit, r_rate, iv,
                                         "call" if opt_type == "CE" else "put") - slippage, 2)

            exit_reason = "EOD"
            if action == "BUY":
                if exit_p - entry_p >= leg_tp: exit_p = entry_p + leg_tp; exit_reason = "Target Hit"
                elif exit_p - entry_p <= -leg_sl: exit_p = entry_p - leg_sl; exit_reason = "SL Hit"
                leg_pnl = (exit_p - entry_p) * n_lots * lot_size
            else:
                if entry_p - exit_p >= leg_tp: exit_p = entry_p - leg_tp; exit_reason = "Target Hit"
                elif entry_p - exit_p <= -leg_sl: exit_p = entry_p + leg_sl; exit_reason = "SL Hit"
                leg_pnl = (entry_p - exit_p) * n_lots * lot_size

            day_total += leg_pnl
            day_trades.append({"date": day_date, "day": bar["day"], "dte": dte, "expiry_date": expiry_date,
                "exit_time": "15:15" if trade_type == "Intraday" else expiry_date,
                "symbol": symbol, "option_type": opt_type, "action": action, "strike": best_strike,
                "entry_prem": entry_p, "exit_prem": exit_p, "lots": n_lots, "lot_size": lot_size,
                "pnl": round(leg_pnl, 2), "exit_reason": exit_reason, "vix": bar["vix"],
                "spot_open": bar["open"], "spot_close": bar["close"],
                "max_pnl": round(abs(leg_pnl) * random.uniform(1.1, 2.0) * (1 if leg_pnl >= 0 else -1), 2)})

        if strategy_tp and day_total >= float(strategy_tp):
            day_total = float(strategy_tp)
        if strategy_sl and day_total <= -float(strategy_sl):
            day_total = -float(strategy_sl)

        equity += day_total
        day_pnls[day_date] = round(day_total, 2)
        equity_curve.append({"date": day_date, "equity": round(equity, 2)})
        trades.extend(day_trades)

    pnl_list = list(day_pnls.values())
    wins = [p for p in pnl_list if p > 0]; losses = [p for p in pnl_list if p < 0]
    total_pnl = sum(pnl_list); td = len(pnl_list)
    wr = round(len(wins)/td*100, 1) if td else 0
    gp = sum(wins); gl = abs(sum(losses))
    pf = round(gp/gl, 2) if gl > 0 else 99
    avg_win = round(sum(wins)/len(wins), 2) if wins else 0
    avg_loss = round(sum(losses)/len(losses), 2) if losses else 0

    cum = 0; peak = 0; max_dd = 0
    for p in pnl_list:
        cum += p
        if cum > peak: peak = cum
        dd = cum - peak
        if dd < max_dd: max_dd = dd

    mws = mls = cw = cl = 0
    for p in pnl_list:
        if p > 0: cw += 1; cl = 0; mws = max(mws, cw)
        elif p < 0: cl += 1; cw = 0; mls = max(mls, cl)
        else: cw = cl = 0

    est_margin = sum(float(l.get("closest_premium",150)) * LOT_SIZES.get(symbol,75) * int(l.get("lots",1)) *
                     (5 if l.get("action")=="SELL" else 1) for l in legs)

    # Monthly
    monthly = {}
    for d, p in day_pnls.items():
        ym = d[:7]
        if ym not in monthly: monthly[ym] = {"pnl":0,"days":0,"wins":0,"losses":0,"mdd":0,"roi":0}
        monthly[ym]["pnl"] += p; monthly[ym]["days"] += 1
        if p > 0: monthly[ym]["wins"] += 1
        elif p < 0: monthly[ym]["losses"] += 1
    for ym in monthly:
        ymp = [day_pnls[d] for d in day_pnls if d.startswith(ym)]
        c2=0; pk2=0; md2=0
        for p in ymp:
            c2+=p
            if c2>pk2: pk2=c2
            dd2=c2-pk2
            if dd2<md2: md2=dd2
        monthly[ym]["mdd"] = round(md2, 2)
        monthly[ym]["roi"] = round(monthly[ym]["pnl"] / max(est_margin, 1) * 100, 2)
        monthly[ym]["pnl"] = round(monthly[ym]["pnl"], 2)

    # Day-wise
    dw = {0:[],1:[],2:[],3:[],4:[]}
    for d, p in day_pnls.items():
        wd = datetime.strptime(d, "%Y-%m-%d").weekday()
        if wd < 5: dw[wd].append(p)
    day_wise = {"Mon": round(sum(dw[0]),2), "Tue": round(sum(dw[1]),2),
                "Wed": round(sum(dw[2]),2), "Thu": round(sum(dw[3]),2), "Fri": round(sum(dw[4]),2)}

    metrics = {
        "est_margin": round(est_margin, 0), "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_pnl / max(capital, 1) * 100, 2),
        "avg_day_pnl": round(total_pnl/td, 2) if td else 0,
        "max_profit_day": round(max(pnl_list), 2) if pnl_list else 0,
        "max_loss_day": round(min(pnl_list), 2) if pnl_list else 0,
        "avg_monthly_pnl": round(total_pnl / max(len(monthly), 1), 2),
        "win_days": len(wins), "loss_days": len(losses), "trading_days": td,
        "win_rate": wr, "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": pf, "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd / max(capital, 1) * 100, 2),
        "max_win_streak": mws, "max_lose_streak": mls,
        "final_equity": round(equity, 2), "total_trades": len(trades),
    }
    return {"metrics": metrics, "trades": trades, "equity_curve": equity_curve,
            "day_wise": day_wise, "monthly": monthly}

# ── Auth ─────────────────────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def register():
    d = request.json; username = d.get("username","").strip(); password = d.get("password","")
    if not username or not password: return jsonify({"error": "Username and password required"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hash_pw(password)))
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
    d = request.json; conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=? AND password=?",
                        (d.get("username"), hash_pw(d.get("password","")))).fetchone()
    conn.close()
    if not user: return jsonify({"error": "Invalid credentials"}), 401
    session["user_id"] = user["id"]
    return jsonify({"success": True, "username": user["username"], "capital": user["paper_capital"]})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear(); return jsonify({"success": True})

@app.route("/api/me")
def me():
    u = current_user()
    if not u: return jsonify({"error": "Not authenticated"}), 401
    return jsonify({"username": u["username"], "capital": u["paper_capital"], "has_api": bool(u.get("api_key"))})

# ── Broker ───────────────────────────────────────────────────────
@app.route("/api/broker/connect", methods=["POST"])
@require_auth
def broker_connect():
    d = request.json; u = current_user(); conn = get_db()
    conn.execute("UPDATE users SET api_key=?, api_secret=?, firstock_user_id=? WHERE id=?",
                 (d.get("api_key"), d.get("api_secret"), d.get("firstock_user_id"), u["id"]))
    conn.commit(); conn.close()
    return jsonify({"success": True, "message": "Broker credentials saved"})

@app.route("/api/broker/status")
@require_auth
def broker_status():
    u = current_user()
    return jsonify({"connected": bool(u.get("api_key")), "mode": "live" if u.get("api_key") else "simulated"})

# ── Market Data ──────────────────────────────────────────────────
@app.route("/api/quote/<symbol>")
@require_auth
def get_quote(symbol):
    price = get_simulated_price(symbol); change = round(random.gauss(0, price*0.005), 2)
    return jsonify({"symbol": symbol, "ltp": price, "change": change,
                    "change_pct": round(change/price*100, 2), "mode": "simulated"})

@app.route("/api/option-chain/<symbol>")
@require_auth
def option_chain(symbol):
    expiry_days = int(request.args.get("expiry_days", 7)); spot = get_simulated_price(symbol)
    return jsonify({"symbol": symbol, "spot": spot, "expiry_days": expiry_days,
                    "chain": generate_option_chain(symbol, spot, expiry_days), "timestamp": datetime.now().isoformat()})

@app.route("/api/indices")
@require_auth
def get_indices():
    result = []
    for s in ["NIFTY","BANKNIFTY","SENSEX","FINNIFTY"]:
        p = get_simulated_price(s); chg = round(random.gauss(0, p*0.004), 2)
        result.append({"symbol": s, "ltp": p, "change": chg, "change_pct": round(chg/p*100, 2)})
    return jsonify(result)

# ── Paper Trading ────────────────────────────────────────────────
@app.route("/api/paper/buy", methods=["POST"])
@require_auth
def paper_buy():
    u = current_user(); d = request.json
    symbol = d.get("symbol"); option_type = d.get("option_type","CE")
    strike = float(d.get("strike")); expiry = d.get("expiry"); lots = int(d.get("lots",1))
    lot_size = LOT_SIZES.get(symbol, 75); direction = "call" if option_type == "CE" else "put"
    spot = get_simulated_price(symbol); expiry_days = int(d.get("expiry_days",7))
    iv = float(d.get("iv",15))/100; T = expiry_days/365
    price = black_scholes(spot, strike, T, 0.07, iv, direction); cost = price * lots * lot_size
    conn = get_db()
    cap = conn.execute("SELECT paper_capital FROM users WHERE id=?", (u["id"],)).fetchone()["paper_capital"]
    if cost > cap: conn.close(); return jsonify({"error": f"Insufficient capital"}), 400
    conn.execute("UPDATE users SET paper_capital=? WHERE id=?", (cap-cost, u["id"]))
    conn.execute("INSERT INTO paper_trades (user_id,symbol,instrument,direction,strike,expiry,option_type,lots,entry_price,entry_time,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (u["id"],symbol,f"{symbol}{strike}{option_type}",direction,strike,expiry,option_type,lots,price,datetime.now().isoformat(),"OPEN"))
    conn.commit(); conn.close()
    return jsonify({"success":True,"message":f"Bought {lots} lot(s) {symbol} {strike} {option_type} @ ₹{price:.2f}","premium":price,"cost":round(cost,2),"remaining_capital":round(cap-cost,2)})

@app.route("/api/paper/close/<int:trade_id>", methods=["POST"])
@require_auth
def paper_close(trade_id):
    u = current_user(); conn = get_db()
    trade = conn.execute("SELECT * FROM paper_trades WHERE id=? AND user_id=? AND status='OPEN'", (trade_id, u["id"])).fetchone()
    if not trade: conn.close(); return jsonify({"error":"Trade not found"}), 404
    trade = dict(trade); spot = get_simulated_price(trade["symbol"])
    exit_price = black_scholes(spot, trade["strike"], 7/365, 0.07, 0.15, trade["direction"])
    lot_size = LOT_SIZES.get(trade["symbol"], 75)
    pnl = (exit_price - trade["entry_price"]) * trade["lots"] * lot_size
    pnl_pct = (exit_price - trade["entry_price"]) / trade["entry_price"] * 100
    cap = conn.execute("SELECT paper_capital FROM users WHERE id=?", (u["id"],)).fetchone()["paper_capital"]
    conn.execute("UPDATE users SET paper_capital=? WHERE id=?", (cap + exit_price*trade["lots"]*lot_size, u["id"]))
    conn.execute("UPDATE paper_trades SET status='CLOSED',exit_price=?,exit_time=?,pnl=?,pnl_pct=? WHERE id=?",
                 (exit_price, datetime.now().isoformat(), round(pnl,2), round(pnl_pct,2), trade_id))
    conn.commit(); conn.close()
    return jsonify({"success":True,"exit_price":round(exit_price,2),"pnl":round(pnl,2),"pnl_pct":round(pnl_pct,2)})

@app.route("/api/paper/positions")
@require_auth
def paper_positions():
    u = current_user(); conn = get_db()
    trades = conn.execute("SELECT * FROM paper_trades WHERE user_id=? ORDER BY entry_time DESC", (u["id"],)).fetchall()
    cap = conn.execute("SELECT paper_capital FROM users WHERE id=?", (u["id"],)).fetchone()["paper_capital"]
    conn.close()
    open_t = []; closed_t = []
    for t in trades:
        t = dict(t)
        if t["status"] == "OPEN":
            spot = get_simulated_price(t["symbol"]); lot_size = LOT_SIZES.get(t["symbol"],75)
            curr = black_scholes(spot, t["strike"], 7/365, 0.07, 0.15, t["direction"])
            t["current_price"] = round(curr,2)
            t["unrealized_pnl"] = round((curr-t["entry_price"])*t["lots"]*lot_size, 2)
            t["unrealized_pct"] = round((curr-t["entry_price"])/t["entry_price"]*100, 2)
            open_t.append(t)
        else: closed_t.append(t)
    return jsonify({"capital":round(cap,2),"open":open_t,"closed":closed_t[:20],
                    "total_realized_pnl":round(sum(t["pnl"] for t in closed_t if t["pnl"]),2),
                    "total_unrealized_pnl":round(sum(t["unrealized_pnl"] for t in open_t),2)})

@app.route("/api/paper/reset", methods=["POST"])
@require_auth
def paper_reset():
    u = current_user(); capital = float(request.json.get("capital",500000)); conn = get_db()
    conn.execute("UPDATE users SET paper_capital=? WHERE id=?", (capital, u["id"]))
    conn.execute("DELETE FROM paper_trades WHERE user_id=?", (u["id"],))
    conn.commit(); conn.close()
    return jsonify({"success":True,"capital":capital})

# ── Backtesting ──────────────────────────────────────────────────
@app.route("/api/backtest/run", methods=["POST"])
@require_auth
def run_backtest():
    u = current_user(); config = request.json
    results = run_backtest_engine(config)
    name = config.get("name", f"Backtest {datetime.now().strftime('%d %b %H:%M')}")
    share_token = str(uuid.uuid4())[:12]; conn = get_db()
    conn.execute("INSERT INTO backtest_results (user_id,name,share_token,config,metrics,trades,equity_curve,day_wise,monthly) VALUES (?,?,?,?,?,?,?,?,?)",
                 (u["id"],name,share_token,json.dumps(config),json.dumps(results["metrics"]),
                  json.dumps(results["trades"]),json.dumps(results["equity_curve"]),
                  json.dumps(results["day_wise"]),json.dumps(results["monthly"])))
    conn.commit(); conn.close()
    return jsonify({"success":True,"name":name,"share_token":share_token,**results})

@app.route("/api/backtest/history")
@require_auth
def backtest_history():
    u = current_user(); conn = get_db()
    rows = conn.execute("SELECT id,name,share_token,metrics,created_at FROM backtest_results WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (u["id"],)).fetchall()
    conn.close()
    return jsonify([{**dict(r),"metrics":json.loads(r["metrics"])} for r in rows])

@app.route("/api/backtest/<int:bt_id>")
@require_auth
def get_backtest(bt_id):
    u = current_user(); conn = get_db()
    row = conn.execute("SELECT * FROM backtest_results WHERE id=? AND user_id=?", (bt_id, u["id"])).fetchone()
    conn.close()
    if not row: return jsonify({"error":"Not found"}), 404
    row = dict(row)
    return jsonify({**row,"metrics":json.loads(row["metrics"]),"trades":json.loads(row["trades"]),
                    "equity_curve":json.loads(row["equity_curve"]),"config":json.loads(row["config"]),
                    "day_wise":json.loads(row.get("day_wise","{}")),"monthly":json.loads(row.get("monthly","{}"))})

@app.route("/api/backtest/share/<token>")
def get_shared_backtest(token):
    conn = get_db()
    row = conn.execute("SELECT * FROM backtest_results WHERE share_token=?", (token,)).fetchone()
    conn.close()
    if not row: return jsonify({"error":"Not found"}), 404
    row = dict(row)
    return jsonify({**row,"metrics":json.loads(row["metrics"]),"trades":json.loads(row["trades"]),
                    "equity_curve":json.loads(row["equity_curve"]),"config":json.loads(row["config"]),
                    "day_wise":json.loads(row.get("day_wise","{}")),"monthly":json.loads(row.get("monthly","{}"))})

# ── Saved Strategies ─────────────────────────────────────────────
@app.route("/api/strategy/save", methods=["POST"])
@require_auth
def save_strategy():
    u = current_user(); d = request.json
    name = d.get("name","").strip(); config = d.get("config",{})
    if not name: return jsonify({"error":"Strategy name required"}), 400
    conn = get_db()
    conn.execute("INSERT INTO saved_strategies (user_id,name,config) VALUES (?,?,?)", (u["id"],name,json.dumps(config)))
    conn.commit(); conn.close()
    return jsonify({"success":True,"name":name})

@app.route("/api/strategy/list")
@require_auth
def list_strategies():
    u = current_user(); conn = get_db()
    rows = conn.execute("SELECT id,name,config,created_at FROM saved_strategies WHERE user_id=? ORDER BY created_at DESC", (u["id"],)).fetchall()
    conn.close()
    return jsonify([{**dict(r),"config":json.loads(r["config"])} for r in rows])

@app.route("/api/strategy/<int:strat_id>", methods=["DELETE"])
@require_auth
def delete_strategy(strat_id):
    u = current_user(); conn = get_db()
    conn.execute("DELETE FROM saved_strategies WHERE id=? AND user_id=?", (strat_id, u["id"]))
    conn.commit(); conn.close()
    return jsonify({"success":True})

@app.route("/")
@app.route("/<path:path>")
def index(path=""):
    return render_template("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

# ⚡ TradeForge — NSE Options Paper Trading & Backtesting App

A full-stack web app for paper trading and backtesting Nifty, BankNifty, Sensex, 
and FinnNifty options using the Firstock API.

---

## 📁 Project Files

```
tradeapp/
├── app.py              ← Python backend (Flask server)
├── requirements.txt    ← Python packages needed
├── README.md           ← This file
└── templates/
    └── index.html      ← Frontend (all UI)
```

---

## 🚀 DEPLOYMENT GUIDE (Railway.app — FREE)

### Step 1: Install Git
Download from https://git-scm.com/downloads and install.

### Step 2: Create a GitHub account
Go to https://github.com and sign up (free).

### Step 3: Upload your project to GitHub

1. Open terminal / command prompt
2. Navigate to your project folder:
   ```
   cd path/to/tradeapp
   ```
3. Run these commands one by one:
   ```
   git init
   git add .
   git commit -m "Initial TradeForge app"
   ```
4. Go to https://github.com/new
5. Create a new repository named `tradeforge`
6. Copy the commands shown (push existing repository) and run them

### Step 4: Deploy on Railway

1. Go to https://railway.app
2. Click "Start a New Project"
3. Select "Deploy from GitHub repo"
4. Connect your GitHub account and select `tradeforge`
5. Railway will auto-detect Python and deploy!

### Step 5: Set environment variable (important!)

In Railway dashboard:
1. Click your project → Variables tab
2. Add: `SECRET_KEY` = any random string (e.g. `mySecretKey2024abc`)
3. Click Deploy

### Step 6: Open your app!

Railway gives you a URL like: `https://tradeforge-production.up.railway.app`

---

## 🔌 Connecting Firstock API

Once the app is running:
1. Register/Login with any username and password (stored locally)
2. Click the **SIMULATED** pill in top-right nav
3. Enter your Firstock credentials:
   - **Firstock User ID**: Your Firstock login ID
   - **API Key**: From Firstock dashboard → API section
   - **API Secret**: From Firstock dashboard → API section
4. Click Save & Connect

> **Note**: Without API keys, the app uses realistic simulated market data.
> All features work in simulation mode for practice/testing.

---

## 🔑 Getting Firstock API Access

1. Log in at https://firstock.in
2. Go to your profile → API Access
3. Enable API access (free with trading account)
4. Copy your API Key and Secret

---

## 📊 Features

### 1. Dashboard
- Live paper trading capital balance
- Realized & unrealized P&L
- Open positions summary
- Recent backtest results

### 2. Options Chain
- Live Nifty/BankNifty/Sensex/FinnNifty chains
- Full Greeks: Delta, Theta, Vega, Gamma
- Open Interest with visual bars
- One-click buy from chain to paper trading
- Weekly / Monthly expiry selection

### 3. Paper Trading
- Place CE/PE orders with custom lots
- Real-time P&L tracking with Black-Scholes pricing
- Close positions anytime
- Full trade history
- Reset account with custom capital

### 4. Backtesting
- RSI + EMA momentum strategy
- Configurable: IV, DTE, OTM%, profit target, stop loss
- Results: equity curve, win rate, profit factor, max drawdown
- Save and compare multiple backtests

---

## 💡 Tips for Beginners

- **Start with simulation mode** — no real money, no API key needed
- **Use ₹5,00,000 paper capital** to simulate realistic trading
- **Run backtests first** before paper trading a strategy
- **Watch Greeks**: Delta shows direction sensitivity, Theta is time decay

---

## ⚠️ Disclaimer

This app is for educational and paper trading purposes only.
Past backtest performance does not guarantee future results.
Always consult a SEBI-registered advisor before real trading.

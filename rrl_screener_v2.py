# ============================================================
#  RR.L ENHANCED DAILY SCREENER v2
#  All 8 improvements included:
#  1. Volume confirmation (50% above average)
#  2. FTSE 100 market filter (ISF.L)
#  3. ATR-based dynamic stop loss
#  4. Earnings countdown (all 4 stocks)
#  5. Trade journal → Google Sheets
#  6. Position size calculator
#  7. Midday alert (separate script)
#  8. Richer HTML email with everything included
# ============================================================

import subprocess
subprocess.run([
    "pip", "install",
    "yfinance", "pandas", "matplotlib", "numpy",
    "gspread", "google-auth",
    "--quiet"
])

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import os
import json
import base64
import gspread
from google.oauth2.service_account import Credentials
import warnings
warnings.filterwarnings("ignore")

print("✅ Libraries loaded")


# ============================================================
#  YOUR SETTINGS — EDIT THESE
# ============================================================

# --- Your account & risk ---
ACCOUNT_SIZE  = 500           # Your account in GBP — change this
RISK_PCT      = 1.5            # % to risk per trade (1–2% recommended)

# --- Trade status ---
IN_TRADE      = False          # Change to True once you've entered
ENTRY_PRICE   = 0              # Set to your actual entry price (pence)

# --- RR.L trade levels (pence) ---
TICKER        = "RR.L"
ENTRY_TRIGGER = 1228
TARGET_1      = 1280
TARGET_2      = 1350

# --- Indicators ---
MA_SHORT      = 20
MA_MEDIUM     = 50
MA_LONG       = 200
RSI_PERIOD    = 14
ATR_PERIOD    = 14
ATR_MULTIPLIER = 1.5           # Stop = entry - (ATR * this multiplier)
VOLUME_SURGE  = 1.5            # Volume must be 1.5x the 20-day average

# --- Earnings dates (update these when companies announce) ---
EARNINGS_DATES = {
    "RR.L":  datetime(2026, 7, 30),
    "MSFT":  datetime(2026, 7, 28),
    "AZN.L": datetime(2026, 8, 1),    # TBC — update when announced
    "RKLB":  datetime(2026, 8, 6),    # TBC — update when announced
}

# --- FTSE filter ---
FTSE_TICKER   = "ISF.L"        # iShares FTSE 100 ETF

# --- Google Sheets ---
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

# --- Email (from GitHub Secrets) ---
SENDER_EMAIL    = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("EMAIL_PASSWORD")
RECEIVER_EMAIL  = os.environ.get("RECEIVER_EMAIL")


# ============================================================
#  HELPER FUNCTIONS
# ============================================================

def download(ticker, days=180):
    end   = datetime.today()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def calc_indicators(df):
    df["MA20"]  = df["Close"].rolling(MA_SHORT).mean()
    df["MA50"]  = df["Close"].rolling(MA_MEDIUM).mean()
    df["MA200"] = df["Close"].rolling(MA_LONG).mean()

    delta       = df["Close"].diff()
    gain        = delta.clip(lower=0)
    loss        = -delta.clip(upper=0)
    df["RSI"]   = 100 - (100 / (1 + gain.rolling(RSI_PERIOD).mean() /
                                    loss.rolling(RSI_PERIOD).mean()))

    ema12           = df["Close"].ewm(span=12, adjust=False).mean()
    ema26           = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"]      = ema12 - ema26
    df["Signal"]    = df["MACD"].ewm(span=9, adjust=False).mean()
    df["Histogram"] = df["MACD"] - df["Signal"]

    df["Vol_MA20"]  = df["Volume"].rolling(20).mean()

    # ATR
    prev_close      = df["Close"].shift(1)
    tr              = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"]  - prev_close).abs()
    ], axis=1).max(axis=1)
    df["ATR"]       = tr.rolling(ATR_PERIOD).mean()

    return df


def days_to_earnings(ticker):
    date = EARNINGS_DATES.get(ticker)
    if not date:
        return None, "Unknown"
    days = (date - datetime.today()).days
    if days < 0:
        return days, f"Passed ({date.strftime('%d %b')})"
    return days, date.strftime("%d %b %Y")


def position_size(entry, stop):
    """Returns shares to buy based on account size and risk %."""
    risk_per_share = entry - stop            # pence
    if risk_per_share <= 0:
        return 0, 0
    max_loss_gbp   = ACCOUNT_SIZE * (RISK_PCT / 100)
    max_loss_pence = max_loss_gbp * 100
    shares         = int(max_loss_pence / risk_per_share)
    cost_gbp       = (shares * entry) / 100
    return shares, cost_gbp


def log_to_sheets(row_data):
    """Appends one row to your Google Sheet trade journal."""
    creds_b64 = os.environ.get("GOOGLE_SHEETS_CREDS", "")
    if not creds_b64 or not SPREADSHEET_ID:
        print("⚠️  Google Sheets not configured — skipping journal log")
        return

    try:
        creds_json = json.loads(base64.b64decode(creds_b64).decode())
        creds      = Credentials.from_service_account_info(
            creds_json,
            scopes=["https://spreadsheets.google.com/feeds",
                    "https://www.googleapis.com/auth/drive"]
        )
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(SPREADSHEET_ID)

        # Get or create the RR.L tab
        try:
            ws = sheet.worksheet("RR.L Journal")
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet("RR.L Journal", rows=1000, cols=20)
            ws.append_row([
                "Date", "Price (p)", "MA20", "MA50", "MA200",
                "RSI", "MACD Bullish", "Volume Surge", "FTSE OK",
                "ATR Stop", "Score", "Verdict",
                "Shares (suggested)", "Cost (£)", "In Trade"
            ])

        ws.append_row(row_data)
        print("✅ Logged to Google Sheets")
    except Exception as e:
        print(f"⚠️  Sheets error: {e}")


# ============================================================
#  STEP 1: DOWNLOAD DATA
# ============================================================

print("\n📥 Downloading data...")
df   = download(TICKER)
ftse = download(FTSE_TICKER)

if df.empty:
    print("❌ No RR.L data. Market may be closed.")
    exit(0)

df   = calc_indicators(df)
ftse = calc_indicators(ftse)
print("✅ Data downloaded and indicators calculated")


# ============================================================
#  STEP 2: SCORE THE SETUP
# ============================================================

latest        = df.iloc[-1]
prev          = df.iloc[-2]
current_price = float(latest["Close"])
current_rsi   = float(latest["RSI"])
current_atr   = float(latest["ATR"])
ma20          = float(latest["MA20"])
ma50          = float(latest["MA50"])
ma200         = float(latest["MA200"])
current_vol   = float(latest["Volume"])
vol_avg       = float(latest["Vol_MA20"])
today_str     = datetime.today().strftime("%A %d %B %Y")

# ATR-based dynamic stop
atr_stop      = int(ENTRY_TRIGGER - (current_atr * ATR_MULTIPLIER))
atr_stop      = max(atr_stop, ENTRY_TRIGGER - 200)  # floor cap

# If in trade, use actual entry; otherwise use trigger for sizing
sizing_entry  = ENTRY_PRICE if (IN_TRADE and ENTRY_PRICE > 0) else ENTRY_TRIGGER
shares, cost  = position_size(sizing_entry, atr_stop)

# FTSE filter
ftse_price    = float(ftse.iloc[-1]["Close"])
ftse_ma50     = float(ftse.iloc[-1]["MA50"])
ftse_ok       = ftse_price > ftse_ma50

# Volume surge (only meaningful on breakout day, flagged as context)
vol_surge     = current_vol > (vol_avg * VOLUME_SURGE)
vol_ratio     = (current_vol / vol_avg) if vol_avg > 0 else 0

# Earnings
days_rrl, rrl_earnings_str = days_to_earnings("RR.L")
earnings_warning = days_rrl is not None and 0 <= days_rrl <= 14

# Checklist
cond_above_trigger  = current_price > ENTRY_TRIGGER
cond_above_ma50     = current_price > ma50
cond_above_ma20     = current_price > ma20
cond_rsi_healthy    = 40 <= current_rsi <= 65
cond_macd_bullish   = float(latest["MACD"]) > float(latest["Signal"])
cond_macd_improving = float(latest["Histogram"]) > float(prev["Histogram"])
cond_vol_surge      = vol_surge
cond_ftse_ok        = ftse_ok

checklist = {
    f"Price above entry trigger ({ENTRY_TRIGGER}p)": cond_above_trigger,
    "Price above MA50":                              cond_above_ma50,
    "Price above MA20":                              cond_above_ma20,
    "RSI in healthy zone (40–65)":                   cond_rsi_healthy,
    "MACD above signal line":                        cond_macd_bullish,
    "MACD histogram improving":                      cond_macd_improving,
    "Volume surge (1.5× average)":                   cond_vol_surge,
    "FTSE 100 above MA50 (market healthy)":          cond_ftse_ok,
}
score = sum(checklist.values())
max_score = len(checklist)

if earnings_warning:
    verdict       = "⚠️  EARNINGS SOON"
    verdict_detail = f"RR.L reports in {days_rrl} days. Do not enter new trades."
    verdict_color  = "#ffaa00"
elif score >= 7:
    verdict        = "🟢 STRONG SETUP"
    verdict_detail = "Nearly all conditions met. Watch for entry on volume."
    verdict_color  = "#00cc66"
elif score >= 5:
    verdict        = "🟡 BUILDING SETUP"
    verdict_detail = "Good progress. A few conditions still missing."
    verdict_color  = "#ffdd00"
elif score >= 3:
    verdict        = "🟠 PARTIAL SETUP"
    verdict_detail = "Some conditions met. Not ready — keep watching."
    verdict_color  = "#ff8800"
else:
    verdict        = "🔴 NOT READY"
    verdict_detail = "Too few conditions met. Stay patient."
    verdict_color  = "#ff4444"

gap           = ENTRY_TRIGGER - current_price
risk_pp       = ENTRY_TRIGGER - atr_stop
reward_t1     = TARGET_1 - ENTRY_TRIGGER
reward_t2     = TARGET_2 - ENTRY_TRIGGER
rr_t1         = reward_t1 / risk_pp if risk_pp > 0 else 0
rr_t2         = reward_t2 / risk_pp if risk_pp > 0 else 0

print(f"✅ Score: {score}/{max_score} — {verdict}")


# ============================================================
#  STEP 3: CHART
# ============================================================

fig = plt.figure(figsize=(14, 11))
fig.patch.set_facecolor("#0f1117")
gs  = gridspec.GridSpec(4, 1, height_ratios=[3, 1, 1, 0.6], hspace=0.08)

# ---- Price panel ----
ax1 = fig.add_subplot(gs[0])
ax1.set_facecolor("#0f1117")

colors = ["#26a69a" if c >= o else "#ef5350"
          for c, o in zip(df["Close"], df["Open"])]
ax1.bar(df.index, df["High"] - df["Low"], bottom=df["Low"],
        color=colors, width=0.6, alpha=0.3)
ax1.bar(df.index, df["Close"] - df["Open"], bottom=df["Open"],
        color=colors, width=0.6, alpha=0.9)

ax1.plot(df.index, df["MA20"],  color="#ffd700", linewidth=1.2,
         label="MA20", linestyle="--")
ax1.plot(df.index, df["MA50"],  color="#00bfff", linewidth=1.5, label="MA50")
ax1.plot(df.index, df["MA200"], color="#ff6b6b", linewidth=1.5, label="MA200")

ax1.axhline(ENTRY_TRIGGER, color="#00ff88", linewidth=1.2, linestyle=":",
            label=f"Entry: {ENTRY_TRIGGER}p")
ax1.axhline(atr_stop,      color="#ff4444", linewidth=1.2, linestyle=":",
            label=f"ATR Stop: {atr_stop}p")
ax1.axhline(TARGET_1,      color="#88ddff", linewidth=0.8, linestyle=":",
            label=f"T1: {TARGET_1}p")
ax1.axhline(TARGET_2,      color="#aaaaff", linewidth=0.8, linestyle=":",
            label=f"T2: {TARGET_2}p")

ax1.set_title(
    f"RR.L  |  {today_str}  |  {current_price:.0f}p  |  "
    f"Score: {score}/{max_score}  |  {verdict}",
    color="white", fontsize=12, fontweight="bold", pad=12
)
ax1.legend(loc="upper left", fontsize=8, facecolor="#1a1a2e",
           labelcolor="white", framealpha=0.8)
ax1.tick_params(colors="gray", labelbottom=False)
ax1.set_ylabel("Price (p)", color="gray", fontsize=9)
for spine in ax1.spines.values():
    spine.set_edgecolor("#333")

ax1v = ax1.twinx()
vol_colors = ["#00ff88" if v > vol_avg * VOLUME_SURGE else "#ffffff"
              for v in df["Volume"]]
ax1v.bar(df.index, df["Volume"], color=vol_colors, alpha=0.15, width=0.6)
ax1v.plot(df.index, df["Vol_MA20"] * VOLUME_SURGE, color="#00ff88",
          linewidth=0.8, linestyle="--", alpha=0.5)
ax1v.set_ylim(0, df["Volume"].max() * 5)
ax1v.set_yticks([])

# ---- RSI panel ----
ax2 = fig.add_subplot(gs[1], sharex=ax1)
ax2.set_facecolor("#0f1117")
ax2.plot(df.index, df["RSI"], color="#ffd700", linewidth=1.4)
ax2.axhline(70, color="#ef5350", linewidth=0.8, linestyle="--", alpha=0.7)
ax2.axhline(40, color="#26a69a", linewidth=0.8, linestyle="--", alpha=0.7)
ax2.axhline(50, color="#555",    linewidth=0.5, linestyle="--", alpha=0.5)
ax2.fill_between(df.index, df["RSI"], 40,
                 where=(df["RSI"] < 40), color="#26a69a", alpha=0.15)
ax2.fill_between(df.index, df["RSI"], 70,
                 where=(df["RSI"] > 70), color="#ef5350", alpha=0.15)
ax2.set_ylim(0, 100)
ax2.set_ylabel("RSI", color="gray", fontsize=9)
ax2.text(df.index[-1], min(current_rsi + 4, 92), f" {current_rsi:.1f}",
         color="#ffd700", fontsize=8)
ax2.tick_params(colors="gray", labelbottom=False)
for spine in ax2.spines.values():
    spine.set_edgecolor("#333")

# ---- MACD panel ----
ax3 = fig.add_subplot(gs[2], sharex=ax1)
ax3.set_facecolor("#0f1117")
hc  = ["#26a69a" if h >= 0 else "#ef5350" for h in df["Histogram"]]
ax3.bar(df.index, df["Histogram"], color=hc, width=0.6, alpha=0.7)
ax3.plot(df.index, df["MACD"],   color="#00bfff", linewidth=1.3, label="MACD")
ax3.plot(df.index, df["Signal"], color="#ff9800", linewidth=1.0,
         label="Signal", linestyle="--")
ax3.axhline(0, color="#555", linewidth=0.5)
ax3.set_ylabel("MACD", color="gray", fontsize=9)
ax3.legend(loc="upper left", fontsize=7, facecolor="#1a1a2e",
           labelcolor="white", framealpha=0.8)
ax3.tick_params(colors="gray", labelbottom=False)
for spine in ax3.spines.values():
    spine.set_edgecolor("#333")

# ---- ATR panel ----
ax4 = fig.add_subplot(gs[3], sharex=ax1)
ax4.set_facecolor("#0f1117")
ax4.plot(df.index, df["ATR"], color="#cc88ff", linewidth=1.2, label="ATR(14)")
ax4.fill_between(df.index, df["ATR"], alpha=0.1, color="#cc88ff")
ax4.set_ylabel("ATR", color="gray", fontsize=9)
ax4.text(df.index[-1], float(df["ATR"].iloc[-1]), f"  {current_atr:.0f}p",
         color="#cc88ff", fontsize=8)
ax4.tick_params(colors="gray")
for spine in ax4.spines.values():
    spine.set_edgecolor("#333")

plt.setp(ax4.get_xticklabels(), color="gray", fontsize=7)
plt.tight_layout()
chart_path = "RRL_chart_v2.png"
plt.savefig(chart_path, dpi=150, bbox_inches="tight",
            facecolor="#0f1117", edgecolor="none")
plt.close()
print("✅ Chart saved")


# ============================================================
#  STEP 4: LOG TO GOOGLE SHEETS
# ============================================================

log_to_sheets([
    today_str,
    round(current_price, 0),
    round(ma20, 0),
    round(ma50, 0),
    round(ma200, 0),
    round(current_rsi, 1),
    "Yes" if cond_macd_bullish   else "No",
    "Yes" if cond_vol_surge      else "No",
    "Yes" if cond_ftse_ok        else "No",
    atr_stop,
    f"{score}/{max_score}",
    verdict.replace("🟢","").replace("🟡","").replace("🔴","").replace("🟠","").replace("⚠️","").strip(),
    shares,
    round(cost, 2),
    "Yes" if IN_TRADE else "No"
])


# ============================================================
#  STEP 5: BUILD EMAIL
# ============================================================

def tick(p): return "✅" if p else "❌"
def tr_row(label, val, color="#e0e0e0"):
    return f"""<tr>
      <td style="padding:5px 12px;color:#888;font-size:13px;">{label}</td>
      <td style="padding:5px 12px;color:{color};font-weight:bold;">{val}</td>
    </tr>"""
def check_row(label, passed, detail=""):
    c = "#00cc66" if passed else "#ff4444"
    i = "✅" if passed else "❌"
    return f"""<tr>
      <td style="padding:5px 10px;color:{c};font-size:16px;">{i}</td>
      <td style="padding:5px 10px;color:#e0e0e0;font-size:13px;">{label}</td>
      <td style="padding:5px 10px;color:#666;font-size:12px;">{detail}</td>
    </tr>"""

# Earnings table rows for all 4 stocks
def earn_row(ticker, color="#e0e0e0"):
    d, s = days_to_earnings(ticker)
    warn = d is not None and 0 <= d <= 14
    c    = "#ff4444" if warn else color
    flag = " ⚠️" if warn else ""
    return f"""<tr>
      <td style="padding:4px 12px;color:#888;font-size:13px;">{ticker}</td>
      <td style="padding:4px 12px;color:{c};font-size:13px;">{s}{flag}</td>
      <td style="padding:4px 12px;color:#555;font-size:12px;">
        {f'{d} days' if d is not None and d >= 0 else 'passed'}</td>
    </tr>"""

# What to do today section
if IN_TRADE:
    action_text = f"""
    You are currently IN a trade (entered at {ENTRY_PRICE}p).<br><br>
    • <b>ATR Stop:</b> {atr_stop}p — exit immediately if price closes below this<br>
    • <b>Target 1:</b> {TARGET_1}p — sell half, move stop to breakeven<br>
    • <b>Target 2:</b> {TARGET_2}p — sell the rest<br>
    • Check the news before market open for any RR.L announcements
    """
elif cond_above_trigger and score >= 6:
    action_text = f"""
    Setup is <b>strong</b>. Entry trigger has been hit.<br><br>
    • Confirm price closed above {ENTRY_TRIGGER}p yesterday<br>
    • Check volume was above average on the breakout<br>
    • If confirmed: buy at market open (8:00am GMT)<br>
    • Suggested position: <b>{shares} shares</b> (~£{cost:,.0f})<br>
    • Set stop at <b>{atr_stop}p</b> immediately after buying
    """
elif score >= 5:
    action_text = f"""
    Setup is <b>building</b> — not quite ready yet.<br><br>
    • Watch for price to push above {ENTRY_TRIGGER}p on strong volume<br>
    • Check RR.L news this morning before market open<br>
    • Do not enter until score reaches 6+/{max_score}<br>
    • Keep your powder dry — patience is your edge
    """
else:
    action_text = f"""
    Setup is <b>not ready</b>.<br><br>
    • No action required today<br>
    • Continue monitoring daily<br>
    • The setup can form quickly — stay consistent
    """

html = f"""
<html>
<body style="background:#0b0d14;font-family:'Segoe UI',Arial,sans-serif;
             color:#e0e0e0;margin:0;padding:24px;max-width:700px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);
              border-radius:14px;padding:24px 28px;margin-bottom:18px;
              border-left:5px solid {verdict_color};">
    <div style="font-size:12px;color:#555;letter-spacing:2px;
                text-transform:uppercase;">Daily Swing Trade Report</div>
    <div style="font-size:11px;color:#444;margin-top:2px;">{today_str}</div>
    <div style="font-size:26px;font-weight:bold;color:white;margin-top:10px;">
      🛩️ Rolls-Royce Holdings · RR.L
    </div>
    <div style="font-size:32px;font-weight:900;color:{verdict_color};
                margin-top:6px;letter-spacing:-1px;">{verdict}</div>
    <div style="color:#888;margin-top:6px;font-size:14px;">{verdict_detail}</div>
    <div style="margin-top:16px;display:flex;gap:16px;">
      <div style="background:#0f1117;border-radius:8px;padding:10px 18px;
                  display:inline-block;margin-right:12px;">
        <div style="font-size:11px;color:#555;">PRICE</div>
        <div style="font-size:22px;font-weight:bold;color:white;">
          {current_price:.0f}p</div>
      </div>
      <div style="background:#0f1117;border-radius:8px;padding:10px 18px;
                  display:inline-block;margin-right:12px;">
        <div style="font-size:11px;color:#555;">SCORE</div>
        <div style="font-size:22px;font-weight:bold;color:{verdict_color};">
          {score}/{max_score}</div>
      </div>
      <div style="background:#0f1117;border-radius:8px;padding:10px 18px;
                  display:inline-block;">
        <div style="font-size:11px;color:#555;">ATR STOP</div>
        <div style="font-size:22px;font-weight:bold;color:#ff4444;">
          {atr_stop}p</div>
      </div>
    </div>
  </div>

  <!-- What to do today -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:10px;">📌 WHAT TO DO TODAY</div>
    <div style="font-size:14px;color:#ccc;line-height:1.7;">{action_text}</div>
  </div>

  <!-- Checklist -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:10px;">📋 ENTRY CHECKLIST ({score}/{max_score})</div>
    <table style="width:100%;border-collapse:collapse;">
      {check_row(f"Price above entry trigger ({ENTRY_TRIGGER}p)", cond_above_trigger, f"Current: {current_price:.0f}p | Gap: {gap:+.0f}p")}
      {check_row("Price above MA50", cond_above_ma50, f"MA50: {ma50:.0f}p")}
      {check_row("Price above MA20", cond_above_ma20, f"MA20: {ma20:.0f}p")}
      {check_row("RSI in healthy zone (40–65)", cond_rsi_healthy, f"RSI: {current_rsi:.1f}")}
      {check_row("MACD above signal line", cond_macd_bullish)}
      {check_row("MACD histogram improving", cond_macd_improving)}
      {check_row(f"Volume surge (>{VOLUME_SURGE:.0f}× average)", cond_vol_surge, f"Today: {vol_ratio:.1f}× average")}
      {check_row("FTSE 100 above MA50 (market healthy)", cond_ftse_ok, f"FTSE ETF: {ftse_price:.0f}p | MA50: {ftse_ma50:.0f}p")}
    </table>
  </div>

  <!-- Indicators + Position Size side by side -->
  <div style="display:flex;gap:16px;margin-bottom:18px;">

    <!-- Indicators -->
    <div style="flex:1;background:#1a1a2e;border-radius:14px;
                padding:20px 24px;border:1px solid #252540;">
      <div style="font-size:14px;font-weight:bold;color:#ffd700;
                  margin-bottom:10px;">📊 INDICATORS</div>
      <table style="width:100%;border-collapse:collapse;">
        {tr_row("Price",     f"{current_price:.0f}p", "#ffffff")}
        {tr_row("MA20",      f"{ma20:.0f}p",          "#ffd700")}
        {tr_row("MA50",      f"{ma50:.0f}p",          "#00bfff")}
        {tr_row("MA200",     f"{ma200:.0f}p",         "#ff6b6b")}
        {tr_row("RSI (14)",  f"{current_rsi:.1f}",    "#ffd700")}
        {tr_row("ATR (14)",  f"{current_atr:.0f}p",   "#cc88ff")}
        {tr_row("Volume",    f"{vol_ratio:.1f}× avg", "#00ff88" if cond_vol_surge else "#888")}
        {tr_row("FTSE",      "Healthy ✅" if ftse_ok else "Weak ❌", "#00cc66" if ftse_ok else "#ff4444")}
      </table>
    </div>

    <!-- Position size -->
    <div style="flex:1;background:#1a1a2e;border-radius:14px;
                padding:20px 24px;border:1px solid #252540;">
      <div style="font-size:14px;font-weight:bold;color:#ffd700;
                  margin-bottom:10px;">💰 POSITION SIZE</div>
      <table style="width:100%;border-collapse:collapse;">
        {tr_row("Account",      f"£{ACCOUNT_SIZE:,}",      "#888")}
        {tr_row("Risk %",       f"{RISK_PCT}%",             "#888")}
        {tr_row("Max loss",     f"£{ACCOUNT_SIZE*RISK_PCT/100:.0f}", "#ff4444")}
        {tr_row("Entry",        f"{ENTRY_TRIGGER}p",        "#00ff88")}
        {tr_row("ATR Stop",     f"{atr_stop}p",             "#ff4444")}
        {tr_row("Risk/share",   f"{risk_pp}p",              "#888")}
        {tr_row("Shares",       f"{shares}",                "#ffffff")}
        {tr_row("Total cost",   f"£{cost:,.0f}",            "#ffffff")}
        {tr_row("R:R to T1",    f"1:{rr_t1:.1f}",          "#88ddff")}
        {tr_row("R:R to T2",    f"1:{rr_t2:.1f}",          "#aaaaff")}
      </table>
    </div>

  </div>

  <!-- Earnings tracker -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:10px;">📅 EARNINGS TRACKER</div>
    <table style="width:100%;border-collapse:collapse;">
      <tr>
        <th style="text-align:left;padding:4px 12px;color:#555;
                   font-size:11px;letter-spacing:1px;">STOCK</th>
        <th style="text-align:left;padding:4px 12px;color:#555;
                   font-size:11px;letter-spacing:1px;">DATE</th>
        <th style="text-align:left;padding:4px 12px;color:#555;
                   font-size:11px;letter-spacing:1px;">COUNTDOWN</th>
      </tr>
      {earn_row("RR.L",  "#00ff88")}
      {earn_row("AZN.L", "#88ddff")}
      {earn_row("MSFT",  "#aaaaff")}
      {earn_row("RKLB",  "#ffaa00")}
    </table>
    <div style="color:#555;font-size:11px;margin-top:8px;">
      ⚠️ = within 14 days — do not enter new trades on that stock
    </div>
  </div>

  <!-- Chart -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:10px;">📈 6-MONTH CHART</div>
    <img src="cid:rrl_chart_v2" style="width:100%;border-radius:8px;" />
    <div style="color:#555;font-size:11px;margin-top:8px;">
      Green bars on volume = surge day (1.5× average) · 
      Dotted lines = your trade levels · 
      Purple panel = ATR volatility
    </div>
  </div>

  <!-- Footer -->
  <div style="text-align:center;color:#333;font-size:11px;
              padding:16px;line-height:1.8;">
    ⚠️ Research purposes only — not financial advice.<br>
    All trading carries risk. Never risk more than you can afford to lose.<br>
    RR.L Screener v2 · Generated {today_str}
  </div>

</body>
</html>
"""

plain = f"""
RR.L DAILY REPORT — {today_str}
{"="*50}
Price: {current_price:.0f}p | Score: {score}/{max_score} | {verdict}
ATR Stop: {atr_stop}p | Suggested shares: {shares} (~£{cost:,.0f})

CHECKLIST
{"─"*40}
{"✅" if cond_above_trigger else "❌"} Price > {ENTRY_TRIGGER}p
{"✅" if cond_above_ma50 else "❌"} Price > MA50 ({ma50:.0f}p)
{"✅" if cond_above_ma20 else "❌"} Price > MA20 ({ma20:.0f}p)
{"✅" if cond_rsi_healthy else "❌"} RSI 40-65 (currently {current_rsi:.1f})
{"✅" if cond_macd_bullish else "❌"} MACD bullish
{"✅" if cond_macd_improving else "❌"} MACD improving
{"✅" if cond_vol_surge else "❌"} Volume surge ({vol_ratio:.1f}x avg)
{"✅" if cond_ftse_ok else "❌"} FTSE healthy

EARNINGS
{"─"*40}
RR.L : {rrl_earnings_str}
"""


# ============================================================
#  STEP 6: SEND EMAIL
# ============================================================

print("📧 Sending email...")
msg             = MIMEMultipart("related")
msg["Subject"]  = (f"RR.L · {current_price:.0f}p · "
                   f"{score}/{max_score} · {verdict} · {today_str}")
msg["From"]     = SENDER_EMAIL
msg["To"]       = RECEIVER_EMAIL

alt = MIMEMultipart("alternative")
msg.attach(alt)
alt.attach(MIMEText(plain, "plain"))
alt.attach(MIMEText(html,  "html"))

with open(chart_path, "rb") as f:
    img = MIMEImage(f.read())
    img.add_header("Content-ID", "<rrl_chart_v2>")
    img.add_header("Content-Disposition", "inline", filename=chart_path)
    msg.attach(img)

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SENDER_EMAIL, SENDER_PASSWORD)
        s.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
    print("✅ Email sent successfully")
except Exception as e:
    print(f"❌ Email failed: {e}")
    raise

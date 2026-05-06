# ============================================================
#  RR.L CLAUDE AUTO-UPDATER
#  Runs every Sunday at 8am via GitHub Actions.
#
#  What it does:
#  1. Downloads latest RR.L price data
#  2. Sends it to the Claude API for analysis
#  3. Claude reviews current levels and suggests updates
#  4. This script patches rrl_screener_v2.py with new values
#  5. GitHub Actions commits and pushes the changes
#  6. Sends you an email summarising what changed and why
#
#  Required GitHub Secrets:
#  - ANTHROPIC_API_KEY  (from console.anthropic.com)
#  - SENDER_EMAIL, EMAIL_PASSWORD, RECEIVER_EMAIL (same as before)
# ============================================================

import subprocess
subprocess.run([
    "pip", "install",
    "yfinance", "pandas", "numpy", "anthropic",
    "--quiet"
])

import yfinance as yf
import pandas as pd
import numpy as np
import json
import re
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import anthropic
import warnings
warnings.filterwarnings("ignore")

print("✅ Libraries loaded")

SENDER_EMAIL    = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("EMAIL_PASSWORD")
RECEIVER_EMAIL  = os.environ.get("RECEIVER_EMAIL")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY")
SCREENER_FILE   = "rrl_screener_v2.py"
ALERT_FILE      = "rrl_midday_alert.py"
today_str       = datetime.today().strftime("%A %d %B %Y")


# ============================================================
#  STEP 1: PULL CURRENT MARKET DATA
# ============================================================

print("\n📥 Downloading RR.L data for Claude to review...")

def download(ticker, days=120):
    end   = datetime.today()
    start = end - timedelta(days=days)
    df    = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"), progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

df   = download("RR.L")
ftse = download("ISF.L")

if df.empty:
    print("❌ No data. Market closed or weekend. Exiting.")
    exit(0)

# Calculate indicators
df["MA20"]  = df["Close"].rolling(20).mean()
df["MA50"]  = df["Close"].rolling(50).mean()
df["MA200"] = df["Close"].rolling(200).mean()

delta       = df["Close"].diff()
gain        = delta.clip(lower=0)
loss        = -delta.clip(upper=0)
df["RSI"]   = 100 - (100 / (1 + gain.rolling(14).mean() /
                                 loss.rolling(14).mean()))

ema12       = df["Close"].ewm(span=12, adjust=False).mean()
ema26       = df["Close"].ewm(span=26, adjust=False).mean()
df["MACD"]  = ema12 - ema26
df["Sig"]   = df["MACD"].ewm(span=9, adjust=False).mean()

prev_c      = df["Close"].shift(1)
tr          = pd.concat([
    df["High"] - df["Low"],
    (df["High"] - prev_c).abs(),
    (df["Low"]  - prev_c).abs()
], axis=1).max(axis=1)
df["ATR"]   = tr.rolling(14).mean()
df["VolMA"] = df["Volume"].rolling(20).mean()

latest      = df.iloc[-1]
price       = float(latest["Close"])
ma20        = float(latest["MA20"])
ma50        = float(latest["MA50"])
ma200       = float(latest["MA200"])
rsi         = float(latest["RSI"])
macd        = float(latest["MACD"])
signal      = float(latest["Sig"])
atr         = float(latest["ATR"])
vol         = float(latest["Volume"])
vol_avg     = float(latest["VolMA"])

# Recent price range (3 months)
high_3m     = float(df["High"].tail(60).max())
low_3m      = float(df["Low"].tail(60).min())
week_change = float(((df["Close"].iloc[-1] - df["Close"].iloc[-6])
                     / df["Close"].iloc[-6]) * 100)

# FTSE status
ftse["MA50"] = ftse["Close"].rolling(50).mean()
ftse_price   = float(ftse.iloc[-1]["Close"])
ftse_ma50    = float(ftse.iloc[-1]["MA50"])
ftse_healthy = ftse_price > ftse_ma50

print(f"✅ Data ready — RR.L at {price:.0f}p | RSI {rsi:.1f} | ATR {atr:.0f}p")

# Read the current settings from the screener file
with open(SCREENER_FILE, "r") as f:
    current_code = f.read()

# Extract current values using regex
def extract(pattern, text, default=0):
    m = re.search(pattern, text)
    return float(m.group(1)) if m else default

current_entry  = extract(r"ENTRY_TRIGGER\s*=\s*([\d.]+)", current_code)
current_stop   = extract(r"ATR_STOP\s*=\s*([\d.]+)", current_code,
                 extract(r"STOP_LOSS_BASE\s*=\s*([\d.]+)", current_code))
current_t1     = extract(r"TARGET_1\s*=\s*([\d.]+)", current_code)
current_t2     = extract(r"TARGET_2\s*=\s*([\d.]+)", current_code)
current_acct   = extract(r"ACCOUNT_SIZE\s*=\s*([\d.]+)", current_code, 5000)

print(f"   Current levels: Entry {current_entry:.0f}p | "
      f"T1 {current_t1:.0f}p | T2 {current_t2:.0f}p")


# ============================================================
#  STEP 2: ASK CLAUDE TO REVIEW AND UPDATE THE LEVELS
# ============================================================

print("\n🤖 Asking Claude to review and update trade levels...")

prompt = f"""
You are a swing trading analyst reviewing a Rolls-Royce (RR.L) trade setup.
Today is {today_str}.

CURRENT MARKET DATA:
- Price: {price:.0f}p
- 3-month high: {high_3m:.0f}p
- 3-month low: {low_3m:.0f}p
- Week change: {week_change:+.1f}%
- MA20: {ma20:.0f}p
- MA50: {ma50:.0f}p
- MA200: {ma200:.0f}p
- RSI (14): {rsi:.1f}
- MACD: {macd:.2f} | Signal: {signal:.2f} | {'Bullish' if macd > signal else 'Bearish'}
- ATR (14): {atr:.0f}p (daily volatility measure)
- Volume: {vol/vol_avg:.1f}x average
- FTSE 100: {'Healthy (above MA50)' if ftse_healthy else 'Weak (below MA50)'}

CURRENT SCREENER SETTINGS:
- Entry trigger: {current_entry:.0f}p
- Target 1: {current_t1:.0f}p
- Target 2: {current_t2:.0f}p
- Account size: £{current_acct:.0f}

YOUR TASK:
Review whether the current trade levels are still appropriate given
the latest price data. Consider:
1. Is the entry trigger still at a meaningful resistance level?
2. Are the targets realistic given current price and ATR?
3. What ATR-based stop would you recommend (entry - 1.5x ATR)?
4. Are the levels still offering a minimum 2:1 risk/reward?

Respond ONLY with a JSON object. No other text, no markdown, no explanation
outside the JSON. Use this exact format:

{{
  "entry_trigger": <integer pence>,
  "target_1": <integer pence>,
  "target_2": <integer pence>,
  "atr_stop": <integer pence>,
  "risk_reward_t1": <float>,
  "changed": <true or false>,
  "summary": "<2-3 sentence plain English summary of what changed and why>",
  "market_view": "<1 sentence current market view on RR.L>",
  "caution": "<any warnings or risks to flag — empty string if none>"
}}
"""

client   = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
response = client.messages.create(
    model      = "claude-sonnet-4-20250514",
    max_tokens = 1000,
    messages   = [{"role": "user", "content": prompt}]
)

raw = response.content[0].text.strip()

# Clean up in case Claude wraps in backticks
raw = re.sub(r"^```json\s*", "", raw)
raw = re.sub(r"\s*```$",      "", raw)

try:
    updates = json.loads(raw)
    print(f"✅ Claude responded — changed: {updates.get('changed', False)}")
    print(f"   New levels: Entry {updates['entry_trigger']}p | "
          f"T1 {updates['target_1']}p | T2 {updates['target_2']}p | "
          f"Stop {updates['atr_stop']}p")
except json.JSONDecodeError as e:
    print(f"❌ Claude response parse error: {e}")
    print(f"   Raw response: {raw[:300]}")
    exit(1)


# ============================================================
#  STEP 3: PATCH THE SCREENER FILES WITH NEW VALUES
# ============================================================

def patch(code, var, new_val):
    """Replace a variable assignment in Python code."""
    pattern     = rf"^({var}\s*=\s*)[\d.]+(.*)$"
    replacement = rf"\g<1>{new_val}\2"
    return re.sub(pattern, replacement, code, flags=re.MULTILINE)

new_entry = updates["entry_trigger"]
new_t1    = updates["target_1"]
new_t2    = updates["target_2"]
new_stop  = updates["atr_stop"]

# Patch the main screener
updated_code = current_code
updated_code = patch(updated_code, "ENTRY_TRIGGER", new_entry)
updated_code = patch(updated_code, "TARGET_1",      new_t1)
updated_code = patch(updated_code, "TARGET_2",      new_t2)

with open(SCREENER_FILE, "w") as f:
    f.write(updated_code)

print(f"✅ {SCREENER_FILE} updated")

# Patch the midday alert file
with open(ALERT_FILE, "r") as f:
    alert_code = f.read()

alert_code = patch(alert_code, "ATR_STOP", new_stop)
alert_code = patch(alert_code, "TARGET_1", new_t1)
alert_code = patch(alert_code, "TARGET_2", new_t2)

with open(ALERT_FILE, "w") as f:
    f.write(alert_code)

print(f"✅ {ALERT_FILE} updated")


# ============================================================
#  STEP 4: SEND UPDATE EMAIL
# ============================================================

changed    = updates.get("changed", False)
summary    = updates.get("summary", "No changes needed.")
mkt_view   = updates.get("market_view", "")
caution    = updates.get("caution", "")
rr_t1      = updates.get("risk_reward_t1", 0)

def tr_row(label, old, new, highlight=False):
    changed_flag = old != new
    color  = "#ffd700" if changed_flag else "#888"
    change = f" → <b style='color:#ffd700'>{new}p</b>" if changed_flag else ""
    return f"""<tr>
      <td style="padding:5px 12px;color:#888;font-size:13px;">{label}</td>
      <td style="padding:5px 12px;color:{color};font-size:13px;">
        {old:.0f}p{change}</td>
    </tr>"""

html = f"""
<html>
<body style="background:#0b0d14;font-family:'Segoe UI',Arial,sans-serif;
             color:#e0e0e0;margin:0;padding:24px;max-width:680px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);
              border-radius:14px;padding:24px 28px;margin-bottom:18px;
              border-left:5px solid {'#ffd700' if changed else '#00cc66'};">
    <div style="font-size:11px;color:#555;letter-spacing:2px;
                text-transform:uppercase;">Weekly Auto-Update · {today_str}</div>
    <div style="font-size:24px;font-weight:bold;color:white;margin-top:8px;">
      🤖 Claude Updated Your Screener
    </div>
    <div style="font-size:20px;font-weight:bold;
                color:{'#ffd700' if changed else '#00cc66'};margin-top:6px;">
      {'⚡ Levels have been updated' if changed else '✅ No changes needed'}
    </div>
    <div style="color:#888;margin-top:8px;font-size:14px;">{mkt_view}</div>
  </div>

  <!-- Summary -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:10px;">📝 CLAUDE'S ANALYSIS</div>
    <div style="font-size:14px;color:#ccc;line-height:1.8;">{summary}</div>
    {f'<div style="margin-top:12px;padding:12px;background:#2a1a1a;border-radius:8px;color:#ffaa00;font-size:13px;">⚠️ {caution}</div>' if caution else ''}
  </div>

  <!-- Level changes -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:10px;">📊 TRADE LEVELS (old → new)</div>
    <table style="width:100%;border-collapse:collapse;">
      {tr_row("Entry trigger", current_entry, new_entry)}
      {tr_row("ATR stop",      current_stop,  new_stop)}
      {tr_row("Target 1",      current_t1,    new_t1)}
      {tr_row("Target 2",      current_t2,    new_t2)}
      <tr>
        <td style="padding:5px 12px;color:#888;font-size:13px;">Risk/Reward (T1)</td>
        <td style="padding:5px 12px;color:#88ddff;font-size:13px;">
          1:{rr_t1:.1f}</td>
      </tr>
    </table>
    <div style="margin-top:12px;color:#555;font-size:11px;">
      {'⚡ Yellow = changed level' if changed else 'All levels remain the same.'}
    </div>
  </div>

  <!-- What was updated -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:10px;">🔧 FILES UPDATED</div>
    <div style="font-size:13px;color:#888;line-height:2;">
      ✅ rrl_screener_v2.py — entry trigger, targets updated<br>
      ✅ rrl_midday_alert.py — ATR stop, targets updated<br>
      ✅ Changes committed to GitHub automatically<br>
      ✅ Tomorrow's 7:30am email will use the new levels
    </div>
  </div>

  <!-- Current data snapshot -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:10px;">📈 MARKET DATA USED</div>
    <table style="width:100%;border-collapse:collapse;">
      <tr><td style="padding:4px 12px;color:#888;font-size:13px;">Price</td>
          <td style="padding:4px 12px;color:white;">{price:.0f}p</td></tr>
      <tr><td style="padding:4px 12px;color:#888;font-size:13px;">MA20 / MA50 / MA200</td>
          <td style="padding:4px 12px;color:#ffd700;">{ma20:.0f}p / {ma50:.0f}p / {ma200:.0f}p</td></tr>
      <tr><td style="padding:4px 12px;color:#888;font-size:13px;">RSI</td>
          <td style="padding:4px 12px;color:#ffd700;">{rsi:.1f}</td></tr>
      <tr><td style="padding:4px 12px;color:#888;font-size:13px;">ATR (volatility)</td>
          <td style="padding:4px 12px;color:#cc88ff;">{atr:.0f}p/day</td></tr>
      <tr><td style="padding:4px 12px;color:#888;font-size:13px;">FTSE 100</td>
          <td style="padding:4px 12px;color:{'#00cc66' if ftse_healthy else '#ff4444'};">
            {'Healthy' if ftse_healthy else 'Weak'}</td></tr>
      <tr><td style="padding:4px 12px;color:#888;font-size:13px;">3m High / Low</td>
          <td style="padding:4px 12px;color:#888;">{high_3m:.0f}p / {low_3m:.0f}p</td></tr>
      <tr><td style="padding:4px 12px;color:#888;font-size:13px;">Week change</td>
          <td style="padding:4px 12px;
                     color:{'#00cc66' if week_change >= 0 else '#ff4444'};">
            {week_change:+.1f}%</td></tr>
    </table>
  </div>

  <div style="text-align:center;color:#333;font-size:11px;padding:16px;
              line-height:1.8;">
    ⚠️ All changes are suggestions from AI analysis — not financial advice.<br>
    Review the changes before trusting them. You can override them manually<br>
    in your GitHub repo at any time.<br>
    RR.L Auto-Updater · {today_str}
  </div>

</body>
</html>
"""

plain = f"""
RR.L CLAUDE AUTO-UPDATE — {today_str}
{"="*50}
{'Levels updated' if changed else 'No changes needed'}

ANALYSIS: {summary}

LEVELS (old → new):
Entry : {current_entry:.0f}p → {new_entry}p
Stop  : {current_stop:.0f}p  → {new_stop}p
T1    : {current_t1:.0f}p   → {new_t1}p
T2    : {current_t2:.0f}p   → {new_t2}p
R:R   : 1:{rr_t1:.1f}

{'CAUTION: ' + caution if caution else ''}
"""

print("📧 Sending update email...")
msg            = MIMEMultipart("alternative")
msg["Subject"] = (f"RR.L Auto-Update · "
                  f"{'Levels Changed ⚡' if changed else 'No Changes ✅'} · "
                  f"{today_str}")
msg["From"]    = SENDER_EMAIL
msg["To"]      = RECEIVER_EMAIL
msg.attach(MIMEText(plain, "plain"))
msg.attach(MIMEText(html,  "html"))

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SENDER_EMAIL, SENDER_PASSWORD)
        s.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
    print("✅ Update email sent")
except Exception as e:
    print(f"❌ Email failed: {e}")
    raise

print("\n✅ Auto-update complete")

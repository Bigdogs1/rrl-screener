# ============================================================
#  RR.L MIDDAY ALERT SCRIPT
#  Runs at 12:30pm GMT every weekday via GitHub Actions.
#  Only sends an alert if you are IN a trade (IN_TRADE=True)
#  AND price has hit your stop loss or a target.
#
#  This protects you while you're at work or away from
#  your phone during market hours.
# ============================================================

import subprocess
subprocess.run(["pip", "install", "yfinance", "--quiet"])

import yfinance as yf
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import os
import warnings
warnings.filterwarnings("ignore")


# ============================================================
#  YOUR SETTINGS — MUST MATCH rrl_screener_v2.py
# ============================================================

IN_TRADE     = False    # ← Set True when you enter a trade
ENTRY_PRICE  = 0        # ← Your actual entry price in pence
ATR_STOP     = 1120     # ← Copy from morning email's ATR Stop
TARGET_1     = 1280
TARGET_2     = 1350
TICKER       = "RR.L"

SENDER_EMAIL    = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("EMAIL_PASSWORD")
RECEIVER_EMAIL  = os.environ.get("RECEIVER_EMAIL")


# ============================================================
#  MAIN LOGIC
# ============================================================

if not IN_TRADE:
    print("ℹ️  Not in a trade — no midday alert needed. Exiting.")
    exit(0)

print("📥 Checking RR.L price...")
ticker = yf.Ticker(TICKER)
hist   = ticker.history(period="1d", interval="5m")

if hist.empty:
    print("❌ No price data available. Market may be closed.")
    exit(0)

current_price = float(hist["Close"].iloc[-1])
session_high  = float(hist["High"].max())
session_low   = float(hist["Low"].min())
now_str       = datetime.now().strftime("%H:%M GMT")
today_str     = datetime.now().strftime("%A %d %B %Y")
pnl_pence     = current_price - ENTRY_PRICE
pnl_pct       = (pnl_pence / ENTRY_PRICE) * 100 if ENTRY_PRICE > 0 else 0

print(f"   Current: {current_price:.0f}p | Entry: {ENTRY_PRICE}p | P&L: {pnl_pence:+.0f}p")

# Determine alert type
stop_hit     = current_price <= ATR_STOP
target1_hit  = current_price >= TARGET_1
target2_hit  = current_price >= TARGET_2

# Only send email on significant events OR as a daily check-in
if target2_hit:
    alert_type   = "🎯 TARGET 2 HIT"
    alert_color  = "#00cc66"
    alert_action = (f"TARGET 2 REACHED at {current_price:.0f}p. "
                    f"Consider selling your remaining position. "
                    f"Well done — that's the full trade.")
elif target1_hit:
    alert_type   = "🎯 TARGET 1 HIT"
    alert_color  = "#00cc66"
    alert_action = (f"TARGET 1 REACHED at {current_price:.0f}p. "
                    f"Sell HALF your position now. "
                    f"Move your stop to breakeven ({ENTRY_PRICE}p).")
elif stop_hit:
    alert_type   = "🛑 STOP LOSS HIT"
    alert_color  = "#ff4444"
    alert_action = (f"STOP LOSS TRIGGERED at {current_price:.0f}p. "
                    f"EXIT YOUR POSITION NOW. "
                    f"Do not wait to see if it bounces. Honour your plan.")
else:
    alert_type   = "📊 Midday Check-In"
    alert_color  = "#ffd700"
    alert_action = (f"Price is at {current_price:.0f}p — within your trade range. "
                    f"No action needed. Stop at {ATR_STOP}p, "
                    f"Target 1 at {TARGET_1}p.")

# Build HTML email
html = f"""
<html>
<body style="background:#0b0d14;font-family:'Segoe UI',Arial,sans-serif;
             color:#e0e0e0;margin:0;padding:24px;max-width:600px;">

  <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);
              border-radius:14px;padding:24px 28px;margin-bottom:18px;
              border-left:5px solid {alert_color};">
    <div style="font-size:11px;color:#555;letter-spacing:2px;
                text-transform:uppercase;">Midday Alert · {now_str}</div>
    <div style="font-size:24px;font-weight:bold;color:white;margin-top:8px;">
      🛩️ RR.L Midday Alert
    </div>
    <div style="font-size:28px;font-weight:900;color:{alert_color};
                margin-top:6px;">{alert_type}</div>
  </div>

  <!-- Action -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid {alert_color}33;">
    <div style="font-size:14px;font-weight:bold;color:{alert_color};
                margin-bottom:10px;">⚡ ACTION REQUIRED</div>
    <div style="font-size:15px;color:#fff;line-height:1.7;">{alert_action}</div>
  </div>

  <!-- Price snapshot -->
  <div style="background:#1a1a2e;border-radius:14px;padding:20px 24px;
              margin-bottom:18px;border:1px solid #252540;">
    <div style="font-size:14px;font-weight:bold;color:#ffd700;
                margin-bottom:12px;">📊 Trade Status</div>
    <table style="width:100%;border-collapse:collapse;">
      <tr>
        <td style="padding:5px 12px;color:#888;">Current price</td>
        <td style="padding:5px 12px;color:white;font-weight:bold;
                   font-size:18px;">{current_price:.0f}p</td>
      </tr>
      <tr>
        <td style="padding:5px 12px;color:#888;">Your entry</td>
        <td style="padding:5px 12px;color:#888;">{ENTRY_PRICE}p</td>
      </tr>
      <tr>
        <td style="padding:5px 12px;color:#888;">P&L</td>
        <td style="padding:5px 12px;
                   color:{'#00cc66' if pnl_pence >= 0 else '#ff4444'};
                   font-weight:bold;">{pnl_pence:+.0f}p ({pnl_pct:+.1f}%)</td>
      </tr>
      <tr>
        <td style="padding:5px 12px;color:#888;">Session high</td>
        <td style="padding:5px 12px;color:#26a69a;">{session_high:.0f}p</td>
      </tr>
      <tr>
        <td style="padding:5px 12px;color:#888;">Session low</td>
        <td style="padding:5px 12px;color:#ef5350;">{session_low:.0f}p</td>
      </tr>
      <tr>
        <td style="padding:5px 12px;color:#888;">Stop loss (ATR)</td>
        <td style="padding:5px 12px;color:#ff4444;font-weight:bold;">
          {ATR_STOP}p</td>
      </tr>
      <tr>
        <td style="padding:5px 12px;color:#888;">Target 1</td>
        <td style="padding:5px 12px;color:#88ddff;">{TARGET_1}p</td>
      </tr>
      <tr>
        <td style="padding:5px 12px;color:#888;">Target 2</td>
        <td style="padding:5px 12px;color:#aaaaff;">{TARGET_2}p</td>
      </tr>
    </table>
  </div>

  <div style="text-align:center;color:#333;font-size:11px;padding:16px;">
    ⚠️ Not financial advice. Honour your plan.<br>
    RR.L Midday Alert · {today_str}
  </div>
</body>
</html>
"""

plain = f"""
RR.L MIDDAY ALERT — {now_str}
{"="*40}
{alert_type}

{alert_action}

Current: {current_price:.0f}p
Entry:   {ENTRY_PRICE}p
P&L:     {pnl_pence:+.0f}p ({pnl_pct:+.1f}%)
Stop:    {ATR_STOP}p
T1:      {TARGET_1}p
T2:      {TARGET_2}p
"""

# Send
print(f"📧 Sending {alert_type} alert...")
msg            = MIMEMultipart("alternative")
msg["Subject"] = f"RR.L {alert_type} — {current_price:.0f}p — {now_str}"
msg["From"]    = SENDER_EMAIL
msg["To"]      = RECEIVER_EMAIL
msg.attach(MIMEText(plain, "plain"))
msg.attach(MIMEText(html,  "html"))

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SENDER_EMAIL, SENDER_PASSWORD)
        s.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
    print(f"✅ Alert sent: {alert_type}")
except Exception as e:
    print(f"❌ Email failed: {e}")
    raise

"""
Morning Financial Briefing — daily agent
Runs at 23:00 UTC (07:00 CST) via GitHub Actions cron.

Secrets required in GitHub repo settings:
  ANTHROPIC_API_KEY    — your Anthropic (or compatible) API key
  ANTHROPIC_BASE_URL   — (optional) custom base URL, e.g. for palebluedot proxy
  GMAIL_FROM           — Gmail address used to send (must own the app password)
  GMAIL_APP_PASSWORD   — Gmail App Password (not your Google account password)
  GMAIL_TO             — set in workflow env, defaults to jimmy.xu88@icloud.com
"""

import os
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

# ── Date in CST (UTC+8) ──────────────────────────────────────────────────────
CST = timezone(timedelta(hours=8))
today = datetime.now(CST)
date_str = today.strftime("%B %d, %Y")          # e.g. "April 04, 2026"
date_cn  = today.strftime("%Y-%m-%d")            # e.g. "2026-04-04"
subject  = f"Morning Financial Briefing — {date_str}"

# ── Anthropic client (supports custom base_url for proxy providers) ───────────
client_kwargs = {"api_key": os.environ["ANTHROPIC_API_KEY"]}
base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
if base_url:
    client_kwargs["base_url"] = base_url

client = anthropic.Anthropic(**client_kwargs)

# ── Prompt ────────────────────────────────────────────────────────────────────
PROMPT = f"""Today is {date_str} (China Standard Time, {date_cn}).

You are a financial analyst writing a daily morning briefing for a sophisticated investor. Search the web for the latest news and market data from the past 24 hours, then write the briefing below.

Write in plain text suitable for email — no markdown symbols like ** or ##. Use ALL CAPS for section headers. Write sections 1-3 in flowing analytical prose. Use numbered or labeled entries for section 4.

=== FORMAT ===

MORNING FINANCIAL BRIEFING — {date_str}

----------------------------------------------------------------------
1. US TECH & AI
----------------------------------------------------------------------
[Narrative covering NVDA, AAPL, and the broader AI/semiconductor sector. What moved? Why? What are the forward implications? Include earnings, analyst calls, product news, export controls, or macro headwinds/tailwinds. Context and interpretation, not just headlines.]

----------------------------------------------------------------------
2. GLOBAL MACRO
----------------------------------------------------------------------
[Narrative covering Fed and PBOC policy signals, US Treasury yields (2Y, 10Y), USD index (DXY), and key commodities (oil, gold, copper). What is the macro narrative — risk-on or risk-off? What is the market pricing in?]

----------------------------------------------------------------------
3. A-SHARE / CHINA MARKETS
----------------------------------------------------------------------
[Narrative covering policy signals from Beijing (NDRC, PBOC, regulators), market sentiment on CSI 300 / ChiNext, and sector rotation themes. What sectors are seeing inflows or outflows?]

----------------------------------------------------------------------
4. STOCK SPOTLIGHT
----------------------------------------------------------------------
For each of the 7 focus stocks, give a brief update: recent price action, any news or filings, sector context, and what to watch.

  世纪华通 / Century Huatong (002602):
  中信金属 / CITIC Metal (601061):
  五矿新能 / Minmetals New Energy (002628):
  恒邦股份 / Hengbang Co. (002237):
  雅克科技 / Yake Technology (002409):
  三峡能源 / Three Gorges Energy (600905):
  中国电建 / PowerChina (601669):

----------------------------------------------------------------------
5. KEY THEMES TO WATCH TODAY
----------------------------------------------------------------------
[4-5 bullet points summarizing the most important themes, risks, or catalysts to monitor during today's trading session.]

=== END FORMAT ===

Be direct and analytical. Every sentence should add information or interpretation. Avoid filler phrases and generic commentary.
"""

# ── Run Claude with web search (handles pause_turn continuations) ─────────────
tools = [{"type": "web_search_20260209", "name": "web_search"}]
messages = [{"role": "user", "content": PROMPT}]
MAX_CONTINUATIONS = 5
continuation = 0
response = None

print(f"Generating briefing for {date_str}...")

while True:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        tools=tools,
        messages=messages,
    )

    if response.stop_reason == "end_turn":
        break
    elif response.stop_reason == "pause_turn":
        if continuation >= MAX_CONTINUATIONS:
            print("Warning: reached max continuations, using partial response.")
            break
        continuation += 1
        print(f"Continuing (iteration {continuation})...")
        messages = [
            {"role": "user", "content": PROMPT},
            {"role": "assistant", "content": response.content},
        ]
    else:
        # tool_use or unexpected — shouldn't occur with server-side tools
        break

# Extract final text blocks
briefing_text = "\n".join(
    block.text for block in response.content
    if hasattr(block, "type") and block.type == "text"
).strip()

if not briefing_text:
    print("Error: no text content in response.", file=sys.stderr)
    sys.exit(1)

print(f"Briefing generated ({len(briefing_text)} chars). Sending email...")

# ── Send via Gmail SMTP ───────────────────────────────────────────────────────
gmail_from     = os.environ["GMAIL_FROM"]
gmail_password = os.environ["GMAIL_APP_PASSWORD"]
gmail_to       = os.environ.get("GMAIL_TO", "jimmy.xu88@icloud.com")

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"]    = gmail_from
msg["To"]      = gmail_to
msg.attach(MIMEText(briefing_text, "plain", "utf-8"))

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(gmail_from, gmail_password)
    server.sendmail(gmail_from, gmail_to, msg.as_string())

print(f"Done. Briefing sent to {gmail_to} with subject: {subject}")

"""
Morning Financial Briefing — daily agent
Runs at 23:00 UTC (07:00 CST) via GitHub Actions cron.

Secrets required in GitHub repo settings:
  ANTHROPIC_API_KEY    — your palebluedot API key
  ANTHROPIC_BASE_URL   — palebluedot base URL (e.g. https://open.palebluedot.ai/v1)
  GMAIL_FROM           — Gmail address used to send
  GMAIL_APP_PASSWORD   — Gmail App Password
  GMAIL_TO             — recipient (set in workflow env)
"""

import os
import smtplib
import sys
import feedparser
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

# ── Date in CST (UTC+8) ──────────────────────────────────────────────────────
CST = timezone(timedelta(hours=8))
today = datetime.now(CST)
date_str = today.strftime("%B %d, %Y")
date_cn  = today.strftime("%Y-%m-%d")
subject  = f"Morning Financial Briefing — {date_str}"

# ── Anthropic client ──────────────────────────────────────────────────────────
client_kwargs = {"api_key": os.environ["ANTHROPIC_API_KEY"]}
base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
if base_url:
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    client_kwargs["base_url"] = base_url

client = anthropic.Anthropic(**client_kwargs)

# ── Fetch RSS feeds ───────────────────────────────────────────────────────────
def fetch_feed(url, label, max_items=8):
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            return ""
        lines = [f"\n[{label}]"]
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()
            # strip HTML tags crudely
            import re
            summary = re.sub(r"<[^>]+>", " ", summary)[:250].strip()
            lines.append(f"• {title}")
            if summary and summary != title:
                lines.append(f"  {summary}")
        return "\n".join(lines)
    except Exception as e:
        print(f"Warning: could not fetch {label}: {e}")
        return ""

print(f"Fetching news for {date_str}...")

feeds = [
    ("https://finance.yahoo.com/rss/headline?s=NVDA",        "NVDA News"),
    ("https://finance.yahoo.com/rss/headline?s=AAPL",        "AAPL News"),
    ("https://finance.yahoo.com/rss/headline?s=AMD,INTC,TSM","AI/Semiconductor News"),
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch"),
    ("https://feeds.reuters.com/reuters/businessNews",        "Reuters Business"),
    ("https://feeds.reuters.com/reuters/technologyNews",      "Reuters Tech"),
    ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC Markets"),
    ("https://www.cnbc.com/id/10000664/device/rss/rss.html",  "CNBC Economy"),
    ("https://feeds.reuters.com/reuters/CNtopNews",           "Reuters China"),
    ("https://www.scmp.com/rss/92/feed",                      "SCMP Business"),
    ("https://finance.yahoo.com/rss/headline?s=000100.SZ,601061.SS,002602.SZ", "A-Share Focus Stocks"),
]

news_context = ""
for url, label in feeds:
    news_context += fetch_feed(url, label)

if len(news_context) > 14000:
    news_context = news_context[:14000] + "\n...[truncated]"

if news_context.strip():
    print(f"News context fetched: {len(news_context)} chars")
else:
    print("Warning: no RSS feeds returned content — Claude will write from training knowledge")

# ── Prompt ────────────────────────────────────────────────────────────────────
PROMPT = f"""Today is {date_str} (China Standard Time, {date_cn}).

You are a financial analyst writing a daily morning briefing for a sophisticated investor.

Below is a news feed collected this morning from financial RSS sources. Use it as your primary source, supplemented by your training knowledge, to write a comprehensive narrative briefing.

--- NEWS FEED ---
{news_context if news_context.strip() else "(No live feed available — write from training knowledge and note this.)"}
--- END NEWS FEED ---

Write the briefing in plain text suitable for email. No markdown symbols like ** or ##. Use ALL CAPS for section headers. Sections 1-3 in flowing analytical prose. Section 4 in labeled entries.

----------------------------------------------------------------------
MORNING FINANCIAL BRIEFING — {date_str}
----------------------------------------------------------------------

1. US TECH & AI
[Narrative covering NVDA, AAPL, and the broader AI/semiconductor sector. What moved? Why? What are the forward implications? Include earnings, analyst calls, product news, export controls, or macro headwinds/tailwinds. Context and interpretation, not just headlines.]

----------------------------------------------------------------------
2. GLOBAL MACRO
----------------------------------------------------------------------
[Narrative covering Fed and PBOC policy signals, US Treasury yields (2Y, 10Y), USD index (DXY), and key commodities (oil, gold, copper). What is the macro narrative — risk-on or risk-off? What is the market pricing in?]

----------------------------------------------------------------------
3. A-SHARE / CHINA MARKETS
----------------------------------------------------------------------
[Narrative covering policy signals from Beijing (NDRC, PBOC, regulators), market sentiment on CSI 300 / ChiNext, and sector rotation themes.]

----------------------------------------------------------------------
4. STOCK SPOTLIGHT
----------------------------------------------------------------------
For each of the 7 focus stocks, give a brief update: recent price action, any news, sector context, and what to watch.

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
4-5 bullet points summarizing the most important themes, risks, or catalysts to monitor during today's trading session.

Be direct and analytical. Every sentence should add information or interpretation.
"""

# ── Call Claude (no tool use) ─────────────────────────────────────────────────
print("Generating briefing...")

response = client.messages.create(
    model="anthropic/claude-sonnet-4.6",
    max_tokens=8000,
    messages=[{"role": "user", "content": PROMPT}],
)

briefing_text = "\n".join(
    block.text for block in response.content
    if hasattr(block, "type") and block.type == "text"
).strip()

if not briefing_text:
    print("Error: no text content in response.", file=sys.stderr)
    sys.exit(1)

# ── Save as markdown for Obsidian ────────────────────────────────────────────
obsidian_dir = "Daily Briefing"
os.makedirs(obsidian_dir, exist_ok=True)
md_path = os.path.join(obsidian_dir, f"{date_cn}.md")
with open(md_path, "w", encoding="utf-8") as f:
    f.write(f"# Morning Financial Briefing — {date_str}\n\n")
    f.write(briefing_text)
print(f"Saved to {md_path}")

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

print(f"Done. Sent to {gmail_to}: {subject}")

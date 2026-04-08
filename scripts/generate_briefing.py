"""
Morning Financial Briefing 2.0

Goal:
- Keep the macro morning note concise and high signal.
- Add an explicit "macro -> watchlist transmission" layer.
- Read the custom watchlist from Morning News/股票观察池.md.
"""

from __future__ import annotations

import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
import feedparser


# ── Time ─────────────────────────────────────────────────────────────────────
CST = timezone(timedelta(hours=8))
today = datetime.now(CST)
date_str = today.strftime("%B %d, %Y")
date_cn = today.strftime("%Y-%m-%d")
subject = f"Morning Financial Briefing — {date_str}"


# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_FILE = ROOT / "股票观察池.md"
OUTPUT_DIR = ROOT / "Daily Briefing"


# ── Watchlist parsing ────────────────────────────────────────────────────────
DEFAULT_STOCKS = [
    "龙源电力",
    "三峡能源",
    "雅克科技",
    "三七互娱",
    "胜宏科技",
]
DEFAULT_SECTORS = ["新能源运营", "游戏", "半导体材料", "贵金属", "宏观与大宗"]


def parse_watchlist(path: Path) -> tuple[list[str], list[str], str]:
    if not path.exists():
        return DEFAULT_STOCKS, DEFAULT_SECTORS, "（未找到股票观察池，已使用默认观察池）"

    stocks: list[str] = []
    sectors: list[str] = []
    mappings: list[str] = []
    current_section = ""

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            current_section = line[3:].strip()
            continue
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        if current_section in {"核心跟踪", "二级跟踪"}:
            if item not in stocks:
                stocks.append(item)
        elif current_section == "行业主题映射":
            mappings.append(item)
            sector = item.split("：", 1)[0].split(":", 1)[0].strip()
            if sector and sector not in sectors:
                sectors.append(sector)

    if not stocks:
        stocks = DEFAULT_STOCKS[:]
    if not sectors:
        sectors = DEFAULT_SECTORS[:]

    mapping_text = "\n".join(f"- {x}" for x in mappings) if mappings else "（未设置行业主题映射）"
    return stocks, sectors, mapping_text


STOCKS, SECTORS, WATCHLIST_MAPPING = parse_watchlist(WATCHLIST_FILE)


# ── Anthropic client ─────────────────────────────────────────────────────────
client_kwargs = {"api_key": os.environ["ANTHROPIC_API_KEY"]}
base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
if base_url:
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    client_kwargs["base_url"] = base_url
client = anthropic.Anthropic(**client_kwargs)


# ── RSS fetching ─────────────────────────────────────────────────────────────
def fetch_feed(url: str, label: str, max_items: int = 8) -> str:
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            return ""
        lines = [f"\n[{label}]"]
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()
            link = entry.get("link", "").strip()
            summary = re.sub(r"<[^>]+>", " ", summary)[:250].strip()
            lines.append(f"• {title}")
            if link:
                lines.append(f"  URL: {link}")
            if summary and summary != title:
                lines.append(f"  {summary}")
        return "\n".join(lines)
    except Exception as exc:
        print(f"Warning: could not fetch {label}: {exc}")
        return ""


print(f"Fetching news for {date_str}...")

feeds = [
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch"),
    ("https://feeds.reuters.com/reuters/businessNews", "Reuters Business"),
    ("https://feeds.reuters.com/reuters/technologyNews", "Reuters Tech"),
    ("https://feeds.reuters.com/reuters/CNtopNews", "Reuters China"),
    ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC Markets"),
    ("https://www.cnbc.com/id/10000664/device/rss/rss.html", "CNBC Economy"),
    ("https://finance.yahoo.com/rss/headline?s=^IXIC,^GSPC,NVDA,AAPL,AMD,TSM", "Yahoo Finance Tech"),
    ("https://finance.yahoo.com/rss/headline?s=GC=F,CL=F,SI=F,HG=F", "Yahoo Finance Commodities"),
    ("https://www.scmp.com/rss/92/feed", "SCMP Business"),
    ("https://finance.yahoo.com/rss/headline?s=000100.SZ,601061.SS,002409.SZ,002555.SZ,001289.SZ", "A-Share Focus Proxy"),
]

news_context = ""
for url, label in feeds:
    news_context += fetch_feed(url, label)

if len(news_context) > 18000:
    news_context = news_context[:18000] + "\n...[truncated]"

if news_context.strip():
    print(f"News context fetched: {len(news_context)} chars")
else:
    print("Warning: no RSS feeds returned content — model will rely on general knowledge")


stocks_str = "、".join(STOCKS)
sectors_str = "、".join(SECTORS)

PROMPT = f"""Today is {date_str} (China Standard Time, {date_cn}).

You are writing a morning macro briefing for a China A-share investor who already has a custom stock watchlist and existing company research notes.

Current stock watchlist: {stocks_str}
Current theme sectors: {sectors_str}

Theme mapping:
{WATCHLIST_MAPPING}

--- NEWS FEED ---
{news_context if news_context.strip() else "(No live feed available — write with caution and clearly note where direct feed support is missing.)"}
--- END NEWS FEED ---

Core requirements:
- Use concise English section headers but write the body in Chinese where natural.
- Prioritize only what changed expectations versus yesterday.
- Do not produce a generic global market essay.
- Always connect macro and global market changes back to the A-share watchlist.
- If the feed does not support a claim directly, state that the signal is inferential.
- No investment advice phrasing like “buy/sell”; focus on research implications.
- Target reading time: under 5 minutes.

Output EXACTLY this structure:

---
Morning Financial Briefing — {date_str}
---

## Top Global News (Ranked)

[4–6 items max]

**[Headline]**
Source: [source]
Link: [URL or N/A]
Impact: High / Medium / Low

- What's NEW:
- Why it matters:
- 中文总结：

---

## Macro Regime Snapshot

- 当前宏观主线：
- 风险偏好：偏强 / 中性 / 偏弱
- 利率与流动性线索：
- 大宗商品主线：
- 美股科技情绪：
- 中文总结：

---

## A-Share Watchlist Transmission

For each relevant theme, explain how the global/macroeconomic change transmits to the custom watchlist.

### [主题名称]
- 变化内容：
- 影响公司：
- 传导方向：正面 / 中性 / 负面
- 主要逻辑：
- 哪类公司最该复核：

At minimum, consider:
- 新能源运营
- 游戏
- 半导体材料
- 贵金属 / 大宗（if relevant)

---

## Watchlist Companies To Recheck Today

Only list companies from the custom watchlist that deserve extra attention because of macro, sector, or overnight global developments.

- [公司名]：为什么今天要重点复核

If none, write:
- 今日无因宏观或海外市场变化而必须立即复核的观察池公司

---

## Key Signals To Watch (Next 24–48h)

- [3–5 bullets]

---

## Morning Takeaways

- [3–5 bullets focused on research implications, not trading calls]

---
"""


print("Generating briefing...")
response = client.messages.create(
    model="anthropic/claude-sonnet-4.6",
    max_tokens=8000,
    messages=[{"role": "user", "content": PROMPT}],
)

briefing_text = "\n".join(
    block.text for block in response.content if getattr(block, "type", "") == "text"
).strip()

if not briefing_text:
    print("Error: no text content in response.", file=sys.stderr)
    sys.exit(1)

OUTPUT_DIR.mkdir(exist_ok=True)
md_path = OUTPUT_DIR / f"{date_cn}.md"
with md_path.open("w", encoding="utf-8") as fh:
    fh.write(f"# Morning Financial Briefing — {date_str}\n\n")
    fh.write(briefing_text)
print(f"Saved to {md_path}")

print(f"Briefing generated ({len(briefing_text)} chars). Sending email...")

gmail_from = os.environ["GMAIL_FROM"]
gmail_password = os.environ["GMAIL_APP_PASSWORD"]
gmail_to = os.environ.get("GMAIL_TO", "jimmy.xu88@icloud.com")

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"] = gmail_from
msg["To"] = gmail_to
msg.attach(MIMEText(briefing_text, "plain", "utf-8"))

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(gmail_from, gmail_password)
    server.sendmail(gmail_from, gmail_to, msg.as_string())

print(f"Done. Sent to {gmail_to}: {subject}")

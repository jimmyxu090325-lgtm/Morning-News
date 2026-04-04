"""
China Tax Intelligence System — daily brief
Runs at 23:00 UTC (07:00 CST) via GitHub Actions cron.

Secrets required (same as morning briefing):
  ANTHROPIC_API_KEY    — palebluedot API key
  ANTHROPIC_BASE_URL   — palebluedot base URL
  GMAIL_FROM           — Gmail address used to send
  GMAIL_APP_PASSWORD   — Gmail App Password
  GMAIL_TO             — recipient (set in workflow env)
"""

import os
import re
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
subject  = f"中国税务情报日报 — {date_cn}"

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
            link = entry.get("link", "").strip()
            summary = re.sub(r"<[^>]+>", " ", summary)[:300].strip()
            lines.append(f"• {title}")
            if link:
                lines.append(f"  URL: {link}")
            if summary and summary != title:
                lines.append(f"  {summary}")
        return "\n".join(lines)
    except Exception as e:
        print(f"Warning: could not fetch {label}: {e}")
        return ""

print(f"Fetching tax news for {date_cn}...")

feeds = [
    ("https://feeds.reuters.com/reuters/CNtopNews",          "Reuters 中国"),
    ("https://feeds.reuters.com/reuters/businessNews",        "Reuters 商业"),
    ("https://www.scmp.com/rss/92/feed",                      "南华早报 商业"),
    ("https://feeds.reuters.com/reuters/companyNews",         "Reuters 公司动态"),
    ("https://finance.yahoo.com/rss/headline?s=tax+china",   "Yahoo 中国税务"),
    ("https://www.oecd.org/tax/automatic-exchange/news.xml", "OECD 税务"),
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch"),
    ("https://www.chinatax.gov.cn/rss/chinataxrss.xml",      "国家税务总局"),
]

news_context = ""
for url, label in feeds:
    news_context += fetch_feed(url, label)

if len(news_context) > 14000:
    news_context = news_context[:14000] + "\n...[内容截断]"

if news_context.strip():
    print(f"新闻内容已抓取：{len(news_context)} 字符")
else:
    print("警告：RSS 未返回内容，将基于训练知识生成")

# ── Prompt ────────────────────────────────────────────────────────────────────
PROMPT = f"""今天是 {date_cn}（中国标准时间）。

你是一位资深涉税顾问的助理，负责准备每日《中国税务情报日报》。

以下是今日从各大新闻源抓取的资讯，请以此为主要依据，结合专业知识，生成结构化日报。

--- 新闻源 ---
{news_context if news_context.strip() else "（今日无实时新闻源，请基于最新专业知识生成，并注明信息来源）"}
--- 新闻源结束 ---

核心原则：
- 主要使用中文输出
- 结构清晰，使用要点格式，不写长段落
- 只纳入对监管方向、执法趋势或客户风险有意义的内容
- 对每条资讯，尽量附上原文链接（使用新闻源中提供的 URL）
- 若信息不构成实质性信号，不要纳入

请严格按照以下格式输出：

---
中国税务情报日报 — {date_cn}
---

### 🧾 重要税务政策动态

（每条格式如下，共 3–5 条）

**[政策名称 / 标题]**
来源：[国家税务总局 / 财政部 / Bloomberg / Reuters 等]
链接：[URL，无则填"暂无"]
风险等级：高 / 中 / 低

- 变化内容：
- 影响对象：
- 监管意图：
- 实务影响：

---

### 🚨 重大税务执法案例（最重要）

（每条格式如下，共 2–4 条；若当日无新案例，注明"今日暂无重大执法公告"）

**[公司名称 + 事件]**
来源：[公告 / 交易所 / 媒体]
链接：[URL，无则填"暂无"]
风险等级：高 / 中 / 低

- 事件经过：
- 税务问题类型：
- 涉及金额：（如有）
- 执法信号：
- 潜在关联风险方：

---

### 🌐 CRS 与国际税务动态

（每条格式如下，共 2–3 条；若无重要动态，注明"今日无重大 CRS 新动态"）

**[标题]**
来源：[OECD / 国家税务总局 / 媒体]
链接：[URL，无则填"暂无"]
风险等级：高 / 中 / 低

- 变化内容：
- 对离岸 / 高净值客户的影响：
- 执法趋势：

---

### 🎯 今日风险雷达

- 3–5 条要点，聚焦今日出现的新风险信号

---

### 💡 今日客户沟通建议

- 2–3 条要点，说明今天应主动向客户传达什么信息

---
"""

# ── Call Claude ───────────────────────────────────────────────────────────────
print("正在生成税务日报...")

response = client.messages.create(
    model="anthropic/claude-sonnet-4.6",
    max_tokens=8000,
    messages=[{"role": "user", "content": PROMPT}],
)

brief_text = "\n".join(
    block.text for block in response.content
    if hasattr(block, "type") and block.type == "text"
).strip()

if not brief_text:
    print("Error: 未获取到文本内容", file=sys.stderr)
    sys.exit(1)

print(f"日报已生成（{len(brief_text)} 字符）。正在发送邮件...")

# ── Save as markdown for Obsidian ─────────────────────────────────────────────
obsidian_dir = "Tax Daily News"
os.makedirs(obsidian_dir, exist_ok=True)
md_path = os.path.join(obsidian_dir, f"{date_cn}.md")
with open(md_path, "w", encoding="utf-8") as f:
    f.write(f"# 中国税务情报日报 — {date_cn}\n\n")
    f.write(brief_text)
print(f"已保存至 {md_path}")

# ── Send via Gmail SMTP ───────────────────────────────────────────────────────
gmail_from     = os.environ["GMAIL_FROM"]
gmail_password = os.environ["GMAIL_APP_PASSWORD"]
gmail_to       = os.environ.get("GMAIL_TO", "jimmy.xu88@icloud.com")

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"]    = gmail_from
msg["To"]      = gmail_to
msg.attach(MIMEText(brief_text, "plain", "utf-8"))

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(gmail_from, gmail_password)
    server.sendmail(gmail_from, gmail_to, msg.as_string())

print(f"完成。已发送至 {gmail_to}，主题：{subject}")

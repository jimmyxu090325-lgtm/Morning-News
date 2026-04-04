"""
A-Share Daily Stock Research Brief
Runs at 23:00 UTC (07:00 CST) daily via GitHub Actions cron.
Also runs at 13:00 UTC (21:00 CST) on Fridays for weekly summary.

Secrets required (same as other briefings):
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
import requests
import feedparser
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

# ── Date / mode ───────────────────────────────────────────────────────────────
CST = timezone(timedelta(hours=8))
today = datetime.now(CST)
date_cn   = today.strftime("%Y-%m-%d")
weekday   = today.weekday()          # 0=Mon … 4=Fri
hour_cst  = today.hour

# Friday evening run (13:00 UTC = 21:00 CST) → weekly summary mode
WEEKLY_MODE = (weekday == 4 and hour_cst >= 20)

subject = (
    f"A股研究周报 — {date_cn}" if WEEKLY_MODE
    else f"A股每日研究简报 — {date_cn}"
)

# ── Stocks & sectors ──────────────────────────────────────────────────────────
STOCKS = [
    "中信金属", "五矿新能", "世纪华通", "三峡能源",
    "中国电建", "恒邦股份", "雅克科技", "远东股份", "特变电工",
]
SECTORS = ["科技", "能源（新能源/光伏）", "贵金属", "游戏"]

# ── Anthropic client ──────────────────────────────────────────────────────────
client_kwargs = {"api_key": os.environ["ANTHROPIC_API_KEY"]}
base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
if base_url:
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    client_kwargs["base_url"] = base_url

client = anthropic.Anthropic(**client_kwargs)

# ── Browser-like headers ──────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# ── HTML listing scraper ──────────────────────────────────────────────────────
def fetch_html(url, label, max_items=10):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["nav", "footer", "script", "style", "header",
                         "aside", "form", "button"]):
            tag.decompose()

        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        seen_titles, items = set(), []

        for a in soup.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            href  = a["href"].strip()
            if len(title) < 6 or title in seen_titles:
                continue
            if any(x in href for x in ["javascript:", "mailto:", "#", "void("]):
                continue
            href = urljoin(base, href) if not href.startswith("http") else href
            link_domain = urlparse(href).netloc
            base_domain = urlparse(url).netloc
            if link_domain and link_domain != base_domain:
                root = lambda d: ".".join(d.split(".")[-2:])
                if root(link_domain) != root(base_domain):
                    continue
            seen_titles.add(title)
            items.append((title, href))
            if len(items) >= max_items:
                break

        if not items:
            print(f"  [{label}] 未找到文章链接")
            return ""

        lines = [f"\n[{label}]"]
        for title, href in items:
            lines.append(f"• {title}")
            lines.append(f"  URL: {href}")
        return "\n".join(lines)
    except Exception as e:
        print(f"Warning: could not fetch {label} ({url}): {e}")
        return ""


def fetch_rss(url, label, max_items=8):
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            return ""
        lines = [f"\n[{label}]"]
        for entry in feed.entries[:max_items]:
            title   = entry.get("title", "").strip()
            link    = entry.get("link", "").strip()
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))[:200].strip()
            lines.append(f"• {title}")
            if link:
                lines.append(f"  URL: {link}")
            if summary and summary != title:
                lines.append(f"  {summary}")
        return "\n".join(lines)
    except Exception as e:
        print(f"Warning: could not fetch RSS {label}: {e}")
        return ""


# ── Sources ───────────────────────────────────────────────────────────────────
print(f"Fetching stock news for {date_cn} (weekly={WEEKLY_MODE})...")

SOURCES = [
    # 巨潮资讯 — 官方公告披露平台（上交所 + 深交所合并）
    ("https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
     "巨潮资讯·公告查询",    "html"),
    # 东方财富 — 研报频道
    ("https://data.eastmoney.com/report/",
     "东方财富·研报",        "html"),
    # 东方财富 — A股公告
    ("https://data.eastmoney.com/notices/",
     "东方财富·公告",        "html"),
    # 证券时报
    ("https://www.stcn.com/stock/",
     "证券时报·股票",        "html"),
    # 新浪财经 — A股要闻
    ("https://finance.sina.com.cn/stock/",
     "新浪财经·股票",        "html"),
    # 上交所公告
    ("http://www.sse.com.cn/disclosure/listedinfo/announcement/",
     "上交所·公告",          "html"),
    # 深交所公告
    ("https://www.szse.cn/disclosure/listed/notice/",
     "深交所·公告",          "html"),
    # 证券日报
    ("https://www.zqrb.cn/stock/",
     "证券日报·股票",        "html"),
    # ── 美股 & 期货夜盘 ───────────────────────────────────────────────────────
    # Yahoo Finance RSS — 科技股（NVDA/AAPL/AMD + 纳指）
    ("https://finance.yahoo.com/rss/headline?s=^IXIC,^GSPC,NVDA,AAPL,AMD,TSM",
     "Yahoo Finance·美股科技",  "rss"),
    # Yahoo Finance RSS — 黄金 / 原油期货
    ("https://finance.yahoo.com/rss/headline?s=GC=F,CL=F,SI=F",
     "Yahoo Finance·贵金属/原油", "rss"),
    # MarketWatch — 美股要闻
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories",
     "MarketWatch·美股要闻",    "rss"),
    # Reuters — 商品与能源
    ("https://feeds.reuters.com/reuters/businessNews",
     "Reuters·商业",            "rss"),
    # 东方财富 — 期货频道（A股夜盘）
    ("https://futures.eastmoney.com/",
     "东方财富·期货",           "html"),
]

news_context = ""
for url, label, kind in SOURCES:
    if kind == "rss":
        news_context += fetch_rss(url, label)
    else:
        news_context += fetch_html(url, label)

if len(news_context) > 22000:
    news_context = news_context[:22000] + "\n...[内容截断]"

if news_context.strip():
    print(f"数据已抓取：{len(news_context)} 字符")
else:
    print("警告：未获取到任何内容，将基于训练知识生成")

# ── Prompts ───────────────────────────────────────────────────────────────────
stocks_str  = "、".join(STOCKS)
sectors_str = "、".join(SECTORS)

DAILY_PROMPT = f"""今天是 {date_cn}（中国标准时间）。

你是一位专注于A股市场的研究员助理，负责每日追踪以下行业和个股。

追踪行业：{sectors_str}
追踪个股：{stocks_str}

--- 今日抓取数据 ---
{news_context if news_context.strip() else "（今日无实时数据，请基于最新专业知识生成，并注明来源）"}
--- 数据结束 ---

核心原则：
- 全程使用中文
- 只报告今日有实质性信息的内容，没有信息的股票/行业直接略过
- 每条必须附来源名称和链接（无链接则注明"暂无链接"）
- 只有真正重大的消息才触发提醒（如业绩预警、重组并购、监管处罚、停复牌等）
- 不需要操作建议

请按以下格式输出：

---
A股每日研究简报 — {date_cn}
---

### 📢 个股公告与重要信息

（有公告或重大信息才列出，无信息的股票略过）

**[股票名称]（股票代码）**
来源：[交易所公告 / 媒体报道]
链接：[URL]

- 公告/事件摘要：
- 影响判断：利好 / 利空 / 中性

---

### 📑 行业研报动态

（今日有无新发布的行业研究报告，按行业分组）

**[行业名称]**
- [研报标题] — [券商/机构]（链接：URL）
- 核心观点摘要：

---

### 🌙 美股夜盘 & 期货夜盘 → A股影响

**美股收盘情况**
- 纳斯达克 / 标普500：涨跌幅及主要驱动
- 相关科技股（NVDA / AAPL / AMD / TSM）：涨跌情况
- 市场情绪：风险偏好 偏强 / 中性 / 偏弱

**期货夜盘**
- 黄金期货（COMEX / 沪金）：
- 原油期货（WTI / 布伦特）：
- 其他相关期货（铜、白银等）：

**对追踪行业的传导分析**

| 行业 | 受影响方向 | 主要逻辑 |
|------|-----------|---------|
| 科技 | 利好/利空/中性 | |
| 能源·新能源·光伏 | 利好/利空/中性 | |
| 贵金属 | 利好/利空/中性 | |
| 游戏 | 利好/利空/中性 | |

---

### 🚨 重大消息提醒

（仅在有真正重大事件时才写此节，例如：监管处罚、重组、业绩预警、重要政策冲击等）

**[股票/行业 + 事件]**
- 事件内容：
- 影响程度：高 / 中

（如无重大消息，写"今日无重大提醒"）

---
"""

WEEKLY_PROMPT = f"""今天是 {date_cn}（周五，中国标准时间）。

你是一位专注于A股市场的研究员助理，负责每周五晚间为以下行业和个股生成周度走势回顾。

追踪行业：{sectors_str}
追踪个股：{stocks_str}

--- 本周抓取数据 ---
{news_context if news_context.strip() else "（无实时数据，请基于训练知识生成，并注明来源）"}
--- 数据结束 ---

核心原则：
- 全程使用中文
- 总结本周（周一至周五）的整体表现和关键事件
- 每个行业和个股都要有覆盖，即使没有重大事件也要说明走势方向
- 附上重要来源和链接

请按以下格式输出：

---
A股研究周报 — 本周（截至 {date_cn}）
---

### 📊 本周市场回顾

- A股整体走势（涨跌幅、成交量变化）：
- 主要驱动因素：

**本周美股 & 大宗商品表现**
- 纳斯达克 / 标普500本周涨跌：
- 黄金本周走势：
- 原油本周走势：
- 对A股的整体影响：偏正面 / 中性 / 偏负面

---

### 🏭 行业周度表现

（每个行业单独一节）

**科技板块**
- 本周走势：
- 关键事件：
- 下周关注点：

**能源·新能源·光伏**
- 本周走势：
- 关键事件：
- 下周关注点：

**贵金属**
- 本周走势：
- 关键事件：
- 下周关注点：

**游戏**
- 本周走势：
- 关键事件：
- 下周关注点：

---

### 🔍 个股周度总结

（每只股票都需覆盖）

**[股票名称]（股票代码）**
- 本周走势方向：上涨 / 下跌 / 横盘
- 本周关键事件/公告：（无则写"无重大事件"）
- 下周关注点：

---

### 🎯 下周重点关注
- 3–5条下周需要重点留意的信号或事件

---
"""

# ── Call Claude ───────────────────────────────────────────────────────────────
print("正在生成研究简报...")

prompt = WEEKLY_PROMPT if WEEKLY_MODE else DAILY_PROMPT

response = client.messages.create(
    model="anthropic/claude-sonnet-4.6",
    max_tokens=8000,
    messages=[{"role": "user", "content": prompt}],
)

brief_text = "\n".join(
    block.text for block in response.content
    if hasattr(block, "type") and block.type == "text"
).strip()

if not brief_text:
    print("Error: 未获取到文本内容", file=sys.stderr)
    sys.exit(1)

print(f"简报已生成（{len(brief_text)} 字符）。正在保存并发送...")

# ── Save as markdown for Obsidian ─────────────────────────────────────────────
obsidian_dir = "Stock Research"
os.makedirs(obsidian_dir, exist_ok=True)
suffix = "-weekly" if WEEKLY_MODE else ""
md_path = os.path.join(obsidian_dir, f"{date_cn}{suffix}.md")
with open(md_path, "w", encoding="utf-8") as f:
    f.write(f"# {subject}\n\n")
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

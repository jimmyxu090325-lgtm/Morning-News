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
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
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

# ── HTML listing page scraper ─────────────────────────────────────────────────
def fetch_html(url, label, max_items=8):
    """Scrape an HTML listing page and extract article titles + links."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove non-content elements
        for tag in soup(["nav", "footer", "script", "style", "header",
                         "aside", "form", "button"]):
            tag.decompose()

        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        seen_titles, items = set(), []

        for a in soup.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            href  = a["href"].strip()

            # Basic quality filters
            if len(title) < 8:
                continue
            if title in seen_titles:
                continue
            if any(x in href for x in ["javascript:", "mailto:", "#", "void("]):
                continue

            # Make absolute URL
            href = urljoin(base, href) if not href.startswith("http") else href

            # Skip links pointing to external unrelated domains
            link_domain = urlparse(href).netloc
            base_domain = urlparse(url).netloc
            if link_domain and link_domain != base_domain:
                # Allow if same root domain (e.g. sub.chinatax.gov.cn)
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


# ── RSS feed fetcher (kept for OECD) ─────────────────────────────────────────
def fetch_rss(url, label, max_items=6):
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            return ""
        lines = [f"\n[{label}]"]
        for entry in feed.entries[:max_items]:
            title   = entry.get("title", "").strip()
            link    = entry.get("link", "").strip()
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))[:250].strip()
            lines.append(f"• {title}")
            if link:
                lines.append(f"  URL: {link}")
            if summary and summary != title:
                lines.append(f"  {summary}")
        return "\n".join(lines)
    except Exception as e:
        print(f"Warning: could not fetch RSS {label}: {e}")
        return ""


# ── Source list ───────────────────────────────────────────────────────────────
print(f"Fetching tax news for {date_cn}...")

# (url, label, type)  type = "html" | "rss"
SOURCES = [
    # ── Tier 1: 官方权威来源 ──────────────────────────────────────────────────
    ("https://www.chinatax.gov.cn/chinatax/n810341/n810825/index.html",
     "国家税务总局·最新动态",     "html"),
    ("https://fgk.chinatax.gov.cn/zcfgk/c100016/index.html",
     "国家税务总局·政策法规库",   "html"),
    ("https://www.mof.gov.cn/zhengwuxinxi/zhengcefabu/",
     "财政部·政策发布",           "html"),
    ("https://szs.mof.gov.cn/zhengcefabu/",
     "财政部税政司·政策发布",     "html"),
    ("http://www.customs.gov.cn/customs/302249/302266/index.html",
     "海关总署·公告",             "html"),
    # ── Tier 2: 四大会计师事务所 ──────────────────────────────────────────────
    ("https://kpmg.com/cn/en/services/tax/china-tax-insights/china-tax-alert.html",
     "毕马威·China Tax Alert",    "html"),
    ("https://www.pwccn.com/en/services/tax/publications/taxlibrary-chinatax.html",
     "普华永道·China Tax News",   "html"),
    ("https://www.deloitte.com/cn/en/services/tax/perspectives/tax-newsflash.html",
     "德勤·Tax Newsflash",        "html"),
    ("https://www.ey.com/en_cn/tax/tax-alerts",
     "安永·China Tax Alerts",     "html"),
    # ── Tier 3: 国际 / CRS ───────────────────────────────────────────────────
    ("https://www.oecd.org/tax/automatic-exchange/news.xml",
     "OECD·CRS/BEPS",             "rss"),
    # ── 专业媒体 ──────────────────────────────────────────────────────────────
    ("https://www.chinatax.gov.cn/rss/chinataxrss.xml",
     "国家税务总局·RSS",          "rss"),
]

news_context = ""
for url, label, kind in SOURCES:
    if kind == "rss":
        news_context += fetch_rss(url, label)
    else:
        news_context += fetch_html(url, label)

if len(news_context) > 18000:
    news_context = news_context[:18000] + "\n...[内容截断]"

if news_context.strip():
    print(f"新闻内容已抓取：{len(news_context)} 字符")
else:
    print("警告：未获取到任何内容，将基于训练知识生成")

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
- **每条资讯必须附上来源名称和原文链接（来源链接为强制要求，无链接不得纳入）**
- 若信息不构成实质性信号，不要纳入
- **地域范围严格限定为中国大陆（不包括香港、澳门、台湾）**；港澳台相关内容一律排除

信息来源优先级（CRITICAL）：

Tier 1 — 官方权威来源（最高优先级）：
  - 国家税务总局官网（chinatax.gov.cn）
  - 各省市税务局官网
  - 财政部官网（mof.gov.cn）
  - 海关总署官网（customs.gov.cn）
  → 用于：新税收法规、官方政策发布、执法公告、税务案例

Tier 2 — 专业机构（高优先级）：
  - 四大会计师事务所（普华永道 PwC、德勤 Deloitte、安永 EY、毕马威 KPMG）的中国税务洞察
  - 国内领先律所（如中伦、金杜）的税务专栏
  → 用于：税务规则解读、执法趋势分析、税务风险专业洞察

Tier 3 — 国际 / CRS 来源：
  - OECD（CRS / BEPS 更新）
  - 四大事务所国际税务出版物
  → 用于：CRS 动态、跨境税务执法、全球税收透明度趋势

Tier 4 — 财经媒体（仅补充）：
  - Bloomberg、Financial Times、Reuters
  → 仅在报道涉及上市公司的重大税务案件或重大执法事件时使用

来源规则：
1. 每条必须附来源名称和链接（强制）
2. 优先引用原始来源，而非二手报道
3. 若多个来源并存：官方 > 四大 > 律所 > 媒体
4. 不得引用未经核实或低质量网站
5. 若无可信来源，该条目不得纳入

请严格按照以下格式输出：

---
中国税务情报日报 — {date_cn}
---

### 🧾 重要税务政策动态

（每条格式如下，共 3–5 条；来源优先：国家税务总局 > 财政部 > 四大解读）

**[政策名称 / 标题]**
来源：[机构名称]
链接：[URL（必填，无可信链接则不纳入此条）]
风险等级：高 / 中 / 低

- 变化内容：
- 影响对象：
- 监管意图：
- 实务影响：

---

### 🚨 重大税务执法案例（最重要）

（每条格式如下，共 2–4 条；来源优先：① 税务局行政处罚决定书/公告 ② 裁判文书网判决 ③ 最高检/法院通报 ④ 四大分析 ⑤ 媒体报道；若当日无新案例，注明"今日暂无重大执法公告"）

**[公司名称 + 事件]**
来源：[机构名称]
链接：[URL（必填）]
风险等级：高 / 中 / 低

- 事件经过：
- 税务问题类型：
- 涉及金额：（如有）
- 执法信号：
- 潜在关联风险方：

---

### 🌐 CRS 与国际税务动态

（每条格式如下，共 2–3 条；来源优先：OECD > 国家税务总局 > 四大国际税务报告；若无重要动态，注明"今日无重大 CRS 新动态"）

**[标题]**
来源：[机构名称]
链接：[URL（必填）]
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
gmail_cc       = "zy5733@163.com"

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"]    = gmail_from
msg["To"]      = gmail_to
msg["Cc"]      = gmail_cc
msg.attach(MIMEText(brief_text, "plain", "utf-8"))

all_recipients = [gmail_to, gmail_cc]
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(gmail_from, gmail_password)
    server.sendmail(gmail_from, all_recipients, msg.as_string())

print(f"完成。已发送至 {gmail_to}，抄送 {gmail_cc}，主题：{subject}")

"""
A-share Morning News 2.0

Purpose:
1. Generate a daily/weekly stock research brief for the custom watchlist.
2. Read the watchlist from Morning News/股票观察池.md instead of hardcoding.
3. Output a research-oriented format that can feed dynamic tracking notes.
4. Save a separate "needs update" queue file for manual review.

Environment:
  ANTHROPIC_API_KEY
  ANTHROPIC_BASE_URL (optional)
  GMAIL_FROM
  GMAIL_APP_PASSWORD
  GMAIL_TO
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
from urllib.parse import urljoin, urlparse

import anthropic
import feedparser
import requests
from bs4 import BeautifulSoup


# ── Time ─────────────────────────────────────────────────────────────────────
CST = timezone(timedelta(hours=8))
today = datetime.now(CST)
date_cn = today.strftime("%Y-%m-%d")
weekday = today.weekday()  # 0=Mon … 4=Fri
hour_cst = today.hour
WEEKLY_MODE = weekday == 4 and hour_cst >= 20

subject = (
    f"A股研究周报 — {date_cn}" if WEEKLY_MODE else f"A股每日研究简报 — {date_cn}"
)


# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_FILE = ROOT / "股票观察池.md"
OUTPUT_DIR = ROOT / "Stock Research"
QUEUE_DIR = ROOT / "动态跟踪待更新"


# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_STOCKS = [
    "龙源电力",
    "三峡能源",
    "雅克科技",
    "三七互娱",
    "江苏新能",
    "浙江新能",
    "五矿新能",
    "世纪华通",
]
DEFAULT_SECTORS = ["新能源运营", "游戏", "半导体材料", "宏观与大宗"]
DEFAULT_CODES = {
    "龙源电力": "001289.SZ",
    "三峡能源": "600905.SH",
    "雅克科技": "002409.SZ",
    "三七互娱": "002555.SZ",
    "胜宏科技": "300476.SZ",
    "江苏新能": "603693.SH",
    "浙江新能": "600032.SH",
    "五矿新能": "688779.SH",
    "世纪华通": "002602.SZ",
    "完美世界": "002624.SZ",
    "恺英网络": "002517.SZ",
    "中闽能源": "600163.SH",
}


def parse_stock_entry(item: str) -> tuple[str, str | None]:
    item = item.strip()
    m = re.match(r"^(.*?)（([0-9]{6}\.(?:SH|SZ))）$", item)
    if not m:
        m = re.match(r"^(.*?)\(([0-9]{6}\.(?:SH|SZ))\)$", item)
    if m:
        return m.group(1).strip(), m.group(2).strip().upper()
    return item, DEFAULT_CODES.get(item)


def parse_watchlist(path: Path) -> tuple[list[str], list[str], str, dict[str, str]]:
    """Read a simple markdown watchlist.

    Supported sections:
    ## 核心跟踪
    ## 二级跟踪
    ## 行业主题映射
    """
    if not path.exists():
        return DEFAULT_STOCKS, DEFAULT_SECTORS, "（未找到股票观察池，已使用默认列表）", {
            k: v for k, v in DEFAULT_CODES.items() if k in DEFAULT_STOCKS
        }

    stocks: list[str] = []
    sectors: list[str] = []
    mapping_lines: list[str] = []
    stock_codes: dict[str, str] = {}
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
            stock_name, stock_code = parse_stock_entry(item)
            if stock_name not in stocks:
                stocks.append(stock_name)
            if stock_code:
                stock_codes[stock_name] = stock_code
        elif current_section == "行业主题映射":
            mapping_lines.append(item)
            sector = item.split("：", 1)[0].split(":", 1)[0].strip()
            if sector and sector not in sectors:
                sectors.append(sector)

    if not stocks:
        stocks = DEFAULT_STOCKS[:]
    if not sectors:
        sectors = DEFAULT_SECTORS[:]
    for stock in stocks:
        if stock not in stock_codes and stock in DEFAULT_CODES:
            stock_codes[stock] = DEFAULT_CODES[stock]

    mapping_text = "\n".join(f"- {x}" for x in mapping_lines) if mapping_lines else "（未设置行业主题映射）"
    return stocks, sectors, mapping_text, stock_codes


STOCKS, SECTORS, WATCHLIST_MAPPING, STOCK_CODES = parse_watchlist(WATCHLIST_FILE)


# ── Anthropic client ─────────────────────────────────────────────────────────
client_kwargs = {"api_key": os.environ["ANTHROPIC_API_KEY"]}
base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
if base_url:
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    client_kwargs["base_url"] = base_url
client = anthropic.Anthropic(**client_kwargs)


# ── Scraping helpers ─────────────────────────────────────────────────────────
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


def fetch_html(url: str, label: str, max_items: int = 10) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["nav", "footer", "script", "style", "header", "aside", "form", "button"]):
            tag.decompose()

        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        seen_titles: set[str] = set()
        items: list[tuple[str, str]] = []

        for a in soup.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            href = a["href"].strip()
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
    except Exception as exc:
        print(f"Warning: could not fetch {label} ({url}): {exc}")
        return ""


def fetch_rss(url: str, label: str, max_items: int = 8) -> str:
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            return ""
        lines = [f"\n[{label}]"]
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))[:200].strip()
            lines.append(f"• {title}")
            if link:
                lines.append(f"  URL: {link}")
            if summary and summary != title:
                lines.append(f"  {summary}")
        return "\n".join(lines)
    except Exception as exc:
        print(f"Warning: could not fetch RSS {label}: {exc}")
        return ""


def to_secid(code: str) -> str:
    num, market = code.split(".")
    market_id = "1" if market.upper() == "SH" else "0"
    return f"{market_id}.{num}"


def fetch_price_snapshot(stock_codes: dict[str, str]) -> str:
    if not stock_codes:
        return "（未配置股票代码，无法抓取价格快照）"

    secids = [to_secid(code) for code in stock_codes.values()]
    quote_url = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get"
        "?fltt=2&invt=2&fields=f2,f3,f12,f14,f15,f16,f17,f18&secids="
        + ",".join(secids)
    )
    try:
        resp = requests.get(quote_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        diff = (((data or {}).get("data") or {}).get("diff") or [])
        by_code = {item.get("f12"): item for item in diff if item.get("f12")}
    except Exception as exc:
        print(f"Warning: could not fetch live quote snapshot: {exc}")
        return "（本轮未抓到可靠价格快照）"

    lines = []
    for name in STOCKS:
        code = stock_codes.get(name)
        if not code:
            lines.append(f"- {name}: 未配置代码")
            continue
        item = by_code.get(code.split(".")[0])
        if not item:
            lines.append(f"- {name}（{code}）: 未抓到实时行情")
            continue

        latest = item.get("f2")
        pct = item.get("f3")
        prev_close = item.get("f18")

        trend_5 = "N/A"
        trend_20 = "N/A"
        try:
            kline_url = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid={to_secid(code)}&fields1=f1,f2,f3&fields2=f51,f52&klt=101&fqt=1&lmt=25"
            )
            k_resp = requests.get(kline_url, headers=HEADERS, timeout=15)
            k_resp.raise_for_status()
            k_data = k_resp.json()
            klines = (((k_data or {}).get("data") or {}).get("klines") or [])
            closes = []
            for row in klines:
                parts = row.split(",")
                if len(parts) >= 2:
                    closes.append(float(parts[1]))
            if len(closes) >= 6 and closes[-6] != 0:
                trend_5 = f"{(closes[-1] / closes[-6] - 1) * 100:.2f}%"
            if len(closes) >= 21 and closes[-21] != 0:
                trend_20 = f"{(closes[-1] / closes[-21] - 1) * 100:.2f}%"
        except Exception as exc:
            print(f"Warning: could not fetch kline for {name} ({code}): {exc}")

        lines.append(
            f"- {name}（{code}）：最新价 {latest}，日涨跌 {pct}% ，5日 {trend_5}，20日 {trend_20}，昨收 {prev_close}"
        )

    return "\n".join(lines) if lines else "（本轮未抓到可靠价格快照）"


print(f"Fetching stock news for {date_cn} (weekly={WEEKLY_MODE})...")

SOURCES = [
    ("https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search", "巨潮资讯·公告查询", "html"),
    ("https://data.eastmoney.com/report/", "东方财富·研报", "html"),
    ("https://data.eastmoney.com/notices/", "东方财富·公告", "html"),
    ("https://www.stcn.com/stock/", "证券时报·股票", "html"),
    ("https://finance.sina.com.cn/stock/", "新浪财经·股票", "html"),
    ("http://www.sse.com.cn/disclosure/listedinfo/announcement/", "上交所·公告", "html"),
    ("https://www.szse.cn/disclosure/listed/notice/", "深交所·公告", "html"),
    ("https://www.zqrb.cn/stock/", "证券日报·股票", "html"),
    ("https://finance.yahoo.com/rss/headline?s=^IXIC,^GSPC,NVDA,AAPL,AMD,TSM", "Yahoo Finance·美股科技", "rss"),
    ("https://finance.yahoo.com/rss/headline?s=GC=F,CL=F,SI=F", "Yahoo Finance·贵金属/原油", "rss"),
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch·美股要闻", "rss"),
    ("https://feeds.reuters.com/reuters/businessNews", "Reuters·商业", "rss"),
    ("https://futures.eastmoney.com/", "东方财富·期货", "html"),
]

news_context = ""
for url, label, kind in SOURCES:
    news_context += fetch_rss(url, label) if kind == "rss" else fetch_html(url, label)

if len(news_context) > 24000:
    news_context = news_context[:24000] + "\n...[内容截断]"

if news_context.strip():
    print(f"数据已抓取：{len(news_context)} 字符")
else:
    print("警告：未获取到任何内容，将基于训练知识生成")

price_context = fetch_price_snapshot(STOCK_CODES)
print("价格快照已整理。")


stocks_str = "、".join(STOCKS)
sectors_str = "、".join(SECTORS)

DAILY_PROMPT = f"""今天是 {date_cn}（中国标准时间）。

你是一位面向个人投资研究库的 A 股研究助理。你的目标不是写泛资讯晨报，而是为“已有研究笔记”提供增量判断。

当前股票观察池：{stocks_str}
当前主题行业：{sectors_str}

行业主题映射：
{WATCHLIST_MAPPING}

股票价格快照：
{price_context}

--- 今日抓取数据 ---
{news_context if news_context.strip() else "（今日无实时抓取数据，请基于较新公开知识生成，并明确哪些内容缺少直接来源支撑）"}
--- 数据结束 ---

请严格遵守以下原则：
- 全程使用中文。
- 重点围绕股票观察池，不要被泛市场噪音带偏。
- 只有和观察池公司、对应行业链条、宏观传导直接相关的内容才保留。
- 每条都要写来源名称和链接；如果数据源抓取不到正文，要明确说明“不足以确认”。
- 不要输出空泛评论，要强调“是否改变已有投资逻辑”。
- 优先使用给出的股票价格快照；如果某只股票没有可靠价格数据，不要编造。
- 不写交易指令，不给买卖建议。

请按以下结构输出：

---
A股每日研究简报 — {date_cn}
---

## 今日重点结论
- 3到5条最值得关注的变化

## 个股事件跟踪

只列有信息的公司；没有信息的公司不要硬写。

### [公司名]
- 事件类型：公告 / 行研 / 行业新闻 / 宏观传导 / 股价异动
- 来源：
- 链接：
- 核心摘要：
- 影响判断：正面 / 中性 / 负面
- 是否改变原有投资逻辑：是 / 否 / 待观察
- 原因说明：
- 是否建议写入动态跟踪页：是 / 否

## 行业与宏观传导

### [主题名称]
- 今日变化：
- 影响公司：
- 传导方向：正面 / 中性 / 负面
- 传导逻辑：

## 股价与预期校验

优先使用上面的股票价格快照。没有可靠价格的股票，可以写“本轮未抓到可靠价格快照”。

### [公司名]
- 价格信息：
- 异动原因：
- 与当前研究结论是否一致：一致 / 背离 / 暂无法判断

## 今日需更新的动态跟踪页

请只列真正值得更新的公司，格式如下：
- [[公司名-动态跟踪]]：一句话说明更新原因

## 今日忽略的噪音
- 1到3条今天看起来热闹但对观察池帮助不大的信息

---
"""

WEEKLY_PROMPT = f"""今天是 {date_cn}（周五，中国标准时间）。

你是一位面向个人投资研究库的 A 股研究助理，负责输出一份周度研究回顾，帮助校准股票观察池的判断。

当前股票观察池：{stocks_str}
当前主题行业：{sectors_str}

行业主题映射：
{WATCHLIST_MAPPING}

--- 本周抓取数据 ---
{news_context if news_context.strip() else "（本周无实时抓取数据，请基于较新公开知识生成，并明确哪些内容缺少直接来源支撑）"}
--- 数据结束 ---

要求：
- 全程使用中文。
- 不追求面面俱到，重点是“本周发生了什么变化，以及对观察池的影响”。
- 每条尽量附来源和链接。
- 强调哪些公司 thesis 被强化、削弱，哪些只是噪音。
- 不要写买卖建议。

请按以下结构输出：

---
A股研究周报 — 本周（截至 {date_cn}）
---

## 本周最重要变化
- 3到5条

## 重点行业回顾

### [行业]
- 本周关键变化：
- 影响公司：
- 对投资框架的影响：

## 重点公司回顾

### [公司名]
- 本周关键事件：
- 本周判断：强化 thesis / 削弱 thesis / 基本不变
- 下周最该盯的变量：

## 股价与基本面校验
- 哪些公司本周股价与基本面一致
- 哪些公司出现背离
- 哪些公司因缺乏可靠价格数据暂无法校验

## 下周需要更新的动态跟踪页
- [[公司名-动态跟踪]]：更新原因

---
"""


print("正在生成研究简报...")
prompt = WEEKLY_PROMPT if WEEKLY_MODE else DAILY_PROMPT
response = client.messages.create(
    model="anthropic/claude-sonnet-4.6",
    max_tokens=8000,
    messages=[{"role": "user", "content": prompt}],
)

brief_text = "\n".join(
    block.text for block in response.content if getattr(block, "type", "") == "text"
).strip()

if not brief_text:
    print("Error: 未获取到文本内容", file=sys.stderr)
    sys.exit(1)

print(f"简报已生成（{len(brief_text)} 字符）。正在保存并发送...")

OUTPUT_DIR.mkdir(exist_ok=True)
QUEUE_DIR.mkdir(exist_ok=True)

suffix = "-weekly" if WEEKLY_MODE else ""
md_path = OUTPUT_DIR / f"{date_cn}{suffix}.md"
queue_path = QUEUE_DIR / f"{date_cn}{suffix}.md"

with md_path.open("w", encoding="utf-8") as fh:
    fh.write(f"# {subject}\n\n")
    fh.write(brief_text)
print(f"已保存至 {md_path}")

queue_header = [
    f"# 动态跟踪待更新 - {date_cn}{'（周报）' if WEEKLY_MODE else ''}",
    "",
    "以下内容由 Morning News 2.0 自动生成，用于人工筛选哪些公司需要写入动态跟踪页。",
    "",
    "## 建议更新来源",
    f"- [[Stock Research/{date_cn}{suffix}]]",
    "",
    "## 待处理清单",
    "",
]

queue_body = []
capture = False
for line in brief_text.splitlines():
    if line.strip() == "## 今日需更新的动态跟踪页" or line.strip() == "## 下周需要更新的动态跟踪页":
        capture = True
        continue
    if capture and line.startswith("## "):
        break
    if capture:
        queue_body.append(line)

if not queue_body:
    queue_body = ["- 今日无明确建议更新项"]

with queue_path.open("w", encoding="utf-8") as fh:
    fh.write("\n".join(queue_header + queue_body).rstrip() + "\n")
print(f"已保存待更新队列至 {queue_path}")


gmail_from = os.environ["GMAIL_FROM"]
gmail_password = os.environ["GMAIL_APP_PASSWORD"]
gmail_to = os.environ.get("GMAIL_TO", "jimmy.xu88@icloud.com")

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"] = gmail_from
msg["To"] = gmail_to
msg.attach(MIMEText(brief_text, "plain", "utf-8"))

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(gmail_from, gmail_password)
    server.sendmail(gmail_from, gmail_to, msg.as_string())

print(f"完成。已发送至 {gmail_to}，主题：{subject}")

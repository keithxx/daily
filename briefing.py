#!/usr/bin/env python3
"""
每日股票简报生成器
每天早7点（新加坡时间）调用 Claude API，搜集关注股票的最新消息，生成 HTML 简报并通过 Gmail 发送。
"""

import os
import sys
import json
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic

# ---------- Configuration ----------
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
EMAIL_TO = os.environ["EMAIL_TO"]

STOCKS_FILE = Path(__file__).parent / "stocks.txt"
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 8000
MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds


# ---------- Step 1: Load stock list ----------
def load_stocks():
    """Read stocks.txt — one ticker per line, blank lines and # comments ignored."""
    if not STOCKS_FILE.exists():
        raise FileNotFoundError(f"Cannot find {STOCKS_FILE}")
    tickers = []
    for line in STOCKS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tickers.append(line.upper())
    if not tickers:
        raise ValueError("stocks.txt is empty — add at least one ticker")
    return tickers


# ---------- Step 2: Generate briefing via Claude API ----------
def build_prompt(tickers):
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""你是一位资深的中文财经编辑，为一位关注中美股市的投资者撰写每日晨间简报。今天是 {today}。

需要覆盖的股票代码：{', '.join(tickers)}

请使用 web_search 工具，对每只股票搜索过去24-48小时内的：
1. 最新股价变动（最近一个交易日的收盘价 + 涨跌幅）
2. 重要公司新闻、公告、业绩
3. 分析师评级变化或研报观点
4. 可能影响股价的行业或宏观事件

然后以**严格的 JSON 格式**返回（不要任何 markdown 代码块标记，不要任何解释文字，只返回纯 JSON）：

{{
  "intro": "一段100字以内的市场概览开场白，用温暖但专业的笔调，串联这10只股票背后的当日市场主线",
  "stocks": [
    {{
      "ticker": "股票代码",
      "name_cn": "中文公司名",
      "price": "最新价格（含货币符号）",
      "change": "涨跌幅，例如 +2.35% 或 -1.20%",
      "change_direction": "up 或 down 或 neutral",
      "news": ["要点1：最新新闻或公告（一句话）", "要点2", "要点3"],
      "analyst": "分析师观点或评级变动的简要概述（1-2句话，若无则写'暂无显著分析师动作'）",
      "insight": "编辑的一句话洞察——不是重复数据，而是给读者一个思考角度"
    }}
  ]
}}

重要要求：
- news 字段保持2-4个要点，每个要点一句话，具体、带数字、带事实
- 所有输出用中文
- 如果某只股票搜不到信息，诚实注明
- insight 要有编辑的声音和判断，不要模板化
- JSON 必须能被 json.loads() 直接解析"""


def generate_briefing(tickers):
    """Call Claude API with web search enabled, return parsed JSON."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = build_prompt(tickers)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"[{datetime.now()}] Calling Claude API (attempt {attempt}/{MAX_RETRIES})...")
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 30,
                }],
            )

            # Collect all text blocks from response
            full_text = ""
            for block in response.content:
                if block.type == "text":
                    full_text += block.text + "\n"

            # Strip potential code fences and parse JSON
            cleaned = full_text.replace("```json", "").replace("```", "").strip()
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1:
                raise ValueError(f"No JSON object found in response. Raw:\n{cleaned[:500]}")

            parsed = json.loads(cleaned[start:end + 1])
            print(f"[{datetime.now()}] API succeeded — {len(parsed.get('stocks', []))} stocks processed")
            return parsed

        except (json.JSONDecodeError, ValueError) as e:
            print(f"[{datetime.now()}] Parse error: {e}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_DELAY)
        except anthropic.APIError as e:
            print(f"[{datetime.now()}] API error: {e}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_DELAY)


# ---------- Step 3: Render briefing as HTML email ----------
def render_html(data):
    """Produce a clean HTML email body from the parsed briefing data."""
    today_cn = datetime.now().strftime("%Y年%m月%d日")
    weekday_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][datetime.now().weekday()]

    intro = data.get("intro", "")
    stocks = data.get("stocks", [])

    stocks_html = ""
    for s in stocks:
        direction = s.get("change_direction", "neutral")
        color = {"up": "#7cb86a", "down": "#d47662", "neutral": "#999"}.get(direction, "#999")
        arrow = {"up": "▲", "down": "▼", "neutral": "—"}.get(direction, "—")

        news_items = s.get("news", [])
        news_html = "".join(f'<li style="margin-bottom:6px;color:#555">{item}</li>' for item in news_items) \
            or '<li style="color:#999;font-style:italic">今日暂无显著消息</li>'

        insight_html = ""
        if s.get("insight"):
            insight_html = f'''
            <div style="margin-top:14px;padding:12px 14px;background:#faf6ed;border-left:3px solid #d4a858;border-radius:0 3px 3px 0;">
              <div style="font-size:10px;letter-spacing:0.15em;color:#a88841;text-transform:uppercase;margin-bottom:4px;">编辑手记</div>
              <div style="font-style:italic;color:#3a3a3a;font-size:14px;line-height:1.6;">{s["insight"]}</div>
            </div>
            '''

        stocks_html += f'''
        <article style="background:#fff;border:1px solid #e8e6e0;border-radius:6px;padding:22px 24px;margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;padding-bottom:12px;border-bottom:1px solid #f0eee8;margin-bottom:14px;">
            <div>
              <span style="font-family:'SF Mono',monospace;font-size:17px;font-weight:600;color:#a88841;">{s.get("ticker","")}</span>
              <span style="font-size:16px;color:#2a2a2a;margin-left:10px;">{s.get("name_cn","")}</span>
            </div>
            <div style="text-align:right;font-family:'SF Mono',monospace;font-size:13px;">
              <div style="color:#2a2a2a;font-weight:600;">{s.get("price","—")}</div>
              <div style="color:{color};margin-top:2px;">{arrow} {s.get("change","—")}</div>
            </div>
          </div>

          <div style="font-size:10px;letter-spacing:0.18em;color:#999;text-transform:uppercase;margin-bottom:8px;">今日动态</div>
          <ul style="padding-left:18px;margin:0 0 12px 0;font-size:14px;line-height:1.65;">{news_html}</ul>

          <div style="font-size:10px;letter-spacing:0.18em;color:#999;text-transform:uppercase;margin-bottom:8px;">卖方视角</div>
          <p style="margin:0;color:#555;font-size:14px;line-height:1.65;">{s.get("analyst","—")}</p>

          {insight_html}
        </article>
        '''

    html = f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f5f3ee;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#2a2a2a;">
<div style="max-width:680px;margin:0 auto;padding:32px 20px;">

  <header style="border-bottom:1px solid #d8d4c8;padding-bottom:20px;margin-bottom:28px;">
    <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:12px;">
      <div>
        <h1 style="margin:0;font-size:32px;font-style:italic;color:#1a1a1a;font-weight:500;">晨间简报</h1>
        <span style="font-family:'SF Mono',monospace;font-size:10px;letter-spacing:0.2em;color:#a88841;text-transform:uppercase;">Morning Edition</span>
      </div>
      <div style="text-align:right;font-family:'SF Mono',monospace;font-size:12px;color:#888;">
        <div style="color:#666;">{weekday_cn}</div>
        <div>{today_cn}</div>
      </div>
    </div>
  </header>

  <div style="padding:20px 24px;background:#fff;border-left:3px solid #d4a858;margin-bottom:28px;border-radius:0 4px 4px 0;font-style:italic;font-size:15px;line-height:1.7;color:#444;">
    {intro}
  </div>

  {stocks_html}

  <footer style="margin-top:36px;padding-top:20px;border-top:1px solid #d8d4c8;text-align:center;font-family:'SF Mono',monospace;font-size:10px;letter-spacing:0.12em;color:#999;text-transform:uppercase;">
    Generated by Claude · Not investment advice
  </footer>
</div>
</body>
</html>'''

    return html


# ---------- Step 4: Send via Gmail SMTP ----------
def send_email(html_body, tickers):
    today = datetime.now().strftime("%m月%d日")
    subject = f"晨间简报 · {today} · {len(tickers)}只关注股票"

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    print(f"[{datetime.now()}] Sending email to {EMAIL_TO}...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print(f"[{datetime.now()}] Email sent successfully")


# ---------- Main ----------
def main():
    try:
        tickers = load_stocks()
        print(f"[{datetime.now()}] Loaded {len(tickers)} tickers: {tickers}")

        briefing = generate_briefing(tickers)
        html = render_html(briefing)

        # Also save a local copy for debugging (GitHub Actions will show it in logs)
        debug_path = Path(__file__).parent / "latest_briefing.html"
        debug_path.write_text(html, encoding="utf-8")
        print(f"[{datetime.now()}] HTML saved to {debug_path}")

        send_email(html, tickers)
        print(f"[{datetime.now()}] ✓ Done")

    except Exception as e:
        print(f"[{datetime.now()}] ✗ FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

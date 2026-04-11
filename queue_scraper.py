"""
鼎泰豐排隊號碼爬蟲
==================
每 5 分鐘查詢一次排隊頁面，取得 1~2 人的目前號碼，
號碼有變化時透過 ntfy.sh 推播到手機。

使用方式
--------
1. pip install -r requirements.txt
2. python3 queue_scraper.py
   → 首次執行會顯示 ntfy 頻道名稱，例如：
     [訂閱說明] 手機安裝 ntfy app，訂閱頻道：dintaifung-ab12cd34
3. 手機開啟 ntfy app，搜尋並訂閱該頻道即可收到推播。
4. Ctrl+C 停止腳本。

加上 --debug 參數可印出原始 HTML，方便排查解析問題：
    python3 queue_scraper.py --debug
"""

import re
import sys
import time
import random
import string
import logging
import argparse
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ── 設定區 ────────────────────────────────────────────────────────────────────
SCRAPE_URL = "https://www.dintaifung.tw/queue/?type=3&s=0008&d=1"
INTERVAL   = 300          # 秒（5 分鐘）
NTFY_BASE  = "https://ntfy.sh"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer":         "https://www.dintaifung.tw/",
}
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def _random_topic() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"dintaifung-{suffix}"


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def parse_queue_number(html: str) -> str | None:
    """
    從頁面 HTML 解析 1~2 人的排隊號碼。
    回傳號碼字串，找不到時回傳 None。
    """
    soup = BeautifulSoup(html, "html.parser")

    # 策略 1：尋找含「1」~「2」人字樣的儲存格，取同列 / 兄弟元素中的純數字
    patterns = [
        re.compile(r"1[\s~～\-–—]+2\s*[人位名]"),
        re.compile(r"1\s*[人位名].*?2\s*[人位名]"),
        re.compile(r"[12]\s*[人位名]以?[下內]"),
    ]
    for tag in soup.find_all(["td", "th", "div", "span", "li", "p"]):
        text = tag.get_text(strip=True)
        if any(p.search(text) for p in patterns):
            # 嘗試在同一 <tr> 裡找數字
            parent_tr = tag.find_parent("tr")
            if parent_tr:
                cells = parent_tr.find_all(["td", "th"])
                for cell in cells:
                    cell_text = cell.get_text(strip=True)
                    if re.fullmatch(r"\d+", cell_text):
                        return cell_text

            # 嘗試在兄弟 / 父元素中找純數字
            for sibling in tag.find_next_siblings():
                sibling_text = sibling.get_text(strip=True)
                if re.fullmatch(r"\d+", sibling_text):
                    return sibling_text

            # 嘗試在同一父容器內找純數字
            if tag.parent:
                nums = re.findall(r"\b(\d{1,4})\b", tag.parent.get_text())
                if nums:
                    return nums[0]

    # 策略 2：全頁找帶有「號」「號碼」「現在叫號」等關鍵字旁的數字
    full_text = soup.get_text()
    match = re.search(r"現在叫號[：:\s]*(\d+)", full_text)
    if match:
        return match.group(1)

    return None


def send_ntfy(topic: str, message: str, title: str = "鼎泰豐排隊號碼") -> None:
    try:
        resp = requests.post(
            f"{NTFY_BASE}/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title":    title.encode("utf-8"),
                "Priority": "default",
                "Tags":     "fork_and_knife",
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("推播成功 → ntfy.sh/%s", topic)
    except Exception as exc:
        log.warning("推播失敗：%s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="鼎泰豐排隊號碼爬蟲")
    parser.add_argument("--debug", action="store_true", help="印出原始 HTML 供排查")
    parser.add_argument(
        "--topic",
        default=None,
        help="指定 ntfy.sh 頻道名稱（預設自動生成）",
    )
    args = parser.parse_args()

    topic = args.topic or _random_topic()

    print("=" * 60)
    print("鼎泰豐 1~2 人排隊號碼監控")
    print("=" * 60)
    print(f"[訂閱說明] 手機安裝 ntfy app，訂閱頻道：{topic}")
    print(f"           或直接瀏覽 https://ntfy.sh/{topic}")
    print(f"[監控網址] {SCRAPE_URL}")
    print(f"[更新頻率] 每 {INTERVAL // 60} 分鐘")
    print("Ctrl+C 停止\n")

    last_number: str | None = None
    first_run = True

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        try:
            html = fetch_page(SCRAPE_URL)

            if args.debug:
                print("── 原始 HTML（前 3000 字元）──")
                print(html[:3000])
                print("─" * 40)

            number = parse_queue_number(html)

            if number is None:
                log.warning("[%s] 無法解析到 1~2 人號碼，頁面結構可能已變更", now)
                if first_run:
                    send_ntfy(
                        topic,
                        "⚠️ 無法取得排隊號碼，請手動查看網站。",
                        title="鼎泰豐 — 解析失敗",
                    )
                    first_run = False
            else:
                log.info("[%s] 1~2 人目前號碼：%s", now, number)
                if first_run or number != last_number:
                    msg = f"1~2 人目前號碼：{number}"
                    send_ntfy(topic, msg)
                    last_number = number
                    first_run = False
                else:
                    log.info("號碼未變動，不推播")

        except requests.RequestException as exc:
            log.error("[%s] 網路錯誤：%s", now, exc)
        except Exception as exc:
            log.error("[%s] 未預期錯誤：%s", now, exc)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止監控。")
        sys.exit(0)

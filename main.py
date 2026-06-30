import os
import json
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

REGIONS = ["부산", "양산", "김해"]
KEYWORDS = ["승강기", "엘리베이터"]

SENT_FILE = "sent_notice.json"


def load_sent():
    if not os.path.exists(SENT_FILE):
        return {}
    with open(SENT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_sent(sent_data):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sent_data, f, ensure_ascii=False, indent=2)


def send_telegram(message, link):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    keyboard = {
        "inline_keyboard": [
            [{"text": "🔗 공고 바로가기", "url": link}]
        ]
    }

    response = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": message,
        "reply_markup": json.dumps(keyboard, ensure_ascii=False)
    })

    print("텔레그램 응답:", response.status_code)


def make_message(text):
    lines = text.split("\n")

    notice_id = lines[0].strip() if len(lines) > 0 else ""
    bid_method = lines[2].strip() if len(lines) > 2 else ""
    title = lines[3].strip() if len(lines) > 3 else ""
    deadline = lines[4].strip() if len(lines) > 4 else ""
    status = lines[5].strip() if len(lines) > 5 else ""
    complex_name = lines[6].strip() if len(lines) > 6 else ""
    posted_at = lines[7].strip() if len(lines) > 7 else ""

    return f"""🔔 신규 승강기/엘리베이터 공고

🏢 단지명
{complex_name}

📋 공고명
{title}

📌 입찰방식
{bid_method}

⏰ 마감일
{deadline}

📅 공고일
{posted_at}

📎 상태
{status}

공고번호: {notice_id}
"""


def get_row_link(row):
    default_link = "https://www.k-apt.go.kr/bid/bidList.do"

    try:
        href = row.locator("a").first.get_attribute("href")
        if href:
            if href.startswith("http"):
                return href
            return "https://www.k-apt.go.kr" + href
    except:
        pass

    return default_link


def check_kapt():
    print("K-apt 확인 시작")

    sent_data = load_sent()
    new_sent = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.k-apt.go.kr/bid/bidList.do")
        page.wait_for_timeout(5000)

        rows = page.locator("table tbody tr")
        print(f"공고 {rows.count()}개 확인")

        for i in range(rows.count()):
            row = rows.nth(i)
            text = row.inner_text()

            region_ok = any(region in text for region in REGIONS)
            keyword_ok = any(keyword in text for keyword in KEYWORDS)

            if not (region_ok and keyword_ok):
                continue

            notice_id = text.split("\n")[0].strip()

            if notice_id in sent_data:
                print(f"이미 보낸 공고: {notice_id}")
                continue

            link = get_row_link(row)
            message = make_message(text)

            send_telegram(message, link)

            sent_data[notice_id] = {
                "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "text": text,
                "link": link
            }

            new_sent += 1
            print(f"신규 전송 완료: {notice_id}")

        browser.close()

    save_sent(sent_data)

    if new_sent == 0:
        print("새로 보낼 공고 없음")



send_telegram(
    "✅ GitHub Actions 테스트 성공!",
    "https://www.k-apt.go.kr/bid/bidList.do"
)
check_kapt()

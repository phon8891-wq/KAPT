import os
import json
import requests
from datetime import datetime, timedelta

API_URL = "https://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV2/getPblAncDeSearchV2"

SERVICE_KEY = os.environ["KAPT_SERVICE_KEY"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SENT_FILE = "sent_notice.json"

AREAS = ["부산", "양산", "김해"]
KEYWORDS = ["승강기", "엘리베이터", "elevator", "리프트", "승강"]

NUM_OF_ROWS = 100


def load_sent():
    if not os.path.exists(SENT_FILE):
        return set()

    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception:
        return set()


def save_sent(sent):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent)[-2000:], f, ensure_ascii=False, indent=2)


def normalize_items(items):
    if not items:
        return []

    if isinstance(items, list):
        return items

    if isinstance(items, dict):
        return [items]

    return []


def get_notices():
    today = datetime.now()
    start_date = (today - timedelta(days=14)).strftime("%Y%m%d")
    end_date = (today + timedelta(days=30)).strftime("%Y%m%d")

    params = {
        "serviceKey": SERVICE_KEY,
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": "1",
        "numOfRows": str(NUM_OF_ROWS),
    }

    print("K-apt 공공API 요청 시작")
    print("조회기간:", start_date, "~", end_date)

    response = requests.get(API_URL, params=params, timeout=30)

    print("API 상태코드:", response.status_code)
    print("API 응답 미리보기:", response.text[:700])

    response.raise_for_status()

    data = response.json()

    body = data.get("response", {}).get("body", {})
    items = body.get("items", [])

    if isinstance(items, dict) and "item" in items:
        items = items.get("item")

    return normalize_items(items)


def item_text(item):
    return " ".join(str(v) for v in item.values() if v)


def is_target_notice(item):
    text = item_text(item).lower()

    area_match = any(area in text for area in AREAS)
    keyword_match = any(keyword.lower() in text for keyword in KEYWORDS)

    return area_match and keyword_match


def get_notice_id(item):
    for key in ["bidNo", "bidNum", "pblancNo", "aptCode", "bidTitle"]:
        value = item.get(key)
        if value:
            return str(value)

    return str(abs(hash(item_text(item))))


def get_value(item, keys, default=""):
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return default


def make_message(item):
    title = get_value(item, ["bidTitle", "pblancNm", "title"], "제목 없음")
    apt_name = get_value(item, ["aptNm", "aptName", "kaptName"], "단지명 없음")
    bid_no = get_value(item, ["bidNo", "bidNum", "pblancNo"], "번호 없음")
    bid_date = get_value(item, ["bidDate", "pblancDate", "startDate"], "")
    deadline = get_value(item, ["bidDeadline", "bidCloseDate", "endDate"], "")
    content = get_value(item, ["bidContent", "content"], "")

    if len(content) > 300:
        content = content[:300] + "..."

    return f"""🚨 K-apt 승강기 공고 알림

공고명: {title}
단지명: {apt_name}
공고번호: {bid_no}
공고일: {bid_date}
마감일: {deadline}

내용:
{content}

상세 확인:
https://www.k-apt.go.kr/
"""


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    response = requests.post(url, data=payload, timeout=30)

    print("텔레그램 상태코드:", response.status_code)
    print("텔레그램 응답:", response.text[:500])

    response.raise_for_status()


def main():
    print("===================================")
    print("K-apt 승강기 공고 알림 시작")
    print("===================================")

    sent = load_sent()
    notices = get_notices()

    print("가져온 공고 수:", len(notices))

    new_alert_count = 0
    checked_count = 0

    for item in notices:
        checked_count += 1

        notice_id = get_notice_id(item)

        if notice_id in sent:
            continue

        text = item_text(item)

        print("-----------------------------------")
        print("새 공고 확인:", notice_id)
        print(text[:500])

        if is_target_notice(item):
            print("대상 공고 발견. 텔레그램 발송.")
            message = make_message(item)
            send_telegram(message)
            new_alert_count += 1
        else:
            print("조건 불일치. 알림 제외.")

        sent.add(notice_id)

    save_sent(sent)

    print("===================================")
    print("검토한 공고 수:", checked_count)
    print("새 알림 수:", new_alert_count)
    print("완료")
    print("===================================")


if __name__ == "__main__":
    main()

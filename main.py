import os
import json
import requests
from datetime import datetime, timedelta

BASE_URL = "https://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV2"

DATE_API = f"{BASE_URL}/getPblAncDeSearchV2"
NAME_API = f"{BASE_URL}/getBidPblAncNmSearchV2"

SERVICE_KEY = os.environ["KAPT_SERVICE_KEY"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SENT_FILE = "sent_notice.json"

AREAS = ["부산", "양산", "김해"]
KEYWORDS = ["승강기", "엘리베이터", "리프트", "승강"]
NUM_OF_ROWS = 500


def load_sent():
    if not os.path.exists(SENT_FILE):
        return set()
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_sent(sent):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent)[-3000:], f, ensure_ascii=False, indent=2)


def normalize_items(items):
    if not items:
        return []
    if isinstance(items, list):
        return items
    if isinstance(items, dict):
        if "item" in items:
            return normalize_items(items["item"])
        return [items]
    return []


def parse_items(data):
    body = data.get("response", {}).get("body", {})
    return normalize_items(body.get("items", []))


def request_api(url, params, label):
    print(f"{label} 요청")
    res = requests.get(url, params=params, timeout=30)
    print(f"{label} 상태코드:", res.status_code)
    print(f"{label} 응답 미리보기:", res.text[:500])
    res.raise_for_status()
    return parse_items(res.json())


def get_date_notices():
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

    print("조회기간:", start_date, "~", end_date)
    return request_api(DATE_API, params, "공고일 조회 API")


def get_keyword_notices(keyword):
    candidate_params = [
        {"bidTitle": keyword},
        {"bidPblancNm": keyword},
        {"pblancNm": keyword},
        {"bidNm": keyword},
        {"searchKeyword": keyword},
    ]

    for extra in candidate_params:
        params = {
            "serviceKey": SERVICE_KEY,
            "pageNo": "1",
            "numOfRows": str(NUM_OF_ROWS),
            **extra,
        }

        try:
            items = request_api(NAME_API, params, f"공고명 검색 API({keyword}/{list(extra.keys())[0]})")
            if items:
                return items
        except Exception as e:
            print("공고명 검색 실패:", keyword, extra, e)

    return []


def get_all_notices():
    all_items = []

    all_items.extend(get_date_notices())

    for keyword in KEYWORDS:
        all_items.extend(get_keyword_notices(keyword))

    unique = {}
    for item in all_items:
        nid = get_notice_id(item)
        unique[nid] = item

    return list(unique.values())


def item_text(item):
    return " ".join(str(v) for v in item.values() if v)


def is_target_notice(item):
    text = item_text(item).lower()
    area_match = any(area in text for area in AREAS)
    keyword_match = any(keyword.lower() in text for keyword in KEYWORDS)
    return area_match and keyword_match


def get_notice_id(item):
    for key in ["bidNum", "bidNo", "pblancNo", "bidTitle", "aptCode"]:
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
    apt_name = get_value(item, ["bidKaptname", "aptNm", "aptName", "kaptName"], "단지명 없음")
    bid_no = get_value(item, ["bidNum", "bidNo", "pblancNo"], "번호 없음")
    area = get_value(item, ["bidArea", "addr", "address"], "")
    bid_date = get_value(item, ["bidRegDate", "bidDate", "pblancDate"], "")
    deadline = get_value(item, ["bidDeadline", "bidCloseDate", "endDate"], "")
    content = get_value(item, ["bidContent", "content"], "")

    if len(content) > 300:
        content = content[:300] + "..."

    return f"""🚨 K-apt 승강기 공고 알림

공고명: {title}
단지명: {apt_name}
지역: {area}
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
    res = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    print("텔레그램 상태코드:", res.status_code)
    print("텔레그램 응답:", res.text[:500])
    res.raise_for_status()


def main():
    print("===================================")
    print("K-apt 승강기 공고 알림 시작")
    print("===================================")

    sent = load_sent()
    notices = get_all_notices()

    print("전체 가져온 공고 수:", len(notices))

    new_alert_count = 0

    for item in notices:
        notice_id = get_notice_id(item)

        if notice_id in sent:
            continue

        print("-----------------------------------")
        print("새 공고 확인:", notice_id)
        print(item_text(item)[:700])

        if is_target_notice(item):
            print("대상 공고 발견. 텔레그램 발송.")
            send_telegram(make_message(item))
            new_alert_count += 1
        else:
            print("조건 불일치. 알림 제외.")

        sent.add(notice_id)

    save_sent(sent)

    print("===================================")
    print("새 알림 수:", new_alert_count)
    print("완료")
    print("===================================")


if __name__ == "__main__":
    main()

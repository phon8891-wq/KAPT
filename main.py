import os
import json
import requests
from datetime import datetime, timedelta
from urllib.parse import urlencode

BASE_URL = "https://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV2"

DATE_API = f"{BASE_URL}/getPblAncDeSearchV2"
NAME_API = f"{BASE_URL}/getBidPblAncNmSearchV2"

SERVICE_KEY = os.environ["KAPT_SERVICE_KEY"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SENT_FILE = "sent_notice.json"

AREAS = ["부산", "양산", "김해"]
AREA_CODES = ["26", "48"]
KEYWORDS = ["승강기", "엘리베이터", "리프트", "승강"]
SEARCH_KEYWORDS = ["승강기", "승강기교체", "엘리베이터"]

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
    print(f"{label} 미리보기:", res.text[:500])
    res.raise_for_status()
    return parse_items(res.json())


def get_date_range():
    today = datetime.now()
    start_date = (today - timedelta(days=45)).strftime("%Y%m%d")
    end_date = (today + timedelta(days=60)).strftime("%Y%m%d")
    return start_date, end_date


def get_date_notices():
    start_date, end_date = get_date_range()

    params = {
        "serviceKey": SERVICE_KEY,
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": "1",
        "numOfRows": str(NUM_OF_ROWS),
    }

    print("공고일 조회기간:", start_date, "~", end_date)
    return request_api(DATE_API, params, "공고일 조회 API")


def get_name_notices(keyword):
    start_date, end_date = get_date_range()

    params = {
        "serviceKey": SERVICE_KEY,
        "bidTitle": keyword,
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": "1",
        "numOfRows": str(NUM_OF_ROWS),
    }

    return request_api(NAME_API, params, f"공고명 검색 API: {keyword}")


def item_text(item):
    return " ".join(str(v) for v in item.values() if v)


def get_notice_id(item):
    for key in ["bidNum", "bidNo", "pblancNo"]:
        if item.get(key):
            return str(item.get(key))
    return str(abs(hash(item_text(item))))


def get_all_notices():
    all_items = []

    all_items.extend(get_date_notices())

    for keyword in SEARCH_KEYWORDS:
        try:
            all_items.extend(get_name_notices(keyword))
        except Exception as e:
            print("공고명 검색 실패:", keyword, e)

    unique = {}
    for item in all_items:
        unique[get_notice_id(item)] = item

    return list(unique.values())


def parse_date(value):
    if not value:
        return None

    text = str(value).strip()[:10]

    for fmt in ["%Y-%m-%d", "%Y%m%d"]:
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass

    return None


def is_current_notice(item):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    reg_date = parse_date(item.get("bidRegDate") or item.get("bidDate"))
    deadline = parse_date(item.get("bidDeadline") or item.get("bidCloseDate"))

    if deadline and deadline < today:
        return False

    if reg_date and reg_date < today - timedelta(days=60):
        return False

    return True


def is_target_notice(item):
    text = item_text(item).lower()
    bid_area = str(item.get("bidArea", ""))

    area_match = any(area in text for area in AREAS)
    area_code_match = bid_area in AREA_CODES
    keyword_match = any(keyword.lower() in text for keyword in KEYWORDS)

    return (area_match or area_code_match) and keyword_match and is_current_notice(item)


def get_value(item, keys, default=""):
    for key in keys:
        if item.get(key):
            return str(item.get(key))
    return default


def get_area_name(item):
    text = item_text(item)
    bid_area = str(item.get("bidArea", ""))

    if "부산" in text or bid_area == "26":
        return "부산"
    if "양산" in text:
        return "양산"
    if "김해" in text:
        return "김해"
    if bid_area == "48":
        return "경남"

    return "지역 미확인"


def make_detail_url(item):
    title = get_value(item, ["bidTitle"], "")
    bid_num = get_value(item, ["bidNum", "bidNo"], "")
    apt_name = get_value(item, ["bidKaptname", "aptNm", "aptName"], "")
    apt_code = get_value(item, ["aptCode"], "")
    bid_area = get_value(item, ["bidArea"], "")

    today = datetime.now()
    date_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    date_end = today.strftime("%Y-%m-%d")

    params = {
        "pageSelect": "10",
        "searchBidGb": "bid_gb_1",
        "bidTitle": title,
        "aptName": apt_name,
        "searchDateGb": "reg",
        "dateStart": date_start,
        "dateEnd": date_end,
        "dateArea": "1",
        "bidState": "",
        "codeAuth": "",
        "codeWay": "",
        "codeAuthSub": "",
        "codeSucWay": "",
        "codeClassifyType1": "",
        "codeClassifyType2": "",
        "codeClassifyType3": "",
        "pageNo": "1",
        "type": "4",
        "bidArea": bid_area,
        "bidNum": bid_num,
        "bidNo": "",
        "dTime": str(int(datetime.now().timestamp() * 1000)),
        "mainKaptCode": "",
        "aptCode": apt_code,
    }

    return "https://www.k-apt.go.kr/bid/bidDetail.do?" + urlencode(params)


def make_message(item):
    title = get_value(item, ["bidTitle"], "제목 없음")
    apt_name = get_value(item, ["bidKaptname", "aptNm", "aptName"], "단지명 없음")
    bid_no = get_value(item, ["bidNum", "bidNo"], "번호 없음")
    area = get_area_name(item)
    status = get_value(item, ["bidState", "bidStatus"], "")
    bid_date = get_value(item, ["bidRegDate", "bidDate"], "")
    deadline = get_value(item, ["bidDeadline", "bidCloseDate"], "")
    content = get_value(item, ["bidContent"], "")
    detail_url = make_detail_url(item)

    if len(content) > 250:
        content = content[:250] + "..."

    return f"""🛗 K-apt 승강기 공고 알림

📍 지역: {area}
🏢 단지: {apt_name}
📌 공고명: {title}
📄 공고번호: {bid_no}
📅 공고일: {bid_date}
⏰ 마감일: {deadline}
상태: {status}

내용:
{content}

🔗 상세보기:
{detail_url}
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
    print("텔레그램 응답:", res.text[:300])
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
        text = item_text(item)

        print("-----------------------------------")
        print("공고 확인:", notice_id)
        print(text[:700])

        if notice_id in sent:
            print("이미 알림 보낸 공고. 제외.")
            continue

        if is_target_notice(item):
            print("대상 공고 발견. 텔레그램 발송.")
            send_telegram(make_message(item))
            sent.add(notice_id)
            new_alert_count += 1
        else:
            print("조건 불일치 또는 과거 공고. 알림 제외.")

    save_sent(sent)

    print("===================================")
    print("새 알림 수:", new_alert_count)
    print("완료")
    print("===================================")


if __name__ == "__main__":
    main()

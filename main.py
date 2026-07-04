import os, json, requests
from datetime import datetime, timedelta
from urllib.parse import quote

SERVICE_KEY = os.environ["KAPT_SERVICE_KEY"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SENT_FILE = "sent_notice.json"

KEYWORDS = ["승강기", "엘리베이터", "elevator", "리프트"]
AREAS = ["부산", "양산", "김해"]

API_URL = "http://apis.data.go.kr/1613000/AptBidInfoService/getBidInfo"

def load_sent():
    if not os.path.exists(SENT_FILE):
        return set()
    with open(SENT_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))

def save_sent(sent):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent)[-1000:], f, ensure_ascii=False, indent=2)

def get_notices():
    today = datetime.now()
    start = (today - timedelta(days=7)).strftime("%Y%m%d")
    end = (today + timedelta(days=30)).strftime("%Y%m%d")

    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": 1,
        "numOfRows": 100,
        "type": "json",
        "bidStartDate": start,
        "bidEndDate": end,
    }

    r = requests.get(API_URL, params=params, timeout=20)
    print("API status:", r.status_code)
    print("API preview:", r.text[:500])
    r.raise_for_status()

    data = r.json()
    body = data.get("response", {}).get("body", {})
    items = body.get("items", {}).get("item", [])

    if isinstance(items, dict):
        items = [items]

    return items

def text_of(item):
    return " ".join(str(v) for v in item.values() if v)

def is_target(item):
    text = text_of(item).lower()
    has_keyword = any(k.lower() in text for k in KEYWORDS)
    has_area = any(a in text for a in AREAS)
    return has_keyword and has_area

def notice_id(item):
    for key in ["bidNo", "bidNum", "bidTitle", "id"]:
        if item.get(key):
            return str(item.get(key))
    return str(hash(text_of(item)))

def send_telegram(item):
    title = item.get("bidTitle") or item.get("title") or "K-apt 공고"
    apt = item.get("aptName") or item.get("kaptName") or ""
    area = item.get("bidArea") or item.get("addr") or ""
    date = item.get("bidEndDate") or item.get("endDate") or ""

    msg = f"""🚨 K-apt 승강기 공고 알림

공고명: {title}
단지명: {apt}
지역: {area}
마감일: {date}

K-apt에서 상세 확인하세요.
https://www.k-apt.go.kr/
"""

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    res = requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=20)
    print("Telegram:", res.status_code, res.text[:300])
    res.raise_for_status()

def main():
    print("K-apt 공공API 확인 시작")

    sent = load_sent()
    notices = get_notices()

    print("가져온 공고 수:", len(notices))

    new_count = 0

    for item in notices:
        nid = notice_id(item)

        if nid in sent:
            continue

        if is_target(item):
            print("대상 공고 발견:", item)
            send_telegram(item)
            new_count += 1

        sent.add(nid)

    save_sent(sent)
    print("새 알림 수:", new_count)
    print("완료")

if __name__ == "__main__":
    main()

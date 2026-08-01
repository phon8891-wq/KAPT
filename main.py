#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
K-APT 부산·경남 승강기 입찰공고 텔레그램 알림 봇

- Render Background Worker용
- 5분마다 자동 조회
- 부산(26), 경남(48) 승강기 관련 공고만 알림
- 같은 공고는 한 번만 발송
- 첫 실행 또는 저장기록 초기화 시 기존 공고는 발송하지 않고 기준값으로만 저장
- 신규 공고가 없을 때는 텔레그램 메시지를 보내지 않음
"""

import os
import sys
import json
import time
import html
import datetime
import traceback
import urllib.parse
import urllib.request

SERVICE_KEY = os.environ.get("KAPT_SERVICE_KEY", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

API_URL = (
    "https://apis.data.go.kr/1613000/"
    "ApHusBidPblAncInfoOfferServiceV2/getPblAncDeSearchV2"
)

TARGET_AREAS = {26, 48}
ELEVATOR_TYPE3 = {"06"}
ELEVATOR_KEYWORDS = ["승강기", "엘리베이터", "엘레베이터", "리프트", "elev"]
LOOKBACK_DAYS = 3
NUM_OF_ROWS = 100
CHECK_INTERVAL_SECONDS = 300
MAX_RETRIES = 3
MAX_SEEN_ITEMS = 5000

PERSISTENT_DIR = "/var/data"
if os.path.isdir(PERSISTENT_DIR) and os.access(PERSISTENT_DIR, os.W_OK):
    DATA_DIR = PERSISTENT_DIR
else:
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(DATA_DIR, "seen.json")

AREA_NAME = {26: "부산", 48: "경남"}
STATE_NAME = {
    "1": "신규공고",
    "2": "수정공고",
    "3": "재공고",
    "4": "유찰",
    "5": "낙찰(계약완료)",
    "6": "취소",
    "8": "낙찰(계약진행)",
    "9": "낙찰무효",
    "10": "계약취소",
    "99": "낙찰취소 후 신규공고",
}


def log(message):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set(), False
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, list):
            return set(), False
        return {str(v).strip() for v in data if str(v).strip()}, True
    except Exception as error:
        log(f"중복 기록 불러오기 실패: {error}")
        return set(), False


def save_seen(seen):
    os.makedirs(DATA_DIR, exist_ok=True)
    values = sorted({str(v).strip() for v in seen if str(v).strip()})[-MAX_SEEN_ITEMS:]
    temp_file = SEEN_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(values, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_file, SEEN_FILE)


def fetch_page(start_date, end_date, page_no):
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": page_no,
        "numOfRows": NUM_OF_ROWS,
        "type": "json",
    }
    query_string = "serviceKey=" + SERVICE_KEY + "&" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        API_URL + "?" + query_string,
        headers={"User-Agent": "kapt-elevator-bot/2.0", "Accept": "application/json"},
    )
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            last_error = error
            log(f"API 요청 실패 ({attempt}/{MAX_RETRIES}): {error}")
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 3)
    raise RuntimeError(f"K-APT API 요청 최종 실패: {last_error}")


def normalize_page_items(items):
    if not items:
        return []
    if isinstance(items, list):
        return items
    if isinstance(items, dict):
        if "item" in items:
            item_data = items["item"]
            if isinstance(item_data, list):
                return item_data
            if isinstance(item_data, dict):
                return [item_data]
            return []
        return [items]
    return []


def fetch_all(start_date, end_date):
    all_items = []
    page_no = 1
    while True:
        data = fetch_page(start_date, end_date, page_no)
        response = data.get("response", {})
        header = response.get("header", {})
        body = response.get("body", {})
        result_code = str(header.get("resultCode", "")).strip()
        if result_code and result_code not in {"00", "0"}:
            raise RuntimeError(
                f"K-APT API 오류: {result_code} / {header.get('resultMsg', '알 수 없는 오류')}"
            )
        try:
            total_count = int(body.get("totalCount", 0) or 0)
        except (TypeError, ValueError):
            total_count = 0
        page_items = normalize_page_items(body.get("items", []))
        if not page_items:
            break
        all_items.extend(page_items)
        log(f"페이지 {page_no}: {len(page_items)}건 / 누적 {len(all_items)}건 / 전체 {total_count}건")
        if total_count and len(all_items) >= total_count:
            break
        if len(page_items) < NUM_OF_ROWS:
            break
        page_no += 1
        time.sleep(0.3)
    return all_items


def get_area_code(item):
    try:
        return int(item.get("bidArea"))
    except (TypeError, ValueError):
        return None


def is_target_area(item):
    return get_area_code(item) in TARGET_AREAS


def is_elevator(item):
    classify_type3 = str(item.get("codeClassifyType3") or "").strip()
    if classify_type3 in ELEVATOR_TYPE3:
        return True
    title = str(item.get("bidTitle") or "").lower()
    return any(keyword.lower() in title for keyword in ELEVATOR_KEYWORDS)


def get_notice_id(item):
    for key in ["bidNum", "bidNo", "pblancNo"]:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def unique_target_items(all_items):
    unique_items = {}
    for item in all_items:
        if not is_target_area(item) or not is_elevator(item):
            continue
        notice_id = get_notice_id(item)
        if notice_id:
            unique_items[notice_id] = item
    return list(unique_items.values())


def build_detail_url(item):
    return "https://www.k-apt.go.kr/bid/bidDetail.do?bidNum=" + urllib.parse.quote(get_notice_id(item))


def build_message(item):
    area_name = AREA_NAME.get(get_area_code(item), "지역 미확인")
    state_code = str(item.get("bidState") or "").strip()
    state_name = STATE_NAME.get(state_code, state_code or "상태 미확인")
    title = html.escape(str(item.get("bidTitle") or "제목 없음"))
    apartment_name = html.escape(str(item.get("bidKaptname") or "단지명 없음"))
    registration_date = html.escape(str(item.get("bidRegDate") or "미확인"))
    deadline = html.escape(str(item.get("bidDeadline") or "미확인"))
    bid_num = html.escape(get_notice_id(item) or "번호 없음")
    emergency = " ⚠️ 긴급공고" if str(item.get("bidEmrgYn") or "").upper() == "Y" else ""
    detail_url = html.escape(build_detail_url(item), quote=True)
    return "\n".join([
        "🛗 <b>K-APT 승강기 공고 알림</b>",
        "",
        f"📍 지역: <b>{area_name}</b>",
        f"🏢 단지: {apartment_name}",
        f"📌 공고명: {title}{emergency}",
        f"📄 공고번호: {bid_num}",
        f"📋 상태: {html.escape(state_name)}",
        f"📅 공고일: {registration_date}",
        f"⏰ 마감일: {deadline}",
        "",
        f'<a href="{detail_url}">🔗 K-APT 상세보기</a>',
    ])


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"User-Agent": "kapt-elevator-bot/2.0"})
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(f"텔레그램 응답 오류: {result}")
            return
        except Exception as error:
            last_error = error
            log(f"텔레그램 발송 실패 ({attempt}/{MAX_RETRIES}): {error}")
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 3)
    raise RuntimeError(f"텔레그램 발송 최종 실패: {last_error}")


def run_once():
    today = datetime.date.today()
    start_date = (today - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    log("-----------------------------------")
    log(f"조회 기간: {start_date} ~ {end_date}")
    seen, seen_file_exists = load_seen()
    log(f"중복 기록 파일: {SEEN_FILE}")
    log(f"기존 알림 기록: {len(seen)}건")
    all_items = fetch_all(start_date, end_date)
    target_items = unique_target_items(all_items)
    log(f"부산·경남 승강기 공고: {len(target_items)}건")
    current_ids = {get_notice_id(item) for item in target_items if get_notice_id(item)}

    # 저장기록이 없으면 현재 공고는 보내지 않고 기준값으로만 저장한다.
    # 재시작 시 과거 공고가 다시 울리는 것을 막는 핵심 처리다.
    if not seen_file_exists:
        save_seen(current_ids)
        log(f"최초 실행/기록 초기화 감지: 현재 공고 {len(current_ids)}건을 기준값으로 저장")
        log("기존 공고는 발송하지 않으며 다음 조회부터 새 공고만 알림")
        return

    new_items = [item for item in target_items if get_notice_id(item) not in seen]
    new_items.sort(key=lambda item: (str(item.get("bidRegDate") or ""), get_notice_id(item)))
    log(f"신규 미발송 공고: {len(new_items)}건")
    if not new_items:
        return

    for item in new_items:
        notice_id = get_notice_id(item)
        title = str(item.get("bidTitle") or "제목 없음")
        if notice_id in seen:
            continue
        send_telegram(build_message(item))
        seen.add(notice_id)
        save_seen(seen)
        log(f"발송 완료: {title} / {notice_id}")
        time.sleep(1)


def validate_environment():
    missing = []
    if not SERVICE_KEY:
        missing.append("KAPT_SERVICE_KEY")
    if not TG_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TG_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log("환경변수가 비어 있습니다: " + ", ".join(missing))
        sys.exit(1)


def main():
    validate_environment()
    log("===================================")
    log("K-APT 부산·경남 승강기 알림 봇 시작")
    log("Render Background Worker 방식")
    log(f"조회 주기: {CHECK_INTERVAL_SECONDS // 60}분")
    log("===================================")

    while True:
        cycle_started = time.time()
        try:
            run_once()
        except KeyboardInterrupt:
            log("프로그램을 종료합니다.")
            break
        except Exception as error:
            log(f"이번 조회 중 오류 발생: {error}")
            traceback.print_exc()

        elapsed = time.time() - cycle_started
        sleep_seconds = max(1, CHECK_INTERVAL_SECONDS - elapsed)
        log(f"다음 조회까지 {int(sleep_seconds)}초 대기")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()

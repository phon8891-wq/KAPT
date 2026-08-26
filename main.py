#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KAPT API FIX V7
부산·경남 승강기 입찰공고 텔레그램 알림
GitHub Actions 전용

- WEB 크롤링 방식 사용 안 함
- 공공데이터 API만 사용
- 여러 요청 호환 조합 자동 시도
- 부산(26), 경남(48) 승강기 관련 공고만 알림
- sent_notice.json 중복 방지
"""

import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.parse
import urllib.error
import html
import socket

VERSION = "KAPT API FIX V7"

SERVICE_KEY = os.environ.get("KAPT_SERVICE_KEY", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

BASE_URLS = [
    "https://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV2/getPblAncDeSearchV2",
    "http://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV2/getPblAncDeSearchV2",
]

TARGET_AREAS = {26, 48}
ELEVATOR_TYPE3 = {"06"}
ELEVATOR_KEYWORDS = ["승강기", "엘리베이터", "엘레베이터", "리프트", "elevator", "elev"]

LOOKBACK_DAYS = 3
NUM_OF_ROWS = 100
MAX_SENT_ITEMS = 5000
REQUEST_TIMEOUT = 15

SENT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "sent_notice.json",
)

AREA_NAME = {
    11: "서울", 26: "부산", 27: "대구", 28: "인천", 29: "광주",
    30: "대전", 31: "울산", 36: "세종", 41: "경기", 42: "강원",
    43: "충북", 44: "충남", 45: "전북", 46: "전남", 47: "경북",
    48: "경남", 50: "제주",
}

STATE_NAME = {
    "1": "신규공고", "2": "수정공고", "3": "재공고", "4": "유찰",
    "5": "낙찰(계약완료)", "6": "취소", "8": "낙찰(계약진행)",
    "9": "낙찰무효", "10": "계약취소", "99": "낙찰취소 후 신규공고",
}


def log(message):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


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


def load_sent():
    if not os.path.exists(SENT_FILE):
        return set(), False

    try:
        with open(SENT_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            return set(), False

        return {
            str(value).strip()
            for value in data
            if str(value).strip()
        }, True

    except Exception as error:
        log(f"sent_notice.json 불러오기 실패: {error}")
        return set(), False


def save_sent(sent):
    values = sorted({
        str(value).strip()
        for value in sent
        if str(value).strip()
    })[-MAX_SENT_ITEMS:]

    temp_file = SENT_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(values, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())

    os.replace(temp_file, SENT_FILE)


def key_candidates():
    raw = SERVICE_KEY
    decoded = urllib.parse.unquote(raw)

    values = []

    for value in (raw, decoded):
        if value and value not in values:
            values.append(value)

    return values


def build_url(base_url, service_key, start_date, end_date, page_no, type_name):
    params = {
        "serviceKey": service_key,
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": str(page_no),
        "numOfRows": str(NUM_OF_ROWS),
        type_name: "json",
    }

    return base_url + "?" + urllib.parse.urlencode(params)


def parse_response(raw):
    text = raw.strip()

    if not text:
        raise RuntimeError("빈 응답")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(
            "JSON이 아닌 응답: "
            + text[:500].replace("\n", " ")
        )

    response = data.get("response", {})
    header = response.get("header", {})

    result_code = str(header.get("resultCode", "")).strip()
    result_msg = str(header.get("resultMsg", "")).strip()

    if result_code and result_code not in {"00", "0"}:
        raise RuntimeError(
            f"API 오류 {result_code}: {result_msg or '메시지 없음'}"
        )

    return data


def request_url(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Connection": "close",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")

        return parse_response(raw)

    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""

        body = body[:500].replace("\n", " ")

        raise RuntimeError(
            f"HTTP {error.code} {error.reason}"
            + (f" / {body}" if body else "")
        )

    except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
        raise RuntimeError(f"접속 오류: {error}")


def fetch_page(start_date, end_date, page_no):
    date_pairs = [
        (start_date, end_date),
        (start_date.replace("-", ""), end_date.replace("-", "")),
    ]

    type_names = ["type", "_type"]

    errors = []
    attempt = 0

    for base_url in BASE_URLS:
        protocol = "HTTPS" if base_url.startswith("https://") else "HTTP"

        for service_key in key_candidates():
            for type_name in type_names:
                for query_start, query_end in date_pairs:
                    attempt += 1

                    log(
                        f"{VERSION} 요청 #{attempt}: "
                        f"{protocol}, {type_name}=json, "
                        f"{query_start}~{query_end}, page={page_no}"
                    )

                    url = build_url(
                        base_url,
                        service_key,
                        query_start,
                        query_end,
                        page_no,
                        type_name,
                    )

                    try:
                        data = request_url(url)
                        log(f"{VERSION} 요청 #{attempt} 성공")
                        return data

                    except Exception as error:
                        message = str(error)
                        errors.append(f"#{attempt} {message}")
                        log(f"{VERSION} 요청 #{attempt} 실패: {message}")

                    time.sleep(0.5)

    raise RuntimeError(
        "K-APT API 모든 호환 요청 실패 / "
        + " | ".join(errors[-8:])
    )


def normalize_items(items):
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
        body = response.get("body", {})

        try:
            total_count = int(body.get("totalCount", 0) or 0)
        except (TypeError, ValueError):
            total_count = 0

        page_items = normalize_items(body.get("items", []))

        if not page_items:
            break

        all_items.extend(page_items)

        log(
            f"페이지 {page_no}: {len(page_items)}건 / "
            f"누적 {len(all_items)}건 / 전체 {total_count}건"
        )

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
    unique = {}

    for item in all_items:
        if not is_target_area(item):
            continue

        if not is_elevator(item):
            continue

        notice_id = get_notice_id(item)

        if notice_id:
            unique[notice_id] = item

    return list(unique.values())


def get_area_name(item):
    area_code = get_area_code(item)

    if area_code is None:
        return "지역 미확인"

    return AREA_NAME.get(area_code, str(area_code))


def build_detail_url(item):
    return (
        "https://www.k-apt.go.kr/bid/bidDetail.do?bidNum="
        + urllib.parse.quote(get_notice_id(item))
    )


def build_message(item):
    state_code = str(item.get("bidState") or "").strip()

    state_name = STATE_NAME.get(
        state_code,
        state_code if state_code else "상태 미확인",
    )

    title = html.escape(str(item.get("bidTitle") or "제목 없음"))
    apartment_name = html.escape(str(item.get("bidKaptname") or "단지명 없음"))
    registration_date = html.escape(str(item.get("bidRegDate") or "미확인"))
    deadline = html.escape(str(item.get("bidDeadline") or "미확인"))
    bid_num = html.escape(get_notice_id(item) or "번호 없음")

    emergency = (
        " ⚠️ 긴급공고"
        if str(item.get("bidEmrgYn") or "").upper() == "Y"
        else ""
    )

    detail_url = html.escape(build_detail_url(item), quote=True)

    return "\n".join([
        "🛗 <b>K-APT 승강기 공고 알림</b>",
        "",
        f"📍 지역: <b>{get_area_name(item)}</b>",
        f"🏢 단지: {apartment_name}",
        f"📌 공고명: {title}{emergency}",
        f"📄 공고번호: {bid_num}",
        f"📋 상태: {html.escape(state_name)}",
        f"📅 공고일: {registration_date}",
        f"⏰ 마감일: {deadline}",
        "",
        f'🔗 <a href="{detail_url}">K-APT 상세보기</a>',
    ])


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    payload = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={"User-Agent": "kapt-api-fix-v7"},
    )

    last_error = None

    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.loads(
                    response.read().decode("utf-8")
                )

            if not result.get("ok"):
                raise RuntimeError(f"텔레그램 오류: {result}")

            return

        except Exception as error:
            last_error = error
            log(f"텔레그램 발송 실패 ({attempt}/3): {error}")

            if attempt < 3:
                time.sleep(attempt * 2)

    raise RuntimeError(f"텔레그램 발송 최종 실패: {last_error}")


def main():
    validate_environment()

    today = datetime.date.today()
    start_date = (
        today - datetime.timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    log("========================================")
    log(VERSION)
    log("WEB 크롤링 사용 안 함 / 공공데이터 API 모드")
    log(f"조회 기간: {start_date} ~ {end_date}")
    log("========================================")

    sent, sent_file_exists = load_sent()

    all_items = fetch_all(start_date, end_date)
    target_items = unique_target_items(all_items)

    current_ids = {
        get_notice_id(item)
        for item in target_items
        if get_notice_id(item)
    }

    log(f"전체 조회 공고: {len(all_items)}건")
    log(f"부산·경남 승강기 공고: {len(target_items)}건")
    log(f"기존 발송 기록: {len(sent)}건")

    if not sent_file_exists:
        save_sent(current_ids)
        log(f"최초 실행: 현재 {len(current_ids)}건을 기준값으로 저장")
        log("기존 공고는 발송하지 않음")
        return

    new_items = [
        item
        for item in target_items
        if get_notice_id(item) not in sent
    ]

    new_items.sort(
        key=lambda item: (
            str(item.get("bidRegDate") or ""),
            get_notice_id(item),
        )
    )

    log(f"신규 미발송 공고: {len(new_items)}건")

    sent_count = 0

    for item in new_items:
        notice_id = get_notice_id(item)
        title = str(item.get("bidTitle") or "제목 없음")

        try:
            send_telegram(build_message(item))

            sent.add(notice_id)
            save_sent(sent)

            sent_count += 1
            log(f"발송 완료: {title} / {notice_id}")

            time.sleep(1)

        except Exception as error:
            log(f"발송 실패: {title} / {error}")

    log(f"이번 실행 발송: {sent_count}건")
    log("정상 종료")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
K-APT 부산·경남 승강기 입찰공고 텔레그램 알림 봇

수정 사항
- 한 번의 API 조회에서 같은 공고가 여러 번 나와도 1회만 발송
- 이미 발송한 공고는 seen.json에 기록
- Render Persistent Disk가 /var/data에 있으면 해당 위치에 기록
- 신규 공고가 없을 때 텔레그램 메시지를 보내지 않음
- seen.json을 안전하게 임시 파일에 저장한 후 교체
"""

import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.parse
import html


# ───────────────────── 환경변수 ─────────────────────

SERVICE_KEY = os.environ.get("KAPT_SERVICE_KEY", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# ───────────────────── API 설정 ─────────────────────

API_URL = (
    "https://apis.data.go.kr/1613000/"
    "ApHusBidPblAncInfoOfferServiceV2/getPblAncDeSearchV2"
)

TARGET_AREAS = {26, 48}
ELEVATOR_TYPE3 = {"06"}

ELEVATOR_KEYWORDS = [
    "승강기",
    "엘리베이터",
    "엘레베이터",
    "리프트",
    "ELEV",
    "elev",
]

LOOKBACK_DAYS = 3
NUM_OF_ROWS = 100


# Render에 /var/data Persistent Disk가 연결되어 있으면 그곳에 저장한다.
# 연결되어 있지 않으면 현재 폴더에 저장한다.
PERSISTENT_DIR = "/var/data"

if os.path.isdir(PERSISTENT_DIR) and os.access(PERSISTENT_DIR, os.W_OK):
    SEEN_FILE = os.path.join(PERSISTENT_DIR, "seen.json")
else:
    SEEN_FILE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "seen.json",
    )


# ───────────────────── 지역 및 상태 이름 ─────────────────────

AREA_NAME = {
    11: "서울",
    26: "부산",
    27: "대구",
    28: "인천",
    29: "광주",
    30: "대전",
    31: "울산",
    36: "세종",
    41: "경기",
    42: "강원",
    43: "충북",
    44: "충남",
    45: "전북",
    46: "전남",
    47: "경북",
    48: "경남",
    50: "제주",
}

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


# ───────────────────── 기본 함수 ─────────────────────

def log(message):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return {
                str(value).strip()
                for value in data
                if str(value).strip()
            }

        return set()

    except Exception as error:
        log(f"기존 알림 기록 불러오기 실패: {error}")
        return set()


def save_seen(seen):
    try:
        directory = os.path.dirname(SEEN_FILE)

        if directory:
            os.makedirs(directory, exist_ok=True)

        data = sorted(
            {
                str(value).strip()
                for value in seen
                if str(value).strip()
            }
        )[-3000:]

        temp_file = SEEN_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_file, SEEN_FILE)

    except Exception as error:
        log(f"알림 기록 저장 실패: {error}")


# ───────────────────── K-APT API 조회 ─────────────────────

def fetch_page(start_date, end_date, page_no, num_rows=NUM_OF_ROWS):
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": page_no,
        "numOfRows": num_rows,
        "type": "json",
    }

    query_string = (
        "serviceKey="
        + SERVICE_KEY
        + "&"
        + urllib.parse.urlencode(params)
    )

    url = API_URL + "?" + query_string

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "kapt-elevator-bot/1.1",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        raw_data = response.read().decode("utf-8")

    return json.loads(raw_data)


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
        data = fetch_page(
            start_date=start_date,
            end_date=end_date,
            page_no=page_no,
        )

        response = data.get("response", {})
        header = response.get("header", {})
        body = response.get("body", {})

        result_code = str(header.get("resultCode", ""))

        if result_code and result_code not in {"00", "0"}:
            result_message = header.get("resultMsg", "알 수 없는 오류")
            raise RuntimeError(
                f"K-APT API 오류: {result_code} / {result_message}"
            )

        try:
            total_count = int(body.get("totalCount", 0) or 0)
        except (TypeError, ValueError):
            total_count = 0

        page_items = normalize_page_items(body.get("items", []))

        if not page_items:
            break

        all_items.extend(page_items)

        log(
            f"페이지 {page_no}: "
            f"{len(page_items)}건 / 누적 {len(all_items)}건 / "
            f"전체 {total_count}건"
        )

        if total_count and len(all_items) >= total_count:
            break

        if len(page_items) < NUM_OF_ROWS:
            break

        page_no += 1
        time.sleep(0.3)

    return all_items


# ───────────────────── 공고 필터 ─────────────────────

def get_area_code(item):
    try:
        return int(item.get("bidArea"))
    except (TypeError, ValueError):
        return None


def is_target_area(item):
    return get_area_code(item) in TARGET_AREAS


def is_elevator(item):
    classify_type3 = str(
        item.get("codeClassifyType3") or ""
    ).strip()

    if classify_type3 in ELEVATOR_TYPE3:
        return True

    title = str(item.get("bidTitle") or "").lower()

    return any(
        keyword.lower() in title
        for keyword in ELEVATOR_KEYWORDS
    )


def get_notice_id(item):
    for key in ["bidNum", "bidNo", "pblancNo"]:
        value = item.get(key)

        if value is not None and str(value).strip():
            return str(value).strip()

    return ""


# ───────────────────── 텔레그램 메시지 ─────────────────────

def get_area_name(item):
    area_code = get_area_code(item)

    if area_code is None:
        return "지역 미확인"

    return AREA_NAME.get(area_code, str(area_code))


def build_detail_url(item):
    bid_num = get_notice_id(item)
    encoded_bid_num = urllib.parse.quote(bid_num)

    return (
        "https://www.k-apt.go.kr/bid/bidDetail.do"
        f"?bidNum={encoded_bid_num}"
    )


def build_message(item):
    area_name = get_area_name(item)

    state_code = str(item.get("bidState") or "")
    state_name = STATE_NAME.get(
        state_code,
        state_code if state_code else "상태 미확인",
    )

    title = html.escape(
        str(item.get("bidTitle") or "제목 없음")
    )

    apartment_name = html.escape(
        str(item.get("bidKaptname") or "단지명 없음")
    )

    registration_date = html.escape(
        str(item.get("bidRegDate") or "미확인")
    )

    deadline = html.escape(
        str(item.get("bidDeadline") or "미확인")
    )

    bid_num = html.escape(
        get_notice_id(item) or "번호 없음"
    )

    emergency = (
        " ⚠️ 긴급공고"
        if str(item.get("bidEmrgYn") or "").upper() == "Y"
        else ""
    )

    detail_url = html.escape(
        build_detail_url(item),
        quote=True,
    )

    lines = [
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
        f'🔗 <a href="{detail_url}">K-APT 상세보기</a>',
    ]

    return "\n".join(lines)


def send_telegram(text):
    url = (
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    )

    payload = urllib.parse.urlencode(
        {
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "User-Agent": "kapt-elevator-bot/1.1",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        raw_data = response.read().decode("utf-8")

    result = json.loads(raw_data)

    if not result.get("ok"):
        raise RuntimeError(
            f"텔레그램 발송 실패: {result}"
        )

    return result


# ───────────────────── 메인 실행 ─────────────────────

def main():
    log("===================================")
    log("K-APT 부산·경남 승강기 공고 알림 시작")
    log("===================================")
    log(f"중복 기록 파일: {SEEN_FILE}")

    missing_variables = []

    if not SERVICE_KEY:
        missing_variables.append("KAPT_SERVICE_KEY")

    if not TG_TOKEN:
        missing_variables.append("TELEGRAM_BOT_TOKEN")

    if not TG_CHAT_ID:
        missing_variables.append("TELEGRAM_CHAT_ID")

    if missing_variables:
        log(
            "환경변수가 비어 있습니다: "
            + ", ".join(missing_variables)
        )
        sys.exit(1)

    today = datetime.date.today()

    start_date = (
        today - datetime.timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    end_date = today.strftime("%Y-%m-%d")

    log(f"조회 기간: {start_date} ~ {end_date}")

    seen = load_seen()
    log(f"기존 알림 기록: {len(seen)}건")

    try:
        all_items = fetch_all(
            start_date=start_date,
            end_date=end_date,
        )

    except Exception as error:
        log(f"K-APT API 조회 실패: {error}")
        sys.exit(1)

    log(f"전체 공고 수신: {len(all_items)}건")

    matched_items = []

    # 이번 실행에서 이미 목록에 넣은 공고번호
    current_run_ids = set()

    duplicate_count = 0

    for item in all_items:
        if not is_target_area(item):
            continue

        if not is_elevator(item):
            continue

        notice_id = get_notice_id(item)

        if not notice_id:
            log(
                "공고번호가 없어 제외: "
                + str(item.get("bidTitle") or "제목 없음")
            )
            continue

        # 과거 실행에서 이미 보낸 공고
        if notice_id in seen:
            continue

        # 이번 API 조회 결과 안에서 중복으로 나온 공고
        if notice_id in current_run_ids:
            duplicate_count += 1
            continue

        current_run_ids.add(notice_id)
        matched_items.append(item)

    matched_items.sort(
        key=lambda item: (
            str(item.get("bidRegDate") or ""),
            get_notice_id(item),
        )
    )

    log(f"조회 결과 내부 중복 제외: {duplicate_count}건")
    log(
        "조건 일치 "
        f"(부산·경남 + 승강기 + 미발송): "
        f"{len(matched_items)}건"
    )

    if not matched_items:
        log("새로운 승강기 공고가 없습니다.")
        log("텔레그램 메시지는 발송하지 않습니다.")
        return

    sent_count = 0

    for item in matched_items:
        notice_id = get_notice_id(item)
        title = str(item.get("bidTitle") or "제목 없음")

        # 발송 직전에도 다시 한번 중복 확인
        if notice_id in seen:
            log(f"중복 발송 직전 차단: {title}")
            continue

        try:
            message = build_message(item)
            send_telegram(message)

            seen.add(notice_id)
            save_seen(seen)

            sent_count += 1
            log(
                f"텔레그램 발송 완료: "
                f"{title} / {notice_id}"
            )

            time.sleep(0.5)

        except Exception as error:
            log(
                f"텔레그램 발송 실패: "
                f"{title} / {error}"
            )

    log("===================================")
    log(f"신규 공고 발송: {sent_count}건")
    log(f"현재 알림 기록: {len(seen)}건")
    log("실행 완료")
    log("===================================")


if __name__ == "__main__":
    main()

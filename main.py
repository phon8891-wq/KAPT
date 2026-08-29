#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
K-APT 부산·경남 승강기 입찰공고 텔레그램 알림 봇
공공데이터포털 V3 API 전용 / GitHub Actions

확인된 V3 정보
- Base URL:
  https://apis.data.go.kr/1613000/ApHusBidPblAncInfoOfferServiceV3
- 공고일 조회 기능:
  /getPblAncDeSearchV3

기능
- 최근 7일 공고 조회
- 부산(26), 경남(48) 필터
- 승강기 관련 공고 필터
- sent_notice.json 중복 방지
- 신규 공고만 Telegram 발송
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

VERSION = "KAPT PUBLIC API V3"

SERVICE_KEY = os.environ.get("KAPT_SERVICE_KEY", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

API_URL = (
    "https://apis.data.go.kr/1613000/"
    "ApHusBidPblAncInfoOfferServiceV3/"
    "getPblAncDeSearchV3"
)

TARGET_AREAS = {26, 48}

ELEVATOR_TYPE3 = {"06"}

ELEVATOR_KEYWORDS = [
    "승강기",
    "엘리베이터",
    "엘레베이터",
    "리프트",
    "elevator",
    "elev",
]

LOOKBACK_DAYS = 7
NUM_OF_ROWS = 100
MAX_RETRIES = 5
MAX_SENT_ITEMS = 5000
REQUEST_TIMEOUT = 30

SENT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "sent_notice.json",
)

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

        sent = {
            str(value).strip()
            for value in data
            if str(value).strip()
        }

        return sent, True

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


def build_api_url(start_date, end_date, page_no):
    """
    기존에 정상 사용하던 공공데이터포털 인증키 처리 방식을 그대로 유지.
    KAPT_SERVICE_KEY가 Encoding 인증키여도 이중 인코딩하지 않음.
    """
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": page_no,
        "numOfRows": NUM_OF_ROWS,
        "type": "json",
    }

    query_string = (
        "serviceKey="
        + SERVICE_KEY
        + "&"
        + urllib.parse.urlencode(params)
    )

    return API_URL + "?" + query_string


def fetch_page(start_date, end_date, page_no):
    url = build_api_url(
        start_date,
        end_date,
        page_no,
    )

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(
                f"V3 API 조회 page={page_no} "
                f"({attempt}/{MAX_RETRIES})"
            )

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "kapt-telegram-v3/1.0",
                    "Accept": "application/json",
                    "Connection": "close",
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                raw_data = response.read().decode(
                    "utf-8",
                    errors="replace",
                )

            try:
                data = json.loads(raw_data)

            except json.JSONDecodeError:
                raise RuntimeError(
                    "API 응답이 JSON이 아닙니다: "
                    + raw_data[:800].replace("\n", " ")
                )

            response_data = data.get("response", {})
            header = response_data.get("header", {})

            result_code = str(
                header.get("resultCode", "")
            ).strip()

            result_message = str(
                header.get("resultMsg", "")
            ).strip()

            if result_code and result_code not in {"00", "0"}:
                raise RuntimeError(
                    f"K-APT V3 API 오류: "
                    f"{result_code} / {result_message}"
                )

            return data

        except urllib.error.HTTPError as error:
            try:
                error_body = error.read().decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                error_body = ""

            last_error = (
                f"HTTP {error.code} {error.reason}"
                + (
                    f" / {error_body[:1000]}"
                    if error_body
                    else ""
                )
            )

            log(
                f"V3 API 조회 실패 "
                f"({attempt}/{MAX_RETRIES}): "
                f"{last_error}"
            )

        except Exception as error:
            last_error = str(error)

            log(
                f"V3 API 조회 실패 "
                f"({attempt}/{MAX_RETRIES}): "
                f"{last_error}"
            )

        if attempt < MAX_RETRIES:
            time.sleep(min(attempt * 5, 20))

    raise RuntimeError(
        f"K-APT V3 API 요청 최종 실패: {last_error}"
    )


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
            start_date,
            end_date,
            page_no,
        )

        response = data.get("response", {})
        body = response.get("body", {})

        try:
            total_count = int(
                body.get("totalCount", 0) or 0
            )
        except (TypeError, ValueError):
            total_count = 0

        page_items = normalize_page_items(
            body.get("items", [])
        )

        if not page_items:
            break

        all_items.extend(page_items)

        log(
            f"페이지 {page_no}: "
            f"{len(page_items)}건 / "
            f"누적 {len(all_items)}건 / "
            f"전체 {total_count}건"
        )

        if total_count and len(all_items) >= total_count:
            break

        if len(page_items) < NUM_OF_ROWS:
            break

        page_no += 1
        time.sleep(0.5)

    return all_items


def get_area_code(item):
    for key in (
        "bidArea",
        "bidAreaCode",
        "areaCode",
    ):
        value = item.get(key)

        if value is None:
            continue

        try:
            return int(value)
        except (TypeError, ValueError):
            pass

    return None


def is_target_area(item):
    return get_area_code(item) in TARGET_AREAS


def is_elevator(item):
    classify_type3 = str(
        item.get("codeClassifyType3") or ""
    ).strip()

    if classify_type3 in ELEVATOR_TYPE3:
        return True

    title = str(
        item.get("bidTitle")
        or item.get("bidPblAncNm")
        or ""
    ).lower()

    return any(
        keyword.lower() in title
        for keyword in ELEVATOR_KEYWORDS
    )


def get_notice_id(item):
    for key in (
        "bidNum",
        "bidNo",
        "pblancNo",
        "bidPblAncNo",
    ):
        value = item.get(key)

        if value is not None and str(value).strip():
            return str(value).strip()

    return ""


def get_title(item):
    return str(
        item.get("bidTitle")
        or item.get("bidPblAncNm")
        or "제목 없음"
    )


def unique_target_items(all_items):
    unique_items = {}

    for item in all_items:
        if not is_target_area(item):
            continue

        if not is_elevator(item):
            continue

        notice_id = get_notice_id(item)

        if notice_id:
            unique_items[notice_id] = item

    return list(unique_items.values())


def get_area_name(item):
    area_code = get_area_code(item)

    if area_code is None:
        return "지역 미확인"

    return AREA_NAME.get(
        area_code,
        str(area_code),
    )


def build_detail_url(item):
    notice_id = get_notice_id(item)

    if not notice_id:
        return "https://www.k-apt.go.kr/bid/bidList.do"

    return (
        "https://www.k-apt.go.kr/"
        "bid/bidDetail.do?bidNum="
        + urllib.parse.quote(notice_id)
    )


def build_message(item):
    state_code = str(
        item.get("bidState")
        or item.get("bidPblAncStusCd")
        or ""
    ).strip()

    state_name = STATE_NAME.get(
        state_code,
        state_code if state_code else "상태 미확인",
    )

    title = html.escape(
        get_title(item)
    )

    apartment_name = html.escape(
        str(
            item.get("bidKaptname")
            or item.get("kaptName")
            or item.get("hsmpNm")
            or "단지명 없음"
        )
    )

    registration_date = html.escape(
        str(
            item.get("bidRegDate")
            or item.get("bidPblAncDt")
            or "미확인"
        )
    )

    deadline = html.escape(
        str(
            item.get("bidDeadline")
            or item.get("bidClsDt")
            or "미확인"
        )
    )

    bid_num = html.escape(
        get_notice_id(item) or "번호 없음"
    )

    emergency = (
        " ⚠️ 긴급공고"
        if str(
            item.get("bidEmrgYn")
            or item.get("emrgBidYn")
            or ""
        ).upper() == "Y"
        else ""
    )

    detail_url = html.escape(
        build_detail_url(item),
        quote=True,
    )

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
    url = (
        "https://api.telegram.org/"
        f"bot{TG_TOKEN}/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "User-Agent": "kapt-telegram-v3/1.0",
        },
    )

    last_error = None

    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:
                result = json.loads(
                    response.read().decode("utf-8")
                )

            if not result.get("ok"):
                raise RuntimeError(
                    f"텔레그램 응답 오류: {result}"
                )

            return

        except Exception as error:
            last_error = error

            log(
                f"텔레그램 발송 실패 "
                f"({attempt}/3): {error}"
            )

            if attempt < 3:
                time.sleep(attempt * 3)

    raise RuntimeError(
        f"텔레그램 발송 최종 실패: {last_error}"
    )


def main():
    validate_environment()

    today = datetime.date.today()

    start_date = (
        today
        - datetime.timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    end_date = today.strftime("%Y-%m-%d")

    log("========================================")
    log(VERSION)
    log(
        "공식 V3 API: "
        "getPblAncDeSearchV3"
    )
    log(
        f"조회 기간: "
        f"{start_date} ~ {end_date}"
    )
    log("========================================")

    sent, sent_file_exists = load_sent()

    all_items = fetch_all(
        start_date,
        end_date,
    )

    target_items = unique_target_items(
        all_items
    )

    current_ids = {
        get_notice_id(item)
        for item in target_items
        if get_notice_id(item)
    }

    log(
        f"최근 {LOOKBACK_DAYS}일 전체 공고: "
        f"{len(all_items)}건"
    )

    log(
        f"부산·경남 승강기 공고: "
        f"{len(target_items)}건"
    )

    log(
        f"기존 발송 기록: "
        f"{len(sent)}건"
    )

    # sent_notice.json이 정말 없는 최초 설치 때만 기준값 저장.
    # 기존 운영 중인 파일이 있으면 신규 공고를 임의로 sent 처리하지 않음.
    if not sent_file_exists:
        save_sent(current_ids)

        log(
            f"최초 설치: 현재 공고 "
            f"{len(current_ids)}건을 기준값으로 저장"
        )

        log(
            "최초 설치 회차는 기존 공고를 발송하지 않음"
        )

        return

    new_items = [
        item
        for item in target_items
        if get_notice_id(item) not in sent
    ]

    new_items.sort(
        key=lambda item: (
            str(
                item.get("bidRegDate")
                or item.get("bidPblAncDt")
                or ""
            ),
            get_notice_id(item),
        )
    )

    log(
        f"신규 미발송 공고: "
        f"{len(new_items)}건"
    )

    sent_count = 0
    failed_count = 0

    for item in new_items:
        notice_id = get_notice_id(item)
        title = get_title(item)

        try:
            send_telegram(
                build_message(item)
            )

            # 실제 Telegram 성공 후에만 기록
            sent.add(notice_id)
            save_sent(sent)

            sent_count += 1

            log(
                f"발송 완료: "
                f"{title} / {notice_id}"
            )

            time.sleep(1)

        except Exception as error:
            failed_count += 1

            log(
                f"발송 실패: "
                f"{title} / {error}"
            )

    log(
        f"이번 실행 발송 성공: "
        f"{sent_count}건"
    )

    log(
        f"이번 실행 발송 실패: "
        f"{failed_count}건"
    )

    log("정상 종료")


if __name__ == "__main__":
    main()

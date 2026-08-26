#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
K-APT 부산·경남 승강기 입찰공고 텔레그램 알림 봇
GitHub Actions 전용

- 한 번 실행 후 종료
- sent_notice.json으로 중복 알림 방지
- 부산(26), 경남(48) 승강기 관련 공고만 알림
- API 오류 발생 시 응답 내용까지 로그 출력
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


# =========================================================
# 환경변수
# =========================================================

SERVICE_KEY = os.environ.get("KAPT_SERVICE_KEY", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


# =========================================================
# K-APT API
# =========================================================

API_URL = (
    "https://apis.data.go.kr/1613000/"
    "ApHusBidPblAncInfoOfferServiceV2/"
    "getPblAncDeSearchV2"
)


# =========================================================
# 검색 조건
# =========================================================

# 부산 26 / 경남 48
TARGET_AREAS = {26, 48}

# 승강기 업종 코드
ELEVATOR_TYPE3 = {"06"}

# 혹시 코드가 누락된 공고를 잡기 위한 제목 키워드
ELEVATOR_KEYWORDS = [
    "승강기",
    "엘리베이터",
    "엘레베이터",
    "리프트",
    "elev",
]

# 최근 며칠 공고 조회
LOOKBACK_DAYS = 3

# API 한 페이지 조회 건수
NUM_OF_ROWS = 100

# API 및 텔레그램 재시도 횟수
MAX_RETRIES = 3

# sent_notice.json 최대 보관 개수
MAX_SENT_ITEMS = 5000


# =========================================================
# sent_notice.json
# =========================================================

SENT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "sent_notice.json",
)


# =========================================================
# 지역명
# =========================================================

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


# =========================================================
# 공고 상태
# =========================================================

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


# =========================================================
# 로그
# =========================================================

def log(message):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(
        f"[{now}] {message}",
        flush=True,
    )


# =========================================================
# 발송 기록 불러오기
# =========================================================

def load_sent():
    if not os.path.exists(SENT_FILE):
        return set(), False

    try:
        with open(
            SENT_FILE,
            "r",
            encoding="utf-8",
        ) as file:

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
        log(
            f"sent_notice.json 불러오기 실패: {error}"
        )

        return set(), False


# =========================================================
# 발송 기록 저장
# =========================================================

def save_sent(sent):
    values = sorted({
        str(value).strip()
        for value in sent
        if str(value).strip()
    })[-MAX_SENT_ITEMS:]

    temp_file = SENT_FILE + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            values,
            file,
            ensure_ascii=False,
            indent=2,
        )

        file.flush()

        os.fsync(
            file.fileno()
        )

    os.replace(
        temp_file,
        SENT_FILE,
    )


# =========================================================
# API 한 페이지 조회
# =========================================================

def fetch_page(start_date, end_date, page_no):

    params = {
        "serviceKey": SERVICE_KEY,
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": page_no,
        "numOfRows": NUM_OF_ROWS,
        "type": "json",
    }

    # serviceKey에 이미 % 인코딩이 들어있는 경우
    # 다시 %25로 변환되는 문제를 막기 위해 safe="%" 사용
    query_string = urllib.parse.urlencode(
        params,
        safe="%",
    )

    url = API_URL + "?" + query_string

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:
            log(
                f"API 요청: page={page_no}, "
                f"{start_date} ~ {end_date}"
            )

            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:

                raw_data = response.read().decode(
                    "utf-8",
                    errors="replace",
                )

            # 응답이 JSON인지 확인
            try:
                return json.loads(raw_data)

            except json.JSONDecodeError:
                raise RuntimeError(
                    "API 응답이 JSON이 아닙니다. "
                    f"응답 일부: {raw_data[:500]}"
                )

        except urllib.error.HTTPError as error:

            try:
                error_body = error.read().decode(
                    "utf-8",
                    errors="replace",
                )

            except Exception:
                error_body = ""

            last_error = (
                f"HTTP {error.code} "
                f"{error.reason}"
            )

            if error_body:
                last_error += (
                    f" / 서버 응답: "
                    f"{error_body[:1000]}"
                )

            log(
                f"API 요청 실패 "
                f"({attempt}/{MAX_RETRIES}): "
                f"{last_error}"
            )

        except urllib.error.URLError as error:

            last_error = (
                f"URL 오류: {error}"
            )

            log(
                f"API 요청 실패 "
                f"({attempt}/{MAX_RETRIES}): "
                f"{last_error}"
            )

        except Exception as error:

            last_error = str(error)

            log(
                f"API 요청 실패 "
                f"({attempt}/{MAX_RETRIES}): "
                f"{last_error}"
            )

        if attempt < MAX_RETRIES:

            time.sleep(
                attempt * 3
            )

    raise RuntimeError(
        "K-APT API 요청 최종 실패: "
        f"{last_error}"
    )


# =========================================================
# API items 형태 정리
# =========================================================

def normalize_page_items(items):

    if not items:
        return []

    if isinstance(
        items,
        list,
    ):
        return items

    if isinstance(
        items,
        dict,
    ):

        if "item" in items:

            item_data = items["item"]

            if isinstance(
                item_data,
                list,
            ):
                return item_data

            if isinstance(
                item_data,
                dict,
            ):
                return [item_data]

            return []

        return [items]

    return []


# =========================================================
# 전체 페이지 조회
# =========================================================

def fetch_all(start_date, end_date):

    all_items = []

    page_no = 1

    while True:

        data = fetch_page(
            start_date,
            end_date,
            page_no,
        )

        response = data.get(
            "response",
            {},
        )

        header = response.get(
            "header",
            {},
        )

        body = response.get(
            "body",
            {},
        )

        result_code = str(
            header.get(
                "resultCode",
                "",
            )
        ).strip()

        if (
            result_code
            and result_code not in {"00", "0"}
        ):

            result_message = header.get(
                "resultMsg",
                "알 수 없는 오류",
            )

            raise RuntimeError(
                "K-APT API 오류: "
                f"{result_code} / "
                f"{result_message}"
            )

        try:

            total_count = int(
                body.get(
                    "totalCount",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            total_count = 0

        page_items = normalize_page_items(
            body.get(
                "items",
                [],
            )
        )

        if not page_items:
            break

        all_items.extend(
            page_items
        )

        log(
            f"페이지 {page_no}: "
            f"{len(page_items)}건 / "
            f"누적 {len(all_items)}건 / "
            f"전체 {total_count}건"
        )

        if (
            total_count
            and len(all_items) >= total_count
        ):
            break

        if len(page_items) < NUM_OF_ROWS:
            break

        page_no += 1

        time.sleep(0.3)

    return all_items


# =========================================================
# 지역 코드
# =========================================================

def get_area_code(item):

    try:

        return int(
            item.get(
                "bidArea"
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return None


# =========================================================
# 부산·경남 여부
# =========================================================

def is_target_area(item):

    return (
        get_area_code(item)
        in TARGET_AREAS
    )


# =========================================================
# 승강기 공고 여부
# =========================================================

def is_elevator(item):

    classify_type3 = str(
        item.get(
            "codeClassifyType3"
        )
        or ""
    ).strip()

    if (
        classify_type3
        in ELEVATOR_TYPE3
    ):
        return True

    title = str(
        item.get(
            "bidTitle"
        )
        or ""
    ).lower()

    return any(
        keyword.lower()
        in title
        for keyword
        in ELEVATOR_KEYWORDS
    )


# =========================================================
# 공고번호
# =========================================================

def get_notice_id(item):

    for key in [
        "bidNum",
        "bidNo",
        "pblancNo",
    ]:

        value = item.get(
            key
        )

        if (
            value is not None
            and str(value).strip()
        ):

            return str(
                value
            ).strip()

    return ""


# =========================================================
# 중복 제거 + 조건 필터링
# =========================================================

def unique_target_items(all_items):

    unique_items = {}

    for item in all_items:

        if not is_target_area(
            item
        ):
            continue

        if not is_elevator(
            item
        ):
            continue

        notice_id = get_notice_id(
            item
        )

        if notice_id:

            unique_items[
                notice_id
            ] = item

    return list(
        unique_items.values()
    )


# =========================================================
# 지역명 표시
# =========================================================

def get_area_name(item):

    area_code = get_area_code(
        item
    )

    if area_code is None:
        return "지역 미확인"

    return AREA_NAME.get(
        area_code,
        str(area_code),
    )


# =========================================================
# K-APT 상세페이지
# =========================================================

def build_detail_url(item):

    return (
        "https://www.k-apt.go.kr/"
        "bid/bidDetail.do?bidNum="
        + urllib.parse.quote(
            get_notice_id(item)
        )
    )


# =========================================================
# 텔레그램 메시지
# =========================================================

def build_message(item):

    state_code = str(
        item.get(
            "bidState"
        )
        or ""
    ).strip()

    state_name = STATE_NAME.get(
        state_code,
        (
            state_code
            if state_code
            else "상태 미확인"
        ),
    )

    title = html.escape(
        str(
            item.get(
                "bidTitle"
            )
            or "제목 없음"
        )
    )

    apartment_name = html.escape(
        str(
            item.get(
                "bidKaptname"
            )
            or "단지명 없음"
        )
    )

    registration_date = html.escape(
        str(
            item.get(
                "bidRegDate"
            )
            or "미확인"
        )
    )

    deadline = html.escape(
        str(
            item.get(
                "bidDeadline"
            )
            or "미확인"
        )
    )

    bid_num = html.escape(
        get_notice_id(item)
        or "번호 없음"
    )

    emergency = (
        " ⚠️ 긴급공고"
        if str(
            item.get(
                "bidEmrgYn"
            )
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
        f'<a href="{detail_url}">🔗 K-APT 상세보기</a>',
    ])


# =========================================================
# 텔레그램 발송
# =========================================================

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
    }).encode(
        "utf-8"
    )

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "User-Agent": "kapt-elevator-bot/5.0",
        },
    )

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:

                result = json.loads(
                    response.read().decode(
                        "utf-8"
                    )
                )

            if not result.get(
                "ok"
            ):

                raise RuntimeError(
                    "텔레그램 응답 오류: "
                    f"{result}"
                )

            return

        except Exception as error:

            last_error = error

            log(
                "텔레그램 발송 실패 "
                f"({attempt}/{MAX_RETRIES}): "
                f"{error}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    attempt * 3
                )

    raise RuntimeError(
        "텔레그램 발송 최종 실패: "
        f"{last_error}"
    )


# =========================================================
# 환경변수 확인
# =========================================================

def validate_environment():

    missing = []

    if not SERVICE_KEY:
        missing.append(
            "KAPT_SERVICE_KEY"
        )

    if not TG_TOKEN:
        missing.append(
            "TELEGRAM_BOT_TOKEN"
        )

    if not TG_CHAT_ID:
        missing.append(
            "TELEGRAM_CHAT_ID"
        )

    if missing:

        log(
            "환경변수가 비어 있습니다: "
            + ", ".join(
                missing
            )
        )

        sys.exit(1)


# =========================================================
# MAIN
# =========================================================

def main():

    validate_environment()

    today = datetime.date.today()

    start_date = (
        today
        - datetime.timedelta(
            days=LOOKBACK_DAYS
        )
    ).strftime(
        "%Y-%m-%d"
    )

    end_date = today.strftime(
        "%Y-%m-%d"
    )

    log(
        "K-APT 공고 조회 시작"
    )

    log(
        f"조회 기간: "
        f"{start_date} ~ {end_date}"
    )

    sent, sent_file_exists = load_sent()

    all_items = fetch_all(
        start_date,
        end_date,
    )

    target_items = unique_target_items(
        all_items
    )

    current_ids = {
        get_notice_id(
            item
        )
        for item
        in target_items
        if get_notice_id(
            item
        )
    }

    log(
        "부산·경남 승강기 공고: "
        f"{len(target_items)}건"
    )

    log(
        "기존 발송 기록: "
        f"{len(sent)}건"
    )

    # sent_notice.json이 없으면
    # 현재 공고는 발송하지 않고 기준값만 저장
    if not sent_file_exists:

        save_sent(
            current_ids
        )

        log(
            "최초 실행: 기존 공고 "
            f"{len(current_ids)}건을 "
            "기준값으로 저장"
        )

        log(
            "기존 공고는 발송하지 않음"
        )

        return

    new_items = [
        item
        for item
        in target_items
        if get_notice_id(
            item
        )
        not in sent
    ]

    new_items.sort(
        key=lambda item: (
            str(
                item.get(
                    "bidRegDate"
                )
                or ""
            ),
            get_notice_id(
                item
            ),
        )
    )

    log(
        "신규 미발송 공고: "
        f"{len(new_items)}건"
    )

    sent_count = 0

    for item in new_items:

        notice_id = get_notice_id(
            item
        )

        title = str(
            item.get(
                "bidTitle"
            )
            or "제목 없음"
        )

        if notice_id in sent:
            continue

        try:

            send_telegram(
                build_message(
                    item
                )
            )

            sent.add(
                notice_id
            )

            save_sent(
                sent
            )

            sent_count += 1

            log(
                "발송 완료: "
                f"{title} / "
                f"{notice_id}"
            )

            time.sleep(1)

        except Exception as error:

            log(
                "발송 실패: "
                f"{title} / "
                f"{error}"
            )

    log(
        "이번 실행 발송: "
        f"{sent_count}건"
    )

    log(
        "실행 종료"
    )


if __name__ == "__main__":
    main()MAX_SENT_ITEMS = 5000

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


def fetch_page(start_date, end_date, page_no):
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": page_no,
        "numOfRows": NUM_OF_ROWS,
        "type": "json",
    }

    query_string = (
        "serviceKey=" + SERVICE_KEY + "&" + urllib.parse.urlencode(params)
    )

    request = urllib.request.Request(
        API_URL + "?" + query_string,
        headers={
            "User-Agent": "kapt-elevator-bot/4.0",
            "Accept": "application/json",
        },
    )

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw_data = response.read().decode("utf-8")
            return json.loads(raw_data)

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
        f"https://api.telegram.org/"
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
            "User-Agent": "kapt-elevator-bot/4.0",
        },
    )

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
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
                f"({attempt}/{MAX_RETRIES}): {error}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(attempt * 3)

    raise RuntimeError(
        f"텔레그램 발송 최종 실패: {last_error}"
    )


def validate_environment():
    missing = []

    if not SERVICE_KEY:
        missing.append("KAPT_SERVICE_KEY")

    if not TG_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")

    if not TG_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        log(
            "환경변수가 비어 있습니다: "
            + ", ".join(missing)
        )
        sys.exit(1)


def main():
    validate_environment()

    today = datetime.date.today()

    start_date = (
        today - datetime.timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    end_date = today.strftime("%Y-%m-%d")

    log("K-APT 공고 조회 시작")
    log(f"조회 기간: {start_date} ~ {end_date}")

    sent, sent_file_exists = load_sent()

    all_items = fetch_all(start_date, end_date)
    target_items = unique_target_items(all_items)

    current_ids = {
        get_notice_id(item)
        for item in target_items
        if get_notice_id(item)
    }

    log(f"부산·경남 승강기 공고: {len(target_items)}건")
    log(f"기존 발송 기록: {len(sent)}건")

    # sent_notice.json이 없거나 읽지 못한 경우
    # 현재 공고를 기준값으로만 저장하고 발송하지 않는다.
    if not sent_file_exists:
        save_sent(current_ids)

        log(
            f"최초 실행: 기존 공고 "
            f"{len(current_ids)}건을 기준값으로 저장"
        )
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
        title = str(
            item.get("bidTitle") or "제목 없음"
        )

        if notice_id in sent:
            continue

        try:
            send_telegram(build_message(item))

            sent.add(notice_id)
            save_sent(sent)

            sent_count += 1

            log(
                f"발송 완료: "
                f"{title} / {notice_id}"
            )

            time.sleep(1)

        except Exception as error:
            log(
                f"발송 실패: "
                f"{title} / {error}"
            )

    log(f"이번 실행 발송: {sent_count}건")
    log("실행 종료")


if __name__ == "__main__":
    main()

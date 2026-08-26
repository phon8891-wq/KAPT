#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
K-APT 부산·경남 승강기 입찰공고 텔레그램 알림 봇
GitHub Actions 전용 - K-APT 웹페이지 직접 조회 버전

- 공공데이터포털 API 사용 안 함
- K-APT 전국 입찰공고 페이지 직접 조회
- 부산 / 경남 공고만 필터링
- 승강기 관련 공고만 필터링
- sent_notice.json 중복 방지
- API -> WEB 방식 첫 전환 시 기존 공고는 발송하지 않음
"""

import os
import sys
import json
import time
import datetime
import hashlib
import html
import re
import urllib.request
import urllib.parse
import urllib.error

from html.parser import HTMLParser


# =========================================================
# 환경변수
# =========================================================

TG_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TG_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# =========================================================
# K-APT 웹 주소
# =========================================================

KAPT_BASE = "https://www.k-apt.go.kr"

KAPT_LIST_URL = (
    "https://www.k-apt.go.kr/bid/bidList.do"
)

# 한 번 실행할 때 확인할 페이지 수
# GitHub Actions가 주기적으로 실행되므로 10페이지면 충분히 넉넉함
MAX_PAGES = 10

MAX_RETRIES = 3

MAX_SENT_ITEMS = 5000


# =========================================================
# 검색 조건
# =========================================================

TARGET_REGIONS = {
    "부산",
    "경남",
}

ELEVATOR_KEYWORDS = [
    "승강기",
    "엘리베이터",
    "엘레베이터",
    "리프트",
    "elevator",
    "elev",
]


# =========================================================
# sent_notice.json
# =========================================================

SENT_FILE = os.path.join(
    os.path.dirname(
        os.path.abspath(__file__)
    ),
    "sent_notice.json",
)


# =========================================================
# 로그
# =========================================================

def log(message):
    now = datetime.datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    print(
        f"[{now}] {message}",
        flush=True,
    )


# =========================================================
# 발송 기록
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
            f"sent_notice.json 읽기 실패: {error}"
        )

        return set(), False


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
# HTML 테이블 파서
# =========================================================

class BidTableParser(HTMLParser):

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.in_tr = False
        self.in_td = False

        self.current_row = []
        self.current_cell = []

        self.current_href = ""
        self.current_onclick = ""

        self.rows = []

    def handle_starttag(
        self,
        tag,
        attrs,
    ):

        tag = tag.lower()

        attrs_dict = dict(attrs)

        if tag == "tr":

            self.in_tr = True
            self.current_row = []

        elif (
            tag == "td"
            and self.in_tr
        ):

            self.in_td = True
            self.current_cell = []

        elif (
            tag == "a"
            and self.in_tr
            and self.in_td
        ):

            href = attrs_dict.get(
                "href",
                "",
            )

            onclick = attrs_dict.get(
                "onclick",
                "",
            )

            if (
                href
                and href != "#"
                and not href.lower().startswith(
                    "javascript:"
                )
            ):

                self.current_href = href

            if onclick:
                self.current_onclick = onclick

    def handle_data(
        self,
        data,
    ):

        if (
            self.in_tr
            and self.in_td
        ):

            text = data.strip()

            if text:
                self.current_cell.append(
                    text
                )

    def handle_endtag(
        self,
        tag,
    ):

        tag = tag.lower()

        if (
            tag == "td"
            and self.in_td
        ):

            text = " ".join(
                self.current_cell
            ).strip()

            self.current_row.append(
                text
            )

            self.current_cell = []

            self.in_td = False

        elif (
            tag == "tr"
            and self.in_tr
        ):

            if self.current_row:

                self.rows.append({
                    "cells": self.current_row[:],
                    "href": self.current_href,
                    "onclick": self.current_onclick,
                })

            self.current_row = []

            self.current_href = ""
            self.current_onclick = ""

            self.in_tr = False
            self.in_td = False


# =========================================================
# K-APT 페이지 다운로드
# =========================================================

def fetch_html(page_no):

    params = {
        "pageNo": page_no,
        "type": 4,
        "searchBidGb": "bid_gb_1",
    }

    url = (
        KAPT_LIST_URL
        + "?"
        + urllib.parse.urlencode(params)
    )

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            log(
                f"K-APT 웹 조회 "
                f"page={page_no} "
                f"({attempt}/{MAX_RETRIES})"
            )

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    ),
                    "Accept": (
                        "text/html,"
                        "application/xhtml+xml,"
                        "application/xml;q=0.9,*/*;q=0.8"
                    ),
                    "Accept-Language": (
                        "ko-KR,ko;q=0.9,en;q=0.8"
                    ),
                    "Referer": (
                        "https://www.k-apt.go.kr/"
                    ),
                    "Connection": "close",
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=20,
            ) as response:

                raw = response.read()

                charset = (
                    response.headers.get_content_charset()
                    or "utf-8"
                )

            try:

                text = raw.decode(
                    charset,
                    errors="replace",
                )

            except Exception:

                text = raw.decode(
                    "utf-8",
                    errors="replace",
                )

            if (
                "입찰목록"
                not in text
                and "전국 입찰공고"
                not in text
            ):

                raise RuntimeError(
                    "K-APT 입찰공고 페이지가 아닌 "
                    "다른 응답을 받았습니다."
                )

            return text

        except Exception as error:

            last_error = error

            log(
                f"K-APT 웹 조회 실패: {error}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    attempt * 3
                )

    raise RuntimeError(
        "K-APT 웹 조회 최종 실패: "
        f"{last_error}"
    )


# =========================================================
# 지역 추출
# =========================================================

def extract_region(title):

    match = re.search(
        r"\[([^\]]+)\]",
        title,
    )

    if not match:
        return ""

    return match.group(1).strip()


# =========================================================
# 승강기 공고 여부
# =========================================================

def is_elevator(title):

    text = title.lower()

    return any(
        keyword.lower() in text
        for keyword in ELEVATOR_KEYWORDS
    )


# =========================================================
# 부산 / 경남 여부
# =========================================================

def is_target_region(title):

    region = extract_region(
        title
    )

    return (
        region
        in TARGET_REGIONS
    )


# =========================================================
# 상세 링크 만들기
# =========================================================

def make_detail_url(
    href,
    onclick,
):

    if href:

        return urllib.parse.urljoin(
            KAPT_BASE,
            href,
        )

    # onclick 안에서 bidNum 추출 시도
    if onclick:

        # 긴 숫자 형태의 입찰번호 추출
        numbers = re.findall(
            r"['\"]?(\d{6,})['\"]?",
            onclick,
        )

        if numbers:

            bid_num = numbers[-1]

            return (
                "https://www.k-apt.go.kr/"
                "bid/bidDetail.do?bidNum="
                + urllib.parse.quote(
                    bid_num
                )
            )

    return KAPT_LIST_URL


# =========================================================
# 안정적인 공고 ID 생성
# =========================================================

def make_notice_id(
    title,
    deadline,
    apartment,
    registration_date,
):

    source = "|".join([
        title.strip(),
        deadline.strip(),
        apartment.strip(),
        registration_date.strip(),
    ])

    digest = hashlib.sha256(
        source.encode("utf-8")
    ).hexdigest()[:24]

    return (
        "WEB-"
        + digest
    )


# =========================================================
# HTML 한 페이지에서 공고 추출
# =========================================================

def parse_bids(html_text):

    parser = BidTableParser()

    parser.feed(
        html_text
    )

    results = []

    for row in parser.rows:

        cells = row.get(
            "cells",
            [],
        )

        # 정상적인 K-APT 입찰 목록은 보통
        # 순번 / 종류 / 낙찰방법 / 공고명 /
        # 마감일 / 상태 / 단지명 / 공고일
        if len(cells) < 8:
            continue

        try:

            number = cells[0]
            bid_type = cells[1]
            award_method = cells[2]
            title = cells[3]
            deadline = cells[4]
            state = cells[5]
            apartment = cells[6]
            registration_date = cells[7]

        except IndexError:
            continue

        # 실제 입찰 데이터가 아닌 행 제거
        if not title:
            continue

        if title == "입찰공고명":
            continue

        if "입찰 정보가 존재" in title:
            continue

        notice_id = make_notice_id(
            title,
            deadline,
            apartment,
            registration_date,
        )

        detail_url = make_detail_url(
            row.get(
                "href",
                "",
            ),
            row.get(
                "onclick",
                "",
            ),
        )

        results.append({
            "id": notice_id,
            "number": number,
            "bid_type": bid_type,
            "award_method": award_method,
            "title": title,
            "deadline": deadline,
            "state": state,
            "apartment": apartment,
            "registration_date": registration_date,
            "region": extract_region(
                title
            ),
            "url": detail_url,
        })

    return results


# =========================================================
# 최신 공고 전체 조회
# =========================================================

def fetch_recent_bids():

    all_items = {}

    empty_pages = 0

    for page_no in range(
        1,
        MAX_PAGES + 1,
    ):

        html_text = fetch_html(
            page_no
        )

        items = parse_bids(
            html_text
        )

        log(
            f"페이지 {page_no}: "
            f"{len(items)}건 파싱"
        )

        if not items:

            empty_pages += 1

            if empty_pages >= 2:
                break

        else:

            empty_pages = 0

        for item in items:

            all_items[
                item["id"]
            ] = item

        time.sleep(0.5)

    return list(
        all_items.values()
    )


# =========================================================
# 대상 공고 필터
# =========================================================

def filter_target_bids(items):

    results = []

    for item in items:

        title = item.get(
            "title",
            "",
        )

        if not is_target_region(
            title
        ):
            continue

        if not is_elevator(
            title
        ):
            continue

        results.append(
            item
        )

    return results


# =========================================================
# 텔레그램 메시지
# =========================================================

def build_message(item):

    region = html.escape(
        item.get(
            "region",
            "지역 미확인",
        )
    )

    apartment = html.escape(
        item.get(
            "apartment",
            "단지명 미확인",
        )
    )

    title = html.escape(
        item.get(
            "title",
            "제목 없음",
        )
    )

    deadline = html.escape(
        item.get(
            "deadline",
            "미확인",
        )
    )

    state = html.escape(
        item.get(
            "state",
            "미확인",
        )
    )

    registration_date = html.escape(
        item.get(
            "registration_date",
            "미확인",
        )
    )

    award_method = html.escape(
        item.get(
            "award_method",
            "미확인",
        )
    )

    detail_url = html.escape(
        item.get(
            "url",
            KAPT_LIST_URL,
        ),
        quote=True,
    )

    return "\n".join([
        "🛗 <b>K-APT 승강기 공고 알림</b>",
        "",
        f"📍 지역: <b>{region}</b>",
        f"🏢 단지: {apartment}",
        f"📌 공고명: {title}",
        f"📋 상태: {state}",
        f"💰 낙찰방법: {award_method}",
        f"📅 공고일: {registration_date}",
        f"⏰ 마감일: <b>{deadline}</b>",
        "",
        f'🔗 <a href="{detail_url}">'
        f'K-APT 확인하기</a>',
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
            "User-Agent": (
                "kapt-elevator-bot-web/1.0"
            ),
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

                raw = response.read().decode(
                    "utf-8",
                    errors="replace",
                )

            result = json.loads(
                raw
            )

            if not result.get(
                "ok"
            ):

                raise RuntimeError(
                    f"텔레그램 응답 오류: {result}"
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

    log(
        "======================================"
    )

    log(
        "K-APT WEB 방식 조회 시작"
    )

    log(
        "대상: 부산·경남 승강기 관련 공고"
    )

    log(
        "======================================"
    )

    sent, sent_file_exists = load_sent()

    all_items = fetch_recent_bids()

    target_items = filter_target_bids(
        all_items
    )

    log(
        f"최근 조회 전체 공고: "
        f"{len(all_items)}건"
    )

    log(
        f"부산·경남 승강기 대상: "
        f"{len(target_items)}건"
    )

    log(
        f"기존 발송 기록: "
        f"{len(sent)}건"
    )

    current_web_ids = {
        item["id"]
        for item in target_items
    }

    # =====================================================
    # API 방식 -> WEB 방식 최초 전환 확인
    #
    # 기존 sent_notice.json에는 숫자형 API 공고번호만 있고
    # WEB- 형태 ID가 없다.
    #
    # 이 상태에서 바로 발송하면 기존 최근 공고가
    # 전부 신규로 인식될 수 있기 때문에
    # 첫 실행은 기준값만 저장한다.
    # =====================================================

    has_web_history = any(
        str(value).startswith(
            "WEB-"
        )
        for value in sent
    )

    if not has_web_history:

        sent.update(
            current_web_ids
        )

        save_sent(
            sent
        )

        log(
            "WEB 방식 최초 실행"
        )

        log(
            f"현재 대상 공고 "
            f"{len(current_web_ids)}건을 "
            f"기준값으로 저장"
        )

        log(
            "기존 공고 대량 발송 방지를 위해 "
            "이번 실행에서는 알림을 보내지 않습니다."
        )

        log(
            "다음 실행부터 신규 공고만 알림됩니다."
        )

        log(
            "정상 종료"
        )

        return

    # =====================================================
    # 신규 공고 찾기
    # =====================================================

    new_items = [
        item
        for item in target_items
        if item["id"] not in sent
    ]

    new_items.sort(
        key=lambda item: (
            item.get(
                "registration_date",
                "",
            ),
            item.get(
                "title",
                "",
            ),
        )
    )

    log(
        f"신규 미발송 공고: "
        f"{len(new_items)}건"
    )

    sent_count = 0

    for item in new_items:

        notice_id = item[
            "id"
        ]

        title = item.get(
            "title",
            "제목 없음",
        )

        try:

            send_telegram(
                build_message(
                    item
                )
            )

            sent.add(
                notice_id
            )

            # 한 건 발송할 때마다 바로 저장
            # 중간 실패 시에도 중복 발송 방지
            save_sent(
                sent
            )

            sent_count += 1

            log(
                f"발송 완료: {title}"
            )

            time.sleep(1)

        except Exception as error:

            log(
                f"발송 실패: "
                f"{title} / {error}"
            )

    log(
        f"이번 실행 발송: "
        f"{sent_count}건"
    )

    log(
        "K-APT WEB 방식 실행 종료"
    )


if __name__ == "__main__":
    main()

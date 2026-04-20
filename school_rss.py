#!/usr/bin/env python3
"""관곡초등학교 웹사이트 변경 추적 & RSS 생성기"""

import hashlib
import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
BASE_URL = "https://gwan-gok-e.goeyi.kr/gwan-gok-e"
DATA_DIR = Path(__file__).parent / "data"
RSS_FILE = Path(__file__).parent / "feed.xml"

# 추적할 게시판 목록 (사이트 전체)
BOARDS = {
    # 알림마당
    "공지사항":                 {"bbsId": "11818", "mi": "20550"},
    "가정통신문":               {"bbsId": "11819", "mi": "19565"},
    "가정통신문(교육청)":       {"bbsId": "13879", "mi": "22143"},
    "학교앨범":                 {"bbsId": "11822", "mi": "19569"},
    "안전교육":                 {"bbsId": "11823", "mi": "19570"},
    # 학생마당
    "학교생활인권규정":         {"bbsId": "11844", "mi": "19602"},
    "학생자치회":               {"bbsId": "12475", "mi": "20558"},
    "칭찬축하합니다":           {"bbsId": "11840", "mi": "19597"},
    "자유게시판":               {"bbsId": "11841", "mi": "19599"},
    "비밀상담실":               {"bbsId": "11842", "mi": "19600"},
    "관곡신문방송기자단":       {"bbsId": "12476", "mi": "20559"},
    # 학부모마당
    "학교운영위원회(운영)":     {"bbsId": "11849", "mi": "19609"},
    "학교운영위원회(구성)":     {"bbsId": "11852", "mi": "19611"},
    "학부모회(운영)":           {"bbsId": "12494", "mi": "20577"},
    "학부모회(구성)":           {"bbsId": "12493", "mi": "20576"},
    # 학교평가
    "학교평가자료실":           {"bbsId": "11890", "mi": "19663"},
    "관곡혁신교육자료실":       {"bbsId": "11883", "mi": "19652"},
    # 행정정보 및 민원 - 학교재정공개
    "예산결산공개":             {"bbsId": "11864", "mi": "19629"},
    "업무추진비집행현황":       {"bbsId": "11867", "mi": "19632"},
    "수의계약내역":             {"bbsId": "11871", "mi": "19636"},
    "입찰공고":                 {"bbsId": "11874", "mi": "19639"},
    "행정소식":                 {"bbsId": "11875", "mi": "19640"},
    # 전자민원창구
    "민원신청":                 {"bbsId": "11830", "mi": "19585"},
    "시설개방공지":             {"bbsId": "12496", "mi": "20580"},
    "시설개방현황":             {"bbsId": "12497", "mi": "20581"},
    "시설개방신청":             {"bbsId": "11833", "mi": "19588"},
    # 늘봄
    "늘봄공지사항":             {"bbsId": "11897", "mi": "19674"},
    "늘봄자료실":               {"bbsId": "11899", "mi": "19676"},
    # 급식정보
    "급식정보실":               {"bbsId": "11825", "mi": "19573"},
    "영양상담및교육":           {"bbsId": "11826", "mi": "19576"},
    # 보건실
    "성고충상담":               {"bbsId": "12490", "mi": "20573"},
    "보건정보실":               {"bbsId": "11824", "mi": "21308"},
    # 도서관
    "희망꿈터소식":             {"bbsId": "11900", "mi": "19678"},
    # 초등돌봄교실
    "돌봄교실공지":             {"bbsId": "11895", "mi": "19671"},
    # 진로정보
    "드림레터":                 {"bbsId": "11925", "mi": "19709"},
    # 관곡유치원
    "유치원공지사항":           {"bbsId": "11855", "mi": "19618"},
    "유치원운영위(운영)":       {"bbsId": "11858", "mi": "19623"},
    "유치원운영위(규정)":       {"bbsId": "11860", "mi": "19625"},
    # 관곡소식
    "관곡소식공지":             {"bbsId": "12474", "mi": "20552"},
    "교무기획부":               {"bbsId": "12477", "mi": "20560"},
    "진로연구부":               {"bbsId": "12478", "mi": "20561"},
    "생활인권부":               {"bbsId": "12479", "mi": "20562"},
    "정보체육부":               {"bbsId": "12480", "mi": "20563"},
    "수업나눔방":               {"bbsId": "12481", "mi": "20564"},
    "사이버신고센터":           {"bbsId": "12482", "mi": "20565"},
    "관곡소식운영위(소개)":     {"bbsId": "12483", "mi": "20566"},
    "관곡소식운영위(규정)":     {"bbsId": "12491", "mi": "20574"},
    "관곡소식운영위(구성)":     {"bbsId": "12484", "mi": "20567"},
    "관곡소식운영위(운영)":     {"bbsId": "12485", "mi": "20568"},
    "관곡소식학부모회(소개)":   {"bbsId": "12486", "mi": "20569"},
    "관곡소식학부모회(규정)":   {"bbsId": "12487", "mi": "20570"},
    "관곡소식학부모회(구성)":   {"bbsId": "12488", "mi": "20571"},
    "관곡소식학부모회(운영)":   {"bbsId": "12489", "mi": "20572"},
}

# 추적할 콘텐츠 페이지 (잘 안 바뀌지만 변경 감지용)
PAGES = {
    "메인페이지": f"{BASE_URL}/main.do",
    "학교일정":   f"{BASE_URL}/ps/schdul/selectSchdulMainList.do?mi=19567",
    "급식식단":   f"{BASE_URL}/ad/fm/foodmenu/selectFoodMenuView.do?mi=19574",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "SchoolRSSBot/1.0 (change-tracker)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


def parse_date(raw: str) -> str:
    """'등록일2024.04.03' 같은 형식에서 '2024-04-03'을 추출한다."""
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict:
    state_file = DATA_DIR / "state.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {"boards": {}, "pages": {}}


def save_state(state: dict):
    state_file = DATA_DIR / "state.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def fetch_board(bbs_id: str, count: int = 20) -> list[dict]:
    """게시판 글 목록을 가져온다."""
    url = f"{BASE_URL}/na/ntt/selectNttList.do"
    params = {"bbsId": bbs_id, "pageIndex": "1"}
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [오류] 게시판 {bbs_id} 조회 실패: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []

    for row in soup.select("table tbody tr, div.bbs_list ul li, div.board_list ul li"):
        # 제목 링크: .nttInfoBtn 클래스 + data-id 속성으로 글 번호 전달
        link = row.select_one("a.nttInfoBtn, a[data-id], a[href]")
        if not link:
            continue

        title = link.get_text(strip=True)
        ntt_sn = link.get("data-id", "")

        # data-id가 있으면 상세 URL 생성, 없으면 href 사용
        if ntt_sn:
            item_link = f"{BASE_URL}/na/ntt/selectNttInfo.do?mi=&bbsId={bbs_id}&nttSn={ntt_sn}"
        else:
            href = link.get("href", "")
            item_link = href if href.startswith("http") else f"{BASE_URL}{href}" if href.startswith("/") else ""

        # 날짜 추출
        date_text = ""
        date_cell = row.select_one("td.date, td:nth-of-type(4), span.date, span.bbs_date")
        if date_cell:
            date_text = date_cell.get_text(strip=True)

        if title:
            items.append({
                "title": title,
                "link": item_link,
                "date": date_text,
                "id": hashlib.md5(f"{bbs_id}:{title}:{date_text}".encode()).hexdigest()[:12],
            })

    return items[:count]


def fetch_page_hash(url: str) -> str:
    """페이지 콘텐츠의 해시값을 반환한다."""
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # 스크립트, 스타일, 방문자 카운터 등 동적 요소 제거
        for tag in soup.select("script, style, .visit_count, .today_count, #footer"):
            tag.decompose()
        text = soup.get_text(strip=True)
        return hashlib.sha256(text.encode()).hexdigest()
    except requests.RequestException as e:
        print(f"  [오류] 페이지 조회 실패 ({url}): {e}")
        return ""


def check_changes(state: dict) -> list[dict]:
    """모든 게시판과 페이지를 체크하고 변경사항을 반환한다."""
    changes = []
    now = datetime.now(KST).isoformat()

    # 1) 게시판 새 글 체크
    for name, info in BOARDS.items():
        print(f"[체크] 게시판: {name} (bbsId={info['bbsId']})")
        items = fetch_board(info["bbsId"])
        known_ids = set(state["boards"].get(name, {}).get("ids", []))

        new_items = [item for item in items if item["id"] not in known_ids]
        if new_items:
            print(f"  → 새 글 {len(new_items)}건 발견!")
            for item in new_items:
                changes.append({
                    "type": "new_post",
                    "board": name,
                    "title": item["title"],
                    "link": item["link"],
                    "date": item.get("date", ""),
                    "post_date": parse_date(item.get("date", "")),
                    "detected_at": now,
                })

        # 상태 업데이트 (현재 글 목록으로 교체)
        state["boards"][name] = {
            "ids": [item["id"] for item in items],
            "last_check": now,
            "count": len(items),
        }
        time.sleep(0.5)  # 서버 부하 방지

    # 2) 페이지 변경 체크
    for name, url in PAGES.items():
        print(f"[체크] 페이지: {name}")
        new_hash = fetch_page_hash(url)
        if not new_hash:
            continue

        old_hash = state["pages"].get(name, {}).get("hash", "")
        if old_hash and old_hash != new_hash:
            print(f"  → 변경 감지!")
            changes.append({
                "type": "page_changed",
                "board": name,
                "title": f"[페이지 변경] {name}",
                "link": url,
                "date": "",
                "post_date": datetime.now(KST).strftime("%Y-%m-%d"),
                "detected_at": now,
            })

        state["pages"][name] = {"hash": new_hash, "last_check": now}
        time.sleep(1)

    return changes


def generate_rss(changes: list[dict], existing_items: list[dict] | None = None):
    """RSS 2.0 XML 피드를 생성한다."""
    all_items = (existing_items or []) + changes

    # 최근 6개월 이내 항목만 유지 (게시 등록일 기준)
    six_months_ago = (datetime.now(KST) - timedelta(days=180)).strftime("%Y-%m-%d")
    all_items = [
        item for item in all_items
        if (item.get("post_date") or item.get("detected_at", "")[:10]) >= six_months_ago
    ]

    # 게시 등록일 기준 최신순 정렬
    all_items.sort(
        key=lambda x: x.get("post_date") or x.get("detected_at", "")[:10],
        reverse=True,
    )

    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = "관곡초등학교 변경 추적"
    SubElement(channel, "link").text = f"{BASE_URL}/main.do"
    SubElement(channel, "description").text = "관곡초등학교 웹사이트 게시판 및 페이지 변경사항"
    SubElement(channel, "language").text = "ko"
    SubElement(channel, "lastBuildDate").text = datetime.now(KST).strftime(
        "%a, %d %b %Y %H:%M:%S %z"
    )

    for item_data in all_items:
        item = SubElement(channel, "item")
        board = item_data.get("board", "")
        title = item_data.get("title", "")
        SubElement(item, "title").text = f"[{board}] {title}" if board else title
        if item_data.get("link"):
            SubElement(item, "link").text = item_data["link"]
        SubElement(item, "description").text = (
            f"게시판: {board} | 감지: {item_data.get('detected_at', '')}"
        )
        SubElement(item, "guid", isPermaLink="false").text = hashlib.md5(
            json.dumps(item_data, sort_keys=True).encode()
        ).hexdigest()
        if item_data.get("detected_at"):
            SubElement(item, "pubDate").text = item_data["detected_at"]

    indent(rss, space="  ")
    tree = ElementTree(rss)
    tree.write(RSS_FILE, encoding="unicode", xml_declaration=True)
    print(f"\n[RSS] {RSS_FILE} 생성 완료 (항목 {len(all_items)}개)")
    return all_items


def load_history() -> list[dict]:
    history_file = DATA_DIR / "history.json"
    if history_file.exists():
        return json.loads(history_file.read_text())
    return []


def save_history(items: list[dict]):
    history_file = DATA_DIR / "history.json"
    history_file.write_text(json.dumps(items, ensure_ascii=False, indent=2))


def main():
    print("=" * 50)
    print(f"관곡초등학교 변경 추적 시작 - {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    ensure_dirs()
    state = load_state()
    history = load_history()

    is_first_run = not state.get("boards") and not state.get("pages")

    changes = check_changes(state)
    save_state(state)

    if is_first_run:
        print(f"\n[초기화] 첫 실행 — 현재 상태를 기준점으로 저장했습니다.")
        print(f"  게시판 {len(BOARDS)}개, 페이지 {len(PAGES)}개 등록 완료")
        print(f"  다음 실행부터 변경사항이 감지됩니다.")
        # 첫 실행에서도 현재 글 목록을 RSS에 넣어줌
        all_items = generate_rss(changes, history)
        save_history(all_items)
    elif changes:
        print(f"\n[결과] 변경사항 {len(changes)}건 감지!")
        for c in changes:
            print(f"  • [{c['board']}] {c['title']}")
        all_items = generate_rss(changes, history)
        save_history(all_items)
    else:
        print("\n[결과] 변경사항 없음")
        generate_rss([], history)

    print("완료!\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""관곡초등학교 웹사이트 변경 추적 & RSS 생성기"""

import hashlib
import json
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

# 추적할 게시판 목록
BOARDS = {
    "공지사항":       {"bbsId": "11818", "mi": "20550"},
    "가정통신문":     {"bbsId": "11819", "mi": ""},
    "학교앨범":       {"bbsId": "11822", "mi": "19569"},
    "안전교육":       {"bbsId": "11823", "mi": ""},
    "급식정보실":     {"bbsId": "11825", "mi": ""},
    "칭찬축하합니다": {"bbsId": "11840", "mi": ""},
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

    # 학교 CMS 게시판은 보통 테이블 또는 리스트 형태
    # 테이블 형태 파싱
    for row in soup.select("table tbody tr, div.bbs_list ul li, div.board_list ul li"):
        link = row.select_one("a[href]")
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link.get("href", "")

        # 날짜 추출
        date_text = ""
        date_cell = row.select_one("td.date, td:nth-of-type(4), span.date, span.bbs_date")
        if date_cell:
            date_text = date_cell.get_text(strip=True)

        if title:
            items.append({
                "title": title,
                "link": href if href.startswith("http") else f"{BASE_URL}{href}" if href.startswith("/") else "",
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
                    "detected_at": now,
                })

        # 상태 업데이트 (현재 글 목록으로 교체)
        state["boards"][name] = {
            "ids": [item["id"] for item in items],
            "last_check": now,
            "count": len(items),
        }
        time.sleep(1)  # 서버 부하 방지

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
                "detected_at": now,
            })

        state["pages"][name] = {"hash": new_hash, "last_check": now}
        time.sleep(1)

    return changes


def generate_rss(changes: list[dict], existing_items: list[dict] | None = None):
    """RSS 2.0 XML 피드를 생성한다."""
    all_items = (existing_items or []) + changes
    # 최근 100개만 유지
    all_items = all_items[-100:]

    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = "관곡초등학교 변경 추적"
    SubElement(channel, "link").text = f"{BASE_URL}/main.do"
    SubElement(channel, "description").text = "관곡초등학교 웹사이트 게시판 및 페이지 변경사항"
    SubElement(channel, "language").text = "ko"
    SubElement(channel, "lastBuildDate").text = datetime.now(KST).strftime(
        "%a, %d %b %Y %H:%M:%S %z"
    )

    for item_data in reversed(all_items):
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

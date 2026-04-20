#!/usr/bin/env python3
"""관곡초등학교 웹사이트 변경 추적 & RSS 생성기"""

import hashlib
import json
import logging
import re
import smtplib
import time
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
DATA_DIR = Path(__file__).parent / "data"
RSS_FILE = Path(__file__).parent / "feed.xml"
CONFIG_FILE = Path(__file__).parent / "config.json"

log = logging.getLogger("school_rss")

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


def load_history() -> list[dict]:
    history_file = DATA_DIR / "history.json"
    if history_file.exists():
        return json.loads(history_file.read_text())
    return []


def save_history(items: list[dict]):
    history_file = DATA_DIR / "history.json"
    history_file.write_text(json.dumps(items, ensure_ascii=False, indent=2))


def fetch_board(base_url: str, bbs_id: str, count: int = 20) -> list[dict]:
    """게시판 글 목록을 가져온다."""
    url = f"{base_url}/na/ntt/selectNttList.do"
    params = {"bbsId": bbs_id, "pageIndex": "1"}
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"게시판 {bbs_id} 조회 실패: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []

    for row in soup.select("table tbody tr, div.bbs_list ul li, div.board_list ul li"):
        link = row.select_one("a.nttInfoBtn, a[data-id], a[href]")
        if not link:
            continue

        title = link.get_text(strip=True)
        ntt_sn = link.get("data-id", "")

        if ntt_sn:
            item_link = f"{base_url}/na/ntt/selectNttInfo.do?mi=&bbsId={bbs_id}&nttSn={ntt_sn}"
        else:
            href = link.get("href", "")
            item_link = href if href.startswith("http") else f"{base_url}{href}" if href.startswith("/") else ""

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
        for tag in soup.select("script, style, .visit_count, .today_count, #footer"):
            tag.decompose()
        text = soup.get_text(strip=True)
        return hashlib.sha256(text.encode()).hexdigest()
    except requests.RequestException as e:
        log.warning(f"페이지 조회 실패 ({url}): {e}")
        return ""


def check_changes(cfg: dict, state: dict) -> list[dict]:
    """config 기반으로 모든 사이트의 게시판과 페이지를 체크한다."""
    changes = []
    now = datetime.now(KST).isoformat()

    for site in cfg.get("sites", []):
        if not site.get("enabled", True):
            continue

        base_url = site["base_url"]
        site_name = site["name"]

        # 게시판 체크
        for board in site.get("boards", []):
            if not board.get("enabled", True):
                continue

            name = f"{site_name}/{board['name']}"
            bbs_id = board["bbsId"]
            log.info(f"[체크] {name} (bbsId={bbs_id})")

            items = fetch_board(base_url, bbs_id)
            known_ids = set(state["boards"].get(name, {}).get("ids", []))

            new_items = [item for item in items if item["id"] not in known_ids]
            if new_items:
                log.info(f"  → 새 글 {len(new_items)}건!")
                for item in new_items:
                    changes.append({
                        "type": "new_post",
                        "site": site_name,
                        "board": board["name"],
                        "title": item["title"],
                        "link": item["link"],
                        "date": item.get("date", ""),
                        "post_date": parse_date(item.get("date", "")),
                        "detected_at": now,
                    })

            state["boards"][name] = {
                "ids": [item["id"] for item in items],
                "last_check": now,
                "count": len(items),
            }
            time.sleep(0.5)

        # 페이지 체크
        for page in site.get("pages", []):
            if not page.get("enabled", True):
                continue

            name = f"{site_name}/{page['name']}"
            page_url = page["url"]
            if not page_url.startswith("http"):
                page_url = base_url + page_url

            log.info(f"[체크] 페이지: {name}")
            new_hash = fetch_page_hash(page_url)
            if not new_hash:
                continue

            old_hash = state["pages"].get(name, {}).get("hash", "")
            if old_hash and old_hash != new_hash:
                log.info(f"  → 변경 감지!")
                changes.append({
                    "type": "page_changed",
                    "site": site_name,
                    "board": page["name"],
                    "title": f"[페이지 변경] {page['name']}",
                    "link": page_url,
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

    six_months_ago = (datetime.now(KST) - timedelta(days=180)).strftime("%Y-%m-%d")
    all_items = [
        item for item in all_items
        if (item.get("post_date") or item.get("detected_at", "")[:10]) >= six_months_ago
    ]

    all_items.sort(
        key=lambda x: x.get("post_date") or x.get("detected_at", "")[:10],
        reverse=True,
    )

    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = "사이트 변경 추적"
    SubElement(channel, "link").text = ""
    SubElement(channel, "description").text = "웹사이트 게시판 및 페이지 변경사항"
    SubElement(channel, "language").text = "ko"
    SubElement(channel, "lastBuildDate").text = datetime.now(KST).strftime(
        "%a, %d %b %Y %H:%M:%S %z"
    )

    for item_data in all_items:
        item = SubElement(channel, "item")
        board = item_data.get("board", "")
        site = item_data.get("site", "")
        title = item_data.get("title", "")
        label = f"[{site}/{board}]" if site else f"[{board}]"
        SubElement(item, "title").text = f"{label} {title}" if board else title
        if item_data.get("link"):
            SubElement(item, "link").text = item_data["link"]
        SubElement(item, "description").text = (
            f"사이트: {site} | 게시판: {board} | 등록일: {item_data.get('post_date', '')}"
        )
        SubElement(item, "guid", isPermaLink="false").text = hashlib.md5(
            json.dumps(item_data, sort_keys=True).encode()
        ).hexdigest()
        post_date = item_data.get("post_date", "")
        if post_date:
            SubElement(item, "pubDate").text = post_date
        elif item_data.get("detected_at"):
            SubElement(item, "pubDate").text = item_data["detected_at"][:10]

    indent(rss, space="  ")
    tree = ElementTree(rss)
    tree.write(RSS_FILE, encoding="unicode", xml_declaration=True)
    log.info(f"[RSS] feed.xml 생성 완료 (항목 {len(all_items)}개)")
    return all_items


# ── Email ─────────────────────────────────────────────

def send_email(email_cfg: dict, subject: str, html_body: str):
    """SMTP로 메일을 발송한다."""
    recipients = [r.strip() for r in email_cfg["recipient"].split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{email_cfg.get('sender_name', 'RSS')} <{email_cfg['username']}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    server = smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"])
    server.ehlo()
    server.starttls()
    server.login(email_cfg["username"], email_cfg["password"])
    server.sendmail(email_cfg["username"], recipients, msg.as_string())
    server.quit()
    log.info(f"메일 발송 완료 → {email_cfg['recipient']}")


def build_change_email(changes: list[dict]) -> str:
    """변경사항을 HTML 메일 본문으로 만든다."""
    rows = ""
    for c in changes:
        link = c.get("link", "")
        title = c.get("title", "")
        cell = f'<a href="{link}">{title}</a>' if link else title
        rows += f"""<tr>
            <td style="padding:6px 10px;border-bottom:1px solid #eee;font-size:13px;color:#888;">{c.get('post_date','')}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #eee;font-size:13px;">
                <span style="background:#e8f5e9;color:#2e7d32;padding:2px 6px;border-radius:3px;font-size:11px;">{c.get('board','')}</span>
                {cell}
            </td>
        </tr>"""

    return f"""
    <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;">
        <h2 style="color:#2c5f2d;">사이트 변경 알림 ({len(changes)}건)</h2>
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr>
                <th style="text-align:left;padding:8px 10px;background:#f5f5f5;font-size:12px;">등록일</th>
                <th style="text-align:left;padding:8px 10px;background:#f5f5f5;font-size:12px;">내용</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>
        <p style="margin-top:16px;font-size:11px;color:#999;">자동 발송 메일입니다.</p>
    </div>"""


# ── Main entry (config-based) ─────────────────────────

def run_crawl(cfg: dict) -> list[dict]:
    """config 기반 크롤링 실행. app.py 및 standalone 모두에서 호출."""
    log.info(f"크롤링 시작 - {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")

    ensure_dirs()
    state = load_state()
    history = load_history()
    is_first_run = not state.get("boards") and not state.get("pages")

    changes = check_changes(cfg, state)
    save_state(state)

    all_items = generate_rss(changes, history)
    save_history(all_items)

    if is_first_run:
        log.info("첫 실행 — 기준점 저장 완료")
    elif changes:
        log.info(f"변경사항 {len(changes)}건 감지")

        # 메일 발송
        email_cfg = cfg.get("email", {})
        if email_cfg.get("enabled") and email_cfg.get("username") and email_cfg.get("recipient"):
            try:
                subject = f"[변경알림] {len(changes)}건 새 글 감지"
                body = build_change_email(changes)
                send_email(email_cfg, subject, body)
            except Exception as e:
                log.error(f"메일 발송 실패: {e}")
    else:
        log.info("변경사항 없음")

    return changes


def main():
    """standalone 실행 (config.json 사용)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())
    else:
        log.error("config.json이 없습니다")
        return

    run_crawl(cfg)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""관곡초등학교 변경 추적 — 관리 서버 (Flask + APScheduler)"""

import base64
import json
import logging
import os
import sys
from pathlib import Path
from threading import Lock

import requests as http_req

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request, send_from_directory

from school_rss import run_crawl

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

app = Flask(__name__)
scheduler = BackgroundScheduler()
config_lock = Lock()
log = logging.getLogger("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Config ────────────────────────────────────────────

def load_config() -> dict:
    with config_lock:
        return json.loads(CONFIG_FILE.read_text())


def save_config(cfg: dict):
    with config_lock:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


# ── Scheduled job ─────────────────────────────────────

def scheduled_crawl():
    """스케줄러에서 호출되는 크롤링 작업."""
    log.info("=== 스케줄 크롤링 시작 ===")
    cfg = load_config()
    changes = run_crawl(cfg)

    # GitHub push
    gh = cfg.get("github", {})
    if gh.get("enabled") and gh.get("token"):
        try:
            git_push(gh)
        except Exception as e:
            log.error(f"GitHub push 실패: {e}")

    log.info(f"=== 크롤링 완료: {len(changes)}건 변경 ===")


def gh_api(gh_cfg: dict, method: str, path: str, json_data: dict = None) -> dict:
    """GitHub REST API 호출 헬퍼."""
    url = f"https://api.github.com/repos/{gh_cfg['repo']}/{path}".rstrip("/")
    headers = {
        "Authorization": f"token {gh_cfg['token']}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = http_req.request(method, url, headers=headers, json=json_data, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def gh_upload_file(gh_cfg: dict, file_path: str, repo_path: str):
    """GitHub API로 파일 하나를 업로드 (create or update)."""
    content = Path(file_path).read_bytes()
    encoded = base64.b64encode(content).decode()
    branch = gh_cfg.get("branch", "main")

    # 기존 파일의 sha 확인 (업데이트 시 필요)
    sha = None
    try:
        existing = gh_api(gh_cfg, "GET", f"contents/{repo_path}?ref={branch}")
        sha = existing.get("sha")
    except http_req.HTTPError:
        pass  # 파일이 없으면 새로 생성

    data = {
        "message": f"auto: update {repo_path}",
        "content": encoded,
        "branch": branch,
    }
    if sha:
        data["sha"] = sha

    gh_api(gh_cfg, "PUT", f"contents/{repo_path}", data)


def git_push(gh_cfg: dict):
    """feed.xml을 GitHub API로 업로드."""
    feed_path = BASE_DIR / "feed.xml"
    if not feed_path.exists():
        log.warning("feed.xml이 없어 push 건너뜀")
        return

    gh_upload_file(gh_cfg, str(feed_path), "feed.xml")
    log.info("GitHub API push 성공: feed.xml")


def gh_test_connection(gh_cfg: dict) -> dict:
    """GitHub 토큰/repo 접근 테스트."""
    try:
        repo_info = gh_api(gh_cfg, "GET", "")
        return {
            "status": "ok",
            "repo": repo_info.get("full_name"),
            "private": repo_info.get("private"),
            "permissions": repo_info.get("permissions", {}),
        }
    except http_req.HTTPError as e:
        return {"status": "error", "message": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def reschedule():
    """config의 interval에 맞게 스케줄러를 재설정."""
    cfg = load_config()
    hours = cfg.get("crawl_interval_hours", 12)

    # 기존 job 제거
    if scheduler.get_job("crawl"):
        scheduler.remove_job("crawl")

    scheduler.add_job(scheduled_crawl, "interval", hours=hours, id="crawl",
                      misfire_grace_time=3600)
    log.info(f"스케줄 설정: {hours}시간 간격")


# ── API ───────────────────────────────────────────────

@app.route("/")
def admin_page():
    return send_from_directory(BASE_DIR / "templates", "admin.html")


@app.route("/viewer")
def viewer_page():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/feed.xml")
def feed_xml():
    return send_from_directory(BASE_DIR, "feed.xml", mimetype="application/rss+xml")


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = load_config()
    # 비밀번호/토큰은 마스킹
    safe = json.loads(json.dumps(cfg))
    if safe.get("email", {}).get("password"):
        safe["email"]["password"] = "********"
    if safe.get("github", {}).get("token"):
        safe["github"]["token"] = "********"
    return jsonify(safe)


@app.route("/api/config", methods=["POST"])
def update_config():
    new_cfg = request.json
    old_cfg = load_config()

    # 마스킹된 비밀번호/토큰은 기존 값 유지
    if new_cfg.get("email", {}).get("password") == "********":
        new_cfg["email"]["password"] = old_cfg.get("email", {}).get("password", "")
    if new_cfg.get("github", {}).get("token") == "********":
        new_cfg["github"]["token"] = old_cfg.get("github", {}).get("token", "")

    save_config(new_cfg)
    reschedule()
    return jsonify({"status": "ok"})


@app.route("/api/crawl", methods=["POST"])
def trigger_crawl():
    """수동 크롤링 실행."""
    cfg = load_config()
    changes = run_crawl(cfg)
    return jsonify({"status": "ok", "changes": len(changes)})


@app.route("/api/status", methods=["GET"])
def status():
    job = scheduler.get_job("crawl")
    next_run = str(job.next_run_time) if job else None
    state_file = BASE_DIR / "data" / "state.json"
    last_check = None
    if state_file.exists():
        state = json.loads(state_file.read_text())
        checks = [v.get("last_check", "") for v in state.get("boards", {}).values()]
        if checks:
            last_check = max(checks)
    return jsonify({
        "next_run": next_run,
        "last_check": last_check,
        "scheduler_running": scheduler.running,
    })


@app.route("/api/test-email", methods=["POST"])
def test_email():
    """테스트 메일 발송."""
    cfg = load_config()
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("username") or not email_cfg.get("recipient"):
        return jsonify({"status": "error", "message": "메일 설정이 없습니다"}), 400

    from school_rss import send_email
    try:
        send_email(email_cfg, "테스트 메일", "<h3>관곡초 변경추적 테스트</h3><p>메일 발송이 정상 작동합니다.</p>")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/update", methods=["POST"])
def update_app():
    """GitHub에서 최신 코드를 다운받아 파일 교체 후 재시작."""
    cfg = load_config()
    gh_cfg = cfg.get("github", {})
    if not gh_cfg.get("token") or not gh_cfg.get("repo"):
        return jsonify({"status": "error", "message": "GitHub 설정이 필요합니다"}), 400

    branch = gh_cfg.get("branch", "main")
    headers = {
        "Authorization": f"token {gh_cfg['token']}",
        "Accept": "application/vnd.github.v3+json",
    }

    # 업데이트 대상 파일 목록
    update_files = [
        "app.py", "school_rss.py", "requirements.txt",
        "Dockerfile", "docker-compose.yml",
        "index.html", "templates/admin.html",
    ]

    updated = []
    errors = []
    for file_path in update_files:
        try:
            url = f"https://api.github.com/repos/{gh_cfg['repo']}/contents/{file_path}?ref={branch}"
            resp = http_req.get(url, headers=headers, timeout=15)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            content = base64.b64decode(resp.json()["content"])
            local_path = BASE_DIR / file_path
            local_path.parent.mkdir(parents=True, exist_ok=True)

            # 변경 있을 때만 덮어쓰기
            if local_path.exists() and local_path.read_bytes() == content:
                continue

            local_path.write_bytes(content)
            updated.append(file_path)
        except Exception as e:
            errors.append(f"{file_path}: {e}")

    if not updated:
        return jsonify({"status": "ok", "message": "이미 최신 버전입니다", "updated": []})

    log.info(f"업데이트 완료: {updated}")

    # 응답을 먼저 보낸 후 재시작 (Docker restart: unless-stopped가 다시 띄워줌)
    import threading
    def restart():
        import time
        time.sleep(1)
        log.info("재시작합니다...")
        os._exit(0)  # Docker가 재시작해줌

    threading.Thread(target=restart, daemon=True).start()

    return jsonify({
        "status": "ok",
        "message": f"{len(updated)}개 파일 업데이트, 재시작 중...",
        "updated": updated,
        "errors": errors,
    })


@app.route("/api/test-github", methods=["POST"])
def test_github():
    """GitHub 연결 테스트."""
    cfg = load_config()
    gh_cfg = cfg.get("github", {})
    if not gh_cfg.get("token") or not gh_cfg.get("repo"):
        return jsonify({"status": "error", "message": "GitHub 설정이 없습니다"}), 400
    result = gh_test_connection(gh_cfg)
    return jsonify(result), 200 if result["status"] == "ok" else 400


# ── Main ──────────────────────────────────────────────

if __name__ == "__main__":
    scheduler.start()
    reschedule()
    # 초기 1회 실행
    log.info("서버 시작, 초기 크롤링 실행")
    scheduled_crawl()
    app.run(host="0.0.0.0", port=5000)

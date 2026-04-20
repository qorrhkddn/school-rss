#!/usr/bin/env python3
"""관곡초등학교 변경 추적 — 관리 서버 (Flask + APScheduler)"""

import json
import logging
import subprocess
from pathlib import Path
from threading import Lock

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


def git_push(gh_cfg: dict):
    """feed.xml 등을 GitHub에 push."""
    env = {"GIT_TERMINAL_PROMPT": "0"}
    repo_url = f"https://x-access-token:{gh_cfg['token']}@github.com/{gh_cfg['repo']}.git"

    cmds = [
        ["git", "add", "feed.xml", "data/"],
        ["git", "diff", "--cached", "--quiet"],  # 변경 없으면 exit 1
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, env=env)
        if cmd[1] == "diff" and r.returncode == 0:
            log.info("GitHub: 변경 없음, push 건너뜀")
            return

    subprocess.run(
        ["git", "commit", "-m", "auto: update feed"],
        cwd=BASE_DIR, capture_output=True, env=env,
    )
    subprocess.run(
        ["git", "remote", "set-url", "origin", repo_url],
        cwd=BASE_DIR, capture_output=True, env=env,
    )
    result = subprocess.run(
        ["git", "push", "origin", gh_cfg.get("branch", "main")],
        cwd=BASE_DIR, capture_output=True, text=True, env=env,
    )
    if result.returncode == 0:
        log.info("GitHub push 성공")
    else:
        log.error(f"GitHub push 실패: {result.stderr}")


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


# ── Main ──────────────────────────────────────────────

if __name__ == "__main__":
    scheduler.start()
    reschedule()
    # 초기 1회 실행
    log.info("서버 시작, 초기 크롤링 실행")
    scheduled_crawl()
    app.run(host="0.0.0.0", port=5000)

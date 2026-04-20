# school-rss

웹사이트 변경 추적 및 RSS 피드 생성기. 다중 사이트 지원, 메일 알림, 관리 UI 포함.

## 구조

```
app.py                  # Flask 웹서버 (관리 UI + API + 스케줄러)
school_rss.py           # 크롤러 + RSS 생성 + 메일 발송 (라이브러리)
config.json             # 모든 설정 (사이트, 메일, GitHub, 주기)
index.html              # 피드 뷰어 (GitHub Pages 서빙)
templates/admin.html    # 관리 페이지 (Docker에서 서빙)
feed.xml                # 생성된 RSS 2.0 피드
data/state.json         # 마지막 체크 상태
data/history.json       # 변경 이력
Dockerfile              # Docker 이미지
docker-compose.yml      # Synology NAS 배포용
```

## 아키텍처

```
GitHub Pages (정적)          Synology NAS (Docker)
┌─────────────────┐         ┌──────────────────────┐
│ index.html      │         │ app.py (Flask:5000)   │
│ feed.xml        │ ← push  │  ├ 관리 UI (:5100)    │
│                 │         │  ├ APScheduler 주기실행│
│                 │         │  ├ 크롤링 → feed.xml   │
│                 │         │  └ 변경 시 메일 발송   │
└─────────────────┘         └──────────────────────┘
```

## 규칙

### config.json 구조
- `crawl_interval_hours`: 크롤링 주기 (시간)
- `email`: SMTP 설정 (server, port, username, password, recipient, sender_name, enabled)
- `github`: Pages 연동 (repo, branch, token, enabled)
- `sites[]`: 크롤링 대상 사이트 배열
  - 각 사이트: `name`, `base_url`, `enabled`, `boards[]`, `pages[]`
  - 각 게시판: `name`, `bbsId`, `mi`, `enabled`
- 게시판/사이트 추가/삭제는 관리 UI 또는 config.json 직접 편집

### 크롤링
- 게시판: `selectNttList.do?bbsId=` 조회, `data-id` 속성에서 `nttSn` 추출 → `selectNttInfo.do` URL 생성
- 서버 부하 방지: 게시판 간 0.5초, 페이지 간 1초 sleep
- `run_crawl(cfg)` 함수로 app.py와 standalone 모두에서 호출 가능

### 날짜
- 게시글 등록일: `등록일2025.04.17` → `parse_date()` → `2025-04-17`
- RSS pubDate, 필터, 정렬 모두 **게시 등록일 기준**
- 6개월 이내 항목만 유지

### 메일 알림
- 변경 감지 시 HTML 메일 자동 발송 (email.enabled=true일 때)
- `send_email()` / `build_change_email()` 함수
- 관리 UI에서 테스트 메일 발송 가능

### 관리 페이지 (templates/admin.html)
- Flask 서버의 `/` 경로로 접근
- API: `/api/config` (GET/POST), `/api/crawl` (POST), `/api/status` (GET), `/api/test-email` (POST)
- 비밀번호/토큰은 API 응답에서 마스킹, 저장 시 마스킹 값이면 기존 값 유지

### Docker 배포 (Synology NAS)
- `docker compose up -d` 로 실행
- 포트: 5100 (호스트) → 5000 (컨테이너)
- Volume: config.json, data/, feed.xml
- TZ=Asia/Seoul

### GitHub Pages 배포
- Public repo: https://qorrhkddn.github.io/school-rss/
- `.github/workflows/pages.yml` — push 시 자동 배포
- Docker 컨테이너가 크롤링 후 자동 git push (github.enabled=true일 때)

## Changelog

### 2026-04-20
- 초기 구현: 6개 게시판 크롤링 + RSS 생성
- 53개 전체 게시판으로 확장
- 게시 등록일 기준 6개월 필터 + 최신순 정렬
- `data-id` 속성 기반 게시글 링크 생성
- index.html 웹 뷰어 (검색, 필터, 읽음 상태)
- 크롤링 대상 게시판 펼침 목록
- GitHub Pages + crontab 배포
- **config.json 기반 다중 사이트 지원으로 리팩터링**
- **Flask 관리 서버 (app.py) + 관리 UI (admin.html)**
- **SMTP 메일 알림 (변경 감지 시 자동 발송)**
- **Docker / docker-compose 지원 (Synology NAS)**
- **APScheduler로 크롤링 주기 관리 (관리 UI에서 변경 가능)**

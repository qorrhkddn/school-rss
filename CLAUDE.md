# school-rss

관곡초등학교(https://gwan-gok-e.goeyi.kr/gwan-gok-e/main.do) 웹사이트 변경 추적 및 RSS 피드 생성기.

## 구조

```
school_rss.py      # 크롤러 + RSS 생성 (메인 스크립트)
index.html         # 웹 뷰어 (GitHub Pages로 서빙)
feed.xml           # 생성된 RSS 2.0 피드
data/state.json    # 마지막 체크 상태 (글 ID 해시, 페이지 해시)
data/history.json  # 변경 이력 (RSS 항목 원본)
```

## 규칙

### 크롤링
- 대상: `BOARDS` dict에 정의된 53개 게시판 + `PAGES` dict의 3개 페이지
- 게시판은 `selectNttList.do?bbsId=` 로 조회, 글 링크는 `data-id` 속성에서 `nttSn` 추출하여 `selectNttInfo.do` URL 생성
- 새 게시판 추가 시 `BOARDS` dict에 `bbsId`와 `mi` 추가 + `index.html`의 펼침 목록에도 반영
- 서버 부하 방지: 게시판 간 0.5초, 페이지 간 1초 sleep

### 날짜
- 게시글 등록일 형식: `등록일2025.04.17` → `parse_date()`로 `2025-04-17` 추출
- RSS `pubDate`, HTML 날짜 표시, 6개월 필터, 정렬 모두 **게시 등록일 기준** (감지 시각 아님)
- 페이지 변경 항목은 감지일을 post_date로 사용

### RSS 피드
- 최근 6개월 이내 항목만 유지 (게시 등록일 기준)
- 최신순 정렬
- `feed.xml`로 출력

### HTML 뷰어 (index.html)
- feed.xml을 fetch하여 클라이언트에서 렌더링
- 검색, 게시판 필터, 읽음 상태 추적 (localStorage)
- 크롤링 대상 게시판 목록을 펼침(`<details>`)으로 표시
- 최종 업데이트 시각 헤더에 표시

### 배포
- GitHub Pages (public repo): https://qorrhkddn.github.io/school-rss/
- `.github/workflows/pages.yml`로 push 시 자동 배포
- crontab으로 12시간 간격(08:23, 20:23) 실행 후 자동 commit & push

## Changelog

### 2026-04-20
- 초기 구현: 6개 게시판 크롤링 + RSS 생성
- 53개 전체 게시판으로 확장
- 게시 등록일 기준 6개월 필터 + 최신순 정렬
- `data-id` 속성 기반 게시글 링크 생성
- index.html 웹 뷰어 추가 (검색, 필터, 읽음 상태)
- pubDate를 게시 등록일로 변경
- 최종 업데이트 시각 표시
- 크롤링 대상 게시판 펼침 목록 추가
- GitHub Pages 배포 + crontab(12h) 설정

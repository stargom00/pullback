# 포지션 보드 — 토스 잔고 동기화 설정 (v5.103)

## 아키텍처

```
[맥 로컬] sync_toss.py --(launchd, 30분 간격)--> TossClient(조회전용)
                              |
                              v  X-Sync-Token 헤더
                     POST /api/positions/sync
                              |
                              v
                  [Railway] positions.json (볼륨)
                              |
                              v  GET /api/positions
                  가격은 서버가 그때그때 새로 조회해 결합
```

Railway는 토스 Open API를 직접 호출할 수 없다 — 토스가 "허용 IP"만 받는데
Railway 아웃바운드 IP가 배포마다 안 고정되기 때문. 그래서 조회는 항상
**맥 로컬**에서 하고, 서버로는 수량·평단 같은 최소 정보만 보낸다. 가격은
동기화 지연을 허용하면 손익이 부정확해지므로 서버가 매 GET 요청마다 직접
새로 조회한다.

## 1. 환경변수 설정

### 로컬 (`.env`, 이미 `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET` 있는 파일에 추가)

```
SYNC_TOKEN=<아래 명령으로 생성한 랜덤 토큰>
PULLBACK_SERVER_URL=https://pullback2-production.up.railway.app
```

토큰 생성:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Railway (대시보드 → 이 프로젝트 → Variables)

**같은 `SYNC_TOKEN` 값을 반드시 그대로 붙여넣을 것** — 서버가 이 값으로
요청을 검증한다(없거나 다르면 401). 이 단계는 Railway 대시보드 접근이
필요해 여기서 자동화할 수 없다 — 직접 추가해야 한다.

## 2. 수동 실행으로 먼저 확인

```bash
cd /Users/seulkicho/pullback
python3 sync_toss.py --force
```
`--force`는 장중 시간대 게이트를 건너뛰어 아무 때나 테스트할 수 있게 한다
(평소 launchd 자동 실행에서는 안 씀). 성공하면 `동기화 완료: N건 저장됨`
로그가 뜬다. 실패하면 `SYNC_TOKEN`/Railway 환경변수/IP 허용 목록부터 확인.

## 3. launchd 자동 실행 등록

plist는 **30분마다(하루 종일) 무조건 실행**하도록 단순하게 두고, "장중에만
실제로 동기화한다"는 판단은 `sync_toss.py` 안에서 한다(`_is_market_hours`).
이유: 미국장이 KST 자정을 넘겨(전날 22시~다음날 6시반) 걸쳐 있어서 이걸
`StartCalendarInterval`에 시각 30여 개를 나열하는 방식으로 정확히 표현하려면
plist가 커지고 실수하기 쉽다 — 시간 판정 로직 하나를 파이썬에 두는 쪽이
읽기 쉽고 수정하기 쉽다. 장중이 아니면 스크립트가 즉시 스킵(로그만 남기고
종료)하므로 결과적으로 요구사항("장중 30분 간격, KR장+US장 커버")과 동일하게
동작한다. 맥이 꺼져 있으면 launchd 자체가 안 돌기 때문에 그냥 스킵된
것과 같은 결과다 — 별도 처리 불필요.

### plist 파일 생성

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.seulki.pullback.synctoss.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.seulki.pullback.synctoss</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Library/Frameworks/Python.framework/Versions/3.13/bin/python3</string>
        <string>/Users/seulkicho/pullback/sync_toss.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/seulkicho/pullback</string>
    <key>StartInterval</key>
    <integer>1800</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/Users/seulkicho/pullback/sync_toss.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/seulkicho/pullback/sync_toss.log</string>
</dict>
</plist>
EOF
```

`ProgramArguments`의 python3 경로가 실제 환경과 다르면 `which python3`로 확인
후 바꿀 것. `RunAtLoad`는 일부러 `false` — 등록/맥 재시작 직후 장중이
아닐 때 바로 한 번 도는 걸 막기 위함(30분 뒤 첫 실행부터 정상 게이트 적용).

### 등록 (사용자 확인 후 직접 실행)

```bash
launchctl unload ~/Library/LaunchAgents/com.seulki.pullback.synctoss.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.seulki.pullback.synctoss.plist
```

### 상태 확인 / 로그

```bash
launchctl list | grep synctoss
tail -f /Users/seulkicho/pullback/sync_toss.log
```

### 해제

```bash
launchctl unload ~/Library/LaunchAgents/com.seulki.pullback.synctoss.plist
rm ~/Library/LaunchAgents/com.seulki.pullback.synctoss.plist
```

## 4. 안전 원칙 (구현에 반영됨)

- `sync_toss.py`는 `toss_client.py`(조회 전용, 주문 엔드포인트 자체가 코드에
  없음)만 사용 — 이 스크립트에도 주문 관련 호출은 없다.
- 서버로 보내는 필드는 티커·종목명·시장·수량·평단·통화뿐 — 계좌번호 등
  식별정보는 애초에 추출하지 않는다(`_extract_positions()` 참고).
- `POST /api/positions/sync`는 `X-Sync-Token` 헤더가 `SYNC_TOKEN` 환경변수와
  일치하지 않으면(둘 중 하나라도 없어도) 401.
- 손절가(포지션 보드에서 입력)는 `positions_meta.json`이라는 별도 파일에
  저장되어 30분마다의 잔고 동기화가 절대 덮어쓰지 않는다.

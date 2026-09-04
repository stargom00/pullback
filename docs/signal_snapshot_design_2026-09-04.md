# 신호 스냅샷 설계 (2026-09-04, 설계만·구현 안 함)

> ①-a(돌파임박 손절 재정의 수정 방향)와 ①-b(나머지 4탭 저널 stop
> 쓰기 경로 조사)를 하나로 묶은 설계. 사용자 지시로 이 문서만 작성하고
> 구현은 별도 세션/작업으로 미룬다.

## 1. 문제

`2026-09-04_kr_confirm_entry_all_tabs_90cp.py` 측정(및 v5.166로 프로덕션에
반영된 `CONFIRM_RULE_BY_TAB`, `app.py:10049-10055`)의 안C EV는 **신호일
hist에서 계산한 stop**을 손절 분모로 쓴다(재계산 안 함, 코드로 확인
완료). 그런데 프로덕션 "오늘의 결정"이 실제로 저장·표시하는 stop은
**등록(클릭) 시점** 값이다 — 5탭 전부 같은 구조적 문제:

- **돌파임박(유일하게 자체 보정 있음)**: `app.py:10102-10107`이
  `_registration_day_low(df, r.get("date"))`(`app.py:9729-9748`)를 호출.
  이 함수 자체 docstring이 "감시 등록일(=근사 신호일 ... 신호
  최초감지일이 아니라 사용자가 관찰/감시 등록한 날짜)"이라고 스스로
  적어놓았다 — 등록 지연 시 다른 날짜.
- **나머지 4탭(눌림목/박스돌파/돌파/추세전환)**: `app.py:10093`
  `stop_f = r.get("stop")` — 저널에 저장된 값을 그대로 씀, 보정 자체가
  없음(더 조용한 형태의 같은 문제).

**저널에 `stop`이 쓰이는 경로** (5탭 공통, tab 무관):
1. 프론트 `static/index.html`의 `quickWatch()`(4725-4738, payload
   `stop: s.stop`는 4735줄)/`pivotChoiceEnterNow()`(4792줄)/
   `pivotChoiceCustomWait()`(4808줄)/재점화 전용
   `reignitionQuickWatch()`(1492줄, 이건 게이트형 5탭 대상 아님 — 아래
   3절 참고) — `s`는 `lastHits.find(...)`, 즉 **브라우저가 마지막으로
   받은 `/api/scan` 결과**. 등록 버튼을 신호 당일에 안 누르고 며칠 뒤
   누르면, 그 사이 스캔 캐시가 갱신되며 `analyze_*()`가 최신 봉 기준으로
   재계산한 `stop`/`pivot`이 실려 있을 수 있다.
2. 서버 `POST /api/watch/quick`(`app.py:10506`) — `stop = float(body.get
   ("stop"))`(10528줄 부근, 검증 없이 그대로 수용) → `rec["stop"] =
   stop`(10598줄), `rec["date"] = datetime.now(KST)...`(10587줄, **등록
   순간**, 신호 감지일 아님). 재계산 트리거 전혀 없음.

측정 EV(예: 돌파임박 1.062R, z=34.7)의 손절 분모와, 등록이 늦은
실사용자가 실제로 받는 손절 분모가 다른 값일 수 있다 — 측정 EV와 실행
EV가 다른 물건이 되는 지점.

## 2. 해결: 서버측 (ticker, tab) 최초감지 스냅샷

`run_scan()`이 게이트형 5탭 히트를 만들 때마다(신호가 **처음** 잡히는
순간) 그 신호의 원본 손절/피벗/저가를 서버에 영구 저장해두고, 이후
"오늘의 결정"과 등록(quickWatch) 양쪽이 이 스냅샷만 참조하게 한다 —
브라우저 캐시나 클릭 타이밍과 무관하게 항상 같은 값.

### 저장 스키마

```python
# {f"{ticker}|{tab}": {"signal_date": "YYYY-MM-DD", "stop": float,
#                       "pivot": float, "low": float,
#                       "last_seen_date": "YYYY-MM-DD"}}
```
- `tab`은 한글 탭명(`눌림목`/`돌파임박`/`박스돌파`/`돌파`/`추세전환`) —
  같은 티커가 여러 탭에서 동시에 잡힐 수 있으므로 복합키 필수.
- `low` = 신호일(=최초감지일) 저가(`df["Low"].iloc[-1]`) — 돌파임박의
  `_registration_day_low` 대체용. `stop`은 `analyze_*()`가 그날 반환한
  구조적 stop(나머지 4탭용).
- `last_seen_date` — 3절 에피소드 리셋 판정용(마지막으로 이 (ticker,tab)
  이 스캔 히트에 다시 나타난 날).

### 저장 위치

기존 `_index_last_good`/`journal_user.json` 패턴 재사용(`/data` 영구
볼륨, `os.path.dirname(JOURNAL_PATH)` 기준) — 신규 상수
`SIGNAL_SNAPSHOT_PATH = os.path.join(os.path.dirname(JOURNAL_PATH),
"signal_snapshot_cache.json")`. `_load_index_gate_cache`/
`_save_index_gate_cache`(`app.py:9394-9415`, gatefix 커밋에서 신설)와
완전히 같은 모양의 load/save 함수 한 쌍을 새로 만들면 됨(복붙 가능,
재사용 아님 — 다른 스토어라 별도 파일/변수 필요).

### 기록 지점

`run_scan()`(`app.py:6203`) 내부, 게이트형 5탭 히트가 `hits`에
적재되는 지점 — `app.py:6290` `hits.append({...})` 바로 앞뒤.
`mode`→탭명 매핑은 `app.py:8011-8012`에 이미 있는 것 재사용:
```python
{"pullback": "눌림목", "turnaround": "추세전환", "breakout": "돌파",
 "boxbreak": "박스돌파", "imminent": "돌파임박"}
```
`mode`가 이 5개 중 하나일 때만 스냅샷 기록(다른 모드는 대상 아님).
**이미 기록이 있고 3절의 리셋 조건에 해당 안 하면 갱신하지 않는다** —
"최초"라는 의미를 지키는 핵심 조건.

## 3. 에피소드 리셋 규칙

같은 (ticker, tab)이 몇 달 뒤 완전히 다른 셋업으로 다시 잡혔는데 옛
스냅샷을 계속 쓰면 무관한 손절이 재사용된다. 두 조건 중 하나라도
만족하면 "새 에피소드"로 보고 스냅샷을 덮어쓴다(둘 다 새 스냅샷 기록
시점에 판정):

- **(a) 5거래일 이상 연속 부재 후 재등장**: 스캔마다 (ticker,tab)이
  현재 히트에 있으면 `last_seen_date`를 오늘로 갱신. 히트에 없으면
  그대로 둠. 새로 히트에 잡혔을 때 `last_seen_date`와 오늘 사이 거래일
  간격이 5일 이상이면 리셋(주말 포함 달력일이 아니라 거래일 기준 —
  `_kr_market_open`류 캘린더 로직 재사용 필요, 아직 어느 함수로 셀지는
  미정).
- **(b) pivot ±3% 이상 변동**: 새로 잡힌 히트의 `result["pivot"]`이
  저장된 스냅샷의 `pivot` 대비 ±3% 넘게 다르면(구조적 저항선 자체가
  바뀐 것으로 간주) 리셋.

## 4. quickWatch 변경 — stop 전송 제거, 서버가 스냅샷에서 채움

- **프론트**(`static/index.html`): `quickWatch()`(4725-4738)의 payload
  (4735줄)에서 `stop: s.stop` 제거. `pivotChoiceEnterNow()`(4792줄),
  `pivotChoiceCustomWait()`(4808줄)도 동일. `reignitionQuickWatch()`
  (1492줄)는 게이트형 5탭이 아닌 재점화 감시라 스냅샷 대상 밖 — **변경
  안 함**(기존 `stop: s.stop` 그대로 유지).
- **서버**(`app.py:10506` `watch_quick()`): `stop = float(body.get
  ("stop"))`(10528줄 부근) 제거하고, `tab`(=`body.get("tab")`, 이미 받고
  있음)과 `ticker`로 스냅샷 스토어를 조회해 `stop`을 서버가 직접 채움.
  스냅샷에 없는 경우(스캔이 아직 한 번도 이 (ticker,tab)을 못 잡은
  극초기 케이스, 또는 `category=="관찰"`처럼 게이트형 5탭이 아닌 경로)
  처리 방침 미정 — ⚠️ **결정 필요**: 현재처럼 body.stop 폴백을 허용할지,
  아니면 "스냅샷 없이는 등록 거부"로 더 엄격하게 갈지는 다음 설계
  세션에서 정한다.

## 5. 돌파임박 `_registration_day_low` 교체

`app.py:10102-10107`:
```python
# 현재
if confirmed and tab == "돌파임박":
    reg_low = _registration_day_low(df, r.get("date"))
    if reg_low is not None and reg_low < pivot_f:
        stop_f = reg_low
    else:
        confirmed = False
```
→
```python
if confirmed and tab == "돌파임박":
    snap = get_signal_snapshot(ticker, "돌파임박")   # 신규 함수, 2절 스토어 조회
    if snap is not None and snap["low"] < pivot_f:
        stop_f = snap["low"]
    else:
        confirmed = False
```
`_registration_day_low()`(`app.py:9729-9748`) 자체는 이 호출부가
유일한 사용처이므로 교체 후 삭제 대상.

나머지 4탭의 `stop_f = r.get("stop")`(`app.py:10093`)은 **4절**(quickWatch
변경)로 저널에 저장되는 `stop` 자체가 이미 스냅샷 값이 되므로 이 지점은
수정 불필요 — 다만 **스냅샷 도입 이전에 등록된 기존 pending 레코드**는
구식 값을 그대로 갖고 있어 소급 적용이 안 된다(마이그레이션 여부는 별도
판단 필요, 이 문서에서 결정 안 함).

## 6. 남은 미결 사항 (다음 설계 세션)

1. 거래일 간격(3절 (a))을 셀 캘린더 함수 확정.
2. 스냅샷 미존재 시 quickWatch 폴백 정책(4절).
3. 기존 pending 레코드 마이그레이션 여부.
4. 스냅샷 스토어 크기 관리(오래된 (ticker,tab) 항목 정리 — `/data`
   볼륨은 5GB 중 2.4% 사용 확인됨(`docs/data_volume_cleanup_design.md`)이라
   당장 급하지 않음, 정책만 비워둠).

## 진행 로그 (2026-09-04, 구현 세션)

- **[1] snapshot-store 완료(커밋 c008a67).** `app.py`에 `GATE_MODE_LABELS`/
  `SIGNAL_SNAPSHOT_PATH`/`_load_signal_snapshots`/`_save_signal_snapshots`/
  `get_signal_snapshot`/`_record_signal_snapshot` 신설, `run_scan()`의
  게이트형 5탭 히트 적재 지점(hits.append 직전)에서 호출. 스키마에
  `signal_high` 추가(설계 문서 작성 시점엔 없었음, 3절 확인조건 판정에
  필요해 사용자 지시로 추가). 디스크 저장은 스캔 1회당 최대 1번(루프
  중엔 in-memory만 갱신, `snapshot_dirty` 플래그로 변경 있을 때만 저장) —
  수백 개 히트마다 파일 쓰기가 나가는 걸 방지. `debug_ticker()`의 기존
  `_mode_labels` 리터럴 사본은 `GATE_MODE_LABELS` 참조로 교체(CLAUDE.md
  리터럴 사본 금지 원칙). 격리 테스트(최초기록/연속재등장 미갱신/동일일
  재스캔 no-op/5거래일 갭 리셋/pivot 3%+ 리셋/pivot 3%미만 무리셋) 전부
  통과 확인.

# 대장후보 → 눌림목 전환 관찰 (v5.54)

> **⚠️ v5.68 노트: 이 문서와 인용하는 `leader_check_cost.py`/
> `leader_to_pullback_dist.py`는 저장소에 안 남아있어 재현 불가**
> (`scripts/measurements/README.md` 감사 표 참고). 전환율 74%·중앙값
> 6봉이라는 수치, 그리고 이걸 근거로 만든 만료 기간(아래 참고) 모두
> 재측정 전까지는 검증되지 않은 상태.

`docs/all_tabs_common_yardstick_investigation.md` Script D에서 대장후보 히트의 74%가 60봉 내 눌림목 탭에 전환된다는 걸 확인한 뒤(중앙값 6봉), 기존 감시(⚡)/관찰(👁) 인프라를 재사용해 이 전환을 알려주는 기능을 만든 기록.

## 설계 결정

### 1. 만료 기간 — 출현까지 걸리는 봉수 상세 분포

Script D는 median/p25/p75/max만 냈어서(p90 없음), `leader_to_pullback_dist.py`로 재측정(원본과 동일 방법론 — 대장후보 히트마다 60봉 내 매일 `analyze()`를 돌려 눌림목 게이트를 처음 통과하는 날 찾기).

| 통계 | 값 | 누적 비율 |
|---|---|---|
| n | 876 | — |
| min | 1봉 | — |
| median | 6봉 | — |
| p75 | 13봉 | 14봉 이내 77.5% |
| p90 | 26봉 | 20봉 86.5% / 30봉 92.2% |
| p95 | 37봉 | — |
| max | 60봉(관측 한도) | 100% |

초반에 몰려있지만(3봉 이내 37.9%, 7봉 이내 58.2%) 꼬리가 길다(20→30봉 사이에도 6.7%p 추가). 돌파임박 트리거(3영업일 — 즉시 확인·즉시 만료가 맞는 빠른 신호)와 성격이 다르다: 대장후보는 RS95+ 리스트 소속 자체가 핵심인 느린 신호라, **만료를 p90=26영업일로 설정**. p75(14봉)로 잡으면 실제 전환의 22.5%를 만료 전에 놓친다.

### 2. 눌림목 게이트 판정 — 프론트 vs 백엔드

`scanner.analyze()`(눌림목)는 백엔드 전용 함수라 프론트에서 직접 못 돌린다. 세 가지 안을 검토:

- (a) 눌림목 탭 로드 시 프론트에서 `lastHits`와 관찰 목록 대조 — 탭을 실제로 열어야만 갱신됨.
- (b) 새 백엔드 엔드포인트(`/api/watch/leader-check`)가 관찰 티커만 받아 `analyze()` 판정.
- (c) 얼마냐봇 폴링용 API만 우선 신설 — 봇 쪽 코드가 이 레포에 없어 나중으로.

(b)를 선택하기 전에 비용을 실측(`leader_check_cost.py`, KR 10개+US 10개=20개 티커를 격리해서 fetch+analyze):

| 단계 | 소요 |
|---|---|
| KR fetch(10개, 10워커 병렬) | 2.75초 |
| US fetch(10개, 배치) | 1.50초 |
| KR analyze() 10건 | 0.012초 |
| US analyze() 10건 | 0.013초 |

fetch가 압도적 비용, analyze() 자체는 무시할 수준. 그런데 이 측정은 **격리된 상황**(RS 순위를 가짜로 고정해서 fetch+analyze 연산 비용만 잰 것)이라 실제 구현에는 안 씀 — `analyze()`가 요구하는 RS 백분위는 전체 유니버스 안에서의 상대순위라, 관찰 티커만 따로 fetch해선 정확한 rs_rank를 만들 수 없다(둘 다 필요한 정보인데 하나만 조각으로 뗄 수 없음).

**실제 구현**은 앱이 이미 갖고 있는 시장 데이터 캐시(`_fetch_market_data("all")`)를 재사용한다 — 이 캐시는 모드에 상관없이 하루에 한 번 이상(다른 탭을 하나라도 열면) 채워지고, `bundle["data"]`/`bundle["rs_ranks"]`에 전체 유니버스가 이미 들어있다. 캐시가 따뜻하면(흔한 경우) 새 네트워크 호출이 전혀 없이 딕셔너리 조회 + `analyze()` 호출만 하므로 사실상 즉시 응답. 캐시가 아직 없으면(콜드 스타트, 예: 앱을 막 켰을 때) `/api/watch/leader-check`가 직접 fetch를 걸지 않고 `pending:true`만 반환 — 다른 탭 로드가 캐시를 채울 때까지 다음 폴링(60초)에서 재시도.

### 3. 관찰 종류 구분

`watch_kind` 필드 신설(`'leader_conversion'` — 기존 트리거 관찰은 필드 없음, `trigger_price` 존재 여부로 계속 구분). 화면은 이중 구분: 배지 문구("🎯 눌림목 전환됨" vs "🟢 확인됨(거래량 동반 돌파)")와 기존 `tab` 필드(대장후보/돌파임박)가 목록에서 같이 보임.

### 4. `trigger_date` → `watch_start_date` 개명

`trigger_date`가 "돌파임박 트리거일"이라는 이름이지만, 실제로는 "관찰이 시작된 날"이라는 범용 개념이고 이제 두 관찰 종류가 같이 쓰게 됐다. 오늘 세션에서 "필드명이 실제 의미와 다르다"는 문제를 몇 번 잡았던 것과 같은 종류라 이름을 바로잡음. 새로 쓰는 값은 `watch_start_date`, 읽는 쪽은 전부 `watchStartDate(r) = r.watch_start_date ?? r.trigger_date`로 옛 레코드(trigger_date만 있음) 호환.

## 구현

- `app.py` `/api/watch/quick`: `category` 파라미터 추가. `category==='관찰'`이면 pivot 불필요, `status='watch'`, `watch_kind`·스냅샷 필드(`leader_snapshot_price`/`leader_snapshot_ma20_dist_pct`/`rs`) 저장. 기존 `category='추세추종'`(기본값, 감시 버튼) 경로 무변경. 중복 등록 체크는 category별로 분리(감시=pending 기준, 관찰=watch+watch_kind 기준).
- `app.py` `/api/watch/leader-check` 신설: 티커 목록 받아 `_fetch_market_data("all")` 캐시에서 조회 → `analyze()` 판정(눌림목 탭과 동일한 저유동성 하드필터도 동일 적용, "전환됨" 배지가 실제 탭 히트와 어긋나지 않게).
- `static/index.html`: `watchLeaderConversion()`(원클릭 등록, quickWatch와 같은 패턴), `isLeaderConversionWatch()`(중복방지), `leaderWatchInfoHtml()`(배지, triggerInfoHtml과 나란히), `checkLeaderConversions()`(판정 API 호출, `updateTracking()`에 편승 — 별도 폴러 안 만듦, 트리거 관찰의 today_high 갱신과 같은 주기), `watchExpiryDays()`/`isWatchResolved()`(만료·해소 판정을 관찰 종류별로 일반화, `cleanupExpiredWatches`/`renderWatchCleanupBar`가 재사용).
- 대장후보 카드의 ⚡감시 버튼(대장후보엔 pivot이 없어 원래 항상 "피벗없음"으로 실패하던 죽은 버튼)을 "👁 눌림목 전환 관찰"로 교체.

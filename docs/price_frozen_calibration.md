# 가격고정(M&A 의심) 필터 — 전 탭 공통화 (v5.90)

## 계기

Seulki가 CRNX/APGE 두 종목이 스캐너에 잡히는 문제를 지적. 둘 다 실제 M&A
발표 종목으로 확인됨(원인 확인 절 참고) — 인수가 부근에 가격이 고정된
상태인데도 "깔끔한 횡보"처럼 보여 특정 탭에 통과하고 있었다.

## 1. 원인 확인 — 웹 검증 + 코드 추적

- **CRNX(Crinetics Pharmaceuticals)**: 2026-07-06 Vertex Pharmaceuticals가
  $85.00/주 현금 인수 발표(총 약 $10.0B). 종가($84.84)가 인수가에 거의
  고정. 주주총회는 2026-08-28 예정. (출처: businesswire.com, news.vrtx.com)
- **APGE(Apogee Therapeutics)**: 2026-06-22 AbbVie가 $135.11/주 현금 인수
  발표(약 $10.9B). 종가($134.97)가 인수가에 거의 고정. (출처:
  investors.apogeetherapeutics.com, news.abbvie.com)

**코드 추적 결과**: `scanner.py`에는 이미 v4.80부터 `merger_warning()`이
있었고, CRNX/APGE 둘 다 원래 로직으로도 정확히 `merger: True`로 판정됨
(재확인 완료) — **탐지 로직 자체는 문제가 없었다.** 진짜 원인은 **커버리지
공백**: 이 체크(`_merger_block`)가 6개 함수(`analyze`/`analyze_super`/
`analyze_boxbreak`/`analyze_imminent`/`analyze_breakdown`/`analyze_pattern`)
에만 있었고, `analyze_leader`(대장후보)·`analyze_surge`(급등)·
`analyze_turnaround`(추세전환)·`analyze_breakout`(돌파) 4개엔 아예 없었다.
실제로 CRNX/APGE를 현재 코드로 직접 돌려보면:

| 탭 | CRNX | APGE |
|---|---|---|
| 대장후보(leader) | **HIT** | **HIT** |
| 급등(surge) | **HIT** | **HIT** |
| 추세전환/돌파/눌림목 | None(구조상 불일치, 무관) | None |

대장후보·급등 탭이 실제로 CRNX/APGE를 잡고 있었다 — 발표 직후 갭업+거래량
폭발이 이 두 탭의 조건과 구조적으로 겹치기 때문.

## 2. 재설계 — "가격고정" 단순 정의로 통합

기존 `merger_warning()`은 3조건(변동성붕괴 비율·좁은밴드·발표충격갭)이었으나,
사용자 지시로 더 단순한 2조건으로 재설계:

1. **발표충격갭**: 과거 5~120봉 구간에 거래량 폭발(평균 5배+) 동반 +15%
   이상 급등.
2. **변동성 극소**: 최근 20봉 ATR%(ATR/종가)가 **0.5% 미만**.

## 3. 캘리브레이션 — 실데이터

2026-08-29, `yfinance` 실데이터로 확인(2년치 일봉).

### 양성 표본(실제 M&A) — ATR%

| 종목 | ATR%(최근 20봉) |
|---|---|
| CRNX | 0.124% |
| APGE | 0.089% |

### 음성 표본(정상 신고가 주도주) — ATR%

| 종목 | ATR%(최근 20봉) | 비고 |
|---|---|---|
| NVDA | 2.783% | 최솟값(마진 기준) |
| PLTR | 3.704% | +15%+ 갭 이력 있음(2026-08-04, +29.5%) — 그래도 ATR 정상 |
| AVGO | 3.136% | |
| TSLA | 3.444% | |
| MSTR | 5.738% | 최댓값 |
| SMCI | 5.636% | +15%+ 갭 이력 3건 — 그래도 ATR 정상 |
| CRWD | 3.924% | |
| ANET | 3.548% | |
| VRT | 4.785% | |
| MU | 4.986% | +15%+ 갭 이력 3건 — 그래도 ATR 정상 |

**분리 마진**: 양성 최댓값(CRNX 0.124%) 대비 음성 최솟값(NVDA 2.783%)까지
**22.4배** 여유 — 임계값 0.5%는 양쪽 사이 어디에 둬도 안전하지만, 사용자
제시값(0.5%)을 그대로 채택.

**중요 확인**: PLTR/SMCI/MU는 실제로 +15%+ 발표충격갭 조건(조건1)을
단독으로는 충족한다(모두 실적 발표 등에 의한 정상적 갭업, M&A 아님) — 하지만
조건2(ATR<0.5%)를 전혀 충족하지 않아 AND 결합에서 안전하게 걸러진다. 즉
"갭업 이력이 있다고 무조건 의심되는" 구조가 아니라, "갭업 이후 실제로
변동성이 죽었는가"까지 봐야 의심 확정이라는 설계 의도가 실데이터로도
확인됨.

## 4. 구현 — 정보용 필드로 전환(하드 게이트 → 표시 레이어)

- `scanner.py`: `price_frozen_check(c, h, lo, v)` 단일 공통 유틸(구
  `merger_warning` 대체) — 10개 `analyze_*()` 함수(눌림목/추세전환/
  대장후보/슈퍼대장/돌파/박스돌파/돌파임박/급등/붕괴/패턴) 전부가 내부에서
  호출해 `price_frozen`/`price_frozen_reasons`를 결과 딕셔너리에 항상
  포함시킨다. **더 이상 게이트가 아니다** — v4.80처럼 `return None`으로
  완전 제외하지 않고, 정보로만 남긴다.
- `app.py`: `run_scan()`(눌림목 외 9개 탭 공용 루프)은 `analyze_*()`가
  이미 채워준 필드를 그대로 씀(중복 계산 안 함). Stage2/IBD9/실적우수/
  강한피벗처럼 다른 함수 계열을 쓰는 4개 경로는 각자 `price_frozen_check()`
  를 직접 호출해 부착. `_trace_*` 디버그 재현 함수 4개는 게이트를
  "가격고정(정보용, 비차단)"으로 바꿔 항상 통과 처리하되 판정값은 그대로
  보여준다.
- `scripts/measurements/harness.py`: `passes_liquidity_filter()`가
  `hit.get("price_frozen")`도 같이 체크 — analyze_*()가 이미 필드를 채워
  주므로 기존 15개+ 측정 스크립트를 하나도 안 고쳐도 v4.80과 동일하게
  가격고정 종목이 EV 측정에서 계속 제외된다(rule 8: 방식이 바뀌어도 결과
  수치엔 영향 없음).
- `static/index.html`: 카드가 뜨는 모든 탭이 거치는 공용 `renderCards()`
  에서 `price_frozen=true` 항목을 기본적으로 목록에서 빼고, 탭 하단에
  "⚠️ 가격고정(M&A 의심) N개 숨김 — 펼치기" 한 줄을 표시. 클릭하면
  해당 탭의 숨겨진 카드들을 별도 그리드로 펼쳐 보여주고 각 카드에
  ⚠️ 가격고정 배지(구 🤝 M&A의심)가 붙는다. 기존 종목 숨김(✕ 버튼,
  90일 자동만료, `hiddenMap`)과는 완전히 독립적인 별도 상태
  (`priceFrozenExpandedModes`, 탭별로 펼침 여부 기억).
- RS 랭킹 계산은 이 체크보다 앞서 별도로 끝나 있어(`_compute_rs_ranks`
  등) 영향 없음 — 가격고정 종목도 여전히 다른 종목들의 RS 백분위 계산
  모집단에 포함된다.

## 5. 검증

- `python3 test_scanner.py` — 0 FAIL(29개 케이스 전부 ✓).
- `python3 -m pytest test_trace_parity.py` — 381 passed(38종목×2 RS값×5
  셋업, `_trace_*`↔`analyze_*` stop/risk_pct 완전 일치 + 셋업별 최소
  커버리지 확인).
- `price_frozen_check(CRNX)` / `price_frozen_check(APGE)` 직접 호출 →
  둘 다 `price_frozen=True`, 사유 `['발표충격갭', '변동성극소(ATR
  0.09~0.12%)']`.
- 10개 `analyze_*()` 함수 전부 CRNX/APGE로 직접 호출 확인 — 구조적으로
  해당 탭 조건을 만족하는 경우(leader/super/imminent/pattern) 전부
  `price_frozen=True`로 정확히 채워짐, 그 외 탭은 다른 사유로 `None`
  (정상, price_frozen과 무관).

## 근거

- 원인 확인: WebSearch(businesswire.com, news.vrtx.com,
  investors.apogeetherapeutics.com, news.abbvie.com), scanner.py 직접 호출
  테스트(2026-08-29).
- 캘리브레이션: `yfinance` 2년 일봉, CRNX/APGE + NVDA/PLTR/AVGO/TSLA/
  MSTR/SMCI/CRWD/ANET/VRT/MU 10종목 실측(2026-08-29).

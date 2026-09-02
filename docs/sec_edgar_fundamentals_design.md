# SEC EDGAR 펀더멘털 통합 — 설계만 (2026-09-02, 구현 안 함)

> **착수 조건**: 2026-09-02 축소판 측정(yfinance만, EPS 지표 4개
> — `scripts/measurements/2026-09-02_canslim_eps_growth_pullback_us.py`)
> 에서 사전 등록 기준(EV 격차 +0.15R & z≥1.96 & 시기 반분 재현)을
> 통과하는 지표가 하나라도 나오면 그때 착수한다. 전부 기각/표본부족이면
> 이 문서는 참고 기록으로만 남기고 착수하지 않는다.

## 배경 — 왜 yfinance만으론 안 되는가 (실측 확인)

`earnings.py`(기존, 실시간 배지용)가 쓰는 `yf.Ticker(t).quarterly_income_stmt`/
`.income_stmt`/`.quarterly_balance_sheet`를 실제로 찍어봤다:

```
AAPL quarterly_income_stmt: 딱 5분기치만(오늘 기준 최근 5개)
AAPL annual income_stmt:    딱 5개년치만
AAPL quarterly_balance_sheet: 7분기치만
```

NVDA 등 다른 티커도 동일 — **티커별 차이가 아니라 yfinance/Yahoo
자체의 하드 제한**이다. 이 함수들은 "오늘 기준 최근 N분기" 스냅샷만
주지, 과거 특정 시점(예: 250거래일 전) 기준 재구성이 안 된다 — 90개
체크포인트 백테스트(최대 950거래일≈3.8년 전)에 매출·자기자본을 쓰려면
그만큼 과거로 거슬러 올라간 "그 시점에 실제 공개돼 있던 값"이 필요한데,
이 세 함수로는 원천적으로 불가능하다(과거 체크포인트에 최신 미래
데이터가 새거나, 조회 범위 밖이라 계산 자체가 안 됨).

`tk.get_earnings_dates(limit=100)`(Yahoo 상한 100)는 예외 — 발표일+
Reported EPS를 깊게 준다(AAPL 2014년, CELH 2010년, SMCI 2007년까지
확인). **EPS만 있으면 되는 지표(YoY·CAGR·가속·흑자전환)는 이걸로
충분**하다(2026-09-02 축소판 측정이 이 경로 사용). 문제는 **매출·
자기자본을 깊은 발표일과 함께 주는 yfinance 엔드포인트가 없다**는 것
— `get_earnings_history()`도 EPS 전용.

## SEC EDGAR `companyfacts` API — 실측 검증 결과

`https://data.sec.gov/api/xbrl/companyfacts/CIK{10자리}.json`을 실제
조회해 확인:

- 티커→CIK 매핑: `https://www.sec.gov/files/company_tickers.json`
  (무료, 10,391개 티커, 매핑 성공률 실측 100%(40/40 샘플)).
- 응답 하나에 **매출·EPS·순이익·자기자본이 전부** 들어있고, 각 값에
  실제 **SEC 제출일(`filed`)**이 태그돼 있음 — AAPL 기준 2008년까지
  분기 EPS 174건. 발표일 근사가 아니라 법적 공시일 자체라 yfinance
  방식보다 오히려 더 엄밀.
- 실 스캐너 US 유니버스(2,120종목)에서 무작위 40종목 테스트:
  **CIK 매핑 100%(40/40), 분기 XBRL 사용 가능 87.5%(35/40)**. 실패
  5건(BIDU/ASML/DEO/RY/EQNR/FRO)은 전부 **외국 민간 발행인(ADR)** —
  10-Q 대신 20-F/40-F를 연 1회만 제출해 분기 XBRL이 아예 없거나
  `us-gaap`이 아닌 `ifrs-full` 택소노미를 씀. 이 종목들은 `earnings.py`
  기존 원칙대로 "제외"가 아니라 "판정불가"로 처리(추정 금지).

## 데이터 정제 — 실측으로 확인된 필요 로직

1. **단일분기 vs 누적(YTD) 값 분리**: XBRL 원자료엔 같은 태그 아래
   단일분기 값과 연초누계 값이 섞여 있음. `end - start` 일수가
   80~100일인 것만 "클린 단일분기"로 채택(AAPL 매출 117건→클린
   단일분기 48건으로 정확히 필터링됨, 실측 확인).
2. **정정공시 처리**: 같은 회계기간이 여러 제출서류에 재인용되며
   `filed` 날짜가 다른 복수 항목으로 나타남(실측: AAPL 2025Q1 매출이
   2025-05-02 제출분과 2026-05-01 제출분에 중복 등장). 룩어헤드
   방지 원칙상 **그 기간의 최초 제출일(min filed)** 값만 채택 —
   나중 정정치는 "당시 몰랐던 정보"라 쓰면 안 됨.
3. **매출 태그 우선순위**: 회계기준 변경(ASC606, 2018년경)으로 태그명이
   바뀐 회사가 많음 — `RevenueFromContractWithCustomerExcludingAssessedTax`
   →`Revenues`→`SalesRevenueNet` 순으로 존재하는 것을 채택하는 폴백
   목록 필요(단일 태그만 보면 특정 연도 이전 데이터가 통째로 빠짐).

## 제안 모듈 구조 (구현 시)

- `sec_edgar.py`(신규, naver_kr.py와 같은 레벨):
  - `_load_ticker_cik_map()`: company_tickers.json 캐시(영구, 거의 안
    바뀜 — 주 1회 정도 갱신이면 충분).
  - `fetch_company_facts(ticker) -> dict | None`: CIK 조회 실패/
    companyfacts 없음(ADR 등)이면 None(판정불가 신호, 조용히 추정 금지).
    성공 응답은 **영구 캐시**(과거 공시는 안 바뀜, 최신 분기만 추가됨
    — 캐시 파일에 마지막 조회 시각만 남기고 다음 조회 때 새 분기만
    있는지 가볍게 확인하는 정도로 충분).
  - `clean_quarterly_series(facts, tag_priority) -> list[(period_end, filed, value)]`:
    위 정제 로직(단일분기 필터+최초제출일 선택+태그 폴백) 캡슐화.
  - `metrics_as_of(facts, signal_date) -> dict`: signal_date 이전
    `filed`만 사용해 (a)~(f) 전부 재구성. 축소판 스크립트의
    `_assert_no_lookahead()`와 동일한 assert 재사용.
- `earnings.py`(기존, 실시간 배지용)는 **건드리지 않는다** — 용도가
  다름(오늘 시점 배지 vs 과거 시점 백테스트). 두 모듈이 같은 SEC
  데이터를 각자 다른 방식(실시간 스냅샷 vs 시점별 재구성)으로 쓰는
  것도 이상하지 않음 — 억지로 통합하지 않는다.

## 캐싱/성능

- `companyfacts` 응답 크기: AAPL 3.8MB(대형주 기준, 44년치 전체
  택소노미 포함) — 소형주는 훨씬 작을 것으로 추정(미실측). 필요한
  4개 태그(Revenue류/EPS/NetIncome/StockholdersEquity)만 뽑아
  캐시하면 저장공간은 문제 안 됨.
- 요청 속도: SEC 권장 상한 초당 10회 — 실제 유니크 히트 티커 수(수백
  단위 예상) 기준 여유 있게 처리 가능(0.15초 간격이면 안전).
- User-Agent 헤더 필수(연락처 포함) — 안 넣으면 차단될 수 있음(SEC
  정책, `sec.gov/os/accessing-edgar-data` 참고).

## KR/DART — 미실측, 구조적 유사성만 확인

OpenDART(`opendart.fss.or.kr`)가 SEC EDGAR와 구조적으로 유사해 보임
— 재무제표 API + 접수번호(`rcept_dt`, 공시 접수일)로 같은 시점별
재구성이 원리상 가능. **단, 이건 추정이지 실측이 아니다**:
- 무료 API 키 발급(가입) 필요 — 이번 조사에선 아직 발급 안 함.
- 실제 응답 구조·깊이(몇 년치까지 주는지)·정정공시 처리 방식·회계
  기준(K-IFRS 개별/연결 구분 등) 전부 미확인.
- 착수하려면 키 발급 후 이번 SEC EDGAR 검증과 동일한 방식(무작위
  샘플 종목으로 커버리지·깊이 실측)을 반나절 규모로 먼저 해야 한다.

## 이번엔 구현하지 않음

이 문서는 설계 기록만이다. 착수 여부는 축소판 측정 결과에 달려있다.

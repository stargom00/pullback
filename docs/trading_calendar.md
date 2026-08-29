# 거래일 캘린더 (개장일 판정) — 정적 목록, 매년 갱신 필요

## 배경

스케줄러는 요일/공휴일 구분 없이 매일 돌아간다. `_market_session_key()`는
KST 요일·시각만으로 "장 마감 후" 여부를 판정해서, 실제로 장이 안 열린
날(주말은 물론, 평일인 공휴일)에도 "오늘자 daykey"가 만들어져 그 날짜로
돈의흐름 리포트·종가베팅 스냅샷·스캔 캐시가 저장되는 문제가 있었다 —
예: 금요일 데이터 그대로인데 토요일 날짜로 라벨링. `is_trading_day(market,
date)`(app.py)가 이 문제를 막는다 — 주말 + 아래 정적 공휴일 목록을 둘 다
확인한다.

## 왜 라이브러리가 아니라 정적 목록인가

- `holidays`/`exchange_calendars` 둘 다 이 프로젝트에 설치돼 있지 않다.
  `requirements.txt`가 버전 고정이 전혀 없어(v5.93 Railway 장애 조사에서
  이미 확인된 리스크 — CLAUDE.md 참고) 신규 의존성 추가 자체가 예측 못한
  배포 실패 위험을 늘린다. "날짜 가드"라는 기능의 무게에 비해 득실이
  안 맞는다고 판단.
- `pykrx`는 이미 의존성이지만, 실시간 조회는 KRX_ID/KRX_PW 로그인이
  필요해서 이 프로젝트가 v4.38.9에서 이미 포기한 경로다(`universe.py`
  주석 참고 — "pykrx는 KRX 로그인 요구로 폐기"). 개장일 조회 API도
  마찬가지로 막힐 게 뻔해 시도하지 않았다.

## 갱신 절차 (매년 12월경 다음 해 목록 추가 권장)

1. KRX: `open.krx.co.kr` 또는 KRX 공식 휴장일 공고에서 다음 해 전체
   휴장일을 확인.
2. NYSE: `nyse.com`의 공식 Holiday Calendar 페이지에서 다음 해 전체
   휴장일을 확인.
3. `app.py`의 `KRX_HOLIDAYS_2026_2027`/`NYSE_HOLIDAYS_2026_2027` 딕셔너리
   (또는 그 시점 변수명)에 `"YYYY-MM-DD"` 형식으로 추가.
4. 목록에 없는 연도가 오면 `is_trading_day()`가 로그에 경고를 남기고
   주말만 걸러진 상태로 동작한다(fail-open, 앱이 죽지는 않음) — 이
   로그(`[is_trading_day] ... 목록 갱신 필요`)를 보면 갱신 시점을 안다.

## 2026~2027 목록

아래 목록은 2026-08-29 WebSearch/WebFetch 조사 기준. 출처와 함께
`app.py`의 실제 상수(`KRX_HOLIDAYS_2026`/`KRX_HOLIDAYS_2027`/
`NYSE_HOLIDAYS_2026`/`NYSE_HOLIDAYS_2027`/`*_CONFIRMED_YEARS`)와 동기화
상태를 유지할 것 — 이 문서가 out-of-date면 코드 쪽이 최신이다. 모든
날짜는 `datetime.weekday()`로 요일을 재계산해 대체공휴일 규칙(토요일
공휴일 → 다음 평일 대체)과 논리적으로 맞는지 검증 완료.

### KRX (코스피/코스닥) 휴장일 — 2026만 확인됨

| 날짜 | 요일 | 사유 |
|---|---|---|
| 2026-01-01 | 목 | 신정 |
| 2026-02-16 | 월 | 설날 연휴(전날) |
| 2026-02-17 | 화 | 설날 |
| 2026-02-18 | 수 | 설날 연휴(다음날) |
| 2026-03-02 | 월 | 삼일절 대체공휴일(3/1이 일요일) |
| 2026-05-01 | 금 | 근로자의 날 |
| 2026-05-05 | 화 | 어린이날 |
| 2026-05-25 | 월 | 부처님오신날 대체공휴일(5/24가 일요일) |
| 2026-06-03 | 수 | 전국동시지방선거(임시공휴일) |
| 2026-07-17 | 금 | 제헌절(2026년 한시적 공휴일 지위 복원 — **상시 규칙 아님**, 매년 재확인 필요) |
| 2026-08-17 | 월 | 광복절 대체공휴일(8/15가 토요일) |
| 2026-09-24 | 목 | 추석 연휴(전날) |
| 2026-09-25 | 금 | 추석 |
| 2026-10-05 | 월 | 개천절 대체공휴일(10/3이 토요일) |
| 2026-10-09 | 금 | 한글날 |
| 2026-12-25 | 금 | 크리스마스 |
| 2026-12-31 | 목 | 연말 휴장일(매년 정확한 날짜가 다를 수 있어 KRX 공지 재확인 권장) |

출처(교차 확인): [kstockguide.com/holidays](https://kstockguide.com/holidays),
[calendarlabs.com/krx-market-holidays-2026](https://www.calendarlabs.com/krx-market-holidays-2026/),
[market-holiday.com/markets/krx/holidays](https://market-holiday.com/markets/krx/holidays) —
전부 2차 소스(KRX 공식 페이지 직접 확인은 아님). 6/3·7/17 특별휴장은
[BigGo Finance 기사](https://finance.biggo.com/news/6XJ9RJ4BaoGGrU-IpR-p)로
별도 확인(전국동시지방선거·제헌절 공휴일 지위 복원).

**2027 KRX는 조사 시점(2026-08-29) 기준 공식 캘린더 미발표** — 여러
공휴일 사이트를 확인했으나 "2027년 상세 휴장일 안내를 찾을 수 없음"으로
결론. 부정확한 추정치(대체공휴일 규칙 오적용 위험)를 넣는 대신
`KRX_CONFIRMED_YEARS = (2026,)`로 2027을 확인 범위 밖에 둬서
`is_trading_day()`가 로그로 알려주게 했다. **2026년 12월경 KRX가 2027년
캘린더를 공표하면 이 문서와 `app.py`를 같이 갱신할 것** — 참고로 조사 중
확인한 2027년 한국 공휴일(대체공휴일 미반영 상태, KRX 확정 아님)은:
1/1, 2/7(설날, 2/8·2/9 대체 가능성), 3/1, 5/5, 5/13, 6/6(일요일이라
KRX엔 불필요), 7/17(제헌절 — 2026 한시조치 연장 여부 불명), 8/15,
9/14~9/16(추석), 10/3, 10/9, 12/25.

### NYSE 휴장일 — 2026·2027 둘 다 확인됨(공식 발표 기준)

| 날짜(2026) | 요일 | 사유 | 날짜(2027) | 요일 | 사유 |
|---|---|---|---|---|---|
| 2026-01-01 | 목 | New Year's Day | 2027-01-01 | 금 | New Year's Day |
| 2026-01-19 | 월 | MLK Day | 2027-01-18 | 월 | MLK Day |
| 2026-02-16 | 월 | Washington's Birthday | 2027-02-15 | 월 | Washington's Birthday |
| 2026-04-03 | 금 | Good Friday | 2027-03-26 | 금 | Good Friday |
| 2026-05-25 | 월 | Memorial Day | 2027-05-31 | 월 | Memorial Day |
| 2026-06-19 | 금 | Juneteenth | 2027-06-18 | 금 | Juneteenth(observed) |
| 2026-07-03 | 금 | Independence Day(observed, 7/4가 토요일) | 2027-07-05 | 월 | Independence Day(observed, 7/4가 일요일) |
| 2026-09-07 | 월 | Labor Day | 2027-09-06 | 월 | Labor Day |
| 2026-11-26 | 목 | Thanksgiving Day | 2027-11-25 | 목 | Thanksgiving Day |
| 2026-12-25 | 금 | Christmas Day | 2027-12-24 | 금 | Christmas Day(observed, 12/25가 토요일) |

출처: [nyse.com/markets/hours-calendars](https://www.nyse.com/markets/hours-calendars)
(공식) + [stockmarkethours.org](https://stockmarkethours.org/nyse-holidays-2026)
교차 확인. 참고: 조기 마감일(Black Friday, Christmas Eve 등)은 전일
휴장이 아니라 개장일 판정 목적상 포함 안 함(`is_trading_day()`는 개장/휴장
이진 판정만 함, 조기마감은 "개장"으로 취급).

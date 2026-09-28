# 슈퍼인베스터 13F 추정 매입단가 스크리너 — Phase 1

> **모든 산출물은 "관심 신호 · 측정 전"이다.** 여러 슈퍼인베스터가 NEW/ADD한 종목과
> 추정 매입단가·현재가 위치를 CSV로 뽑는 것까지가 Phase 1이다.
> 펀더멘털·밸류·이벤트·100점 스코어는 **Phase 2**, 백테스트는 **Phase 3**(범위 밖).
> 승률·수익률 주장은 이 도구 어디에도 없다.

앱(`app.py`·`static/`·`scanner.py`)과 **완전히 분리**돼 있다 — 이 디렉터리는 로컬 전용이고
배포되지 않는다.

## 실행

```bash
python3 tools/superinvestor/run.py                  # 전체(13F → 티커 → 가격 → CSV)
python3 tools/superinvestor/run.py --no-prices      # 13F 집계까지만(빠른 점검)
python3 tools/superinvestor/validate_dataroma.py    # 검증: Dataroma 분류 일치율
```

첫 실행은 SEC 데이터셋 6구간(약 550MB)을 받아 `cache/sec/`에 둔다 — 이후 실행은 캐시만 쓴다.
OpenFIGI는 키 없이 분당 25요청이라 신규 CUSIP 400건이면 약 100초(캐시됨).
`OPENFIGI_API_KEY`가 있으면 자동으로 빠른 한도를 쓴다.

출력(`output/`, git 추적 안 함):
- `superinvestor_YYYYMMDD.csv` — 종목별 1행
- `unmapped_cusips.csv` — CUSIP→티커 매핑 실패(**버리지 않는다**)

## 구성

| 파일 | 역할 |
|---|---|
| `investors.json` | 투자자 목록(CIK·`list_fixed_date`·`alt_ciks`·`dataroma_code`). CIK는 전부 실제 제출분에서 확인한 값 |
| `secdata.py` | SEC Form 13F Data Sets 로더 + 함정 1·2·3·4·5·7 처리, `pick_filer()` |
| `figi.py` | CUSIP/CINS → 티커(OpenFIGI) + 함정 8(증권 유형) |
| `prices.py` | 일봉(yfinance), 분기 VWAP 근사·저가/고가, 함정 6(분할 보정) |
| `screen.py` | 분류·추정 매입단가·cluster 밀집도·컨센서스 (**공식 상수 전부 상단**) |
| `run.py` | 실행기 — CSV·콘솔 요약 |
| `edgar.py` | 검증용 **독립 경로**(EDGAR 원본 XML) |
| `validate_dataroma.py` | 검증 — Dataroma 공개 분류와 일치율 대조 |

테스트는 레포 루트 `test_superinvestor.py`(순수 로직 + 실데이터 대조, 사보타주 확인 기록 포함).

## 13F 함정 — 어디서 처리하나

| # | 함정 | 처리 |
|---|---|---|
| 1 | PUT/CALL 행은 롱 보유 아님 | `secdata.keep_holding_row()` |
| 2 | `sshPrnamtType=PRN` 제외, SH만 | 같은 함수 |
| 3 | VALUE 단위(2023-01-03 이전 제출분 = 천 달러) | `secdata.normalize_value()` — 근거는 데이터셋 동봉 readme 원문 인용 |
| 4 | 13F-HR/A: RESTATEMENT vs NEW HOLDINGS | `secdata.apply_amendments()` |
| 5 | 같은 CUSIP 여러 행 합산 | `secdata.aggregate_by_cusip()` |
| 6 | 분기 간 주식 수 비교 시 분할 보정 | `prices.split_factor_after()` + `screen.classify_history(split_factor=…)` |
| 7 | 컨피덴셜은 사전 탐지 불가 | 뒤늦게 추가된 보유만 `late_disclosed` |
| 8 | ETF/ETN 제외, ADR·우선주 표시 | `figi.security_kind()` (+ 비주식 `non_equity` 제외) |

## 이 도구를 만들면서 실제로 밟은 함정(기록)

2026-09-28 첫 구축에서 **문서에 없던 문제**를 네 건 밟았다. 같은 함정을 다시 밟지 않으려고 남긴다.

1. **13F-NT를 내는 법인을 투자자로 등록하면 0건이 된다.** `ICAHN CAPITAL LP`·
   `ValueAct Capital Management`는 매 분기 통지서만 내고 보유는 `ICAHN CARL C`·
   `ValueAct Holdings`가 낸다. Pershing Square는 2026Q2부터 보고 주체가
   `PERSHING SQUARE INC.`로 바뀌었다 → `alt_ciks` + `pick_filer()`(분기별 선택,
   여러 법인이 같은 분기에 HR을 내면 합치지 않고 총액 큰 쪽). 보유명세를 한 분기도
   못 찾은 투자자가 있으면 **실행이 실패**한다(`--allow-missing-investors`로만 통과).
2. **CUSIP만 물으면 외국 법인 미국 상장주가 전부 매핑 실패한다.** 13F는 CINS(G/H/D 접두)로
   들어온다 — Aon·Chubb·Linde·ICLR·STX 등. `ID_CUSIP` → `ID_CINS` 폴백으로 실패 38건 → 1건.
3. **OpenFIGI가 같은 CUSIP에 채권 항목을 섞어 준다**(`GOOGL 6.25 05/15/29 A`).
   `marketSector == "Equity"`를 먼저 고르고, 주식이 없으면 `non_equity`로 제외한다.
4. **cluster 밀집도 N/A 규칙을 "서로 다른 분기 수" 합집합으로 세면 새어나간다.**
   실측 EQH: 한 투자자가 직전 분기에 28,245주(전체의 0.3%)를 더 갖고 있어 합집합이
   2분기가 되었고, 두 추정단가가 41.8964 vs 41.8965 — **밀집도 0.000이라는 가짜 정밀도**가
   출력됐다. 지배분기(증가주식수 90% 이상을 차지하는 분기) 집합으로 판정하도록 고쳤다.

## 알려진 한계 (Phase 1 범위)

- **13F는 45일 지연 공개**다. `quarter_ends()`가 그 기한을 반영해 분기를 고르지만,
  공개 시점에 이미 포지션이 바뀌어 있을 수 있다.
- **추정 매입단가는 분기 VWAP 근사**(`(H+L+C)/3` 거래량 가중)다. 분기 중 실제 매수 시점은
  알 수 없어서 분기 저가~고가를 같이 낸다. 컬럼명에 `_approx`가 붙은 이유다.
- 가격은 **분할 보정·배당 미보정**(yfinance `Close`). 배당을 반영하면 "그때 낸 주당 가격"이
  아니게 되므로 의도한 선택이다.
- 최근 5개 분기를 분류하려면 비교 기준으로 직전 1분기를 더 받는다(총 6분기 로드).
- `CLASSIFY_EPS_PCT`(0.1%)·`DOMINANT_QUARTER_SHARE_PCT`(90%)는 **AI 판단 어림값**으로
  측정 근거가 없다(코드 주석에 명시). Phase 3에서 재검토 대상.
- 컨피덴셜 보유는 원리적으로 사전 탐지 불가 — 나중에 `late_disclosed`로만 드러난다.
- **투자자 목록은 `list_fixed_date` 이후 공개분만 Phase 3 신호로 써야 한다**(사후선택 편향).
  목록에 사람을 추가할 때 그 사람의 `list_fixed_date`만 새로 적는다(전체 갱신 금지).

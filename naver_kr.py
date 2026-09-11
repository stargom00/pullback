"""
네이버 금융 데이터 소스 — 한국 종목(.KS/.KQ) 전용.
- 일봉(오늘 포함): api.finance.naver.com/siseJson.naver (수정주가 기준).
  오늘 거래일 행도 실시간에 가깝게 채워줘서 별도 현재가 오버레이가 필요 없다
  (v4.90 — m.stock.naver.com의 fetch_live_price는 반대로 하루 지연된 값만
  줘서 오버레이용으로 쓰면 안 됨을 실측으로 확인, 디버그 참고용으로만 남김).

yfinance와 동일한 컬럼명(Open/High/Low/Close/Volume) + DatetimeIndex 의
pandas DataFrame을 반환하므로 scanner.py는 수정 불필요.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pandas as pd
import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

_SISE_URL = "https://api.finance.naver.com/siseJson.naver"
_TIMEOUT = 8


def to_code(ticker: str) -> str:
    """'033640.KQ' -> '033640' (6자리 종목 코드)."""
    return ticker.split(".")[0].zfill(6)


def is_kr(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ"))


def _parse_sise(text: str) -> pd.DataFrame | None:
    """
    siseJson 응답 파싱.
    응답 예 (작은따옴표 변종, 첫 줄은 헤더):
    [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],
     ["20250115", 70000, 71000, 69500, 70500, 12345678, 51.23],
     ...]
    """
    if not text:
        return None
    # 작은따옴표 → 큰따옴표, 개행/공백 정리 후 JSON 파싱
    cleaned = text.strip().replace("'", '"')
    # 후행 콤마 제거 (네이버가 가끔 붙임)
    cleaned = re.sub(r",\s*]", "]", cleaned)
    try:
        rows = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not rows or len(rows) < 2:
        return None

    header, *data = rows
    if not data:
        return None

    recs = []
    for r in data:
        # r = [날짜, 시가, 고가, 저가, 종가, 거래량, 외국인소진율]
        if len(r) < 6:
            continue
        try:
            recs.append({
                "Date": pd.to_datetime(str(r[0]).strip(), format="%Y%m%d"),
                "Open": float(r[1]),
                "High": float(r[2]),
                "Low": float(r[3]),
                "Close": float(r[4]),
                "Volume": float(r[5]),
            })
        except (ValueError, TypeError):
            continue

    if not recs:
        return None

    df = pd.DataFrame(recs).set_index("Date").sort_index()
    # 0 종가(거래정지 등) 행 제거
    df = df[df["Close"] > 0]
    return df if not df.empty else None


def fetch_history(ticker: str, days: int = 730) -> pd.DataFrame | None:
    """한국 종목 과거 일봉 (수정주가). 기본 730일(≈2년, 거래일 약 485봉).

    v5.28: 400일(≈거래일 269봉)이던 시절엔 A-B-C 상한가 패턴의 적응형 A구간
    (A_MAX_LOOKBACK=250)이 126봉을 넘는 종목 전부(실측 214/214, 100%)가
    trade_value_ratio 탐색창(A 이전 126봉)을 온전히 확보 못 했다. 730일로
    늘리면 그중 213/214(99.5%)가 해소됨을 실측 확인. API 쪽 상한은 없음
    (요청 기간에 그대로 비례해서 반환, 429/5xx 재시도 로직은 무관하게
    작동) — fetch당 소요시간도 400/730/1095일 전부 요청당 약 1초로 동일
    (페이로드 크기가 아니라 왕복 지연이 지배적). rs_raw_score·
    rs_score_stage2·count_bases_since_bottom·late_stage_info·trend_grade·
    off_high_pct·rr_info의 52주/12개월 지표는 전부 "끝에서부터 N봉"
    trailing window라 이 변경과 무관(더 늘어난 과거 데이터가 앞에 붙을
    뿐 최근 N봉 내용 자체는 그대로) — KR 유니버스 1503종목 실측 비교로
    확인(7개 함수 전부 사실상 100% 동일값, 유일한 차이는 이전엔 데이터
    부족으로 None/0 처리되던 종목 1건이 새로 계산 가능해진 것뿐)."""
    code = to_code(ticker)
    return _fetch_sise_history(code, days)


def fetch_index_history(code: str, days: int = 200) -> pd.DataFrame | None:
    """지수 일봉. code: 'KOSPI' | 'KOSDAQ' (siseJson이 지수도 동일 형식 제공)."""
    return _fetch_sise_history(code.upper(), days)


def _fetch_sise_history(symbol: str, days: int) -> pd.DataFrame | None:
    """siseJson 일봉 공통 fetch. symbol은 종목코드(6자리) 또는 지수명(KOSPI 등).

    v4.48.1: 재시도(2회) + 지수 백오프 + 지터 추가.
    - 유니버스 확대(800→1500+) 시 일시적 실패 하나가 종목 누락으로 이어지지 않게.
    - 429/5xx엔 더 길게 대기. 매 요청에 50~150ms 지터로 버스트 완화(차단 예방).
    """
    import random as _rand
    import time as _time
    end = datetime.now()
    start = end - timedelta(days=days)
    params = {
        "symbol": symbol,
        "requestType": 1,
        "startTime": start.strftime("%Y%m%d"),
        "endTime": end.strftime("%Y%m%d"),
        "timeframe": "day",
    }
    _time.sleep(_rand.uniform(0.02, 0.08))   # 버스트 완화 지터 (v4.48.1 축소 — 싱글플라이트 락 도입으로 여유)
    for attempt in range(3):                  # 최초 1회 + 재시도 2회
        try:
            resp = requests.get(_SISE_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code == 429 or resp.status_code >= 500:
                # 레이트리밋/서버 오류 → 백오프 후 재시도 (429는 더 길게)
                base = 2.0 if resp.status_code == 429 else 0.6
                _time.sleep(base * (attempt + 1) + _rand.uniform(0, 0.4))
                continue
            resp.raise_for_status()
            return _parse_sise(resp.text)
        except (requests.RequestException, ValueError):
            if attempt < 2:
                _time.sleep(0.6 * (attempt + 1) + _rand.uniform(0, 0.3))
                continue
            return None
    return None


def fetch_live_price(ticker: str) -> float | None:
    """
    장중 현재가 (실시간 근접). 네이버 모바일 통합 API.
    실패 시 None → 호출부에서 일봉 마지막 종가로 폴백.

    v4.87 버그수정: totalInfos에서 code가 'closePrice'/'nowVal'인 항목을 찾았는데,
    실제 API 응답의 totalInfos엔 그런 code가 없음(lastClosePrice=전일종가,
    openPrice/highPrice/lowPrice뿐 — 실측 확인함, 마키나락스 477850 사례).
    그래서 이 함수가 사실상 항상 None을 반환해 호출부가 매번 일봉 마지막
    종가로 폴백하고 있었음 — 장중엔 그게 전일 종가라 "가격이 안 바뀐다"는
    문제로 보임. dealTrendInfos[0](최신 거래일)의 closePrice가 실제 현재가에
    해당하므로 이를 최종 폴백으로 추가."""
    code = to_code(ticker)
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        price = None
        if isinstance(data, dict):
            # 가장 흔한 위치
            ct = data.get("closePrice") or data.get("nowVal")
            if ct:
                price = _to_num(ct)
            if price is None:
                # totalInfos: [{"code":"closePrice","value":"37,800"}, ...] (구형/일부 응답)
                for item in data.get("totalInfos", []) or []:
                    if item.get("code") in ("closePrice", "nowVal"):
                        price = _to_num(item.get("value"))
                        if price:
                            break
            if price is None:
                # v4.87: dealTrendInfos[0] = 최신 거래일 항목의 종가(장중엔 현재가에 해당)
                dt = data.get("dealTrendInfos") or []
                if dt and isinstance(dt[0], dict):
                    price = _to_num(dt[0].get("closePrice"))
        return price
    except (requests.RequestException, ValueError, KeyError):
        return None


def _to_num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def fetch(ticker: str) -> pd.DataFrame | None:
    """
    한국 종목 일봉 조회. siseJson 자체가 오늘 거래일 데이터를 이미 실시간에
    가깝게 포함하고 있어(직접 확인함 — 삼성전자우 005935의 오늘 행 O/H/L/거래량이
    m.stock.naver.com의 당일 실시간 값과 정확히 일치) 별도 '현재가 덮어쓰기'가
    필요 없다.

    v4.89→v4.90 경위: "한국 종목 오늘 등락률이 +0%로 뜬다" 버그를 fetch_live_price()
    쪽 게이팅으로 고치려 했으나(v4.89), 근본 원인은 그게 아니었음.
    fetch_live_price()(m.stock.naver.com의 'integration' API)가 반환하는 값은
    어떤 필드를 봐도 결국 dealTrendInfos[0] = 가장 최근 '완결' 거래일 종가라
    항상 하루 지연됨 — totalInfos의 lastClosePrice도 이름 그대로 "전일 종가".
    반면 fetch_history()가 쓰는 siseJson 엔드포인트는 오늘 날짜 행을 이미
    실시간으로 채워서 준다(직접 curl로 확인). 그래서 v4.87~v4.89가 하던
    "하루 지연된 값으로 오늘 봉을 덮어쓰거나 새로 만드는" 로직이 오히려 이미
    정확한 오늘 데이터를 하루 전 값으로 오염시키고 있었음(오늘 종가를
    어제 종가로 바꿔써서 등락률이 0%가 됨). 그래서 그 오버레이 자체를 제거.
    """
    return fetch_history(ticker)


# ── 지수 (코스피/코스닥) ──────────────────────────────
# 종목과 경로가 다름: m.stock.naver.com/api/index/{CODE}/basic
# CODE: KOSPI, KOSDAQ
_INDEX_CODES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}


def fetch_index(code: str) -> dict | None:
    """한국 지수 현재값 + 등락. code: 'KOSPI' | 'KOSDAQ'.
    반환: {"name", "value", "change", "change_pct"} 또는 실패 시 None."""
    code = code.upper()
    url = f"https://m.stock.naver.com/api/index/{code}/basic"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return None
        # 네이버 응답: closePrice(현재값), compareToPreviousClosePrice(등락폭),
        #              fluctuationsRatio(등락률). 부호는 compareToPreviousPrice.code 등.
        val = _to_num(data.get("closePrice"))
        chg = _to_num(data.get("compareToPreviousClosePrice"))
        pct = _to_num(data.get("fluctuationsRatio"))
        if val is None:
            return None
        # 하락이면 부호 보정 (compareToPreviousPrice.code: '2'=상승 '5'=하락 통상)
        sign = 1.0
        cmp = data.get("compareToPreviousPrice")
        if isinstance(cmp, dict):
            c = str(cmp.get("code", ""))
            if c in ("3", "4", "5"):   # 보합/하락 계열
                sign = -1.0
        if chg is not None and sign < 0:
            chg = -abs(chg)
        if pct is not None and sign < 0:
            pct = -abs(pct)
        return {
            "name": _INDEX_CODES.get(code, code),
            "value": val,
            "change": chg,
            "change_pct": pct,
        }
    except (requests.RequestException, ValueError, KeyError):
        return None


# ── 종목 분류 상수(ETF 판별) ─────────────────────
# v5.252(사용자 지시): 여기 있던 PC 페이지 스크레이퍼(fetch_top_value/
# _parse_quant_page/_QUANT_URL/_ITEM_RE)는 삭제 — finance.naver.com이
# 2026-09-10 SPA로 개편돼 200 OK에 0건만 돌려줬고(유니버스는 v5.246,
# 시총 필터는 v5.251에 모바일 API로 이미 이전), 마지막 호출부였던
# /api/eod 거래대금 상위도 v5.252에서 fetch_top_turnover_v2() 캐시
# 재사용으로 바뀌었다. 아래 ETF 키워드/판별기는 모바일 API 경로
# (fetch_top_turnover_v2)가 계속 쓴다.
import time as _time

# ETF/ETN/인버스/레버리지 등 — 개별주가 아니라 제외 (미너비니/오닐 대상 아님)
# ETF/ETN 전용 브랜드 접두어 (개별주명과 안 겹치는 것만).
# 주의: "삼성","미래에셋","신한","한국투자" 같은 그룹명 단독은 넣지 말 것
# — 삼성전자/미래에셋증권/신한지주 등 진짜 개별주가 오탐됨.
_ETF_KEYWORDS = (
    # 운용사 ETF 브랜드명 (개별 종목명에 안 쓰이는 고유 브랜드)
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "KINDEX", "HANARO", "KOSEF",
    "TIMEFOLIO", "히어로즈", "KIWOOM", "마이다스", "KCGI", "FOCUS",
    "TREX", "삼성액티브", "삼성KODEX", "1Q ", "FnGuide",
    # 브랜드 + 공백 형태로만 (단독 단어 오탐 방지)
    "SOL ", "ACE ", "PLUS ", "RISE ", "WOORI ", "마이티 ", "파워 ",
    # 추가 ETF 브랜드 (스크린샷서 발견: MIDAS/WON/KoAct/TIME 등)
    "MIDAS", "WON ", "KOACT", "TIME ", "BNK", "마이다스",
    "DAISHIN", "HEROES", "마이에셋", "교보악사",
    # ETF/ETN 상품 유형 키워드 (개별주명에 거의 안 나옴)
    "인버스", "레버리지", "곱버스", "ETN", "ETF",
    "선물", "2X", "3X", "국고채", "통안채", "커버드콜",
    "맥쿼리인프라", "REITS", "TIGERETF",
    # 액티브/밸류업/테마 ETF 유형 (대부분 ETF 전용 작명)
    "액티브", "밸류업", "코스피200", "코스닥150", "200선물",
    "TR)", "채권액티브", "혼합형",
)


def _is_etf_like(name: str) -> bool:
    """ETF/ETN/인버스/레버리지 등 개별주가 아닌 종목 판별."""
    up = name.upper()
    return any(k.upper() in up for k in _ETF_KEYWORDS)


# ── 거래대금 상위 v2 — 모바일 API 기반(v5.252부터 KR 유니버스·EOD의 유일 소스) ──
# (2026-09-01, 사용자 지시) 발견: fetch_top_value()가 긁는
# finance.naver.com/sise/sise_quant.naver는 page 파라미터가 응답에
# 반영되지 않는 버그가 있어(실측 재현 — page 1~59 전부 동일 콘텐츠 반환)
# 거래대금 기준으로는 시장당 ~90개 정도만 건지고, 나머지는 전부 시가총액
# 폴백으로 채워지고 있었다(전체 1500 중 91%가 시총 폴백). 그 사고 원인
# 조사 중 발견한 대체 소스 — m.stock.naver.com의 모바일 API는 같은 사이트
# 안의 다른 페이지지만 page 파라미터가 정상 동작(실측 확인, page별로
# 실제 다른 종목 반환)하고, 시총순 정렬(marketValue) 응답에도 종목별
# 당일 누적거래대금(accumulatedTradingValue)이 이미 포함돼 있어, 전체
# 시장을 훑어 거래대금 기준으로 직접 재정렬하면 "진짜" 거래대금 상위
# 리스트를 만들 수 있다.
#
# **fetch_top_value()는 건드리지 않는다** — 이 함수는 비교 측정용 병행
# 경로다. 종가베팅 백테스트 재측정에서 이 유니버스로 채택 여부가 갈리기
# 전까지 운영 경로(get_universe/load_kr_dynamic)는 기존 그대로 둔다
# (사용자 지시).
_MSTOCK_MARKETVALUE_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
_MSTOCK_PAGE_SIZE = 100          # 실측 확인: 100은 정상, 1000은 서버가 비JSON 응답으로 거부
_MSTOCK_MAX_CONSEC_FAILS = 2     # 같은 시장에서 연속 실패 시 그 시장 페이징 중단(부분 결과 + incomplete 플래그)


def _mstock_parse_trading_value(raw) -> int | None:
    """accumulatedTradingValue 문자열("3,308,883", 단위 백만원)을 정수로.
    파싱 불가 종목은 순위에서 제외(값 없이 섞으면 정렬이 왜곡됨)."""
    try:
        return int(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch_top_turnover_v2(top_n: int = 1500, page_size: int = _MSTOCK_PAGE_SIZE) -> tuple[dict, dict]:
    """m.stock.naver.com 기반 "진짜" 거래대금 상위 top_n. 반환:
    (universe: {코드.KS/.KQ: 이름}, stats: 진단정보).

    stats 필드: kospi_total/kosdaq_total(서버가 보고한 시장 전체 종목 수),
    kospi_fetched/kosdaq_fetched(실제로 받은 종목 수), skipped_etf,
    incomplete(연속 실패로 한 시장이라도 끝까지 못 훑었으면 True — 이
    경우 순위가 편향됐을 수 있어 호출부가 결과를 신뢰하기 전에 확인해야
    함), errors(예외 메시지 목록).

    fetch_top_value()와 달리 시장별 미리 배분(per_market)하지 않고 전체
    시장을 다 훑은 뒤 실제 거래대금(accumulatedTradingValue)으로 직접
    정렬한다 — 그래야 "거래대금 상위"라는 이름이 실제와 맞는다."""
    all_rows: list[tuple[str, str, int]] = []
    stats = {"kospi_total": None, "kosdaq_total": None,
              "kospi_fetched": 0, "kosdaq_fetched": 0,
              "skipped_etf": 0, "incomplete": False, "errors": []}

    for market, suffix, total_key, fetched_key in (
        ("KOSPI", ".KS", "kospi_total", "kospi_fetched"),
        ("KOSDAQ", ".KQ", "kosdaq_total", "kosdaq_fetched"),
    ):
        page = 1
        consec_fails = 0
        while True:
            data = None
            for attempt in range(2):   # 페이지당 최대 2회 시도(최초+재시도 1회)
                try:
                    resp = requests.get(
                        _MSTOCK_MARKETVALUE_URL.format(market=market),
                        params={"page": page, "pageSize": page_size},
                        headers=_HEADERS, timeout=_TIMEOUT,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except (requests.RequestException, ValueError) as e:
                    if attempt == 1:
                        stats["errors"].append(f"{market} page={page}: {type(e).__name__}: {e}")
            if data is None:
                consec_fails += 1
                if consec_fails >= _MSTOCK_MAX_CONSEC_FAILS:
                    # 이 시장은 여기서 중단 — 남은 종목을 놓친 채로 부분
                    # 결과만 갖고 있다는 뜻이라 incomplete로 명시(조용히
                    # 갭 있는 순위를 진짜처럼 반환하지 않는다).
                    stats["incomplete"] = True
                    break
                page += 1
                continue
            consec_fails = 0
            if stats[total_key] is None:
                stats[total_key] = data.get("totalCount")
            stocks = data.get("stocks") or []
            if not stocks:
                break
            for s in stocks:
                code, name = s.get("itemCode"), s.get("stockName")
                val = _mstock_parse_trading_value(s.get("accumulatedTradingValue"))
                if not code or not name or val is None:
                    continue
                if _is_etf_like(name):
                    stats["skipped_etf"] += 1
                    continue
                all_rows.append((f"{code}{suffix}", name, val))
                stats[fetched_key] += 1
            _time.sleep(0.1)
            page += 1
            if stats[total_key] is not None and (page - 1) * page_size >= stats[total_key]:
                break   # 서버가 밝힌 전체 종목 수만큼 다 받았으면 정상 종료

    all_rows.sort(key=lambda r: r[2], reverse=True)
    universe = {t: n for t, n, _v in all_rows[:top_n]}
    return universe, stats


# (v5.252 삭제) fetch_top_marketcap() — sise_market_sum PC 페이지 스크레이퍼.
# 유일한 호출부였던 fetch_top_value()와 함께 제거. 시총 데이터가 필요하면
# 아래 fetch_high_marketcap_allowed()(모바일 API)를 쓸 것.


# ── 시가총액 하한 필터 (v4.91) — 국장 소형주 스캔 제외용 ──
# v5.251(사용자 지시): 원래 finance.naver.com sise_market_sum.naver(PC 페이지)
# 를 정규식으로 긁었는데(code=(\d{6})), 그 페이지가 2026-09-10 장마감 전후
# Next.js SPA로 개편돼 200 OK에 종목 데이터 0건 — v5.246이 유니버스
# (sise_quant)만 모바일 API로 옮기고 이 함수는 놓쳐서, 허용목록 0건 →
# app._get_mcap_allowed() fail-open → 시총 1000억 필터가 꺼진 채 운영됐다.
# 게다가 옛 정규식은 숫자 6자리만 받아 알파벳 혼용 신규코드(0011A0 등,
# 거래소 2024-01 도입)를 원천적으로 못 담았다. fetch_top_turnover_v2()와
# 같은 모바일 API(m.stock.naver.com marketValue, marketValue 단위=억원)로
# 교체 — 이 경로는 itemCode를 그대로 줘서 알파벳 혼용 코드도 포함한다.


def _mstock_parse_market_value_eok(raw) -> int | None:
    """marketValue 문자열("15,171,093", 단위 억원)을 정수로. 파싱 불가면 None."""
    try:
        return int(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch_high_marketcap_allowed(min_eok: int = 1000, page_size: int = _MSTOCK_PAGE_SIZE) -> tuple[set, dict]:
    """시가총액이 min_eok(억원) 이상인 코스피+코스닥 종목의 티커 집합(허용목록)
    과 진단 stats를 반환: (allowed, stats).

    '미달 집합(블랙리스트)'이 아니라 '충족 집합(화이트리스트)'인 이유는
    v4.91 그대로 — 호출부는 이 집합에 없는 KR 티커를 문턱 미달로 보고 뺀다.

    **화이트리스트라서 불완전한 목록은 조용한 과잉 배제가 된다** — 한 시장
    이라도 끝까지 못 받았으면(stats["incomplete"]) 부분 집합을 돌려주지 않고
    빈 집합을 돌려준다(호출부가 fail-open + 경고로 처리). 부분 목록을
    그대로 쓰면 못 받은 페이지의 대형주가 "시총 미달"로 스캔에서 빠진다.

    정렬 순서(시총 내림차순)에 기대 중간에 멈추지 않고 전 페이지를 받는다
    — 순서 가정이 깨져도 결과가 틀리지 않게(코스피+코스닥 약 45페이지,
    fetch_top_turnover_v2와 같은 규모).

    stats: kospi_total/kosdaq_total(서버 보고 종목 수), kospi_fetched/
    kosdaq_fetched, n_unparsed(marketValue 파싱 불가), n_allowed,
    incomplete, errors."""
    allowed: set = set()
    stats = {"kospi_total": None, "kosdaq_total": None, "kospi_fetched": 0, "kosdaq_fetched": 0,
             "n_unparsed": 0, "n_allowed": 0, "incomplete": False, "errors": []}
    for market, suffix, total_key, fetched_key in (
        ("KOSPI", ".KS", "kospi_total", "kospi_fetched"),
        ("KOSDAQ", ".KQ", "kosdaq_total", "kosdaq_fetched"),
    ):
        page = 1
        consec_fails = 0
        while True:
            data = None
            for attempt in range(2):
                try:
                    resp = requests.get(
                        _MSTOCK_MARKETVALUE_URL.format(market=market),
                        params={"page": page, "pageSize": page_size},
                        headers=_HEADERS, timeout=_TIMEOUT,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except (requests.RequestException, ValueError) as e:
                    if attempt == 1:
                        stats["errors"].append(f"{market} page={page}: {type(e).__name__}: {e}")
            if data is None:
                consec_fails += 1
                if consec_fails >= _MSTOCK_MAX_CONSEC_FAILS:
                    stats["incomplete"] = True
                    break
                page += 1
                continue
            consec_fails = 0
            if stats[total_key] is None:
                stats[total_key] = data.get("totalCount")
            stocks = data.get("stocks") or []
            if not stocks:
                break
            for st in stocks:
                code = st.get("itemCode")
                mv = _mstock_parse_market_value_eok(st.get("marketValue"))
                if not code:
                    continue
                stats[fetched_key] += 1
                if mv is None:
                    stats["n_unparsed"] += 1
                    continue
                if mv >= min_eok:
                    allowed.add(f"{code}{suffix}")
            _time.sleep(0.1)
            page += 1
            if stats[total_key] is not None and (page - 1) * page_size >= stats[total_key]:
                break
        # 서버가 밝힌 전체 수만큼 못 받았으면(연속실패 외에 페이지가 일찍 비는
        # 경우 포함) 불완전으로 본다.
        if stats[total_key] is None or stats[fetched_key] < stats[total_key]:
            stats["incomplete"] = True
    if stats["incomplete"]:
        stats["n_allowed"] = 0
        return set(), stats
    stats["n_allowed"] = len(allowed)
    return allowed, stats

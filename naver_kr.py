"""
네이버 금융 데이터 소스 — 한국 종목(.KS/.KQ) 전용.
- 과거 1년치 일봉: api.finance.naver.com/siseJson.naver (수정주가 기준)
- 장중 현재가: m.stock.naver.com 모바일 API (실시간 근접)

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


def fetch_history(ticker: str, days: int = 400) -> pd.DataFrame | None:
    """한국 종목 과거 일봉 (수정주가). 기본 400일 → 1년치 확보."""
    code = to_code(ticker)
    return _fetch_sise_history(code, days)


def fetch_index_history(code: str, days: int = 200) -> pd.DataFrame | None:
    """지수 일봉. code: 'KOSPI' | 'KOSDAQ' (siseJson이 지수도 동일 형식 제공)."""
    return _fetch_sise_history(code.upper(), days)


def _fetch_sise_history(symbol: str, days: int) -> pd.DataFrame | None:
    """siseJson 일봉 공통 fetch. symbol은 종목코드(6자리) 또는 지수명(KOSPI 등)."""
    end = datetime.now()
    start = end - timedelta(days=days)
    params = {
        "symbol": symbol,
        "requestType": 1,
        "startTime": start.strftime("%Y%m%d"),
        "endTime": end.strftime("%Y%m%d"),
        "timeframe": "day",
    }
    try:
        resp = requests.get(_SISE_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return _parse_sise(resp.text)
    except (requests.RequestException, ValueError):
        return None


def fetch_live_price(ticker: str) -> float | None:
    """
    장중 현재가 (실시간 근접). 네이버 모바일 통합 API.
    실패 시 None → 호출부에서 일봉 마지막 종가로 폴백.
    """
    code = to_code(ticker)
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # totalInfos 리스트에서 'closePrice' 또는 dealTrendInfos 등에 현재가 존재.
        # 구조 변동 대비: 여러 경로를 순서대로 시도.
        # 1) 최상위 'dealTrendInfos' / 'closePrice'
        price = None
        if isinstance(data, dict):
            # 가장 흔한 위치
            ct = data.get("closePrice") or data.get("nowVal")
            if ct:
                price = _to_num(ct)
            if price is None:
                # totalInfos: [{"code":"closePrice","value":"37,800"}, ...]
                for item in data.get("totalInfos", []) or []:
                    if item.get("code") in ("closePrice", "nowVal"):
                        price = _to_num(item.get("value"))
                        if price:
                            break
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
    한국 종목 통합 fetch: 과거 일봉 + 현재가 보정.
    - 일봉을 받고, 장중 현재가가 있으면 '오늘' 봉의 Close를 덮어쓴다.
    - 오늘 봉이 아직 없으면 현재가로 새 행을 추가한다.
    """
    df = fetch_history(ticker)
    if df is None or df.empty:
        return None

    live = fetch_live_price(ticker)
    if live and live > 0:
        today = pd.Timestamp(datetime.now().date())
        last_day = df.index[-1].normalize()
        if last_day == today:
            # 오늘 봉 존재 → 종가/고저 보정
            df.loc[df.index[-1], "Close"] = live
            df.loc[df.index[-1], "High"] = max(df.iloc[-1]["High"], live)
            df.loc[df.index[-1], "Low"] = min(df.iloc[-1]["Low"], live)
        else:
            # 오늘 봉 없음 → 현재가로 임시 행 추가 (거래량은 직전값 근사)
            prev_close = float(df.iloc[-1]["Close"])
            df.loc[today] = {
                "Open": prev_close,
                "High": max(prev_close, live),
                "Low": min(prev_close, live),
                "Close": live,
                "Volume": float(df.iloc[-1]["Volume"]),
            }
    return df


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


# ── 거래대금 상위 종목 (pykrx 대체) ─────────────────────
# 네이버 금융 거래대금 상위 페이지를 긁어 코스피/코스닥 상위 N개를
# {티커.KS/.KQ: 이름}으로 반환. KRX 인증(pykrx) 불필요.
# 페이지: finance.naver.com/sise/sise_quant.naver?sosok=0(코스피)/1(코스닥)
import time as _time

_QUANT_URL = "https://finance.naver.com/sise/sise_quant.naver"
# code=XXXXXX 뒤 속성이 어떻든(따옴표·class 등) 종목명 텍스트까지 잡음
_ITEM_RE = re.compile(r'code=(\d{6})[^>]*>\s*([^<]+?)\s*</a>')

# ETF/ETN/인버스/레버리지 등 — 개별주가 아니라 제외 (미너비니/오닐 대상 아님)
# ETF/ETN 전용 브랜드 접두어 (개별주명과 안 겹치는 것만).
# 주의: "삼성","미래에셋","신한","한국투자" 같은 그룹명 단독은 넣지 말 것
# — 삼성전자/미래에셋증권/신한지주 등 진짜 개별주가 오탐됨.
_ETF_KEYWORDS = (
    # 운용사 ETF 브랜드명 (개별 종목명에 안 쓰이는 고유 브랜드)
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "KINDEX", "HANARO", "KOSEF",
    "TIMEFOLIO", "히어로즈", "KIWOOM", "마이다스", "KCGI", "FOCUS",
    "TREX", "에셋플러스", "삼성액티브", "삼성KODEX", "1Q ", "FnGuide",
    # 브랜드 + 공백 형태로만 (단독 단어 오탐 방지)
    "SOL ", "ACE ", "PLUS ", "RISE ", "WOORI ", "마이티 ", "파워 ",
    # ETF/ETN 상품 유형 키워드 (개별주명에 거의 안 나옴)
    "인버스", "레버리지", "곱버스", "ETN", "ETF",
    "선물", "2X", "3X", "국고채", "통안채", "커버드콜",
    "맥쿼리인프라", "리츠", "REITS", "TIGERETF",
    # 지수 추종형 (개별주명엔 안 나오는 조합)
    "200선물", "코스피200", "코스닥150",
)


def _is_etf_like(name: str) -> bool:
    """ETF/ETN/인버스/레버리지 등 개별주가 아닌 종목 판별."""
    up = name.upper()
    return any(k.upper() in up for k in _ETF_KEYWORDS)


def _parse_quant_page(html: str) -> list:
    """sise_quant 페이지 HTML에서 (종목코드, 종목명) 리스트 추출 (등장 순서=거래대금 순)."""
    results = []
    seen = set()
    for m in _ITEM_RE.finditer(html):
        code, name = m.group(1), m.group(2).strip()
        if code and name and code not in seen:
            seen.add(code)
            results.append((code, name))
    return results


def fetch_top_value(top_n: int = 800, include_etf: bool = False) -> dict:
    """코스피+코스닥 거래대금 상위 종목을 {코드.KS/.KQ: 이름}으로.
    네이버 거래대금 상위 페이지를 페이지네이션으로 긁는다. KRX 인증 불필요.
    기본적으로 ETF/ETN/인버스/레버리지는 제외(개별주만). 실패 시 빈 dict."""
    out = {}
    skipped_etf = 0
    for sosok, suffix in ((0, ".KS"), (1, ".KQ")):
        empty_streak = 0
        for page in range(1, 26):  # 페이지당 ~50종목, 최대 25페이지(~1250/시장)
            # 시장당 목표치 채우면 중단 (top_n 절반씩 분배 + 여유)
            mkt_count = sum(1 for k in out if k.endswith(suffix))
            if mkt_count >= top_n:  # 넉넉히 받고 마지막에 자름
                break
            try:
                resp = requests.get(
                    _QUANT_URL,
                    params={"sosok": sosok, "page": page},
                    headers=_HEADERS,
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                resp.encoding = "euc-kr"  # 네이버 sise는 EUC-KR 인코딩
                rows = _parse_quant_page(resp.text)
                if not rows:
                    empty_streak += 1
                    if empty_streak >= 2:  # 빈 페이지 2연속이면 끝
                        break
                    continue
                empty_streak = 0
                added = 0
                for code, name in rows:
                    if not include_etf and _is_etf_like(name):
                        skipped_etf += 1
                        continue
                    key = f"{code}{suffix}"
                    if key not in out:
                        out[key] = name
                        added += 1
                _time.sleep(0.12)
            except (requests.RequestException, ValueError):
                break
    # 거래대금 상위만으로 부족하면 시가총액 상위를 병합 (커버리지 확대)
    if len(out) < top_n:
        try:
            mcap = fetch_top_marketcap()
            for k, v in mcap.items():
                if k not in out:
                    out[k] = v
        except Exception:
            pass
    if len(out) > top_n:
        out = dict(list(out.items())[:top_n])
    return out


# ── 시가총액 상위 (거래대금 상위와 병합해 커버리지 확대) ──
_MARKETSUM_URL = "https://finance.naver.com/sise/sise_market_sum.naver"


def fetch_top_marketcap(per_market_pages: int = 20) -> dict:
    """코스피+코스닥 시가총액 상위를 {코드.KS/.KQ: 이름}으로.
    sise_market_sum 페이지를 긁는다. ETF 제외. 거래대금 상위와 병합용."""
    out = {}
    for sosok, suffix in ((0, ".KS"), (1, ".KQ")):
        empty = 0
        for page in range(1, per_market_pages + 1):
            try:
                resp = requests.get(
                    _MARKETSUM_URL,
                    params={"sosok": sosok, "page": page},
                    headers=_HEADERS,
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                resp.encoding = "euc-kr"
                rows = _parse_quant_page(resp.text)  # 같은 링크 형식
                if not rows:
                    empty += 1
                    if empty >= 2:
                        break
                    continue
                empty = 0
                for code, name in rows:
                    if _is_etf_like(name):
                        continue
                    out.setdefault(f"{code}{suffix}", name)
                _time.sleep(0.12)
            except (requests.RequestException, ValueError):
                break
    return out

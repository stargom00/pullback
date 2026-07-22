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
    # v4.48.1: 시장당 top_n의 절반씩 균형 수집.
    # 기존엔 시장당 top_n까지 받고 마지막에 앞에서부터 잘랐는데, 수집 순서가
    # 코스피 전부 → 코스닥이라 top_n=1500이면 코스피 1250 + 코스닥 250이 되는
    # 버그(모멘텀 중소형주가 사는 코스닥이 증발). 절반씩이면 트림 왜곡 없음.
    per_market = (top_n + 1) // 2
    for sosok, suffix in ((0, ".KS"), (1, ".KQ")):
        empty_streak = 0
        for page in range(1, 26):  # 페이지당 ~50종목, 최대 25페이지(~1250/시장)
            mkt_count = sum(1 for k in out if k.endswith(suffix))
            if mkt_count >= per_market:
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
        # 교차 트림: 시장별 순위를 유지하며 번갈아 채워 코스피 편중 방지
        ks = [(k, v) for k, v in out.items() if k.endswith(".KS")]
        kq = [(k, v) for k, v in out.items() if k.endswith(".KQ")]
        merged, i = {}, 0
        while len(merged) < top_n and (i < len(ks) or i < len(kq)):
            if i < len(ks) and len(merged) < top_n:
                merged[ks[i][0]] = ks[i][1]
            if i < len(kq) and len(merged) < top_n:
                merged[kq[i][0]] = kq[i][1]
            i += 1
        out = merged
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

"""업비트 공개 시세 — v5.313 저점 매매 기록의 코인 종목용.

키·인증이 필요 없는 **공개 엔드포인트만** 쓴다(사용자 지시 "API 키·인증 추가 금지"):
  - GET https://api.upbit.com/v1/ticker?markets=KRW-BTC  → 현재가(없는 마켓은 HTTP 404
    {"error":{"name":404,"message":"Code not found"}}). **여러 마켓을 한 번에 물으면 하나만
    없어도 전체가 404**라(2026-10-02 실측: KRW-BTC,KRW-ZZZZ → 404) 마켓 하나씩 묻는다.
  - GET https://api.upbit.com/v1/market/all            → korean_name(예: KRW-BTC → 비트코인).

입력 규칙: "KRW-BTC"처럼 업비트 마켓코드 그대로(KRW- 접두사)만 코인으로 본다 — 접두사 없는
입력은 기존 KR/US 경로 그대로(미국 티커 BTC 같은 것과 충돌 방지, 사용자 지시).
가격은 업비트가 준 값을 그대로 돌려준다(소수점 알트 가격을 정수로 깎지 않는다).
"""
from __future__ import annotations

import re

import requests

# 새 대기시간 금지(사용자 지시) — 기존 외부 시세 조회(naver_kr)와 같은 값을 재사용한다.
from naver_kr import _TIMEOUT

_TICKER_URL = "https://api.upbit.com/v1/ticker"
_MARKETS_URL = "https://api.upbit.com/v1/market/all"
_MARKET_RE = re.compile(r"KRW-[A-Z0-9]{1,15}")


def is_upbit(code) -> bool:
    """업비트 KRW 마켓코드 형식인가(대소문자 무관). 실재 여부는 보지 않는다."""
    return bool(_MARKET_RE.fullmatch(str(code or "").strip().upper()))


def fetch_ticker(market: str) -> dict | None:
    """마켓 하나의 시세 dict(업비트 응답 그대로). 없는 마켓(404)·빈 응답·네트워크 실패는 None."""
    m = str(market or "").strip().upper()
    if not is_upbit(m):
        return None
    try:
        resp = requests.get(_TICKER_URL, params={"markets": m}, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    row = data[0]
    if row.get("market") != m or not isinstance(row.get("trade_price"), (int, float)):
        return None
    return row


def korean_name(market: str) -> str | None:
    """market/all에서 korean_name. 못 구하면 None(호출부가 마켓코드로 대신 표시)."""
    m = str(market or "").strip().upper()
    try:
        resp = requests.get(_MARKETS_URL, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        for row in resp.json() or []:
            if isinstance(row, dict) and row.get("market") == m:
                return row.get("korean_name") or None
    except (requests.RequestException, ValueError):
        return None
    return None


def resolve(market: str) -> dict | None:
    """실재 확인 + 표시 정보. 실재는 **ticker 응답**으로만 판정한다(목록에만 있고 시세가
    없는 마켓을 통과시키지 않는다). 반환: {code, name, price} 또는 None."""
    m = str(market or "").strip().upper()
    row = fetch_ticker(m)
    if not row:
        return None
    return {"code": m, "name": korean_name(m) or m, "price": row["trade_price"]}


_CANDLE_URL = "https://api.upbit.com/v1/candles/{unit}"
CANDLE_MAX = 200   # 업비트 공개 캔들 API의 1회 최대 개수(문서 값)


def fetch_candles(market: str, unit: str = "days", count: int = CANDLE_MAX):
    """v5.314+ 저점 평가용 일봉/월봉(unit="days"|"months"). 공개 API 1회 호출, 최신→과거 응답을 날짜
    오름차순 DataFrame(Open·High·Low·Close·Volume, KST 날짜 인덱스)으로. 실패면 None."""
    import pandas as pd
    m = str(market or "").strip().upper()
    if not is_upbit(m) or unit not in ("days", "months"):
        return None
    try:
        resp = requests.get(_CANDLE_URL.format(unit=unit), params={"market": m, "count": min(count, CANDLE_MAX)},
                            timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        rows = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(rows, list) or not rows:
        return None
    df = pd.DataFrame({
        "Open": [r.get("opening_price") for r in rows], "High": [r.get("high_price") for r in rows],
        "Low": [r.get("low_price") for r in rows], "Close": [r.get("trade_price") for r in rows],
        "Volume": [r.get("candle_acc_trade_volume") for r in rows],
    }, index=pd.to_datetime([str(r.get("candle_date_time_kst", ""))[:10] for r in rows]))
    return df.sort_index().astype(float)

"""
펀더멘털(밸류에이션) 조회 — 카드 '밸류 보기' 클릭 시 온디맨드 호출.

참고용 배지 전용. 진입 조건엔 쓰지 않는다.
(미너비니/오닐 셋업은 가격·거래량·추세가 핵심. 밸류는 보조 참고.)

판정 기준 — PER밴드(과거 PER 분포에서 현재 위치):
  - 현재 PER ≤ 하위 25% 분위 → "싸다"
  - 25% ~ 75% → "적정"
  - > 75% 분위 → "비싸다"
밴드 계산 불가 시(데이터 부족) → PER 숫자만 표시, 판정은 None.

미국주: yfinance (info + 과거 가격 ÷ EPS(TTM)로 PER 시계열)
한국주: 네이버 모바일 integration API (현재 PER/PBR/ROE)
        ※ 한국주는 과거 PER밴드 데이터 한계 → 현재 지표 위주.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import naver_kr


# ── 공통 판정 ─────────────────────────────────────
_PER_MAX = 200   # 이보다 크면 '이익 미미/적자 근접'으로 간주, 숫자 무의미


def _sane_per(per: float | None) -> tuple[float | None, str | None]:
    """PER 위생 처리. 적자(≤0)·이익 미미(과대 PER)는 숫자 대신 사유 플래그.
    반환: (표시용 PER 또는 None, 사유 라벨 또는 None)."""
    if per is None:
        return None, None
    if per <= 0:
        return None, "적자"          # 음수 PER = 순이익 적자
    if per > _PER_MAX:
        return None, "이익 미미"      # PER 수백~수만 = EPS 거의 0 → 무의미
    return per, None


def _safety_margin(tp: float | None, close: float | None) -> dict:
    """안전마진 진입가 판정 (유튜브 영상식).
    컨센 목표주가(TP)에 안전마진 20~30% 적용 → TP×0.7 ~ TP×0.8 구간.
    현재가가 이 구간 상단(TP×0.8) 이하면 '진입 가능'.
    반환: {tp, sm_low(×0.7), sm_high(×0.8), sm_enter(bool), sm_upside%}"""
    if not (isinstance(tp, (int, float)) and tp > 0 and isinstance(close, (int, float)) and close > 0):
        return {"tp": None, "sm_low": None, "sm_high": None, "sm_enter": None, "sm_upside": None}
    sm_low = tp * 0.7    # 안전마진 30%
    sm_high = tp * 0.8   # 안전마진 20%
    return {
        "tp": round(tp, 2),
        "sm_low": round(sm_low, 2),
        "sm_high": round(sm_high, 2),
        "sm_enter": close <= sm_high,          # 안전마진 20% 가격 이하면 진입권
        "sm_upside": round((tp / close - 1) * 100, 1),   # 목표가까지 상승여력 %
    }


def _band_verdict(cur_per: float | None, per_series: pd.Series | None) -> dict:
    """현재 PER을 과거 PER 분포에 대입해 싸다/적정/비싸다 판정."""
    if cur_per is None or cur_per <= 0:
        return {"verdict": None, "pct_in_band": None, "band_low": None, "band_high": None}
    if per_series is None or len(per_series) < 60:   # 최소 60거래일
        return {"verdict": None, "pct_in_band": None, "band_low": None, "band_high": None}

    s = per_series.dropna()
    s = s[(s > 0) & (s < s.quantile(0.99) * 1.5)]   # 극단치 제거
    if len(s) < 60:
        return {"verdict": None, "pct_in_band": None, "band_low": None, "band_high": None}

    q25, q75 = float(s.quantile(0.25)), float(s.quantile(0.75))
    # 현재 PER의 백분위 위치
    pct = float((s < cur_per).mean() * 100)
    if cur_per <= q25:
        verdict = "싸다"
    elif cur_per <= q75:
        verdict = "적정"
    else:
        verdict = "비싸다"
    return {
        "verdict": verdict,
        "pct_in_band": round(pct, 0),
        "band_low": round(q25, 1),
        "band_high": round(q75, 1),
    }


# ── 미국주 ─────────────────────────────────────
def _us_fundamentals(ticker: str) -> dict | None:
    import yfinance as yf
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception:
        return None

    roe = info.get("returnOnEquity")
    roe_pct = round(roe * 100, 0) if isinstance(roe, (int, float)) else None
    fwd_pe = info.get("forwardPE")
    trail_pe = info.get("trailingPE")
    fwd_eps = info.get("forwardEps")
    tp = info.get("targetMeanPrice")      # 컨센 평균 목표주가
    close = info.get("currentPrice") or info.get("regularMarketPrice")

    # 과거 PER 시계열 = 일별 종가 ÷ EPS(TTM)
    per_series = None
    try:
        eps_ttm = info.get("trailingEps")
        if eps_ttm and eps_ttm > 0:
            hist = tk.history(period="5y", interval="1d", auto_adjust=False)
            if hist is not None and not hist.empty:
                # EPS는 현재값 고정 근사 (정밀 시계열 EPS는 yfinance 한계)
                per_series = hist["Close"] / eps_ttm
    except Exception:
        per_series = None

    # PER 위생 처리: 적자/이익 미미면 숫자 대신 사유 플래그
    trail_disp, trail_flag = _sane_per(trail_pe if isinstance(trail_pe, (int, float)) else None)
    fwd_disp, fwd_flag = _sane_per(fwd_pe if isinstance(fwd_pe, (int, float)) else None)
    band = _band_verdict(trail_disp, per_series) if trail_disp else \
           {"verdict": None, "pct_in_band": None, "band_low": None, "band_high": None}

    return {
        "market": "US",
        "roe": roe_pct,
        "fwd_pe": round(fwd_disp, 1) if isinstance(fwd_disp, (int, float)) else None,
        "trail_pe": round(trail_disp, 1) if isinstance(trail_disp, (int, float)) else None,
        "fwd_eps": round(fwd_eps, 2) if isinstance(fwd_eps, (int, float)) else None,
        "per_flag": trail_flag or fwd_flag,
        "growing": (isinstance(fwd_disp, (int, float)) and isinstance(trail_disp, (int, float))
                    and fwd_disp < trail_disp),   # Fwd<Trail → 이익 성장 예상
        # 선행 PER 8배 미만이면 '싸다'(영상 기준, 메모리/시클리컬에 특히 유효)
        "per_cheap": (isinstance(fwd_disp, (int, float)) and fwd_disp < 8),
        **_safety_margin(tp, close),
        **band,
    }


# ── 한국주 ─────────────────────────────────────
def _kr_fundamentals(ticker: str) -> dict | None:
    """네이버 모바일 integration API에서 PER/PBR/ROE 파싱.
    ※ 실제 응답 필드명은 배포 환경에서 확인 후 보정 필요(아래 후보 경로 다중 시도)."""
    import requests
    code = naver_kr.to_code(ticker)
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    try:
        resp = requests.get(url, headers=naver_kr._HEADERS, timeout=naver_kr._TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    per = pbr = eps = cns_per = cns_eps = None
    close_kr = None
    # totalInfos value엔 단위가 붙음: "62.63배", "20,484원", "4.99배" → _num()로 제거
    for item in (data.get("totalInfos") or []):
        c = str(item.get("code", ""))
        v = _num(item.get("value"))
        if c == "per" and per is None:
            per = v
        elif c == "pbr" and pbr is None:
            pbr = v
        elif c == "eps" and eps is None:
            eps = v
        elif c == "cnsPer" and cns_per is None:    # 추정(선행) PER
            cns_per = v
        elif c == "cnsEps" and cns_eps is None:    # 추정(선행) EPS
            cns_eps = v
        elif c in ("lastClosePrice", "closePrice") and close_kr is None:
            close_kr = v
    # ※ 네이버 integration API엔 ROE 필드가 없음 → 미표시.
    # 현재가: dealTrendInfos(최근 거래일) 우선
    try:
        dti = data.get("dealTrendInfos") or []
        if dti:
            cp = _num(dti[0].get("closePrice"))
            if cp:
                close_kr = cp
    except Exception:
        pass
    # 목표주가(TP): 네이버 integration엔 보통 없음. 있으면 사용(없으면 None).
    tp_kr = None
    for k in ("targetPrice", "consensusTargetPrice", "tp"):
        if data.get(k):
            tp_kr = _num(data.get(k))
            if tp_kr:
                break

    # 한국주 과거 PER밴드: 네이버 일봉 + 현재 EPS 근사로 역산 시도
    per_series = None
    try:
        if eps and eps > 0:
            df = naver_kr.fetch_history(ticker, days=400)
            if df is not None and not df.empty:
                per_series = df["Close"] / eps
    except Exception:
        per_series = None

    # PER 위생 처리: 적자/이익 미미면 숫자 대신 사유 플래그
    per_disp, per_flag = _sane_per(per)
    cns_disp, cns_flag = _sane_per(cns_per)
    # 밴드는 정상 PER일 때만 의미 있음
    band = _band_verdict(per_disp, per_series) if per_disp else \
           {"verdict": None, "pct_in_band": None, "band_low": None, "band_high": None}

    return {
        "market": "KR",
        "roe": None,                 # 네이버 integration API엔 ROE 없음
        "fwd_pe": round(cns_disp, 1) if isinstance(cns_disp, (int, float)) else None,  # 추정PER
        "trail_pe": round(per_disp, 1) if isinstance(per_disp, (int, float)) else None,
        "pbr": round(pbr, 2) if isinstance(pbr, (int, float)) else None,
        "fwd_eps": round(cns_eps, 0) if isinstance(cns_eps, (int, float)) else None,
        "per_flag": per_flag or cns_flag,   # '적자' / '이익 미미' / None
        # 추정PER < 현재PER → 이익 성장 예상 (둘 다 정상 PER일 때만)
        "growing": (isinstance(cns_disp, (int, float)) and isinstance(per_disp, (int, float))
                    and 0 < cns_disp < per_disp),
        # 추정PER 8배 미만이면 '싸다'(영상 기준)
        "per_cheap": (isinstance(cns_disp, (int, float)) and cns_disp < 8),
        **_safety_margin(tp_kr, close_kr),
        **band,
    }


def _num(v) -> float | None:
    """단위 붙은 네이버 value 파싱: '62.63배'·'20,484원'·'4.99%'·'1,754,851백만' → float."""
    if v is None:
        return None
    s = str(v).strip()
    # 숫자/소수점/마이너스만 남기고 제거 (콤마·단위·공백 모두)
    import re as _re
    m = _re.search(r"-?[\d,]+\.?\d*", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


# ── 진입점 ─────────────────────────────────────
def get_fundamentals(ticker: str) -> dict | None:
    """티커 1개의 밸류에이션 지표. 미/한 자동 분기."""
    if naver_kr.is_kr(ticker):
        return _kr_fundamentals(ticker)
    return _us_fundamentals(ticker)

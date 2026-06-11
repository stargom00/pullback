"""
눌림목 스캐너 — 핵심 탐지 로직
조건: 우상향 추세 + 이평선 부근 조정 + 거래량 감소 + RSI 중립권
"""
import math

import pandas as pd


# ── 설정 ──────────────────────────────────────────────
CONFIG = {
    "min_bars": 80,            # 최소 일봉 개수
    "ma_short": 10,
    "ma_mid": 20,
    "ma_long": 60,
    "pullback_min": 0.03,      # 최근 고점 대비 최소 조정폭 3%
    "pullback_max": 0.18,      # 최대 조정폭 18% (이상이면 추세 훼손 간주)
    "ma_proximity": 0.035,     # 이평선과의 거리 허용치 3.5%
    "vol_contraction": 0.85,   # 최근 3일 평균 거래량 < 20일 평균 × 0.85
    "rsi_min": 35,
    "rsi_max": 62,
    "recent_high_window": 40,  # 60일 고점이 최근 N봉 안에 있어야 함
}


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, math.nan)
    return 100 - (100 / (1 + rs))


def analyze(df: pd.DataFrame, cfg: dict = CONFIG) -> dict | None:
    """
    일봉 DataFrame(Open/High/Low/Close/Volume)을 받아
    눌림목 조건 충족 여부와 점수를 반환. 미충족이면 None.
    """
    if df is None or len(df) < cfg["min_bars"]:
        return None

    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None

    c = df["Close"]
    v = df["Volume"]

    ma10 = c.rolling(cfg["ma_short"]).mean()
    ma20 = c.rolling(cfg["ma_mid"]).mean()
    ma60 = c.rolling(cfg["ma_long"]).mean()
    r = rsi(c)

    close = float(c.iloc[-1])
    m10, m20, m60 = float(ma10.iloc[-1]), float(ma20.iloc[-1]), float(ma60.iloc[-1])
    cur_rsi = float(r.iloc[-1])

    if any(math.isnan(x) for x in (m10, m20, m60, cur_rsi)):
        return None

    # ── 1) 우상향 추세 ──
    trend_above_ma60 = close > m60
    ma_stack = m20 > m60
    ma20_slope = m20 > float(ma20.iloc[-11])  # 20일선이 10봉 전보다 위
    in_uptrend = trend_above_ma60 and ma_stack and ma20_slope
    if not in_uptrend:
        return None

    # ── 2) 최근 고점이 살아있는가 ──
    last60 = c.iloc[-60:].reset_index(drop=True)
    high60 = float(last60.max())
    bars_since_high = len(last60) - 1 - int(last60.idxmax())
    recent_high_ok = bars_since_high <= cfg["recent_high_window"]

    # ── 3) 조정폭 (눌림 깊이) ──
    pullback = (high60 - close) / high60
    pullback_ok = cfg["pullback_min"] <= pullback <= cfg["pullback_max"]
    if not pullback_ok:
        return None

    # ── 4) 이평선 지지 (10/20/60일선 중 하나에 근접) ──
    dist10 = (close - m10) / m10
    dist20 = (close - m20) / m20
    dist60 = (close - m60) / m60
    prox = cfg["ma_proximity"]
    near_ma = min(abs(dist10), abs(dist20), abs(dist60))
    ma_touch = near_ma <= prox
    support_ma = min(
        [(abs(dist10), "MA10"), (abs(dist20), "MA20"), (abs(dist60), "MA60")]
    )[1]
    if not ma_touch:
        return None

    # ── 5) 거래량 수축 ──
    vol3 = float(v.iloc[-3:].mean())
    vol20 = float(v.iloc[-20:].mean())
    vol_ratio = vol3 / vol20 if vol20 > 0 else 9.9
    vol_dry = vol_ratio <= cfg["vol_contraction"]

    # ── 6) RSI 중립권 ──
    rsi_ok = cfg["rsi_min"] <= cur_rsi <= cfg["rsi_max"]
    if not rsi_ok:
        return None

    # ── 7) 캔들 수축 (VCP 보너스) — 최근 5봉 평균 변동폭 < 직전 10봉 ──
    rng = (df["High"] - df["Low"]) / df["Close"]
    tightening = float(rng.iloc[-5:].mean()) < float(rng.iloc[-15:-5].mean())

    # ── 점수화 (100점 만점) ──
    score = 0.0
    # 눌림 깊이: 5~10%가 이상적
    ideal = 1 - min(abs(pullback - 0.075) / 0.075, 1)
    score += 25 * ideal
    # 이평선 밀착도
    score += 25 * (1 - near_ma / prox)
    # 거래량 수축 정도
    score += 25 * max(0.0, min(1.0, (1.1 - vol_ratio) / 0.5))
    # RSI 위치: 45 부근이 이상적
    score += 15 * (1 - min(abs(cur_rsi - 45) / 20, 1))
    # 보너스
    score += 5 if tightening else 0
    score += 5 if recent_high_ok else 0

    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0

    return {
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "pullback_pct": round(pullback * 100, 1),
        "support_ma": support_ma,
        "ma_dist_pct": round(near_ma * 100, 2),
        "vol_ratio": round(vol_ratio, 2),
        "vol_dry": vol_dry,
        "rsi": round(cur_rsi, 1),
        "tightening": tightening,
        "recent_high_ok": recent_high_ok,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }

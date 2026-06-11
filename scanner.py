"""
눌림목 스캐너 v2 — 핵심 탐지 로직
조건: 우상향 추세(200일선 포함) + 이평선 부근 조정 + 거래량 감소
      + RSI 중립권 + RS(유니버스 내 상대강도) 50 이상
추가: 피벗(돌파가) / 손절가 / 리스크 % 계산
"""
import math

import pandas as pd


# ── 설정 ──────────────────────────────────────────────
CONFIG = {
    "min_bars": 210,           # 최소 일봉 개수 (200일선 계산용)
    "ma_short": 10,
    "ma_mid": 20,
    "ma_long": 60,
    "ma_trend": 200,           # 장기 추세 필터
    "pullback_min": 0.03,      # 최근 고점 대비 최소 조정폭 3%
    "pullback_max": 0.18,      # 최대 조정폭 18% (이상이면 추세 훼손 간주)
    "ma_proximity": 0.035,     # 이평선과의 거리 허용치 3.5%
    "vol_contraction": 0.85,   # 최근 3일 평균 거래량 < 20일 평균 × 0.85
    "rsi_min": 35,
    "rsi_max": 62,
    "recent_high_window": 40,  # 60일 고점이 최근 N봉 안에 있어야 함
    "rs_min": 50,              # RS 등급 최소치 (유니버스 내 백분위)
    "pivot_window": 10,        # 피벗(돌파가) = 직전 N봉 고가
    # 주도주(RS 90+) 완화 기준: 얕고 짧은 눌림도 인정
    "leader_rs": 90,
    "leader_pullback_min": 0.015,
    "leader_rsi_max": 72,
}


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, math.nan)
    return 100 - (100 / (1 + rs))


def rs_raw_score(close: pd.Series) -> float | None:
    """
    IBD 방식 상대강도 원점수: 최근 3개월 수익률에 2배 가중,
    6/9/12개월 수익률 각 1배. (63/126/189/252 거래일 기준)
    유니버스 전체에서 백분위로 환산해 RS 등급(1~99)이 됨.
    """
    c = close.dropna()
    if len(c) < 200:
        return None
    now = float(c.iloc[-1])

    def ret(days):
        idx = -min(days, len(c) - 1) - 1
        past = float(c.iloc[idx])
        return now / past - 1 if past > 0 else 0.0

    return 2 * ret(63) + ret(126) + ret(189) + ret(252)


def to_rs_rank(raw_scores: dict[str, float]) -> dict[str, int]:
    """원점수 dict → 백분위(1~99) dict"""
    valid = {t: s for t, s in raw_scores.items() if s is not None}
    n = len(valid)
    if n == 0:
        return {}
    ordered = sorted(valid.items(), key=lambda kv: kv[1])
    ranks = {}
    for i, (t, _) in enumerate(ordered):
        ranks[t] = max(1, min(99, round((i + 1) / n * 99)))
    return ranks


def analyze(df: pd.DataFrame, rs_rank: int | None = None, cfg: dict = CONFIG) -> dict | None:
    """
    일봉 DataFrame(Open/High/Low/Close/Volume)을 받아
    눌림목 조건 충족 여부와 점수를 반환. 미충족이면 None.
    rs_rank: 유니버스 내 상대강도 백분위 (1~99). None이면 RS 필터 생략.
    """
    if df is None or len(df) < cfg["min_bars"]:
        return None

    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None

    # ── 0) RS 필터 + 주도주 판정 ──
    if rs_rank is not None and rs_rank < cfg["rs_min"]:
        return None
    is_leader = rs_rank is not None and rs_rank >= cfg["leader_rs"]
    pb_min = cfg["leader_pullback_min"] if is_leader else cfg["pullback_min"]
    rsi_max = cfg["leader_rsi_max"] if is_leader else cfg["rsi_max"]

    c = df["Close"]
    h = df["High"]
    lo = df["Low"]
    v = df["Volume"]

    ma10 = c.rolling(cfg["ma_short"]).mean()
    ma20 = c.rolling(cfg["ma_mid"]).mean()
    ma60 = c.rolling(cfg["ma_long"]).mean()
    ma200 = c.rolling(cfg["ma_trend"]).mean()
    r = rsi(c)

    close = float(c.iloc[-1])
    m10, m20, m60 = float(ma10.iloc[-1]), float(ma20.iloc[-1]), float(ma60.iloc[-1])
    m200 = float(ma200.iloc[-1])
    cur_rsi = float(r.iloc[-1])

    if any(math.isnan(x) for x in (m10, m20, m60, m200, cur_rsi)):
        return None

    # ── 1) 우상향 추세 (장기 추세 포함) ──
    trend_above_ma60 = close > m60
    above_ma200 = close > m200          # 200일선 위 = Stage 2 추세만
    ma_stack = m20 > m60
    ma20_slope = m20 > float(ma20.iloc[-11])
    in_uptrend = trend_above_ma60 and above_ma200 and ma_stack and ma20_slope
    if not in_uptrend:
        return None

    # ── 2) 최근 고점이 살아있는가 ──
    last60 = c.iloc[-60:].reset_index(drop=True)
    high60 = float(last60.max())
    bars_since_high = len(last60) - 1 - int(last60.idxmax())
    recent_high_ok = bars_since_high <= cfg["recent_high_window"]

    # ── 3) 조정폭 (눌림 깊이) ──
    pullback = (high60 - close) / high60
    pullback_ok = pb_min <= pullback <= cfg["pullback_max"]
    if not pullback_ok:
        return None

    # ── 4) 이평선 지지 ──
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
    rsi_ok = cfg["rsi_min"] <= cur_rsi <= rsi_max
    if not rsi_ok:
        return None

    # ── 7) 캔들 수축 (VCP 보너스) ──
    rng = (h - lo) / c
    tightening = float(rng.iloc[-5:].mean()) < float(rng.iloc[-15:-5].mean())

    # ── 8) 피벗 / 손절 / 리스크 ──
    pw = cfg["pivot_window"]
    pivot = float(h.iloc[-pw:].max())          # 직전 N봉 고가 = 돌파 트리거
    pullback_low = float(lo.iloc[-pw:].min())  # 눌림 저점
    # 손절 = 눌림 저점과 지지 이평선(1% 이탈 허용) 중 더 높은 쪽
    # 단, 현재가보다 위에 있는 후보는 제외 (가격이 지지선 살짝 아래일 때 방지)
    support_price = {"MA10": m10, "MA20": m20, "MA60": m60}[support_ma]
    candidates = [x for x in (pullback_low, support_price * 0.99) if x < close]
    stop = max(candidates) if candidates else pullback_low
    risk_pct = (pivot - stop) / pivot * 100 if pivot > 0 else 0.0
    pivot_dist_pct = (pivot - close) / close * 100  # 현재가→피벗 거리

    # ── 점수화 (100점 만점) ──
    score = 0.0
    ideal = 1 - min(abs(pullback - 0.075) / 0.075, 1)
    score += 20 * ideal
    score += 20 * (1 - near_ma / prox)
    score += 20 * max(0.0, min(1.0, (1.1 - vol_ratio) / 0.5))
    score += 15 * (1 - min(abs(cur_rsi - 45) / 20, 1))
    if rs_rank is not None:                     # RS 기여 (최대 15점)
        score += 15 * max(0.0, (rs_rank - 50) / 49)
    score += 5 if tightening else 0
    score += 5 if recent_high_ok else 0

    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0

    return {
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "rs": rs_rank,
        "leader": is_leader,
        "pullback_pct": round(pullback * 100, 1),
        "support_ma": support_ma,
        "ma_dist_pct": round(near_ma * 100, 2),
        "vol_ratio": round(vol_ratio, 2),
        "vol_dry": vol_dry,
        "rsi": round(cur_rsi, 1),
        "tightening": tightening,
        "recent_high_ok": recent_high_ok,
        "pivot": round(pivot, 2),
        "pivot_dist_pct": round(pivot_dist_pct, 2),
        "stop": round(stop, 2),
        "risk_pct": round(risk_pct, 2),
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }

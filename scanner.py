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




def trendline_level(h: pd.Series, lookback: int = 40, order: int = 2):
    """
    최근 lookback봉의 스윙 고점들로 하락 추세선을 그어 오늘의 추세선 값을 반환.
    스윙 고점 2개 미만이거나 기울기가 하락이 아니면 None.
    """
    seg = h.iloc[-lookback:].reset_index(drop=True)
    n = len(seg)
    if n < lookback:
        return None
    peaks = []
    for i in range(order, n - order):
        window = seg.iloc[i - order:i + order + 1]
        if seg.iloc[i] >= float(window.max()):
            peaks.append((i, float(seg.iloc[i])))
    if len(peaks) < 2:
        return None
    peaks = peaks[-3:]  # 최근 고점 최대 3개
    xs = [p[0] for p in peaks]
    ys = [p[1] for p in peaks]
    # 1차 직선 적합
    npts = len(xs)
    mean_x, mean_y = sum(xs) / npts, sum(ys) / npts
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    if slope >= 0:
        return None  # 하락 추세선만 의미 있음
    intercept = mean_y - slope * mean_x
    level = slope * (n - 1) + intercept
    return level if level > 0 else None


def select_pivot(h, lo, c, close, recent_high_window: int):
    """
    피벗 후보 3종 중 현재가 위에서 가장 가까운 것 선택.
    - 타이트존: 최근 5봉 고가 (VCP 마지막 수축 상단)
    - 전고: 최근 N봉 고가
    - 추세선: 하락 추세선의 오늘 값
    반환: (pivot, pivot_type, tl_break)
    tl_break = 최근 3봉 내 추세선 상향 돌파 여부 (미너비니 조기 신호)
    """
    cands = []
    hi5 = float(h.iloc[-5:].max())
    cands.append((hi5, "타이트존"))
    hiN = float(h.iloc[-recent_high_window:].max())
    cands.append((hiN, "전고"))
    tl = trendline_level(h)
    tl_break = False
    if tl is not None:
        if close > tl and float(c.iloc[-3]) <= tl:
            tl_break = True          # 갓 돌파 → 배지
        elif close <= tl:
            cands.append((tl, "추세선"))
    above = [(p, t) for p, t in cands if p > close * 1.001]
    if above:
        pivot, ptype = min(above, key=lambda x: x[0])
    else:
        pivot, ptype = max(cands, key=lambda x: x[0])
    return pivot, ptype, tl_break


def ud_volume_ratio(c: pd.Series, v: pd.Series, days: int = 10) -> float:
    """상승일 거래량 합 / 하락일 거래량 합 (최근 N일). 1보다 크면 매집 우위."""
    ret = c.diff().iloc[-days:]
    vv = v.iloc[-days:]
    up = float(vv[ret > 0].sum())
    down = float(vv[ret < 0].sum())
    if down <= 0:
        return 9.9
    return round(min(up / down, 9.9), 2)


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


def analyze(df: pd.DataFrame, rs_rank: int | None = None, rs_mom: int | None = None, cfg: dict = CONFIG, _setup_eval: bool = False) -> dict | None:
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
    # 주도주(RS90+)는 20일선이 평평해도 허용 (VCP 베이스 빌딩 중 정상)
    slope_floor = 0.98 if is_leader else 1.0  # 주도주는 10봉간 -2%까지 허용
    ma20_slope = m20 > float(ma20.iloc[-11]) * slope_floor
    in_uptrend = trend_above_ma60 and above_ma200 and ma_stack and ma20_slope
    if not in_uptrend:
        return None

    # ── 돌파일 판정: +4% 이상 양봉이면 셋업은 "전날 기준"으로 평가 ──
    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
    breakout_day = change_pct >= 4.0

    # ── 2) 최근 고점이 살아있는가 ──
    last60 = c.iloc[-60:].reset_index(drop=True)
    high60 = float(last60.max())
    bars_since_high = len(last60) - 1 - int(last60.idxmax())
    recent_high_ok = bars_since_high <= cfg["recent_high_window"]

    # ── 3) 조정폭 (눌림 깊이) — 돌파일엔 전날 종가/전날까지의 고점 기준 ──
    if breakout_day:
        high60_ref = float(c.iloc[-61:-1].max())
        pullback = (high60_ref - prev_close) / high60_ref
    else:
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
    # 돌파일(+4% 이상 양봉)에는 그날 상승분만큼 거리 허용 — 출발하는 날 목록에서 사라지지 않게
    prox_allow = prox + max(0.0, change_pct / 100) if change_pct >= 4.0 else prox
    ma_touch = near_ma <= prox_allow
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

    # ── 6) RSI 중립권 — 돌파일엔 전날 RSI로 평가 ──
    rsi_eval = float(r.iloc[-2]) if breakout_day else cur_rsi
    rsi_ok = cfg["rsi_min"] <= rsi_eval <= rsi_max
    if not rsi_ok:
        return None

    # ── 7) 캔들 수축 (VCP 보너스) ──
    rng = (h - lo) / c
    tightening = float(rng.iloc[-5:].mean()) < float(rng.iloc[-15:-5].mean())

    # ── 8) 피벗 / 손절 / 리스크 ──
    pw = cfg["pivot_window"]
    pivot, pivot_type, tl_break = select_pivot(h, lo, c, close, pw)
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
    score += 20 * max(0.0, 1 - near_ma / prox)
    score += 20 * max(0.0, min(1.0, (1.1 - vol_ratio) / 0.5))
    score += 15 * (1 - min(abs(cur_rsi - 45) / 20, 1))
    if rs_rank is not None:                     # RS 기여 (최대 15점)
        score += 15 * max(0.0, (rs_rank - 50) / 49)
    score += 5 if tightening else 0
    score += 5 if recent_high_ok else 0
    score += 3 if (rs_mom is not None and rs_mom >= 10) else 0
    # RS 곱셈 반영: 힘(RS) × 모양 — 둘 다 좋아야 고득점
    if rs_rank is not None:
        score *= 0.7 + 0.3 * rs_rank / 99

    # 🔥 트리거 발동: 당일 강한 양봉 + (추세선 돌파 or 피벗 코앞/돌파)
    triggered = change_pct >= 4.0 and (tl_break or pivot_dist_pct <= 2.0)
    # 전날 셋업 점수: 오늘 봉을 빼고 재평가 (🔥 카드 표시용, 재귀 1회 제한)
    setup_score = None
    if triggered and not _setup_eval:
        prev = analyze(df.iloc[:-1], rs_rank=rs_rank, rs_mom=rs_mom, cfg=cfg, _setup_eval=True)
        if prev:
            setup_score = prev["score"]

    return {
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "triggered": triggered,
        "setup_score": setup_score,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "leader": is_leader,
        "mode": "pullback",
        "pullback_pct": round(pullback * 100, 1),
        "support_ma": support_ma,
        "ma_dist_pct": round(near_ma * 100, 2),
        "vol_ratio": round(vol_ratio, 2),
        "vol_dry": vol_dry,
        "rsi": round(cur_rsi, 1),
        "tightening": tightening,
        "recent_high_ok": recent_high_ok,
        "pivot": round(pivot, 2),
        "pivot_type": pivot_type,
        "tl_break": tl_break,
        "ud": ud_volume_ratio(c, v),
        "pivot_dist_pct": round(pivot_dist_pct, 2),
        "stop": round(stop, 2),
        "risk_pct": round(risk_pct, 2),
        "risk_warn": risk_pct > 8.0,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# 추세 전환 스캔: 역배열 → 정배열 첫 형성 (최근 1개월 내)
# ══════════════════════════════════════════════════════
TURN_CONFIG = {
    "min_bars": 210,
    "align_window": 22,      # 정배열 형성이 최근 N봉 이내여야 함
    "max_ma200_dist": 0.25,  # 200일선에서 25% 이상 떨어졌으면 이미 늦음
    "rs_min": 30,            # 전환 초입은 RS가 낮은 게 정상 → 완화
}


def analyze_turnaround(df: pd.DataFrame, rs_rank: int | None = None,
                       rs_mom: int | None = None, cfg: dict = TURN_CONFIG, _setup_eval: bool = False) -> dict | None:
    """역배열에서 정배열(20>60>200, 종가>200일선)로 갓 전환한 종목 탐지"""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None

    if rs_rank is not None and rs_rank < cfg["rs_min"]:
        return None
    # RS 모멘텀이 명확히 꺾인 종목은 제외 (전환의 핵심 = 상대강도 개선)
    if rs_mom is not None and rs_mom < 0:
        return None

    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    ma200 = c.rolling(200).mean()
    r = rsi(c)

    close = float(c.iloc[-1])
    m20, m60, m200 = float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma200.iloc[-1])
    cur_rsi = float(r.iloc[-1])
    if any(math.isnan(x) for x in (m20, m60, m200, cur_rsi)):
        return None

    # 정배열 시리즈 (오늘 포함 최근 구간)
    aligned = (ma20 > ma60) & (ma60 > ma200) & (c > ma200)
    if not bool(aligned.iloc[-1]):
        return None
    # 며칠 전에 처음 정배열이 됐는가 (직전 False까지 거슬러)
    align_days = 0
    for val in reversed(aligned.tolist()):
        if val:
            align_days += 1
        else:
            break
    if align_days > cfg["align_window"]:
        return None  # 이미 한 달 넘게 정배열 → 전환 아님

    # 200일선에서 너무 멀면(이미 급등) 제외
    ma200_dist = (close - m200) / m200
    if ma200_dist > cfg["max_ma200_dist"]:
        return None

    # 거래량: 전환 구간(최근 10일)이 평소(50일)보다 늘었는가 (확장이 좋음)
    vol10 = float(v.iloc[-10:].mean())
    vol50 = float(v.iloc[-50:].mean())
    vol_ratio = vol10 / vol50 if vol50 > 0 else 0.0

    # 피벗: 20봉 고가/타이트존/하락추세선 중 가장 가까운 트리거, 손절은 60일선 -2%
    pivot, pivot_type, tl_break = select_pivot(h, lo, c, close, 20)
    ud = ud_volume_ratio(c, v)
    stop = m60 * 0.98
    candidates = [x for x in (stop, float(lo.iloc[-10:].min())) if x < close]
    stop = max(candidates) if candidates else float(lo.iloc[-10:].min())
    risk_pct = (pivot - stop) / pivot * 100 if pivot > 0 else 0.0
    pivot_dist_pct = (pivot - close) / close * 100

    # ── 점수 (100점) ──
    score = 0.0
    score += 30 * (cfg["align_window"] + 1 - align_days) / cfg["align_window"]  # 신선도
    if rs_mom is not None:
        score += 25 * max(0.0, min(rs_mom, 40)) / 40                            # RS 개선 폭
    if rs_rank is not None:
        score += 15 * rs_rank / 99                                              # 현재 RS
    score += 15 * max(0.0, min((vol_ratio - 0.9) / 0.9, 1.0))                   # 거래량 확장
    score += 15 * (1 - min(ma200_dist, 0.25) / 0.25)                            # 200일선 근접

    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
    triggered = change_pct >= 4.0 and (tl_break or pivot_dist_pct <= 2.0)
    setup_score = None
    if triggered and not _setup_eval:
        prev = analyze_turnaround(df.iloc[:-1], rs_rank=rs_rank, rs_mom=rs_mom, cfg=cfg, _setup_eval=True)
        if prev:
            setup_score = prev["score"]

    return {
        "mode": "turnaround",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "triggered": triggered,
        "setup_score": setup_score,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "leader": False,
        "align_days": align_days,
        "ma200_dist_pct": round(ma200_dist * 100, 1),
        "vol_ratio": round(vol_ratio, 2),
        "vol_dry": False,
        "rsi": round(cur_rsi, 1),
        "pivot": round(pivot, 2),
        "pivot_type": pivot_type,
        "tl_break": tl_break,
        "ud": ud,
        "pivot_dist_pct": round(pivot_dist_pct, 2),
        "stop": round(stop, 2),
        "risk_pct": round(risk_pct, 2),
        "risk_warn": risk_pct > 15.0,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# 강세 신고가 스캔: RS 90+ & 신고가 근처 & 아직 눌림 전 (대장 후보)
# ══════════════════════════════════════════════════════
LEADER_CONFIG = {
    "min_bars": 210,
    "rs_min": 88,            # 대장 후보 = 상대강도 최상위
    "near_high": 0.08,       # 60일 고점 대비 8% 이내 (아직 깊이 안 눌림)
    "max_pullback": 0.03,    # 눌림 3% 미만 (= 눌림목 스캐너와 안 겹침)
}


def analyze_leader(df: pd.DataFrame, rs_rank: int | None = None,
                   rs_mom: int | None = None, cfg: dict = LEADER_CONFIG) -> dict | None:
    """RS 최상위 + 신고가 부근 + 아직 눌림 전인 '달리는 대장' 포착.
    눌림목/추세전환과 겹치지 않게 눌림 3% 미만만."""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None
    if rs_rank is None or rs_rank < cfg["rs_min"]:
        return None

    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    ma200 = c.rolling(200).mean()
    r = rsi(c)

    close = float(c.iloc[-1])
    m20, m60, m200 = float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma200.iloc[-1])
    cur_rsi = float(r.iloc[-1])
    if any(math.isnan(x) for x in (m20, m60, m200, cur_rsi)):
        return None

    # 정배열 + 강한 추세
    if not (close > m20 > m60 > m200):
        return None

    high60 = float(c.iloc[-60:].max())
    dist_from_high = (high60 - close) / high60
    # 신고가 8% 이내 AND 눌림 3% 미만 (= 아직 안 쉼)
    if dist_from_high > cfg["near_high"] or dist_from_high >= cfg["max_pullback"]:
        return None

    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
    # 52주 고점 갱신 여부
    high_all = float(h.max())
    at_new_high = close >= high_all * 0.99
    # 다음 눌림 시 지지 후보 = 20일선까지 거리
    ma20_dist_pct = (close - m20) / m20 * 100
    vol_ratio = float(v.iloc[-10:].mean()) / float(v.iloc[-50:].mean()) if float(v.iloc[-50:].mean()) > 0 else 0.0

    # 점수 = RS 중심 (대장 후보는 강함이 전부)
    score = 0.0
    score += 60 * rs_rank / 99
    score += 20 * (1 - min(dist_from_high / cfg["near_high"], 1))   # 신고가 밀착
    score += 10 if at_new_high else 0
    if rs_mom is not None:
        score += 10 * max(0.0, min(rs_mom, 30)) / 30

    return {
        "mode": "leader",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "triggered": False,
        "setup_score": None,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "leader": True,
        "at_new_high": at_new_high,
        "dist_from_high_pct": round(dist_from_high * 100, 1),
        "ma20_dist_pct": round(ma20_dist_pct, 1),
        "vol_ratio": round(vol_ratio, 2),
        "vol_dry": False,
        "rsi": round(cur_rsi, 1),
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# 슈퍼대장 스캔: RS 95+ 무조건 표시 (위치 불문, 지금 가장 강한 종목들)
# ══════════════════════════════════════════════════════
SUPER_CONFIG = {
    "min_bars": 210,
    "rs_min": 95,            # 시장 최상위 상대강도만
}


def analyze_super(df: pd.DataFrame, rs_rank: int | None = None,
                  rs_mom: int | None = None, cfg: dict = SUPER_CONFIG) -> dict | None:
    """RS 95+ 종목을 위치(신고가/눌림/이평선 부근) 무관하게 모두 포착.
    현재 상태를 status로 분류해 '담을곳'인지 '대기'인지 판단 보조."""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None
    if rs_rank is None or rs_rank < cfg["rs_min"]:
        return None

    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    ma50 = c.rolling(50).mean()
    ma200 = c.rolling(200).mean()
    r = rsi(c)

    close = float(c.iloc[-1])
    m10, m20, m50, m200 = [float(x.iloc[-1]) for x in (ma10, ma20, ma50, ma200)]
    cur_rsi = float(r.iloc[-1])
    if any(math.isnan(x) for x in (m20, m50, m200, cur_rsi)):
        return None

    high60 = float(c.iloc[-60:].max())
    dist_from_high = (high60 - close) / high60
    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
    high_all = float(h.max())
    at_new_high = close >= high_all * 0.99

    # 지지선 근접/테스트/반등 판정
    near_ma20 = abs(close - m20) / m20 <= 0.03
    near_ma50 = abs(close - m50) / m50 <= 0.03
    # 어제 종가 대비 오늘 반등했는가 (지지 후 양봉 = 받침 확인 신호)
    bounced = change_pct > 0
    # 최근 3봉 중 저가가 20일선을 찍고 종가는 위 = 지지 테스트 성공 흐름
    low3 = float(lo.iloc[-3:].min())
    tested_ma20 = low3 <= m20 * 1.01 and close > m20

    if at_new_high or dist_from_high <= 0.03:
        status = "신고가"          # 달리는 중 — 추격 금지
    elif near_ma20:
        # 20일선에 닿음 — 받쳤는지 테스트 중인지 구분
        if tested_ma20 and bounced:
            status = "20일선 지지✓"  # 찍고 반등 = 매수 확인 신호
        else:
            status = "20일선 테스트"  # 닿았지만 결과 미확정
    elif near_ma50:
        status = "50일선 지지" if bounced else "50일선 테스트"
    elif dist_from_high <= 0.15:
        status = "눌림 진행"       # 아직 지지선 안 닿음 — 대기
    else:
        status = "조정 깊음"       # 15% 넘게 빠짐 — 추세 점검 필요

    # 다음 매수 후보가(담을곳): 가장 가까운 아래쪽 이평선
    below = [x for x in (m10, m20, m50) if x < close]
    if below:
        buy_zone = max(below)
        buy_zone_dist = (close - buy_zone) / close   # 항상 양수
        near_buy_zone = buy_zone_dist <= 0.03
    else:
        # 현재가가 모든 단기 이평선 아래 = 이미 지지선 밑으로 눌린 상태
        buy_zone = m50
        buy_zone_dist = (close - buy_zone) / close   # 음수일 수 있음
        near_buy_zone = False   # 지지선 아래로 빠졌으면 '근접' 아님

    score = round(60 * rs_rank / 99 + 20 * (1 - min(dist_from_high / 0.15, 1))
                  + (10 if at_new_high else 0)
                  + (10 * max(0.0, min(rs_mom or 0, 30)) / 30), 1)

    return {
        "mode": "super",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": score,
        "triggered": False,
        "setup_score": None,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "leader": True,
        "status": status,
        "near_buy_zone": near_buy_zone,
        "buy_zone_dist_pct": round(buy_zone_dist * 100, 1),
        "at_new_high": at_new_high,
        "dist_from_high_pct": round(dist_from_high * 100, 1),
        "buy_zone": round(buy_zone, 2),
        "rsi": round(cur_rsi, 1),
        "vol_dry": False,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# 돌파 스캔: 베이스(횡보) 직후 박스 천장을 거래량 동반 돌파한 종목
# (눌림목/슈퍼대장이 못 잡는 "방금 이륙" 구간)
# ══════════════════════════════════════════════════════
BREAKOUT_CONFIG = {
    "min_bars": 210,
    "rs_min": 80,            # 돌파는 강한 종목만 의미 있음
    "base_min_len": 20,      # 베이스(횡보) 최소 길이
    "base_max_range": 0.25,  # 베이스 고저 폭이 25% 이내여야 "타이트한 베이스"
    "vol_mult": 1.5,         # 돌파일 거래량 ≥ 평균의 1.5배
    "extended_max": 0.12,    # 피벗 +12% 넘으면 너무 연장 → 제외
    "valid_zone": 0.05,      # 피벗 +5% 이내 = 매수 유효 구간
}


def analyze_breakout(df: pd.DataFrame, rs_rank: int | None = None,
                     rs_mom: int | None = None, cfg: dict = BREAKOUT_CONFIG) -> dict | None:
    """베이스 천장을 거래량 동반 상향 돌파한 종목 포착.
    돌파 후 +5% 이내=매수 유효, +5~12%=연장(추격주의), +12% 초과=제외."""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None
    if rs_rank is None or rs_rank < cfg["rs_min"]:
        return None

    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma50 = c.rolling(50).mean()
    ma200 = c.rolling(200).mean()
    r = rsi(c)

    close = float(c.iloc[-1])
    m50, m200 = float(ma50.iloc[-1]), float(ma200.iloc[-1])
    cur_rsi = float(r.iloc[-1])
    if any(math.isnan(x) for x in (m50, m200, cur_rsi)):
        return None

    # 상승 추세 위에서의 돌파만 (200일선 위)
    if close < m200:
        return None

    # ── 베이스 식별: 돌파일(오늘) 직전 N봉이 횡보였는가 ──
    # 오늘 봉 제외하고, 그 앞 base_min_len~60봉 구간의 고/저
    base = c.iloc[-(cfg["base_min_len"] + 1):-1]   # 오늘 직전 베이스 구간
    if len(base) < cfg["base_min_len"]:
        return None
    base_high = float(base.max())
    base_low = float(base.min())
    if base_high <= 0:
        return None
    base_range = (base_high - base_low) / base_high
    # 베이스가 너무 넓으면(추세 진행 중) 돌파 베이스 아님
    if base_range > cfg["base_max_range"]:
        return None

    # ── 돌파 판정: 오늘 종가가 베이스 천장 위로 ──
    pivot = base_high          # 돌파한 박스 천장 = 피벗
    if close <= pivot:
        return None            # 아직 돌파 안 함

    # 연장도: 피벗 대비 현재가가 얼마나 위인가
    ext = (close - pivot) / pivot
    if ext > cfg["extended_max"]:
        return None            # 너무 연장됨(+12% 초과) → 추격 금지, 제외

    # ── 거래량 동반 확인 ──
    vol_today = float(v.iloc[-1])
    vol_avg = float(v.iloc[-51:-1].mean())   # 직전 50봉 평균(오늘 제외)
    vol_mult = vol_today / vol_avg if vol_avg > 0 else 0.0
    if vol_mult < cfg["vol_mult"]:
        return None            # 거래량 없는 돌파 = 가짜 가능성

    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
    in_valid_zone = ext <= cfg["valid_zone"]   # +5% 이내 = 매수 유효
    base_days = len(base)
    # 손절: 베이스 천장(피벗) 살짝 아래 = 돌파 실패 기준
    stop = round(pivot * 0.97, 2)
    risk_pct = (close - stop) / close * 100 if close > 0 else 0.0

    # 점수 = RS + 거래량 강도 + 유효구간(연장 안 됨) + 베이스 길이
    score = round(
        50 * rs_rank / 99
        + 20 * min(vol_mult / 3.0, 1.0)        # 거래량 3배면 만점
        + 20 * (1 - min(ext / cfg["valid_zone"], 1.0))   # 피벗에 가까울수록 높음
        + 10 * min(base_days / 60, 1.0),       # 베이스 길수록(최대 60봉)
        1)

    return {
        "mode": "breakout",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": score,
        "triggered": in_valid_zone,   # 유효구간이면 카드 강조
        "setup_score": None,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "leader": True,
        "pivot": round(pivot, 2),
        "pivot_type": "베이스 천장",
        "ext_pct": round(ext * 100, 1),
        "in_valid_zone": in_valid_zone,
        "vol_mult": round(vol_mult, 1),
        "base_days": base_days,
        "base_range_pct": round(base_range * 100, 1),
        "stop": stop,
        "risk_pct": round(risk_pct, 2),
        "rsi": round(cur_rsi, 1),
        "vol_dry": False,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# ⚡급등 감지 (실험) — 단타용. 추세추종 아님, RS 무관.
# "오늘 거래량+가격이 터진 것"만 포착. 신호일 뿐 지속 보장 없음.
# ══════════════════════════════════════════════════════
SURGE_CONFIG = {
    "min_bars": 60,          # 급등은 긴 데이터 불필요(단타)
    "vol_mult": 4.0,         # ★조정 포인트: 거래량 20일평균 N배 (안 나오면 3.0으로)
    "change_min": 7.0,       # ★조정 포인트: 당일 등락률 % 하한 (안 나오면 5.0으로)
    "above_ma200": True,     # 200일선 위만(완전 잡주 제외). False로 풀 수 있음
}


def analyze_surge(df: pd.DataFrame, rs_rank: int | None = None,
                  rs_mom: int | None = None, cfg: dict = SURGE_CONFIG) -> dict | None:
    """당일 거래량 급증 + 강한 양봉 포착. RS 무관(단타 신호).
    ⚠️ 추세 신호 아님 — 하루이틀 모멘텀, 안 이어질 수 있음."""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None

    c, h, lo, v, o = df["Close"], df["High"], df["Low"], df["Volume"], df["Open"]
    close = float(c.iloc[-1])
    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0

    # ── 1) 당일 강한 양봉 ──
    if change_pct < cfg["change_min"]:
        return None

    # ── 2) 거래량 급증 (20일 평균 대비) ──
    vol_today = float(v.iloc[-1])
    vol_avg = float(v.iloc[-21:-1].mean())   # 직전 20봉 평균(오늘 제외)
    vol_mult = vol_today / vol_avg if vol_avg > 0 else 0.0
    if vol_mult < cfg["vol_mult"]:
        return None

    # ── 3) 최소 필터: 200일선 위 (완전 잡주 제외, 옵션) ──
    ma200 = c.rolling(200).mean()
    m200 = float(ma200.iloc[-1]) if len(c) >= 200 else None
    above_ma200 = (m200 is not None and close > m200)
    if cfg["above_ma200"] and m200 is not None and not above_ma200:
        return None

    r = rsi(c)
    cur_rsi = float(r.iloc[-1])

    # 단타 판단 보조 정보
    high60 = float(c.iloc[-60:].max())
    # 위꼬리: 오늘 고가 대비 종가가 얼마나 밀렸나 (고점에서 밀리면 약함)
    today_high = float(h.iloc[-1])
    today_open = float(o.iloc[-1])
    upper_wick = (today_high - close) / today_high * 100 if today_high > 0 else 0.0
    # 신고가 경신 여부
    high_all = float(h.iloc[:-1].max())
    new_high = close > high_all

    # 점수 = 거래량 강도 + 양봉 강도 (RS 무관)
    score = round(min(vol_mult / 6.0, 1.0) * 50 + min(change_pct / 15.0, 1.0) * 50, 1)

    return {
        "mode": "surge",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": score,
        "triggered": new_high,           # 신고가면 강조
        "setup_score": None,
        "rs": rs_rank if rs_rank is not None else "-",
        "rs_mom": rs_mom,
        "leader": False,
        "vol_mult": round(vol_mult, 1),
        "upper_wick_pct": round(upper_wick, 1),
        "new_high": new_high,
        "above_ma200": above_ma200,
        "dist_from_high_pct": round((high60 - close) / high60 * 100, 1) if high60 > 0 else 0.0,
        "rsi": round(cur_rsi, 1),
        "vol_dry": False,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ],
    }

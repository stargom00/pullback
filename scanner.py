"""
눌림목 스캐너 v2 — 핵심 탐지 로직
조건: 우상향 추세(200일선 포함) + 이평선 부근 조정 + 거래량 감소
      + RSI 중립권 + RS(유니버스 내 상대강도) 50 이상
추가: 피벗(돌파가) / 손절가 / 리스크 % 계산
"""
import math
from datetime import datetime, timezone, timedelta

import pandas as pd


# 한국 장중 여부 (KST 09:00~15:30, 평일). 장중 돌파 미확정 배지 판정용.
_KST = timezone(timedelta(hours=9))


def is_kr_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(_KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_KST)
    now = now.astimezone(_KST)
    if now.weekday() >= 5:  # 토/일
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 <= minutes <= 15 * 60 + 30


def climax_warning(c: pd.Series, h: pd.Series, lo: pd.Series, v: pd.Series) -> dict:
    """미너비니식 클라이맥스(과열/소진) 경고 감지.
    급등은 매수가 아니라 '매도/경계' 신호 — 포물선·최대하락일·소진갭·과도이격.
    반환: {climax: bool, reasons: [..], level: 'none'|'caution'|'danger'}
    """
    reasons = []
    if len(c) < 60:
        return {"climax": False, "reasons": [], "level": "none"}
    close = float(c.iloc[-1])

    # 1) 포물선 급등: 최근 10봉 상승률이 과도 (예: +30% 이상)
    ret10 = close / float(c.iloc[-11]) - 1 if len(c) >= 11 else 0.0
    if ret10 >= 0.30:
        reasons.append("포물선급등")

    # 2) 20일선에서 과도 이격 (extended) — 미너비니 '너무 멀면 매수 금지/매도 고려'
    ma20 = float(c.rolling(20).mean().iloc[-1])
    ext = (close - ma20) / ma20 if ma20 > 0 else 0.0
    if ext >= 0.25:
        reasons.append("이평과열")

    # 3) 최대 하락일: 최근 봉의 일간 하락이 지난 60봉 중 최대급
    daily_ret = c.pct_change()
    recent_drop = float(daily_ret.iloc[-1])
    min_60 = float(daily_ret.iloc[-60:].min())
    if recent_drop <= min_60 and recent_drop < -0.05:
        reasons.append("최대급락일")

    # 4) 소진성 거래량: 오늘 거래량이 최근 60봉 최대 + 음봉
    vol_today = float(v.iloc[-1])
    vol_max60 = float(v.iloc[-60:].max())
    if vol_today >= vol_max60 and recent_drop < 0:
        reasons.append("소진성거래량")

    # 5) RSI 과열 (보조)
    cur_rsi = float(rsi(c).iloc[-1])
    if cur_rsi >= 80:
        reasons.append("RSI과열")

    if not reasons:
        return {"climax": False, "reasons": [], "level": "none"}
    # 위험도: 매도 직접 신호(최대급락/소진거래량)가 있으면 danger, 아니면 caution
    danger = any(r in ("최대급락일", "소진성거래량") for r in reasons)
    return {
        "climax": True,
        "reasons": reasons,
        "level": "danger" if danger else "caution",
    }


def volume_info(close: float, v: pd.Series) -> dict:
    """오늘 거래량 + 거래대금(종가×거래량 근사). 카드 표시용."""
    vol_today = float(v.iloc[-1]) if len(v) else 0.0
    turnover = close * vol_today   # 거래대금 근사 (종가 기준)
    return {
        "volume": round(vol_today),
        "turnover": round(turnover),
    }


def rr_info(pivot: float, stop: float, h: pd.Series, entry: float | None = None,
            lo: pd.Series | None = None, c: pd.Series | None = None,
            base_low: float | None = None) -> dict:
    """손익비(R) 계산. 실제 진입가 기준 + 손절 현실화 + 측정이동 목표.

    손절 현실화: 넘어온 stop(베이스 기반)과 ATR 기반 손절 중 진입가에
      더 가까운 것을 사용. 연장된 종목(베이스에서 멀어진)은 ATR 손절이
      자동 적용돼 1R이 비현실적으로 커지는 것을 막는다. 손절폭 상한 12%.
    목표(측정이동): 베이스 높이(천장-바닥)를 돌파점에 더한 값.
      신고가라 전고가 의미없을 때 정석적 목표 산정(오닐/미너비니).
      전고가 측정이동보다 더 위면 전고 사용. 최소 2R 보장.
    """
    entry = entry if (entry and entry > 0) else pivot

    # ── 손절 현실화 ──
    stop_eff = stop
    if lo is not None and c is not None and len(c) >= 15:
        atr_val = atr(h, lo, c, 14)
        atr_stop = entry - atr_val * 2.5      # ATR 2.5배 손절
        # 베이스 손절과 ATR 손절 중 진입가에 더 가까운(=손절폭 작은) 것
        if atr_stop > stop_eff:
            stop_eff = atr_stop
    # 손절폭 상한 12% (이보다 넓으면 12%로 조임)
    max_stop = entry * 0.88
    if stop_eff < max_stop:
        stop_eff = max_stop
    stop_eff = round(stop_eff, 2)

    risk = entry - stop_eff
    if risk <= 0:
        return {"target": None, "rr": None, "target_basis": None, "stop_eff": stop_eff}

    # ── 목표 산정 ──
    longterm_high = float(h.iloc[-250:].max()) if len(h) >= 20 else float(h.max())
    # 측정이동: 베이스 높이를 돌파점(피벗)에 더함
    mm_target = None
    if base_low is not None and base_low > 0 and pivot > base_low:
        base_height = pivot - base_low
        mm_target = pivot + base_height

    if longterm_high > entry * 1.08:
        # 전고가 진입가보다 8%+ 위 → 전고 목표 (충분히 의미있음)
        target, basis = longterm_high, "전고"
    elif mm_target and mm_target > entry * 1.03:
        # 신고가 등 → 측정이동 목표
        target, basis = mm_target, "측정이동"
    else:
        # 베이스 정보 없거나 측정이동도 가까우면 → 2R 폴백
        target, basis = entry + risk * 2, "2R"

    # 최소 2R 보장: 측정이동/전고가 2R보다 가까우면 2R로 끌어올림
    if target < entry + risk * 2:
        target, basis = entry + risk * 2, "2R"

    rr = (target - entry) / risk
    return {
        "target": round(target, 2),
        "rr": round(rr, 1),
        "target_basis": basis,
        "stop_eff": stop_eff,   # 현실화된 손절 (카드 표시용)
    }


def _rr_block(pivot: float, stop: float, h: pd.Series, lo: pd.Series, c: pd.Series,
              base_low: float | None = None, entry: float | None = None,
              warn_pct: float = 8.0) -> dict:
    """카드용 손절/리스크/손익비 블록. rr_info로 손절을 현실화한 뒤
    stop·risk_pct·손익비를 모두 '현실화된 손절(stop_eff)' 기준으로 통일.
    표시 손절과 R 계산이 어긋나지 않도록 한 곳에서 처리."""
    info = rr_info(pivot, stop, h, entry=entry, lo=lo, c=c, base_low=base_low)
    eff = info.get("stop_eff") or stop
    base = entry if (entry and entry > 0) else pivot
    risk_pct = (base - eff) / base * 100 if base > 0 else 0.0
    return {
        "stop": round(eff, 2),
        "risk_pct": round(risk_pct, 2),
        "target": info["target"],
        "rr": info["rr"],
        "target_basis": info["target_basis"],
        "risk_warn": risk_pct > warn_pct,
    }


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
    "rs_min": 80,              # RS 등급 최소치 (눌림목=조정 중이라 80, 약간 여유)
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


def atr(h: pd.Series, lo: pd.Series, c: pd.Series, period: int = 14) -> float:
    """변동성(하루 변동폭) — 손절폭 산정용.
    True Range = max(고-저, |고-전일종가|, |저-전일종가|).
    급등/급락 며칠에 평균이 통째로 끌려가는 문제를 막기 위해
    평균(mean)이 아니라 중앙값(median)을 사용한다 (이상치에 강건).
    """
    prev_c = c.shift(1)
    tr = pd.concat([
        h - lo,
        (h - prev_c).abs(),
        (lo - prev_c).abs(),
    ], axis=1).max(axis=1)
    val = tr.iloc[-period:].median()
    return float(val) if not math.isnan(val) else 0.0




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


def up_down_volume(c: pd.Series, v: pd.Series, window: int = 50):
    """U/D Volume Ratio (매집/분산 비율) — 오닐 지표.
    최근 window일 중 '오른 날 거래량 합' ÷ '내린 날 거래량 합'.
    >1.0 = 매집(상승일에 거래량 더 실림, 기관 매수)
    <1.0 = 분산(하락일에 거래량 더 실림, 기관 매도)
    1.0 = 중립. 보통 1.0 이상이면 건강, 1.25+면 강한 매집.
    """
    if len(c) < window + 1:
        window = len(c) - 1
    if window < 5:
        return None
    cc = c.iloc[-window:]
    vv = v.iloc[-window:]
    prev = c.iloc[-(window + 1):-1].values
    up_vol = 0.0
    down_vol = 0.0
    for i in range(len(cc)):
        if cc.iloc[i] > prev[i]:
            up_vol += float(vv.iloc[i])
        elif cc.iloc[i] < prev[i]:
            down_vol += float(vv.iloc[i])
    if down_vol <= 0:
        return 9.99 if up_vol > 0 else None
    return round(up_vol / down_vol, 2)


def significant_support(lo: pd.Series, window: int, min_touches: int = 2,
                        band: float = 0.02, exclude: int = 1):
    """'여러 번 지지받은' 의미있는 지지 가격을 찾는다 (저항의 거울 버전).
    단순 최저가(=폭락 바닥 꼬리 하나)를 손절로 잡는 문제를 막기 위함.
    구간 저가 중 ±band 안에 저가가 min_touches개 이상 닿은 가격을
    '진짜 지지'로 인정, 그 중 가장 낮은(=가장 안전한) 값을 반환. 없으면 None.
    """
    if exclude > 0 and len(lo) > window + exclude:
        seg = lo.iloc[-(window + exclude):-exclude]
    elif exclude > 0 and len(lo) > exclude:
        seg = lo.iloc[:-exclude]
    else:
        seg = lo.iloc[-window:]
    seg = seg.dropna()
    if len(seg) < min_touches:
        return None
    lows = seg.tolist()
    for level in sorted(lows):   # 낮은 가격부터
        if level <= 0:
            continue
        touches = sum(1 for x in lows if abs(x - level) / level <= band)
        if touches >= min_touches:
            return level    # 가장 낮은 '유효 지지'(2번+ 지지받음)
    return None


def significant_resistance(h: pd.Series, window: int, min_touches: int = 2,
                           band: float = 0.02, exclude: int = 2):
    """'여러 번 부딪힌' 의미있는 저항 가격을 찾는다.
    단순 최고가(=긴 꼬리 하나=오버슈팅)를 천장으로 잡는 문제를 막기 위함.

    방법: 구간 내 각 봉의 고가를 후보로, 그 가격 ±band 안에 고가가
    들어온 봉이 min_touches개 이상이면 '진짜 저항'으로 인정.
    그런 저항 중 가장 높은 값을 반환. 없으면 None (호출부에서 max로 폴백).
    exclude: 최근 N봉(신고가 갱신 중일 수 있는 봉) 제외.
    """
    if exclude > 0 and len(h) > window + exclude:
        seg = h.iloc[-(window + exclude):-exclude]
    elif exclude > 0 and len(h) > exclude:
        seg = h.iloc[:-exclude]
    else:
        seg = h.iloc[-window:]
    seg = seg.dropna()
    if len(seg) < min_touches:
        return None
    highs = seg.tolist()
    for level in sorted(highs, reverse=True):   # 높은 가격부터
        if level <= 0:
            continue
        touches = sum(1 for x in highs if abs(x - level) / level <= band)
        if touches >= min_touches:
            return level    # 가장 높은 '유효 저항'(2번+ 닿음)
    return None


def select_pivot(h, lo, c, close, recent_high_window: int, is_kr: bool = False):
    """
    피벗 후보 중 현재가 위에서 가장 가까운 것 선택.
    ★ 핵심: 피벗은 '베이스(횡보 구간)의 저항선'이라 고정돼야 한다.
       그래서 '오늘 포함 최근 며칠'(신고가 갱신 중인 봉)을 제외하고,
       그 이전 구간의 고점을 피벗으로 삼는다. → 주가가 신고가를 만들어도
       피벗(과거 천장)이 따라 움직이지 않음.
    - 베이스 천장(단기): 최근 5봉 고가, 단 직전 2봉(오늘·어제 신고가) 제외
    - 전고(중기): 최근 N봉 고가, 단 직전 2봉 제외
    - 추세선: 하락 추세선의 오늘 값
    반환: (pivot, pivot_type, tl_break, tl_break_intraday)
    """
    EXCLUDE = 2   # 오늘·어제(신고가 갱신 중일 수 있는 봉) 제외

    cands = []
    # 베이스 천장 — 직전 2봉 빼고 그 앞 5봉의 고가 (고정된 단기 저항)
    if len(h) > EXCLUDE + 5:
        base_short = float(h.iloc[-(5 + EXCLUDE):-EXCLUDE].max())
        cands.append((base_short, "베이스천장"))
    # 전고(중기) — '여러 번 닿은 의미있는 저항' 우선. 긴 꼬리(오버슈팅) 하나는
    # 천장으로 안 침. 그런 저항이 없으면(진짜 신고가 추세) 단순 최고가로 폴백.
    if len(h) > EXCLUDE + recent_high_window:
        sig = significant_resistance(h, recent_high_window, min_touches=2,
                                     band=0.02, exclude=EXCLUDE)
        if sig is not None:
            cands.append((float(sig), "전고"))
        else:
            base_long = float(h.iloc[-(recent_high_window + EXCLUDE):-EXCLUDE].max())
            cands.append((base_long, "전고"))
    # 안전장치: 후보가 비면(데이터 짧음) 기존 방식으로
    if not cands:
        cands.append((float(h.iloc[-5:].max()), "베이스천장"))

    tl = trendline_level(h)
    tl_break = False
    tl_break_intraday = False
    if tl is not None:
        if close > tl and float(c.iloc[-3]) <= tl:
            tl_break = True          # 갓 돌파 (종가 확정) → 배지
        elif close > tl:
            if is_kr and is_kr_market_open():
                tl_break_intraday = True
        elif close <= tl:
            cands.append((tl, "추세선"))
    above = [(p, t) for p, t in cands if p > close * 1.001]
    if above:
        pivot, ptype = min(above, key=lambda x: x[0])
    else:
        pivot, ptype = max(cands, key=lambda x: x[0])
    return pivot, ptype, tl_break, tl_break_intraday


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
    IBD / MarketSmith 공식 RS Rating에 맞춘 상대강도 원점수.
    12개월을 3개월씩 4분기로 나눠, 최근 분기에 2배 가중:
        RS = 0.4 × Q1수익률 + 0.2 × Q2 + 0.2 × Q3 + 0.2 × Q4
        (Q1=최근 3개월, Q4=가장 오래된 3개월)
    이는 IBD가 공개한 RS Rating 가중 공식과 동일하다.
    유니버스 전체에서 백분위로 환산해 RS 등급(1~99)이 됨.
    (트레이딩뷰 RS Rating과 같은 공식. 단 모집단이 유니버스라 숫자는 다를 수 있음)
    """
    c = close.dropna()
    if len(c) < 200:
        return None
    now = float(c.iloc[-1])

    def price_ago(days):
        idx = -min(days, len(c) - 1) - 1
        return float(c.iloc[idx])

    # 분기 경계 가격 (63거래일 ≈ 3개월)
    p0 = now                 # 현재
    p3 = price_ago(63)       # 3개월 전
    p6 = price_ago(126)      # 6개월 전
    p9 = price_ago(189)      # 9개월 전
    p12 = price_ago(252)     # 12개월 전

    if min(p3, p6, p9, p12) <= 0:
        return None

    # 각 분기 수익률
    q1 = p0 / p3 - 1    # 최근 3개월
    q2 = p3 / p6 - 1    # 직전 3개월
    q3 = p6 / p9 - 1
    q4 = p9 / p12 - 1   # 가장 오래된 3개월

    # IBD 가중: 최근 분기 2배 (0.4 + 0.2 + 0.2 + 0.2 = 1.0)
    return 0.4 * q1 + 0.2 * q2 + 0.2 * q3 + 0.2 * q4


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


def analyze(df: pd.DataFrame, rs_rank: int | None = None, rs_mom: int | None = None, cfg: dict = CONFIG, _setup_eval: bool = False, is_kr: bool = False) -> dict | None:
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
    pivot, pivot_type, tl_break, tl_break_intraday = select_pivot(h, lo, c, close, pw, is_kr=is_kr)
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
    score = min(score, 100.0)   # 0~100 만점 캡

    # 🔥 트리거 발동: 당일 강한 양봉 + (추세선 돌파 or 피벗 코앞/돌파)
    triggered = change_pct >= 4.0 and (tl_break or pivot_dist_pct <= 2.0)
    # 전날 셋업 점수: 오늘 봉을 빼고 재평가 (🔥 카드 표시용, 재귀 1회 제한)
    setup_score = None
    if triggered and not _setup_eval:
        prev = analyze(df.iloc[:-1], rs_rank=rs_rank, rs_mom=rs_mom, cfg=cfg, _setup_eval=True, is_kr=is_kr)
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
        "tl_break_intraday": tl_break_intraday,
        "ud": ud_volume_ratio(c, v),
        "pivot_dist_pct": round(pivot_dist_pct, 2),
        **_rr_block(pivot, stop, h, lo, c,
                    base_low=float(lo.iloc[-cfg["recent_high_window"]:].min()),
                    entry=None, warn_pct=8.0),
        **volume_info(close, v),
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
    "rs_min": 80,            # 추세전환도 주도주 위주로 (기존 30→80)
}


def analyze_turnaround(df: pd.DataFrame, rs_rank: int | None = None,
                       rs_mom: int | None = None, cfg: dict = TURN_CONFIG, _setup_eval: bool = False, is_kr: bool = False) -> dict | None:
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
    pivot, pivot_type, tl_break, tl_break_intraday = select_pivot(h, lo, c, close, 20, is_kr=is_kr)
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
        prev = analyze_turnaround(df.iloc[:-1], rs_rank=rs_rank, rs_mom=rs_mom, cfg=cfg, _setup_eval=True, is_kr=is_kr)
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
        "tl_break_intraday": tl_break_intraday,
        "ud": ud,
        "pivot_dist_pct": round(pivot_dist_pct, 2),
        **_rr_block(pivot, stop, h, lo, c,
                    base_low=float(lo.iloc[-30:].min()),
                    entry=None, warn_pct=15.0),
        **volume_info(close, v),
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
    "rs_min": 85,            # 돌파는 강한 종목만 의미 있음 (주도주 위주 85)
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
        **_rr_block(pivot, stop, h, lo, c, base_low=base_low,
                    entry=close, warn_pct=8.0),   # 이미 돌파 → 현재가 진입 기준
        "rsi": round(cur_rsi, 1),
        "vol_dry": False,
        "ud_vol": up_down_volume(c, v, 50),
        **volume_info(close, v),
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# 📦 박스 돌파 (box breakout) — 횡보 박스/하락추세 상단을 거래량 동반 돌파
# 국장에서 자주 나오는 패턴: 일정 기간 눌려있다 거래량 터지며 위로 탈출.
# 짧/중/장(20/40/60봉) 박스를 모두 보고, 하나라도 돌파면 포착.
# 돌파임박(돌파 전)과 달리 '이미 박스 상단을 뚫은' 상태.
# 급등(가온전선 +29%)도 돌파면 포함. 장중 돌파도 표시(미확정 배지).
# ══════════════════════════════════════════════════════
BOXBREAK_CONFIG = {
    "min_bars": 140,         # 120일선 + 여유
    "rs_min": 85,            # 박스 탈출은 강한 종목이 크게 감 (주도주 위주 85)
    "box_windows": [20, 40, 60],   # 짧/중/장 박스 동시 확인
    "box_max_range": 0.30,   # 박스 고저폭 ≤30% (국장 변동성 고려, 너무 넓으면 박스 아님)
    "vol_mult": 1.5,         # 돌파일 거래량 ≥ 평균 1.5배 (박스돌파의 핵심)
    "ma_long": 120,          # 장기선(120일) 위 — "장기선 위 박스탈출은 크게 간다"
}


def analyze_boxbreak(df: pd.DataFrame, rs_rank: int | None = None,
                     rs_mom: int | None = None, cfg: dict = BOXBREAK_CONFIG,
                     is_kr: bool = False) -> dict | None:
    """횡보 박스(또는 하락 후 횡보)의 상단을 거래량 동반 돌파한 종목.
    20/40/60봉 박스를 모두 검사해 '가장 의미있는(좁고 긴) 박스'의 돌파를 잡는다."""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None
    if rs_rank is None or rs_rank < cfg["rs_min"]:
        return None

    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    close = float(c.iloc[-1])
    ma_long = c.rolling(cfg["ma_long"]).mean()
    m_long = float(ma_long.iloc[-1])
    if math.isnan(m_long):
        return None

    # 장기선(120일) 위에서의 돌파만 — 추세 살아있는 박스 탈출
    if close < m_long:
        return None

    # ── 거래량 동반 (박스돌파의 생명) ──
    vol_today = float(v.iloc[-1])
    vol_avg = float(v.iloc[-51:-1].mean())   # 직전 50봉 평균(오늘 제외)
    vol_mult = vol_today / vol_avg if vol_avg > 0 else 0.0
    if vol_mult < cfg["vol_mult"]:
        return None

    # ── 20/40/60봉 박스를 각각 검사, 돌파한 것 중 최선을 선택 ──
    # "최선" = 박스가 좁고(타이트) 길수록 의미있는 탈출
    best = None
    for win in cfg["box_windows"]:
        if len(c) < win + 2:
            continue
        # 박스 상단은 '여러 번 닿은 의미있는 저항'으로 (긴 꼬리=오버슈팅 제외).
        # 그런 저항이 없으면 단순 고가 최고치로 폴백.
        box_h = h.iloc[-(win + 1):-1]        # 오늘 직전 win봉 (고가)
        box_l = lo.iloc[-(win + 1):-1]       # (저가)
        sig_high = significant_resistance(h, win, min_touches=2, band=0.02, exclude=1)
        box_high = float(sig_high) if sig_high is not None else float(box_h.max())
        box_low = float(box_l.min())
        if box_high <= 0:
            continue
        box_range = (box_high - box_low) / box_high
        if box_range > cfg["box_max_range"]:
            continue                          # 박스가 너무 넓음 → 박스 아님
        # 돌파 판정: 현재가가 박스 상단(의미있는 저항)을 +0.5% 이상 확실히 넘어야.
        if close <= box_high * 1.005:
            continue
        ext = (close - box_high) / box_high   # 박스 상단 대비 얼마나 위
        tightness = 1 - min(box_range / cfg["box_max_range"], 1.0)
        quality = tightness * 0.5 + min(win / 60, 1.0) * 0.3 + min(vol_mult / 3, 1.0) * 0.2
        cand = {
            "win": win, "box_high": box_high, "box_low": box_low,
            "box_range": box_range, "ext": ext, "quality": quality,
        }
        if best is None or cand["quality"] > best["quality"]:
            best = cand

    if best is None:
        return None   # 어떤 박스도 돌파 안 함

    pivot = best["box_high"]   # 돌파한 박스 상단 = 피벗
    ext = best["ext"]

    # 장중 돌파 미확정 여부 (한국 장중 + 종가 아직 안 굳음)
    intraday_unconfirmed = False
    if is_kr and is_kr_market_open():
        # 오늘 종가가 아직 확정 전이고 현재가로 막 넘었으면 미확정
        prev_high = float(h.iloc[-2]) if len(h) >= 2 else pivot
        if close > pivot and prev_high <= pivot:
            intraday_unconfirmed = True

    r = rsi(c)
    cur_rsi = float(r.iloc[-1])
    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0

    # 손절: 박스 상단(피벗) 살짝 아래 = 돌파 실패 기준
    stop = round(pivot * 0.97, 2)
    # 이미 돌파한 상태 → 실제 진입은 현재가. 리스크/손익비 모두 현재가 기준으로 통일.
    risk_pct = (close - stop) / close * 100 if close > 0 else 0.0

    score = round(best["quality"] * 100 * (0.7 + 0.3 * rs_rank / 99), 1)

    return {
        "mode": "boxbreak",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": score,
        "triggered": ext <= 0.05,    # 박스 상단 +5% 이내면 매수 유효구간 강조
        "setup_score": None,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "leader": True,
        "pivot": round(pivot, 2),
        "pivot_type": f"박스상단 {best['win']}일",
        "ext_pct": round(ext * 100, 1),
        "vol_mult": round(vol_mult, 1),
        "box_days": best["win"],
        "box_range_pct": round(best["box_range"] * 100, 1),
        "tl_break_intraday": intraday_unconfirmed,
        **_rr_block(pivot, stop, h, lo, c, base_low=best["box_low"],
                    entry=close, warn_pct=8.0),   # 이미 돌파 → 현재가 진입 기준
        "rsi": round(cur_rsi, 1),
        "vol_dry": False,
        "ud_vol": up_down_volume(c, v, 50),
        **volume_info(close, v),
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# 🎯 돌파 임박 (pre-breakout) — 천장 코앞 + 거래량 수축
# 박스 천장/전고/추세선 바로 아래(-5%~0%)까지 올라왔지만 아직 안 뚫은,
# "돌파 직전 대기" 종목. 돌파 전날 미리 잡으려는 용도.
# ══════════════════════════════════════════════════════
IMMINENT_CONFIG = {
    "min_bars": 210,
    "rs_min": 85,            # 돌파 직전 대기 — 주도주만 (기존 50→85)
    "near_min": -0.05,   # 피벗 대비 현재가 하한 (-5%: 천장 5% 아래까지)
    "near_max": 0.0,     # 상한 0%: 아직 안 뚫음 (피벗 이하)
    "pivot_window": 20,
    "vol_contraction": 0.8,  # 거래량 3일/20일 비율이 이 이하면 '수축' 가점
}


def analyze_imminent(df: pd.DataFrame, rs_rank: int | None = None,
                     rs_mom: int | None = None, cfg: dict = IMMINENT_CONFIG,
                     is_kr: bool = False) -> dict | None:
    """천장(피벗) 바로 아래까지 올라왔지만 아직 안 뚫은 '돌파 직전' 종목.
    피벗 대비 -5%~0% 구간 + 우상향 추세. 거래량 수축은 가점(필수 아님)."""
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

    # ── 1) 우상향 추세 (정배열 기반) ──
    if close < m200:
        return None
    if not (m20 > m60):
        return None

    # ── 2) 피벗 근접 (천장 코앞이지만 아직 안 뚫음) ──
    pivot, pivot_type, tl_break, tl_break_intraday = select_pivot(h, lo, c, close, cfg["pivot_window"], is_kr=is_kr)
    near = (close - pivot) / pivot if pivot > 0 else -1.0   # 음수면 피벗 아래
    if not (cfg["near_min"] <= near <= cfg["near_max"]):
        return None   # -5%~0% 밖이면 탈락 (멀거나 이미 돌파)

    # ── 2-b) 박스 상단(피벗) 두드림 횟수 ──
    # 최근 20봉 중 고가가 피벗 ±2% 안에 들어온(=천장을 찔러본) 봉의 수.
    # 여러 번 두드릴수록 매물벽이 약해져 돌파 확률↑ (미너비니/오닐).
    # 연속된 두드림은 1회로 묶어 과다 집계 방지.
    touch_band = pivot * 0.02
    touched = (h.iloc[-20:] >= pivot - touch_band)   # 피벗 -2% 위로 고가가 닿음
    touch_count = 0
    prev = False
    for t in touched.tolist():
        if t and not prev:
            touch_count += 1   # 새로 닿기 시작한 구간마다 +1
        prev = t

    # ── 3) 거래량 수축 여부 (가점용, 필수 아님) ──
    vol3 = float(v.iloc[-3:].mean())
    vol20 = float(v.iloc[-20:].mean())
    vol_ratio = vol3 / vol20 if vol20 > 0 else 9.9
    vol_dry = vol_ratio <= cfg["vol_contraction"]

    # ── 4) 변동폭 축소(VCP): 최근 5봉 변동폭이 그 전 5봉보다 작은가 ──
    rng_recent = float((h.iloc[-5:] - lo.iloc[-5:]).mean())
    rng_prev = float((h.iloc[-10:-5] - lo.iloc[-10:-5]).mean())
    tightening = rng_recent < rng_prev if rng_prev > 0 else False

    # ── 손절 / 리스크 ──
    # 손절은 '여러 번 지지받은 의미있는 바닥' 기준. 폭락 바닥 꼬리 하나를
    # 손절로 잡으면 리스크가 비현실적으로 커지므로(예: 30%) 그걸 방지.
    # 우선순위: 의미있는 지지 → 20일선 -2% → (폴백) 단순 저점.
    # 단 현재가 아래 후보만. 손절폭은 참고용 — 진입/거름 판단은 사용자가 차트로.
    sig_sup = significant_support(lo, cfg["pivot_window"], min_touches=2, band=0.02, exclude=1)
    cand = []
    if sig_sup is not None and sig_sup < close:
        cand.append(sig_sup)
    if m20 * 0.98 < close:
        cand.append(m20 * 0.98)
    if cand:
        stop = max(cand)   # 현재가 아래 후보 중 가장 가까운(=타이트한) 것
    else:
        stop = float(lo.iloc[-cfg["pivot_window"]:].min())   # 폴백
    pivot_dist_pct = (pivot - close) / close * 100   # 현재가→피벗 남은 거리(양수)
    risk_pct = (pivot - stop) / pivot * 100 if pivot > 0 else 0.0   # 피벗 진입 기준

    # ── 점수 (100점) ──
    # 피벗 근접도 35 (가까울수록↑) + 거래량수축 20 + VCP 20 + RS 15 + 200일선위 10
    near_score = 35 * (1 - min(abs(near) / 0.05, 1.0))   # 0%면 35, -5%면 0
    score = (
        near_score
        + (20 if vol_dry else 20 * max(0.0, min((1.1 - vol_ratio) / 0.5, 1.0)))
        + (20 if tightening else 0)
        + 15 * max(0.0, (rs_rank - 50) / 49)
        + 10
    )
    if rs_rank is not None:
        score *= 0.7 + 0.3 * rs_rank / 99

    # 두드림 가점: 2회 이상 두드린 종목은 돌파 확률↑ → 점수 보너스 (최대 +10)
    if touch_count >= 2:
        score += min((touch_count - 1) * 4, 10)
    score = min(score, 100.0)   # 점수는 0~100 만점으로 캡 (가점 포함 100 초과 방지)

    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0

    return {
        "mode": "imminent",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "triggered": near >= -0.02,   # 피벗 2% 이내면 카드 강조(임박 임박)
        "setup_score": None,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "leader": rs_rank >= 90,
        "pivot": round(pivot, 2),
        "pivot_type": pivot_type,
        "tl_break": tl_break,
        "tl_break_intraday": tl_break_intraday,
        "pivot_dist_pct": round(pivot_dist_pct, 2),
        "touch_count": touch_count,
        "vol_ratio": round(vol_ratio, 2),
        "ud_vol": up_down_volume(c, v, 50),
        "vol_dry": vol_dry,
        "tightening": tightening,
        "rsi": round(cur_rsi, 1),
        **_rr_block(pivot, stop, h, lo, c,
                    base_low=float(lo.iloc[-cfg["pivot_window"]:].min()),
                    entry=None, warn_pct=8.0),
        **volume_info(close, v),
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ],
    }
# "오늘 거래량+가격이 터진 것"만 포착. 신호일 뿐 지속 보장 없음.
# ══════════════════════════════════════════════════════
SURGE_CONFIG = {
    "min_bars": 60,          # 급등은 긴 데이터 불필요(단타)
    "vol_mult": 4.0,         # ★조정 포인트: 거래량 20일평균 N배 (안 나오면 3.0으로)
    "change_min": 7.0,       # ★조정 포인트: 당일 등락률 % 하한 (안 나오면 5.0으로)
    "above_ma200": True,     # 200일선 위만(완전 잡주 제외). False로 풀 수 있음
    # ── 첫날 포착: 어제까지 "조용했던" 종목만 (이미 며칠 달린 건 제외) ──
    "quiet_days": 4,         # 오늘 직전 N일을 "조용했나" 검사 구간으로
    "quiet_vol_max": 2.0,    # 직전 N일 거래량이 평균의 2배 넘었으면 = 이미 터짐(제외)
    "quiet_run_max": 18.0,   # 직전 N일 누적 상승이 N%를 넘었으면 = 이미 달림(제외)
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

    # ── 3) 첫날 포착: 어제까지 조용했나 (이미 며칠 달린 종목 제외) ──
    qd = cfg["quiet_days"]
    if len(c) > qd + 21:
        # (a) 직전 qd일 거래량이 그 이전 20일 평균 대비 조용했나
        prior_vol_avg = float(v.iloc[-(qd + 21):-(qd + 1)].mean())
        recent_vol_avg = float(v.iloc[-(qd + 1):-1].mean())
        if prior_vol_avg > 0 and recent_vol_avg / prior_vol_avg > cfg["quiet_vol_max"]:
            return None   # 직전 며칠 이미 거래량 터짐 = 첫날 아님
        # (b) 직전 qd일 누적 상승폭이 과하지 않았나
        run_start = float(c.iloc[-(qd + 1)])
        prior_run = (prev_close / run_start - 1) * 100 if run_start > 0 else 0.0
        if prior_run > cfg["quiet_run_max"]:
            return None   # 오늘 전에 이미 크게 올랐음 = 첫날 아님

    # ── 4) 최소 필터: 200일선 위 (완전 잡주 제외, 옵션) ──
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

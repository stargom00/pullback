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


def merger_warning(c: pd.Series, h: pd.Series, lo: pd.Series, v: pd.Series) -> dict:
    """M&A(인수합병)/특수상황 의심 감지.
    GSAT(아마존 인수) 같은 종목은 인수가 부근에 가격이 '고정'돼
    변동성이 비정상적으로 죽고 좁은 밴드에 갇힌다. 차트상으론 깔끔한
    횡보(=눌림목/베이스)로 보이지만 실제론 상방이 인수가에 막히고
    하방은 딜 무산 시 급락하는 비대칭 리스크 → 추세매매 부적합.

    조건(동시 충족):
      1) 변동성 붕괴: 최근 20봉 ATR%가 그 이전 60봉 ATR%의 40% 이하
      2) 좁은 밴드: 최근 20봉이 ±5% 안에 갇힘 (고가/저가 폭)
      3) 점프 흔적: 횡보 진입 전(과거 60~120봉 구간)에 거래량 폭발(평균 5배+)
                    동반 큰 갭/급등(+15% 이상)이 있었음 = 발표 충격
    반환: {merger: bool, reasons: [..]}
    """
    reasons = []
    if len(c) < 130:
        return {"merger": False, "reasons": []}
    close = float(c.iloc[-1])
    if close <= 0:
        return {"merger": False, "reasons": []}

    # ── 1) 변동성 붕괴 (ATR%로 정규화 — 가격대 무관 비교) ──
    # 최근 20봉 ATR%(=ATR/가격)가 발표 갭 이전의 정상 변동성 대비 급감했는가.
    # 절대 ATR은 가격대(60달러 vs 80달러)에 따라 왜곡되므로 반드시 % 비교.
    def atr_pct(hh, ll, cc):
        a = atr(hh, ll, cc, 14)
        px = float(cc.iloc[-1])
        return a / px if px > 0 else 9.9
    atr_recent = atr_pct(h.iloc[-20:], lo.iloc[-20:], c.iloc[-20:])
    # 비교 기준: 발표 갭이 섞이지 않은 '먼 과거'(−120~−60봉)의 정상 변동성
    atr_base = atr_pct(h.iloc[-120:-60], lo.iloc[-120:-60], c.iloc[-120:-60])
    if atr_base <= 0:
        return {"merger": False, "reasons": []}
    vol_collapse = (atr_recent / atr_base) <= 0.60
    if vol_collapse:
        reasons.append("변동성붕괴")

    # ── 2) 좁은 밴드 고정 ──
    hi20 = float(h.iloc[-20:].max())
    lo20 = float(lo.iloc[-20:].min())
    band = (hi20 - lo20) / close if close > 0 else 9.9
    tight = band <= 0.05
    if tight:
        reasons.append("좁은밴드고정")

    # ── 3) 횡보 직전 점프 흔적 (발표 충격) ──
    # 횡보 구간(최근 20봉) 직전, 과거 60~120봉 사이에서 거래량 폭발+급등 탐색
    seg_v = v.iloc[-120:-20]
    seg_c = c.iloc[-120:-20]
    jumped = False
    if len(seg_v) >= 20 and len(seg_c) >= 20:
        vmean = float(v.iloc[-120:].mean())
        if vmean > 0:
            daily = seg_c.pct_change()
            for i in range(len(seg_v)):
                vol_spike = float(seg_v.iloc[i]) >= vmean * 5
                gap_up = float(daily.iloc[i]) >= 0.15 if not math.isnan(float(daily.iloc[i])) else False
                if vol_spike and gap_up:
                    jumped = True
                    break
    if jumped:
        reasons.append("발표충격갭")

    # 판정: 발표충격갭은 필수(M&A의 결정적 증거) + 좁은밴드 필수.
    # 변동성붕괴는 보조(가점) — 둘만 맞아도 강한 의심으로 본다.
    # (발표갭+좁은밴드 = 발표 후 인수가에 가격이 고정된 전형적 패턴)
    merger = jumped and tight
    return {"merger": merger, "reasons": reasons if merger else []}


def _merger_block(c, h, lo, v) -> dict:
    """analyze 결과에 붙일 M&A 의심 플래그 블록."""
    try:
        mw = merger_warning(c, h, lo, v)
    except Exception:
        return {"merger": False, "merger_reasons": []}
    return {"merger": mw["merger"], "merger_reasons": mw["reasons"]}


def volume_info(close: float, v: pd.Series) -> dict:
    """오늘 거래량 + 거래대금 + 평균 대비 배수. 카드 표시용.
    vol_vs_avg: 오늘 거래량 ÷ 최근 50일 평균. 1.0=평소, 0.4=평소의 40%, 2.0=2배.
    """
    vol_today = float(v.iloc[-1]) if len(v) else 0.0
    turnover = close * vol_today   # 거래대금 근사 (종가 기준)
    avg50 = float(v.iloc[-50:].mean()) if len(v) >= 5 else 0.0
    vol_vs_avg = round(vol_today / avg50, 2) if avg50 > 0 else None
    return {
        "volume": round(vol_today),
        "turnover": round(turnover),
        "avg_volume": round(avg50),
        "vol_vs_avg": vol_vs_avg,   # 오늘/평균 (1.0=평소)
    }


def rr_info(pivot: float, stop: float, h: pd.Series, entry: float | None = None,
            lo: pd.Series | None = None, c: pd.Series | None = None,
            base_low: float | None = None) -> dict:
    """손익비(R) 계산. 진입가 기준 + 측정이동 목표.

    v4.37.4: 손절은 호출부(탭별 analyze)에서 구조(지지/저점/베이스하단)로
      계산해 넘긴 값을 '그대로' 사용한다. 과거의 ATR손절·12%상한 보정은
      손절을 현재가에 연동시켜 자꾸 움직이게 만드는 버그라 제거.
      → 손절은 가격 구조에 고정, 현재가가 변해도 안 흔들림.
      목표(측정이동): 베이스 높이(천장-바닥)를 돌파점에 더한 값.
      전고가 측정이동보다 더 위면 전고 사용. 최소 2R 보장.
    """
    entry = entry if (entry and entry > 0) else pivot

    # 손절은 넘어온 구조 기반 값을 그대로 사용 (보정 없음)
    stop_eff = round(stop, 2)

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
              warn_pct: float = 8.0, is_kr: bool = False,
              stop_struct: float | None = None, atr_buf: float = 0.0) -> dict:
    """카드용 손절/리스크/손익비 블록. rr_info로 손절을 현실화한 뒤
    stop·risk_pct·손익비를 모두 '현실화된 손절(stop_eff)' 기준으로 통일.
    한국 중소형주는 변동성이 커서 손절폭 경고 기준을 완화(12%)한다.
    stop_struct/atr_buf: ATR 버퍼 추적용 (버퍼전 구조손절, 버퍼값)."""
    if is_kr and warn_pct < 12.0:
        warn_pct = 12.0
    info = rr_info(pivot, stop, h, entry=entry, lo=lo, c=c, base_low=base_low)
    eff = info.get("stop_eff") or stop
    base = entry if (entry and entry > 0) else pivot
    risk_pct = (base - eff) / base * 100 if base > 0 else 0.0
    return {
        "stop": round(eff, 2),
        "risk_pct": round(risk_pct, 2),
        "entry_basis": "현재가" if (entry and entry > 0) else "피벗",   # 리스크/R 계산 기준
        "target": info["target"],
        "rr": info["rr"],
        "target_basis": info["target_basis"],
        "risk_warn": risk_pct > warn_pct,
        "stop_struct": round(stop_struct, 2) if stop_struct is not None else None,
        "atr_buf": round(atr_buf, 2),
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
    # 손절 ATR 버퍼: 구조 손절(지지선) 아래로 ATR×배수만큼 여유를 둬
    # 노이즈(지지선 살짝 깨고 반등)에 털리는 걸 방지. 종목 변동성 자동 반영.
    # 추적하며 조정: 0.3(타이트)~0.5(여유). 0이면 버퍼 없음.
    "atr_stop_buffer": 0.3,
}


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, math.nan)
    return 100 - (100 / (1 + rs))


def anchored_vwap(h: pd.Series, lo: pd.Series, c: pd.Series, v: pd.Series,
                  lookback: int = 25) -> dict:
    """미너비니/오닐식 Anchored VWAP.
    앵커 = 최근 lookback봉(약 5주) 중 '최저가 봉'(=최근 베이스/눌림의 시작).
      미너비니의 extension 판단은 단기(10/20일선) 기준이므로, AVWAP도 최근
      베이스 구간을 반영해야 한다. 옛날 폭등일을 앵커로 잡으면 강세주가
      건강한 눌림인데도 '과열'로 오진된다(기가비스 사례). 최근 저점을
      앵커로 삼으면 '최근 매수자들의 평균가' 기준이 되어 정확하다.
    그 봉부터 현재까지 거래량 가중평균가(typical price)를 계산.
    zone: 단기 이격도 등급 (미너비니 extension 기준).
      healthy(0~+8%) / extended(+8~+15%) / overheated(+15%+) /
      near(0~-4%) / below(-4%↓)
    """
    n = len(c)
    if n < 20:
        return {"avwap": None, "above": None, "dist_pct": None, "anchor_ago": None, "zone": None}
    win = min(lookback, n)
    # 앵커 = 최근 win봉 중 최저가 봉 (= 최근 베이스/눌림의 바닥, 의미있는 시작점)
    lo_win = lo.iloc[-win:]
    anchor_pos_in_win = int(lo_win.values.argmin())
    anchor_idx = n - win + anchor_pos_in_win
    seg_h = h.iloc[anchor_idx:]
    seg_lo = lo.iloc[anchor_idx:]
    seg_c = c.iloc[anchor_idx:]
    seg_v = v.iloc[anchor_idx:]
    typical = (seg_h + seg_lo + seg_c) / 3.0
    vsum = float(seg_v.sum())
    if vsum <= 0:
        return {"avwap": None, "above": None, "dist_pct": None, "anchor_ago": None, "zone": None}
    avwap = float((typical * seg_v).sum() / vsum)
    cur = float(c.iloc[-1])
    if avwap <= 0:
        return {"avwap": None, "above": None, "dist_pct": None, "anchor_ago": None, "zone": None}
    dist_pct = (cur - avwap) / avwap * 100
    # 이격도 등급 (미너비니 extension: 단기 이평 기준이라 임계 낮춤)
    if dist_pct >= 15:
        zone = "overheated"     # 과열 — 추격 금지 (10/20일선서 과도 이격)
    elif dist_pct >= 8:
        zone = "extended"       # 연장 — 추격 주의
    elif dist_pct >= 0:
        zone = "healthy"        # 건강한 우위 (지지 유효)
    elif dist_pct >= -4:
        zone = "near"           # AVWAP 살짝 아래 (애매)
    else:
        zone = "below"          # 매물 부담
    return {
        "avwap": round(avwap, 2),
        "above": cur > avwap,
        "dist_pct": round(dist_pct, 1),
        "anchor_ago": n - 1 - anchor_idx,
        "zone": zone,
    }


def apply_atr_buffer(stop: float, h: pd.Series, lo: pd.Series, c: pd.Series,
                     mult: float) -> tuple:
    """구조 손절 아래로 ATR×mult 만큼 버퍼를 더한다 (노이즈 흡수).
    손절은 구조(지지선/저점)에 고정된 채, 종목 변동성만큼만 살짝 내려감.
    반환: (버퍼적용_손절, 버퍼전_구조손절, 버퍼값). mult=0이면 버퍼 없음.
    탭별 mult: 눌림/추세전환 0.3(여유), 돌파/박스돌파/돌파임박 0.15(타이트).
    """
    stop_struct = stop
    if mult <= 0 or stop is None:
        return stop, stop_struct, 0.0
    buf = atr(h, lo, c, 14) * mult
    return stop - buf, stop_struct, buf


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
        RS = 0.4 × Q1 + 0.2 × Q2 + 0.2 × Q3 + 0.2 × Q4
    v4.37.1: 분기 수익률을 '로그수익률 + 클리핑'으로 계산.
      - 단순 비율(p0/p3-1)은 저가주 폭등($1→$15 = +1400%)이 한 분기 점수를
        폭발시켜, 현재 추락 중인 종목도 RS 99로 잡히는 버그가 있었음.
      - 로그수익률 ln(p0/p3)은 극단 폭등을 압축하고, ±0.7(±약100%)로 클립해
        저가주 왜곡을 막는다. 정상 추세주의 순위는 거의 보존.
    """
    import math
    c = close.dropna()
    if len(c) < 200:
        return None
    now = float(c.iloc[-1])

    def price_ago(days):
        idx = -min(days, len(c) - 1) - 1
        return float(c.iloc[idx])

    p0 = now
    p3 = price_ago(63)
    p6 = price_ago(126)
    p9 = price_ago(189)
    p12 = price_ago(252)

    if min(p0, p3, p6, p9, p12) <= 0:
        return None

    CLIP = 0.7  # 분기 로그수익률 상·하한 (≈ ±100%) — 저가주 폭등 왜곡 차단

    def logret(a, b):
        r = math.log(a / b)
        return max(-CLIP, min(CLIP, r))

    q1 = logret(p0, p3)    # 최근 3개월
    q2 = logret(p3, p6)
    q3 = logret(p6, p9)
    q4 = logret(p9, p12)   # 가장 오래된 3개월

    # IBD 가중: 최근 분기 2배 (0.4 + 0.2 + 0.2 + 0.2 = 1.0)
    return 0.4 * q1 + 0.2 * q2 + 0.2 * q3 + 0.2 * q4


def to_rs_rank(raw_scores: dict[str, float]) -> dict[str, int]:
    """원점수 dict → 백분위(1~99) dict.
    v4.37+: 원점수는 '지수 대비 초과성과'(종목RS - 지수RS)를 받는다.
    즉 백분위는 '지수를 이긴 정도'의 순위 → universe 편향 완화."""
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

    # 손절 후보 (미너비니식: 의미있는 지지 기준, spike 꼬리 제외):
    #  1) 현재가 아래의 지지 이평선 중 가장 가까운(=손절폭 작은) 것
    #     — 화면 지지선이 현재가 위여도 버리지 않고, 아래 이평을 찾는다.
    #  2) 2번+ 지지받은 '의미있는 저점'(significant_support) — 단순 최저가(spike
    #     꼬리) 대신. 일시적 장중 급락 꼬리가 손절로 잡히는 문제 방지.
    #  → 둘 중 현재가에 더 가까운(=손절 짧은) 쪽을 손절로. 둘 다 없으면 폴백.
    ma_below = [x for x in (m10, m20, m60) if x and x < close]
    ma_stop = max(ma_below) * 0.99 if ma_below else None   # 가장 가까운 아래 이평 -1%
    # 손절에 쓴 이평 이름 (화면 지지선 표시용 — 손절가와 일치시킴)
    stop_ma_name = None
    if ma_below:
        nearest = max(ma_below)
        stop_ma_name = "MA10" if nearest == m10 else "MA20" if nearest == m20 else "MA60"
    sig_low = significant_support(lo, pw, min_touches=2, band=0.02, exclude=1)
    pullback_low = float(lo.iloc[-pw:].min())  # 폴백용 단순 저점
    cand = [x for x in (ma_stop, sig_low) if x is not None and x < close]
    if cand:
        stop = max(cand)            # 현재가에 가장 가까운 유효 손절(=손절폭 최소)
    else:
        stop = pullback_low         # 폴백: 둘 다 없으면 단순 저점
    # ── ATR 버퍼: 구조 손절 아래로 ATR×배수만큼 여유 (노이즈 흡수) ──
    # 손절은 여전히 구조(지지선)에 고정되어 현재가 따라 안 움직이고,
    # 종목 변동성(ATR)만큼만 살짝 아래로 내려 정상 변동에 안 털리게 한다.
    atr_val = atr(h, lo, c, 14)     # 종목 변동성 (버퍼 + 경고 공용)
    stop, stop_struct, atr_buf = apply_atr_buffer(
        stop, h, lo, c, cfg.get("atr_stop_buffer", 0.0))
    # 화면 지지선 표시를 실제 손절 기준과 일치시킴 (버퍼 전 구조 손절 기준)
    if ma_stop is not None and stop_struct == ma_stop and stop_ma_name:
        disp_support = stop_ma_name          # 손절을 이평으로 잡음 → 그 이평 표시
        disp_support_dist = round((close - stop) / close * 100, 2)
    elif stop_struct == sig_low and sig_low is not None:
        disp_support = "지지저점"             # 의미있는 저점으로 잡음
        disp_support_dist = round((close - stop) / close * 100, 2)
    else:
        disp_support = support_ma             # 폴백: 기존 가장 가까운 이평
        disp_support_dist = round((close - stop) / close * 100, 2)
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

    # ── 변동성(ATR%) 경고 — 미너비니: 손절폭은 종목 변동성에 맞춰라 ──
    # ATR%가 크면 하루 정상 변동이 커서, 타이트한 손절이 노이즈에 털린다.
    # 고변동 종목은 진입 신중 + 손절폭 충분히(또는 비중 축소) 필요.
    atr_pct = round(atr_val / close * 100, 1) if close > 0 else 0.0
    # 손절폭(현재가→손절)이 ATR의 1.5배 미만이면 노이즈에 털릴 위험
    stop_dist_pct = (close - stop) / close * 100 if close > 0 else 0.0
    atr_tight = stop_dist_pct < atr_pct * 1.5  # 손절이 변동성 대비 너무 타이트
    vol_high = atr_pct >= 7.0                  # 고변동 종목(하루 7%+ 변동)

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
        **_merger_block(c, h, lo, v),
        "pullback_pct": round(pullback * 100, 1),
        "support_ma": disp_support,
        "ma_dist_pct": disp_support_dist,
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
        "atr_pct": atr_pct,
        "vol_high": vol_high,
        "atr_tight": atr_tight,
        **_rr_block(pivot, stop, h, lo, c,
                    base_low=float(lo.iloc[-cfg["recent_high_window"]:].min()),
                    entry=close, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf),
        **volume_info(close, v),
        "avwap": anchored_vwap(h, lo, c, v),
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
    # ── 1→2단계 첫 돌파 신호 ──
    "ma200_slope_lookback": 20,   # 200일선 기울기 판정 구간(봉)
    "ma200_rising_min": 0.0,      # 200일선이 N봉 전보다 높아야(바닥 상향전환)
    "breakout_vol_mult": 1.5,     # 돌파일 거래량이 50일 평균의 N배↑ = 진짜 돌파
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

    # ── 1→2단계 핵심: 200일선(장기선)이 바닥에서 우상향 전환했는가 ──
    # 역배열 바닥은 200일선이 우하향/평탄. 진짜 전환은 200일선이 막 들리기 시작.
    lb = cfg["ma200_slope_lookback"]
    ma200_rising = False
    if len(ma200.dropna()) > lb:
        m200_prev = float(ma200.iloc[-1 - lb])
        if not math.isnan(m200_prev) and m200_prev > 0:
            ma200_slope = (m200 - m200_prev) / m200_prev
            ma200_rising = ma200_slope > cfg["ma200_rising_min"]
    if not ma200_rising:
        return None  # 장기선이 아직 안 들렸으면 1단계 미졸업 → 전환 아님

    # 거래량: 전환 구간(최근 10일)이 평소(50일)보다 늘었는가 (확장이 좋음)
    vol10 = float(v.iloc[-10:].mean())
    vol50 = float(v.iloc[-50:].mean())
    vol_ratio = vol10 / vol50 if vol50 > 0 else 0.0

    # ── 1→2단계 핵심: 돌파일 거래량 폭증(50일 평균 대비) ──
    # 베이스 첫 돌파는 당일 거래량이 터져야 진짜. 최근 5일 중 최대 거래일 배수.
    vol_today = float(v.iloc[-1])
    vol_mult_today = vol_today / vol50 if vol50 > 0 else 0.0
    vol_mult_5d = float(v.iloc[-5:].max()) / vol50 if vol50 > 0 else 0.0
    breakout_vol = vol_mult_5d >= cfg["breakout_vol_mult"]

    # 피벗: 20봉 고가/타이트존/하락추세선 중 가장 가까운 트리거, 손절은 60일선 -2%
    pivot, pivot_type, tl_break, tl_break_intraday = select_pivot(h, lo, c, close, 20, is_kr=is_kr)
    ud = ud_volume_ratio(c, v)
    stop = m60 * 0.98
    candidates = [x for x in (stop, float(lo.iloc[-10:].min())) if x < close]
    stop = max(candidates) if candidates else float(lo.iloc[-10:].min())
    # ATR 버퍼 (추세전환=0.3, 변동성 여유)
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, 0.3)
    risk_pct = (pivot - stop) / pivot * 100 if pivot > 0 else 0.0
    pivot_dist_pct = (pivot - close) / close * 100

    # ── 점수 (100점) ──
    score = 0.0
    score += 25 * (cfg["align_window"] + 1 - align_days) / cfg["align_window"]  # 신선도
    if rs_mom is not None:
        score += 20 * max(0.0, min(rs_mom, 40)) / 40                            # RS 개선 폭
    if rs_rank is not None:
        score += 10 * rs_rank / 99                                              # 현재 RS
    score += 10 * max(0.0, min((vol_ratio - 0.9) / 0.9, 1.0))                   # 거래량 확장
    score += 10 * (1 - min(ma200_dist, 0.25) / 0.25)                            # 200일선 근접
    score += 15 * min(vol_mult_5d / 3.0, 1.0)                                   # 돌파일 거래량 폭증
    if breakout_vol:
        score += 10                                                            # 진짜 돌파 보너스

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
        "ma200_rising": ma200_rising,
        "breakout_vol": breakout_vol,
        "vol_mult_today": round(vol_mult_today, 1),
        "vol_mult_5d": round(vol_mult_5d, 1),
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
                    entry=close, warn_pct=15.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf),
        **volume_info(close, v),
        "avwap": anchored_vwap(h, lo, c, v),
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
                     rs_mom: int | None = None, cfg: dict = BREAKOUT_CONFIG, is_kr: bool = False) -> dict | None:
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
    # ATR 버퍼 (돌파=0.15, 타이트 유지 — 피벗 깨지면 빠른 손절이 정석)
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, 0.15)
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
        **_merger_block(c, h, lo, v),
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
                    entry=close, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf),   # 이미 돌파 → 현재가 진입 기준
        "rsi": round(cur_rsi, 1),
        "vol_dry": False,
        "ud_vol": up_down_volume(c, v, 50),
        **volume_info(close, v),
        "avwap": anchored_vwap(h, lo, c, v),
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
    # ATR 버퍼 (박스돌파=0.15, 타이트 유지)
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, 0.15)
    # 이미 돌파한 상태 → 실제 진입은 현재가. 리스크/손익비 모두 현재가 기준으로 통일.
    risk_pct = (close - stop) / close * 100 if close > 0 else 0.0

    score = round(best["quality"] * 100 * (0.7 + 0.3 * rs_rank / 99), 1)

    return {
        "mode": "boxbreak",
        **_merger_block(c, h, lo, v),
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
                    entry=close, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf),   # 이미 돌파 → 현재가 진입 기준
        "rsi": round(cur_rsi, 1),
        "vol_dry": False,
        "ud_vol": up_down_volume(c, v, 50),
        **volume_info(close, v),
        "avwap": anchored_vwap(h, lo, c, v),
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
    # ATR 버퍼 (돌파임박=0.15, 타이트 유지)
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, 0.15)
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
        **_merger_block(c, h, lo, v),
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
                    entry=None, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf),
        **volume_info(close, v),
        "avwap": anchored_vwap(h, lo, c, v),
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


INVERSE_CONFIG = {
    "min_bars": 60,
    "rsi_overbought": 80,     # 인버스가 과열(=지수 과대낙폭, 반등 위험)
}


def analyze_inverse(df: pd.DataFrame, meta: dict | None = None,
                    cfg: dict = INVERSE_CONFIG) -> dict | None:
    """인버스 ETF 분석. 일반 종목의 거울상 —
    인버스가 강세(정배열·상승)면 = 지수가 약세 = 하락장 신호.

    반환 dict의 'strength'로 하락 강도를 표현:
      strong: 인버스 정배열+상승 = 본격 하락장 (인버스 매수 가능 구간)
      building: 인버스 상승 시작 = 하락 전환 조짐
      weak: 인버스 약세 = 지수 견조 (인버스 부적합)
    """
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None

    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    ma200 = c.rolling(min(200, len(c))).mean()
    r = rsi(c)

    close = float(c.iloc[-1])
    m20, m60, m200 = float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma200.iloc[-1])
    cur_rsi = float(r.iloc[-1])
    if any(math.isnan(x) for x in (m20, m60, cur_rsi)):
        return None

    prev = float(c.iloc[-2]) if len(c) > 1 else close
    change_pct = (close - prev) / prev * 100 if prev > 0 else 0.0

    # 인버스 강세 판정 (= 지수 약세)
    aligned = m20 > m60 and (math.isnan(m200) or m60 > m200) and close > m20
    above_ma20 = close > m20
    ma20_slope = m20 > float(ma20.iloc[-6]) if len(ma20) > 6 else False  # 20일선 상승
    vol_mult = 0.0
    vol50 = float(v.rolling(min(50, len(v))).mean().iloc[-1])
    if vol50 > 0:
        vol_mult = float(v.iloc[-1]) / vol50

    # 최근 5일 수익률 (인버스가 오르는 중인가)
    ret5 = (close / float(c.iloc[-6]) - 1) * 100 if len(c) > 6 else 0.0

    if aligned and ma20_slope:
        strength, txt = "strong", "본격 하락장 (인버스 강세)"
    elif above_ma20 and ret5 > 0:
        strength, txt = "building", "하락 전환 조짐 (인버스 상승 시작)"
    else:
        strength, txt = "weak", "지수 견조 (인버스 부적합)"

    # 과열 경고: 인버스 RSI 과매수 = 지수 과대낙폭 = 반등(인버스 급락) 위험
    overheated = cur_rsi >= cfg["rsi_overbought"]

    name = (meta or {}).get("name", "")
    leverage = (meta or {}).get("leverage", 1)
    underlying = (meta or {}).get("underlying", "")

    return {
        "name": name,
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "strength": strength,
        "strength_txt": txt,
        "leverage": leverage,
        "underlying": underlying,
        "above_ma20": above_ma20,
        "ma20_slope_up": ma20_slope,
        "aligned": aligned,
        "ret5_pct": round(ret5, 1),
        "vol_mult": round(vol_mult, 1),
        "rsi": round(cur_rsi, 1),
        "overheated": overheated,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }

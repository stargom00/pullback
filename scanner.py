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


def late_stage_info(c: pd.Series, lo: pd.Series, h: pd.Series, v: pd.Series,
                    is_kr: bool = False) -> dict:
    """후기 스테이지/과확장 종합 판정 (v4.48).
    미너비니: 큰 시세를 낸 뒤의 베이스일수록 실패 확률이 높고,
    200일선 이격이 클수록 클라이맥스(소진) 리스크가 커진다.
    반환: {ext200_pct, base_count_approx, flags[], level: none|caution|danger}
    - ext200 >= danger(기본 100%) 또는 클라이맥스 danger → danger (제외 대상)
    - ext200 >= caution(기본 60%) / 조정 3회+ / 클라이맥스 caution → caution (배지)
    """
    cfg = CONFIG
    flags, level = [], "none"
    try:
        ma200 = c.rolling(200).mean()
        m200 = float(ma200.iloc[-1]) if len(ma200.dropna()) else 0.0
        close = float(c.iloc[-1])
        ext200 = (close / m200 - 1.0) if m200 > 0 else 0.0
    except Exception:
        ext200 = 0.0
    if ext200 >= cfg.get("ext200_danger", 1.0):
        flags.append(f"이격{int(ext200*100)}%")
        level = "danger"
    elif ext200 >= cfg.get("ext200_caution", 0.6):
        flags.append(f"이격{int(ext200*100)}%")
        level = "caution"
    # 베이스 카운트 근사 (바닥 후 15%+ 조정 횟수)
    try:
        bi = count_bases_since_bottom(c, lo, h)
        n_corr = bi.get("corrections", 0)
        if 0 < n_corr < 99 and n_corr >= cfg.get("late_base_caution", 3):
            flags.append(f"{n_corr + 1}차베이스")
            if level == "none":
                level = "caution"
    except Exception:
        pass
    # 클라이맥스 (기존 함수 — 이제야 연결)
    try:
        cx = climax_warning(c, h, lo, v)
        if cx.get("climax"):
            flags.extend(cx.get("reasons", []))
            if cx.get("level") == "danger":
                level = "danger"
            elif level == "none":
                level = "caution"
    except Exception:
        pass
    return {"ext200_pct": round(ext200 * 100, 1),
            "late_flags": flags, "late_level": level}


def distribution_check(c: pd.Series, h: pd.Series, lo: pd.Series, v: pd.Series) -> dict:
    """보유 종목의 분산(매도) 신호 감지 (v4.51) — 진입 후 위험 경보용.
    오닐/미너비니 기준: 기관이 팔기 시작하는 신호를 조합.
    반환: {level: none|caution|danger, signals: [...], detail: {...}}

    신호:
    - 고점대량반전: 신고가 부근에서 대량거래 + 종가 저가권 마감 (BHE 패턴)
    - 최대급락일: 최근 하락이 60일 중 최대급 + 대량거래
    - UD악화: U/D 비율 1.0 미만 (매집→분산 전환)
    - 이평이탈: 종가가 21일선 아래 마감 (단기 추세 훼손)
    - 50일선이탈: 종가가 50일선 아래 (중기 추세 훼손 — 더 심각)
    """
    out = {"level": "none", "signals": [], "detail": {}}
    try:
        if c is None or len(c) < 55:
            return out
        close = float(c.iloc[-1])
        prev = float(c.iloc[-2])
        vol_today = float(v.iloc[-1])
        avg50v = float(v.iloc[-50:].mean())
        vol_ratio = vol_today / avg50v if avg50v > 0 else 1.0
        day_ret = close / prev - 1 if prev > 0 else 0.0

        signals, danger = [], False

        # 1) 고점 대량 반전: 최근 20봉 고가 근처(-3% 이내)에서 대량거래 + 종가가
        #    당일 레인지 하위 40%에 마감 (윗꼬리 = 매도 소화)
        hi20 = float(h.iloc[-20:].max())
        near_high = close >= hi20 * 0.97
        day_hi, day_lo = float(h.iloc[-1]), float(lo.iloc[-1])
        rng = day_hi - day_lo
        close_pos = (close - day_lo) / rng if rng > 0 else 0.5
        if near_high and vol_ratio >= 1.5 and close_pos <= 0.4:
            signals.append("고점대량반전")
            danger = True

        # 2) 최대 급락일 + 대량거래
        daily = c.pct_change()
        if (day_ret <= float(daily.iloc[-60:].min()) and day_ret < -0.04
                and vol_ratio >= 1.3):
            signals.append("최대급락일")
            danger = True

        # 3) U/D 악화 (매집→분산)
        ud = up_down_volume(c, v, 50)
        if ud is not None and ud < 1.0:
            signals.append(f"U/D {ud} 분산")

        # 4) 이평 '이탈' — 위에서 아래로 깨는 순간만 분산 신호.
        # (버그 수정: 단순히 'close < ma50'이면 이미 한참 전 하락해 바닥에서
        #  반등 중인 종목도 매일 danger로 오탐. 네이처셀 +8.9% 양봉 사례.
        #  '어제는 이평 위 → 오늘 이평 아래'로 새로 깨는 하락일만 신호로 인정.)
        #
        # v4.56 [수정] 거래량 게이트 + 리클레임 억제 추가.
        #  [원인] 신호 1·2는 거래량 조건(1.5배/1.3배)이 있는데 이평이탈만 없었음.
        #         분산 = 기관 매도 = 거래량 동반. 거래량 없는 이탈은 분산이 아니라
        #         흔들기(shakeout). 슈피겐 사례: 지지 언더컷 후 종가 레인지 상단
        #         회복 + 거래량 0.57배였는데 danger로 오탐.
        #  [수정] 이평이탈이 danger가 되려면 (거래량 >= DIST_VOL_MIN) AND (종가가
        #         레인지 하위권 마감). 저거래량이거나 종가를 되찾았으면 caution으로
        #         강등하고 '흔들기 가능' 라벨을 붙인다.
        DIST_VOL_MIN = 1.2        # 분산 인정 최소 거래량 배수
        prev_close = float(c.iloc[-2])
        ma21 = float(c.rolling(21).mean().iloc[-1])
        ma50 = float(c.rolling(50).mean().iloc[-1])
        ma21_prev = float(c.rolling(21).mean().iloc[-2])
        ma50_prev = float(c.rolling(50).mean().iloc[-2])
        is_down_day = day_ret < 0
        vol_confirms = vol_ratio >= DIST_VOL_MIN
        reclaimed = close_pos >= 0.5      # 장중 깨고 종가는 상단 회복 = 흔들기
        dist_ok = vol_confirms and not reclaimed

        # 오늘 하락하며 50일선을 새로 깬 경우만 (어제는 위 or 근처)
        if is_down_day and close < ma50 and prev_close >= ma50_prev:
            if dist_ok:
                signals.append("50일선이탈(대량)")
                danger = True
            else:
                signals.append(f"50일선이탈(거래량 {vol_ratio:.2f}배 — 흔들기 가능)")
        elif is_down_day and close < ma21 and prev_close >= ma21_prev:
            if dist_ok:
                signals.append("21일선이탈(대량)")
            else:
                signals.append(f"21일선이탈(거래량 {vol_ratio:.2f}배 — 흔들기 가능)")

        # 5) 클라이맥스(소진) 연계
        # v4.56 [수정] climax_warning의 '최대급락일'은 거래량 조건이 없는 '가격 기반'
        #   신호다(scanner.py:53). 이걸 분산 판정에 danger로 그대로 상속하면
        #   저거래량 급락(= 흔들기)도 '기관 매도'로 오탐한다.
        #   '소진성거래량'은 정의상 60봉 최대 거래량이라 그 자체로 확증 → danger 유지.
        #   '최대급락일'은 거래량 확증(vol_confirms)이 있을 때만 danger로 승격.
        cx = climax_warning(c, h, lo, v)
        if cx.get("climax") and cx.get("level") == "danger":
            cx_reasons = cx.get("reasons", [])
            for r in cx_reasons:
                if r not in signals:
                    signals.append(r)
            if "소진성거래량" in cx_reasons or vol_confirms:
                danger = True

        if not signals:
            return out
        out["signals"] = signals
        out["level"] = "danger" if danger else "caution"
        out["detail"] = {"vol_ratio": round(vol_ratio, 2),
                         "day_ret_pct": round(day_ret * 100, 1),
                         "ud": ud}
        return out
    except Exception:
        return out


def ftd_state(close: pd.Series, vol: pd.Series) -> dict:
    """오닐 FTD(팔로우스루 데이) 상태 머신 (v4.57).

    로직:
    - 조정 판정: 저점 이전 고점 대비 -6% 이상 하락했을 때만 FTD 개념 적용
    - 반등 시도: 최근 40일 내 최저 종가일 = 시도의 저점(rally_low).
      argmin 정의상 그 이후 종가는 저점을 깨지 않았음이 보장됨.
    - rally_day: 저점일=1일차로 센 경과 거래일 수
    - FTD: 시도 4일차 이후, 지수 +1.25%↑ 상승 + 거래량 전일比 증가인 날

    v4.57 추가 — 조정 종료 조건:
      지수가 (a) 저점 대비 조정폭의 절반 이상 회복했고 (b) 조정 이전 고점의
      -3% 이내면 조정 국면 종료(in_correction=False). FTD 발생 여부는 조건에
      넣지 않는다 — FTD 없이 완만히 회복한 시장이 40일 창에 저점이 남은 한
      영원히 조정 모드로 갇히는 버그를 막기 위함.

    반환에 추가: ftd_idx_back(FTD가 끝에서 몇 번째 뒤), peak_before, recovered
    """
    out = {"in_correction": False, "rally_day": 0, "ftd": False,
           "ftd_days_ago": None, "ftd_idx_back": None,
           "rally_low": None, "peak_before": None,
           "drawdown_pct": 0.0, "recovered": False}
    try:
        if close is None or len(close) < 45 or vol is None or len(vol) != len(close):
            return out
        c60 = close.iloc[-60:].reset_index(drop=True)
        v60 = vol.iloc[-60:].reset_index(drop=True)
        n60 = len(c60)
        tail = min(40, n60)
        low_local = int(c60.iloc[-tail:].reset_index(drop=True).idxmin())
        low_p = n60 - tail + low_local
        rally_low = float(c60.iloc[low_p])
        peak_before = float(c60.iloc[:low_p + 1].max())
        drawdown = (rally_low / peak_before - 1.0) * 100 if peak_before > 0 else 0.0
        out["rally_low"] = round(rally_low, 2)
        out["peak_before"] = round(peak_before, 2)
        out["drawdown_pct"] = round(drawdown, 1)
        if drawdown > -6.0:
            return out                      # 조정 아님 — FTD 불필요
        last_i = n60 - 1
        out["rally_day"] = last_i - low_p + 1   # 저점일 = 1일차
        ret = c60.pct_change()
        ftd_i = None
        for i in range(low_p + 3, last_i + 1):
            if float(ret.iloc[i]) >= 0.0125 and float(v60.iloc[i]) > float(v60.iloc[i - 1]):
                ftd_i = i
                break
        if ftd_i is not None:
            out["ftd"] = True
            out["ftd_days_ago"] = last_i - ftd_i
            out["ftd_idx_back"] = last_i - ftd_i
        # ── v4.57: 조정 종료 판정 (FTD 유무와 무관) ──
        cur = float(c60.iloc[-1])
        recovered = False
        if peak_before > rally_low:
            retrace = (cur - rally_low) / (peak_before - rally_low)
            near_peak = cur >= peak_before * 0.97
            recovered = (retrace >= 0.50) and near_peak
        out["recovered"] = recovered
        out["in_correction"] = not recovered
        return out
    except Exception:
        return out


def dist_count(close: pd.Series, vol: pd.Series, ftd: dict | None = None,
               window: int = 25, drop_pct: float = -0.002,
               expire_gain: float = 0.05) -> dict:
    """분산일 카운트 — 오닐 제거 규칙 포함 (v4.57 신규).

    분산일 = 지수가 전일 대비 drop_pct(-0.2%)↓ + 거래량이 전일보다 증가한 날.
    기존 코드(app._index_regime 인라인)는 순수 25일 롤링 합만 써서 두 가지
    제거 규칙이 없었다 → "늘어나기만 하고 안 빠지는" 카운트. 두 규칙 추가:
      (a) FTD 리셋: FTD 이전 분산일은 '이전 조정'의 것 → 제외
      (b) 5% 만료: 현재가가 그 분산일 종가 대비 +5%↑면 소화된 매도 → 만료

    Volume이 없거나 전부 0이면 days=None (판정 불가). 0을 반환하면 "분산일
    없음=건강한 시장"이라는 반대 신호가 되므로 절대 0으로 위장하지 않는다.

    반환: {days, raw, expired, pre_ftd, dates, vol_ok}
    """
    out = {"days": None, "raw": None, "expired": 0, "pre_ftd": 0,
           "dates": [], "vol_ok": False}
    try:
        if close is None or len(close) < window + 2:
            return out
        if vol is None or len(vol) != len(close):
            return out
        v_win = vol.iloc[-(window + 1):]
        if float(v_win.fillna(0).sum()) <= 0:
            return out
        if int(v_win.isna().sum()) > window * 0.3:
            return out
        out["vol_ok"] = True

        ret = close.pct_change()
        vol_up = vol > vol.shift(1)
        down = ret <= drop_pct
        mask = (down & vol_up).iloc[-window:]
        raw = int(mask.sum())
        out["raw"] = raw

        cur = float(close.iloc[-1])
        ftd_back = (ftd or {}).get("ftd_idx_back")
        kept, expired, pre_ftd = [], 0, 0
        n = len(close)
        for pos, flag in enumerate(mask.tolist()):
            if not flag:
                continue
            idx_back = (window - 1) - pos
            abs_i = n - 1 - idx_back
            d_close = float(close.iloc[abs_i])
            if ftd_back is not None and idx_back > ftd_back:
                pre_ftd += 1
                continue
            if d_close > 0 and (cur / d_close - 1.0) >= expire_gain:
                expired += 1
                continue
            kept.append({
                "idx_back": idx_back,
                "ret_pct": round(float(ret.iloc[abs_i]) * 100, 2),
                "vol_x": round(float(vol.iloc[abs_i]) / float(vol.iloc[abs_i - 1]), 2)
                          if float(vol.iloc[abs_i - 1]) > 0 else None,
            })
        out["days"] = len(kept)
        out["expired"] = expired
        out["pre_ftd"] = pre_ftd
        out["dates"] = kept
        return out
    except Exception:
        return out


def gate_suggest(dist, ftd: dict, above_ma60: bool) -> tuple[str, str]:
    """분산일 + FTD 상태 → 시장 게이트 자동 제안 (v4.57).

    ⚠️ 시그니처 변경: dist_days(int) → dist(dict, dist_count()의 반환).
       거래량 없어 판정 불가(days=None)를 구분하기 위함. app._index_regime이
       dist_count() 결과를 넘기도록 함께 수정됨.

    [v4.57 핵심] FTD 분기를 분산일 임계보다 먼저 평가. 기존엔
    `if dist_days >= 6: return correction`이 맨 위라 FTD 분기가 죽은 코드였음
    (FTD는 조정 뒤에 나오므로 분산일이 큰 게 정상인데 그걸 먼저 걸러버림).

    반환: (gate, 이유). gate: confirmed|pressure|correction
    노출 %는 만들지 않는다 — R 설정(3R/1.5R/0)이 유일한 근거 있는 규칙.
    """
    # dict 아닌 int가 들어오면(구 호출부 잔존) 방어적으로 감싸기
    if isinstance(dist, int):
        dist = {"days": dist, "raw": dist}
    d = dist.get("days")

    # 0) 거래량 없어 판정 불가 — 0으로 위장 금지
    if d is None:
        if not above_ma60:
            return "correction", "60일선 아래 · 거래량 데이터 없어 분산일 판정 불가"
        return "pressure", "거래량 데이터 없어 분산일 판정 불가 — 보수적 판정"

    # 1) FTD 먼저 (v4.57: 순서가 핵심)
    if ftd.get("ftd"):
        ago = ftd.get("ftd_days_ago")
        if ftd.get("recovered"):
            if d >= 6:
                return "correction", f"FTD 후 회복했으나 분산일 재차 {d}개 — 신규 매도 압력"
            if d >= 4:
                return "pressure", f"FTD 후 회복 · 분산일 {d}개 누적 — A급만"
            return "confirmed", f"FTD 후 회복 완료 · 분산일 {d}개"
        if d >= 5:
            return "correction", f"FTD({ago}일 전) 후 분산일 {d}개 — 랠리 실패 조짐"
        if d >= 3:
            return "pressure", f"FTD({ago}일 전) 후 분산일 {d}개 — A급만 1.5R"
        return "confirmed", (f"FTD 확인 ({ago}일 전) · 분산일 {d}개 — "
                             f"시험 매수 0.5R 1~2건부터")

    # 2) 조정 중인데 FTD 아직 없음
    if ftd.get("in_correction"):
        day = ftd.get("rally_day", 0)
        dd = ftd.get("drawdown_pct")
        return "correction", (f"조정 중 (고점比 {dd}%) · 반등 시도 {day}일차 · FTD 대기")

    # 3) FTD 없이 회복한 경우 (조정은 끝났으나 매수 확증 없음)
    if ftd.get("recovered") and not ftd.get("ftd"):
        if not above_ma60:
            return "pressure", "60일선 아래 — 선별 진입"
        if d >= 5:
            return "correction", f"FTD 없이 회복 · 분산일 {d}개 — 매도 압력 우세"
        if d >= 3:
            return "pressure", f"FTD 없이 회복 · 분산일 {d}개 — 매수 확증 부족"
        return "confirmed", f"조정 회복 (FTD 미발생) · 분산일 {d}개"

    # 4) 정상 추세
    if not above_ma60:
        return "pressure", "60일선 아래 — 선별 진입"
    if d >= 6:
        return "correction", f"분산일 {d}개 — 기관 매도 우세"
    if d >= 4:
        return "pressure", f"분산일 {d}개 — 압박 누적"
    return "confirmed", f"상승 추세 · 분산일 {d}개"


def mom_3m(c: pd.Series) -> float | None:
    """3개월(63거래일) 절대 수익률. RS(상대)의 폭락장 맹점 보완용."""
    if len(c) < 64:
        return None
    base = float(c.iloc[-64])
    return float(c.iloc[-1]) / base - 1.0 if base > 0 else None


def trend_grade(c: pd.Series, lo: pd.Series, h: pd.Series, rs_rank,
                ud: float | None = None) -> dict:
    """미너비니 Trend Template 8조건 채점 → A/B/C/D 등급 (v4.48.3).
    이 앱의 이평 체계(20/60/200)에 맞게 150일선 조건은 60일선으로 대응.
    A = 8/8 + RS 87+ (진짜 주도주) / B = 7+ / C = 5~6 / D = 그 이하.
    각 카드에 등급 배지로 표시 — '수많은 종목 중 진짜'를 한 글자로.

    v5.32: len(c)<200이면 등급 자체를 매기지 않는다("?"). 예전엔 ma200/
    ma200_prev가 NaN인데도 8조건 중 "200일선 위·200일선 상승·60>200일선"
    3개가 비교 자체는 실행돼(NaN 비교는 예외를 안 던지고 그냥 False) 무조건
    실패 처리됐다 — boxbreak(min_bars=140)/pattern(130) 탭의 신규 상장주가
    실제론 D급이 아닌데도 33/33 전부 D로 나오던 원인. 200일선 조건은
    "어려운 조건"이 아니라 상장 200일 미만이면 정의 자체가 안 되는
    질문이라, 점수를 깎지 않고 판정 불가로 둔다. 프론트(static/index.html)
    는 이미 grade==='?'일 때 등급 배지를 렌더링하지 않아 UI 변경 불필요."""
    if len(c) < 200:
        return {"grade": "?", "passed": 0, "fails": [], "ud_note": ""}
    try:
        close = float(c.iloc[-1])
        ma20 = float(c.rolling(20).mean().iloc[-1])
        ma60 = float(c.rolling(60).mean().iloc[-1])
        ma200s = c.rolling(200).mean()
        ma200 = float(ma200s.iloc[-1])
        ma200_prev = float(ma200s.iloc[-21]) if len(ma200s.dropna()) > 21 else ma200
        lo52 = float(lo.iloc[-252:].min()) if len(lo) >= 252 else float(lo.min())
        hi52 = float(h.iloc[-252:].max()) if len(h) >= 252 else float(h.max())
        rs = rs_rank if rs_rank is not None else 50
        checks = [
            ("200일선 위", close > ma200),
            ("200일선 상승", ma200 > ma200_prev),
            ("60일선 위", close > ma60),
            ("60일선>200일선", ma60 > ma200),
            ("20일선 위", close > ma20),
            ("52주 저점 +30%↑", lo52 > 0 and close / lo52 - 1 >= 0.30),
            ("52주 고점 -25% 이내", hi52 > 0 and 1 - close / hi52 <= 0.25),
            ("RS 70+", rs >= 70),
        ]
        passed = sum(1 for _, ok in checks if ok)
        fails = [name for name, ok in checks if not ok]
        if passed == 8 and rs >= 87:
            grade = "A"
        elif passed >= 7:
            grade = "B"
        elif passed >= 5:
            grade = "C"
        else:
            grade = "D"
        # U/D 반영 (v4.49): 분산(≤0.8) = 기관이 팔고 있다는 뜻 → 한 단계 강등.
        # 차트가 8/8이어도 하락일에 거래량이 실리면 A급이 아님 (A/D Rating 근사).
        ud_note = ""
        if ud is not None:
            if ud <= 0.8:
                order = ["A", "B", "C", "D"]
                if grade in order[:-1]:
                    grade = order[order.index(grade) + 1]
                fails.append(f"U/D {ud} 분산")
                ud_note = "분산"
            elif ud >= 1.5:
                ud_note = "매집"
        return {"grade": grade, "passed": passed, "fails": fails, "ud_note": ud_note}
    except Exception:
        return {"grade": "?", "passed": 0, "fails": [], "ud_note": ""}


def _risk_hard_ok(rrb: dict, is_kr: bool, pivot: float | None = None) -> bool:
    """리스크 기하 하드 게이트: 손절폭이 한도를 넘으면 베이스가 너무 느슨한
    것 → 후보 제외. (risk_warn 표시만 하던 것을 강제화)

    판정 기준은 '피벗 → 현실화 손절' 거리 (베이스의 구조적 느슨함).
    당일 급등한 돌파일의 종가 기준 리스크로 판정하면 정상 셋업까지 잘리므로
    (Case13 회귀), pivot이 주어지면 피벗 기준으로 계산한다.
    BHE 사례(피벗 94.75, 손절 85 → 10.3%)는 피벗 기준으로도 차단됨.

    v5.40: 한도 = max(고정 US8%/KR12%, ATR%×1.5) — loosen-only. 고ATR
    종목(예: DELL 9.06%/ATR 7.7%)이 구조적으로 탈락하던 문제(badge_fields의
    stop_wide는 v4.67에 이미 ATR×1.5로 바뀌었는데 이 게이트만 고정 %로
    남아있던 불일치) 해소. 저ATR 종목은 max()라 기존 통과분에 영향 없음
    (loosen-only 실측: 4탭 전부 baseline 이하로 떨어지는 사례 0건).
    절대 상한 15%는 유지 — ATR이 커도 무제한 완화는 게이트 무력화."""
    if not CONFIG.get("risk_hard_enforce", True):
        return True
    fixed_limit = CONFIG.get("risk_hard_kr", 12.0) if is_kr else CONFIG.get("risk_hard_us", 8.0)
    try:
        stop_eff = float(rrb.get("stop", 0.0))
        if pivot and pivot > 0 and stop_eff > 0:
            risk = (pivot - stop_eff) / pivot * 100.0
        else:
            risk = float(rrb.get("risk_pct", 0.0))
        limit = fixed_limit
        atr_pct = rrb.get("atr_pct")
        if atr_pct is not None and atr_pct > 0:
            atr_limit = min(atr_pct * CONFIG.get("risk_hard_atr_mult", 1.5),
                            CONFIG.get("risk_hard_atr_cap", 15.0))
            limit = max(fixed_limit, atr_limit)
        return risk <= limit
    except Exception:
        return True


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
    seg_v = v.iloc[-120:-5]
    seg_c = c.iloc[-120:-5]
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


def off_high_pct(c, lookback: int = 252) -> float:
    """최근 lookback봉 고점 대비 현재가 낙폭(%). 음수=고점 아래.
    예: 고점 6.57, 현재 3.28 → -50.1 반환. 돌파/임박 모드에서 '무너진
    종목의 가짜 돌파' 거름용. (BLDP 케이스: -50%인데 단기저항을 피벗으로
    오인해 '돌파임박'으로 잡히던 문제 차단)"""
    cc = c.dropna()
    if len(cc) < 20:
        return 0.0
    win = cc.iloc[-lookback:] if len(cc) >= lookback else cc
    hi = float(win.max())
    now = float(cc.iloc[-1])
    return (now - hi) / hi * 100 if hi > 0 else 0.0


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
        "avg_turnover": round(close * avg50),   # 평균 거래대금 (v4.49.2 유동성 판정용)
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
    # v4.68: 일간 변동성(ATR%) — 카드에 손절폭과 나란히 표시해 '넓다/좁다'를 직접 판단 가능하게.
    try:
        _cur = float(c.iloc[-1])
        atr_pct = round(atr(h, lo, c) / _cur * 100, 1) if _cur > 0 else None
    except Exception:
        atr_pct = None
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
        "atr_pct": atr_pct,
    }


# ── 설정 ──────────────────────────────────────────────
CONFIG = {
    "min_bars": 210,           # 최소 일봉 개수 (200일선 계산용)
    "ma_short": 10,
    "ma_mid": 20,
    "ma_long": 60,
    "ma_trend": 200,           # 장기 추세 필터
    "pullback_min": 0.03,      # 최근 고점 대비 최소 조정폭 3%
    # 최대 조정폭 (이상이면 눌림이 아니라 새 베이스 구축 → 패턴 탭 영역)
    # ※ 장중 고가 기준으로 측정 (종가 기준은 실제 조정을 과소평가 — 디앤디 사례:
    #    장중고점 대비 -24%인데 종가고점 대비 -18%로 계산돼 눌림목에 잘못 표시됨)
    "pullback_max_kr": 0.15,   # KR: 변동성 커서 15%까지 허용
    "pullback_max_us": 0.12,   # US: 12% (미너비니 기준 건강한 눌림 상한)
    # ── 후기 스테이지/확장도 게이트 (v4.48, BHE 사후분석) ──
    # BHE 사례: 6개월 +110%, 200일선 이격 +70%의 4차 베이스 돌파(95)를 통과시켜
    # -9.4% 붕괴를 맞음. 확장도가 주 필터(BHE의 베이스들은 9%대로 얕아 카운트로 안 걸림).
    "ext200_caution": 0.60,    # 200일선 이격 60%+ → 후기 스테이지 경고 (배지+감점)
    "ext200_danger": 1.00,     # 200일선 이격 100%+ → 제외 (클라이맥스 영역)
    "late_base_caution": 3,    # 바닥 후 15%+ 조정 3회+ (≈4차 베이스) → 경고
    "late_stage_exclude": True,  # danger 레벨 제외 여부
    # 리스크 기하 하드 게이트: 구조 손절이 한도보다 멀면 "베이스가 느슨" → 제외.
    # (기존 risk_warn은 표시만 했음 — BHE 10.3%가 경고 딱지 달고 통과한 버그)
    # 절대 모멘텀 (v4.49, 앤트킹 스크린 차용): 3개월 +30% 이상만 주도주로 인정.
    # RS는 유니버스 내 '상대' 백분위라 폭락장에선 "덜 빠진 종목"도 90이 나오는
    # 맹점이 있음 — 절대 수익률 조건이 그런 가짜 주도주를 걸러냄.
    "leader_mom_3m_min": 0.30,
    "risk_hard_kr": 12.0,
    "risk_hard_us": 8.0,
    "risk_hard_enforce": True,
    # v5.40: loosen-only ATR 완화 — 고정 한도(8%/12%)는 그대로 두고,
    # ATR%×1.5가 고정 한도보다 크면(=고ATR 종목) 그쪽을 한도로 채택.
    # badge_fields의 stop_wide가 이미 쓰는 ATR×1.5를 게이트에도 그대로
    # 적용(새 배수 발명 안 함). 절대 상한 15%는 유지 — ATR이 아무리 커도
    # 손절폭이 15%를 넘으면 "완화"가 아니라 "게이트 무력화"라 무조건 탈락.
    # 저ATR 종목은 영향 없음(max()라 기존 통과분이 새로 탈락하는 일 없음).
    "risk_hard_atr_mult": 1.5,
    "risk_hard_atr_cap": 15.0,
    "pullback_max": 0.18,      # (구버전 호환용 폴백 — 시장별 키 없을 때만 사용)
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


def ud_volume_detail(c: pd.Series, v: pd.Series, window: int = 50) -> dict | None:
    """U/D Volume Ratio 신뢰도 분해 (v5.24) — 단일/소수 급등일이 상승거래량
    분자를 지배해 실제로는 이벤트성 순환매인데 매집처럼 보이는 왜곡을 탐지.

    사용자 리포트 사례(한울반도체 320000.KQ): 일봉 U/D 1.86으로 건강해
    보였지만, 실제로는 이틀(2026-06-22~23) 거래량이 상승거래량 합의 68%를
    차지한 이벤트성 급등(그 주 고점 대비 종가 완전 반납, 이후 주가는 사건
    이전보다도 낮아짐)이었음 — 매집이 아니라 순환매였다.
    재현 검증: 이 종목은 top1_share(0.33)만으론 0.40 임계를 못 넘었지만
    top3_share(0.68)가 0.65를 넘어 정확히 걸림 — 그래서 게이트는
    top1_share 단독이 아니라 top1_share·top3_share 두 조건을 AND로 본다
    (임계값 자체는 재현 검증 후 그대로 유지, 낮추지 않음).

    반환: {ud_raw, top1_share, top3_share, ud_ex_top1, ud_ex_top3, hhi,
    n50, ud_reliable}.
    - ud_ex_top1: 최대 상승일 거래량 하나를 뺀 U/D (진단용 dict 보관, 카드
      주 표시는 ud_ex_top3를 씀 — 이틀·사흘짜리 사건을 더 잘 잡기 때문).
    - ud_ex_top3: 상위 3개 상승일 거래량을 뺀 U/D (카드 주 표시값).
    - n50: 상승거래량 누적 50%에 도달하는 데 필요한 최소 일수(진단용,
      게이트 아님) — 1이면 하루가 전체의 절반 이상, 클수록 고르게 분산.
    - 하락일 또는 상승일 거래량 합이 0이면(ZeroDivision 위험) None을
      반환하고 호출부가 '데이터부족'으로 처리한다 — 방어값(0/9.99 등)으로
      채우지 않는다.
    """
    if len(c) < window + 1:
        window = len(c) - 1
    if window < 5:
        return None
    cc = c.iloc[-window:]
    vv = v.iloc[-window:]
    prev = c.iloc[-(window + 1):-1].values

    up_vols, down_vols = [], []
    for i in range(len(cc)):
        if cc.iloc[i] > prev[i]:
            up_vols.append(float(vv.iloc[i]))
        elif cc.iloc[i] < prev[i]:
            down_vols.append(float(vv.iloc[i]))

    up_sum = sum(up_vols)
    down_sum = sum(down_vols)
    if up_sum <= 0 or down_sum <= 0:
        return None

    up_sorted = sorted(up_vols, reverse=True)
    top1 = up_sorted[0]
    top3_vol = sum(up_sorted[:3])

    top1_share = top1 / up_sum
    top3_share = top3_vol / up_sum
    hhi = sum((x / up_sum) ** 2 for x in up_vols)

    cum, n50 = 0.0, 0
    half = up_sum * 0.5
    for x in up_sorted:
        cum += x
        n50 += 1
        if cum >= half:
            break

    return {
        "ud_raw": round(up_sum / down_sum, 2),
        "top1_share": round(top1_share, 3),
        "top3_share": round(top3_share, 3),
        "ud_ex_top1": round((up_sum - top1) / down_sum, 2),
        "ud_ex_top3": round((up_sum - top3_vol) / down_sum, 2),
        "hhi": round(hhi, 3),
        "n50": n50,
        "ud_reliable": bool(top1_share < 0.40 and top3_share < 0.65),
    }


def weekly_ema10(c: pd.Series) -> float | None:
    """일봉 종가를 주봉으로 리샘플링해 10주 EMA 계산 (v5.04, 눌림 지지 알림용).
    최소 70봉(≈14주) 필요. 일봉 df가 이미 DatetimeIndex라 resample 바로 가능."""
    if c is None or len(c) < 70:
        return None
    try:
        weekly = c.resample("W").last().dropna()
        if len(weekly) < 10:
            return None
        return float(weekly.ewm(span=10, adjust=False).mean().iloc[-1])
    except Exception:
        return None


def monthly_retrace_50(c: pd.Series, lookback_months: int = 18) -> float | None:
    """월봉 기준 최근 상승 스윙의 50% 되돌림가 (v5.04, confluence 가산용 참고치).
    '월봉 상승분'의 정확한 스윙 판정 규칙이 스펙에 엄밀히 정의돼 있지 않아,
    최근 lookback_months개월 내 월봉 최저점 이후 형성된 최고점을 스윙으로
    보는 단순 버전으로 구현 — 정밀도보다 방향성 참고용(선택 조건)이라 충분."""
    if c is None or len(c) < 90:
        return None
    try:
        monthly = c.resample("ME").last().dropna()
        if len(monthly) < 6:
            return None
        recent = monthly.iloc[-lookback_months:] if len(monthly) >= lookback_months else monthly
        low_idx = recent.idxmin()
        after_low = recent.loc[low_idx:]
        if len(after_low) < 2:
            return None
        swing_low = float(after_low.iloc[0])
        swing_high = float(after_low.max())
        if swing_high <= swing_low:
            return None
        return swing_high - (swing_high - swing_low) * 0.5
    except Exception:
        return None


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


def significant_resistance_near(h, lo, close, window, min_touches=2,
                                band=0.02, exclude=2, max_dist=0.12):
    """'현재가에서 가장 가까운' 의미있는 저항 (돌파임박 전용, v4.52).
    기존 significant_resistance는 '가장 높은' 저항을 골라 먼 스파이크 고점을
    피벗으로 잡는 문제가 있음(더블유게임즈 76,500 오인). 이 함수는:
      ① 현재가 위쪽(+0.2%~+max_dist) 저항만 후보로
      ② 고가 반응(저항) + 저가 반응(지지) 합산 → 지지가 저항으로 바뀐
         자리(polarity flip)도 인식 — 리테스트 셋업의 핵심
      ③ 그중 '가장 가까운(낮은)' 유효 저항 반환 → 실제 리테스트 대상
    없으면 None(호출부에서 기존 로직으로 폴백)."""
    n = len(h)
    if n < window + exclude:
        return None
    hi = h.iloc[-(window + exclude):-exclude] if exclude > 0 else h.iloc[-window:]
    lw = lo.iloc[-(window + exclude):-exclude] if exclude > 0 else lo.iloc[-window:]
    hi = hi.dropna(); lw = lw.dropna()
    if len(hi) < min_touches:
        return None
    highs = hi.tolist()
    lows = lw.tolist()
    # 현재가 위 +0.2%~+max_dist 범위의 고가만 후보
    lo_b = close * 1.002
    hi_b = close * (1.0 + max_dist)
    cands = sorted({x for x in highs if lo_b <= x <= hi_b})
    best = None
    for level in cands:                 # 가까운(낮은) 것부터
        if level <= 0:
            continue
        # 고가 터치(저항) + 저가 터치(지지→저항 역전) 합산
        hit = sum(1 for x in highs if abs(x - level) / level <= band)
        hit += sum(1 for x in lows if abs(x - level) / level <= band)
        if hit >= min_touches:
            best = level                # 가장 가까운 유효 저항에서 멈춤
            break
    return best


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


def select_pivot(h, lo, c, close, recent_high_window: int, is_kr: bool = False,
                 use_near: bool = False, v=None):
    """
    피벗 후보 중 현재가 위에서 가장 가까운 것 선택.
    ★ 핵심: 피벗은 '베이스(횡보 구간)의 저항선'이라 고정돼야 한다.
       그래서 '오늘 포함 최근 며칠'(신고가 갱신 중인 봉)을 제외하고,
       그 이전 구간의 고점을 피벗으로 삼는다. → 주가가 신고가를 만들어도
       피벗(과거 천장)이 따라 움직이지 않음.
    - 베이스 천장(단기): 최근 5봉 고가, 단 직전 2봉(오늘·어제 신고가) 제외
    - 전고(중기): 최근 N봉 고가, 단 직전 2봉 제외
    - 추세선: 하락 추세선의 오늘 값
    use_near(v4.52): True면 '현재가에서 가장 가까운 의미있는 저항'을 우선.
       돌파임박 탭에서 먼 스파이크 고점 대신 리테스트 중인 실제 저항을 잡기 위함.
    반환: (pivot, pivot_type, tl_break, tl_break_intraday)

    v5.01 [버그수정] 거래량 없는 1회성 꼬리가 EXCLUDE 구간(오늘·어제)을 벗어나는
    순간 갑자기 '정식 피벗'으로 둔갑하던 문제(티쓰리 사례: 거래량 없이 찍은
    고가가 피벗이 2925→2950으로 튐). "전고"(significant_resistance)는 최소
    2회 터치를 요구해 이런 노이즈를 걸러내는데, "베이스천장"은 그 필터 없이
    5봉 중 raw 최댓값을 그대로 썼음. 이제 v(거래량)가 주어지면 그날 거래량이
    최근 20일 평균의 50% 미만인 봉은 베이스천장 후보에서 제외한다.
    """
    EXCLUDE = 2   # 오늘·어제(신고가 갱신 중일 수 있는 봉) 제외

    cands = []
    # v4.52: 가까운 저항 우선 (돌파임박) — 지지→저항 역전 자리까지 인식
    if use_near and len(h) > EXCLUDE + recent_high_window:
        near = significant_resistance_near(h, lo, close, recent_high_window,
                                           min_touches=2, band=0.02,
                                           exclude=EXCLUDE, max_dist=0.12)
        if near is not None:
            cands.append((float(near), "리테스트저항"))
    # 베이스 천장 — 직전 2봉 빼고 그 앞 5봉의 고가 (고정된 단기 저항)
    if len(h) > EXCLUDE + 5:
        win_h = h.iloc[-(5 + EXCLUDE):-EXCLUDE]
        base_short = float(win_h.max())
        if v is not None and len(v) > EXCLUDE + 20:
            win_v = v.iloc[-(5 + EXCLUDE):-EXCLUDE]
            avg_v = float(v.iloc[-(EXCLUDE + 20):-EXCLUDE].mean())
            if avg_v > 0:
                sig = win_v >= avg_v * 0.5
                if sig.any():
                    base_short = float(win_h[sig].max())
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

    v5.32: price_ago가 요청한 days만큼 데이터가 없으면(상장 200~252봉) 예전엔
    가장 오래된 봉으로 조용히 클램프해 "상장 초기가 = 12개월 전 가격"으로
    오인하는 분기를 만들었다(실측: 이 분기가 최종점수를 최대 ±0.14 왜곡,
    KR 11건/US 17건). 이제 부족한 분기는 None 처리 후 제외하고 남은 가중치로
    재정규화한다(accum_score와 동일 패턴, scanner.py의 ACCUM_WEIGHTS 참고).
    outer gate가 200이라 63/126/189일은 항상 확보되고 252(q4)만 빠질 수
    있지만, 향후 min_bars가 낮아져도 안전하도록 일반화해 두었다 — 남은
    분기가 1개 이하면 추세강도 지표로 의미가 없어 None을 반환한다.
    "이 종목 RS가 몇 분기짜리인지"는 rs_quarters_used()로 별도 조회 가능
    (핫패스 비용을 피하려고 반환 시그니처 자체는 안 바꿈)."""
    import math
    c = close.dropna()
    if len(c) < 200:
        return None
    now = float(c.iloc[-1])
    n = len(c)

    def price_ago(days):
        idx = -days - 1
        if n < days + 1:
            return None
        return float(c.iloc[idx])

    CLIP = 0.7  # 분기 로그수익률 상·하한 (≈ ±100%) — 저가주 폭등 왜곡 차단

    def logret(a, b):
        r = math.log(a / b)
        return max(-CLIP, min(CLIP, r))

    p0 = now
    p3 = price_ago(63)
    p6 = price_ago(126)
    p9 = price_ago(189)
    p12 = price_ago(252)

    # (가중치, 시작가, 끝가) — None이거나 <=0(오염 데이터)이면 그 분기 제외.
    quarters = [
        (0.4, p0, p3),   # q1: 최근 3개월
        (0.2, p3, p6),   # q2
        (0.2, p6, p9),   # q3
        (0.2, p9, p12),  # q4: 가장 오래된 3개월
    ]
    parts = [
        (w, logret(a, b)) for w, a, b in quarters
        if a is not None and b is not None and a > 0 and b > 0
    ]
    if len(parts) < 2:
        return None

    wsum = sum(w for w, _ in parts)
    return sum(w * q for w, q in parts) / wsum


def rs_quarters_used(close: pd.Series) -> int | None:
    """rs_raw_score()가 실제 사용한 분기 수(정상 4, 상장 200~252봉이면 3).
    None이면 rs_raw_score 자체가 None. 전체 유니버스 스캔 핫패스에서는 호출
    하지 않음(중복 계산) — /api/debug 등 특정 종목 진단용."""
    c = close.dropna()
    if len(c) < 200:
        return None
    n = len(c)
    count = sum(1 for days in (63, 126, 189, 252) if n >= days + 1)
    return count if count >= 2 else None


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

    # ── 2) 최근 고점이 살아있는가 — 장중 고가(h) 기준 ──
    last60_h = h.iloc[-60:].reset_index(drop=True)
    high60 = float(last60_h.max())
    bars_since_high = len(last60_h) - 1 - int(last60_h.idxmax())
    recent_high_ok = bars_since_high <= cfg["recent_high_window"]

    # ── 3) 조정폭 (눌림 깊이) — 장중 고가 기준, 시장별 상한 ──
    #    종가 기준 측정은 실제 조정을 3~6%p 과소평가함 (고점 캔들의 윗꼬리 무시).
    #    돌파일엔 전날 종가/전날까지의 고가 기준으로 평가.
    pb_max = cfg.get("pullback_max_kr" if is_kr else "pullback_max_us",
                     cfg.get("pullback_max", 0.18))
    if breakout_day:
        high60_ref = float(h.iloc[-61:-1].max())
        pullback = (high60_ref - prev_close) / high60_ref
        # ── 급등 연장 가드 (v4.53.3) ──
        # breakout_day 예외는 '얕게 눌렸다 막 출발하는' 종목을 눌림목에
        # 남기려는 것. 근데 VLO처럼 이미 한참 오른 상태에서 또 급등하면
        # '얕은 눌림'이 아니라 '연장(추격)'임. AVWAP이 extended(+8%)를
        # 넘으면 눌림목에서 제외 — 급등 당일 우뚝 솟은 봉을 눌림으로 오분류 방지.
        _av = anchored_vwap(h, lo, c, v)
        _avz = _av.get("zone")
        if _avz in ("extended", "overheated"):
            return None
    else:
        pullback = (high60 - close) / high60
    pullback_ok = pb_min <= pullback <= pb_max
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
    pivot, pivot_type, tl_break, tl_break_intraday = select_pivot(h, lo, c, close, pw, is_kr=is_kr, v=v)

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
    # v5.43: tightening(캔들 수축 VCP 보너스, 5점) 채점 반영 제거 — 전체
    # 유니버스 실측(과거 체크포인트 3600+건, 돌파임박 기준)에서 True/False간
    # 도달률·손절률 차이가 1.5%p로 오차범위 수준이라 근거 없음 확인. `tightening`
    # 변수/필드 자체는 남겨둠(카드 배지 "변동폭 축소" 표시, 강한피벗 풀
    # strength_score에서 여전히 사용) — 여기서 제거하는 건 이 score 계산의
    # 가점 반영뿐.
    score += 5 if recent_high_ok else 0
    score += 3 if (rs_mom is not None and rs_mom >= 10) else 0
    # RS 곱셈 반영: 힘(RS) × 모양 — 둘 다 좋아야 고득점
    if rs_rank is not None:
        score *= 0.7 + 0.3 * rs_rank / 99
    # ── v4.58: 베이스 품질 가감 (오닐/미너비니) ──
    # 좋은 베이스(길이 5주+, VCP 수축, 거래량 건조)엔 가점, 며칠짜리 얕은
    # 조정(MEC 케이스)엔 감점 + 배지. 곱셈 RS 반영 뒤에 더해서 순수 가감으로.
    _bq = base_quality(c, h, lo, v, pivot=pivot, is_kr=is_kr)
    score += _bq["score_adj"]
    score = max(0.0, min(score, 100.0))   # 0~100 만점 캡

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

    # ── v4.48 게이트: 리스크 기하 + 후기 스테이지 ──
    rrb = _rr_block(pivot, stop, h, lo, c,
                    base_low=float(lo.iloc[-cfg["recent_high_window"]:].min()),
                    entry=close, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    if not _risk_hard_ok(rrb, is_kr, pivot=pivot):
        return None
    _ls = late_stage_info(c, lo, h, v, is_kr)
    _ud50 = up_down_volume(c, v, 50)
    _tt = trend_grade(c, lo, h, rs_rank, ud=_ud50)
    if _ls["late_level"] == "danger" and cfg.get("late_stage_exclude", True):
        return None
    # v4.80: M&A/특수상황 의심 종목은 배지로 표시만 하던 걸 아예 스캔 결과에서 제외.
    # 추세매매 부적합(상방 막힘+하방 비대칭 리스크)이라 안 보이는 게 낫다는 요청.
    _mg = _merger_block(c, h, lo, v)
    if _mg["merger"]:
        return None

    # v5.24: 조용한 매집 스코어(Task 2) — 눌림목 탭 카드에도 노출. 여기서
    # 실패해도 눌림목 탭 전체를 죽이면 안 되므로 별도 try/except로 격리.
    try:
        _qa = quiet_accumulation_score(df, window=60)
    except Exception:
        _qa = {"score": None, "grade": None, "components": None,
               "disqualify_reason": "계산오류", "data_basis": "추정"}

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
        "qa_score": _qa["score"],
        "qa_grade": _qa["grade"],
        "qa_components": _qa["components"],
        "qa_reason": _qa["disqualify_reason"],
        **_mg,
        "pullback_pct": round(pullback * 100, 1),
        "support_ma": disp_support,
        "ma_dist_pct": disp_support_dist,
        "vol_ratio": round(vol_ratio, 2),
        "vol_dry": vol_dry,
        "rsi": round(cur_rsi, 1),
        "tightening": tightening,
        "recent_high_ok": recent_high_ok,
        # v4.61: 베이스/손절폭/약세장 배지 (공통 헬퍼 — 전 탭 일관)
        **badge_fields(c, h, lo, v, pivot, is_kr, rs_rank, rrb),
        "pivot": round(pivot, 2),
        "pivot_type": pivot_type,
        "tl_break": tl_break,
        "tl_break_intraday": tl_break_intraday,
        "ud": _ud50,
        "pivot_dist_pct": round(pivot_dist_pct, 2),
        "atr_pct": atr_pct,
        "vol_high": vol_high,
        "atr_tight": atr_tight,
        **rrb,
        "late_flags": _ls["late_flags"], "late_level": _ls["late_level"],
        "ext200_pct": _ls["ext200_pct"],
        "grade": _tt["grade"], "tt_pass": _tt["passed"], "tt_fails": _tt["fails"],
        **volume_info(close, v),
        "avwap": anchored_vwap(h, lo, c, v),
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }




# ══════════════════════════════════════════════════════
# 베이스 품질 평가 (v4.58) — 오닐/미너비니 기준
# "베이스가 종목의 성격을 말한다": 길이·깊이·수축·거래량건조로
# 좋은 베이스를 만드는 종목에 가점, 짧거나 얕은 조정엔 감점 + 배지.
# ══════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════
# 수평 저항(매물대) 감지 (v4.63)
# 스파이크 고점(한 번 윗꼬리로 찍고 만 자리)은 진짜 저항이 아니다.
# 진짜 피벗 = 가격이 '여러 번' 부딪혀 막힌 수평 매물대.
# 가격대를 잘게 나눠 고가·저가 터치 횟수를 세고, 많이 닿은 구간을 저항으로.
# ══════════════════════════════════════════════════════
def horizontal_levels(h: pd.Series, lo: pd.Series, c: pd.Series,
                      lookback: int = 120, bin_pct: float = 0.01,
                      min_touch: int = 3, above_only_from: float | None = None) -> dict:
    """수평 지지/저항 매물대 탐지.

    방법:
      - 최근 lookback 봉의 고가·저가를 bin_pct(1%) 폭 가격 빈에 누적.
      - 각 봉의 고가와 저가가 속한 빈에 '터치 1회'. (종가 아님 — 꼬리 포함
        실제로 그 가격을 건드렸는지가 매물대 형성의 핵심)
      - min_touch(3회) 이상 닿은 빈 = 유효 매물대. 인접 빈은 병합.
      - 스파이크는 1~2회만 닿으니 자동 제외된다.

    반환:
      resistances : 현재가 위 매물대 [{price, touches, dist_pct}] (가까운 순)
      supports    : 현재가 아래 매물대 (가까운 순)
      pivot       : 현재가 위 '가장 가깝고 유효한' 저항 = 추천 피벗 (없으면 None)
      pivot_touches : 그 피벗의 터치 횟수
    """
    out = {"resistances": [], "supports": [], "pivot": None, "pivot_touches": 0}
    try:
        n = len(c)
        if n < 30:
            return out
        H = h.iloc[-lookback:].values
        L = lo.iloc[-lookback:].values
        cur = float(c.iloc[-1])
        if cur <= 0:
            return out

        # 가격 빈: 현재가 기준 log가 아니라 절대 % 폭. bin 크기 = cur*bin_pct
        binsize = cur * bin_pct
        from collections import defaultdict
        touch = defaultdict(int)
        # 각 봉: 고가 빈, 저가 빈에 +1 (같은 빈이면 1회만)
        for hi, low in zip(H, L):
            bh = round(hi / binsize)
            bl = round(low / binsize)
            seen = {bh, bl}
            for b in seen:
                touch[b] += 1

        # min_touch 이상만, 인접 빈(±1) 병합
        raw = sorted([(b, t) for b, t in touch.items() if t >= min_touch])
        merged = []
        for b, t in raw:
            price = b * binsize
            if merged and abs(price - merged[-1]["price"]) <= binsize * 1.5:
                # 병합: 터치 합산, 가격은 터치 가중 평균
                m = merged[-1]
                total = m["touches"] + t
                m["price"] = (m["price"] * m["touches"] + price * t) / total
                m["touches"] = total
            else:
                merged.append({"price": price, "touches": t})

        res, sup = [], []
        for m in merged:
            price = round(m["price"])
            dist = (price - cur) / cur * 100
            entry = {"price": price, "touches": m["touches"], "dist_pct": round(dist, 1)}
            # v4.64: 위쪽 제외폭 +2% → +0.5%로 축소.
            # ±2% 대칭 제외는 '눈앞의 저항'을 지워버렸다 (나이스정통: 현재가
            # 29,900의 +2%=30,498이라 실제 저항대 30,200~30,400이 제외됨).
            # 트레이딩 대상은 바로 위 저항이므로 위쪽은 0.5%만 제외(당일 노이즈),
            # 아래쪽 지지는 -2% 유지(현재 가격대와 구분).
            if price > cur * 1.005:      # 0.5%+ 위 = 저항
                res.append(entry)
            elif price < cur * 0.98:     # 2%+ 아래 = 지지
                sup.append(entry)
        # 저항: 가까운 순(위로), 지지: 가까운 순(아래로)
        res.sort(key=lambda x: x["price"])
        sup.sort(key=lambda x: -x["price"])
        out["resistances"] = res
        out["supports"] = sup
        # 추천 피벗 = 가장 가까운 저항 (터치 많을수록 신뢰)
        if res:
            out["pivot"] = res[0]["price"]
            out["pivot_touches"] = res[0]["touches"]
        return out
    except Exception:
        return out


def badge_fields(c, h, lo, v, pivot, is_kr, rs_rank, rrb) -> dict:
    """베이스 품질 + 손절폭 + 약세장 적격 배지 필드를 한 번에 생성 (v4.61).
    analyze / analyze_imminent / analyze_breakout 등 여러 탭에서 공통 사용.
    (기존엔 analyze에만 있어 돌파임박 탭에 베이스 배지가 안 떴음.)
    rrb: _rr_block 반환 (risk_pct/risk_warn 사용). 카드 하단 '리스크 %'와 동일값.
    """
    _bq = base_quality(c, h, lo, v, pivot=pivot, is_kr=is_kr)
    risk_pct = float(rrb.get("risk_pct", 0.0))
    # v4.67: 손절폭 넓음 판정을 '고정 %'에서 'ATR 배수'로.
    # [문제] 고정 5%(US)/7%(KR)는 각 종목의 변동성을 무시했다. 미국주는 ATR이
    #        커서(ANAB 7.7%) 정상 손절폭이 이미 5%를 넘어 → 💎적격에서 미국이
    #        통째로 탈락, 국내만 남았다. 계속 빙빙 돌던 문제의 뿌리.
    # [해결] 손절폭이 그 종목 ATR의 1.5배를 넘으면 '넓음'. 즉 변동성 대비 판정.
    #        - 저변동주(ATR 2%): 손절폭 3% 넘으면 넓음 (더 빡셈)
    #        - 고변동주(ATR 7.7%): 손절폭 ~11.5%까지 허용 (ANAB 6.8% 통과)
    #        진짜 '타이트함' = 손절폭/ATR 비율. 절대%가 아님.
    # ATR이 비정상(0/NaN)이면 옛 고정% 기준으로 폴백(안전).
    try:
        _atr_abs = atr(h, lo, c)
        _cur = float(c.iloc[-1])
        _atr_pct = (_atr_abs / _cur * 100) if _cur > 0 else 0.0
    except Exception:
        _atr_pct = 0.0
    _ATR_MULT = 1.5
    if _atr_pct >= 1.0:                       # ATR 유효 → 변동성 대비 판정
        _stop_limit = _atr_pct * _ATR_MULT
    else:                                     # ATR 불량 → 고정% 폴백
        _stop_limit = 7.0 if is_kr else 5.0
    _stop_wide = bool(risk_pct > _stop_limit)
    # v5.24: U/D 신뢰도 분해(ud_volume_detail) — 단일/소수 급등일이 U/D를
    # 지배하는 왜곡 탐지(한울반도체 320000 사례). 데이터부족(하락일 또는
    # 상승일 거래량 합이 0)이면 전부 None — 방어값으로 채우지 않는다.
    _udd = ud_volume_detail(c, v, window=50)
    return {
        "ud_raw": _udd["ud_raw"] if _udd else None,
        "top1_share": _udd["top1_share"] if _udd else None,
        "top3_share": _udd["top3_share"] if _udd else None,
        "ud_ex_top1": _udd["ud_ex_top1"] if _udd else None,
        "ud_ex_top3": _udd["ud_ex_top3"] if _udd else None,
        "ud_hhi": _udd["hhi"] if _udd else None,
        "ud_n50": _udd["n50"] if _udd else None,
        "ud_reliable": _udd["ud_reliable"] if _udd else None,
        "base_badge": _bq["badge"],
        "base_badge_lv": _bq["badge_lv"],
        "base_length_wk": _bq["length_wk"],
        "base_depth_pct": _bq["depth_pct"],
        "base_vcp": _bq["vcp"],
        "base_vol_dry": _bq["vol_dry"],
        "base_discontinuity": _bq["discontinuity"],
        "base_gap_ago": _bq["gap_ago"],
        "stop_wide": _stop_wide,
        "stop_limit_pct": round(_stop_limit, 1),   # v4.67: ATR 기반 실제 한계값
        "atr_pct": round(_atr_pct, 1),
        # v4.66: bear_ok의 손절폭 조건을 stop_wide(5%/7%)와 통일.
        # 기존엔 risk_warn(8%/12%)을 써서, 7.2%짜리가 🚫손절폭넓음(5% 기준)과
        # 💎약세장적격(8% 기준)을 동시에 다는 모순이 있었음(ANAB 사례).
        # 이제 🚫 뜬 종목은 💎이 절대 안 뜬다.
        "bear_ok": bool(
            _bq["badge_lv"] == "good"
            and (rs_rank is not None and rs_rank >= 90)
            and not _stop_wide
        ),
        "bear_ok_reasons": {
            "base": _bq["badge_lv"] == "good",
            "rs90": (rs_rank is not None and rs_rank >= 90),
            "risk_tight": not _stop_wide,
        },
    }


def base_quality(c: pd.Series, h: pd.Series, lo: pd.Series, v: pd.Series,
                 pivot: float | None = None, is_kr: bool = False) -> dict:
    """현재 베이스(눌림/횡보 구간)의 품질을 평가.

    측정 요소 (전부 근거 있는 오닐/미너비니 기준):
      · length_wk : 베이스 길이(주). 오닐 최소 5주. 미만이면 '짧음'.
      · depth_pct : 베이스 깊이(고점→저점 %). 12~33% 정상, 그 이상 결함.
      · vcp       : 변동성 수축 — 베이스 전반부 대비 후반부 진폭이 줄었는가.
      · vol_dry   : 거래량 건조 — 후반부 거래량이 전반부보다 마르는가(매도 소진).

    베이스 정의: 현재가에서 거슬러 올라가며 '직전 스윙 고점'까지를 한 베이스로
    본다. 스윙 고점 = 최근 최고가 봉. 그 봉부터 현재까지가 조정/횡보 구간.

    반환:
      score_adj : 점수 가감 (-12 ~ +8)
      badge     : 대표 배지 1개 (가장 중요한 것만). '탄탄'/'짧음'/'깊이과다'/'없음'/None
      badge_lv  : 'good'|'warn'|'bad'|None
      length_wk, depth_pct, vcp, vol_dry : 원시 측정값 (툴팁/디버그용)
    """
    out = {"score_adj": 0.0, "badge": None, "badge_lv": None,
           "length_wk": None, "depth_pct": None, "vcp": False, "vol_dry": False,
           "discontinuity": False, "gap_ago": None}
    try:
        n = len(c)
        if n < 25:
            return out

        # ── 데이터 불연속(가격 갭) 감지 — 스핀오프/병합/액면변경/재상장 ──
        # NVRI 케이스: yfinance가 상폐된 구 회사 + 재상장된 신 회사를 같은
        # 티커로 이어붙여 210봉을 채웠다. 티커는 같아도 6/1 이전은 다른 회사.
        # 이런 코퍼레이트 액션은 하루 새 극단적 갭(±25%+)을 남긴다. 그 지점
        # 이전 데이터는 '다른 회사'이므로 RS·베이스·이평이 전부 오염된다.
        # 최근 130봉(약 6개월) 내에 그런 갭이 있으면 데이터 신뢰 불가로 본다.
        try:
            recent = c.iloc[-130:] if n >= 130 else c
            rr = recent.pct_change().abs()
            gap_hits = rr[rr >= 0.25]           # 일간 ±25%+ = 코퍼레이트 액션 의심
            if len(gap_hits) > 0:
                # 가장 최근 갭이 끝에서 몇 봉 뒤인지
                last_gap_pos = list(recent.index).index(gap_hits.index[-1])
                out["gap_ago"] = len(recent) - 1 - last_gap_pos
                out["discontinuity"] = True
        except Exception:
            pass

        # ── 베이스 시작점 = 최근 스윙 고점 봉 ──
        # 최근 60봉 내 최고가 봉을 베이스 천장으로 본다. 단 오늘·어제(신고가
        # 갱신 중)는 제외해 '눌림 없이 계속 오르는 중'을 베이스로 오인하지 않게.
        win = min(60, n - 2)
        seg_h = h.iloc[-win:-1] if win >= 3 else h.iloc[-win:]
        peak_local = int(seg_h.reset_index(drop=True).idxmax())
        # 전체 인덱스로 환산 (끝에서 몇 번째 뒤)
        peak_back = (len(seg_h) - peak_local)   # 스윙 고점이 끝에서 몇 봉 뒤
        base_bars = peak_back                    # 고점부터 현재까지 봉 수

        # ── 길이 (거래일 → 주). 한국이든 미국이든 주5일 기준 ──
        length_wk = round(base_bars / 5.0, 1)
        out["length_wk"] = length_wk

        # ── 깊이: 베이스 천장 → 이후 최저 저가 ──
        base_top = float(h.iloc[-base_bars-1:].max()) if base_bars + 1 <= n else float(h.iloc[-base_bars:].max())
        base_bot = float(lo.iloc[-base_bars:].min()) if base_bars >= 1 else float(lo.iloc[-1])
        depth_pct = (base_top - base_bot) / base_top * 100 if base_top > 0 else 0.0
        out["depth_pct"] = round(depth_pct, 1)

        # ── VCP: 베이스 전반부 vs 후반부 봉 진폭(고-저) 평균 비교 ──
        vcp = False
        if base_bars >= 8:
            rng = (h.iloc[-base_bars:] - lo.iloc[-base_bars:]).abs()
            half = base_bars // 2
            early = float(rng.iloc[:half].mean())
            late = float(rng.iloc[half:].mean())
            if early > 0:
                vcp = late < early * 0.75      # 후반 진폭이 전반의 75% 미만 = 수축
        out["vcp"] = vcp

        # ── 거래량 건조: 후반부 평균 거래량이 전반부보다 낮은가 ──
        vol_dry = False
        if base_bars >= 8 and v is not None:
            half = base_bars // 2
            ve = float(v.iloc[-base_bars:-base_bars+half].mean()) if half > 0 else 0.0
            vl = float(v.iloc[-half:].mean()) if half > 0 else 0.0
            if ve > 0:
                vol_dry = vl < ve * 0.85       # 후반 거래량이 전반의 85% 미만
        out["vol_dry"] = vol_dry

        # ── 점수 가감 + 배지 (가장 문제/강점 하나만 노출) ──
        # 우선순위: 데이터불연속(bad) > 없음(bad) > 짧음(warn) > 깊이과다(warn) > 탄탄(good)
        MIN_WK = 5.0                    # 오닐 최소 베이스 = 5주
        adj = 0.0
        badge, lv = None, None

        if out["discontinuity"]:
            # 스핀오프/병합/재상장 등으로 가격이 튄 종목 = 데이터 오염.
            # RS·베이스·이평 전부 다른 회사 데이터 섞여 신뢰 불가.
            # 갭이 최근일수록 위험(신생주 구간), 오래됐으면 경고만.
            ga = out["gap_ago"]
            wk = round(ga / 5.0, 1) if ga is not None else None
            if ga is not None and ga <= 65:      # 13주 이내 갭 = 데이터의 절반이 다른 회사
                adj = -20.0
                badge = f"데이터불연속 {wk}주전"
                lv = "bad"
            else:                                 # 오래된 갭 = 조정됐을 수도, 경고만
                adj = -6.0
                badge = f"과거불연속 {wk}주전" if wk else "과거불연속"
                lv = "warn"
        elif length_wk < 1.0:
            # 며칠짜리 = 베이스라 부를 수 없음
            adj = -12.0
            badge, lv = "베이스없음", "bad"
        elif length_wk < MIN_WK:
            adj = -6.0
            badge, lv = f"베이스짧음 {length_wk}주", "warn"
        elif depth_pct > 35.0:
            # 너무 깊은 조정 = 손상된 베이스
            adj = -5.0
            badge, lv = f"깊이과다 {int(depth_pct)}%", "warn"
        else:
            # 길이 충족 → 품질 요소로 가점
            good = 0
            if vcp: good += 1
            if vol_dry: good += 1
            if 5.0 <= depth_pct <= 33.0: good += 1     # 정상 깊이 (얕은 5~12%도 강세 신호)
            if length_wk >= 7.0: good += 1              # 넉넉한 길이
            adj = 2.0 * good                            # 최대 +8
            if good >= 3:
                badge, lv = "탄탄한베이스", "good"
            elif good >= 1:
                badge, lv = "베이스양호", "good"
            else:
                # 5주+이지만 품질요소 0 = 평범한 베이스. 그래도 길이는 보여준다
                # (배지가 아예 안 뜨면 "기능이 안 되나?" 오해를 주므로 항상 표시)
                badge, lv = f"베이스 {length_wk}주", "neutral"

        out["score_adj"] = adj
        out["badge"] = badge
        out["badge_lv"] = lv
        return out
    except Exception:
        return out


# ══════════════════════════════════════════════════════
# 베이스 카운팅: "추세 전환 후 첫 번째 베이스"인지 판별
# (O'Neil base-count: 1·2차는 확률↑, 3·4차는 실패율↑)
# ══════════════════════════════════════════════════════
def count_bases_since_bottom(c, lo, h,
                             low_lookback: int = 250,
                             recent_bottom_max: int = 200,
                             correction_min: float = 0.18):
    """52주 신저가(바닥) 이후 형성된 '베이스(의미있는 조정)' 개수를 센다.
    반환: {bottom_ago, bottom_recent, corrections, is_first_base}

    - bottom_ago: 최저점이 몇 봉 전인가
    - bottom_recent: 최저점이 recent_bottom_max(기본 126봉≈6개월) 이내인가
    - corrections: 바닥 이후 '15%+ 하락 후 반등' 횟수 (베이스 카운트 근사)
    - is_first_base: (바닥 최근) AND (조정 1회 이하) → 1차 베이스 후보

    조정 카운트 방식: 바닥 이후 구간에서 직전 고점 대비 correction_min(15%)
    이상 하락했다가 다시 그 고점을 회복(또는 신고가)하면 '베이스 1개 완성'으로 간주.
    러닝 피크를 추적하며, 피크에서 15%+ 빠진 골을 만든 뒤 새 피크가 나오면 +1."""
    import math as _m
    n = len(c)
    if n < 60:
        return {"bottom_ago": 0, "bottom_recent": False,
                "corrections": 99, "is_first_base": False}

    win = min(low_lookback, n)
    closes = [float(x) for x in c.iloc[-win:].tolist()]
    lows = [float(x) for x in lo.iloc[-win:].tolist()]

    # 1) 최저점 위치 (저가 기준)
    bottom_idx = min(range(len(lows)), key=lambda i: lows[i])
    bottom_ago = len(lows) - 1 - bottom_idx
    bottom_recent = bottom_ago <= recent_bottom_max

    # 1-b) '진짜 바닥' 검증: 바닥 이전에 의미있는 하락이 있었는가.
    # 장기 상승 종목(URI 등)이 잠깐 눌린 저점을 '바닥'으로 오인하는 것 방지.
    # 바닥 시점 저가가 그 이전 구간 최고가 대비 prior_drop_min(25%)+ 낮아야 진짜 바닥.
    prior_drop_min = 0.25
    bottom_low = lows[bottom_idx]
    pre_seg = closes[:bottom_idx] if bottom_idx > 0 else []
    if pre_seg:
        pre_peak = max(pre_seg)
        prior_drop = (pre_peak - bottom_low) / pre_peak if pre_peak > 0 else 0.0
        real_bottom = prior_drop >= prior_drop_min   # 바닥 전 25%+ 하락 = 진짜 역배열 바닥
    else:
        # 바닥이 데이터 맨 앞 = 그 이전 하락을 못 봄 → 보수적으로 진짜 바닥 아님 처리
        real_bottom = False

    # 2) 바닥 이후 구간에서 조정(베이스) 카운트
    seg = closes[bottom_idx:]
    corrections = 0
    if len(seg) >= 3:
        peak = seg[0]
        in_correction = False
        trough = peak
        for px in seg[1:]:
            if px > peak:
                # 새 고점 회복 → 직전에 의미있는 조정이 있었으면 베이스 1개 완성
                if in_correction and peak > 0 and (peak - trough) / peak >= correction_min:
                    corrections += 1
                peak = px
                trough = px
                in_correction = False
            else:
                if px < trough:
                    trough = px
                if peak > 0 and (peak - px) / peak >= correction_min:
                    in_correction = True

    is_first_base = bottom_recent and corrections <= 1 and real_bottom
    return {
        "bottom_ago": bottom_ago,
        "bottom_recent": bottom_recent,
        "corrections": corrections,
        "real_bottom": real_bottom,
        "is_first_base": is_first_base,
    }


# ══════════════════════════════════════════════════════
# 추세 전환 스캔: 역배열 → 정배열 첫 형성 (최근 1개월 내)
# ══════════════════════════════════════════════════════
TURN_CONFIG = {
    "min_bars": 210,
    "align_window": 40,      # 정배열 형성이 최근 N봉 이내 (22→40, 너무 빡빡했음)
    "max_ma200_dist": 0.35,  # 200일선 거리 한계 (25→35%, 약세장에선 여유 필요)
    "rs_min": 70,            # RS 최소 (80→70, 전환 초기는 RS가 아직 낮을 수 있음)
    # ── 1→2단계 첫 돌파 신호 ──
    "ma200_slope_lookback": 20,   # 200일선 기울기 판정 구간(봉)
    "ma200_rising_min": -0.03,    # 200일선 기울기 (0→-3%, 바닥 평탄~막 드는 구간 허용)
    "breakout_vol_mult": 1.5,     # 돌파일 거래량이 50일 평균의 N배↑ = 진짜 돌파
    # ── 베이스 카운팅: 추세전환 후 '첫 번째 베이스'만 통과 (핵심, 유지) ──
    "first_base_only": True,      # True면 1차 베이스가 아닌 종목 제외
    "low_lookback": 250,          # 신저가(바닥) 탐색 구간(봉, ≈52주)
    "recent_bottom_max": 200,     # 바닥 최근성 (126→200봉≈10개월, 너무 빡빡했음)
    "correction_min": 0.18,       # 베이스 1개로 칠 최소 조정폭 (15→18%, 작은 출렁임은 베이스로 안 셈)
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

    # ── 베이스 카운팅: 추세 전환 후 '첫 번째 베이스'인지 판별 ──
    # 신저가 최근성 + 조정 1회 이하 = 1차 베이스 (3·4차 late-stage 제외)
    base_info = count_bases_since_bottom(
        c, lo, h,
        low_lookback=cfg["low_lookback"],
        recent_bottom_max=cfg["recent_bottom_max"],
        correction_min=cfg["correction_min"],
    )
    if cfg.get("first_base_only", True) and not base_info["is_first_base"]:
        return None  # 1차 베이스가 아니면(2·3·4차) 제외

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
    pivot, pivot_type, tl_break, tl_break_intraday = select_pivot(h, lo, c, close, 20, is_kr=is_kr, v=v)
    ud = up_down_volume(c, v, 50)
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
    # 바닥 신선도: 최근에 바닥 친 1차 베이스일수록 가점 (전환 초기 = 확률↑)
    bot_ago = base_info["bottom_ago"]
    score += 10 * max(0.0, 1 - bot_ago / cfg["recent_bottom_max"])             # 바닥 최근성
    if base_info["corrections"] == 0:
        score += 5                                                             # 조정 0회(가장 이른 첫 베이스) 보너스
    # v5.57: U/D의 점수 가감·ud_weak 경고 플래그 제거 — 실측(2R 레이스 EV)
    # 결과가 정반대였음(눌림목 0.33R vs 0.14R, 돌파임박 0.33R vs 0.23R,
    # U/D<1 그룹이 오히려 더 좋음). "매집 미확증=경계" 프레이밍이 근거 없이
    # 반대 방향이라 판정(점수·플래그)에서 완전히 빼고 ud 원값만 참고로 남김
    # (docs/all_tabs_common_yardstick_investigation.md 참고).
    # 배점 총합이 115(25+20+10+10+10+15+10+10+5, U/D 10점 제외)라 100점 만점으로 정규화
    score = max(0.0, min(100.0, score * (100.0 / 115.0)))

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
        "base_count": base_info["corrections"] + 1,   # 현재 진행 중인 베이스 차수(1차=1)
        "bottom_ago": base_info["bottom_ago"],
        "is_first_base": base_info["is_first_base"],
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

    # ── v4.49: 절대 모멘텀 게이트 — 3개월 +30% 미만이면 주도주 아님 ──
    # (폭락장에서 "덜 빠져서 RS 높은" 가짜 주도주 차단. 조건 미달로 탭이
    #  비면 그게 "지금 주도주가 없다"는 팩트임)
    _mom = mom_3m(c)
    if _mom is None or _mom < CONFIG.get("leader_mom_3m_min", 0.30):
        return None

    # 점수 = RS 중심 (대장 후보는 강함이 전부)
    score = 0.0
    score += 60 * rs_rank / 99
    score += 20 * (1 - min(dist_from_high / cfg["near_high"], 1))   # 신고가 밀착
    score += 10 if at_new_high else 0
    if rs_mom is not None:
        score += 10 * max(0.0, min(rs_mom, 30)) / 30

    return {
        "mode": "leader",
        "mom_3m_pct": round(_mom * 100, 1),
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
                  rs_mom: int | None = None, is_kr: bool = False,
                  cfg: dict = SUPER_CONFIG) -> dict | None:
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

    # ── v4.49: 절대 모멘텀 게이트 — 슈퍼대장은 절반 기준 ──
    # 대장후보(발굴)는 30% 하드지만, 이 탭은 이미 검증된 주도주의 담을곳 추적이라
    # 3개월 횡보 베이스 중이면 3개월 수익률이 낮아지는 게 정상. 15%로 완화.
    _mom = mom_3m(c)
    if _mom is None or _mom < CONFIG.get("leader_mom_3m_min", 0.30) / 2:
        return None

    score = round(60 * rs_rank / 99 + 20 * (1 - min(dist_from_high / 0.15, 1))
                  + (10 if at_new_high else 0)
                  + (10 * max(0.0, min(rs_mom or 0, 30)) / 30), 1)

    # v5.55: 진입 좌표 — buy_zone 대기 안(안1)이 검증 실패했음이 확인됨
    # (docs/all_tabs_common_yardstick_investigation.md Script F — 60봉 내
    # 미터치 27.9%가 오히려 median +75.4%로 더 강해서, 대기=최고 수익 기회를
    # 놓치는 구조). 대신 즉시 진입(entry=현재가) + ATR×2 손절로 재측정한
    # 4개 손절 안(20일선-2%/50일선-2%/ATR×2/significant_support) 중
    # ATR×2가 EV(0.641)와 손절폭(median 10.3%, 다른 3안의 절반 수준) 균형이
    # 가장 실전적이라 채택. `_rr_block`으로 카드용 stop/risk_pct/rr 통일.
    rr = _rr_block(close, close - atr(h, lo, c, 14) * 2, h, lo, c,
                   entry=close, is_kr=is_kr)

    return {
        "mode": "super",
        "mom_3m_pct": round(_mom * 100, 1),
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
        **rr,
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
    "max_off_high": 25,      # 1년 고점 대비 -25% 넘게 빠진 종목 제외
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

    # 고점 대비 낙폭 필터 — 무너진 종목의 가짜 돌파 차단 (예: 고점 -50%)
    if off_high_pct(c) < -cfg["max_off_high"]:
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

    # ── v4.48 게이트: 리스크 기하 + 후기 스테이지 ──
    rrb = _rr_block(pivot, stop, h, lo, c, base_low=base_low,
                    entry=close, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    # v5.41: pivot 인자 제거 — boxbreak와 같은 이유(이미 돌파 상태라 실제
    # 진입은 현재가). extended_max(12%)가 이미 연장을 제한해 영향은 작지만,
    # 게이트/카드 기준을 일치시켜 두 값이 다르게 나오는 사례 자체를 없앰.
    if not _risk_hard_ok(rrb, is_kr):
        return None
    _ls = late_stage_info(c, lo, h, v, is_kr)
    _tt = trend_grade(c, lo, h, rs_rank, ud=up_down_volume(c, v, 50))
    if _ls["late_level"] == "danger" and CONFIG.get("late_stage_exclude", True):
        return None
    # v4.80: M&A/특수상황 의심 종목은 스캔 결과에서 제외 (배지 표시만 하지 않음).
    _mg = _merger_block(c, h, lo, v)
    if _mg["merger"]:
        return None

    return {
        "mode": "breakout",
        **badge_fields(c, h, lo, v, pivot, is_kr, rs_rank, rrb),
        "late_flags": _ls["late_flags"], "late_level": _ls["late_level"],
        "ext200_pct": _ls["ext200_pct"],
        "grade": _tt["grade"], "tt_pass": _tt["passed"], "tt_fails": _tt["fails"],
        **_mg,
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
        **rrb,   # 이미 돌파 → 현재가 진입 기준
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
    "max_off_high": 25,      # 1년 고점 대비 -25% 넘게 빠진 종목 제외
    "box_windows": [20, 40, 60],   # 짧/중/장 박스 동시 확인
    "box_max_range": 0.30,   # 박스 고저폭 ≤30% (국장 변동성 고려, 너무 넓으면 박스 아님)
    "vol_mult": 1.5,         # 돌파일 거래량 ≥ 평균 1.5배 (박스돌파의 핵심)
    "ma_long": 120,          # 장기선(120일) 위 — "장기선 위 박스탈출은 크게 간다"
    # v5.41: breakout에 있던 extended_max가 boxbreak엔 없어서, 박스 상단
    # 대비 손절은 여전히 타이트(피벗 기준)한데 실제로는 이미 크게 연장된
    # 추격 진입(051160.KQ +35.5%)이 하드게이트를 그냥 통과하던 문제. 측정
    # (오늘 히트 24건 중 5건이 12%+ 연장) 후 breakout과 같은 0.12로 채택.
    "extended_max": 0.12,    # 박스 상단 +12% 넘으면 너무 연장 → 제외 (breakout과 동일 기준)
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

    # 고점 대비 낙폭 필터 — 무너진 종목의 가짜 박스돌파 차단 (예: 고점 -50%).
    # -25%까진 허용하므로 정상적인 깊은 박스/컵은 통과, BLDP류만 제외.
    if off_high_pct(c) < -cfg["max_off_high"]:
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
        if ext > cfg["extended_max"]:
            continue                          # 너무 연장됨(+12% 초과) → 추격 금지, 이 박스는 후보 제외
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

    # ── v4.48 게이트: 리스크 기하 + 후기 스테이지 ──
    rrb = _rr_block(pivot, stop, h, lo, c, base_low=best["box_low"],
                    entry=close, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    # v5.41: pivot 인자 제거 — 이미 돌파한 상태라 "실제 진입은 현재가"라는
    # 위 주석과 일치시킴. pivot을 안 넘기면 _risk_hard_ok가 rrb["risk_pct"]
    # (entry=close 기준, 카드 표시값과 동일)를 그대로 씀. 예전엔 pivot 기준으로
    # 판정해 카드엔 29% 뜨는데 게이트는 3.79%로 통과시키는 괴리가 있었음
    # (051160.KQ 사례) — extended_max 게이트 추가로 애초에 그 정도 연장은
    # 후보에서 빠지지만, 이 통일 자체도 별도로 필요한 수정.
    if not _risk_hard_ok(rrb, is_kr):
        return None
    _ls = late_stage_info(c, lo, h, v, is_kr)
    _tt = trend_grade(c, lo, h, rs_rank, ud=up_down_volume(c, v, 50))
    if _ls["late_level"] == "danger" and CONFIG.get("late_stage_exclude", True):
        return None
    # v4.80: M&A/특수상황 의심 종목은 스캔 결과에서 제외.
    _mg = _merger_block(c, h, lo, v)
    if _mg["merger"]:
        return None

    return {
        "mode": "boxbreak",
        "late_flags": _ls["late_flags"], "late_level": _ls["late_level"],
        "ext200_pct": _ls["ext200_pct"],
        "grade": _tt["grade"], "tt_pass": _tt["passed"], "tt_fails": _tt["fails"],
        **_mg,
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
        **rrb,   # 이미 돌파 → 현재가 진입 기준
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
    "max_off_high": 25,      # 1년 고점 대비 -25% 넘게 빠진 종목 제외(무너진 종목의 가짜 돌파 차단)
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

    # ── 1-b) 고점 대비 낙폭 필터 ── 신고가 근처여야 '돌파임박'. 무너진 종목
    #         (예: 고점 -50%)의 단기저항을 피벗으로 오인하는 가짜 돌파 차단.
    if off_high_pct(c) < -cfg["max_off_high"]:
        return None

    # ── 2) 피벗 근접 (천장 코앞이지만 아직 안 뚫음) ──
    pivot, pivot_type, tl_break, tl_break_intraday = select_pivot(h, lo, c, close, cfg["pivot_window"], is_kr=is_kr, use_near=True, v=v)
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
    # 피벗 근접도 35 (가까울수록↑) + 거래량(연속식) 20 + RS 15 + 200일선위 10
    # v5.43: 거래량수축(vol_dry) 20점 절벽 제거 — 항상 연속식(vol_ratio 기반)
    # 사용. VCP(tightening) 20점 가점도 제거. 둘 다 전체 유니버스 실측(과거
    # 체크포인트 3600+건)에서 실증 근거 없음 확인:
    #  - vol_dry(수축) True일 때 이후 10봉 내 실제 거래량동반돌파 비율 39.6%,
    #    False일 때 46.7% — 오히려 역방향(수축이 폭증 돌파를 예고 못 함).
    #  - tightening True/False간 도달률·손절률 차이 1.5%p로 오차범위 수준.
    #  둘 다 "VCP(압축 후 확장)"라는 같은 전제를 코드화한 것인데 실증이 뒷받침
    #  안 함. vol_dry는 연속값(vol_ratio) 정보 자체는 남기고 절벽만 없앰(정보
    #  손실 최소화하며 top30 영향도 가장 적은 방식으로 측정 후 선택).
    #  tightening_used와 vol_dry 필드 자체는 남겨둠(카드 배지·강한피벗 풀
    #  strength_score에서 계속 사용) — 여기서 빠지는 건 이 score 반영뿐.
    near_score = 35 * (1 - min(abs(near) / 0.05, 1.0))   # 0%면 35, -5%면 0
    score = (
        near_score
        + 20 * max(0.0, min((1.1 - vol_ratio) / 0.5, 1.0))
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

    # ── v4.48 게이트: 리스크 기하 + 후기 스테이지 ──
    rrb = _rr_block(pivot, stop, h, lo, c,
                    base_low=float(lo.iloc[-cfg["pivot_window"]:].min()),
                    entry=None, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    if not _risk_hard_ok(rrb, is_kr, pivot=pivot):
        return None
    _ls = late_stage_info(c, lo, h, v, is_kr)
    _tt = trend_grade(c, lo, h, rs_rank, ud=up_down_volume(c, v, 50))
    if _ls["late_level"] == "danger" and CONFIG.get("late_stage_exclude", True):
        return None
    # v4.80: M&A/특수상황 의심 종목은 스캔 결과에서 제외.
    _mg = _merger_block(c, h, lo, v)
    if _mg["merger"]:
        return None

    return {
        "mode": "imminent",
        **badge_fields(c, h, lo, v, pivot, is_kr, rs_rank, rrb),
        **_mg,
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
        # v5.44: 오늘 고가 — "확인 후 진입"(안C 조사, docs/imminent_stop_
        # entry_investigation.md 3.6) 배지용. 관찰 등록 시점의 트리거가로
        # 프론트에서 그대로 스냅샷.
        "signal_high": round(float(h.iloc[-1]), 2),
        "touch_count": touch_count,
        "vol_ratio": round(vol_ratio, 2),
        "ud_vol": up_down_volume(c, v, 50),
        "vol_dry": vol_dry,
        "tightening": tightening,
        "rsi": round(cur_rsi, 1),
        **rrb,
        "late_flags": _ls["late_flags"], "late_level": _ls["late_level"],
        "ext200_pct": _ls["ext200_pct"],
        "grade": _tt["grade"], "tt_pass": _tt["passed"], "tt_fails": _tt["fails"],
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


def inverse_score(aligned: bool, above_ma20: bool, ma20_slope_up: bool,
                   ret5_pct: float, vol_mult: float, overheated: bool) -> int:
    """인버스 강도 점수(0~100) — "지금 인버스 살 만한 국면인가" 종합 점수.
    v4.78: UI에 "강도 점수(0~100)"라고 문구는 있었는데 실제 계산이 없어서
    카드에 항상 '–'만 뜨던 버그. 구조(정배열/20일선/기울기) + 5일 모멘텀 +
    거래량 확인을 합산, 과열(RSI)이면 되돌림 위험으로 감점."""
    score = 40.0 if aligned else (20.0 if above_ma20 else 0.0)
    score += 15.0 if ma20_slope_up else 0.0
    score += max(0.0, min(ret5_pct, 20.0)) / 20.0 * 25.0   # 5일 +20%↑에서 만점
    score += max(0.0, min(vol_mult, 3.0)) / 3.0 * 20.0     # 평균 대비 3배↑에서 만점
    if overheated:
        score -= 15.0
    return int(round(max(0.0, min(100.0, score))))


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
    ma200 = c.rolling(200).mean()  # v5.32: 자기 클램프 제거 — len<200이면 NaN이 그대로
    r = rsi(c)                     # 나와야 아래 aligned의 isnan(m200) 폴백이 발동한다

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
    vol50 = float(v.rolling(50).mean().iloc[-1])  # min_bars=60>=50이라 클램프 불필요(무해했지만 정리)
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
    inv_score = inverse_score(aligned, above_ma20, ma20_slope, ret5, vol_mult, overheated)

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
        "inv_score": inv_score,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# 🩸붕괴 — Stage 4 숏 셋업 (돌파임박/눌림목의 거울상)
# 하락 추세(역배열)에서 지지선을 거래량 동반 이탈하는 종목 포착.
# ══════════════════════════════════════════════════════
BREAKDOWN_CONFIG = {
    "min_bars": 210,
    "rs_max": 40,            # 약세 종목만 (RS 낮을수록 후보) — 주도주 반대
    "near_min": -0.05,       # 지지선 대비 현재가: -5%~+3% (이탈 직전~막 이탈)
    "near_max": 0.03,
    "pivot_window": 20,
    "vol_expand": 1.3,       # 이탈 시 거래량 확장 배수(가점)
}


def analyze_breakdown(df: pd.DataFrame, rs_rank: int | None = None,
                      rs_mom: int | None = None, cfg: dict = BREAKDOWN_CONFIG,
                      is_kr: bool = False) -> dict | None:
    """Stage 4 숏 셋업 — 돌파임박의 거울상.
    하락 추세(역배열: ma20<ma60, 200일선 아래) + 지지선 코앞/막 이탈 +
    거래량 확장이면 후보. 숏 진입은 지지 이탈, 손절은 위(직전 반등 고점)."""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
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

    # ── 1) 하락 추세 (역배열) ── 정배열의 거울상
    if close > m200:
        return None            # 200일선 위면 Stage 4 아님
    if not (m20 < m60):
        return None            # 단기선이 중기선 위면 하락추세 아님

    # ── 2) 지지선 근접/이탈 ── (significant_support 활용)
    support = significant_support(lo, cfg["pivot_window"], min_touches=2, band=0.02, exclude=1)
    if support is None or support <= 0:
        support = float(lo.iloc[-cfg["pivot_window"]:].min())
    near = (close - support) / support if support > 0 else 1.0   # 음수면 지지 아래(이탈)
    if not (cfg["near_min"] <= near <= cfg["near_max"]):
        return None            # 지지 코앞(-5%)~막 이탈(+3%) 밖이면 탈락

    # ── 3) 거래량 확장 (이탈 신뢰도) ──
    vol3 = float(v.iloc[-3:].mean())
    vol20 = float(v.iloc[-20:].mean())
    vol_ratio = vol3 / vol20 if vol20 > 0 else 0.0
    vol_expand = vol_ratio >= cfg["vol_expand"]

    # ── 숏 진입/손절/목표 ──
    entry = support                       # 지지 이탈 시 숏 진입
    # 손절은 위: 직전 반등 고점(최근 pivot_window봉 고가) + ATR 버퍼
    swing_high = float(h.iloc[-cfg["pivot_window"]:].max())
    stop, stop_struct, atr_buf = apply_atr_buffer(swing_high, h, lo, c, 0.15)
    if stop <= entry:
        stop = entry * 1.06               # 폴백: 진입 +6%
    # 목표: 다음 하방 지지 — 1년 저점 또는 진입 -2R
    risk = stop - entry
    target = entry - 2 * risk             # 2R 목표(아래)
    year_low = float(lo.iloc[-252:].min()) if len(lo) >= 252 else float(lo.min())
    target = max(target, year_low)        # 1년 저점 밑으론 안 잡음
    rr = round((entry - target) / risk, 2) if risk > 0 else None

    near_pct = round(near * 100, 2)       # 지지대비 %
    triggered = near <= 0.0 and vol_expand  # 이미 이탈 + 거래량 = 발동
    oversold = cur_rsi <= 30              # 과매도 → 숏 스퀴즈 경고

    # ── 점수 (100점) ── 지지 근접·이탈 35 + 거래량확장 20 + 역배열강도 20 +
    #                     RS약세 15(낮을수록↑) + 200일선아래 10
    near_score = 35 * (1 - min(abs(near) / 0.05, 1.0))
    align_gap = (m60 - m20) / m60 if m60 > 0 else 0.0     # 역배열 벌어짐
    align_score = 20 * min(align_gap / 0.05, 1.0)
    rs_score = 15 * (1 - (rs_rank or 50) / 99) if rs_rank is not None else 7.5
    score = near_score + (20 if vol_expand else 0) + align_score + rs_score + 10
    score = min(max(score, 0.0), 100.0)

    reasons = []
    if near <= 0:
        reasons.append("지지이탈")
    else:
        reasons.append("지지임박")
    if vol_expand:
        reasons.append(f"거래량{round(vol_ratio,1)}배")
    if m20 < m60 < m200:
        reasons.append("완전역배열")
    if cur_rsi <= 40:
        reasons.append("약세모멘텀")

    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0

    # v4.80: M&A/특수상황 의심 종목은 스캔 결과에서 제외. 숏(붕괴) 탭도 마찬가지 —
    # 딜 완주 시 갭업으로 숏도 위험한 비대칭 리스크라 여기도 배제 대상.
    _mg = _merger_block(c, h, lo, v)
    if _mg["merger"]:
        return None

    return {
        "mode": "breakdown",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "triggered": triggered,
        "setup_score": None,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "support": round(support, 2),
        "near_pct": near_pct,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "rr": rr,
        "reasons": reasons,
        "oversold": oversold,
        "rsi": round(cur_rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        **_mg,
        **volume_info(close, v),
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ],
    }


# ════════════════════════════════════════════════════════════════
# 패턴 탐지 (v4.44.0) — 컵앤핸들 / 더블바닥 / 치솟은깃발
# 돌파임박(위치 신호)과 달리 몇 주~몇 달의 '형태'를 인식.
# 패턴이 거의 완성돼 피벗 근처(-6%~+1%)인 종목만 반환.
# ════════════════════════════════════════════════════════════════
PATTERN_CONFIG = {
    "min_bars": 130,
    "near_lo": -6.0,   # 피벗까지 최대 -6% (거의 완성)
    "near_hi": 1.5,    # 피벗 +1.5%까지 (막 돌파 포함)
}


# ══════════════════════════════════════════════════════
# A-B-C 매집 스코어 (v5.19) — 급등매집 A구간(횡보 베이스)의 "조용한 물량
# 수집" 흔적을 수치화. 순위 정렬용 보조 지표일 뿐, 진입 신호도 상한가
# 확률도 아님. 승률/확률 문구 절대 사용 금지.
#
# 가중치·정규화 구간·임계값은 전부 검증된 값이 아니라 사전 추측 —
# 모듈 상단 상수로 빼서 튜닝 가능하게 함(코드 중간 매직넘버 금지).
# ══════════════════════════════════════════════════════
ACCUM_WEIGHTS = {
    "vol_ratio":         0.22,
    "atr_compress":      0.22,
    "close_strength":    0.18,
    "ud_vol":            0.10,
    "vol_pickup":        0.08,
    "range_tight":       0.08,
    "trade_value_ratio": 0.12,
}
# 정규화 구간 (lo, hi) — _norm()에 그대로 사용. lo=0점 경계, hi=1점 경계.
# lo > hi로 주면 "낮을수록 강함" 지표를 자동으로 역방향 정규화함
# (atr_compress·range_tight가 이 경우).
ACCUM_NORM_RANGES = {
    "vol_ratio":         (1.0, 2.0),
    "atr_compress":      (1.2, 0.5),    # 역방향: 1.2(약함)→0, 0.5(강함)→1
    "close_strength":    (0.3, 0.8),
    "ud_vol":            (0.8, 1.6),
    "vol_pickup":        (0.9, 1.6),
    "range_tight":       (0.25, 0.10),  # 역방향: 0.25(ABC게이트 상한, 약함)→0, 0.10(강함)→1
    # v5.30: peak 정의를 가격(종가) 기준→거래대금 기준으로 바꾸면서 raw
    # 분포가 구조적으로 내려감(중앙값 0.4~0.5대) — (0.4,1.0)을 그대로 쓰면
    # 중간 변별구간이 줄고 하한(norm=0) 쏠림이 악화됨. KR 유니버스를 시차를
    # 두고 두 번 실측(863종목/896종목, 살아있는 시세라 한 스냅샷만 믿으면
    # 안 됨 — p20/p80이 스냅샷마다 (0.11,0.71)~(0.16,1.03)으로 흔들림을
    # 확인)한 뒤 두 표본을 합쳐(n=1759) p20/p80=(0.130, 0.864)으로 재산정,
    # 반올림해서 (0.13, 0.86) — 두 스냅샷 각각에 다시 적용해도 중간구간
    # 56.5%~63.3%로 안정적.
    "trade_value_ratio": (0.13, 0.86),  # 0.86 = 자금고점(11봉 롤링평균 최대)과 동급
}
# "강함" 판정 임계값 (accum_parts의 pass 플래그용) — 스펙 표 그대로.
ACCUM_PASS_THRESHOLDS = {
    "vol_ratio":         ("ge", 1.3),
    "atr_compress":      ("le", 0.8),
    "close_strength":    ("ge", 0.55),
    "ud_vol":            ("ge", 1.1),
    "vol_pickup":        ("ge", 1.15),
    "range_tight":       ("le", 0.15),
    # v5.30: norm 구간과 같은 이유로 재산정 — 기존 0.8이 옛 lo/hi(0.4,1.0)
    # 구간에서 차지하던 상대위치(66.7% 지점)를 새 구간(0.13,0.86)에 그대로
    # 적용: 0.13 + 0.667*(0.86-0.13) ≈ 0.62. 두 스냅샷 각각 실측 통과율
    # 25.1%/39.0% (기존 가격peak 기준 0.8의 통과율 28.7%와 비슷한 범위).
    "trade_value_ratio": ("ge", 0.62),
}
ACCUM_SYNERGY_BONUS = 10   # vol_ratio>=1.3 AND atr_compress<=0.8 동시 성립 시 가산
ACCUM_BADGE_MIN = 65       # 🧲 배지 표시 하한
ACCUM_MIN_A_BARS = 15
ACCUM_MIN_PRIOR_BARS = 40
ACCUM_MAX_BAD_VOL_BARS = 3   # A구간 내 거래량 0/NaN 허용 상한(이상이면 미달)
TRADE_VALUE_LOOKBACK_BARS = 126   # 자금고점 탐색 범위 ≈ 6개월(거래일 기준) — a_start 이전만 본다
# v5.30: 자금고점(fund peak) 탐색 창 크기(봉수). v5.27까지는 "가격 종가가
# 최고인 날 찾기"(1단계, 가격 기준) → "그 날 앞뒤 평균 거래대금 내기"
# (2단계, TRADE_VALUE_PEAK_WINDOW=10이 절반값이라 앞뒤5+당일=11봉)로
# 역할이 둘로 쪼개져 있었다. 이제 "거래대금 롤링평균이 최대인 지점 찾기"
# 하나로 탐색과 값 계산이 합쳐져서 상수도 하나면 충분 — 그래서 옛
# TRADE_VALUE_PEAK_WINDOW(절반값, 헷갈리는 단위)는 폐기하고 실제
# 창 길이(11)를 그대로 값으로 쓰는 TRADE_VALUE_ROLL_WINDOW로 교체.
TRADE_VALUE_ROLL_WINDOW = 11


def _norm(value: float, lo: float, hi: float) -> float:
    """선형 클램프 정규화: lo→0, hi→1, 범위 밖은 [0,1]로 잘림.
    lo>hi로 주면 자동으로 역방향(작을수록 1에 가까움)이 된다."""
    if hi == lo:
        return 0.0
    x = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, x))


def _accum_pass(key: str, raw: float) -> bool:
    op, th = ACCUM_PASS_THRESHOLDS[key]
    return raw >= th if op == "ge" else raw <= th


def accumulation_score(c: pd.Series, h: pd.Series, lo: pd.Series, v: pd.Series,
                       a_start: int, a_end: int) -> dict:
    """A구간(a_start~a_end, 절대 정수 위치, 오래된→최신 순)의 매집 흔적 채점.
    반환: {"score": int|None, "score_raw": float|None, "reason": str|None,
           "parts": {...}|None, "synergy": bool|None}
    데이터 부족 시 score=None + 구체적 reason — 0점으로 채우지 않음(None과
    0점은 의미가 다름). score_raw(v5.29)는 int(round())로 뭉개지기 전
    소수 2자리 원점수 — 정렬은 이걸로 해야 동점 뭉침(상위 50위가 서로
    다른 값 15개에 묶이는 등)을 피할 수 있다. score(int)는 배지 표시용
    으로 그대로 유지.
    """
    a_len = a_end - a_start
    if a_len < ACCUM_MIN_A_BARS:
        return {"score": None, "score_raw": None, "reason": "A구간_15봉미만", "parts": None, "synergy": None}

    prior_start = max(0, a_start - 60)
    prior_len = a_start - prior_start
    if prior_len < ACCUM_MIN_PRIOR_BARS:
        return {"score": None, "score_raw": None, "reason": "기준윈도우부족", "parts": None, "synergy": None}

    a_vol = v.iloc[a_start:a_end]
    bad_vol = int(((a_vol.isna()) | (a_vol <= 0)).sum())
    if bad_vol >= ACCUM_MAX_BAD_VOL_BARS:
        return {"score": None, "score_raw": None, "reason": "거래량데이터결측", "parts": None, "synergy": None}

    a_c, a_h, a_lo = c.iloc[a_start:a_end], h.iloc[a_start:a_end], lo.iloc[a_start:a_end]
    p_vol = v.iloc[prior_start:a_start]
    p_c, p_h, p_lo = c.iloc[prior_start:a_start], h.iloc[prior_start:a_start], lo.iloc[prior_start:a_start]

    raw = {}

    # 1) vol_ratio — A구간 평균거래량 / 직전 평균거래량
    p_vol_mean = float(p_vol.mean())
    raw["vol_ratio"] = float(a_vol.mean()) / p_vol_mean if p_vol_mean > 0 else 0.0

    # 2) atr_compress — atr_pct(A) / atr_pct(prior). atr()는 시리즈 끝에서
    #    period개만 보는 tail-relative 함수라, 각 구간 앞에 1봉을 더 붙여
    #    슬라이스하고 period=구간길이로 명시(첫 봉의 전일종가까지 확보).
    def _seg_atr_pct(seg_h, seg_lo, seg_c, start, end):
        ext_start = max(0, start - 1)
        period = end - start
        _h, _lo, _c = h.iloc[ext_start:end], lo.iloc[ext_start:end], c.iloc[ext_start:end]
        atr_val = atr(_h, _lo, _c, period=period)
        mean_close = float(seg_c.mean())
        return (atr_val / mean_close * 100) if mean_close > 0 else 0.0

    a_atr_pct = _seg_atr_pct(a_h, a_lo, a_c, a_start, a_end)
    p_atr_pct = _seg_atr_pct(p_h, p_lo, p_c, prior_start, a_start)
    raw["atr_compress"] = a_atr_pct / p_atr_pct if p_atr_pct > 0 else 1.0

    # 3) close_strength — (close-low)/(high-low) >= 0.6인 봉의 비율. high==low는 제외.
    rng = a_h - a_lo
    valid = rng > 0
    n_valid = int(valid.sum())
    if n_valid > 0:
        pos = (a_c - a_lo) / rng
        strong = int(((pos >= 0.6) & valid).sum())
        raw["close_strength"] = strong / n_valid
    else:
        raw["close_strength"] = 0.0

    # 4) ud_vol — A구간 상승봉 거래량합 / 하락봉 거래량합 (전일 대비, 1봉 앞 포함해 비교)
    ext_start = max(0, a_start - 1)
    c_ext = c.iloc[ext_start:a_end]
    v_ext = v.iloc[ext_start:a_end]
    diffs = c_ext.diff().iloc[1:]     # a_start~a_end-1 구간의 전일대비 변화
    vols_for_diff = v_ext.iloc[1:]
    up_vol = float(vols_for_diff[diffs > 0].sum())
    down_vol = float(vols_for_diff[diffs < 0].sum())
    ud_vol_none = down_vol <= 0
    raw["ud_vol"] = (up_vol / down_vol) if not ud_vol_none else None

    # 5) vol_pickup — A구간 전반부 대비 후반부 평균거래량 비율
    mid = a_start + a_len // 2
    first_half = v.iloc[a_start:mid]
    second_half = v.iloc[mid:a_end]
    fh_mean = float(first_half.mean()) if len(first_half) > 0 else 0.0
    raw["vol_pickup"] = (float(second_half.mean()) / fh_mean) if fh_mean > 0 else 0.0

    # 6) range_tight — (최고가-최저가)/최저가
    a_hi_val, a_lo_val = float(a_h.max()), float(a_lo.min())
    raw["range_tight"] = (a_hi_val - a_lo_val) / a_lo_val if a_lo_val > 0 else 1.0

    # 7) trade_value_ratio — A구간 평균 거래대금(거래량×종가) / 과거 6개월
    #    "자금고점"(거래대금이 가장 몰렸던 구간) 평균 거래대금. "바닥에서
    #    올라오는데 자금 유입이 그때만큼 강한가" 신호.
    #
    #    ⚠️ v5.30: "고점" 정의를 가격(종가) 기준 → 거래대금 기준으로 변경
    #    (단순 버그 수정이 아니라 지표가 재는 개념 자체가 바뀐 것).
    #    아래 fund_peak_*(자금고점)는 _pat_surge_accum()의 peak_abs("상승 1파
    #    고점" = 가격 고점, 여전히 종가 기준 그대로 — 피벗/타점 계산엔
    #    가격 고점이 맞는 개념이라 안 바꿨다)와 서로 다른 개념이니 섞어
    #    쓰지 말 것.
    #
    #    v5.27까지는 "종가가 가장 높았던 하루"를 고점으로 찍고 그 앞뒤
    #    11봉 평균 거래대금을 분모로 썼는데, 그 하루가 거래대금 기준으론
    #    딱히 특별하지 않은 경우가 흔했다(실측 706건 중 16%가 그 날
    #    거래대금/창평균 배수 1.2배 미만, 8%는 1.0배 미만 — "고점"이라면서
    #    거래대금은 평균 이하인 날을 분모로 삼고 있었다는 뜻). 이제 가격이
    #    아니라 거래대금 자체의 TRADE_VALUE_ROLL_WINDOW(11)봉 롤링평균이
    #    최대인 지점을 자금고점으로 잡는다 — 정의상 이 최댓값은 항상 창
    #    평균 이상이라(실측 862건 전부 확인, 최소 배수 1.3배) 저품질
    #    분모 문제가 구조적으로 해소된다. 탐색과 "고점 값" 계산이 이제
    #    한 단계로 합쳐져서 상수도 TRADE_VALUE_ROLL_WINDOW 하나면 된다
    #    (예전처럼 "고점일 찾기"+"그 주변 평균" 2단계로 안 쪼갠다).
    #
    #    기준점은 a_start(A구간 이전만 검색, v5.27) — a_end 기준이면
    #    탐색창이 A구간 자신을 포함해 자기비교가 되는 문제가 있었다.
    #    비교 구간이 20봉도 안 되면(데이터 앞쪽에 붙은 경우) 비교 자체가
    #    무의미하므로 None — 가중합 루프가 None을 "그 요소 제외하고
    #    재정규화"로 처리한다.
    tv_search_start = max(0, a_start - TRADE_VALUE_LOOKBACK_BARS)
    tv_search = v.iloc[tv_search_start:a_start] * c.iloc[tv_search_start:a_start]
    if len(tv_search) < 20:
        raw["trade_value_ratio"] = None
    else:
        fund_peak_trade_value = float(
            tv_search.rolling(TRADE_VALUE_ROLL_WINDOW, center=True,
                              min_periods=TRADE_VALUE_ROLL_WINDOW).mean().max()
        )
        a_trade_value = float((a_vol * a_c).mean())
        raw["trade_value_ratio"] = (a_trade_value / fund_peak_trade_value) if fund_peak_trade_value > 0 else 0.0

    # ── 정규화 + 가중합 (ud_vol None이면 그 가중치 제외하고 나머지 재정규화) ──
    parts = {}
    weight_sum = 0.0
    score_sum = 0.0
    for key, w in ACCUM_WEIGHTS.items():
        r = raw[key]
        if r is None:
            parts[key] = {"raw": None, "norm": None, "pass": None, "gated": False}
            continue
        lo_b, hi_b = ACCUM_NORM_RANGES[key]
        n = _norm(r, lo_b, hi_b)
        # trade_value_ratio 단독 가점 금지: 거래대금이 커도 ud_vol이 분산
        # (<1.0, 하락일에 거래량이 더 실림 = 투매 쪽)을 가리키면 매집 신호로
        # 안 치고 가중합에서 통째로 제외한다(감점이 아니라 다른 미가용
        # 요소와 동일하게 중립 처리 — raw/norm은 진단용으로 그대로 보여줌).
        if key == "trade_value_ratio" and raw["ud_vol"] is not None and raw["ud_vol"] < 1.0:
            parts[key] = {"raw": round(r, 3), "norm": round(n, 3), "pass": _accum_pass(key, r), "gated": True}
            continue
        parts[key] = {"raw": round(r, 3), "norm": round(n, 3), "pass": _accum_pass(key, r), "gated": False}
        weight_sum += w
        score_sum += w * n

    base_score = (score_sum / weight_sum) if weight_sum > 0 else 0.0
    synergy = bool(parts["vol_ratio"]["pass"] and parts["atr_compress"]["pass"])
    final = base_score * 100 + (ACCUM_SYNERGY_BONUS if synergy else 0)
    final = max(0.0, min(100.0, final))

    return {"score": int(round(final)), "score_raw": round(final, 2), "reason": None, "parts": parts, "synergy": synergy}


# ── 조용한 매집 스코어 (v5.24, Task 2) ──
# accum_score(A-B-C 전용)와 별개로, 눌림목 탭을 포함해 더 폭넓게 "조용히
# 물량이 쌓이는 중"인지를 0~100으로 채점. A-B-C의 A구간에도 부착하고
# 눌림목 카드에도 노출한다(부착/UI는 별도 배포 단계 — 이 함수는 순수 계산).
QUIET_ACCUM_DQ_SURGE_PCT = 0.15      # 윈도우 내 단일일 등락률 이 이상이면 "조용하지 않음"
QUIET_ACCUM_DQ_VOL_MULT = 8.0        # 최대거래량일/평균거래량 이 배 이상이면 이벤트성
QUIET_ACCUM_DQ_STRUCT_RATIO = 0.60   # 현재가/윈도우최고가 이 이하면 구조훼손
QUIET_ACCUM_DQ_TOP3_SHARE = 0.35     # 상위3일거래량/윈도우 총거래량 이 이상이면 이벤트성(2~3일짜리 사건 포착)
QUIET_ACCUM_GRADE_STRONG = 75
QUIET_ACCUM_GRADE_TRACE = 55
QUIET_ACCUM_GRADE_NEUTRAL = 35


def _vol_ma_ratio(v: pd.Series, short: int = 5, long: int = 50) -> float:
    """거래량 위축/증가 판정 표준 계산법(v5.24 교정) — 단일 봉 값을 절대
    직접 비교에 쓰지 않고 반드시 '단기 이동평균 ÷ 장기 평균'으로 계산한다.
    이유: 미완성 봉(장중)이나 휴장 직후 거래량이 급감한 단일 봉 하나를
    그대로 분자/분모에 쓰면 -80%대의 허위 위축 신호가 난다 — 5일 평균이
    그 하루의 영향력을 최대 1/5로 완충시킨다."""
    n = len(v)
    _long = min(long, n)
    if _long < 5:
        return 1.0
    ma_short = float(v.iloc[-short:].mean())
    ma_long = float(v.iloc[-_long:].mean())
    return ma_short / ma_long if ma_long > 0 else 1.0


def _quiet_accum_seg_atr_pct(h: pd.Series, lo: pd.Series, c: pd.Series, start: int, end: int) -> float:
    """세그먼트(start~end, 절대 위치)의 ATR% — accumulation_score의
    _seg_atr_pct와 동일 관례(앞에 1봉을 더 붙여 슬라이스, period=구간길이)."""
    ext_start = max(0, start - 1)
    period = end - start
    if period <= 0:
        return 0.0
    _h, _lo, _c = h.iloc[ext_start:end], lo.iloc[ext_start:end], c.iloc[ext_start:end]
    atr_val = atr(_h, _lo, _c, period=period)
    mean_close = float(c.iloc[start:end].mean()) if end > start else 0.0
    return (atr_val / mean_close * 100) if mean_close > 0 else 0.0


def _quiet_accum_grade(score: int) -> str:
    # v5.25: "매집흔적"이 accum_score(v5.19, A구간 전용 배지 "🧲 매집흔적 N")와
    # 텍스트가 겹쳐 같은 ABC 카드에 "매집흔적"이 두 번(다른 숫자로) 뜨는
    # 혼동이 있었음(사용자 리포트) — "🔷매집조짐"으로 교체해 구분.
    if score >= QUIET_ACCUM_GRADE_STRONG:
        return "🔵강한매집"
    if score >= QUIET_ACCUM_GRADE_TRACE:
        return "🔷매집조짐"
    if score >= QUIET_ACCUM_GRADE_NEUTRAL:
        return "⚪중립"
    return "⛔없음"


def _quiet_accum_dq(reason: str) -> dict:
    return {"score": 0, "grade": "⛔없음", "components": None,
            "disqualify_reason": reason, "data_basis": "추정"}


def quiet_accumulation_score(df: pd.DataFrame, window: int = 60) -> dict:
    """조용한 매집 스코어(0~100) — v5.24, Task 2.
    실격 조건(먼저 검사, 걸리면 score=0 + reason):
    - 윈도우 내 단일일 등락률 >= QUIET_ACCUM_DQ_SURGE_PCT → "급등포함_조용하지않음"
    - 최대거래량일/평균거래량 >= QUIET_ACCUM_DQ_VOL_MULT → "이벤트성"
    - 현재가/윈도우최고가 <= QUIET_ACCUM_DQ_STRUCT_RATIO → "구조훼손"
    - 상위3일거래량/윈도우총거래량 >= QUIET_ACCUM_DQ_TOP3_SHARE → "이벤트성_상위3일집중"
      (v5.24 추가 — 이틀·사흘짜리 사건을 단일일 조건이 놓치는 케이스 커버.
      한울반도체 320000 사례가 바로 이것: 이틀 연속 거래량 폭발이었음.)
    데이터 부족(len(df) < window+1, 예: 상장 60일 미만)이면 score=None +
    "데이터부족"으로 구분 — 0점(실격)과는 의미가 다르다.
    구성요소 2(하락일 거래량 위축)·5(후반부 거래량 증가)는 단일 봉 값이
    아니라 _vol_ma_ratio(5일 이동평균 ÷ 장기평균) 원칙을 따른다.
    """
    if df is None or len(df) < window + 1:
        return {"score": None, "grade": None, "components": None,
                "disqualify_reason": "데이터부족", "data_basis": "추정"}

    c_all, h_all, lo_all, v_all = df["Close"], df["High"], df["Low"], df["Volume"]
    n_total = len(df)
    c_win = c_all.iloc[-window:]
    h_win = h_all.iloc[-window:]
    lo_win = lo_all.iloc[-window:]
    v_win = v_all.iloc[-window:]
    close_now = float(c_win.iloc[-1])

    # ── 실격 조건 ──
    prev_win = c_all.iloc[-(window + 1):-1].values
    daily_ret = (c_win.values - prev_win) / prev_win
    if len(daily_ret) and float(daily_ret.max()) >= QUIET_ACCUM_DQ_SURGE_PCT:
        return _quiet_accum_dq("급등포함_조용하지않음")

    avg_vol = float(v_win.mean())
    max_vol = float(v_win.max())
    if avg_vol > 0 and max_vol / avg_vol >= QUIET_ACCUM_DQ_VOL_MULT:
        return _quiet_accum_dq("이벤트성")

    win_high = float(h_win.max())
    if win_high > 0 and close_now / win_high <= QUIET_ACCUM_DQ_STRUCT_RATIO:
        return _quiet_accum_dq("구조훼손")

    total_vol = float(v_win.sum())
    top3_all = sum(sorted(v_win.tolist(), reverse=True)[:3])
    if total_vol > 0 and top3_all / total_vol >= QUIET_ACCUM_DQ_TOP3_SHARE:
        return _quiet_accum_dq("이벤트성_상위3일집중")

    # ── 1) 자금흐름 CLV 가중 (25점) ──
    rng = h_win - lo_win
    rng_safe = rng.where(rng != 0, 1.0)
    mfm = (((c_win - lo_win) - (h_win - c_win)) / rng_safe).where(rng != 0, 0.0)
    vol_sum = float(v_win.sum())
    admf = float((mfm * v_win).sum() / vol_sum) if vol_sum > 0 else 0.0
    comp1 = _norm(admf, -0.10, 0.35) * 25

    # ── 2) 하락일 거래량 위축 (20점) — 단일 봉 대신 5일 이동평균 기준 ──
    down_mask = daily_ret < 0
    vol_ma5 = v_win.rolling(5, min_periods=1).mean()
    if down_mask.any():
        down_smoothed_mean = float(vol_ma5[down_mask].mean())
    else:
        down_smoothed_mean = 0.0   # 하락일이 아예 없음 = 가장 건강한 극단
    baseline50 = float(v_win.iloc[-50:].mean()) if len(v_win) >= 50 else float(v_win.mean())
    dryup = down_smoothed_mean / baseline50 if baseline50 > 0 else 1.0
    comp2 = _norm(dryup, 1.05, 0.60) * 20

    # ── 3) 거래량 분산도 (15점) — Task 1(ud_volume_detail) top1_share 재사용 ──
    _udd = ud_volume_detail(c_win, v_win, window=window)
    top1_share = _udd["top1_share"] if _udd else None
    comp3 = _norm(top1_share, 0.40, 0.12) * 15 if top1_share is not None else 0.0

    # ── 4) 변동성 수축 (15점) — 최근 1/3 구간 ATR% / 이전 2/3 구간 ATR% ──
    recent_len = max(1, window // 3)
    recent_start_abs, recent_end_abs = n_total - recent_len, n_total
    prior_start_abs, prior_end_abs = n_total - window, n_total - recent_len
    atr_recent = _quiet_accum_seg_atr_pct(h_all, lo_all, c_all, recent_start_abs, recent_end_abs)
    atr_prior = _quiet_accum_seg_atr_pct(h_all, lo_all, c_all, prior_start_abs, prior_end_abs)
    comp_val = atr_recent / atr_prior if atr_prior > 0 else 1.0
    comp4 = _norm(comp_val, 1.0, 0.65) * 15

    # ── 5) 후반부 거래량 증가 (15점) — 거래량은 _vol_ma_ratio(5일/50일), 폭은 반반 분할 ──
    vol_trend = _vol_ma_ratio(v_win, short=5, long=50)
    half = window // 2
    range_all = h_win - lo_win
    range_second = float(range_all.iloc[-half:].mean())
    range_first = float(range_all.iloc[-window:-half].mean()) if window > half else range_second
    range_trend = range_second / range_first if range_first > 0 else 1.0
    vol_part = _norm(vol_trend, 0.90, 1.05) * 7.5
    range_part = _norm(range_trend, 1.10, 0.95) * 7.5
    comp5 = vol_part + range_part

    # ── 6) 가격 안정성 (10점) — MDD 얕음(5점) + 윈도우 range 상위 위치(5점) ──
    running_max = c_win.cummax()
    mdd = float(((c_win / running_max) - 1.0).min())
    mdd_score = _norm(mdd, -0.30, -0.10) * 5
    win_low = float(lo_win.min())
    price_pos = (close_now - win_low) / (win_high - win_low) if win_high > win_low else 0.5
    pos_score = _norm(price_pos, 0.40, 0.80) * 5
    comp6 = mdd_score + pos_score

    components = {
        "clv_flow": round(comp1, 1),
        "dryup": round(comp2, 1),
        "vol_dispersion": round(comp3, 1),
        "vol_compression": round(comp4, 1),
        "late_vol_pickup": round(comp5, 1),
        "price_stability": round(comp6, 1),
    }
    score = max(0, min(100, round(sum(components.values()))))

    return {
        "score": score,
        "grade": _quiet_accum_grade(score),
        "components": components,
        "disqualify_reason": None,
        "data_basis": "추정",
    }


def _pat_htf(c, h, lo, v):
    """치솟은깃발(High Tight Flag): ≤45봉 내 +90% 급등 후 3~20봉 얕은(≤25%) 깃발."""
    n = len(c)
    if n < 70:
        return None
    W = 60
    hw = h.iloc[-W:].reset_index(drop=True)
    lw = lo.iloc[-W:].reset_index(drop=True)
    vw = v.iloc[-W:].reset_index(drop=True)
    i_peak = int(hw[:-3].idxmax())          # 고점(마지막 2봉 제외: 깃발 최소 3봉)
    flag_len = W - 1 - i_peak
    if not (3 <= flag_len <= 20):
        return None
    peak = float(hw.iloc[i_peak])
    run_win = lw.iloc[max(0, i_peak - 45):i_peak]
    if len(run_win) < 5:
        return None
    run_lo_i = int(run_win.idxmin())
    run_lo = float(lw.iloc[run_lo_i])
    if run_lo <= 0 or peak / run_lo - 1 < 0.90:
        return None                          # 45봉 내 +90% 급등이어야
    flag_low = float(lw.iloc[i_peak + 1:].min())
    depth = (peak - flag_low) / peak
    if depth > 0.25:
        return None                          # 깃발 눌림 25% 이내
    run_v = float(vw.iloc[max(run_lo_i, i_peak - 15):i_peak + 1].mean())
    flag_v = float(vw.iloc[i_peak + 1:].mean())
    vol_dry = run_v > 0 and flag_v < run_v * 0.7
    if run_v > 0 and flag_v > run_v * 1.05:
        return None                          # 깃발에서 거래량 확대는 분배 위험
    # 성숙도 (v4.48.3): 오닐 정석은 깃발 3~5주(15~25봉). 3봉부터 감지는 하되
    # 15봉 미만이거나 거래량이 안 말랐으면 "미완성" — 형성 중 돌파 추격은 실패 모드.
    _missing = []
    if flag_len < 15:
        _missing.append(f"깃발 {int(flag_len)}/15봉(3주) — {15 - int(flag_len)}봉 더 필요")
    if not vol_dry:
        _missing.append("거래량 고갈 전 (급등기 평균의 70% 미만이어야)")
    return {"pattern": "치솟은깃발", "pattern_emoji": "🚩", "pivot": peak,
            "near_lo": -18.0,   # 깃발은 피벗 아래 깊이 매달림(정상)
            "stop_raw": flag_low, "base_len": int(flag_len),
            "depth_pct": round(depth * 100, 1), "vol_dry": vol_dry,
            "pat_ready": not _missing, "pat_missing": _missing,
            "quality": 20 + (10 if vol_dry else 0) + (5 if depth < 0.15 else 0)}


def _pat_cup_handle(c, h, lo, v):
    """컵앤핸들: 좌측고점 → 12~35% U자 바닥 → 우측회복 → 3~20봉 얕은 손잡이."""
    n = len(c)
    L = min(n, 180)
    hw = h.iloc[-L:].reset_index(drop=True)
    lw = lo.iloc[-L:].reset_index(drop=True)
    vw = v.iloc[-L:].reset_index(drop=True)
    if L < 90:
        return None
    i_rim = int(hw[:L - 35].idxmax())        # 좌측 림: 최소 35봉 전
    rim = float(hw.iloc[i_rim])
    if i_rim >= L - 40:
        return None
    i_low = int(lw[i_rim + 3:L - 5].idxmin())
    cup_low = float(lw.iloc[i_low])
    depth = (rim - cup_low) / rim
    if not (0.12 <= depth <= 0.35):
        return None
    # U자(바닥 체류): 바닥 5% 이내 봉 4개 이상 → V자 반등 배제
    if int((lw.iloc[i_rim:] <= cup_low * 1.05).sum()) < 4:
        return None
    # 우측 회복: 림 -5% 이내 재도달 지점
    rec = hw.iloc[i_low + 3:] >= rim * 0.95
    if not rec.any():
        return None
    i_rec = int(rec.idxmax())
    if i_rec - i_rim < 30:
        return None                          # 컵 전체 최소 30봉
    handle_len = L - 1 - i_rec
    if not (3 <= handle_len <= 20):
        return None
    hd_high = float(hw.iloc[i_rec:].max())
    hd_low = float(lw.iloc[i_rec + 1:].min()) if handle_len >= 2 else float(lw.iloc[-1])
    if hd_high > rim * 1.06:
        return None                          # 이미 크게 돌파했으면 패턴 완료(늦음)
    if hd_low < cup_low + 0.5 * (rim - cup_low):
        return None                          # 손잡이는 컵 상반부에
    hd_depth = (hd_high - hd_low) / hd_high
    if hd_depth > 0.13:
        return None
    right_v = float(vw.iloc[i_low:i_rec + 1].mean())
    hd_v = float(vw.iloc[i_rec + 1:].mean()) if handle_len >= 2 else right_v
    vol_dry = right_v > 0 and hd_v < right_v * 0.85
    _missing = []
    if handle_len < 5:
        _missing.append(f"손잡이 {int(handle_len)}/5봉(1주) — {5 - int(handle_len)}봉 더 필요")
    if not vol_dry:
        _missing.append("손잡이 거래량 고갈 전 (우측회복기의 85% 미만이어야)")
    return {"pattern": "컵앤핸들", "pattern_emoji": "☕", "pivot": hd_high,
            "pat_ready": not _missing, "pat_missing": _missing,
            "stop_raw": hd_low, "base_len": int(L - 1 - i_rim),
            "depth_pct": round(depth * 100, 1), "vol_dry": vol_dry,
            "quality": 15 + (10 if vol_dry else 0) + (5 if hd_depth < 0.08 else 0)}


def _pat_double_bottom(c, h, lo, v):
    """더블바닥(W): 두 바닥(±4%, 15~90봉 간격) + 중간고점 10%↑ + 우측 회복."""
    n = len(c)
    L = min(n, 150)
    hw = h.iloc[-L:].reset_index(drop=True)
    lw = lo.iloc[-L:].reset_index(drop=True)
    if L < 60:
        return None
    # 2차(최근) 바닥 먼저 → 그 앞에서 1차 바닥 탐색 (순서 뒤집혀 잡히는 버그 방지)
    seg2 = lw.iloc[max(0, L - 60):L - 2]
    if seg2.empty:
        return None
    i2 = int(seg2.idxmin())
    b2 = float(lw.iloc[i2])
    if i2 < 20:
        return None
    seg1 = lw.iloc[:i2 - 15]
    if seg1.empty:
        return None
    i1 = int(seg1.idxmin())
    b1 = float(lw.iloc[i1])
    if not (15 <= i2 - i1 <= 90) or b1 <= 0:
        return None
    if not (0.96 <= b2 / b1 <= 1.04):
        return None                          # 두 바닥 ±4%
    if L - 1 - i2 > 50:
        return None                          # 2차 바닥이 너무 오래전이면 무효
    mid = float(hw.iloc[i1:i2 + 1].max())
    if mid / min(b1, b2) - 1 < 0.10:
        return None                          # 중간 반등 10%+
    pre_high = float(hw.iloc[:i1].max()) if i1 >= 5 else None
    if pre_high is None or pre_high < b1 * 1.15:
        return None                          # 바닥 전 하락추세 확인
    close = float(c.iloc[-1])
    if close < b2 * 1.03:
        return None                          # 우측 회복 시작
    return {"pattern": "더블바닥", "pattern_emoji": "🔻🔻", "pivot": mid,
            "stop_raw": b2, "base_len": int(L - 1 - i1),
            "depth_pct": round((pre_high - min(b1, b2)) / pre_high * 100, 1),
            "vol_dry": False, "quality": 12,
            "pat_ready": True, "pat_missing": []}


def _pat_surge_accum(c, h, lo, v):
    """A-B-C 상한가 패턴 (v4.55) — 급등구간 분리 + 동적 A/B 탐지.

    실데이터(광주·금호·삼화·디벨로먼트·마녀공장)로 배운 핵심:
    - 오늘 재폭발하면 close가 곧 최고가라, 최근 급등봉을 빼고 '상승 1파 고점'을
      찾아야 A-B-C 구조(peak=B상단)가 보인다.
    - C는 여러 상한가 국면. 첫 상한가=첫폭발, B후 재폭발=폭발초입, 3번째+=폭발진행.
    - B가 peak 대비 -30% 넘게 깊으면 컵앤핸들(마녀공장)이지 A-B-C 아님.

    스테이지: 첫폭발 / 수렴중 / 폭발초입 / 폭발진행.
    """
    n = len(c)
    if n < 120:
        return None
    try:
        close = float(c.iloc[-1])
        vol = float(v.iloc[-1])
        vol60 = float(v.iloc[-60:-1].mean())
        vol_ratio = vol / vol60 if vol60 > 0 else 0.0
        prev = float(c.iloc[-2])
        change = (close / prev - 1) * 100 if prev else 0

        rets = c.pct_change()

        # ── 1) 최근 연속 급등 구간(C) 식별 ──
        # 뒤에서부터 훑어 +18%↑ 봉들이 모인 구간을 C로 본다.
        # C 구간을 빼야 그 이전 '상승 1파 고점'이 보인다.
        surge_days = int((rets.iloc[-15:] >= 0.18).sum())
        # C 시작 지점: 최근 15봉 중 첫 급등봉 위치
        recent = rets.iloc[-15:]
        surge_positions = [i for i, r in enumerate(recent.values) if r >= 0.18]
        if surge_positions:
            first_surge_rel = surge_positions[0]          # 15봉 window 내 위치
            c_start_abs = len(c) - 15 + first_surge_rel   # 절대 위치
            pre_c = c.iloc[:c_start_abs]                  # C 이전 데이터
        else:
            c_start_abs = len(c)
            pre_c = c

        if len(pre_c) < 90:
            return None

        # ── 2) 상승 1파 고점 = C 이전 구간의 최고가 ──
        pre_window = pre_c.iloc[-90:] if len(pre_c) >= 90 else pre_c
        peak_rel = int(pre_window.values.argmax())
        peak_val = float(pre_window.iloc[peak_rel])
        peak_abs = len(pre_c) - len(pre_window) + peak_rel
        bars_since_peak = len(c) - 1 - peak_abs

        # ── 3) A 구간: 상승 1파 시작 전 수렴 (상승폭 감안해 peak에서 충분히 앞) ──
        # 상승 1파가 A~peak 사이에 있으므로, A는 그 이전이어야 순수 수렴.
        # v5.26: 고정 55봉 대신 적응형 — a_end에서 한 봉씩 뒤로 확장하며 누적
        # 고저폭이 25%를 넘기 직전까지 늘린다. 실데이터 검증 결과 종목별 실제
        # 수렴 유지 길이가 5봉~199봉까지 크게 갈려서(고정 55봉과 어긋나는 게
        # 흔함) 값을 하드코딩하지 않고 직접 찾는다.
        # 주의: a_start가 0(데이터 시작)까지 밀리면 아래 accumulation_score의
        # prior 구간(40봉 필요) 확보가 안 돼 매집 스코어가 채점 불가로 빠지고,
        # quiet_accumulation_score도 window=a_end-a_start=a_end가 되면서
        # len(df)<window+1 조건이 정확히 이 경우에만 항상 참이 되어(기존부터
        # 있던 조건, 이번에 새로 만든 버그 아님) 무조건 실격 처리된다 — 실측
        # 결과 이런 케이스는 애초에 first_wave/near 게이트에서 먼저 탈락하는
        # 종목들이라 실제 통과 히트에는 영향 없었음(22종목 회귀 기준).
        A_MAX_LOOKBACK = 250   # 무한 확장 방지 상한(초장기 횡보 케이스)
        a_end = max(peak_abs - 10, 20)
        a_start = a_end
        running_hi = running_lo = None
        while True:
            candidate_start = a_start - 1
            if candidate_start < 0:
                break
            if a_end - candidate_start > A_MAX_LOOKBACK:
                break
            val = float(c.iloc[candidate_start])
            cand_hi = val if running_hi is None else max(running_hi, val)
            cand_lo = val if running_lo is None else min(running_lo, val)
            cand_mid = (cand_hi + cand_lo) / 2
            cand_range = (cand_hi - cand_lo) / cand_mid if cand_mid > 0 else 1.0
            if cand_range > 0.25:
                break
            a_start = candidate_start
            running_hi, running_lo = cand_hi, cand_lo
        a_seg = c.iloc[a_start:a_end]
        if len(a_seg) < 25:
            return None
        a_hi, a_lo = float(a_seg.max()), float(a_seg.min())
        a_mid = (a_hi + a_lo) / 2
        a_range = (a_hi - a_lo) / a_mid if a_mid > 0 else 1.0
        if a_range > 0.25:
            return None

        # ── 4) 상승 1파 확인: peak가 A 상단 +15%↑ ──
        first_wave = peak_val >= a_hi * 1.15

        # ── 5) B 구간: peak 이후 ~ C 시작 전 (되밀린 수렴) ──
        has_b = False
        b_lo = a_lo
        b_depth = 0.0
        if first_wave and c_start_abs - peak_abs >= 4:
            # peak와 C 사이에 수렴 구간(B) 존재
            b_seg = c.iloc[peak_abs + 1:c_start_abs]
            if len(b_seg) >= 3:
                b_lo = float(b_seg.min())
                b_depth = (peak_val - b_lo) / peak_val
                if b_depth > 0.30:                       # 컵앤핸들 제외
                    return None
                has_b = True

        # ── 6) 장기선 위 ──
        ma60 = float(c.rolling(60).mean().iloc[-1])
        if close < ma60 * 0.95:
            return None

        # ── 7) 거래량 선행 유입 ──
        vol5 = float(v.iloc[-5:].mean())
        vol_building = vol5 > vol60 * 1.2

        # ── 8) 스테이지 판정 ──
        is_blast_today = vol_ratio >= 3.0 and change >= 18

        if is_blast_today:
            if has_b:
                # B 후 재폭발 = 정석 A-B-C의 C
                pivot = peak_val
                if surge_days <= 2:
                    stage = "폭발초입"; quality = 32
                else:
                    stage = "폭발진행"; quality = 18
            elif first_wave:
                # 상승 1파는 있으나 B 없이 바로 재급등 = 폭발진행 취급
                pivot = peak_val
                stage = "폭발진행"; quality = 20
            else:
                # A만 있고 첫 급등 = 첫폭발
                pivot = a_hi
                stage = "첫폭발"; quality = 26
            stop_raw = b_lo if has_b else a_lo
        elif has_b:
            # B 수렴 중 (폭발 전 대기)
            pivot = peak_val
            near = (close - pivot) / pivot * 100
            if not (-25 <= near < -1.5 and vol_ratio < 2.5):
                return None
            stage = "수렴중"
            quality = 20 + int((1 - abs(near) / 25) * 8)
            if vol_building:
                quality += 4
            # ── 매수 타점 (v4.55.1): 피벗(B상단) 추격 대신 B 하단 지지 반등 진입.
            b_seg_now = c.iloc[peak_abs + 1:c_start_abs]
            b_lo_now = float(b_seg_now.min())
            b_hi_now = float(b_seg_now.max())
            # 매수존: B 하단 ~ 하단+B폭의 30% (지지 근처에서 반등 시 진입)
            buy_zone_lo = b_lo_now
            buy_zone_hi = b_lo_now + (b_hi_now - b_lo_now) * 0.30
            # 손절: 매수타점(B하단)보다 아래 = B하단 -3% (지지 이탈 확인선)
            stop_raw = b_lo_now * 0.97
        else:
            return None

        near = (close - pivot) / pivot * 100

        # 수렴중이면 매수타점/목표 계산 (그 외는 None)
        _buy_lo = round(buy_zone_lo, 2) if stage == "수렴중" else None
        _buy_hi = round(buy_zone_hi, 2) if stage == "수렴중" else None
        _target = round(peak_val, 2) if stage == "수렴중" else None

        # v5.19: 매집 스코어 — A구간(a_start~a_end) 채점. 순위 정렬 보조용,
        # 진입신호/확률 아님. a_start/a_end는 여기서 처음 반환에 노출됨
        # (기존엔 내부 계산에만 쓰고 버려졌음).
        _accum = accumulation_score(c, h, lo, v, a_start, a_end)
        try:
            _a_start_date = c.index[a_start].strftime("%Y-%m-%d")
            _a_end_date = c.index[a_end - 1].strftime("%Y-%m-%d")
        except Exception:
            _a_start_date = _a_end_date = None

        # v5.24: 조용한 매집 스코어(Task 2) — A구간에 부착. accum_score(v5.19)와
        # 별개 지표(원본 accum_score는 무변경, 새 필드만 추가). A구간 끝(a_end)
        # 까지의 전체 이력을 넘겨 window=A구간길이로 계산 — 여기서 실패해도
        # _pat_surge_accum 전체를 죽이면 안 되므로 별도 try/except로 격리.
        try:
            _qa_df = pd.DataFrame({"Close": c, "High": h, "Low": lo, "Volume": v}).iloc[:a_end]
            _qa = quiet_accumulation_score(_qa_df, window=max(5, a_end - a_start))
        except Exception:
            _qa = {"score": None, "grade": None, "components": None,
                   "disqualify_reason": "계산오류", "data_basis": "추정"}

        return {
            "pattern": "급등매집",
            "pattern_emoji": "🎆",
            "pivot": round(pivot, 2),
            "stop_raw": round(stop_raw, 2),
            "quality": quality,
            "stage": stage,
            "surge_days": surge_days,
            "vol_ratio": round(vol_ratio, 1),
            "vol_building": vol_building,
            "a_range": round(a_range * 100, 1),
            "b_depth": round(b_depth * 100, 1),
            "has_b": has_b,
            "bars_since_peak": bars_since_peak,
            "base_len": len(a_seg),
            "depth_pct": round(b_depth * 100, 1),
            "vol_dry": vol_ratio < 0.8,
            "buy_zone_lo": _buy_lo,          # 수렴중 매수 타점 하단 (B 하단)
            "buy_zone_hi": _buy_hi,          # 수렴중 매수 타점 상단
            "abc_target": _target,           # 수렴중 목표 (B 상단=피벗)
            "pat_ready": stage.startswith("폭발") or stage == "첫폭발",
            "pat_missing": [] if "폭발" in stage else ["거래량 폭발 대기"],
            "near_lo": -25.0,
            "a_start": a_start, "a_end": a_end,          # v5.19: 절대 정수 위치 노출
            "a_start_date": _a_start_date, "a_end_date": _a_end_date,
            "accum_score": _accum["score"],
            "accum_score_raw": _accum["score_raw"],   # v5.29: 정렬 전용(동점 뭉침 방지), 배지는 accum_score(int) 그대로
            "accum_reason": _accum["reason"],
            "accum_parts": _accum["parts"],
            "accum_synergy": _accum["synergy"],
            "qa_score": _qa["score"],
            "qa_grade": _qa["grade"],
            "qa_components": _qa["components"],
            "qa_reason": _qa["disqualify_reason"],
        }
    except Exception:
        return None


def analyze_pattern(df: pd.DataFrame, rs_rank: int | None = None,
                    rs_mom: int | None = None,
                    cfg: dict = PATTERN_CONFIG, is_kr: bool = False) -> dict | None:
    """장기 패턴(컵앤핸들/치솟은깃발/더블바닥)이 거의 완성돼 피벗 근처인 종목."""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    close = float(c.iloc[-1])
    if close <= 0:
        return None

    hits = []
    for det in (_pat_htf, _pat_cup_handle, _pat_double_bottom, _pat_surge_accum):
        try:
            r = det(c, h, lo, v)
        except Exception:
            r = None
        if r:
            hits.append(r)
    if not hits:
        return None
    # 피벗 근접 조건: -6% ~ +1.5% (단 ABC 폭발은 돌파 당일이라 상한 예외 +35%)
    best = None
    for r in hits:
        near = (close - r["pivot"]) / r["pivot"] * 100
        near_lo = r.get("near_lo", cfg["near_lo"])
        _stg = r.get("stage") or ""
        near_hi = 35.0 if ("폭발" in _stg) else cfg["near_hi"]
        if near_lo <= near <= near_hi:
            r["_near"] = near
            if best is None or r["quality"] > best["quality"]:
                best = r
    if best is None:
        return None

    pivot = float(best["pivot"])
    stop, stop_struct, atr_buf = apply_atr_buffer(float(best["stop_raw"]), h, lo, c, 0.15)
    rr = rs_rank if rs_rank is not None else 50
    near = best["_near"]
    vol20 = float(v.iloc[-20:].mean())
    vol_ratio = float(v.iloc[-1]) / vol20 if vol20 > 0 else 0.0
    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
    cur_rsi = float(rsi(c).iloc[-1])

    # 점수: 근접 30 + 패턴품질(최대 30) + RS 25 + 거래량수축 15
    score = (
        30 * (1 - min(abs(min(near, 0)) / 6.0, 1.0))
        + best["quality"]
        + 25 * max(0.0, (rr - 50) / 49)
        + (15 if best["vol_dry"] else 0)
    )
    score = min(score, 100.0)

    _tt = trend_grade(c, lo, h, rs_rank, ud=up_down_volume(c, v, 50))
    # v4.80: M&A/특수상황 의심 종목은 스캔 결과에서 제외.
    _mg = _merger_block(c, h, lo, v)
    if _mg["merger"]:
        return None
    # v5.19: 매집 스코어는 _pat_surge_accum()가 계산해 best dict에 넣어두지만, 이
    # 함수는 best를 통째로 스프레드하지 않고 필드를 골라서 새 dict를 만들기
    # 때문에 따로 안 퍼올리면 계산만 되고 API까지 안 감. 급등매집이 최종
    # 승자일 때만 의미있는 필드라(다른 3개 패턴엔 "A구간" 개념이 없음)
    # best["pattern"] == "급등매집"일 때만 채운다.
    _is_abc = best["pattern"] == "급등매집"
    _accum_fields = {
        "accum_score": best.get("accum_score") if _is_abc else None,
        "accum_score_raw": best.get("accum_score_raw") if _is_abc else None,
        "accum_reason": best.get("accum_reason") if _is_abc else None,
        "accum_parts": best.get("accum_parts") if _is_abc else None,
        "accum_synergy": best.get("accum_synergy") if _is_abc else None,
        "a_start_date": best.get("a_start_date") if _is_abc else None,
        "a_end_date": best.get("a_end_date") if _is_abc else None,
        # v5.24: 조용한 매집 스코어(Task 2) — 같은 이유로 여기서 퍼올려야 API까지 간다.
        "qa_score": best.get("qa_score") if _is_abc else None,
        "qa_grade": best.get("qa_grade") if _is_abc else None,
        "qa_components": best.get("qa_components") if _is_abc else None,
        "qa_reason": best.get("qa_reason") if _is_abc else None,
    }
    # v5.37: is_kr을 이제 파라미터로 받아서 그대로 전달 — 예전엔 함수 자체에
    # is_kr이 없어 항상 False 고정(리스크 경고 임계 8%가 한국 종목에도
    # 적용됨 + badge_fields 자체가 아예 안 불려서 베이스품질/손절폭/약세장
    # 적격/ATR변동성 배지가 패턴 탭에만 안 뜨고 있었음). rrb를 변수로 먼저
    # 뽑아 badge_fields에도 그대로 넘긴다(analyze()와 동일 패턴).
    rrb = _rr_block(pivot, stop, h, lo, c,
                    base_low=float(best["stop_raw"]),
                    entry=None, warn_pct=8.0, is_kr=is_kr,
                    stop_struct=stop_struct, atr_buf=atr_buf)
    return {
        "mode": "pattern",
        "grade": _tt["grade"], "tt_pass": _tt["passed"], "tt_fails": _tt["fails"],
        **_mg,
        **_accum_fields,
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "triggered": near >= -2.0,
        "setup_score": None,
        "rs": rs_rank,
        "rs_mom": rs_mom,
        "leader": (rs_rank or 0) >= 90,
        "pattern": best["pattern"],
        "pattern_emoji": best["pattern_emoji"],
        "pattern_stage": best.get("stage"),   # ABC: 폭발/수렴완성
        "abc_buy_lo": best.get("buy_zone_lo"),   # ABC 수렴중 매수타점
        "abc_buy_hi": best.get("buy_zone_hi"),
        "abc_target": best.get("abc_target"),    # ABC 수렴중 목표(피벗)
        "pat_ready": best.get("pat_ready", True),
        "pat_missing": best.get("pat_missing", []),
        "base_len": best["base_len"],
        "depth_pct": best["depth_pct"],
        "pivot": round(pivot, 2),
        "pivot_dist_pct": round((pivot - close) / close * 100, 2),
        "vol_ratio": round(vol_ratio, 2),
        "ud_vol": up_down_volume(c, v, 50),
        "vol_dry": best["vol_dry"],
        "rsi": round(cur_rsi, 1),
        **rrb,
        # v5.37: 베이스품질/손절폭(ATR기반)/약세장적격/U-D신뢰도 배지 —
        # analyze()/analyze_breakout()/analyze_imminent()와 동일하게 공통 헬퍼 사용.
        **badge_fields(c, h, lo, v, pivot, is_kr, rs_rank, rrb),
        **volume_info(close, v),
        "avwap": anchored_vwap(h, lo, c, v),
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# Stage 2 트렌드 템플릿 스캐너 (v5.02, 사용자 스펙 그대로 구현)
# 적용 순서: 유동성 → RS백분위 → Stage2템플릿 → 거래량수축/MA수렴 → 티어링
# 유동성컷 + RS백분위 계산은 app.py에서 처리(유니버스 전체를 봐야 하는
# 단계라 종목별 함수인 이 파일에서는 못 함). 여기 analyze_stage2()는
# 이미 유동성·RS 통과한 종목 하나에 대해 Stage2템플릿~티어링만 판정한다.
# ══════════════════════════════════════════════════════

def rs_score_stage2(close: pd.Series) -> float | None:
    """Stage2 스캐너 전용 RS 원점수 (사용자 스펙):
        RS_score = 3M수익률*0.4 + 6M수익률*0.3 + 12M수익률*0.3
    기존 rs_raw_score(1M+3M+6M+9M+12M, IBD가중 0.4/0.2/0.2/0.2, app.py에서
    지수 대비 초과성과로 변환)와는 계산식 자체가 다른 별개 지표 — 다른
    탭(눌림목/돌파/급등 등)의 RS에는 영향 없음. 벤치마크 차감 없이 절대
    수익률 그대로 쓰고(사용자 스펙에 벤치마크 언급 없음), 저가주 폭등 왜곡
    방지용 로그수익률+클리핑만 rs_raw_score와 동일하게 적용."""
    c = close.dropna()
    if len(c) < 253:
        return None
    now = float(c.iloc[-1])

    def price_ago(days):
        # gate(253) > 252(최대 요구치)라 클램프 불필요 — 직접 인덱싱(v5.32 정리)
        return float(c.iloc[-days - 1])

    p3, p6, p12 = price_ago(63), price_ago(126), price_ago(252)
    if min(now, p3, p6, p12) <= 0:
        return None

    CLIP = 0.7

    def logret(a, b):
        r = math.log(a / b)
        return max(-CLIP, min(CLIP, r))

    r3, r6, r12 = logret(now, p3), logret(now, p6), logret(now, p12)
    return 0.4 * r3 + 0.3 * r6 + 0.3 * r12


STAGE2_CONFIG = {
    "min_bars": 262,             # 내부 최대 요구치 252(lo52/hi52 = c.iloc[-252:], 무가드)
                                  # + 10봉 버퍼(다른 tab들의 200요구+210게이트 관례와 동일 폭).
                                  # v5.32 이전엔 260(마진 8, 근거 없음) — rs_score_stage2가
                                  # 253 요구인데 US period="1y"(251봉)로 100% 죽었던 것과
                                  # 같은 구조라 재발 방지 차원에서 마진을 명시적으로 키움.
    "low52_mult": 1.30,          # 현재가 >= 52주 저점 * 1.30
    "high52_mult": 0.75,         # 현재가 >= 52주 고점 * 0.75 (고점 대비 -25% 이내)
    "ma200_rise_lookback": 20,   # 200일선 상승 판정 구간(거래일, ≈1개월)
    "ma_converge_max": 0.03,     # 5/10/20일선 최대-최소 스프레드가 종가의 3% 이내 = 수렴
    "tier_a_mult": 0.90,         # A티어 = 52주 고점 대비 -10% 이내
}


def analyze_stage2(df: pd.DataFrame, rs_pctile: int | None, cfg: dict = STAGE2_CONFIG) -> dict | None:
    """Stage 2 트렌드 템플릿 (사용자 스펙 그대로):
        현재가 > 50MA > 150MA > 200MA
        200MA가 최근 1개월간 상승 중
        현재가 >= 52주 저점 * 1.30
        현재가 >= 52주 고점 * 0.75 (고점 대비 -25% 이내 — 낙폭과대 반등형 제거)
    + 거래량 수축(최근5일 평균 < 50일 평균) + MA(5/10/20) 수렴(스프레드 3%
    이내) — 사용자 스펙상 "필터"로 명시돼 있어 가점이 아니라 둘 다 필수
    통과 조건으로 구현(기존 pullback/imminent의 vol_dry는 가점용이었던 것과
    다름, 별도 함수라 다른 탭에 영향 없음).
    호출부(app.py)가 유동성컷 + RS 백분위(>=70) 필터링을 이미 마친 뒤 이
    함수를 호출한다는 전제 — rs_pctile은 그 결과를 그대로 받아 점수/표시에만 씀."""
    if df is None or len(df) < cfg["min_bars"]:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < cfg["min_bars"]:
        return None
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]

    ma50 = c.rolling(50).mean()
    ma150 = c.rolling(150).mean()
    ma200 = c.rolling(200).mean()
    close = float(c.iloc[-1])
    m50, m150, m200 = float(ma50.iloc[-1]), float(ma150.iloc[-1]), float(ma200.iloc[-1])
    if any(math.isnan(x) for x in (m50, m150, m200)):
        return None

    # ── Stage2 템플릿 ──
    if not (close > m50 > m150 > m200):
        return None

    lb = cfg["ma200_rise_lookback"]
    m200_prev = float(ma200.iloc[-1 - lb]) if len(ma200) > lb else float("nan")
    if math.isnan(m200_prev) or not (m200 > m200_prev):
        return None

    lo52 = float(c.iloc[-252:].min())
    hi52 = float(c.iloc[-252:].max())
    if lo52 <= 0 or hi52 <= 0:
        return None
    if close < lo52 * cfg["low52_mult"]:
        return None
    if close < hi52 * cfg["high52_mult"]:
        return None

    # ── 거래량 수축 + MA 수렴 (둘 다 필수 — 사용자 스펙상 "필터") ──
    vol5 = float(v.iloc[-5:].mean())
    vol50 = float(v.iloc[-50:].mean())
    vol_dry = vol5 < vol50 if vol50 > 0 else False
    if not vol_dry:
        return None

    ma5, ma10, ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
    m5, m10, m20 = float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma20.iloc[-1])
    if any(math.isnan(x) for x in (m5, m10, m20)):
        return None
    ma_spread_pct = (max(m5, m10, m20) - min(m5, m10, m20)) / close
    ma_converge = ma_spread_pct <= cfg["ma_converge_max"]
    if not ma_converge:
        return None

    # ── 티어링 ──
    dist_from_high_pct = (hi52 - close) / hi52 * 100
    tier = "A" if close >= hi52 * cfg["tier_a_mult"] else "B"

    prev_close = float(c.iloc[-2]) if len(c) >= 2 else close
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0

    score = 60 * (rs_pctile or 0) / 99 + 40 * max(0.0, 1 - dist_from_high_pct / 25)

    return {
        "mode": "stage2",
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "score": round(score, 1),
        "tier": tier,
        "rs": rs_pctile,
        "ma50": round(m50, 2), "ma150": round(m150, 2), "ma200": round(m200, 2),
        "high52": round(hi52, 2), "low52": round(lo52, 2),
        "dist_from_high_pct": round(dist_from_high_pct, 1),
        "dist_from_low_pct": round((close / lo52 - 1) * 100, 1),
        "vol_ratio": round(vol5 / vol50, 2) if vol50 > 0 else None,
        "vol_dry": vol_dry,
        "ma_converge": ma_converge,
        "ma_spread_pct": round(ma_spread_pct * 100, 2),
        "triggered": False,
        "setup_score": None,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in ma20.iloc[-60:].tolist()
        ],
    }


# ══════════════════════════════════════════════════════
# IBD 9조건 스크린 (v5.03, 사용자 제공 스펙 — 미국 전용)
# 1.A/D Rating A/B 2.가격$5+ 3.50일평균거래량50만+ 4.50일평균거래대금$500만+
# 5.3개월수익률30%+ 6.21일ATR4%+ 7.베타1+ 8.펀드보유수20+ 9.시총$2억+
#
# 적용 순서: 가격데이터만으로 되는 저비용 5개(2~6번) 먼저 → 통과한 소수만
# yfinance .info가 필요한 고비용 3개(1·7·9번) 확인. 8번(펀드 보유 수)은
# yfinance 무료 데이터로 IBD 원본과 동일한 정확한 개수를 못 구해서
# heldPercentInstitutions(기관 보유 비율)로 대체 — 근사치임을 명시.
# 1번(A/D Rating)도 IBD 고유 알고리즘(13주 가중 가격·거래량)이 아니라
# 기존 up_down_volume(U/D Volume Ratio, 50일)을 등급으로 변환한 근사치.
# ══════════════════════════════════════════════════════

def analyze_ibd9_cheap(df: pd.DataFrame) -> dict | None:
    """IBD 9조건 중 가격 데이터만으로 판정 가능한 5개(조건 2~6).
    가격 $5+ · 50일평균거래량 50만주+ · 50일평균거래대금 $500만+ ·
    3개월수익률 30%+ · 21일ATR(중앙값 방식, atr() 함수 재사용) 4%+.
    전부 통과해야 dict 반환, 하나라도 미달이면 None(고비용 단계 생략)."""
    if df is None or len(df) < 65:
        return None
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if len(df) < 65:
        return None
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    close = float(c.iloc[-1])
    if close < 5:
        return None

    vol50 = float(v.iloc[-50:].mean()) if len(v) >= 50 else float(v.mean())
    if vol50 < 500_000:
        return None

    dvol50 = float((c.iloc[-50:] * v.iloc[-50:]).mean()) if len(c) >= 50 else float((c * v).mean())
    if dvol50 < 5_000_000:
        return None

    p3 = float(c.iloc[-64]) if len(c) >= 64 else None
    if not p3 or p3 <= 0:
        return None
    ret3m = close / p3 - 1
    if ret3m < 0.30:
        return None

    atr21 = atr(h, lo, c, period=21)
    atr_pct21 = atr21 / close * 100 if close > 0 else 0.0
    if atr_pct21 < 4.0:
        return None

    return {
        "close": round(close, 2),
        "vol50_avg": round(vol50),
        "dollar_vol50_avg": round(dvol50),
        "ret_3m_pct": round(ret3m * 100, 1),
        "atr21_pct": round(atr_pct21, 2),
    }


def analyze_ibd9_full(df: pd.DataFrame, cheap: dict, beta: float | None,
                      market_cap: float | None, held_pct_inst: float | None) -> dict | None:
    """IBD 9조건 중 yfinance .info가 필요한 나머지(조건 1·7·8·9) 판정.
    cheap은 analyze_ibd9_cheap()의 반환값(이미 조건 2~6 통과). beta/market_cap/
    held_pct_inst는 호출부(app.py)가 yfinance .info에서 미리 가져와 넘긴다
    (이 함수 자체는 네트워크 호출 없음).
    조건7 베타>=1, 조건9 시총>=$2억, 조건1 A/D등급(U/D Volume Ratio 근사) A/B만.
    조건8(펀드 보유 수)은 정확한 개수를 못 구해 별도 필터 없이 참고 표시만."""
    if beta is None or beta < 1.0:
        return None
    if market_cap is None or market_cap < 200_000_000:
        return None

    c, v = df["Close"], df["Volume"]
    ud = up_down_volume(c, v, 50) or 0.0
    grade = ("A" if ud >= 1.5 else "B" if ud >= 1.15 else
             "C" if ud >= 0.87 else "D" if ud >= 0.65 else "E")
    if grade not in ("A", "B"):
        return None

    prev_close = float(c.iloc[-2]) if len(c) >= 2 else cheap["close"]
    change_pct = (cheap["close"] / prev_close - 1) * 100 if prev_close else 0.0
    score = round(50 * min(ud, 3) / 3 + 50 * min(cheap["ret_3m_pct"], 100) / 100, 1)

    return {
        "mode": "ibd9",
        "change_pct": round(change_pct, 2),
        "score": score,
        "ad_grade": grade,
        "ud_ratio": round(ud, 2),
        "beta": round(beta, 2),
        "market_cap_musd": round(market_cap / 1e6),
        "held_pct_inst": round(held_pct_inst * 100, 1) if held_pct_inst is not None else None,
        **cheap,
        "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
        "spark_ma20": [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ],
    }

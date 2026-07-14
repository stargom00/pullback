"""
scanner.py 교체 블록 (v4.57)

기존 scanner.py에서 아래 두 함수를 찾아 이 파일 내용으로 통째로 교체:
  - def ftd_state(close, vol) -> dict
  - def gate_suggest(dist_days, ftd, above_ma60) -> tuple

그리고 dist_count()는 신규 추가 (기존에 없음 — app.py의 _index_regime 안에
인라인으로 있던 분산일 카운트를 함수로 분리한 것).

세 함수는 scanner.py 안에서 인접 배치 권장.
"""
import pandas as pd


# ══════════════════════════════════════════════════════════════
# v4.57 [근본수정] 시장 게이트 3중 버그 — FTD가 영원히 무시되던 문제
#
# [증상] 나스닥: FTD 19일 전 확인, 20/60일선 위, 정상 추세인데
#        gate_suggest가 "correction" (신규 진입 0). 코스피도 반등 중인데
#        분산일 7개가 25일 내내 안 빠져서 계속 correction.
#
# [원인 1] gate_suggest 첫 줄의 `if dist_days >= 6: return "correction"`이
#          FTD 분기보다 위에 있어 early return. 근데 FTD는 정의상 '조정을
#          겪은 뒤' 나오는 신호 → FTD가 뜨는 상황엔 25일 창에 분산일이
#          이미 6개 이상 쌓여 있는 게 정상. 즉 FTD 분기가 도달 불가능한
#          죽은 코드였음. (나스닥 dist_days=9 → 첫 줄에서 즉시 correction)
#
# [원인 2] 분산일 카운트가 순수 25일 롤링뿐. 오닐의 두 가지 제거 규칙이
#          빠져 있었음:
#            (a) FTD 발생 = 새 랠리 시작 = 카운트 리셋
#            (b) 5% 룰 — 지수가 그 분산일 종가 대비 +5% 이상 오르면 만료
#          그래서 "늘어나기만 하고 빠지질 않음". 25거래일 지나야만 빠짐.
#
# [원인 3] ftd_state의 in_correction이 FTD 후에도 안 풀림. rally_low가
#          40일 창 안에 남아 있는 한 계속 in_correction=True, rally_day가
#          23일까지 증가. FTD 후 조정 종료 조건이 아예 없었음.
#
# [수정] ① gate_suggest에서 FTD 분기를 분산일 임계보다 '먼저' 평가
#        ② dist_count() 신규 — FTD 리셋 + 5% 만료 룰 적용. 순수 25일
#           카운트(dist_raw)도 함께 반환해 비교 가능하게
#        ③ ftd_state에 조정 종료 조건 추가 (FTD 후 저점 대비 회복 &
#           고점 근접 → in_correction=False)
#        ④ Volume 없으면 dist_days=None. 0으로 위장하지 않음 (0이면 항상
#           confirmed가 나와 반대 방향 거짓 신호가 됨)
# ══════════════════════════════════════════════════════════════


def ftd_state(close: pd.Series, vol: pd.Series) -> dict:
    """오닐 FTD(팔로우스루 데이) 상태 머신 (v4.57).

    로직:
    - 조정 판정: 저점 이전 고점 대비 -6% 이상 하락했을 때만 FTD 개념 적용
    - 반등 시도: 최근 40일 내 최저 종가일 = 시도의 저점(rally_low).
      argmin 정의상 그 이후 종가는 저점을 깨지 않았음이 보장됨
      (깨졌다면 그 날이 새 argmin → 시도 자동 리셋)
    - rally_day: 저점일=1일차로 센 경과 거래일 수
    - FTD: 시도 4일차 이후, 지수 +1.25%↑ 상승 + 거래량 전일比 증가인 날

    v4.57 추가 — 조정 종료 조건:
      FTD 발생 후, 지수가 (a) 저점 대비 조정폭의 절반 이상 회복했고
      (b) 조정 이전 고점의 -3% 이내면 → 조정 국면 종료(in_correction=False).
      이게 없으면 FTD 후 몇 주가 지나 정상 추세로 복귀해도 rally_day만
      계속 증가하며 영원히 '조정 중'으로 남는다(나스닥 rally_day=23 사례).

    반환:
      in_correction : 아직 조정 국면인가 (FTD 후 회복하면 False)
      rally_day     : 반등 시도 N일차
      ftd           : FTD 발생 여부
      ftd_days_ago  : FTD가 몇 봉 전인가 (None이면 없음)
      ftd_idx_back  : FTD가 시리즈 끝에서 몇 번째 뒤인가 (dist_count가 씀)
      rally_low     : 반등 시도의 저점
      peak_before   : 저점 이전 고점 (조정 기준가)
      drawdown_pct  : 고점 대비 저점 하락률
      recovered     : FTD 후 회복 완료 (= 조정 졸업)
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

        # 저점: 최근 40일 내 최저 종가 (60일 프레임 내 위치로 환산)
        tail = min(40, n60)
        low_local = int(c60.iloc[-tail:].reset_index(drop=True).idxmin())
        low_p = n60 - tail + low_local
        rally_low = float(c60.iloc[low_p])

        # 조정 깊이 = "저점 이전"의 고점 대비 하락폭
        # (전체 60일 고점과 비교하면 꾸준한 상승장의 트레일링 저점도
        #  최신 고점 대비 -6%로 계산돼 조정으로 오판)
        peak_before = float(c60.iloc[:low_p + 1].max())
        drawdown = (rally_low / peak_before - 1.0) * 100 if peak_before > 0 else 0.0
        out["rally_low"] = round(rally_low, 2)
        out["peak_before"] = round(peak_before, 2)
        out["drawdown_pct"] = round(drawdown, 1)

        if drawdown > -6.0:
            return out                      # 조정 아님 — FTD 불필요

        last_i = n60 - 1
        out["rally_day"] = last_i - low_p + 1   # 저점일 = 1일차

        # FTD 탐색: 4일차(오프셋 3) 이후
        ret = c60.pct_change()
        ftd_i = None
        for i in range(low_p + 3, last_i + 1):
            if float(ret.iloc[i]) >= 0.0125 and float(v60.iloc[i]) > float(v60.iloc[i - 1]):
                ftd_i = i
                break

        if ftd_i is not None:
            out["ftd"] = True
            out["ftd_days_ago"] = last_i - ftd_i
            out["ftd_idx_back"] = last_i - ftd_i   # 시리즈 끝에서 몇 번째 뒤

        # ── v4.57: 조정 종료 판정 ──
        # 회복이 충분하면 더 이상 '조정 중'이 아니다:
        #   (a) 저점→현재가 회복이 조정폭(peak_before - rally_low)의 50% 이상
        #   (b) 현재가가 조정 이전 고점의 -3% 이내
        #
        # ⚠️ FTD 발생 여부를 조건에 넣지 않는다. 넣으면 "FTD를 거치지 않고
        #    슬금슬금 회복한 시장"이 40일 창에 저점이 남아있는 한 영원히
        #    in_correction=True로 갇힌다. 그러면 gate가 계속 correction이라
        #    신규 진입이 무기한 차단됨. 실제로 조정 후 FTD 없이 회복하는
        #    경우는 흔하다(급락일 없이 완만히 되돌리는 장).
        #
        #    오닐 원본에서 FTD는 '바닥에서 진입해도 되는 시점'을 알려주는
        #    신호지, '조정이 끝났음'을 정의하는 유일한 방법이 아니다.
        #    가격이 이미 고점 근처로 돌아왔으면 조정은 사실상 끝난 것.
        #    (FTD의 유무는 gate_suggest에서 별도로 반영 — FTD가 있으면
        #     "시험 매수 0.5R" 같은 진입 강도 안내가 붙는다)
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
    """분산일(Distribution Day) 카운트 — 오닐 제거 규칙 포함 (v4.57 신규).

    분산일 = 지수가 전일 대비 drop_pct(-0.2%)↓ 하락 + 거래량이 전일보다 증가.
    (= 기관이 파는 날)

    [v4.57] 기존 코드는 25일 롤링 합만 썼다. 오닐의 두 제거 규칙이 빠져
    "늘어나기만 하고 안 빠지는" 카운트가 됐고, 조정이 끝나 반등해도 한 달
    내내 correction 게이트에 갇혔다. 두 규칙을 넣는다:

      (a) FTD 리셋: FTD = 새 랠리의 시작. FTD 이전의 분산일은 '이전 조정'의
          것이므로 카운트에서 제외. FTD 당일부터 다시 센다.
      (b) 5% 만료: 지수 현재가가 그 분산일 '종가' 대비 expire_gain(+5%) 이상
          올랐으면, 그 매도는 시장이 이미 소화한 것 → 만료.

    Volume이 없거나 전부 0이면 분산일 판정 자체가 불가능하다. 이때 0을
    반환하면 "분산일 없음 = 건강한 시장"이라는 반대 방향의 거짓 신호가
    되므로, days=None을 반환해 호출부가 '판정 불가'로 처리하게 한다.

    반환:
      days      : 최종 분산일 수 (None이면 거래량 없어 판정 불가)
      raw       : 제거 규칙 적용 전, 순수 25일 롤링 카운트 (비교용)
      expired   : 5% 룰로 만료된 개수
      pre_ftd   : FTD 이전이라 제외된 개수
      dates     : 최종 분산일들의 (인덱스, 등락%, 거래량배수) 목록 (진단용)
      vol_ok    : 거래량 데이터 유효 여부
    """
    out = {"days": None, "raw": None, "expired": 0, "pre_ftd": 0,
           "dates": [], "vol_ok": False}
    try:
        if close is None or len(close) < window + 2:
            return out

        # ── 거래량 유효성 검증 ──
        # yfinance는 지수(^GSPC 등)의 Volume을 0이나 NaN으로 주는 경우가 있다.
        # 그러면 (vol > vol.shift(1))이 전부 False → 분산일 0개 → 항상
        # confirmed. 0으로 위장하면 안 되고, 판정 불가를 명시해야 한다.
        if vol is None or len(vol) != len(close):
            return out
        v_win = vol.iloc[-(window + 1):]
        if float(v_win.fillna(0).sum()) <= 0:
            return out                       # 거래량 전부 0/NaN → 판정 불가
        if int(v_win.isna().sum()) > window * 0.3:
            return out                       # 결측 30% 초과 → 신뢰 불가
        out["vol_ok"] = True

        ret = close.pct_change()
        vol_up = vol > vol.shift(1)
        down = ret <= drop_pct
        mask = (down & vol_up).iloc[-window:]
        raw = int(mask.sum())
        out["raw"] = raw

        cur = float(close.iloc[-1])
        ftd_back = (ftd or {}).get("ftd_idx_back")   # FTD가 끝에서 몇 번째 뒤인가

        kept = []
        expired = 0
        pre_ftd = 0
        n = len(close)
        # mask는 마지막 window개. 각 True 위치를 '끝에서 몇 번째 뒤'로 환산.
        for pos, flag in enumerate(mask.tolist()):
            if not flag:
                continue
            idx_back = (window - 1) - pos     # 0 = 오늘, window-1 = 가장 오래된 날
            abs_i = n - 1 - idx_back
            d_close = float(close.iloc[abs_i])

            # (a) FTD 리셋 — FTD보다 이전(더 뒤)의 분산일은 이전 조정의 것
            if ftd_back is not None and idx_back > ftd_back:
                pre_ftd += 1
                continue

            # (b) 5% 만료 — 현재가가 그 날 종가 대비 +5%↑면 소화된 매도
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


def gate_suggest(dist: dict, ftd: dict, above_ma60: bool) -> tuple[str, str]:
    """분산일 + FTD 상태 → 시장 게이트 자동 제안 (v4.57).

    ⚠️ 시그니처 변경: dist_days(int) → dist(dict, dist_count()의 반환).
       거래량 없어 판정 불가한 경우(days=None)를 구분해야 하기 때문.
       호출부(app._index_regime)도 함께 수정 필요.

    [v4.57 핵심 수정] FTD 분기를 분산일 임계보다 '먼저' 평가한다.
      기존: `if dist_days >= 6: return correction` 이 맨 위에 있어서,
            FTD 분기(그 아래)가 영원히 도달 불가능한 죽은 코드였다.
            FTD는 정의상 조정 뒤에 나오므로 dist_days가 큰 게 정상인데,
            그걸 먼저 걸러버리니 FTD가 절대 반영되지 않았음.
      수정: FTD가 있으면 그건 '새 랠리 시작'이라는 강한 신호다. dist_count가
            이미 FTD 이전 분산일을 제거해 주므로, 여기서 보는 dist는
            'FTD 이후에 새로 쌓인' 분산일이다. 그걸로 판정한다.

    반환: (gate, 이유 한 줄). gate: confirmed | pressure | correction
    노출 비율(%)은 여기서 정하지 않는다 — R 설정(max_open_r)이 이미
    3R/1.5R/0 으로 정의하고 있고, 그게 유일한 근거 있는 규칙이다.
    """
    d = dist.get("days")

    # ── 0) 거래량 없어 분산일 판정 불가 ──
    # 0으로 위장하면 "분산일 없음 = 건강" 이라는 반대 신호가 된다.
    # 추세(60일선)만으로 보수적으로 판정하고, 그 사실을 문구에 명시.
    if d is None:
        if not above_ma60:
            return "correction", "60일선 아래 · 거래량 데이터 없어 분산일 판정 불가"
        return "pressure", "거래량 데이터 없어 분산일 판정 불가 — 보수적 판정"

    # ── 1) FTD 먼저 (v4.57: 순서가 핵심) ──
    if ftd.get("ftd"):
        ago = ftd.get("ftd_days_ago")
        if ftd.get("recovered"):
            # FTD 후 조정 이전 고점 근처까지 회복 → 정상 추세 복귀
            if d >= 6:
                return "correction", f"FTD 후 회복했으나 분산일 재차 {d}개 — 신규 매도 압력"
            if d >= 4:
                return "pressure", f"FTD 후 회복 · 분산일 {d}개 누적 — A급만"
            return "confirmed", f"FTD 후 회복 완료 · 분산일 {d}개"
        # FTD는 났지만 아직 회복 중 (반등 초기)
        if d >= 5:
            return "correction", f"FTD({ago}일 전) 후 분산일 {d}개 — 랠리 실패 조짐"
        if d >= 3:
            return "pressure", f"FTD({ago}일 전) 후 분산일 {d}개 — A급만 1.5R"
        return "confirmed", (f"FTD 확인 ({ago}일 전) · 분산일 {d}개 — "
                             f"시험 매수 0.5R 1~2건부터")

    # ── 2) 조정 중인데 FTD 아직 없음 → 진입 금지 ──
    if ftd.get("in_correction"):
        day = ftd.get("rally_day", 0)
        dd = ftd.get("drawdown_pct")
        return "correction", (f"조정 중 (고점比 {dd}%) · 반등 시도 {day}일차 · FTD 대기")

    # ── 3) FTD 없이 회복한 경우 ──
    # 조정은 있었으나 FTD(급등+거래량)를 거치지 않고 완만히 되돌렸다.
    # 조정 자체는 끝났지만(가격이 고점 근처), 기관 매수의 확증(FTD)이
    # 없었으므로 confirmed까지는 주지 않는다. 아래 정상 추세 판정으로
    # 넘어가되, 분산일이 조금만 쌓여도 pressure가 되도록 보수적으로.
    if ftd.get("recovered") and not ftd.get("ftd"):
        if not above_ma60:
            return "pressure", "60일선 아래 — 선별 진입"
        if d >= 5:
            return "correction", f"FTD 없이 회복 · 분산일 {d}개 — 매도 압력 우세"
        if d >= 3:
            return "pressure", f"FTD 없이 회복 · 분산일 {d}개 — 매수 확증 부족"
        return "confirmed", f"조정 회복 (FTD 미발생) · 분산일 {d}개"

    # ── 4) 조정 국면 아님 (정상 추세) ──
    if not above_ma60:
        return "pressure", "60일선 아래 — 선별 진입"
    if d >= 6:
        return "correction", f"분산일 {d}개 — 기관 매도 우세"
    if d >= 4:
        return "pressure", f"분산일 {d}개 — 압박 누적"
    return "confirmed", f"상승 추세 · 분산일 {d}개"

"""
전(前) 테마 리더 재점화 감시 (v5.125, 사용자 지시).

배경: docs/kr_theme_leader_reignition.md 측정(2026-08-31, 사전등록)에서
KR 테마 D0(점화일) 리더가 D0+30~D0+180거래일 창에서 51.8% 재점화(대조군
대비 z>=1.96 두 건 모두 유의, 확인진입 EV=+0.755R) — 채택됨. 이 모듈은
그 결과를 실시간 워치리스트 계산으로 옮긴다. 저장/스케줄링/알림 노출은
app.py 쪽 책임(theme_lifecycle.py/money_flow.py와 같은 원칙 — 이 모듈은
app.py에 의존하지 않고 시장 데이터를 인자로 받아 계산만 한다).

[백테스트와 의도적으로 다른 점 — 실시간 감시로 옮기며 바뀐 부분]
백테스트의 "재점화 판정"(단일일 +15%↑ OR 5일누적 +25%↑, 거래량 2배)은
이미 벌어진 급등을 사후에 찾는 정의라 실시간 알림에 그대로 못 쓴다(급등이
끝난 뒤에야 판정 가능 — 알림 시점엔 이미 늦음). 그래서 실시간 감시는
표준 돌파 정의(최근 20일 고가 상향 돌파 + 거래량 1.5배, scanner.py의
다른 탭들과 같은 어휘)로 대체한다 — "창 진입 자체가 이미 통계적으로
유리한 시기"라는 근거(백테스트)가 있으므로, 그 창 안에서는 응축 여부와
무관하게 더 이른 표준 돌파만 감시하면 된다(사용자 지시: "시간 창
기반"). 응축(ATR/거래량 수축) 여부는 배지로만 병기하고 게이트로 쓰지
않는다 — 백테스트에서 재점화 전 응축 선행이 43.7%(과반 미달)였으므로
필수 조건으로 걸면 실제 재점화의 과반을 놓친다(docs/kr_theme_leader_reignition.md).

[v5.130] 노이즈가드(MAX_ACTIVE_WATCHES=20, RS 상위만 확인진입 체크) 제거.
check_confirm() 실측 원가 ~4ms/종목 — theme_map 커버리지가 수백 종목으로
늘어도 전체 체크 비용이 초 단위 미만이라 캡을 둘 이유가 없었다(원래
캡의 근거였던 "계산비용"이 측정 결과 무근거로 판명). 사용자가 지적한
실제 문제(사용 예: 두산에너빌리티가 RS 캡 밖이라 확인진입 체크에서
조용히 빠짐)를 캡을 없애 원천 해결 — 이제 창 안의 모든 후보를 매일
확인진입 체크한다.
"""
from __future__ import annotations

import pandas as pd

import scanner

import theme_lifecycle as tl

WATCH_WINDOW_START = 30      # 거래일 — 백테스트(2026-08-31_kr_theme_leader_reignition.py)와 동일
WATCH_WINDOW_END = 180       # 거래일 — 위와 동일
# compute_theme_series(window=...)에 넘길 값 — 창 끝(180거래일 전)의
# D0까지 사이클 탐지가 커버해야 하므로 WATCH_WINDOW_END보다 넉넉히 크게.
LOOKBACK_WINDOW = WATCH_WINDOW_END + tl.BASELINE_WINDOW + 20

PIVOT_LOOKBACK = 20           # 피벗 = 최근 20거래일 고가(당일 제외)
CONFIRM_VOL_MULT = 1.5
CONFIRM_VOL_AVG_WINDOW = 50   # 트레일링 거래량 평균 창(당일 제외)
COMPRESSION_LOOKBACK = 20     # 참고 배지 전용(게이트 아님)
EXEC_ATR_STOP_MULT = 1.5      # 실행정보 표시용 참고 손절(진입가-ATR×1.5) — CLAUDE.md
                              # ATR 상대 손절폭 관례의 배수 재사용. 포워드 트래킹
                              # 확인진입 stop(20일 저가, 백테스트와 동일 정의)과는
                              # 별개 — 이건 순수 카드 표시용 참고치.
FORWARD_MAX_BARS = 60         # 확인진입 후 포워드 추적 최대 봉수(백테스트 confirm_entry_race와 동일)


def find_watch_candidates(theme_map_entries: dict, data: dict, market_turnover: pd.Series) -> list[dict]:
    """theme_map.json 전 테마에서 지금 D0+30~D0+180거래일 창 안에 있는
    (테마, 리더) 이벤트를 반환한다. theme_map_entries: {테마명: entry}
    (theme_map.list_all()의 반환값 그대로). 반환 리스트 원소:
    {theme, ticker, name, d0_date(YYYY-MM-DD), days_since_d0, window_days_left}.
    매핑에 없는 테마는 애초에 theme_map_entries에 없으므로 자동 제외."""
    out = []
    for name, entry in (theme_map_entries or {}).items():
        stocks = entry.get("stocks") or []
        if not stocks:
            continue
        theme_data = tl.compute_theme_series(stocks, data, market_turnover, window=LOOKBACK_WINDOW)
        if theme_data is None:
            continue
        cycles = tl.find_cycles(theme_data)
        if not cycles:
            continue
        latest_idx = len(theme_data["rows"]) - 1
        name_map = {s["ticker"]: (s.get("name") or s["ticker"]) for s in stocks}
        for c in cycles:
            d0 = c["d0"]
            offset = latest_idx - d0["index"]
            if not (WATCH_WINDOW_START <= offset <= WATCH_WINDOW_END):
                continue
            ticker = d0["leader"]
            out.append({
                "theme": name, "ticker": ticker, "name": name_map.get(ticker, ticker),
                "d0_date": str(d0["date"].date()), "days_since_d0": offset,
                "window_days_left": WATCH_WINDOW_END - offset,
            })
    return out


def _atr14(df) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = (high - low).combine((close.shift(1) - low).abs(), max).combine(
        (high - close.shift(1)).abs(), max)
    return tr.rolling(14).mean()


def compression_ref(df) -> dict | None:
    """참고용 배지 — 게이트 아님(모듈 docstring 참고). D-1 vs D-20 ATR14
    수축 여부 + 5일평균거래량 감소 여부. 판정에 필요한 봉 수 부족하면 None."""
    n = len(df)
    if n < COMPRESSION_LOOKBACK + 15:
        return None
    atr = _atr14(df)
    d1, d20 = n - 1, n - 1 - COMPRESSION_LOOKBACK
    atr_d1, atr_d20 = atr.iloc[d1], atr.iloc[d20]
    atr_contract = bool(atr_d1 < atr_d20) if (atr_d1 == atr_d1 and atr_d20 == atr_d20) else None
    vol = df["Volume"]
    vol5_d1 = vol.iloc[max(0, d1 - 4):d1 + 1].mean()
    vol5_d20 = vol.iloc[max(0, d20 - 4):d20 + 1].mean()
    vol_decline = bool(vol5_d1 < vol5_d20) if (vol5_d1 == vol5_d1 and vol5_d20 == vol5_d20) else None
    either = (atr_contract or vol_decline) if (atr_contract is not None or vol_decline is not None) else None
    return {"atr_contract": atr_contract, "vol_decline": vol_decline, "either": either}


def check_confirm(df) -> dict | None:
    """오늘 봉이 표준 돌파(최근 20거래일 고가 상향 돌파 + 거래량 1.5배)
    조건을 충족하는지. df: 종목 OHLCV(마지막 행이 오늘). 반환 None =
    판정에 필요한 봉 수 부족.

    v5.130: 실행 정보(카드 표시용) 필드 추가 — current_price/distance_pct/
    atr_stop/risk_pct/target_2r. `stop`(20일 저가, 포워드 트래킹용 확인진입
    손절 — 백테스트와 동일 정의, 절대 변경 금지)과 `atr_stop`(표시 전용
    참고 손절 = pivot - ATR×1.5)은 서로 다른 개념이니 혼동 주의."""
    n = len(df)
    if n < PIVOT_LOOKBACK + CONFIRM_VOL_AVG_WINDOW + 1:
        return None
    high, low, close, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    pivot = float(high.iloc[-(PIVOT_LOOKBACK + 1):-1].max())
    stop = float(low.iloc[-(PIVOT_LOOKBACK + 1):-1].min())
    avg_vol = scanner.nonzero_vol_mean(vol.iloc[-(CONFIRM_VOL_AVG_WINDOW + 1):-1])  # 거래정지일 제외 v5.129
    today_high = float(high.iloc[-1])
    today_vol = float(vol.iloc[-1])
    confirmed = today_high >= pivot and avg_vol > 0 and today_vol >= CONFIRM_VOL_MULT * avg_vol

    current_price = float(close.iloc[-1])
    atr14 = scanner.atr(high, low, close, 14)
    atr_stop = pivot - atr14 * EXEC_ATR_STOP_MULT
    risk_pct = (pivot - atr_stop) / pivot * 100 if pivot > 0 else None
    target_2r = pivot + 2 * (pivot - atr_stop) if atr_stop < pivot else None
    distance_pct = (pivot - current_price) / current_price * 100 if current_price > 0 else None

    return {"pivot": pivot, "stop": stop, "confirmed": bool(confirmed),
            "compression": compression_ref(df),
            "current_price": current_price,
            "distance_pct": round(distance_pct, 2) if distance_pct is not None else None,
            "atr_stop": round(atr_stop, 2),
            "risk_pct": round(risk_pct, 2) if risk_pct is not None else None,
            "target_2r": round(target_2r, 2) if target_2r is not None else None}

"""1년 +100% 특징 칩(v5.402) — 저점 관찰 종목의 **신호 봉 당시** 두 특징. 표시 전용.

사용자 2026-11-24: "100% 수익을 노리고 … 바닥에서 다시 상승를 시작하는 종목에 진입할꺼야 … 시작하는 종목엔 어떤 특징이잇는지 … 데이터로 분석해서"
→ 측정 docs/lowpoint_double_features.md(확인 2개) + 하락 쪽 docs/lowpoint_double_downside.md → "관찰 표에 칩을 표시로만 붙이기" 제안에 "응".

특징(측정 스크립트 2026-11-24_lowpoint_double_features.py의 F4·F7과 같은 식 — test_double_traits가 실데이터로 값 일치를 고정):
  F4 = 신호 봉 종가 ÷ 단순 200일선 − 1        좋은 쪽 ≤ F4_CUT  (측정 발견 절반 1사분위 컷 −38.35%)
  F7 = 최근 60봉 종가 최고 ÷ 최저 − 1          좋은 쪽 > F7_CUT  (측정 발견 절반 4사분위 컷 64.96%)
측정 결과(KR 주봉 신호·상장 1년+, 1,514신호, 상장폐지 없음): 1년 안 +100% 전체 17.7% · 둘 다 37.7% · 대신 −50% 이하를 찍는 비율 21.2%(전체 12.0%).
판정·순위에 넣지 말 것 — 표시 전용. 내부 요구 봉 수: 신호 봉까지 200봉(F4), 60봉(F7).
"""
from __future__ import annotations

import pandas as pd

F4_CUT = -0.38352601989456214   # 측정 결과 JSON explore.F4_ma200_gap.cuts[0]
F7_CUT = 0.6496303809440842     # 측정 결과 JSON explore.F7_range60.cuts[2]
LISTED_MIN = 250                 # 측정 표본 조건(상장 1년 — 신호 봉까지 250봉)


def traits(daily: "pd.DataFrame | None", base_date: "str | None") -> "dict | None":
    """정제·확정된 일봉 + 신호 봉 날짜 → {f4, f7, f4_ok, f7_ok, n_ok, listed_ok} 또는 None(신호 봉 없음·200봉 미만)."""
    if daily is None or daily.empty or not base_date:
        return None
    idx = daily.index
    jb = int(idx.searchsorted(pd.Timestamp(base_date), side="right")) - 1
    if jb < 0 or pd.Timestamp(idx[jb]).date() != pd.Timestamp(base_date).date() or jb + 1 < 200:
        return None
    c = daily["Close"].astype(float).to_numpy()
    f4 = float(c[jb] / c[jb - 199:jb + 1].mean() - 1)
    w = c[jb - 59:jb + 1]
    f7 = float(w.max() / w.min() - 1)
    f4_ok, f7_ok = f4 <= F4_CUT, f7 > F7_CUT
    return {"f4": round(f4, 4), "f7": round(f7, 4), "f4_ok": bool(f4_ok), "f7_ok": bool(f7_ok),
            "n_ok": int(f4_ok) + int(f7_ok), "listed_ok": jb + 1 >= LISTED_MIN}

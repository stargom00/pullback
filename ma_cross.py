"""120·240일선 상향 돌파 — v5.381 (사용자 지시 2026-11-12 "알림봇으로 120평, 240평을 아래에서 위로 뚫을때 알림오게할수있어?"
→ "전종목은 무리인가? 코스피 코스닥").

판정 = 어제 종가 < 어제 w일선 AND 오늘 종가 ≥ 오늘 w일선(w일선 = 종가 단순평균). 장중 모드는 오늘 봉(진행 중)의 지금가를 종가 자리에 넣는다 — 잠정.
**관심 신호 · 측정 전.** 돌파 뒤 성과는 잰 적 없다(승률·우위 주장 금지). 거래량 배수는 표시 전용(판정에 안 씀).
얼마냐봇 v2.58 ma_cross.py(관심 종목·US용)와 같은 정의 — 봇은 별도 레포라 import 공유가 불가능해 정의를 이 docstring으로 맞춘다.
"""
from __future__ import annotations

import pandas as pd

WINDOWS = (120, 240)
VOL_AVG = 20            # 거래량 배수 기준 = 직전 20봉 평균(표시 전용)


def cross_up(df: pd.DataFrame, w: int, elapsed: float = 1.0) -> "dict | None":
    """마지막 봉이 w일선을 아래→위로 넘었으면 dict. 내부 요구 봉 수 = w+1."""
    if len(df) < w + 1:
        return None
    c = df["Close"].astype(float).to_numpy()
    ma_t = float(c[-w:].mean())
    ma_y = float(c[-w - 1:-1].mean())
    if not (c[-2] < ma_y and c[-1] >= ma_t):
        return None
    v = df["Volume"].astype(float).to_numpy()
    avg = float(v[-VOL_AVG - 1:-1].mean()) if len(v) > VOL_AVG else 0.0
    return {"window": w, "close": float(c[-1]), "ma": ma_t, "dist_pct": (c[-1] / ma_t - 1) * 100,
            "change_pct": (c[-1] / c[-2] - 1) * 100 if c[-2] > 0 else None,
            "vol_x": (float(v[-1]) / (avg * max(elapsed, 0.01))) if avg > 0 else None,
            "value": float(c[-1]) * float(v[-1])}

"""scanner.analyze() 검증: 눌림목 패턴은 잡고, 아닌 건 거르는지"""
import numpy as np
import pandas as pd
from scanner import analyze

rng = np.random.default_rng(42)


def make_df(closes, vols):
    closes = np.array(closes, dtype=float)
    return pd.DataFrame({
        "Open": closes * (1 + rng.normal(0, 0.002, len(closes))),
        "High": closes * 1.012,
        "Low": closes * 0.988,
        "Close": closes,
        "Volume": np.array(vols, dtype=float),
    })


# ── Case 1: 전형적 눌림목 (상승 → 고점 → 완만한 조정, 거래량 수축) ──
up = [100 * (1.006 ** i) + rng.normal(0, 0.3) for i in range(110)]   # 우상향
peak = up[-1]
pull = [peak * (1 - 0.007 * i) + rng.normal(0, 0.2) for i in range(1, 11)]  # 7% 조정
closes1 = up + pull
vols1 = [1_000_000 + rng.integers(-50_000, 50_000) for _ in up] + \
        [int(550_000 - 15_000 * i) for i in range(10)]               # 조정 중 거래량 감소
r1 = analyze(make_df(closes1, vols1))
print("Case1 눌림목:", "탐지 ✓" if r1 else "미탐지 ✗")
if r1:
    print("   score:", r1["score"], "| pullback:", r1["pullback_pct"], "% | 지지:",
          r1["support_ma"], "| vol_ratio:", r1["vol_ratio"], "| RSI:", r1["rsi"])

# ── Case 2: 하락 추세 (걸리면 안 됨) ──
closes2 = [200 * (0.997 ** i) for i in range(120)]
vols2 = [800_000] * 120
r2 = analyze(make_df(closes2, vols2))
print("Case2 하락추세:", "통과(거름) ✓" if r2 is None else "오탐 ✗")

# ── Case 3: 상승 중 과열 (고점 부근, 조정 없음 → 걸리면 안 됨) ──
closes3 = [100 * (1.008 ** i) for i in range(120)]
vols3 = [1_200_000] * 120
r3 = analyze(make_df(closes3, vols3))
print("Case3 과열/무조정:", "통과(거름) ✓" if r3 is None else "오탐 ✗")

# ── Case 4: 추세 붕괴 (25% 폭락 → 걸리면 안 됨) ──
up4 = [100 * (1.006 ** i) for i in range(110)]
crash = [up4[-1] * (1 - 0.03 * i) for i in range(1, 11)]
closes4 = up4 + crash
vols4 = [1_000_000] * 110 + [3_000_000] * 10
r4 = analyze(make_df(closes4, vols4))
print("Case4 추세붕괴:", "통과(거름) ✓" if r4 is None else "오탐 ✗")

# ── Case 5: 눌림이지만 거래량 폭증 (분산 의심 → RSI/vol에 따라) ──
vols5 = [1_000_000] * 110 + [2_500_000] * 10
r5 = analyze(make_df(closes1, vols5))
print("Case5 눌림+거래량폭증:", ("탐지(점수 " + str(r5["score"]) + ", vol_dry=" + str(r5["vol_dry"]) + ")") if r5 else "거름")

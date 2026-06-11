"""scanner v2 검증: RS/200일선 필터 + 피벗 계산"""
import numpy as np
import pandas as pd
from scanner import analyze, rs_raw_score, to_rs_rank

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


# ── Case 1: 1년 내내 우상향 후 7% 눌림 (탐지되어야 함) ──
up = [100 * (1.0035 ** i) + rng.normal(0, 0.3) for i in range(245)]
pull, px = [], up[-1]
for i in range(12):                          # 하락 2일 + 반등 1일 패턴, 총 -7%대
    px *= 0.991 if i % 3 != 2 else 1.004
    pull.append(px + rng.normal(0, 0.2))
closes1 = up + pull
vols1 = [1_000_000] * 245 + [int(550_000 - 10_000 * i) for i in range(12)]
df1 = make_df(closes1, vols1)
r1 = analyze(df1, rs_rank=85)
print("Case1 강세 눌림목(RS85):", "탐지 ✓" if r1 else "미탐지 ✗")
if r1:
    print(f"   score {r1['score']} | RS {r1['rs']} | 피벗 {r1['pivot']} (+{r1['pivot_dist_pct']}%) | 손절 {r1['stop']} | 리스크 {r1['risk_pct']}%")
    assert r1["pivot"] > r1["close"] > r1["stop"], "피벗>현재가>손절 순서 오류"
    print("   피벗 > 현재가 > 손절 순서 ✓")

# ── Case 2: 같은 차트인데 RS 30 (탈락해야 함) ──
r2 = analyze(df1, rs_rank=30)
print("Case2 같은 차트 RS30:", "탈락 ✓" if r2 is None else "오탐 ✗")

# ── Case 3: 반토막 후 2달 반등 (200일선 아래 → 탈락해야 함) ──
down = [700 * (0.997 ** i) for i in range(180)]
rebound = [down[-1] * (1.004 ** i) for i in range(40)]
dip = [rebound[-1] * (1 - 0.006 * i) for i in range(1, 8)]
closes3 = down + rebound + dip
vols3 = [1_000_000] * len(closes3)
r3 = analyze(make_df(closes3, vols3), rs_rank=70)
print("Case3 하락 후 반등(200일선 아래):", "탈락 ✓" if r3 is None else "오탐 ✗")

# ── Case 4: RS 등급 백분위 계산 ──
fake = {f"T{i}": float(i) for i in range(100)}
ranks = to_rs_rank(fake)
print("Case4 RS 백분위:", "OK ✓" if ranks["T99"] == 99 and ranks["T0"] == 1 and 45 <= ranks["T49"] <= 55 else "오류 ✗")

# ── Case 5: rs_raw_score 가중치 (최근 강세 > 과거 강세) ──
recent_strong = pd.Series([100.0] * 190 + [100 * (1.005 ** i) for i in range(63)])
past_strong = pd.Series([100 * (1.005 ** i) for i in range(63)] + [137.0] * 190)
s_recent, s_past = rs_raw_score(recent_strong), rs_raw_score(past_strong)
print("Case5 최근 3개월 가중:", "OK ✓" if s_recent > s_past else f"오류 ✗ ({s_recent:.2f} vs {s_past:.2f})")


# ── Case 6: 주도주 모드 — RS95는 얕은 눌림(2%)도 탐지, RS70은 탈락 ──
rng6 = np.random.default_rng(11)
rets = rng6.normal(0.004, 0.018, 250)
closes6 = list(100 * np.cumprod(1 + rets))
px = closes6[-1]
for r in [-0.009, -0.007, 0.002, -0.007, 0.001]:
    px *= 1 + r
    closes6.append(px)
vols6 = [1_000_000 + int(rng6.integers(-200_000, 200_000)) for _ in range(250)] + \
        [620_000, 590_000, 610_000, 560_000, 540_000]
df6 = pd.DataFrame({"Open": closes6, "High": [c*1.01 for c in closes6],
                    "Low": [c*0.99 for c in closes6], "Close": closes6,
                    "Volume": [float(v) for v in vols6]})
r6a, r6b = analyze(df6, rs_rank=95), analyze(df6, rs_rank=70)
print("Case6 주도주 얕은눌림:", "OK ✓" if (r6a and r6a["leader"] and r6b is None) else "오류 ✗")

# ── Case 7: 추세전환 — 역배열 1년 → 최근 정배열 형성 (탐지) ──
from scanner import analyze_turnaround
rng7 = np.random.default_rng(3)
downtrend = list(200 * np.cumprod(1 + rng7.normal(-0.002, 0.015, 170)))   # 완만한 하락
base = list(downtrend[-1] * np.cumprod(1 + rng7.normal(0.0005, 0.012, 50)))  # 바닥 다지기
recovery = list(base[-1] * np.cumprod(1 + rng7.normal(0.005, 0.012, 38)))    # 회복 랠리
closes7 = downtrend + base + recovery
vols7 = [800_000] * 220 + [1_400_000] * 38
df7 = pd.DataFrame({"Open": closes7, "High": [c*1.01 for c in closes7],
                    "Low": [c*0.99 for c in closes7], "Close": closes7,
                    "Volume": [float(v) for v in vols7]})
r7 = analyze_turnaround(df7, rs_rank=55, rs_mom=35)
print("Case7 추세전환:", f"탐지 ✓ score {r7['score']} | 정배열 D+{r7['align_days']} | 200선 +{r7['ma200_dist_pct']}%" if r7 else "미탐지 ✗")

# ── Case 8: 같은 차트, RS 모멘텀 음수 (탈락해야) ──
r8 = analyze_turnaround(df7, rs_rank=55, rs_mom=-5)
print("Case8 RS모멘텀 음수:", "탈락 ✓" if r8 is None else "오탐 ✗")

# ── Case 9: 1년 내내 정배열 (전환 아님 → 탈락해야) ──
steady = list(100 * np.cumprod(1 + rng7.normal(0.003, 0.012, 265)))
df9 = pd.DataFrame({"Open": steady, "High": [c*1.01 for c in steady],
                    "Low": [c*0.99 for c in steady], "Close": steady,
                    "Volume": [1_000_000.0] * 265})
r9 = analyze_turnaround(df9, rs_rank=90, rs_mom=5)
print("Case9 장기 정배열:", "탈락 ✓ (전환 아님)" if r9 is None else f"오탐 ✗ (D+{r9['align_days']})")

# ── Case 10: RS 곱셈 — 모양 같으면 RS 높은 쪽이 점수 위 ──
# Case1 차트 재사용 (df1, 위에서 정의됨)
hi = analyze(df1, rs_rank=95)
lo_ = analyze(df1, rs_rank=55)
print("Case10 RS 곱셈:", "OK ✓" if hi and lo_ and hi["score"] > lo_["score"] + 5 else "오류 ✗",
      f"(RS95 {hi['score'] if hi else '-'} vs RS55 {lo_['score'] if lo_ else '-'})")

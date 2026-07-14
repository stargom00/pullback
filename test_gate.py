"""게이트 수정 검증. 기존 로직 vs 신규 로직 비교."""
import pandas as pd
import numpy as np
from scanner_gate import ftd_state, dist_count, gate_suggest

# ── 기존(버그) 로직 재현 ──
def old_ftd_state(close, vol):
    out = {"in_correction": False, "rally_day": 0, "ftd": False,
           "ftd_days_ago": None, "rally_low": None, "drawdown_pct": 0.0}
    c60 = close.iloc[-60:].reset_index(drop=True)
    v60 = vol.iloc[-60:].reset_index(drop=True)
    n60 = len(c60)
    tail = min(40, n60)
    low_local = int(c60.iloc[-tail:].reset_index(drop=True).idxmin())
    low_p = n60 - tail + low_local
    rally_low = float(c60.iloc[low_p])
    peak_before = float(c60.iloc[:low_p + 1].max())
    drawdown = (rally_low / peak_before - 1.0) * 100
    out["drawdown_pct"] = round(drawdown, 1)
    if drawdown > -6.0:
        return out
    out["in_correction"] = True
    last_i = n60 - 1
    out["rally_day"] = last_i - low_p + 1
    ret = c60.pct_change()
    for i in range(low_p + 3, last_i + 1):
        if float(ret.iloc[i]) >= 0.0125 and float(v60.iloc[i]) > float(v60.iloc[i-1]):
            out["ftd"] = True
            out["ftd_days_ago"] = last_i - i
            break
    return out

def old_dist_days(close, vol):
    ret = close.pct_change()
    vol_up = vol > vol.shift(1)
    down = ret <= -0.002
    return int((down & vol_up).iloc[-25:].sum())

def old_gate_suggest(dist_days, ftd, above_ma60):
    if dist_days >= 6:
        return "correction", f"분산일 {dist_days}개 — 기관 매도 우세"
    if ftd.get("in_correction"):
        if not ftd.get("ftd"):
            return "correction", "FTD 대기"
        if dist_days <= 3:
            return "confirmed", "FTD 확인"
        return "pressure", "FTD 후 압박"
    if not above_ma60:
        return "pressure", "60일선 아래"
    if dist_days >= 4:
        return "pressure", "압박 누적"
    return "confirmed", "상승 추세"


def build(prices, vols):
    return pd.Series(prices, dtype=float), pd.Series(vols, dtype=float)


def run(name, close, vol, expect_new):
    ma60 = float(close.rolling(60).mean().iloc[-1])
    cur = float(close.iloc[-1])
    above = cur > ma60

    o_f = old_ftd_state(close, vol)
    o_d = old_dist_days(close, vol)
    o_g, o_w = old_gate_suggest(o_d, o_f, above)

    n_f = ftd_state(close, vol)
    n_d = dist_count(close, vol, n_f)
    n_g, n_w = gate_suggest(n_d, n_f, above)

    ok = "✓" if n_g == expect_new else "✗ FAIL"
    print(f"\n{'='*68}\n{name}   → 기대: {expect_new}  {ok}")
    print(f"  60MA위={above}  현재={cur:.0f}")
    print(f"  [기존] dist={o_d:<3} ftd={str(o_f['ftd']):<5} "
          f"in_corr={str(o_f['in_correction']):<5} rally_day={o_f['rally_day']:<3}"
          f" → {o_g}")
    print(f"         why: {o_w}")
    print(f"  [신규] dist={n_d['days']} (raw={n_d['raw']}, 만료={n_d['expired']}, "
          f"FTD전제외={n_d['pre_ftd']})  ftd={n_f['ftd']} "
          f"recovered={n_f['recovered']} in_corr={n_f['in_correction']}")
    print(f"         why: {n_w}")
    return n_g == expect_new


results = []

# ─────────────────────────────────────────────────────────────
# T1: 나스닥 실제 시나리오 재현
#     -7% 조정 → FTD → 19일간 회복해서 고점 근처 복귀, 60MA 위
#     기존: correction (버그)  /  기대: confirmed
# ─────────────────────────────────────────────────────────────
p = [100 + i*0.15 for i in range(120)]          # 완만 상승 120봉 (100→118)
v = [1000] * 120
# 조정: 118 → 110 (-7%), 12봉
for i in range(12):
    p.append(118 - (i+1)*0.7)
    v.append(1300 if i % 2 == 0 else 900)        # 하락일 거래량 증가 = 분산일
# 반등 3봉 (FTD 전)
for i in range(3):
    p.append(p[-1] + 0.3); v.append(950)
# FTD: 4일차, +1.8%, 거래량 증가
p.append(p[-1] * 1.018); v.append(1600)
# FTD 후 19봉 회복 → 고점(118) 근처로
last = p[-1]
step = (117.5 - last) / 19
for i in range(19):
    p.append(p[-1] + step); v.append(1050 + (i%3)*50)
c, vv = build(p, v)
results.append(run("T1 나스닥: FTD 후 19일 회복, 60MA 위", c, vv, "confirmed"))

# ─────────────────────────────────────────────────────────────
# T2: 코스피 실제 시나리오 — 깊은 조정(-25%), FTD 없음, 반등 2일차
#     기존/신규 둘 다 correction 이어야 (회귀 방지)
# ─────────────────────────────────────────────────────────────
p = [100 + i*0.1 for i in range(110)]            # 100 → 111
v = [1000]*110
for i in range(28):                              # -25% 폭락
    p.append(p[-1] * 0.9897)
    v.append(1400 if i % 2 == 0 else 800)
for i in range(2):                               # 반등 2일차
    p.append(p[-1] * 1.008); v.append(1100)
c, vv = build(p, v)
results.append(run("T2 코스피: -25% 조정, FTD 없음, 반등 2일차", c, vv, "correction"))

# ─────────────────────────────────────────────────────────────
# T3: 정상 상승장, 분산일 거의 없음
# ─────────────────────────────────────────────────────────────
p = [100 * (1.002 ** i) for i in range(140)]
v = [1000 + (i % 7) * 30 for i in range(140)]
c, vv = build(p, v)
results.append(run("T3 정상 상승장 (분산일 거의 없음)", c, vv, "confirmed"))

# ─────────────────────────────────────────────────────────────
# T4: FTD 났지만 그 후 분산일 다시 쌓임 → 랠리 실패 조짐
# ─────────────────────────────────────────────────────────────
p = [100 + i*0.15 for i in range(110)]
v = [1000]*110
for i in range(12):                              # 조정
    p.append(p[-1] * 0.994); v.append(1300)
for i in range(3):
    p.append(p[-1] + 0.2); v.append(950)
p.append(p[-1]*1.02); v.append(1700)             # FTD
for i in range(10):                              # FTD 후 분산일 쏟아짐
    p.append(p[-1] * 0.995)
    v.append(1500)
c, vv = build(p, v)
results.append(run("T4 FTD 후 분산일 재차 누적 (랠리 실패)", c, vv, "correction"))

# ─────────────────────────────────────────────────────────────
# T5: 거래량 전부 0 (yfinance ^GSPC 케이스) → 판정 불가
#     0으로 위장해 confirmed 나오면 안 됨
# ─────────────────────────────────────────────────────────────
p = [100 + i*0.1 for i in range(140)]
v = [0]*140
c, vv = build(p, v)
results.append(run("T5 거래량 0 (지수 Volume 없음) → 판정 불가", c, vv, "pressure"))

# ─────────────────────────────────────────────────────────────
# T6: 60일선 아래 + 거래량 0
# ─────────────────────────────────────────────────────────────
p = [100 - i*0.2 for i in range(140)]
v = [0]*140
c, vv = build(p, v)
results.append(run("T6 60MA 아래 + 거래량 0", c, vv, "correction"))

print(f"\n{'='*68}")
print(f"결과: {sum(results)}/{len(results)} 통과")

"""실제 나스닥 상황 정확 재현. dist(순수 25일)=9, FTD 19일 전, 60MA 위.
기존 로직이 correction을 뱉는지(=버그 재현) 확인 후, 신규가 고치는지 본다."""
import pandas as pd
from scanner_gate import ftd_state, dist_count, gate_suggest
from test_gate import old_ftd_state, old_dist_days, old_gate_suggest


def show(name, close, vol, expect):
    ma60 = float(close.rolling(60).mean().iloc[-1])
    cur = float(close.iloc[-1])
    above = cur > ma60
    o_f = old_ftd_state(close, vol); o_d = old_dist_days(close, vol)
    o_g, o_w = old_gate_suggest(o_d, o_f, above)
    n_f = ftd_state(close, vol); n_d = dist_count(close, vol, n_f)
    n_g, n_w = gate_suggest(n_d, n_f, above)
    ok = "✓" if n_g == expect else "✗ FAIL"
    print(f"\n{'='*70}\n{name}  → 기대 {expect}  {ok}")
    print(f"  60MA위={above}  현재={cur:.1f}  drawdown={n_f['drawdown_pct']}%")
    print(f"  [기존] dist={o_d}  ftd={o_f['ftd']}({o_f['ftd_days_ago']}일전)  "
          f"in_corr={o_f['in_correction']}  rally_day={o_f['rally_day']}")
    print(f"         → {o_g}: {o_w}")
    print(f"  [신규] dist={n_d['days']}  (raw={n_d['raw']} / FTD전제외={n_d['pre_ftd']} "
          f"/ 5%만료={n_d['expired']})")
    print(f"         ftd={n_f['ftd']}({n_f['ftd_days_ago']}일전)  "
          f"recovered={n_f['recovered']}  in_corr={n_f['in_correction']}")
    print(f"         → {n_g}: {n_w}")
    if n_d['dates']:
        print(f"         남은 분산일: {[(d['idx_back'], d['ret_pct']) for d in n_d['dates']]}")
    return n_g == expect


# ══════════════════════════════════════════════════════════════
# 나스닥 실제 상황 재현
#   - 조정 -7%
#   - FTD 19일 전
#   - FTD 이후 회복해서 고점 근처, 60MA 위
#   - 순수 25일 롤링 분산일 = 9  ← 핵심. 이걸 만들어야 버그 재현됨
#
# 25일 창 = 최근 25봉. FTD가 19일 전이므로 25일 창 안에:
#   - FTD 이전 분산일 (조정 중 찍힌 것들) 6봉분
#   - FTD 이후 분산일 (회복 중 간간이) 3개
#   → raw = 9
# ══════════════════════════════════════════════════════════════
p, v = [], []
# (1) 상승 100봉: 100 → 130
for i in range(100):
    p.append(100 + i * 0.30); v.append(1000)

peak = p[-1]   # 130 근처

# (2) 조정 -7%: 130 → 121, 6봉. 전부 분산일(하락+거래량증가)
#     이 6봉이 25일 창 안에 들어옴 (FTD 19일전 + 조정 6봉 = 25봉)
for i in range(6):
    p.append(p[-1] * 0.988)          # -1.2%씩
    v.append(1400)                   # 전일보다 증가 → 분산일

# (3) 반등 3봉 (FTD 전, 4일차 만들기)
for i in range(3):
    p.append(p[-1] * 1.003); v.append(1100)

# (4) FTD: +1.8%, 거래량 증가
p.append(p[-1] * 1.018); v.append(1800)

# (5) FTD 후 19봉 회복 — 고점(130) 근처까지.
#     중간에 분산일 3개 심음 (하락 + 거래량증가)
low = p[-1]
target = peak * 0.99      # 고점 -1% 까지 회복
step = (target - low) / 19
for i in range(19):
    if i in (4, 10, 15):                 # 분산일 3개
        p.append(p[-1] * 0.995)          # -0.5% 하락
        v.append(v[-1] * 1.3)            # 거래량 증가
    else:
        p.append(p[-1] + step * 1.25)
        v.append(1000 + (i % 4) * 40)

c = pd.Series(p, dtype=float)
vv = pd.Series(v, dtype=float)

r1 = show("나스닥 실제: raw분산일 9, FTD 19일전, 회복완료, 60MA위",
          c, vv, "confirmed")

# ══════════════════════════════════════════════════════════════
# 회귀: 같은 상황인데 FTD 이후 분산일이 6개 → 랠리 실패로 봐야
# ══════════════════════════════════════════════════════════════
p2, v2 = list(p[:110]), list(v[:110])    # FTD 직후까지 복사
low = p2[-1]
for i in range(19):
    if i in (2, 4, 7, 10, 13, 16):       # FTD 후 분산일 6개
        p2.append(p2[-1] * 0.994)
        v2.append(v2[-1] * 1.35)
    else:
        p2.append(p2[-1] * 1.002)
        v2.append(1000)
c2 = pd.Series(p2, dtype=float); v2 = pd.Series(v2, dtype=float)
r2 = show("회귀: FTD 후 분산일 6개 (랠리 실패)", c2, v2, "correction")

# ══════════════════════════════════════════════════════════════
# 5% 만료 룰 단독 확인: 조정 후 크게 반등해서 옛 분산일들이 만료
# ══════════════════════════════════════════════════════════════
p3, v3 = [], []
for i in range(100):
    p3.append(100 + i * 0.2); v3.append(1000)
# 하락 8봉 전부 분산일
for i in range(8):
    p3.append(p3[-1] * 0.99); v3.append(1500)
# 이후 17봉 강한 반등 (+12%) → 옛 분산일 대비 +5% 넘김
for i in range(17):
    p3.append(p3[-1] * 1.007); v3.append(1000)
c3 = pd.Series(p3, dtype=float); v3 = pd.Series(v3, dtype=float)
r3 = show("5% 만료 룰: 강한 반등으로 옛 분산일 소화", c3, v3, "confirmed")

print(f"\n{'='*70}\n결과: {sum([r1,r2,r3])}/3")

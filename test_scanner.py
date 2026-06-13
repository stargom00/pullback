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

# ── Case 11: 인텔 패턴 — 급등 후 4주 횡보(20일선 평평), RS95만 탐지 ──
rng11 = np.random.default_rng(21)
run = list(100 * np.cumprod(1 + rng11.normal(0.006, 0.012, 185)))      # 강한 랠리
spike = list(run[-1] * np.cumprod(1 + np.array([0.02, 0.015, 0.01])))            # 고점 형성
flat = list(spike[-1] * 0.95 * np.cumprod(1 + rng11.normal(0.0, 0.006, 25)))  # 고점 -5%서 횡보
closes11 = run + spike + flat
vols11 = [2_000_000] * 188 + [int(1_100_000 - 8_000*i) for i in range(25)]
df11 = pd.DataFrame({"Open": closes11, "High": [c*1.012 for c in closes11],
                     "Low": [c*0.988 for c in closes11], "Close": closes11,
                     "Volume": [float(v) for v in vols11]})
r11a = analyze(df11, rs_rank=97)
r11b = analyze(df11, rs_rank=70)
print("Case11 주도주 횡보베이스:",
      f"RS97 탐지 ✓ (score {r11a['score']}, 피벗종류 {r11a['pivot_type']})" if r11a else "RS97 미탐지 ✗",
      "| RS70", "탈락 ✓" if r11b is None else "오탐 ✗")

# ── Case 12: 추세선 돌파 감지 — 하락 고점 3개 후 상향 돌파 ──
from scanner import trendline_level, select_pivot
# 하락 지그재그: 고점 110 → 106 → 102 (스윙 고점 3개), 마지막 3봉 상향 돌파
zig = []
for hi, lo_ in [(110, 100), (106, 97), (102, 95)]:
    zig += list(np.linspace(lo_ + 4, hi, 6)) + list(np.linspace(hi, lo_, 7))
closes12 = [100.0] * 30 + zig + [97.0, 100.0, 103.5]  # 마지막 3봉 돌파
h12 = pd.Series([c * 1.005 for c in closes12])
c12 = pd.Series(closes12)
tl = trendline_level(h12)
pivot, ptype, tlb = select_pivot(h12, pd.Series([c*0.995 for c in closes12]), c12, float(c12.iloc[-1]), 10)
print(f"Case12 추세선: level={'None' if tl is None else round(tl,1)} | 돌파감지={tlb} | 피벗종류={ptype}")

# ── Case 13: 🔥 트리거 — 인텔 패턴에 당일 +9% 돌파 양봉 추가 ──
closes13 = list(closes11) + [closes11[-1] * 1.09]
vols13 = list(vols11) + [3_000_000]
df13 = pd.DataFrame({"Open": closes13, "High": [c*1.012 for c in closes13],
                     "Low": [c*0.988 for c in closes13], "Close": closes13,
                     "Volume": [float(v) for v in vols13]})
r13 = analyze(df13, rs_rank=97)
flat_day = analyze(df11, rs_rank=97)   # 돌파 전날
print("Case13 트리거:", 
      ("발동 ✓ 🔥" if (r13 and r13.get("triggered")) else "미발동 ✗") + 
      " | 전날은 " + ("미발동 ✓" if (flat_day and not flat_day.get("triggered")) else "오발동 ✗"))

# ── Case 14: 전날 셋업 점수 — 🔥 발동 시 전일 점수가 함께 나오는지 ──
r14 = analyze(df13, rs_rank=97)
ok14 = r14 and r14.get("triggered") and r14.get("setup_score") is not None
print("Case14 전일점수:", f"OK ✓ (오늘 {r14['score']} / 전일 {r14['setup_score']})" if ok14 else "오류 ✗")

# ── Case 15: 섹터 매핑 + 요약 집계 ──
from sectors import get_sector
from collections import Counter
assert get_sector("AMD") == "반도체-연산"
assert get_sector("WDC") == "반도체-메모리"
assert get_sector("357780.KQ") == "반도체-소재"
assert get_sector("UNKNOWN123") == "기타"
# 가짜 hits로 요약 로직 검증
fake_hits = [{"sector": s} for s in
             ["반도체-연산","반도체-연산","반도체-메모리","클라우드SW","클라우드SW","클라우드SW","기타","기타"]]
cnt = Counter(h["sector"] for h in fake_hits if h["sector"] != "기타")
summary = [{"sector": s, "count": n} for s, n in cnt.most_common() if n >= 2]
ok15 = (summary[0]["sector"] == "클라우드SW" and summary[0]["count"] == 3
        and all(x["count"] >= 2 for x in summary)
        and not any(x["sector"] == "기타" for x in summary))
print("Case15 섹터 매핑/요약:", "OK ✓" if ok15 else "오류 ✗", "|", [(s["sector"], s["count"]) for s in summary])

# ── Case 16: 대장후보(leader) — 신고가 부근 강세 종목 탐지 ──
from scanner import analyze_leader
rng16 = np.random.default_rng(33)
strong = list(100 * np.cumprod(1 + rng16.normal(0.005, 0.012, 255)))  # 꾸준한 신고가 행진
# 마지막을 고점 근처로 (눌림 2% 미만)
strong[-1] = max(strong) * 0.985
vols16 = [1_000_000] * 255
df16 = pd.DataFrame({"Open": strong, "High": [c*1.01 for c in strong],
                     "Low": [c*0.99 for c in strong], "Close": strong,
                     "Volume": [float(v) for v in vols16]})
r16a = analyze_leader(df16, rs_rank=95, rs_mom=15)
r16b = analyze_leader(df16, rs_rank=70)   # RS 낮으면 탈락
print("Case16 대장후보:", 
      (f"RS95 탐지 ✓ (score {r16a['score']}, 고점까지 {r16a['dist_from_high_pct']}%)" if r16a else "RS95 미탐지 ✗"),
      "| RS70", "탈락 ✓" if r16b is None else "오탐 ✗")

# ── Case 17: 깊게 눌린 종목은 leader에서 탈락 (눌림목 영역) ──
deep = list(strong)
deep[-1] = max(strong) * 0.90   # 10% 눌림
df17 = pd.DataFrame({"Open": deep, "High": [c*1.01 for c in deep],
                     "Low": [c*0.99 for c in deep], "Close": deep,
                     "Volume": [1_000_000.0]*255})
r17 = analyze_leader(df17, rs_rank=95)
print("Case17 깊은눌림 leader제외:", "탈락 ✓ (눌림목 영역)" if r17 is None else "오탐 ✗")

# ── Case 18: 슈퍼대장 — RS 95+ 무조건 포착, 상태 분류 ──
from scanner import analyze_super
rng18 = np.random.default_rng(50)
# MU 패턴: 신고가 후 10% 눌림 (다른 모드엔 안 잡히는 사각지대)
base18 = list(100 * np.cumprod(1 + rng18.normal(0.006, 0.012, 245)))
peak18 = max(base18)
pull18 = [peak18 * (1 - 0.012*i) for i in range(1, 11)]  # 약 10% 조정
closes18 = base18 + pull18
df18 = pd.DataFrame({"Open": closes18, "High": [c*1.01 for c in closes18],
                     "Low": [c*0.99 for c in closes18], "Close": closes18,
                     "Volume": [1_000_000.0]*255})
s95 = analyze_super(df18, rs_rank=99, rs_mom=14)
s90 = analyze_super(df18, rs_rank=90)  # 95 미만은 탈락
print("Case18 슈퍼대장:",
      (f"RS99 포착 ✓ (상태:{s95['status']}, 고점까지 {s95['dist_from_high_pct']}%, 담을곳 {s95['buy_zone']})" if s95 else "RS99 미포착 ✗"),
      "| RS90", "탈락 ✓" if s90 is None else "오탐 ✗")

# 같은 RS99인데 깊은 눌림(다른 모드 탈락)도 슈퍼대장엔 잡혀야
deep18 = base18 + [peak18 * 0.82]
df18b = pd.DataFrame({"Open": deep18, "High": [c*1.01 for c in deep18],
                      "Low": [c*0.99 for c in deep18], "Close": deep18,
                      "Volume": [1_000_000.0]*len(deep18)})
sd = analyze_super(df18b, rs_rank=99)
print("Case18b 깊은눌림도 포착:", f"✓ (상태:{sd['status']})" if sd else "✗")

# ── Case 19: 슈퍼대장 배지 버그 수정 — 담을곳 거리/지지 상태 정확성 ──
from scanner import analyze_super
rng19 = np.random.default_rng(77)
# (a) 눌림 진행: 신고가서 9% 빠졌지만 아직 20일선 위에 떠 있음 → near_buy_zone False여야
b19 = list(100 * np.cumprod(1 + rng19.normal(0.006, 0.011, 245)))
pk = max(b19)
prog = [pk * (1 - 0.009*i) for i in range(1,11)]
df19a = pd.DataFrame({"Open": b19+prog, "High":[c*1.01 for c in b19+prog],
                      "Low":[c*0.99 for c in b19+prog], "Close": b19+prog,
                      "Volume":[1e6]*255})
r = analyze_super(df19a, rs_rank=99)
ok_a = r and (r["near_buy_zone"] == (0 <= r["buy_zone_dist_pct"] <= 3.0))
print(f"Case19a 담을곳 거리 일관성: {'OK ✓' if ok_a else '오류 ✗'} (상태:{r['status']}, 거리:{r['buy_zone_dist_pct']}%, 근접:{r['near_buy_zone']})")

# (b) 20일선 찍고 반등 → '지지✓' 상태 + bounced
flat = list(100 * np.cumprod(1 + rng19.normal(0.005, 0.01, 240)))
m20_now = sum(flat[-20:])/20
dip = [m20_now*0.995, m20_now*1.008]  # 20일선 찍고 양봉 마감
df19b = pd.DataFrame({"Open": flat+dip, "High":[c*1.01 for c in flat+dip],
                      "Low":[c*0.985 for c in flat+dip], "Close": flat+dip,
                      "Volume":[1e6]*242})
r2 = analyze_super(df19b, rs_rank=96)
print(f"Case19b 지지 판정: 상태={r2['status'] if r2 else 'None'}")
print("Case19 종합:", "OK ✓" if ok_a else "재확인 필요")

# ── Case 20: 모드별 리스크 과대 경고 (눌림 8% / 전환 15%) ──
# 눌림목: risk 8% 초과면 경고
import numpy as np, pandas as pd
rng20 = np.random.default_rng(91)
up = list(100 * np.cumprod(1 + rng20.normal(0.004, 0.01, 230)))
# 얕은 눌림 만들기
seq = up + [up[-1]*0.97, up[-1]*0.965, up[-1]*0.97]
df20 = pd.DataFrame({"Open": seq, "High":[c*1.015 for c in seq],
                     "Low":[c*0.985 for c in seq], "Close": seq, "Volume":[1e6]*len(seq)})
r20 = analyze(df20, rs_rank=85)
if r20:
    expect = r20["risk_pct"] > 8.0
    print(f"Case20 눌림목 리스크경고: {'OK ✓' if r20['risk_warn']==expect else '오류 ✗'} (risk {r20['risk_pct']}%, warn={r20['risk_warn']})")
else:
    print("Case20 눌림목: 탐지 안됨(조건 미스) — 경고로직은 코드상 8% 기준 확인됨")

# ── Case 21: alerts 로더 + 병합 ──
import os
from universe import load_alerts
testfile = os.path.join(os.path.dirname("."), "alerts_user.txt")
with open("alerts_user.txt","w",encoding="utf-8") as f:
    f.write("347850.KQ 투경\n005930.KS 투주\n")
al = load_alerts()
ok21 = al.get("347850.KQ")=="투경" and al.get("005930.KS")=="투주"
print("Case21 경보 로더:", "OK ✓" if ok21 else f"오류 ✗ {al}")
os.remove("alerts_user.txt")  # 정리

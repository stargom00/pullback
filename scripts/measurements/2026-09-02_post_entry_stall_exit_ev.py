"""
진입 후 "정체 구간" 조기청산 vs 계속보유 vs 절반청산 EV 비교
(2026-09-02, 사용자 지시).

【배경】아스플로(159010.KQ) 8/25 돌파임박 진입 → 2~3일 횡보를 "정체"로
판단해 +1.0R 청산 → 이후 조정 거쳐 +2R 초과 도달. 이 재량 판단(정체=조기
청산 신호)이 체계적으로 손해인지 확인한다. 사례 1건은 근거가 아니므로
(사용자 사전주의) 진입 체크포인트 기준 전수 시뮬레이션으로 잰다.

【정체 정의 — 사전 등록】진입 후 N일 동안 (a) 손절도 2R 목표도 미도달
AND (b) 매일 종가가 진입가 대비 ±X% 밴드 안. 둘 중 하나라도 깨지면
"정체 아님"(이미 해결됐거나 이미 추세 중)으로 그 (N,X) 조합에서 제외.
N ∈ {2,3,5}, X ∈ {2%,3%,5%} 3×3=9조합 × 3개 진입 탭(눌림목/돌파임박/
박스돌파) = 27개 조합.

【비교 대상 — 전부 같은 코호트(정체 판정이 걸린 시점)에서 분기】
  A) 즉시 청산: 정체 판정일(N일차) 종가로 청산. R_A = (close_N-entry)/risk.
  B) 계속 보유: harness.race()로 원래 진입가/손절 기준 그대로 N일차 이후
     futures를 이어서 레이스(기본 60봉) — 원래 손절/2R목표 그대로 유지.
  C) 절반청산: R_C = 0.5*R_A + 0.5*R_B(B가 데이터부족으로 None이면 C도 None).

【채택 기준 — 사전 등록, 사용자 지시 원문】
  B가 A보다 EV +0.15R 이상 & z≥1.96 & 시기 반분(전반부/후반부) 재현
  → "정체만으로 청산하지 말 것" 채택. 미달 → 현행 재량 유지, 기록만.

【통계 검정에 관한 의도적 이탈 — harness.py 미수정 이유】
A/B/C는 독립된 두 그룹이 아니라 **같은 히트에서 나온 짝지은(paired)
표본**이라 harness.ev_gap_zscore(독립 두 그룹 전용, README 규칙7)를 못
쓴다. 짝비교 z검정(diff=B-A의 평균/표준오차)을 이 파일에 로컬로만 둔다
— harness.py 확장이 정석(README 규칙3)이지만, 오늘은 다른 세션이
static/index.html·app.py를 작업 중이라 사용자 지시로 harness.py를 포함한
기존 파일은 전혀 건드리지 않기로 함(이 스크립트만 신규 생성).

【생존편향 주의(사용자 사전주의) 반영】모든 진입은 harness.checkpoints()로
과거 임의 시점에 "그때 데이터만 잘라서" 재현한 진입이고(현재 살아있는
스타 종목만 역산으로 고르는 게 아님), 정체 판정 이후의 실제 가격 경로를
그대로 이어 쓰므로 정체 후 무너진 케이스도 전수 포함된다.

방법론: 공통 하네스(harness.py) 재사용, 수정 없음(README 규칙3).
90개 체크포인트 = checkpoints(60,950,10)(README 규칙9), 확장 fetch
필요(kr_days=1900, us_period=5y, README 규칙9/harness 경고).
KR/US 분해 병기(README 규칙8).

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-02_post_entry_stall_exit_ev.py`
(눌림목+돌파임박+박스돌파 3개 탭 × 90 체크포인트 — 확장 fetch 포함 총
20~40분 예상, 네트워크 필요).
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze, CONFIG, analyze_imminent, IMMINENT_CONFIG, analyze_boxbreak, BOXBREAK_CONFIG

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — README 규칙9 표준
N_LIST = [2, 3, 5]
X_LIST = [0.02, 0.03, 0.05]
GAP_MIN_R = 0.15
Z_MIN = 1.96

RECENT_OFFSETS = set(OFFSETS[:45])   # off 60~500 — 측정기간 내 "최근"(후반부)
OLDER_OFFSETS = set(OFFSETS[45:])    # off 510~950 — 측정기간 내 "이전"(전반부)

TAB_SPECS = {
    "눌림목": (analyze, CONFIG),
    "돌파임박": (analyze_imminent, IMMINENT_CONFIG),
    "박스돌파": (analyze_boxbreak, BOXBREAK_CONFIG),
}
MIN_BARS_FLOOR = max(cfg["min_bars"] for _, cfg in TAB_SPECS.values())


# ── 1) 히트 수집 (체크포인트별, 3개 탭 동시) ────────────────────────────
def collect_hits(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    hits = {tab: [] for tab in TAB_SPECS}
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)

        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            future = harness.future_after(data[t], off)
            if len(future) < max(N_LIST):
                continue  # 정체 판정에 필요한 최소 N일치도 없음

            for tab, (fn, cfg) in TAB_SPECS.items():
                try:
                    h = fn(hist, rs_rank=rr, rs_mom=rm, cfg=cfg, is_kr=ikr)
                except Exception:
                    h = None
                if h is None or not harness.passes_liquidity_filter(h, ikr):
                    continue
                entry, stop = h.get("close"), h.get("stop")
                if entry is None or stop is None or entry <= stop:
                    continue
                hits[tab].append({
                    "ticker": t, "off": off, "is_kr": ikr,
                    "entry": entry, "stop": stop, "future": future,
                })

        print(f"[collect] off={off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
              + " ".join(f"{k}={len(v)}" for k, v in hits.items()), flush=True)
    return hits


# ── 2) 정체 판정 + A/B/C 분기 ──────────────────────────────────────────
def find_stall(h, N, X):
    """진입 후 N일간 (손절/2R목표 미해결) AND (매일 종가가 진입가±X% 밴드
    안). 둘 중 하나라도 깨지면 None(정체 아님). 반환: N일차 종가(정체
    성립 시) 또는 None."""
    entry, stop = h["entry"], h["stop"]
    risk = entry - stop
    target = entry + 2 * risk
    lo_band, hi_band = entry * (1 - X), entry * (1 + X)
    fut = h["future"]
    if len(fut) < N:
        return None
    for i in range(N):
        lo = float(fut["Low"].iloc[i])
        hi = float(fut["High"].iloc[i])
        if lo <= stop or hi >= target:
            return None  # N일 안에 이미 해결 → 정체 아님
        c = float(fut["Close"].iloc[i])
        if not (lo_band <= c <= hi_band):
            return None  # 밴드 이탈 → 이미 추세 중, 정체 아님
    return float(fut["Close"].iloc[N - 1])


def branch_outcomes(h, N, close_n):
    entry, stop = h["entry"], h["stop"]
    risk = entry - stop
    r_a = (close_n - entry) / risk
    fut_after = h["future"].iloc[N:]
    outcome_b = harness.race(entry, stop, fut_after)   # 원래 entry/stop 그대로 이어서 레이스
    r_b = outcome_b[1]
    r_c = None if r_b is None else 0.5 * r_a + 0.5 * r_b
    return r_a, outcome_b, r_b, r_c


def analyze_combo(tab_hits, N, X):
    rows = []
    for h in tab_hits:
        close_n = find_stall(h, N, X)
        if close_n is None:
            continue
        r_a, outcome_b, r_b, r_c = branch_outcomes(h, N, close_n)
        rows.append({
            "ticker": h["ticker"], "off": h["off"], "is_kr": h["is_kr"],
            "r_a": r_a, "outcome_b": outcome_b, "r_b": r_b, "r_c": r_c,
        })
    return rows


# ── 3) 집계 + 짝비교 z검정 (harness 미보유 로직, 위 docstring 참고) ─────
def continuous_summary(values):
    valid = [v for v in values if v is not None]
    n = len(valid)
    if n == 0:
        return {"n": 0, "ev_R": None}
    return {"n": n, "ev_R": round(sum(valid) / n, 4)}


def paired_zscore(diffs):
    """대응표본(같은 히트의 B-A 또는 C-A) 평균격차 z검정. 반환:
    (z, significant, mean_gap)."""
    vals = [d for d in diffs if d is not None]
    n = len(vals)
    if n < 3:
        return None, False, (sum(vals) / n if n else None)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    se = (var / n) ** 0.5
    if se == 0:
        return None, False, mean
    z = mean / se
    return round(z, 3), abs(z) >= Z_MIN, round(mean, 4)


def summarize_rows(rows):
    a_summary = continuous_summary([r["r_a"] for r in rows])
    b_summary = harness.ev_summary([r["outcome_b"] for r in rows])
    c_summary = continuous_summary([r["r_c"] for r in rows])
    z_b, sig_b, gap_b = paired_zscore(
        [(r["r_b"] - r["r_a"]) if r["r_b"] is not None else None for r in rows])
    z_c, sig_c, gap_c = paired_zscore(
        [(r["r_c"] - r["r_a"]) if r["r_c"] is not None else None for r in rows])
    kr_rows = [r for r in rows if r["is_kr"]]
    us_rows = [r for r in rows if not r["is_kr"]]
    return {
        "n_stall": len(rows),
        "A_즉시청산": a_summary,
        "B_계속보유": b_summary,   # stop_rate/target_rate = "정체 후 결과 분포"(측정②)
        "C_절반청산": c_summary,
        "B_vs_A": {"gap_R": gap_b, "z": z_b, "significant": sig_b},
        "C_vs_A": {"gap_R": gap_c, "z": z_c, "significant": sig_c},
        "KR": {"n": len(kr_rows),
               "A": continuous_summary([r["r_a"] for r in kr_rows]),
               "B": harness.ev_summary([r["outcome_b"] for r in kr_rows])},
        "US": {"n": len(us_rows),
               "A": continuous_summary([r["r_a"] for r in us_rows]),
               "B": harness.ev_summary([r["outcome_b"] for r in us_rows])},
    }


def time_split_check(rows):
    older = [r for r in rows if r["off"] in OLDER_OFFSETS]
    recent = [r for r in rows if r["off"] in RECENT_OFFSETS]
    return {
        "전반부(이전, off510~950)": summarize_rows(older) if older else None,
        "후반부(최근, off60~500)": summarize_rows(recent) if recent else None,
    }


# ── 4) 실행 ─────────────────────────────────────────────────────────
def run(data, bench, out_path=None):
    hits = collect_hits(data, bench)
    report = {"offsets": f"{OFFSETS[0]}..{OFFSETS[-1]} step10 ({len(OFFSETS)}개)", "tabs": {}}
    candidates = []

    for tab, tab_hits in hits.items():
        tab_report = {"n_hits_total": len(tab_hits), "combos": {}}
        for N in N_LIST:
            for X in X_LIST:
                key = f"N{N}_X{int(round(X*100))}pct"
                rows = analyze_combo(tab_hits, N, X)
                summary = summarize_rows(rows)
                tab_report["combos"][key] = summary
                print(f"[{tab}][{key}] n_stall={summary['n_stall']} "
                      f"A={summary['A_즉시청산']['ev_R']} B={summary['B_계속보유']['ev_R']} "
                      f"C={summary['C_절반청산']['ev_R']} gap_B={summary['B_vs_A']['gap_R']} "
                      f"z_B={summary['B_vs_A']['z']} stop_rate_B={summary['B_계속보유'].get('stop_rate')} "
                      f"target_rate_B={summary['B_계속보유'].get('target_rate')}", flush=True)
                gap_b = summary["B_vs_A"]["gap_R"]
                if gap_b is not None and gap_b >= GAP_MIN_R and summary["B_vs_A"]["significant"]:
                    candidates.append((tab, N, X, rows))
        report["tabs"][tab] = tab_report

    report["채택_후보_시기반분검증"] = {}
    if candidates:
        for tab, N, X, rows in candidates:
            key = f"{tab}_N{N}_X{int(round(X*100))}pct"
            split = time_split_check(rows)
            older = split["전반부(이전, off510~950)"]
            recent = split["후반부(최근, off60~500)"]
            older_gap = older["B_vs_A"]["gap_R"] if older else None
            recent_gap = recent["B_vs_A"]["gap_R"] if recent else None
            reproduced = (older_gap is not None and older_gap >= GAP_MIN_R and
                          recent_gap is not None and recent_gap >= GAP_MIN_R)
            verdict = "채택(정체만으로 청산하지 말 것)" if reproduced else "미달 — 현행 재량 유지, 기록만"
            report["채택_후보_시기반분검증"][key] = {**split, "verdict": verdict}
            print(f"[시기반분][{key}] 전반부gap={older_gap} 후반부gap={recent_gap} → {verdict}", flush=True)
    else:
        print("[결론] 사전 판정 기준(gap_B>=0.15R & z_B>=1.96) 충족 조합 0건 "
              "→ 27개 조합 전부 현행 재량 유지(기록만)", flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(
        kr_days=1900, us_period="5y", validate_offsets=OFFSETS)
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-09-02_post_entry_stall_exit_ev.results.json")
    run(data, bench, out_path=out)

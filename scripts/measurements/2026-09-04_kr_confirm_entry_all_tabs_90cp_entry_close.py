"""
KR+US 확인진입(안C) EV — 진입가를 피벗(신호일고가)에서 확인일 실제
종가로 바꾼 재측정 (2026-09-04, 사용자 지시 [B]). 조사 전용 — 커밋은
하되 결과 파일(.results.json)은 gitignore 대상, 원본 스크립트
(`2026-09-04_kr_confirm_entry_all_tabs_90cp.py`) 무변경(README 규칙3).

【배경】
app.py "오늘의 결정" 확인(🔴) 카드가 [A]에서 진입가를 피벗→확인일 종가로
바꿨다(화면에 뜨는 진입가가 실제로 살 수 있는 가격이어야 한다는 지적).
그런데 `CONFIRM_RULE_BY_TAB`이 인용하는 EV 수치는 전부 "피벗 지정가
체결" 가정(신호일 고가 레벨에서 확인일에 체결됐다고 가정)으로 측정된
값이라 화면 표시와 실제 EV 근거가 어긋나는 과도기 상태다. 이 스크립트가
그 간극("확인 대기 비용")을 실측한다.

【변경점 — 원본과 딱 하나】
`find_confirm_close()`가 확인일의 실제 종가(`confirm_close`)도 같이
반환하도록 확장하고, `run_confirm_analysis()`가 안C 레이스의 entry를
`trigger_price`(피벗) 대신 `confirm_close`로 바꾼 버전을 **추가로**
계산한다. 그 외 전부 원본과 동일:
- stop 정의: 탭별 원본 그대로(돌파임박=signal_low, 나머지=구조적 stop)
- 레이스 시작일: 확인일 다음 봉부터(`future.iloc[k:]`) 불변
- base_vol50 정의: nonzero_vol_mean, 신호일 고정 — v5.176 통일 그대로
- 확인조건 자체("종가가 신호일 고가 초과 + 거래량 배수 이상")도 불변
  (entry 가격 정의만 바뀌었지 "확인됐다"의 판정 기준은 그대로)

**슬리피지**: 종가진입 변형에 +0.3%(체결가를 그만큼 더 밀어냄)를 추가로
병기 — stop은 불변.

**사전등록(채택 기준, 기존과 동일)**: KR 안C EV>=+0.15R & z>=1.96 &
시기반분(전반/후반) 둘 다 재현 → "KR 유효". 이 채택 판정은 **종가진입
기준**(화면에 실제로 뜨는 정의)으로 내린다 — 피벗진입 결과는 비교
참고용으로 나란히 싣는다. 두 값의 차이(피벗EV - 종가EV)를 "확인 대기
비용"으로 표에 명시한다 — 확인을 기다리는 동안 가격이 이미 올라간 만큼
실제로 손해 보는 R.

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_close.py`
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import (
    analyze, CONFIG,
    analyze_imminent, IMMINENT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_turnaround, TURN_CONFIG,
    nonzero_vol_mean,
)

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — 규칙9 표준
MIN_N_FOR_JUDGMENT = 100   # 사용자 지시: n<100은 판정불가
EV_GAP_THRESHOLD = 0.15    # R, 사전 등록
CONFIRM_K_MAX = 3          # 확인 탐색 최대 봉수(기존 스크립트와 동일)
CONFIRM_VOL_MULT = 1.5
SLIPPAGE_PCT = 0.003       # [B] 사용자 지시: +0.3%도 병기

TABS = {
    "눌림목": {"fn": analyze, "cfg": CONFIG, "stop_key": "stop"},
    "돌파임박": {"fn": analyze_imminent, "cfg": IMMINENT_CONFIG, "stop_key": "signal_low"},
    "박스돌파": {"fn": analyze_boxbreak, "cfg": BOXBREAK_CONFIG, "stop_key": "stop"},
    "돌파": {"fn": analyze_breakout, "cfg": BREAKOUT_CONFIG, "stop_key": "stop"},
    "추세전환": {"fn": analyze_turnaround, "cfg": TURN_CONFIG, "stop_key": "stop"},
}
NEED_BARS = max(t["cfg"]["min_bars"] for t in TABS.values())


# ══════════════════════════════════════════════════════════════════
# 1단계: 5개 탭 히트 수집 (KR+US 한 번의 체크포인트 순회로 동시 수집)
# — 원본 collect_hits()와 완전히 동일(무변경 복붙)
# ══════════════════════════════════════════════════════════════════
def collect_hits(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    hits = {name: [] for name in TABS}

    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)

        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < NEED_BARS:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            future = harness.future_after(data[t], off)
            signal_high = float(hist["High"].iloc[-1])
            signal_low = float(hist["Low"].iloc[-1])
            trailing50_vol = float(nonzero_vol_mean(hist["Volume"].iloc[-50:]))

            for name, spec in TABS.items():
                try:
                    r = spec["fn"](hist, rs_rank=rr, rs_mom=rm, cfg=spec["cfg"], is_kr=ikr)
                except Exception:
                    r = None
                if r is None or not harness.passes_liquidity_filter(r, ikr):
                    continue
                hits[name].append({
                    "ticker": t, "off": off, "is_kr": ikr,
                    "close": r.get("close"), "stop": r.get("stop"),
                    "signal_high": signal_high, "signal_low": signal_low,
                    "trailing50_vol": trailing50_vol, "future": future,
                })

        counts = {k: len(v) for k, v in hits.items()}
        print(f"[PASS1] offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s {counts}",
              flush=True)
    return hits


# ══════════════════════════════════════════════════════════════════
# 2단계: 확인조건 탐색 — 원본과 판정 기준(조건) 동일, 반환값에
# confirm_close(확인일 실제 종가)만 추가
# ══════════════════════════════════════════════════════════════════
_lookahead_checks = 0


def find_confirm_close(h, k_max=CONFIRM_K_MAX):
    """확인조건은 원본과 완전히 동일: 다음 최대 k_max거래일 내 종가가
    신호일 고가 초과 + 거래량이 base_vol50(신호일 고정, nonzero_vol_mean)
    의 1.5배 이상. **변경점은 반환값 하나뿐** — 원본은 (k, trigger)만
    반환했는데 여기선 확인일 실제 종가 `confirm_close`도 같이 반환해
    run_confirm_analysis()가 진입가로 pivot 대신 이 값을 쓸 수 있게
    한다. base_vol>0 가드(v5.176 버그수정) 그대로 유지."""
    global _lookahead_checks
    fut = h["future"]
    trigger = h["signal_high"]
    base_vol = h["trailing50_vol"]
    avail = min(k_max, len(fut))
    for k in range(1, avail + 1):
        assert 1 <= k <= k_max, f"lookahead index violation: k={k}"
        assert k - 1 < len(fut), f"confirm day beyond available future: k={k}, len(fut)={len(fut)}"
        _lookahead_checks += 1
        c = float(fut["Close"].iloc[k - 1])
        vv = float(fut["Volume"].iloc[k - 1])
        if c > trigger and base_vol > 0 and vv >= CONFIRM_VOL_MULT * base_vol:
            return k, trigger, c
    return None


# ══════════════════════════════════════════════════════════════════
# 3단계: 안A/안C(피벗진입) + 안C(종가진입, +슬리피지) 나란히 계산
# ══════════════════════════════════════════════════════════════════
def run_confirm_analysis(hit_list, stop_key):
    a_outcomes = []
    c_pivot_outcomes, c_close_outcomes, c_close_slip_outcomes = [], [], []
    hybrid_pivot_outcomes, hybrid_close_outcomes = [], []
    n_confirmed = 0
    for h in hit_list:
        a_out = harness.race(h["close"], h["stop"], h["future"])
        a_outcomes.append(a_out)
        conf = find_confirm_close(h)
        if conf is None:
            hybrid_pivot_outcomes.append(a_out)
            hybrid_close_outcomes.append(a_out)
            continue
        k, trigger_price, confirm_close = conf
        n_confirmed += 1
        fut_after = h["future"].iloc[k:]
        stop_val = h[stop_key]

        pivot_out = harness.race(trigger_price, stop_val, fut_after)
        close_out = harness.race(confirm_close, stop_val, fut_after)
        close_slip_out = harness.race(confirm_close * (1 + SLIPPAGE_PCT), stop_val, fut_after)

        c_pivot_outcomes.append(pivot_out)
        c_close_outcomes.append(close_out)
        c_close_slip_outcomes.append(close_slip_out)
        hybrid_pivot_outcomes.append(pivot_out)
        hybrid_close_outcomes.append(close_out)

    ev_a = harness.ev_summary(a_outcomes)
    ev_c_pivot = harness.ev_summary(c_pivot_outcomes)
    ev_c_close = harness.ev_summary(c_close_outcomes)
    ev_c_close_slip = harness.ev_summary(c_close_slip_outcomes)

    z_pivot, sig_pivot = harness.ev_gap_zscore(ev_a, ev_c_pivot)
    z_close, sig_close = harness.ev_gap_zscore(ev_a, ev_c_close)
    gap_pivot = (ev_c_pivot["ev_R"] - ev_a["ev_R"]) if (ev_c_pivot["ev_R"] is not None and ev_a["ev_R"] is not None) else None
    gap_close = (ev_c_close["ev_R"] - ev_a["ev_R"]) if (ev_c_close["ev_R"] is not None and ev_a["ev_R"] is not None) else None
    wait_cost = (ev_c_pivot["ev_R"] - ev_c_close["ev_R"]) if (ev_c_pivot["ev_R"] is not None and ev_c_close["ev_R"] is not None) else None

    return {
        "n_hits_total": len(hit_list),
        "안A_전체": ev_a,
        "안C_피벗진입": ev_c_pivot,
        "안C_종가진입": ev_c_close,
        "안C_종가진입_슬리피지0.3%": ev_c_close_slip,
        "하이브리드_피벗진입(미확인=안A로 대체)": harness.ev_summary(hybrid_pivot_outcomes),
        "하이브리드_종가진입(미확인=안A로 대체)": harness.ev_summary(hybrid_close_outcomes),
        "확인율": round(n_confirmed / len(hit_list), 4) if hit_list else None,
        "안C_피벗진입_vs_안A_gap_R": gap_pivot,
        "안C_피벗진입_vs_안A_z": z_pivot,
        "안C_종가진입_vs_안A_gap_R": gap_close,
        "안C_종가진입_vs_안A_z": z_close,
        "안C_종가진입_significant": sig_close,
        "확인_대기_비용(피벗EV-종가EV)": wait_cost,
    }


def half_split(hit_list, stop_key):
    """시기 반분 재현 — 체크포인트 중앙값 기준 전반(최근)/후반(과거)."""
    mid_off = OFFSETS[len(OFFSETS) // 2]
    early = [h for h in hit_list if h["off"] <= mid_off]
    late = [h for h in hit_list if h["off"] > mid_off]
    return {
        "초반(최근시점)": run_confirm_analysis(early, stop_key) if len(early) >= MIN_N_FOR_JUDGMENT else {"note": f"표본부족(n={len(early)})"},
        "후반(과거시점)": run_confirm_analysis(late, stop_key) if len(late) >= MIN_N_FOR_JUDGMENT else {"note": f"표본부족(n={len(late)})"},
    }


def judge(kr_result, kr_half):
    """[B] 사용자 지시: 채택 판정은 종가진입 기준(화면 실제 정의)으로."""
    n = kr_result["안C_종가진입"]["nv"] if kr_result else 0
    if n < MIN_N_FOR_JUDGMENT:
        return f"판정불가(n={n} < {MIN_N_FOR_JUDGMENT})"
    gap = kr_result["안C_종가진입_vs_안A_gap_R"]
    sig = kr_result["안C_종가진입_significant"]
    ev_c = kr_result["안C_종가진입"]["ev_R"]
    if ev_c is None or gap is None:
        return "판정불가(EV 계산 불가)"
    if ev_c < EV_GAP_THRESHOLD or not sig:
        return f"KR 무효(종가진입 안C EV={ev_c:+.3f}R, gap={gap:+.3f}R, z={kr_result['안C_종가진입_vs_안A_z']}) — 기준(EV>=+0.15R & z>=1.96) 미달"
    early = kr_half.get("초반(최근시점)", {})
    late = kr_half.get("후반(과거시점)", {})
    early_ok = isinstance(early, dict) and early.get("안C_종가진입", {}).get("ev_R") is not None and early["안C_종가진입"]["ev_R"] >= EV_GAP_THRESHOLD
    late_ok = isinstance(late, dict) and late.get("안C_종가진입", {}).get("ev_R") is not None and late["안C_종가진입"]["ev_R"] >= EV_GAP_THRESHOLD
    if early_ok and late_ok:
        return f"KR 유효(종가진입 안C EV={ev_c:+.3f}R, z={kr_result['안C_종가진입_vs_안A_z']:.2f}, 시기반분 재현)"
    return f"KR 무효(종가진입 안C EV 기준은 통과했으나 시기반분 재현 실패 — 초반 ok={early_ok}, 후반 ok={late_ok})"


def run(data, bench, out_path=None):
    hits = collect_hits(data, bench)

    report = {"offsets": f"{OFFSETS[0]}..{OFFSETS[-1]} step10 ({len(OFFSETS)}개)",
              "slippage_pct_checked": SLIPPAGE_PCT,
              "lookahead_checks_passed": None, "tabs": {}}

    for name, spec in TABS.items():
        stop_key = spec["stop_key"]
        hit_list = hits[name]
        kr_hits = [h for h in hit_list if h["is_kr"]]
        us_hits = [h for h in hit_list if not h["is_kr"]]

        kr_result = run_confirm_analysis(kr_hits, stop_key) if kr_hits else None
        us_result = run_confirm_analysis(us_hits, stop_key) if us_hits else None
        kr_half = half_split(kr_hits, stop_key) if kr_hits else {}

        verdict = judge(kr_result, kr_half)
        report["tabs"][name] = {
            "stop_key_used": stop_key,
            "n_total": len(hit_list), "n_kr": len(kr_hits), "n_us": len(us_hits),
            "KR": kr_result, "KR_시기반분": kr_half, "US": us_result,
            "판정": verdict,
        }
        print(f"[결과] {name}: {verdict}", flush=True)
        if kr_result:
            print(f"       KR n={len(kr_hits)} 안A={kr_result['안A_전체']['ev_R']} "
                  f"안C_피벗={kr_result['안C_피벗진입']['ev_R']} 안C_종가={kr_result['안C_종가진입']['ev_R']} "
                  f"확인대기비용={kr_result['확인_대기_비용(피벗EV-종가EV)']} "
                  f"z_종가={kr_result['안C_종가진입_vs_안A_z']}", flush=True)
        if us_result:
            print(f"       US n={len(us_hits)} 안C_피벗={us_result['안C_피벗진입']['ev_R']} "
                  f"안C_종가={us_result['안C_종가진입']['ev_R']} "
                  f"확인대기비용={us_result['확인_대기_비용(피벗EV-종가EV)']}", flush=True)

    report["lookahead_checks_passed"] = _lookahead_checks
    print(f"[검증] 룩어헤드 assert 통과 {_lookahead_checks}건", flush=True)

    all_invalid = all("무효" in report["tabs"][n]["판정"] or "판정불가" in report["tabs"][n]["판정"] for n in TABS)
    if all_invalid:
        print("[종합] 전 탭 무효/판정불가(종가진입 기준)", flush=True)
        report["종합"] = "전 탭 무효/판정불가(종가진입 기준)"
    else:
        valid_tabs = [n for n in TABS if "KR 유효" in report["tabs"][n]["판정"]]
        report["종합"] = f"KR 유효 탭(종가진입 기준): {valid_tabs}" if valid_tabs else "유효 탭 없음(무효/판정불가 혼재)"
        print(f"[종합] {report['종합']}", flush=True)

    if out_path:
        def _default(o):
            return str(o)
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=_default)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(kr_days=1900, us_period="5y", validate_offsets=OFFSETS)
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_close.results.json")
    run(data, bench, out_path=out)

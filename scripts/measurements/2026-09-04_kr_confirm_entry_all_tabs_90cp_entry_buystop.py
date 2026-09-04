"""
KR+US 확인진입 안D(피벗 buy-stop 체결) 사전등록 측정 + 안C'(종가진입+
확인일저가손절) 대안 변형, 나란히 (2026-09-04, 사용자 지시 [B] 후속).
조사 전용 — 결과 파일(.results.json)은 gitignore 대상, 스크립트는 커밋.

【배경】
"⑥ 종가진입 재측정" 절(`docs/kr_us_strategy_map.md`)에서 안C(종가진입,
확인일 실제 종가로 체결)로 재측정하니 5탭 중 4탭이 채택 기준 미달로
무너졌다 — "확인 대기 비용"(확인을 기다리는 동안 가격이 먼저 뛴 만큼)이
EV 대부분을 먹었기 때문. 이게 "확인진입 자체가 엣지 없음"을 뜻하는지,
아니면 "종가까지 기다리는 진입 방식"의 문제인지를 가르기 위해 진입
방식을 하나 더 시도한다.

【안D — 피벗 buy-stop 체결 (주 가설)】
- 신호 후 최대 3거래일 내 **첫 고가 ≥ 피벗(신호일고가)인 날 = 체결일**,
  진입가 = 피벗 그대로(buy-stop 주문 가정 — 시장가/지정가 혼합이 아니라
  "그 가격에 도달하면 즉시 체결"). 거래량·종가 조건 없음 — 체결
  시점(장중)엔 그날 종가가 얼마일지, 거래량이 얼마나 실릴지 알 수 없는
  정보이므로 안C의 확인조건을 그대로 못 가져온다.
- **체결일 저가 ≤ 손절이면 그날 바로 손절 처리** — 장중에 체결과 손절
  중 어느 게 먼저 왔는지 알 방법이 없어 보수적으로(손절이 이겼다고
  가정) 처리. `harness.race()`가 진입일부터 레이스를 시작하면(future_df
  의 0번째 봉이 체결일 자체) 이 규칙을 그대로 만족한다(저가≤손절 우선
  체크가 이미 구현돼 있음, README/harness.py 참고) — 별도 구현 불필요.
- **레이스는 체결일부터**(안C 계열의 "확인일 다음날부터"와 다름 —
  체결일 자체가 진입일이므로 그날 가격 흐름부터 레이스 대상).
- **실패 케이스(체결은 됐는데 그날 종가가 피벗 밑으로 되밀린 경우) 전부
  표본 포함** — 종가 조건이 아예 없으므로 자동으로 포함됨(별도 필터
  없음).
- 손절 = 신호 스냅샷 stop(탭별 원 정의: 돌파임박=signal_low, 나머지=
  구조적 stop, base_vol50/구조 재정의 없음). 슬리피지 +0.3% 병기.

【안C' — 종가진입 + 확인일 저가로 손절 당김 (대안, 안D 실패 시 대비)】
- 진입가는 안C와 동일(확인일 실제 종가). **손절만** 신호일저가/구조적
  stop 대신 **확인일(그날) 저가**로 당긴다 — 확인이 됐다는 건 그날
  가격이 신호일고가 위에서 강하게 논다는 뜻이니, 손절을 신호일 대신
  확인일 구조로 다시 잡으면 리스크폭(R의 분모)이 좁아져 R 단위 EV가
  달라질 수 있다는 가설. 확인 조건 자체(종가>신호일고가+거래량배수)는
  안C와 동일 — 이 조건을 만족한 히트에서만 정의된다(안D처럼 별도
  모집단이 아님).
- 레이스는 안C와 동일하게 확인일 **다음날부터**(진입가가 이미 확인일
  종가이므로 그날은 진입 완료 상태).

**사전등록(채택 기준, 기존과 동일)**: EV≥+0.15R & z≥1.96 & 시기반분
(전반/후반) 둘 다 재현. 안D를 주 가설로 판정하고, 안C'는 참고
비교치(사용자 지시: "이건 안D가 실패했을 때의 대안이지 주 가설은
아님")로만 기록 — 자동 채택 판정 대상 아님. dedup z(티커별 최초 확인만
1건, `2026-09-04_..._checks.py`의 `_dedup_first` 재사용)도 안D에 병기.

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_buystop.py`
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
MIN_N_FOR_JUDGMENT = 100
EV_GAP_THRESHOLD = 0.15
CONFIRM_K_MAX = 3
CONFIRM_VOL_MULT = 1.5
SLIPPAGE_PCT = 0.003

TABS = {
    "눌림목": {"fn": analyze, "cfg": CONFIG, "stop_key": "stop"},
    "돌파임박": {"fn": analyze_imminent, "cfg": IMMINENT_CONFIG, "stop_key": "signal_low"},
    "박스돌파": {"fn": analyze_boxbreak, "cfg": BOXBREAK_CONFIG, "stop_key": "stop"},
    "돌파": {"fn": analyze_breakout, "cfg": BREAKOUT_CONFIG, "stop_key": "stop"},
    "추세전환": {"fn": analyze_turnaround, "cfg": TURN_CONFIG, "stop_key": "stop"},
}
NEED_BARS = max(t["cfg"]["min_bars"] for t in TABS.values())


# ══════════════════════════════════════════════════════════════════
# 1단계: 히트 수집 — signal_date 추가(dedup용) 외엔 원본과 동일
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
            signal_date = str(hist.index[-1].date())
            trailing50_vol = float(nonzero_vol_mean(hist["Volume"].iloc[-50:]))

            for name, spec in TABS.items():
                try:
                    r = spec["fn"](hist, rs_rank=rr, rs_mom=rm, cfg=spec["cfg"], is_kr=ikr)
                except Exception:
                    r = None
                if r is None or not harness.passes_liquidity_filter(r, ikr):
                    continue
                hits[name].append({
                    "ticker": t, "off": off, "is_kr": ikr, "signal_date": signal_date,
                    "close": r.get("close"), "stop": r.get("stop"),
                    "signal_high": signal_high, "signal_low": signal_low,
                    "trailing50_vol": trailing50_vol, "future": future,
                })

        counts = {k: len(v) for k, v in hits.items()}
        print(f"[PASS1] offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s {counts}",
              flush=True)
    return hits


# ══════════════════════════════════════════════════════════════════
# 2단계: 확인조건(안C) + buy-stop 체결(안D) 탐색
# ══════════════════════════════════════════════════════════════════
_lookahead_checks = 0


def find_confirm_close(h, k_max=CONFIRM_K_MAX):
    """안C 확인조건 — 원본과 완전히 동일(종가>신호일고가 + 거래량배수,
    base_vol50 신호일 고정+nonzero_vol_mean, base_vol>0 가드)."""
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


def find_buystop_fill(h, k_max=CONFIRM_K_MAX):
    """안D — 신호 후 최대 k_max거래일 내 첫 고가>=피벗인 날 = 체결일.
    거래량/종가 조건 없음(체결 시점엔 아직 모르는 정보). 반환: k(1-based,
    fut.iloc[k-1]이 체결일) 또는 None."""
    global _lookahead_checks
    fut = h["future"]
    trigger = h["signal_high"]
    avail = min(k_max, len(fut))
    for k in range(1, avail + 1):
        assert 1 <= k <= k_max, f"lookahead index violation: k={k}"
        assert k - 1 < len(fut), f"buystop fill beyond available future: k={k}, len(fut)={len(fut)}"
        _lookahead_checks += 1
        hi = float(fut["High"].iloc[k - 1])
        if hi >= trigger:
            return k
    return None


# ══════════════════════════════════════════════════════════════════
# 3단계: 안A + 안C(피벗/종가) + 안D(buystop) + 안C'(종가+확인일저가손절)
# ══════════════════════════════════════════════════════════════════
def run_variants(hit_list, stop_key):
    a_labeled = []
    c_pivot, c_close = [], []
    d_labeled, d_slip_labeled = [], []
    cprime_labeled = []
    n_confirmed = 0
    n_filled = 0

    for h in hit_list:
        a_out = harness.race(h["close"], h["stop"], h["future"])
        a_labeled.append((h["ticker"], h["off"], h["signal_date"], a_out))

        # ── 안C 계열(피벗/종가진입, 확인일 다음날부터 레이스) ──
        conf = find_confirm_close(h)
        if conf is not None:
            k_c, trigger, confirm_close = conf
            n_confirmed += 1
            fut_after = h["future"].iloc[k_c:]
            stop_val = h[stop_key]
            c_pivot.append(harness.race(trigger, stop_val, fut_after))
            c_close.append(harness.race(confirm_close, stop_val, fut_after))
            # 안C': 손절을 확인일(그날) 저가로 당김
            confirm_day_low = float(h["future"]["Low"].iloc[k_c - 1])
            cprime_out = harness.race(confirm_close, confirm_day_low, fut_after)
            cprime_labeled.append((h["ticker"], h["off"], h["signal_date"], cprime_out))

        # ── 안D(buy-stop, 체결일부터 레이스 — 체결일 자체가 0번째 봉) ──
        fill_k = find_buystop_fill(h)
        if fill_k is not None:
            n_filled += 1
            fut_from_fill = h["future"].iloc[fill_k - 1:]
            stop_val = h[stop_key]
            d_out = harness.race(h["signal_high"], stop_val, fut_from_fill)
            d_slip_out = harness.race(h["signal_high"] * (1 + SLIPPAGE_PCT), stop_val, fut_from_fill)
            d_labeled.append((h["ticker"], h["off"], h["signal_date"], d_out))
            d_slip_labeled.append((h["ticker"], h["off"], h["signal_date"], d_slip_out))

    def _dedup_first(labeled):
        """티커별 signal_date 최솟값(가장 이른 시점) 1건만 남김 — checks.py와 동일 기법."""
        best = {}
        for ticker, off, sdate, out in labeled:
            cur = best.get(ticker)
            if cur is None or sdate < cur[0]:
                best[ticker] = (sdate, out)
        return [v[1] for v in best.values()]

    ev_a = harness.ev_summary([o[3] for o in a_labeled])
    ev_c_pivot = harness.ev_summary(c_pivot)
    ev_c_close = harness.ev_summary(c_close)
    ev_d = harness.ev_summary([o[3] for o in d_labeled])
    ev_d_slip = harness.ev_summary([o[3] for o in d_slip_labeled])
    ev_cprime = harness.ev_summary([o[3] for o in cprime_labeled])

    z_d, sig_d = harness.ev_gap_zscore(ev_a, ev_d)
    z_cprime, sig_cprime = harness.ev_gap_zscore(ev_a, ev_cprime)

    d_dedup = _dedup_first(d_labeled)
    ev_d_dedup = harness.ev_summary(d_dedup)
    a_dedup_for_d = _dedup_first(a_labeled)
    ev_a_dedup_for_d = harness.ev_summary(a_dedup_for_d)
    z_d_dedup, sig_d_dedup = harness.ev_gap_zscore(ev_a_dedup_for_d, ev_d_dedup)

    return {
        "n_hits_total": len(hit_list),
        "n_confirmed(안C계열 모집단)": n_confirmed,
        "n_filled(안D 모집단)": n_filled,
        "안A_전체": ev_a,
        "안C_피벗진입": ev_c_pivot,
        "안C_종가진입": ev_c_close,
        "안D_buystop": ev_d,
        "안D_buystop_슬리피지0.3%": ev_d_slip,
        "안D_vs_안A_z": z_d,
        "안D_vs_안A_significant": sig_d,
        "안D_dedup": ev_d_dedup,
        "안D_vs_안A_z_dedup": z_d_dedup,
        "안C'_종가진입_확인일저가손절": ev_cprime,
        "안C'_vs_안A_z": z_cprime,
        "체결율(안D)": round(n_filled / len(hit_list), 4) if hit_list else None,
        "확인율(안C)": round(n_confirmed / len(hit_list), 4) if hit_list else None,
    }


def half_split(hit_list, stop_key):
    mid_off = OFFSETS[len(OFFSETS) // 2]
    early = [h for h in hit_list if h["off"] <= mid_off]
    late = [h for h in hit_list if h["off"] > mid_off]
    return {
        "초반(최근시점)": run_variants(early, stop_key) if len(early) >= MIN_N_FOR_JUDGMENT else {"note": f"표본부족(n={len(early)})"},
        "후반(과거시점)": run_variants(late, stop_key) if len(late) >= MIN_N_FOR_JUDGMENT else {"note": f"표본부족(n={len(late)})"},
    }


def judge_d(kr_result, kr_half):
    """안D(주 가설) 채택 판정."""
    n = kr_result["안D_buystop"]["nv"] if kr_result else 0
    if n < MIN_N_FOR_JUDGMENT:
        return f"판정불가(n={n} < {MIN_N_FOR_JUDGMENT})"
    ev_d = kr_result["안D_buystop"]["ev_R"]
    sig = kr_result["안D_vs_안A_significant"]
    if ev_d is None:
        return "판정불가(EV 계산 불가)"
    if ev_d < EV_GAP_THRESHOLD or not sig:
        return f"KR 무효(안D EV={ev_d:+.3f}R, z={kr_result['안D_vs_안A_z']}) — 기준(EV>=+0.15R & z>=1.96) 미달"
    early = kr_half.get("초반(최근시점)", {})
    late = kr_half.get("후반(과거시점)", {})
    early_ok = isinstance(early, dict) and early.get("안D_buystop", {}).get("ev_R") is not None and early["안D_buystop"]["ev_R"] >= EV_GAP_THRESHOLD
    late_ok = isinstance(late, dict) and late.get("안D_buystop", {}).get("ev_R") is not None and late["안D_buystop"]["ev_R"] >= EV_GAP_THRESHOLD
    if early_ok and late_ok:
        return f"KR 유효(안D EV={ev_d:+.3f}R, z={kr_result['안D_vs_안A_z']:.2f}, 시기반분 재현)"
    return f"KR 무효(안D EV 기준은 통과했으나 시기반분 재현 실패 — 초반 ok={early_ok}, 후반 ok={late_ok})"


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

        kr_result = run_variants(kr_hits, stop_key) if kr_hits else None
        us_result = run_variants(us_hits, stop_key) if us_hits else None
        kr_half = half_split(kr_hits, stop_key) if kr_hits else {}

        verdict_d = judge_d(kr_result, kr_half)
        report["tabs"][name] = {
            "stop_key_used": stop_key,
            "n_total": len(hit_list), "n_kr": len(kr_hits), "n_us": len(us_hits),
            "KR": kr_result, "KR_시기반분": kr_half, "US": us_result,
            "판정_안D": verdict_d,
        }
        print(f"[결과] {name}: {verdict_d}", flush=True)
        cprime_key = "안C'_종가진입_확인일저가손절"
        if kr_result:
            kr_cprime_ev = kr_result[cprime_key]['ev_R']
            print(f"       KR n={len(kr_hits)} 안A={kr_result['안A_전체']['ev_R']} "
                  f"안C_피벗={kr_result['안C_피벗진입']['ev_R']} 안C_종가={kr_result['안C_종가진입']['ev_R']} "
                  f"안D={kr_result['안D_buystop']['ev_R']}(체결율={kr_result['체결율(안D)']}) "
                  f"z_D={kr_result['안D_vs_안A_z']} z_D_dedup={kr_result['안D_vs_안A_z_dedup']} "
                  f"안C'={kr_cprime_ev}", flush=True)
        if us_result:
            us_cprime_ev = us_result[cprime_key]['ev_R']
            print(f"       US n={len(us_hits)} 안D={us_result['안D_buystop']['ev_R']} "
                  f"안C'={us_cprime_ev}", flush=True)

    report["lookahead_checks_passed"] = _lookahead_checks
    print(f"[검증] 룩어헤드 assert 통과 {_lookahead_checks}건", flush=True)

    valid_tabs_d = [n for n in TABS if "KR 유효" in report["tabs"][n]["판정_안D"]]
    report["종합_안D"] = f"KR 유효 탭(안D 기준): {valid_tabs_d}" if valid_tabs_d else "안D 유효 탭 없음"
    print(f"[종합] {report['종합_안D']}", flush=True)

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
                        "2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_buystop.results.json")
    run(data, bench, out_path=out)

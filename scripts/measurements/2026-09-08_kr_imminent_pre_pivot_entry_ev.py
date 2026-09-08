"""
KR 돌파임박 — 피벗 대비 거리별 선진입(신호일 종가) EV (2026-09-08,
사용자 지시 — 실행). 사전 등록: docs/kr_us_strategy_map.md "사전 등록
— KR 돌파임박 피벗 대비 거리별 선진입 EV" 절. **그 절의 정의·구간·
판정식·대조군 대체 규칙을 그대로 따르고, 등록 후 어떤 조건도 바꾸지
않는다.**

【정의】(등록 그대로)
  피벗 대비 거리 = (신호일 종가 − 피벗) / ATR14, 신호일 기준.
  진입 = 히트 발생일 종가(선진입, 확인 대기 없음, 히트 100% 진입).
  손절 = 스냅샷 stop(신호일 저가) — 기존 종가진입(확인 후)과 동일 정의.
【구간】(등록 그대로) −1.5이하(참고,판정제외) / −1.5~−1.0 / −1.0~−0.5 /
  −0.5~0 / 0~0.5.
  주의: analyze_imminent()의 게이트 자체가 근접 조건 near_max=0.0(피벗
  이하만 통과, IMMINENT_CONFIG 참고)이라 "0~0.5"(피벗 위) 버킷은
  구조적으로 n=0이 나온다 — 등록된 "0~0.5 n<100이면 −0.5~0으로 대체"
  규칙이 여기서 그대로 발동한다(사후 선택 아님, 등록 시점에 이미
  정해둔 대체 규칙).
【판정】(등록 그대로) −1.0~−0.5 EV − 대조군 EV ≥ +0.15R & z≥1.96
  (harness.ev_gap_zscore) & 시기반분(초반/후반) 둘 다 재현 & 각 구간
  n≥100. 부호 반대면 미달.

【이번 실행 지시사항 추가 반영】
  - 90개 체크포인트(checkpoints(60,950,10)), harness.py 재사용.
  - 룩어헤드 assert — truncate 일치 검증 + future가 hist보다 반드시
    미래인지 매 히트마다 assert, confirm-entry 탐색도 원본
    find_confirm_close()와 동일한 인덱스 assert 유지.
  - "선진입 손절 후 종가진입 제외" 규칙 적용(같은 종목 중복 계상
    방지): 등록된 "히트당 총 기대 R" 계산 자체(0.157R×15.6%=0.024R,
    외부 참조 상수)는 바꾸지 않되, 이 스크립트가 수집한 선진입 표본
    안에서 "확인 조건(안C, find_confirm_close — 2026-09-04_kr_
    confirm_entry_all_tabs_90cp_entry_close.py와 동일 조건, 재구현
    아니라 그대로 복붙 — README 규칙3)을 만족하는 히트 중, 선진입이
    그 확인일보다 먼저 손절된 비율"을 추가로 계산해 별도 정보란에
    병기한다(판정에는 영향 없음 — 판정은 등록된 식으로만).
  - 히트별 원자료를 JSON에 저장(재fetch 없이 재확인 가능).

실행: 리포 루트에서
  python3 scripts/measurements/2026-09-08_kr_imminent_pre_pivot_entry_ev.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import json
import time

import harness
import scanner
from scanner import analyze_imminent, IMMINENT_CONFIG, nonzero_vol_mean

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — README 규칙9 표준
KR_FETCH_DAYS = 1900
EV_GAP_THRESHOLD = 0.15
MIN_N_FOR_JUDGMENT = 100
CONFIRM_K_MAX = 3           # 2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_close.py와 동일
CONFIRM_VOL_MULT = 1.5      # 위와 동일
CONFIRM_EV_REF = 0.157      # KR 돌파임박 확인진입(종가) EV — "⑥ 종가진입 재측정" 절, 외부 참조 상수(재계산 안 함)
CONFIRM_RATE_REF = 0.156    # KR 돌파임박 확인율 — "재검증 결과 — 우선순위5" 절, 외부 참조 상수(재계산 안 함)

BUCKET_EDGES = [
    ("-1.5 이하", float("-inf"), -1.5),
    ("-1.5~-1.0", -1.5, -1.0),
    ("-1.0~-0.5", -1.0, -0.5),
    ("-0.5~0", -0.5, 0.0),
    ("0~0.5", 0.0, 0.5),
]


def bucket_of(dist):
    for label, lo, hi in BUCKET_EDGES:
        if lo < dist <= hi or (lo == float("-inf") and dist <= hi):
            return label
    return None   # 등록 범위 밖(0.5 초과) — 대상 아님


# find_confirm_close(): 2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_close.py의
# 정의를 그대로 복붙(재구현 아님, README 규칙3) — 확인조건: 다음 최대
# CONFIRM_K_MAX거래일 내 종가가 신호일 고가 초과 + 거래량이 신호일
# 고정 base_vol50(nonzero_vol_mean)의 CONFIRM_VOL_MULT배 이상.
def find_confirm_close(h, k_max=CONFIRM_K_MAX):
    fut = h["future"]
    trigger = h["signal_high"]
    base_vol = h["trailing50_vol"]
    avail = min(k_max, len(fut))
    for k in range(1, avail + 1):
        assert 1 <= k <= k_max, f"lookahead index violation: k={k}"
        assert k - 1 < len(fut), f"confirm day beyond available future: k={k}, len(fut)={len(fut)}"
        c = float(fut["Close"].iloc[k - 1])
        vv = float(fut["Volume"].iloc[k - 1])
        if c > trigger and base_vol > 0 and vv >= CONFIRM_VOL_MULT * base_vol:
            return k, trigger, c
    return None


def collect_hits(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    rows = []
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < IMMINENT_CONFIG["min_bars"]:
                continue
            hist = harness.truncate_at(df, off)
            assert len(hist) == len(df) - off, "truncate 불일치 — 룩어헤드 위험(hist가 미래를 포함할 수 있음)"
            trunc_cache[t] = hist
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            df_full = data[t]
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            try:
                hit = analyze_imminent(hist, rs_rank=rr, rs_mom=rm, cfg=IMMINENT_CONFIG, is_kr=True)
            except Exception:
                continue
            if hit is None or not harness.passes_liquidity_filter(hit, is_kr=True):
                continue
            entry = hit.get("close")
            pivot = hit.get("pivot")
            if entry is None or pivot is None or pivot <= 0:
                continue
            atr14 = scanner.atr(hist["High"], hist["Low"], hist["Close"])
            if atr14 <= 0:
                continue
            dist_atr = (entry - pivot) / atr14
            bucket = bucket_of(dist_atr)
            if bucket is None:
                continue
            stop = float(hist["Low"].iloc[-1])   # 등록된 정의: 신호일 저가
            if entry <= stop:
                continue

            future = harness.future_after(df_full, off)
            assert len(future) == 0 or future.index[0] > hist.index[-1], \
                "룩어헤드: future 첫 봉이 hist 마지막 봉보다 과거임"
            outcome = harness.race(entry, stop, future)

            # ── 선진입 손절 후 종가진입 제외(같은 종목 중복 계상 방지) 판정용 ──
            signal_high = float(hist["High"].iloc[-1])
            trailing50_vol = float(nonzero_vol_mean(hist["Volume"].iloc[-50:]))
            conf = find_confirm_close({"future": future, "signal_high": signal_high,
                                        "trailing50_vol": trailing50_vol})
            would_confirm = conf is not None
            confirm_day = conf[0] if conf else None
            stop_day = None
            if outcome[0] == "stop":
                avail = min(60, len(future))
                for i in range(avail):
                    assert i < len(future), "lookahead index violation: stop_day 탐색"
                    if float(future["Low"].iloc[i]) <= stop:
                        stop_day = i + 1   # confirm_day와 동일 1-index 기준(며칠째 봉인지)
                        break
            # 선진입이 확인일보다 먼저(또는 같은 날) 손절되면, 실전에서는
            # 이미 손절 처리된 자리라 종가진입 후보에서 제외됐을 것 —
            # docs 등록 "후속 처리" 규칙 그대로.
            confirm_excluded_by_early_stop = bool(
                would_confirm and stop_day is not None and confirm_day is not None
                and stop_day <= confirm_day
            )

            rows.append({
                "ticker": t, "signal_date": str(hist.index[-1].date()), "offset": off,
                "pivot": round(pivot, 4), "close": round(entry, 4), "atr14": round(atr14, 4),
                "dist_atr": round(dist_atr, 4), "bucket": bucket,
                "stop": round(stop, 4), "stop_width_pct": round((entry - stop) / entry * 100, 4),
                "stop_width_atr": round((entry - stop) / atr14, 4),
                "outcome": outcome[0], "r": outcome[1],
                "would_confirm": would_confirm, "confirm_day": confirm_day,
                "stop_day": stop_day, "confirm_excluded_by_early_stop": confirm_excluded_by_early_stop,
            })
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            print(f"[collect] {oi+1}/{len(OFFSETS)} off={off} n_rows={len(rows)} elapsed={time.time()-t0:.0f}s", flush=True)
    return rows


def median(vals):
    vals = sorted(vals)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def bucket_report(rows, label):
    outcomes = [(r["outcome"], r["r"]) for r in rows]
    ev = harness.ev_summary(outcomes)
    sw_pct = median([r["stop_width_pct"] for r in rows]) if rows else None
    sw_atr = median([r["stop_width_atr"] for r in rows]) if rows else None
    return {
        "label": label, "n": len(rows), "nv": ev["nv"], "ev_R": ev["ev_R"],
        "stop_rate": ev["stop_rate"], "target_rate": ev["target_rate"],
        "stop_width_pct_median": sw_pct, "stop_width_atr_median": sw_atr,
    }


def half_split_check(rows_by_bucket_fn, target_label, control_label):
    half = len(OFFSETS) // 2
    offs_early = set(OFFSETS[:half])
    offs_late = set(OFFSETS[half:])

    def _one(offs, name):
        rt = rows_by_bucket_fn(target_label, offs)
        rc = rows_by_bucket_fn(control_label, offs)
        ev_t = harness.ev_summary([(r["outcome"], r["r"]) for r in rt])
        ev_c = harness.ev_summary([(r["outcome"], r["r"]) for r in rc])
        if ev_t["ev_R"] is None or ev_c["ev_R"] is None:
            print(f"  [{name}] 표본 부족 — 판정불가 (n_target={ev_t['nv']}, n_control={ev_c['nv']})")
            return None
        gap = ev_t["ev_R"] - ev_c["ev_R"]
        z, sig = harness.ev_gap_zscore(ev_c, ev_t)
        print(f"  [{name}] {target_label}={ev_t['ev_R']:+.3f}R(n={ev_t['nv']}) "
              f"{control_label}={ev_c['ev_R']:+.3f}R(n={ev_c['nv']})  격차={gap:+.3f}R  z={z}")
        return {"gap": gap, "z": z, "sig": sig}

    return _one(offs_early, "초반(최근시점)"), _one(offs_late, "후반(과거시점)")


if __name__ == "__main__":
    _t0 = time.time()
    print("=" * 70)
    print(f"KR 돌파임박 — 피벗 대비 거리별 선진입 EV. checkpoints={len(OFFSETS)}개, kr_days={KR_FETCH_DAYS}")
    print("=" * 70)
    data, kr_u, us_u = harness.fetch_universe_data(
        markets=("kr",), kr_days=KR_FETCH_DAYS, validate_offsets=OFFSETS)
    bench = harness.fetch_kr_benchmarks(days=KR_FETCH_DAYS)

    print("\n" + "=" * 70)
    print("히트 수집")
    print("=" * 70)
    rows = collect_hits(data, bench)
    print(f"\n[collect] 전체 수집 완료: n={len(rows)}")

    by_bucket = {label: [r for r in rows if r["bucket"] == label] for label, _, _ in BUCKET_EDGES}

    print("\n" + "=" * 70)
    print("구간별 결과 (전체 90개 체크포인트)")
    print("=" * 70)
    reports = {}
    for label, _, _ in BUCKET_EDGES:
        rep = bucket_report(by_bucket[label], label)
        reports[label] = rep
        if rep["ev_R"] is not None:
            print(f"  {label:12s} n={rep['n']:5d}(nv={rep['nv']:5d}) EV={rep['ev_R']:+.3f}R "
                  f"손절률={rep['stop_rate']:.1%} 도달률={rep['target_rate']:.1%} "
                  f"손절폭중앙값={rep['stop_width_pct_median']:.2f}%/{rep['stop_width_atr_median']:.2f}ATR")
        else:
            print(f"  {label:12s} n={rep['n']:5d} EV=N/A(표본부족)")

    # ── 대조군 결정 — 등록된 대체 규칙 ──
    control_label = "0~0.5" if reports["0~0.5"]["nv"] >= MIN_N_FOR_JUDGMENT else "-0.5~0"
    print(f"\n대조군: {control_label} "
          f"({'등록 기본값' if control_label == '0~0.5' else '0~0.5 n<100 — 등록된 대체 규칙 발동'})")

    target_label = "-1.0~-0.5"
    ev_t, ev_c = reports[target_label], reports[control_label]
    print("\n" + "=" * 70)
    print("판정")
    print("=" * 70)
    if ev_t["ev_R"] is None or ev_c["ev_R"] is None or ev_t["nv"] < MIN_N_FOR_JUDGMENT or ev_c["nv"] < MIN_N_FOR_JUDGMENT:
        print(f"  {target_label}(n={ev_t['nv']}) 또는 {control_label}(n={ev_c['nv']})이 n<{MIN_N_FOR_JUDGMENT} — 판정불가")
        adopted = False
        gap = z = sig = None
    else:
        gap = ev_t["ev_R"] - ev_c["ev_R"]
        z, sig = harness.ev_gap_zscore(
            harness.ev_summary([(r["outcome"], r["r"]) for r in by_bucket[control_label]]),
            harness.ev_summary([(r["outcome"], r["r"]) for r in by_bucket[target_label]]),
        )
        print(f"  {target_label}({ev_t['ev_R']:+.3f}R) - {control_label}({ev_c['ev_R']:+.3f}R) = {gap:+.3f}R  "
              f"z={z}  {'유의' if sig else '비유의'}")

        def rows_of(label, offs):
            return [r for r in by_bucket[label] if r["offset"] in offs]

        early, late = half_split_check(rows_of, target_label, control_label)
        rep_early = bool(early and early["sig"] and (early["gap"] > 0) == (gap > 0))
        rep_late = bool(late and late["sig"] and (late["gap"] > 0) == (gap > 0))
        reproduced = rep_early and rep_late
        adopted = (gap >= EV_GAP_THRESHOLD) and bool(sig) and reproduced
        print(f"  초반 재현: {rep_early}  후반 재현: {rep_late}")
        print(f"  => 사전판정(격차>=0.15R & z>=1.96 & 양쪽반분재현): {'채택' if adopted else '미채택'}")

    # ── 히트당 총 기대 R 비교 (등록된 참조상수, 재계산 안 함) ──
    print("\n" + "=" * 70)
    print("히트당 총 기대 R — 선진입 vs 종가진입(참조상수)")
    print("=" * 70)
    for label, _, _ in BUCKET_EDGES:
        rep = reports[label]
        if rep["ev_R"] is not None:
            print(f"  {label:12s} 선진입 히트당기대R = EV×1.0 = {rep['ev_R']:+.3f}R")
    confirm_expected = CONFIRM_EV_REF * CONFIRM_RATE_REF
    print(f"  종가진입(참조)   히트당기대R = {CONFIRM_EV_REF}R × {CONFIRM_RATE_REF} = {confirm_expected:.4f}R")

    # ── 선진입 손절 후 종가진입 제외 — 중복 계상 방지 정보(판정에는 미반영) ──
    print("\n" + "=" * 70)
    print("정보: 선진입 손절 후 종가진입 제외 적용 시 확인율 조정 (이 표본 자체, 참조상수 재계산 아님)")
    print("=" * 70)
    n_all = len(rows)
    n_would_confirm = sum(1 for r in rows if r["would_confirm"])
    n_excluded = sum(1 for r in rows if r["confirm_excluded_by_early_stop"])
    raw_rate = n_would_confirm / n_all if n_all else None
    adj_rate = (n_would_confirm - n_excluded) / n_all if n_all else None
    print(f"  전체 선진입 표본 n={n_all}")
    print(f"  확인조건(안C) 충족 n={n_would_confirm} (원시 확인율 {raw_rate:.1%})" if raw_rate is not None else "  표본 없음")
    print(f"  그 중 선진입이 확인일보다 먼저(≤) 손절된 건 n={n_excluded}")
    if adj_rate is not None:
        print(f"  => 중복 제외 후 조정 확인율 {adj_rate:.1%} (참조상수 15.6%와 비교용, 판정 미반영)")
        print(f"  => 조정판 히트당기대R(참조 EV 그대로, 확인율만 조정) = {CONFIRM_EV_REF}R × {adj_rate:.3f} = {CONFIRM_EV_REF*adj_rate:.4f}R")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "2026-09-08_kr_imminent_pre_pivot_entry_ev.results.json")
    with open(out_path, "w") as f:
        json.dump({
            "n_hits_total": len(rows), "bucket_reports": reports,
            "control_label": control_label, "target_label": target_label,
            "gap": gap, "z": z, "adopted": adopted,
            "confirm_ref": {"ev": CONFIRM_EV_REF, "rate": CONFIRM_RATE_REF, "expected_R": confirm_expected},
            "dedup_info": {"n_all": n_all, "n_would_confirm": n_would_confirm,
                           "n_excluded_by_early_stop": n_excluded,
                           "raw_confirm_rate": raw_rate, "adjusted_confirm_rate": adj_rate},
            "rows": rows,
        }, f, default=str, indent=2, ensure_ascii=False)
    print(f"[main] 결과 JSON(히트별 원자료 포함): {out_path}")

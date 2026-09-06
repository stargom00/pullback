"""
KR+US 5탭 확인진입(안C) — 진입 시점 손절폭(ATR 배수)과 EV의 관계
(2026-09-07, 사용자 지시). `2026-09-07_kr_us_breakout_boxbreak_post_pivot_
consolidation_ev.py`의 하네스 재사용 방식 + `2026-09-04_kr_confirm_entry_
all_tabs_90cp_entry_close.py`의 5탭 확인진입(안C_종가진입) 수집 로직을
그대로 가져온다. 새 스크립트 — 두 원본 파일 다 무변경(README 규칙3).

【대상】5탭(눌림목/돌파임박/박스돌파/돌파/추세전환) 전부, 확인진입(안C)
조건(다음 최대 3거래일 내 종가가 신호일 고가 초과 + 거래량 base_vol50의
1.5배 이상, `find_confirm_close()` — entry_close 스크립트와 완전히
동일 판정 조건)을 충족한 건만 대상. 진입가는 그 확인일 실제 종가
(confirm_close, 룩어헤드 아티팩트 수정판 — `docs/confirm_entry_lookahead_
2026-09-04.md` 근거). 손절은 탭별 기존 stop_key 그대로(눌림목/박스돌파/
돌파/추세전환="stop"[구조손절+ATR버퍼], 돌파임박="signal_low") — 이
스크립트가 새로 정의하는 손절이 아니라 프로덕션이 실제로 쓰는 손절값.

【손절폭 ATR 배수 정의】
  atr_mult = (진입가 - 손절가) / ATR14
  scanner.py의 stop_wide 게이트가 쓰는 것과 동일한 양(risk_pct/atr_pct =
  (entry-stop)/entry ÷ atr/entry = (entry-stop)/atr, L1774-1795 참고)을
  절대값(원/달러 단위 대신 ATR 단위)으로 표현한 것 — 같은 개념, 표시
  단위만 다름. ATR14는 신호일(hist 마지막 봉) 기준 scanner.atr(h,lo,c,14)
  — 손절 자체를 만들 때 쓴 것과 동일 시점·동일 함수(apply_atr_buffer가
  쓰는 atr()와 동일 호출).

【질문1 — 손절폭 ATR배수 버킷별 EV】
  버킷: 0~0.5/0.5~1.0/1.0~1.5/1.5~2.0/2.0~3.0/3.0초과. 진입=confirm_close,
  손절=탭 stop_key, 20거래일 레이스(harness.race max_bars=20, -1R/+2R/
  0R(미결) 표준).

【질문2 — 상한 존재 여부】
  1.0~1.5(기준 구간, 프로덕션 stop_wide 게이트의 실질적 상한 부근)
  대비 1.5~2.0/2.0~3.0/3.0초과 각각이 유의하게 나쁜지
  harness.ev_gap_zscore()로 검정. 유의하게 나쁘지 않으면(z<1.96 또는
  부호가 반대) "상한 규칙 근거 없음"으로 명시.

【질문3 — 손절 도달률】
  ev_summary()가 이미 반환하는 stop_rate를 버킷별로 그대로 보고(질문1
  결과에 이미 포함 — 별도 재계산 안 함, 좁은 손절 버킷의 EV가 나쁘다면
  stop_rate가 그만큼 높은지로 원인 확인).

【공통 제약(사용자 지시)】
  - 룩어헤드 assert: find_confirm_close() 내부(entry_close와 동일,
    L146-147 스타일) + 레이스 슬라이스 직전 bound assert.
  - 시기 반분 재현 확인: 버킷마다 전체/초반(최근시점)/후반(과거시점)
    병기, 부호 역전 시 결과에 그대로 드러남(명시적 "판정" 라벨은 달지
    않고 숫자를 나란히 두어 사람이 직접 확인 — 이번 측정은 사전등록된
    채택기준이 없는 순수 탐색이라 entry_close처럼 judge()를 만들지 않음).
  - 표본 100 미만 = 판정불가, 조건 완화 금지(버킷 병합 없음).
  - 다중비교: 5탭×2시장×6버킷=60셀 + 상한검정 5탭×2시장×3=30건. 유의
    셀이 나와도 시기반분 재현(초반/후반 둘 다 같은 부호로 유의)까지
    확인해야 신뢰 — report의 "다중비교_참고"에 총 셀수 명시.
  - KR/US 각각 분해(README 규칙8).

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-07_kr_us_confirm_entry_stop_width_atr_multiple_ev.py`
결과: 이 폴더에 `.results.json`(코드만 커밋, 결과는 gitignore 대상 —
README 규칙5, .gitignore L6).
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
    atr as calc_atr,
)

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — 규칙9 표준
MIN_N_FOR_JUDGMENT = 100
HOLD_BARS = 20
CONFIRM_K_MAX = 3
CONFIRM_VOL_MULT = 1.5

ATR_MULT_BUCKETS = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, float("inf"))]
BASELINE_BUCKET_LABEL = "1.0~1.5ATR"
CEILING_TEST_LABELS = ["1.5~2.0ATR", "2.0~3.0ATR", "3.0ATR초과"]

TABS = {
    "눌림목": {"fn": analyze, "cfg": CONFIG, "stop_key": "stop"},
    "돌파임박": {"fn": analyze_imminent, "cfg": IMMINENT_CONFIG, "stop_key": "signal_low"},
    "박스돌파": {"fn": analyze_boxbreak, "cfg": BOXBREAK_CONFIG, "stop_key": "stop"},
    "돌파": {"fn": analyze_breakout, "cfg": BREAKOUT_CONFIG, "stop_key": "stop"},
    "추세전환": {"fn": analyze_turnaround, "cfg": TURN_CONFIG, "stop_key": "stop"},
}
NEED_BARS = max(t["cfg"]["min_bars"] for t in TABS.values())

_lookahead_checks = 0


# ══════════════════════════════════════════════════════════════════
# 1단계: 5탭 히트 수집 — entry_close 스크립트의 collect_hits()와 동일
# 구조, atr14(신호일 기준)만 추가로 저장.
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
            atr14 = calc_atr(hist["High"], hist["Low"], hist["Close"], 14)

            for name, spec in TABS.items():
                try:
                    r = spec["fn"](hist, rs_rank=rr, rs_mom=rm, cfg=spec["cfg"], is_kr=ikr)
                except Exception:
                    r = None
                if r is None or not harness.passes_liquidity_filter(r, ikr):
                    continue
                # stop_key="signal_low"는 analyze_imminent()의 반환 딕셔너리(r)에
                # 없는 필드다 — hist에서 직접 뽑은 신호일 저가(signal_low, 위에서
                # 계산)를 써야 한다(entry_close 스크립트의 원 관례와 동일, r.get()이
                # 아니라 hit 레벨 값). 그 외 탭은 전부 r["stop"](구조손절+ATR버퍼).
                stop_val = signal_low if spec["stop_key"] == "signal_low" else r.get(spec["stop_key"])
                if stop_val is None:
                    continue
                hits[name].append({
                    "ticker": t, "off": off, "is_kr": ikr,
                    "signal_high": signal_high, "trailing50_vol": trailing50_vol,
                    "atr14": float(atr14) if atr14 else None,
                    "stop_val": float(stop_val),
                    "future": future,
                })

        counts = {k: len(v) for k, v in hits.items()}
        print(f"[PASS1] offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s {counts}",
              flush=True)
    return hits


# ══════════════════════════════════════════════════════════════════
# 2단계: 확인조건 — entry_close 스크립트의 find_confirm_close()와
# 완전히 동일(조건도, 반환값도) 그대로 복붙.
# ══════════════════════════════════════════════════════════════════
def find_confirm_close(h, k_max=CONFIRM_K_MAX):
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


def bucket_label(atr_mult):
    for lo, hi in ATR_MULT_BUCKETS:
        if lo <= atr_mult < hi:
            return f"{lo}~{hi}ATR" if hi != float("inf") else f"{lo}ATR초과"
    return None


def summarize(outcomes):
    ev = harness.ev_summary(outcomes)
    z, sig = harness.one_sample_zscore(ev)
    n = ev["nv"] or 0
    verdict = f"판정불가(n={n}<{MIN_N_FOR_JUDGMENT})" if n < MIN_N_FOR_JUDGMENT else None
    return {**ev, "z": z, "significant": sig, "판정": verdict}


# ══════════════════════════════════════════════════════════════════
# 3단계: 확인진입 → ATR배수 버킷 분류 → 20일 레이스
# ══════════════════════════════════════════════════════════════════
def run_stop_width_analysis(hit_list):
    bucket_outcomes = {f"{lo}~{hi}ATR" if hi != float("inf") else f"{lo}ATR초과": []
                        for lo, hi in ATR_MULT_BUCKETS}
    n_confirmed = 0
    n_atr_bad = 0
    for h in hit_list:
        conf = find_confirm_close(h)
        if conf is None:
            continue
        n_confirmed += 1
        k, _trigger, confirm_close = conf
        stop_val = h["stop_val"]
        atr14 = h["atr14"]
        if not atr14 or atr14 <= 0 or confirm_close <= stop_val:
            n_atr_bad += 1
            continue
        atr_mult = (confirm_close - stop_val) / atr14
        label = bucket_label(atr_mult)
        if label is None:
            continue
        fut = h["future"]
        assert k <= len(fut), f"lookahead bounds violation: k={k} len(fut)={len(fut)}"
        fut_after = fut.iloc[k:]
        outcome = harness.race(confirm_close, stop_val, fut_after, max_bars=HOLD_BARS)
        bucket_outcomes[label].append(outcome)

    results = {label: summarize(outs) for label, outs in bucket_outcomes.items()}
    results["_n_확인진입"] = n_confirmed
    results["_n_ATR계산불가_제외"] = n_atr_bad

    # 질문2 — 1.0~1.5 기준 대비 상한 구간 유의성 검정
    baseline = results.get(BASELINE_BUCKET_LABEL)
    ceiling = {}
    for label in CEILING_TEST_LABELS:
        bucket = results.get(label)
        if not bucket or (bucket.get("nv") or 0) == 0 or not baseline or (baseline.get("nv") or 0) == 0:
            ceiling[label] = {"note": "표본부족(비교불가)"}
            continue
        z, sig = harness.ev_gap_zscore(bucket, baseline)
        gap = (baseline["ev_R"] - bucket["ev_R"]) if (baseline["ev_R"] is not None and bucket["ev_R"] is not None) else None
        worse = bool(sig and gap is not None and gap > 0)
        ceiling[label] = {
            f"{BASELINE_BUCKET_LABEL}_대비_gap_R": gap, "z": z, "significant": sig,
            "유의하게_나쁨": worse,
            "판정": f"판정불가(n={bucket.get('nv')}<{MIN_N_FOR_JUDGMENT} 또는 baseline n={baseline.get('nv')}<{MIN_N_FOR_JUDGMENT})"
                    if (bucket.get("nv") or 0) < MIN_N_FOR_JUDGMENT or (baseline.get("nv") or 0) < MIN_N_FOR_JUDGMENT else None,
        }
    results["_상한검정(vs_1.0~1.5)"] = ceiling
    return results


def run_with_half_split(hit_list, runner):
    """시기 반분 병기 — mid_off 기준, entry_close/post_pivot 스크립트와
    동일 관례. 이번 측정은 사전등록 채택기준이 없는 순수 탐색이라 재현
    여부를 사람이 직접 비교할 수 있도록 숫자만 나란히 둔다(judge() 없음)."""
    full = runner(hit_list)
    if len(hit_list) < MIN_N_FOR_JUDGMENT:
        note = {"note": f"표본부족(n={len(hit_list)})"}
        return {"전체": full, "초반(최근시점)": note, "후반(과거시점)": note}
    mid_off = OFFSETS[len(OFFSETS) // 2]
    early = [h for h in hit_list if h["off"] <= mid_off]
    late = [h for h in hit_list if h["off"] > mid_off]
    return {
        "전체": full,
        "초반(최근시점)": runner(early) if len(early) >= MIN_N_FOR_JUDGMENT else {"note": f"표본부족(n={len(early)})"},
        "후반(과거시점)": runner(late) if len(late) >= MIN_N_FOR_JUDGMENT else {"note": f"표본부족(n={len(late)})"},
    }


def run(data, bench, out_path=None):
    hits = collect_hits(data, bench)

    report = {
        "offsets": f"{OFFSETS[0]}..{OFFSETS[-1]} step10 ({len(OFFSETS)}개)",
        "hold_bars": HOLD_BARS,
        "atr_mult_buckets": [f"{lo}~{hi}" if hi != float("inf") else f"{lo}+" for lo, hi in ATR_MULT_BUCKETS],
        "다중비교_참고": "5탭×2시장×6버킷=60셀 + 상한검정 5탭×2시장×3=30건 — "
                        "유의 셀(z>=1.96)이 나와도 초반/후반 둘 다 같은 부호로 "
                        "유의해야 재현으로 인정, 단독 유의는 우연 가능성 배제 못함.",
        "lookahead_checks_passed": None,
        "tabs": {},
    }

    for name, spec in TABS.items():
        hit_list = hits[name]
        kr_hits = [h for h in hit_list if h["is_kr"]]
        us_hits = [h for h in hit_list if not h["is_kr"]]

        tab_report = {"stop_key_used": spec["stop_key"],
                       "n_total": len(hit_list), "n_kr": len(kr_hits), "n_us": len(us_hits)}
        for market_label, market_hits in (("KR", kr_hits), ("US", us_hits)):
            if len(market_hits) < MIN_N_FOR_JUDGMENT:
                tab_report[market_label] = {"note": f"판정불가(n={len(market_hits)}<{MIN_N_FOR_JUDGMENT})"}
                continue
            tab_report[market_label] = run_with_half_split(market_hits, run_stop_width_analysis)
        report["tabs"][name] = tab_report
        print(f"[결과] {name}: n_total={len(hit_list)} n_kr={len(kr_hits)} n_us={len(us_hits)}", flush=True)

    report["lookahead_checks_passed"] = _lookahead_checks
    print(f"[검증] 룩어헤드 assert 통과 {_lookahead_checks}건", flush=True)

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
                        "2026-09-07_kr_us_confirm_entry_stop_width_atr_multiple_ev.results.json")
    run(data, bench, out_path=out)

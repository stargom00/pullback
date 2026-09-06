"""
KR+US 박스돌파·돌파 탭 — 돌파 후 "안착/이탈" 경로와 EV (2026-09-07,
사용자 지시). 기존 하네스(harness.py) + scanner.analyze_boxbreak/
analyze_breakout를 그대로 재사용, 새 로직(안착 스트릭 추적, ATR거리
버킷, 대기일수 버킷)만 이 스크립트에 추가한다. 새 스크립트 — 기존
`2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_close.py` 등 무변경
(README 규칙3).

【용어 정의 — 전부 종가 기준】
  피벗 = 돌파 직전 베이스 상단 (analyze_boxbreak/analyze_breakout이 이미
         산출하는 r["pivot"] — 박스돌파는 "박스 상단", 돌파는 "베이스
         천장". 확인진입(안C) 계열의 signal_high(신호일 고가)와는 다른
         값이니 혼동 금지.)
  안착 = 돌파일 다음 날부터 연속으로 종가가 피벗 위 유지(리셋 전까지
         카운트). future_after()가 이미 "돌파일 다음 날부터"를 보장한다
         (harness.truncate_at/future_after 정의 — hist는 돌파일까지,
         future는 그 다음날부터).
  이탈 = 종가가 피벗 아래로 마감 → 안착 카운트 0으로 리셋.

【질문1 — 안착 일수(1/2/3/5일) EV, 비교군(즉시진입) 포함】
  안착 D일 "첫 달성" 시점(스트릭이 처음 D에 도달한 날)의 종가에 진입,
  손절=피벗(고정값, ATR버퍼 등 구조손절 보정 없음 — 사용자 정의 그대로),
  20거래일 레이스(harness.race(..., max_bars=20)). D일에 끝내 도달 못한
  히트는 그 D버킷 표본에서 제외(도달 실패 자체가 별개 정보라 뒤섞지
  않음). 비교군 "즉시진입"은 안착 대기 없이 돌파일 종가 그대로 진입.

【질문2 — 돌파일 종가의 피벗 대비 ATR14 거리 버킷】
  거리 = (돌파일 종가 - 피벗) / ATR14. ATR14는 scanner.atr(h, lo, c, 14)
  — 프로덕션 stop 버퍼링에도 쓰는 것과 동일 함수(14봉 True Range 중앙값).
  버킷: 0~0.5 / 0.5~1.0 / 1.0~1.5 / 1.5초과. 각 버킷 히트는 돌파일 종가에
  즉시진입, 손절=피벗, 20거래일 레이스.

【질문3 — 안착3일 확정 코호트의 "진입조건(0.5~1.5 ATR밴드) 도달 대기일수"】
  질문1의 "안착3일" 첫달성 조건을 만족한 히트만 모은 코호트에서, 돌파일
  (day0, 이미 아는 값이라 lookahead 아님)부터 최대 WAIT_SEARCH_MAX
  거래일 동안 종가가 피벗대비 0.5~1.5 ATR 밴드에 처음 들어오는 날을
  찾는다. WAIT_SEARCH_MAX=60은 임의값이 아니라 `harness.checkpoints`
  최소 offset(60)과 같다 — 이 값이 모든 히트가 보장하는 최소 future
  길이라서, 이보다 크게 잡으면 초반 체크포인트 히트 일부가 데이터 부족과
  "끝내 미도달"을 구분 못 하게 된다(진짜 미도달 vs 관측窓 부족 혼동 방지).
  버킷(대기일수): 0~2 / 3~5 / 6~10 / 11~60 / 끝내미도달(60일 내 미진입).
  각 버킷: 그 도달일 종가에 진입(손절=피벗, 20거래일 레이스)한 EV와,
  코호트 대비 비율.

【공통 제약(사용자 지시)】
  - 룩어헤드 assert: 미래 인덱스 접근 전 항상 bounds assert (기존
    find_confirm_close() 패턴과 동일 방식 — L146-147 스타일).
  - 시기 반분 재현 확인: 전체 90개 체크포인트 결과 옆에 초반(최근시점)/
    후반(과거시점) 결과를 항상 병기(mid_off 기준, half_split 관례 그대로).
  - 표본 100 미만 = 판정불가 명시, 조건 완화 금지(버킷을 합치거나
    문턱을 낮추지 않는다 — 표본이 적으면 적은 대로 "판정불가"만 붙인다).
  - KR/US 각각 분해 보고(README 규칙8).

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-07_kr_us_breakout_boxbreak_post_pivot_consolidation_ev.py`
결과: 이 폴더에 `.results.json`으로 저장(코드만 커밋, 결과는 gitignore
대상 — README 규칙5, .gitignore L6 `scripts/measurements/*.results.json`).
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import (
    analyze_boxbreak, BOXBREAK_CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    atr as calc_atr,
)

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — 규칙9 표준(채택급 판정에 필요)
MIN_N_FOR_JUDGMENT = 100      # 사용자 지시: n<100은 판정불가, 조건 완화 금지
HOLD_BARS = 20                # 질문1/2/3 공통 보유기간
CONSOLIDATION_DAYS = [1, 2, 3, 5]
ATR_BUCKETS = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, float("inf"))]
WAIT_SEARCH_MAX = min(OFFSETS)   # 60 — 모든 히트가 보장하는 최소 future 길이(근거는 상단 docstring)
WAIT_BUCKETS = [(0, 2), (3, 5), (6, 10), (11, WAIT_SEARCH_MAX)]

TABS = {
    "박스돌파": {"fn": analyze_boxbreak, "cfg": BOXBREAK_CONFIG},
    "돌파": {"fn": analyze_breakout, "cfg": BREAKOUT_CONFIG},
}
NEED_BARS = max(t["cfg"]["min_bars"] for t in TABS.values())

_lookahead_checks = 0


# ══════════════════════════════════════════════════════════════════
# 1단계: 히트 수집 — 박스돌파/돌파 2탭만(질문 대상). 원본 collect_hits()
# 구조(체크포인트 순회 + RS 재계산 + 유동성 사후필터)는 완전히 재사용,
# 여기서 새로 추가하는 건 pivot/atr14 필드뿐.
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

            for name, spec in TABS.items():
                try:
                    r = spec["fn"](hist, rs_rank=rr, rs_mom=rm, cfg=spec["cfg"], is_kr=ikr)
                except Exception:
                    r = None
                if r is None or not harness.passes_liquidity_filter(r, ikr):
                    continue
                pivot = r.get("pivot")
                close = r.get("close")
                if pivot is None or close is None or pivot <= 0 or close <= pivot:
                    continue   # 방어적 가드 — analyze_*는 이미 close>pivot을 보장하지만 명시적으로 확인
                atr14 = calc_atr(hist["High"], hist["Low"], hist["Close"], 14)
                hits[name].append({
                    "ticker": t, "off": off, "is_kr": ikr,
                    "close": float(close), "pivot": float(pivot),
                    "atr14": float(atr14) if atr14 else None,
                    "future": future,
                })

        counts = {k: len(v) for k, v in hits.items()}
        print(f"[PASS1] offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s {counts}",
              flush=True)
    return hits


# ══════════════════════════════════════════════════════════════════
# 2단계: 경로 추적 유틸 — 안착 스트릭 첫달성, ATR밴드 첫도달, 레이스
# ══════════════════════════════════════════════════════════════════
def find_consolidation_entry(h, target_days):
    """돌파일 다음날(future.iloc[0])부터 종가>피벗 연속 target_days일을
    처음 채운 시점. 중간에 이탈(종가<=피벗)하면 스트릭 리셋 후 계속 탐색
    (재도전 허용 — 첫 시도에서 못 채웠다고 그 히트를 버리지 않음, 질문3의
    "대기일수" 개념과 일관). 반환: (day_idx 1-based, 그날 종가) 또는
    None(가용 future 끝까지 못 채움)."""
    fut = h["future"]
    pivot = h["pivot"]
    n = len(fut)
    streak = 0
    for day_idx in range(1, n + 1):
        assert day_idx - 1 < len(fut), f"lookahead bounds violation: day_idx={day_idx} len={len(fut)}"
        global _lookahead_checks
        _lookahead_checks += 1
        c = float(fut["Close"].iloc[day_idx - 1])
        streak = streak + 1 if c > pivot else 0
        if streak == target_days:
            return day_idx, c
    return None


def find_entry_zone_day(h):
    """돌파일(day0, 이미 아는 값)부터 최대 WAIT_SEARCH_MAX거래일 동안
    종가가 피벗대비 0.5~1.5 ATR14 밴드에 처음 들어오는 날. 반환:
    (day_idx, 그날 종가) — day_idx=0이면 돌파일 자체가 이미 밴드 안.
    None이면 WAIT_SEARCH_MAX일 내 끝내 미도달."""
    pivot, atr14 = h["pivot"], h["atr14"]
    if not atr14 or atr14 <= 0:
        return None
    dist0 = (h["close"] - pivot) / atr14
    if 0.5 <= dist0 < 1.5:
        return 0, h["close"]
    fut = h["future"]
    limit = min(WAIT_SEARCH_MAX, len(fut))
    global _lookahead_checks
    for day_idx in range(1, limit + 1):
        assert day_idx - 1 < len(fut), f"lookahead bounds violation: day_idx={day_idx} len={len(fut)}"
        _lookahead_checks += 1
        c = float(fut["Close"].iloc[day_idx - 1])
        dist = (c - pivot) / atr14
        if 0.5 <= dist < 1.5:
            return day_idx, c
    return None


def race_after_day(h, day_idx, entry_price):
    """day_idx(0=돌파일 자체, 1이상=그 이후 며칠째) 시점 종가로 진입,
    그 다음날부터 레이스 시작(day_idx=0이면 future 전체가 그대로 레이스
    구간 — 즉시진입과 동일)."""
    fut = h["future"]
    assert 0 <= day_idx <= len(fut), f"lookahead bounds violation: day_idx={day_idx} len={len(fut)}"
    race_future = fut.iloc[day_idx:]
    return harness.race(entry_price, h["pivot"], race_future, max_bars=HOLD_BARS)


def summarize(outcomes):
    ev = harness.ev_summary(outcomes)
    z, sig = harness.one_sample_zscore(ev)
    n = ev["nv"] or 0
    verdict = f"판정불가(n={n}<{MIN_N_FOR_JUDGMENT})" if n < MIN_N_FOR_JUDGMENT else None
    return {**ev, "z": z, "significant": sig, "판정": verdict}


# ══════════════════════════════════════════════════════════════════
# 질문1: 안착 일수별 EV (+ 즉시진입 비교군)
# ══════════════════════════════════════════════════════════════════
def run_q1(hit_list):
    results = {}
    immediate = [race_after_day(h, 0, h["close"]) for h in hit_list]
    results["즉시진입(돌파일종가)"] = summarize(immediate)
    for d in CONSOLIDATION_DAYS:
        outs = []
        for h in hit_list:
            found = find_consolidation_entry(h, d)
            if found is None:
                continue
            day_idx, entry_close = found
            outs.append(race_after_day(h, day_idx, entry_close))
        results[f"안착{d}일"] = summarize(outs)
        results[f"안착{d}일"]["n_도달"] = len(outs)
        results[f"안착{d}일"]["도달율"] = round(len(outs) / len(hit_list), 4) if hit_list else None
    return results


# ══════════════════════════════════════════════════════════════════
# 질문2: 돌파일 종가의 피벗대비 ATR14 거리 버킷별 EV
# ══════════════════════════════════════════════════════════════════
def run_q2(hit_list):
    results = {}
    for lo_b, hi_b in ATR_BUCKETS:
        label = f"{lo_b}~{hi_b}ATR" if hi_b != float("inf") else f"{lo_b}ATR초과"
        bucket = []
        for h in hit_list:
            if not h["atr14"] or h["atr14"] <= 0:
                continue
            dist = (h["close"] - h["pivot"]) / h["atr14"]
            if lo_b <= dist < hi_b:
                bucket.append(h)
        outs = [race_after_day(h, 0, h["close"]) for h in bucket]
        results[label] = summarize(outs)
    return results


# ══════════════════════════════════════════════════════════════════
# 질문3: 안착3일 확정 코호트의 진입조건(0.5~1.5ATR) 도달 대기일수 버킷
# ══════════════════════════════════════════════════════════════════
def _wait_bucket_label(day_idx):
    for lo_d, hi_d in WAIT_BUCKETS:
        if lo_d <= day_idx <= hi_d:
            return f"{lo_d}~{hi_d}일" if lo_d != hi_d else f"{lo_d}일"
    return None


def run_q3(hit_list):
    cohort = [h for h in hit_list if find_consolidation_entry(h, 3) is not None]
    n_cohort = len(cohort)
    bucket_items = {f"{lo}~{hi}일": [] for lo, hi in WAIT_BUCKETS}
    never = []
    for h in cohort:
        found = find_entry_zone_day(h)
        if found is None:
            never.append(h)
            continue
        day_idx, entry_close = found
        label = _wait_bucket_label(day_idx)
        assert label is not None, f"unbucketed day_idx={day_idx} (WAIT_SEARCH_MAX={WAIT_SEARCH_MAX})"
        bucket_items[label].append((h, day_idx, entry_close))

    results = {"_코호트_n(안착3일확정)": n_cohort}
    for label, items in bucket_items.items():
        outs = [race_after_day(h, day_idx, entry_close) for h, day_idx, entry_close in items]
        r = summarize(outs)
        r["n"] = len(items)
        r["비율"] = round(len(items) / n_cohort, 4) if n_cohort else None
        results[label] = r
    results["끝내미도달"] = {
        "n": len(never),
        "비율": round(len(never) / n_cohort, 4) if n_cohort else None,
    }
    return results


def run_with_half_split(hit_list, runner):
    """시기 반분 재현 확인 — mid_off 기준 초반(최근시점)/후반(과거시점),
    기존 half_split() 관례와 동일 기준선."""
    full = runner(hit_list)
    if len(hit_list) < MIN_N_FOR_JUDGMENT:
        return {"전체": full, "초반(최근시점)": {"note": f"표본부족(n={len(hit_list)})"},
                "후반(과거시점)": {"note": f"표본부족(n={len(hit_list)})"}}
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
        "wait_search_max": WAIT_SEARCH_MAX,
        "lookahead_checks_passed": None,
        "tabs": {},
    }

    for name in TABS:
        hit_list = hits[name]
        kr_hits = [h for h in hit_list if h["is_kr"]]
        us_hits = [h for h in hit_list if not h["is_kr"]]

        tab_report = {"n_total": len(hit_list), "n_kr": len(kr_hits), "n_us": len(us_hits)}
        for market_label, market_hits in (("KR", kr_hits), ("US", us_hits)):
            if len(market_hits) < MIN_N_FOR_JUDGMENT:
                tab_report[market_label] = {"note": f"판정불가(n={len(market_hits)}<{MIN_N_FOR_JUDGMENT})"}
                continue
            tab_report[market_label] = {
                "질문1_안착일수EV": run_with_half_split(market_hits, run_q1),
                "질문2_ATR거리EV": run_with_half_split(market_hits, run_q2),
                "질문3_대기일수한계": run_with_half_split(market_hits, run_q3),
            }
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
                        "2026-09-07_kr_us_breakout_boxbreak_post_pivot_consolidation_ev.results.json")
    run(data, bench, out_path=out)

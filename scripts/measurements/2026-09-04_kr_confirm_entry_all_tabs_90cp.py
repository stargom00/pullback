"""
KR 단독 확인진입(안C) EV — 5개 탭 × KR/US 90개 체크포인트 (2026-09-04,
사용자 지시). 조사 전용 — 커밋 안 함, 기존 파일 무변경(README 규칙3
재사용만, 신규 스크립트).

【배경】
기존 "KR 표준 EV ~0" 결론(docs/kr_us_strategy_map.md)은 즉시진입(안A)
기준이다. `2026-09-01_confirm_entry_90cp_revalidation.py`가 안C/안C'
(확인진입)로 전체(KR+US 혼합) EV를 0.054R→1.062R까지 끌어올렸음을
보였지만 KR/US 분해가 없었다 — KR에서 어느 탭을 볼지 결정하려면
탭별 KR 단독 안C EV가 필요하다.

【측정 대상 5탭 × KR/US】
눌림목(analyze/CONFIG) · 돌파임박(analyze_imminent/IMMINENT_CONFIG) ·
박스돌파(analyze_boxbreak/BOXBREAK_CONFIG) · 돌파(analyze_breakout/
BREAKOUT_CONFIG) · 추세전환(analyze_turnaround/TURN_CONFIG).
종가베팅은 이미 KR 전용 검증됨(docs/kr_jongga_betting_backtest.md)이라
제외. 재점화는 이 스크립트 대상 아님(별도 KR/US 분해만 추가 예정,
여기선 안 다룸).

【안A/안C 정의 — 탭마다 다름, 전부 명시】
- 안A(즉시진입): 신호일 종가로 진입, `analyze_*()`가 반환한 stop 그대로
  (harness.race() 표준 패턴, 이번 세션 우선순위1~5 재검증과 동일).
- 확인조건(공통): 신호일 다음 최대 3거래일 내 "종가"가 신호일 고가를
  초과 + 그날 거래량이 신호일 기준 트레일링50일평균의 1.5배 이상
  (`2026-09-01_confirm_entry_90cp_revalidation.py`의 `find_confirm_close`
  그대로 재사용 — 재구현 안 함). 진입가 = 신호일 고가 레벨(기존 관례
  그대로).
- 손절 기준은 탭별로 다르다(기존 프로덕션 정의를 그대로 따름, 새로
  발명 안 함):
  - **돌파임박**: 안C = Close확인 + 신호일 저가(signal_low) — 프로덕션
    `CONFIRM_RULE_BY_TAB`의 실제 정의(EV 1.062R 원본, app.py). 이
    스크립트가 인용하는 "0.054R→1.062R" 배경 수치가 바로 이 정의.
  - **눌림목**: 안C' = Close확인 + `analyze()`의 구조적 stop 그대로
    (재정의 안 함) — 프로덕션 `CONFIRM_RULE_BY_TAB` 그대로.
  - **박스돌파/돌파/추세전환**: 프로덕션에 확인진입 규칙 자체가 아직
    없음(`CONFIRM_RULE_BY_TAB`에 항목 없음) — 이번 측정에서 **새로
    정의**: Close확인 + 각 tab의 구조적 stop 그대로(눌림목과 같은
    패턴, signal_low 변형 없음). 결과가 좋아도 이것만으로 프로덕션
    규칙 신설을 의미하지 않음 — 별도 사용자 판단 필요.

【룩어헤드 방지】
신호일 지표(signal_high/trailing50_vol/stop)는 전부 `hist`(트렁케이션
시점까지의 데이터)에서만 계산 — `future`(트렁케이션 이후)는 확인조건
탐색에만 쓴다. `_assert_no_lookahead_confirm()`이 매 확인 판정마다
"확인일 인덱스가 future 배열 내부([0, k_max))에 있는가"를 assert.

【생존편향 방지】
확인조건 미충족 건은 안C의 표본에서 빠지지만(정의상 당연 — "확인된
진입"만 안C 표본), **하이브리드(미확인=안A로 대체) EV를 항상 같이
보고**해 "확인 안 된 건을 조용히 버리고 확인된 것만 좋게 포장"하는
착시를 차단한다(2026-09-01 스크립트와 동일 원칙 재사용). 진입률(확인
비율)도 항상 병기.

【판정 기준(사전 등록, 사용자 지시)】
- KR 안C EV>=+0.15R & z>=1.96 & 시기 반분(전반/후반 45개 체크포인트씩)
  둘 다 재현 → 그 탭 "KR 유효"
- n<100 → "판정불가"로 분리, 유효/무효 결론 내지 않음
- 전 탭 무효 → "KR은 종가베팅 외 검증된 진입법 없음"으로 기록
- 결과가 나쁘게 나와도 조건 완화해서 살리지 않는다(사용자 지시).

방법론: harness.py 재사용(fetch/RS/2R레이스/유동성필터), 90개 체크포인트
=checkpoints(60,950,10), kr_days=1900/us_period=5y(README 규칙10).

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-04_kr_confirm_entry_all_tabs_90cp.py`
(확장 fetch+5탭×전종목 순회라 원본보다 오래 걸림, 수십분 예상).
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
)

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — 규칙9 표준
MIN_N_FOR_JUDGMENT = 100   # 사용자 지시: n<100은 판정불가
EV_GAP_THRESHOLD = 0.15    # R, 사전 등록
CONFIRM_K_MAX = 3          # 확인 탐색 최대 봉수(기존 스크립트와 동일)
CONFIRM_VOL_MULT = 1.5

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
            trailing50_vol = float(hist["Volume"].iloc[-50:].mean())

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
# 2단계: 확인조건 탐색 (룩어헤드 방지 assert 포함)
# ══════════════════════════════════════════════════════════════════
_lookahead_checks = 0


def find_confirm_close(h, k_max=CONFIRM_K_MAX):
    """확인조건: 다음 최대 k_max거래일 내 종가가 신호일 고가 초과 +
    거래량 트레일링50평균의 1.5배 이상. 2026-09-01 스크립트의
    find_confirm_close()와 동일 로직(재구현 아님, 그대로 복붙 — 이
    파일만 있는 신규 탭 확장이라 import로 공유하기보다 독립 스크립트
    원칙 유지, README 규칙2)."""
    global _lookahead_checks
    fut = h["future"]
    trigger = h["signal_high"]
    base_vol = h["trailing50_vol"]
    avail = min(k_max, len(fut))
    for k in range(1, avail + 1):
        # 룩어헤드 방지 assert: k는 반드시 future(트렁케이션 이후) 내부
        # 인덱스여야 하고, trigger/base_vol은 hist(트렁케이션 이전)에서만
        # 온 값이어야 한다 — h["signal_high"]/h["trailing50_vol"]가
        # collect_hits()에서 hist로부터만 계산됐음을 구조적으로 보장
        # (future를 인자로 준 적이 없음), 여기서는 인덱스 범위만 재확인.
        assert 1 <= k <= k_max, f"lookahead index violation: k={k}"
        assert k - 1 < len(fut), f"confirm day beyond available future: k={k}, len(fut)={len(fut)}"
        _lookahead_checks += 1
        c = float(fut["Close"].iloc[k - 1])
        vv = float(fut["Volume"].iloc[k - 1])
        if c > trigger and vv >= CONFIRM_VOL_MULT * base_vol:
            return k, trigger
    return None


# ══════════════════════════════════════════════════════════════════
# 3단계: 안A/안C EV 계산 (탭 공용)
# ══════════════════════════════════════════════════════════════════
def run_confirm_analysis(hit_list, stop_key):
    a_outcomes, c_outcomes, hybrid_outcomes = [], [], []
    n_confirmed = 0
    for h in hit_list:
        a_out = harness.race(h["close"], h["stop"], h["future"])
        a_outcomes.append(a_out)
        conf = find_confirm_close(h)
        if conf is None:
            hybrid_outcomes.append(a_out)   # 미확인 건 = 안A로 대체(생존편향 방지)
            continue
        k, trigger_price = conf
        n_confirmed += 1
        fut_after = h["future"].iloc[k:]
        stop_val = h[stop_key]
        c_out = harness.race(trigger_price, stop_val, fut_after)
        c_outcomes.append(c_out)
        hybrid_outcomes.append(c_out)

    ev_a = harness.ev_summary(a_outcomes)
    ev_c = harness.ev_summary(c_outcomes)
    z, sig = harness.ev_gap_zscore(ev_a, ev_c)
    gap = (ev_c["ev_R"] - ev_a["ev_R"]) if (ev_c["ev_R"] is not None and ev_a["ev_R"] is not None) else None
    return {
        "n_hits_total": len(hit_list),
        "안A_전체": ev_a,
        "안C_확인진입분": ev_c,
        "하이브리드_전체(미확인=안A로 대체)": harness.ev_summary(hybrid_outcomes),
        "확인율": round(n_confirmed / len(hit_list), 4) if hit_list else None,
        "안C_vs_안A_gap_R": gap,
        "안C_vs_안A_z": z,
        "안C_vs_안A_significant": sig,
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


def judge(tab_name, kr_result, kr_half):
    n = kr_result["안C_확인진입분"]["nv"] if kr_result else 0
    if n < MIN_N_FOR_JUDGMENT:
        return f"판정불가(n={n} < {MIN_N_FOR_JUDGMENT})"
    gap = kr_result["안C_vs_안A_gap_R"]
    sig = kr_result["안C_vs_안A_significant"]
    ev_c = kr_result["안C_확인진입분"]["ev_R"]
    if ev_c is None or gap is None:
        return "판정불가(EV 계산 불가)"
    if ev_c < EV_GAP_THRESHOLD or not sig:
        return f"KR 무효(안C EV={ev_c:+.3f}R, gap={gap:+.3f}R, z={kr_result['안C_vs_안A_z']}) — 기준(EV>=+0.15R & z>=1.96) 미달"
    early = kr_half.get("초반(최근시점)", {})
    late = kr_half.get("후반(과거시점)", {})
    early_ok = isinstance(early, dict) and early.get("안C_확인진입분", {}).get("ev_R") is not None and early["안C_확인진입분"]["ev_R"] >= EV_GAP_THRESHOLD
    late_ok = isinstance(late, dict) and late.get("안C_확인진입분", {}).get("ev_R") is not None and late["안C_확인진입분"]["ev_R"] >= EV_GAP_THRESHOLD
    if early_ok and late_ok:
        return f"KR 유효(안C EV={ev_c:+.3f}R, z={kr_result['안C_vs_안A_z']:.2f}, 시기반분 재현)"
    return f"KR 무효(안C EV 기준은 통과했으나 시기반분 재현 실패 — 초반 ok={early_ok}, 후반 ok={late_ok})"


def run(data, bench, out_path=None):
    hits = collect_hits(data, bench)

    report = {"offsets": f"{OFFSETS[0]}..{OFFSETS[-1]} step10 ({len(OFFSETS)}개)",
              "lookahead_checks_passed": None, "tabs": {}}

    for name, spec in TABS.items():
        stop_key = spec["stop_key"]
        hit_list = hits[name]
        kr_hits = [h for h in hit_list if h["is_kr"]]
        us_hits = [h for h in hit_list if not h["is_kr"]]

        kr_result = run_confirm_analysis(kr_hits, stop_key) if kr_hits else None
        us_result = run_confirm_analysis(us_hits, stop_key) if us_hits else None
        kr_half = half_split(kr_hits, stop_key) if kr_hits else {}

        verdict = judge(name, kr_result, kr_half)
        report["tabs"][name] = {
            "stop_key_used": stop_key,
            "n_total": len(hit_list), "n_kr": len(kr_hits), "n_us": len(us_hits),
            "KR": kr_result, "KR_시기반분": kr_half, "US": us_result,
            "판정": verdict,
        }
        print(f"[결과] {name}: {verdict}", flush=True)
        print(f"       KR n={len(kr_hits)} 안A={kr_result['안A_전체']['ev_R'] if kr_result else None} "
              f"안C={kr_result['안C_확인진입분']['ev_R'] if kr_result else None} "
              f"z={kr_result['안C_vs_안A_z'] if kr_result else None} | "
              f"US n={len(us_hits)} 안C={us_result['안C_확인진입분']['ev_R'] if us_result else None}",
              flush=True)

    report["lookahead_checks_passed"] = _lookahead_checks
    print(f"[검증] 룩어헤드 assert 통과 {_lookahead_checks}건", flush=True)

    all_invalid = all("무효" in report["tabs"][n]["판정"] or "판정불가" in report["tabs"][n]["판정"] for n in TABS)
    if all_invalid:
        print("[종합] 전 탭 무효/판정불가 — 'KR은 종가베팅 외 검증된 진입법 없음'", flush=True)
        report["종합"] = "전 탭 무효/판정불가 — KR은 종가베팅 외 검증된 진입법 없음"
    else:
        valid_tabs = [n for n in TABS if "KR 유효" in report["tabs"][n]["판정"]]
        report["종합"] = f"KR 유효 탭: {valid_tabs}" if valid_tabs else "유효 탭 없음(무효/판정불가 혼재)"
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
                        "2026-09-04_kr_confirm_entry_all_tabs_90cp.results.json")
    run(data, bench, out_path=out)

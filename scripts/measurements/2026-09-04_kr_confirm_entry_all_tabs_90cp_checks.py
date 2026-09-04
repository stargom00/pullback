"""
KR 단독 확인진입(안C) EV — ①손절기준/②레이스시작일/③슬리피지/④종목dedup
4개 확인 (2026-09-04, 사용자 지시 후속). 조사 전용 — 커밋 안 함, 기존
파일 무변경(README 규칙3 재사용만, 신규 스크립트).

【배경】원본(`2026-09-04_kr_confirm_entry_all_tabs_90cp.py`)의 결과 JSON엔
`ev_summary()` 집계값(nv/ev_R/stop_rate/target_rate)만 있고 히트별
원자료(ticker/entry/stop/future 경로)가 없어 ③④(슬리피지 재계산, 종목별
dedup)를 계산할 수 없었다. ①②는 코드 검토만으로 이미 답변 완료(아래
docstring에 결론 요약, 이 스크립트는 ①②를 재확인하는 부산물만 만듦).

【① 결론 — 재확인용, 이미 코드로 검증됨】
안C 손절(stop_val = h[stop_key])은 시그널일 hist에서만 계산됨(확인일
데이터로 재계산 안 함) — 원본 스크립트 `collect_hits()`/
`run_confirm_analysis()`와 동일 로직 그대로 재사용. app.py "오늘의 결정"
과의 불일치(돌파임박 `_registration_day_low`가 "등록일"을 "신호 최초
감지일"의 근사로 씀, 다를 수 있음 — app.py:9697-9716, 10055-10060)는
별도 보고 완료, 이 스크립트가 고치는 대상 아님.

【② 결론 — 재확인용, 이미 코드로 검증됨】
확인일 자체는 레이스에서 제외되고 그 다음 봉부터 시작
(`fut_after = future.iloc[k:]`, confirm day는 `future.iloc[k-1]`) —
이미 익일 시작이므로 EV원본과 EV익일은 항상 같은 값. 이 스크립트도
동일 슬라이싱을 그대로 재사용(원본 대비 변경 없음, 회귀 확인용).

【③ 슬리피지】trigger_price(=신호일 고가, 원 정의 그대로) 에 (1+slip)를
곱해 harness.race()를 재실행. stop은 불변(슬리피지는 체결가만 밀어내는
것이지 구조적 손절 레벨을 바꾸지 않음 — 원 정의 유지).

【④ 종목 dedup】같은 티커가 인접 체크포인트(10봉 간격, 확인window
최대 3봉)에서 반복 확인되면 겹치는 미래 구간을 공유해 독립표본이 아니다.
티커별로 signal_date가 가장 이른(=off가 가장 큰) 히트 1건만 남겨
재계산한다. 안A_전체/안C_확인진입분 각각 자기 모집단(전체 hit_list vs
확인된 subset) 안에서 독립적으로 dedup — 원본의 "안A는 전체, 안C는
확인분만"이라는 모집단 관계 자체는 유지하고 그 안에서만 중복을 접는다
(사용자가 다른 dedup 정의를 원하면 재조정 필요, 이 스크립트의 선택을
명시).

【원자료 저장 — 사용자 지시】KR 확인(안C) 히트 전건에 대해
ticker/offset/signal_date/confirm_date/entry/stop/race_outcome(슬리피지
3종 포함)을 결과 JSON에 그대로 저장 — 다음 재확인 때 전종목 재fetch
없이 이 JSON만으로 계산 가능하게.

【실행 순서 — 중요】2026-09-04 10:53부터 같은 레포에서 다른 세션이
`2026-09-04_confirm_entry_grid_search_5tabs.py`(전종목 재fetch, 20~40분
예상)를 돌리고 있었음(사용자 지시: "PID 76942 끝난 뒤 돌려라. 동시 실행
금지"). **이 스크립트를 실행하기 전에 반드시**
`ps aux | grep measurements`로 그 프로세스가 끝났는지 확인할 것.

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-04_kr_confirm_entry_all_tabs_90cp_checks.py`
(원본과 동일하게 전종목 재fetch 필요 — 원본 결과 JSON에 히트별 원자료가
없어서 불가피, 수십분 예상).
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

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — 원본과 동일(규칙9 표준)
CONFIRM_K_MAX = 3
CONFIRM_VOL_MULT = 1.5
SLIPPAGE_PCTS = [0.0, 0.003, 0.005]   # 원본(0%) + 사용자 지시 두 값

TABS = {
    "눌림목": {"fn": analyze, "cfg": CONFIG, "stop_key": "stop"},
    "돌파임박": {"fn": analyze_imminent, "cfg": IMMINENT_CONFIG, "stop_key": "signal_low"},
    "박스돌파": {"fn": analyze_boxbreak, "cfg": BOXBREAK_CONFIG, "stop_key": "stop"},
    "돌파": {"fn": analyze_breakout, "cfg": BREAKOUT_CONFIG, "stop_key": "stop"},
    "추세전환": {"fn": analyze_turnaround, "cfg": TURN_CONFIG, "stop_key": "stop"},
}
NEED_BARS = max(t["cfg"]["min_bars"] for t in TABS.values())


# ══════════════════════════════════════════════════════════════════
# 1단계: 히트 수집 — 원본과 동일 + signal_date만 추가(dedup용)
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
            signal_date = str(hist.index[-1].date())

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
                    "signal_date": signal_date,
                    "trailing50_vol": trailing50_vol, "future": future,
                })

        counts = {k: len(v) for k, v in hits.items()}
        print(f"[PASS1] offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s {counts}",
              flush=True)
    return hits


# ══════════════════════════════════════════════════════════════════
# 2단계: 확인조건 탐색 — 원본과 동일 로직 (find_confirm_close 그대로 복붙)
# ══════════════════════════════════════════════════════════════════
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
        if c > trigger and vv >= CONFIRM_VOL_MULT * base_vol:
            return k, trigger
    return None


# ══════════════════════════════════════════════════════════════════
# 3단계: 안A/안C + 슬리피지 3종 + 원자료 기록
# ══════════════════════════════════════════════════════════════════
def race_with_slippage(trigger_price, stop_val, fut_after):
    """슬리피지 배수별 race() 결과. entry만 밀어내고 stop은 원 정의 그대로
    (③ 사용자 지시: "진입가에 +0.3%, +0.5% 슬리피지")."""
    out = {}
    for slip in SLIPPAGE_PCTS:
        entry = trigger_price * (1 + slip)
        out[slip] = harness.race(entry, stop_val, fut_after)
    return out


def run_confirm_analysis(hit_list, stop_key, keep_raw=False):
    a_outcomes, hybrid_outcomes = [], []
    c_outcomes_by_slip = {s: [] for s in SLIPPAGE_PCTS}
    raw_records = []
    n_confirmed = 0
    for h in hit_list:
        a_out = harness.race(h["close"], h["stop"], h["future"])
        a_outcomes.append((h["ticker"], h["off"], h["signal_date"], a_out))
        conf = find_confirm_close(h)
        if conf is None:
            hybrid_outcomes.append(a_out)
            continue
        k, trigger_price = conf
        n_confirmed += 1
        fut_after = h["future"].iloc[k:]
        stop_val = h[stop_key]
        outs = race_with_slippage(trigger_price, stop_val, fut_after)
        for slip, out in outs.items():
            c_outcomes_by_slip[slip].append((h["ticker"], h["off"], h["signal_date"], out))
        hybrid_outcomes.append(outs[0.0])
        if keep_raw:
            confirm_date = str(h["future"].index[k - 1].date())
            raw_records.append({
                "ticker": h["ticker"], "offset": h["off"],
                "signal_date": h["signal_date"], "confirm_date": confirm_date,
                "entry": round(trigger_price, 4), "stop": round(stop_val, 4),
                "race_outcome": {f"slip_{slip}": list(outs[slip]) for slip in SLIPPAGE_PCTS},
            })

    def _dedup_first(labeled_outcomes):
        """(ticker, off, signal_date, outcome) 리스트 → 티커별 signal_date
        최솟값(=off 최댓값, 가장 이른 시점) 1건만 남김."""
        best = {}
        for ticker, off, sdate, out in labeled_outcomes:
            cur = best.get(ticker)
            if cur is None or sdate < cur[0]:
                best[ticker] = (sdate, out)
        return [v[1] for v in best.values()]

    ev_a = harness.ev_summary([o[3] for o in a_outcomes])
    ev_c_by_slip = {s: harness.ev_summary([o[3] for o in c_outcomes_by_slip[s]]) for s in SLIPPAGE_PCTS}
    ev_c = ev_c_by_slip[0.0]
    z, sig = harness.ev_gap_zscore(ev_a, ev_c)
    gap = (ev_c["ev_R"] - ev_a["ev_R"]) if (ev_c["ev_R"] is not None and ev_a["ev_R"] is not None) else None

    a_dedup = _dedup_first(a_outcomes)
    c_dedup = _dedup_first(c_outcomes_by_slip[0.0])
    ev_a_dedup = harness.ev_summary(a_dedup)
    ev_c_dedup = harness.ev_summary(c_dedup)
    z_dedup, sig_dedup = harness.ev_gap_zscore(ev_a_dedup, ev_c_dedup)

    result = {
        "n_hits_total": len(hit_list),
        "안A_전체": ev_a,
        "안C_확인진입분": ev_c,
        "안C_확인진입분_슬리피지": {f"{int(s*1000)/10}%": ev_c_by_slip[s] for s in SLIPPAGE_PCTS},
        "하이브리드_전체(미확인=안A로 대체)": harness.ev_summary(hybrid_outcomes),
        "확인율": round(n_confirmed / len(hit_list), 4) if hit_list else None,
        "안C_vs_안A_gap_R": gap,
        "안C_vs_안A_z": z,
        "안C_vs_안A_significant": sig,
        "dedup_티커당1건_안A": ev_a_dedup,
        "dedup_티커당1건_안C": ev_c_dedup,
        "dedup_안C_vs_안A_z": z_dedup,
        "dedup_안C_vs_안A_significant": sig_dedup,
    }
    if keep_raw:
        result["_raw_확인건"] = raw_records
    return result


def run(data, bench, out_path=None):
    hits = collect_hits(data, bench)

    report = {"offsets": f"{OFFSETS[0]}..{OFFSETS[-1]} step10 ({len(OFFSETS)}개)",
              "slippage_pcts_checked": SLIPPAGE_PCTS, "tabs": {}}

    for name, spec in TABS.items():
        stop_key = spec["stop_key"]
        hit_list = hits[name]
        kr_hits = [h for h in hit_list if h["is_kr"]]

        kr_result = run_confirm_analysis(kr_hits, stop_key, keep_raw=True) if kr_hits else None
        report["tabs"][name] = {
            "stop_key_used": stop_key, "n_kr": len(kr_hits),
            "KR": kr_result,
        }
        if kr_result:
            slip = kr_result["안C_확인진입분_슬리피지"]
            print(f"[결과] {name}: EV원본={kr_result['안C_확인진입분']['ev_R']:+.3f}R "
                  f"EV+0.3%={slip['0.3%']['ev_R']:+.3f}R EV+0.5%={slip['0.5%']['ev_R']:+.3f}R "
                  f"n_dedup={kr_result['dedup_티커당1건_안C']['nv']} "
                  f"z_dedup={kr_result['dedup_안C_vs_안A_z']}", flush=True)

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
                        "2026-09-04_kr_confirm_entry_all_tabs_90cp_checks.results.json")
    run(data, bench, out_path=out)

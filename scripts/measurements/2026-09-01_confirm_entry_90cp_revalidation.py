"""
안C(돌파임박 확인진입)/안C'(눌림목 확인진입) 90개 체크포인트 재검증
(2026-09-01, 우선순위5, README 규칙9).

배경: "오늘의 결정"(v5.136) 🔴 즉시행동이 두 EV를 실거래 근거로 인용하는데
(안C 0.796R,n=701 / 안C' 0.923R,n=261), 둘 다 checkpoints(60,250,10)=20개
계열 원측정이다 — 오늘 규칙9로 재검증한 4건(depth_atr/RS게이트E/다중히트/
RSI<50)이 전부 90개에서 채택 철회된 것과 같은 출신이라 사용자 지시로
우선순위5 등록.

두 규칙은 서로 다른 원 정의를 쓴다(docs/imminent_stop_entry_investigation.md
3.1절 / docs/pullback_stop_width_and_entry_timing.md ③절) — 여기서도 그대로
구분해서 잰다:
  - 안C(돌파임박): 확인조건 = 신호일 다음 최대 3봉 내 "고가"가 신호일
    고가를 돌파 + 그날 거래량 트레일링50일평균의 1.5배. 손절 = 신호일 저가
    (analyze_imminent()의 구조적 stop이 아니라 재정의).
  - 안C'(눌림목): 확인조건 = 신호일 다음 최대 3봉 내 "종가"가 신호일 고가를
    초과 + 거래량 1.5배. 손절 = 안A와 동일(재정의 안 함, analyze()의 stop
    그대로).
둘 다 진입가는 신호일 고가 레벨(안C/안C' 공통 관례).

방법론: 공통 하네스(harness.py) 재사용. 90개 체크포인트 =
checkpoints(60,950,10), 원측정 대비 fetch를 kr_days=1900/us_period=5y로
확장(harness.fetch_universe_data 문서 경고 — 확장 안 하면 offset>250에서
후반 체크포인트가 조용히 빈다, 오늘 우선순위1에서 실제로 겪은 사고).
2R 레이스는 harness.race()(진입 후 60봉, 저가≤손절 우선판정, 미결=0R).

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-01_confirm_entry_90cp_revalidation.py`
(확장 fetch 포함 총 10~20분 예상, 네트워크 필요).
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze, CONFIG, analyze_imminent, IMMINENT_CONFIG

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — 규칙9 표준


def collect_hits(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())

    pb_hits, im_hits = [], []
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)

        trunc_cache = {}
        for t in tickers:
            df = data[t]
            need = max(CONFIG["min_bars"], IMMINENT_CONFIG["min_bars"])
            if len(df) - off < need:
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

            try:
                hpb = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=ikr)
            except Exception:
                hpb = None
            if hpb is not None and harness.passes_liquidity_filter(hpb, ikr):
                pb_hits.append({
                    "ticker": t, "off": off, "close": hpb.get("close"), "stop": hpb.get("stop"),
                    "signal_high": signal_high, "trailing50_vol": trailing50_vol, "future": future,
                })

            try:
                him = analyze_imminent(hist, rs_rank=rr, rs_mom=rm, cfg=IMMINENT_CONFIG, is_kr=ikr)
            except Exception:
                him = None
            if him is not None and harness.passes_liquidity_filter(him, ikr):
                im_hits.append({
                    "ticker": t, "off": off, "close": him.get("close"), "stop": him.get("stop"),
                    "signal_high": signal_high, "signal_low": signal_low,
                    "trailing50_vol": trailing50_vol, "future": future,
                })

        print(f"offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
              f"pb={len(pb_hits)} im={len(im_hits)}", flush=True)
    return pb_hits, im_hits


def find_confirm_close(h, k_max=3):
    """안C'(눌림목): 종가 기준 확인 — 원 스크립트(2026-08-14)와 동일 로직."""
    fut = h["future"]
    trigger = h["signal_high"]
    base_vol = h["trailing50_vol"]
    avail = min(k_max, len(fut))
    for k in range(1, avail + 1):
        c = float(fut["Close"].iloc[k - 1])
        vv = float(fut["Volume"].iloc[k - 1])
        if c > trigger and vv >= 1.5 * base_vol:
            return k, trigger, c
    return None


def find_confirm_high(h, k_max=3):
    """안C(돌파임박): 장중 고가 기준 확인 — imminent_stop_entry_investigation.md
    3.1절 원 정의("고가를 장중 고가로 돌파")."""
    fut = h["future"]
    trigger = h["signal_high"]
    base_vol = h["trailing50_vol"]
    avail = min(k_max, len(fut))
    for k in range(1, avail + 1):
        hi = float(fut["High"].iloc[k - 1])
        vv = float(fut["Volume"].iloc[k - 1])
        if hi > trigger and vv >= 1.5 * base_vol:
            return k, trigger
    return None


def run_pullback(pb_hits):
    a_outcomes, c_outcomes, d_outcomes, hybrid_outcomes = [], [], [], []
    n_confirmed = 0
    for h in pb_hits:
        a_out = harness.race(h["close"], h["stop"], h["future"])
        a_outcomes.append(a_out)
        conf = find_confirm_close(h)
        if conf is None:
            hybrid_outcomes.append(a_out)
            continue
        k, trigger_price, close_price = conf
        n_confirmed += 1
        fut_after = h["future"].iloc[k:]
        c_out = harness.race(trigger_price, h["stop"], fut_after)
        d_out = harness.race(close_price, h["stop"], fut_after)
        c_outcomes.append(c_out)
        d_outcomes.append(d_out)
        hybrid_outcomes.append(c_out)

    return {
        "n_hits_total": len(pb_hits),
        "안A_전체": harness.ev_summary(a_outcomes),
        "안C'_진입분": harness.ev_summary(c_outcomes),
        "안D'_진입분(시점매칭 대조군)": harness.ev_summary(d_outcomes),
        "하이브리드_전체(미진입=안A로 대체)": harness.ev_summary(hybrid_outcomes),
        "진입률_C'": round(n_confirmed / len(pb_hits), 4) if pb_hits else None,
    }


def run_imminent(im_hits):
    a_outcomes, c_outcomes, hybrid_outcomes = [], [], []
    n_confirmed = 0
    for h in im_hits:
        a_out = harness.race(h["close"], h["stop"], h["future"])
        a_outcomes.append(a_out)
        conf = find_confirm_high(h)
        if conf is None:
            hybrid_outcomes.append(a_out)
            continue
        k, trigger_price = conf
        n_confirmed += 1
        fut_after = h["future"].iloc[k:]
        # 안C 손절 = 신호일 저가(원 정의 3.1 — analyze_imminent()의 구조적
        # stop과 다름, 재정의).
        c_out = harness.race(trigger_price, h["signal_low"], fut_after)
        c_outcomes.append(c_out)
        hybrid_outcomes.append(c_out)

    return {
        "n_hits_total": len(im_hits),
        "안A_전체": harness.ev_summary(a_outcomes),
        "안C_진입분": harness.ev_summary(c_outcomes),
        "하이브리드_전체(미진입=안A로 대체)": harness.ev_summary(hybrid_outcomes),
        "진입률_C": round(n_confirmed / len(im_hits), 4) if im_hits else None,
    }


def run(data, bench, out_path=None):
    pb_hits, im_hits = collect_hits(data, bench)
    report = {
        "offsets": f"{OFFSETS[0]}..{OFFSETS[-1]} step10 ({len(OFFSETS)}개)",
        "pullback_안C'": run_pullback(pb_hits),
        "imminent_안C": run_imminent(im_hits),
    }
    pb_report = report["pullback_안C'"]
    im_report = report["imminent_안C"]
    print(f"[PULLBACK 안C'] {pb_report}", flush=True)
    print(f"[IMMINENT 안C] {im_report}", flush=True)
    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(kr_days=1900, us_period="5y")
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-09-01_confirm_entry_90cp_revalidation.results.json")
    run(data, bench, out_path=out)

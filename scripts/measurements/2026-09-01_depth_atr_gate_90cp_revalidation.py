"""
depth_atr 게이트(v5.71) 규칙9 재검증 — 90개 체크포인트 (2026-09-01)

배경: `docs/kr_us_strategy_map.md` "20개 창 채택 결론 재검증 대기 목록"
우선순위1. depth_atr 게이트는 원래 KR+US 혼합 20개 체크포인트로 채택됐고
(`docs/rs_gate_e_and_depth_atr_v5.71.md`, cohort c EV=0.194R > cohort a
EV=0.108R), 이후 규칙8(KR/US분리) 도입 후 재분해에서 KR z=1.37·US
z=0.99 둘 다 비유의로 나왔다(`kr_us_strategy_map.md` "결과" 표) — 근데
이것도 checkpoints(60,250,10)=20개였다(README 규칙9 미충족). 이 스크립트는
같은 cohort(c) 정의를 checkpoints(60,950,10)=90개로 재측정한다.

【방법론 — 원측정과 동일, 체크포인트 수만 확대】
`2026-08-23_reject_tracer_ev_and_gate_e.py`의 cohort(c) 정의를 그대로
재현하되, 그때는 v5.71 적용 "전" 코드로 재고 지금은 v5.71 적용 "후"(RS
게이트E·depth_atr 둘 다 이미 CONFIG에 반영된 현재 프로덕션 코드) 상태라
접근이 살짝 다르다:
  - 원 스크립트(2026-08-23): 그 시점 CONFIG는 RS게이트=A(구), 눌림폭=고정%.
    `CFG_NO_DEPTH_GATE`로 눌림폭만 무력화해 depth_atr[0.5,3.0] 안이면서
    고정% 범위 밖인 신호를 "증분"으로 잡음. RS는 그때 코드 그대로(A) 사용.
  - 이 스크립트(2026-09-01): 지금 CONFIG는 이미 RS게이트=E, 눌림폭=depth_atr.
    고정%(pullback_min=0.03/pullback_max_kr=0.15/pullback_max_us=0.12,
    v5.71 커밋 이전 값 — 이제 CONFIG에 없어 스크립트에 상수로 고정)를
    스크립트 안에서만 참조값으로 되살려 같은 비교를 한다. RS는 현재
    라이브 게이트(E)를 그대로 쓴다 — depth_atr는 실제로 "E가 이미 걸린
    상태" 위에서 작동하는 게 오늘의 진짜 파이프라인이라, E를 A로 되돌려
    측정하면 오늘 실제로 안 쓰이는 가상의 파이프라인을 재는 셈이 된다.
    (우선순위2에서 RS게이트E 자체를 같은 방식으로 재검증할 예정 — 서로
    독립적인 재검증이라 이 스크립트에서 RS를 건드릴 필요 없음.)
  - cohort(c) 판정: `CFG_NO_DEPTH_GATE`(depth_atr_min=-999/max=999, 항상
    통과)로 `analyze()`를 돌려 나머지 게이트(RS-E 포함)를 전부 통과한
    신호 중, `hit["pullback_pct"]`가 구 고정% 범위 **밖**이면서
    `hit["depth_atr"]`가 [0.5,3.0] **안**인 것만 증분으로 채택 — 이미
    구 범위 안이면 어차피 옛날에도 잡혔을 신호라 증분이 아님(원 스크립트
    로직 그대로).
  - `analyze()`가 `pullback_pct`/`depth_atr`를 이미 hit 필드로 반환하므로
    (v5.71부터), 원 스크립트에 있던 `depth_atr_for()` 수동 재계산 함수는
    이번엔 불필요 — 실제 함수가 계산한 값을 그대로 읽는다(리터럴 사본
    회피, CLAUDE.md 원칙).

【규칙6/7/8/9】 유동성(`harness.passes_liquidity_filter`), 유의성
(`harness.ev_gap_zscore` — 원측정과 동일 함수), KR/US 완전분리 보고,
90개 체크포인트(`harness.checkpoints(60,950,10)`).

【최초 실행 실패 — fetch 깊이 부족, 재현 전 발견】 첫 실행에서 체크포인트
31/90(offset 450) 이후 cohort(a)/(c) 건수가 완전히 멈췄다(a=3175,
c=691에서 고정, 체크포인트 40~90 전부 신규 0건) — `harness.fetch_universe_data()`
가 US를 고정 `period="2y"`(≈505봉)로만 받아서, offset 950 체크포인트가
필요로 하는 최소 950+210(min_bars)=1160봉을 절대 못 채웠기 때문(60~350
구간만 실제로 데이터가 있었던 것). 이건 이미 알려진 문제였다 —
`2026-08-31_kr_pullback_support_breach_1900d.py`의 docstring이 정확히
같은 사유로 harness 기본 fetch 대신 KR `days=1900`/US `period="5y"` 자체
확장 fetch를 쓴다고 명시해뒀는데, 그 전례를 먼저 안 찾아보고 harness
기본값으로 돌렸다가 이 스크립트에서도 똑같이 걸렸다. 그 스크립트의
확장 fetch(`fetch_kr_long_universe`/`fetch_us_long_universe`)를 그대로
가져와 교체 — 아래 코드가 그 결과.

근거 문서: docs/kr_us_strategy_map.md (재검증 결과 절 추가 예정)
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import naver_kr
from universe import get_universe

import harness
from scanner import CONFIG, analyze, to_rs_rank

KR_FETCH_DAYS = 1900          # 2026-08-31_kr_pullback_support_breach_1900d.py와 동일(≈1274봉)
US_FETCH_PERIOD = "5y"        # 위와 동일 — offset 950+min_bars 210=1160봉 확보


def _fetch_kr_long(ticker):
    try:
        df = naver_kr.fetch_history(ticker, days=KR_FETCH_DAYS)
        return ticker, (df if df is not None and not df.empty else None)
    except Exception:
        return ticker, None


def fetch_kr_long_universe(concurrency=10):
    kr_u = get_universe("kr")
    data = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(_fetch_kr_long, t): t for t in kr_u}
        done = 0
        for fut in as_completed(futs):
            t, df = fut.result()
            if df is not None:
                data[t] = df
            done += 1
            if done % 300 == 0:
                print(f"[fetch-kr] {done}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"[fetch-kr] 완료 {len(data)}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    return data


def _fetch_us_batch_long(tickers):
    import yfinance as yf
    out = {}
    if not tickers:
        return out
    try:
        raw = yf.download(tickers, period=US_FETCH_PERIOD, interval="1d",
                           auto_adjust=True, group_by="ticker", threads=True, progress=False)
    except Exception:
        return out
    if raw is None or len(raw) == 0:
        return out
    single = len(tickers) == 1
    for t in tickers:
        try:
            df = raw.copy() if single else raw[t].copy()
            df = df.dropna(how="all")
            if df is None or df.empty or "Close" not in df.columns or df["Close"].dropna().empty:
                continue
            out[t] = df[df["Close"].notna()]
        except Exception:
            continue
    return out


def fetch_us_long_universe(batch_size=100):
    us_u = get_universe("us")
    us_tickers = list(us_u.keys())
    data = {}
    t0 = time.time()
    batches = [us_tickers[i:i + batch_size] for i in range(0, len(us_tickers), batch_size)]
    for i, b in enumerate(batches):
        data.update(_fetch_us_batch_long(b))
        print(f"[fetch-us] batch {i+1}/{len(batches)} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"[fetch-us] 완료 {len(data)}/{len(us_tickers)} elapsed={time.time()-t0:.0f}s", flush=True)
    return data

RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200

# v5.71 커밋(7ca3391) 직전 CONFIG 값 — 이제 라이브 CONFIG엔 없어 여기서만
# 참조용 상수로 고정(git show 7ca3391으로 확인한 실제 구값, 재구현 아니라
# 역사적 상수 그대로 인용).
OLD_PULLBACK_MIN = 0.03
OLD_PULLBACK_MAX_KR = 0.15
OLD_PULLBACK_MAX_US = 0.12

CFG_NO_DEPTH_GATE = dict(CONFIG)
CFG_NO_DEPTH_GATE["depth_atr_min"] = -999.0
CFG_NO_DEPTH_GATE["depth_atr_max"] = 999.0


def rs_3m_ranks(trunc_cache):
    kr3, us3 = {}, {}
    for t, hist in trunc_cache.items():
        r3 = harness.ret_pct(hist["Close"], 63)
        if r3 is None:
            continue
        if harness.is_kr_ticker(t):
            kr3[t] = r3
        else:
            us3[t] = r3
    return {**to_rs_rank(kr3), **to_rs_rank(us3)}


def log(msg):
    print(msg, flush=True)


def main():
    t0 = time.time()
    data = fetch_kr_long_universe()
    data.update(fetch_us_long_universe())
    log(f"[main] 전체 유니버스 fetch 완료 {len(data)}종목 elapsed={time.time()-t0:.0f}s")
    bench = harness.fetch_kr_benchmarks(days=KR_FETCH_DAYS)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())

    offsets = harness.checkpoints(60, 950, 10)   # 90개(규칙9)
    extra = sorted(set(offsets) | {o + RS_DELTA_LOOKBACK for o in offsets})
    log(f"[main] 체크포인트 {len(offsets)}개, RS사전계산 {len(extra)}개 지점")

    rs_cache, r3_cache = {}, {}
    for i, off in enumerate(extra):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < RS_MIN_BARS:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)
        rs_cache[off] = (rs_ranks, rs_moms)
        r3_cache[off] = rs_3m_ranks(trunc_cache)
        if (i + 1) % 20 == 0 or i == len(extra) - 1:
            log(f"[rs] {i+1}/{len(extra)} elapsed={time.time()-t0:.0f}s")

    cohort_a, cohort_c = [], []   # a=현재 프로덕션(참고), c=depth_atr 증분(핵심)
    for oi, off in enumerate(offsets):
        rs_ranks, rs_moms = rs_cache[off]
        r3_ranks = r3_cache[off]
        rs_20ago, _ = rs_cache.get(off + RS_DELTA_LOOKBACK, ({}, {}))

        for t in tickers:
            df = data[t]
            if len(df) - off < CONFIG["min_bars"]:
                continue
            ikr = harness.is_kr_ticker(t)
            hist = harness.truncate_at(df, off)
            rs = rs_ranks.get(t)
            rs3m = r3_ranks.get(t)
            rs_delta = (rs - rs_20ago.get(t)) if (rs is not None and t in rs_20ago) else None
            rm = rs_moms.get(t)
            future = harness.future_after(df, off)

            try:
                hit_a = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CONFIG, is_kr=ikr,
                                 rs_3m=rs3m, rs_delta=rs_delta)
            except Exception:
                hit_a = None
            if hit_a is not None and harness.passes_liquidity_filter(hit_a, ikr):
                cohort_a.append({"ticker": t, "is_kr": ikr, "close": hit_a.get("close"),
                                  "stop": hit_a.get("stop"), "future": future})

            try:
                hit_nodepth = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CFG_NO_DEPTH_GATE, is_kr=ikr,
                                       rs_3m=rs3m, rs_delta=rs_delta)
            except Exception:
                hit_nodepth = None
            if hit_nodepth is None or not harness.passes_liquidity_filter(hit_nodepth, ikr):
                continue
            pb_frac = (hit_nodepth.get("pullback_pct") or 0.0) / 100.0
            pb_max = OLD_PULLBACK_MAX_KR if ikr else OLD_PULLBACK_MAX_US
            within_old_standard = OLD_PULLBACK_MIN <= pb_frac <= pb_max
            if within_old_standard:
                continue   # 구 고정% 범위 안 = 옛날에도 잡혔을 신호, 증분 아님
            da = hit_nodepth.get("depth_atr")
            if da is None or not (0.5 <= da <= 3.0):
                continue
            cohort_c.append({"ticker": t, "is_kr": ikr, "close": hit_nodepth.get("close"),
                              "stop": hit_nodepth.get("stop"), "future": future})

        if (oi + 1) % 10 == 0 or oi == len(offsets) - 1:
            log(f"[ev] checkpoint {off} 완료 ({oi+1}/{len(offsets)}) elapsed={time.time()-t0:.0f}s "
                f"a={len(cohort_a)} c={len(cohort_c)}")

    report(cohort_a, cohort_c)
    log(f"\n[main] 총 소요시간 {time.time()-t0:.0f}s")


def report(cohort_a, cohort_c):
    def _ev(hits):
        outcomes = [harness.race(h["close"], h["stop"], h["future"]) for h in hits]
        return harness.ev_summary(outcomes)

    log("\n" + "=" * 70)
    log("결과 — depth_atr 증분(cohort c) KR/US 분해, 90개 체크포인트")
    log("=" * 70)
    c_kr = [h for h in cohort_c if h["is_kr"]]
    c_us = [h for h in cohort_c if not h["is_kr"]]
    ev_c_kr, ev_c_us = _ev(c_kr), _ev(c_us)
    log(f"  KR: n={ev_c_kr['n_hits']} EV={ev_c_kr['ev_R']:.3f}R" if ev_c_kr['ev_R'] is not None
        else f"  KR: n={ev_c_kr['n_hits']} EV=N/A(표본부족)")
    log(f"  US: n={ev_c_us['n_hits']} EV={ev_c_us['ev_R']:.3f}R" if ev_c_us['ev_R'] is not None
        else f"  US: n={ev_c_us['n_hits']} EV=N/A(표본부족)")
    if ev_c_kr['ev_R'] is not None and ev_c_us['ev_R'] is not None:
        gap = ev_c_us['ev_R'] - ev_c_kr['ev_R']
        z, sig = harness.ev_gap_zscore(ev_c_kr, ev_c_us)
        log(f"  gap(US-KR)={gap:+.3f}R  z={z:.2f}({'유의' if sig else '유의하지 않음'})" if z is not None
            else f"  gap(US-KR)={gap:+.3f}R  z=계산불가")
    log("\n  [원측정(20개 체크포인트) 대비]")
    log("  KR n=92 EV=-0.022R / US n=310 EV=0.210R / gap=+0.231R / z=1.37(비유의)")

    log("\n" + "=" * 70)
    log("참고 — cohort(a) 현재 프로덕션 신호(KR+US 혼합, depth_atr 게이트 포함) EV")
    log("=" * 70)
    ev_a = _ev(cohort_a)
    log(f"  n={ev_a['n_hits']} EV={ev_a['ev_R']:.3f}R" if ev_a['ev_R'] is not None
        else f"  n={ev_a['n_hits']} EV=N/A")
    ev_c_mixed = _ev(cohort_c)
    log(f"  cohort(c) 증분 혼합 EV: n={ev_c_mixed['n_hits']} EV={ev_c_mixed['ev_R']:.3f}R"
        if ev_c_mixed['ev_R'] is not None else f"  cohort(c) 혼합: n={ev_c_mixed['n_hits']} EV=N/A")
    log("  [원측정 참고] cohort(a) 현행A n=1838 EV=0.108R / cohort(c) 증분 n=480 EV=0.194R"
        " (2026-08-23, RS게이트A 기준·checkpoints 20개 — 오늘 측정과 RS게이트가 달라 직접비교 주의)")


if __name__ == "__main__":
    main()

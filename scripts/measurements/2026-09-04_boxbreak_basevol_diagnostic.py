"""
박스돌파 KR 확인율 상승(+1.61pp, EV +0.107R) 원인 진단 (2026-09-04,
사용자 지시) — 조사 전용, app.py/scanner.py 무변경, 커밋 규칙 준수
(README 규칙3 재사용만).

【배경】
trailing50_vol을 plain .mean() → scanner.nonzero_vol_mean()으로 바꾼
재측정(measure-basevol-nonzero, 2026-09-04_kr_confirm_entry_all_tabs_
90cp_basevol_nonzero.results.json)에서 박스돌파 KR만 사전등록 허용폭
(EV ±0.05R, 확인율 ±2pp)을 크게 벗어났다(EV +0.107R, 확인율 +1.61pp).
사용자 지시로 원인을 3가지 각도에서 확인한다:
1. 코드 diff가 정확히 그 한 줄인지(이미 `git show fae25cb` 확인 완료 —
   두 스크립트 다 trailing50_vol 계산식 한 줄 + import/출력경로 주석뿐,
   find_confirm_close()의 비교식 자체는 안 바뀜).
2. `nonzero_vol_mean()`이 유효봉 0개일 때 0.0을 반환하는데, 이 값이
   find_confirm_close()의 `vv >= CONFIRM_VOL_MULT * base_vol` 비교에서
   `CONFIRM_VOL_MULT * 0 = 0`이 되어 "아무 거래량이나 통과"로 새는지
   (base_vol>0 가드가 없음을 코드로 이미 확인 — 실제로 이 경로를 타는
   히트가 있는지가 이 스크립트의 목적).
3. 박스돌파 KR 안C 히트 중 50봉 창에 거래정지(0거래량)일이 1개 이상
   섞인 비율과, 그 히트들만 따로 뗀 확인율(구/신) 변화.

방법론: harness.py 재사용(fetch/RS/유동성필터), 원 재측정과 동일
OFFSETS=checkpoints(60,950,10), kr_days=1900 — 단 US는 이번 진단과
무관해 markets=("kr",)로 생략(재현 시간 단축, 결과는 KR 전용이라
영향 없음). 박스돌파 탭만 계산(다른 4탭 스킵)해 추가로 단축.

실행: `python3 scripts/measurements/2026-09-04_boxbreak_basevol_diagnostic.py`
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze_boxbreak, BOXBREAK_CONFIG, nonzero_vol_mean

OFFSETS = harness.checkpoints(60, 950, 10)
CONFIRM_K_MAX = 3
CONFIRM_VOL_MULT = 1.5


def collect(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    hits = []
    need_bars = BOXBREAK_CONFIG["min_bars"]

    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < need_bars:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            if not ikr:
                continue
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            try:
                r = analyze_boxbreak(hist, rs_rank=rr, rs_mom=rm, cfg=BOXBREAK_CONFIG, is_kr=ikr)
            except Exception:
                r = None
            if r is None or not harness.passes_liquidity_filter(r, ikr):
                continue
            future = harness.future_after(data[t], off)
            signal_high = float(hist["High"].iloc[-1])
            vol_window = hist["Volume"].iloc[-50:]
            vol_old = float(vol_window.mean())
            vol_new = float(nonzero_vol_mean(vol_window))
            n_zero_days = int((vol_window == 0).sum())
            hits.append({
                "ticker": t, "off": off,
                "close": r.get("close"), "stop": r.get("stop"),
                "signal_high": signal_high,
                "vol_old": vol_old, "vol_new": vol_new,
                "n_zero_days": n_zero_days, "future": future,
            })
        counts = len(hits)
        print(f"[PASS1] offset {off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s n_hits={counts}", flush=True)
    return hits


def find_confirm(h, base_vol_key, k_max=CONFIRM_K_MAX):
    """원 스크립트의 find_confirm_close()와 동일 로직(base_vol==0 가드
    없음 — 이게 실제로 문제인지 확인하는 게 이 진단의 목적이라 일부러
    그대로 재현)."""
    fut = h["future"]
    trigger = h["signal_high"]
    base_vol = h[base_vol_key]
    avail = min(k_max, len(fut))
    for k in range(1, avail + 1):
        c = float(fut["Close"].iloc[k - 1])
        vv = float(fut["Volume"].iloc[k - 1])
        if c > trigger and vv >= CONFIRM_VOL_MULT * base_vol:
            return k
    return None


def main():
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",), kr_days=1900, validate_offsets=OFFSETS)
    bench = harness.fetch_kr_benchmarks()
    hits = collect(data, bench)
    print(f"\n총 박스돌파 KR 히트: {len(hits)}")

    # ── 2) base_vol==0(또는 극소) 경로가 실제로 통과에 기여했는지 ──
    zero_base_new = [h for h in hits if h["vol_new"] <= 0]
    zero_base_old = [h for h in hits if h["vol_old"] <= 0]
    print(f"\n[진단2] base_vol<=0인 히트 — 신규정의(nonzero_vol_mean): {len(zero_base_new)}건 / "
          f"구정의(plain mean): {len(zero_base_old)}건")
    if zero_base_new:
        confirmed_via_zero_bug = [h for h in zero_base_new if find_confirm(h, "vol_new") is not None]
        print(f"  이 중 실제로 '확인'으로 잡힌 건(버그 의심 경로 통과): {len(confirmed_via_zero_bug)}건")
        for h in zero_base_new[:5]:
            print(f"    예시: {h['ticker']} off={h['off']} vol_new={h['vol_new']} n_zero_days={h['n_zero_days']}")

    # ── 3) 0거래량일 포함 여부로 나눠서 확인율 구/신 비교 ──
    with_zero = [h for h in hits if h["n_zero_days"] >= 1]
    without_zero = [h for h in hits if h["n_zero_days"] == 0]
    print(f"\n[진단3] 50봉 창에 거래정지(0거래량)일 1개 이상 포함된 히트: "
          f"{len(with_zero)}/{len(hits)} ({100*len(with_zero)/len(hits):.2f}%)")

    def confirm_rate(subset, key):
        if not subset:
            return None, 0
        n_conf = sum(1 for h in subset if find_confirm(h, key) is not None)
        return n_conf / len(subset), n_conf

    for label, subset in (("0거래량일 있음", with_zero), ("0거래량일 없음", without_zero)):
        rate_old, n_old = confirm_rate(subset, "vol_old")
        rate_new, n_new = confirm_rate(subset, "vol_new")
        print(f"  {label}(n={len(subset)}): 확인율 구={rate_old} ({n_old}건) "
              f"신={rate_new} ({n_new}건) Δ={None if rate_old is None else round((rate_new-rate_old)*100,2)}pp")

    rate_old_all, n_old_all = confirm_rate(hits, "vol_old")
    rate_new_all, n_new_all = confirm_rate(hits, "vol_new")
    print(f"\n[전체] 확인율 구={rate_old_all:.4f}({n_old_all}) 신={rate_new_all:.4f}({n_new_all}) "
          f"Δ={100*(rate_new_all-rate_old_all):.2f}pp")

    # vol_new < vol_old인 히트(=0거래량일이 있어 평균이 올라간 경우) 중
    # "구정의로는 확인 실패했는데 신정의로는 확인 성공"한 건이 실제
    # 확인율 상승의 직접 원인인지 짚어본다.
    flipped_to_confirmed = [h for h in hits
                             if find_confirm(h, "vol_old") is None and find_confirm(h, "vol_new") is not None]
    flipped_to_unconfirmed = [h for h in hits
                               if find_confirm(h, "vol_old") is not None and find_confirm(h, "vol_new") is None]
    print(f"\n[전환] 구=미확인→신=확인: {len(flipped_to_confirmed)}건 / "
          f"구=확인→신=미확인: {len(flipped_to_unconfirmed)}건")
    if flipped_to_confirmed:
        avg_vol_old = sum(h["vol_old"] for h in flipped_to_confirmed) / len(flipped_to_confirmed)
        avg_vol_new = sum(h["vol_new"] for h in flipped_to_confirmed) / len(flipped_to_confirmed)
        avg_zero_days = sum(h["n_zero_days"] for h in flipped_to_confirmed) / len(flipped_to_confirmed)
        print(f"  전환 히트 평균: vol_old={avg_vol_old:.0f} vol_new={avg_vol_new:.0f} "
              f"n_zero_days={avg_zero_days:.2f}")


if __name__ == "__main__":
    main()

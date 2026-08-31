"""
KR 눌림목 "지지 붕괴" 가설 재측정 — 표본 확대판 (2026-08-31, 사용자 지시,
docs/kr_us_market_structure.md §6 후속). §6(scripts/measurements/
2026-08-31_kr_pullback_support_breach.py, checkpoints 60~250)이 z=1.87로
1.96에 근소 미달했던 것을, "종가베팅 확장측정" 방식(2026-08-29_kr_jongga_
betting_backtest_extended.py)과 동일한 표본확대 기법 — KR fetch를
naver_kr.fetch_history(days=1900)로 늘리고 체크포인트를 60~950(90개,
10간격)으로 4.5배 확장 — 으로 재실행해 표본을 키운다.

【KR/US 비대칭 fetch에 대한 판단(README 규칙3: 하네스와 다르게 재는 이유
명시)】 jongga 확장측정은 KR 전용이라 US 쪽 "1900일 상당" 관행이 이
레포에 없다. harness.py의 US fetch는 고정 period="2y"라 60~950
체크포인트를 감당 못한다(최대 offset 950 + min_bars 210 = 1160봉 필요,
2y≈505봉으로는 부족). 그래서 US도 동일 사상으로 yf.download(period="5y")
(~1260봉 실측 확보, KR 1900일≈1274봉과 비슷한 규모)으로 자체 확장
fetch를 구현했다 — harness._fetch_us_batch를 그대로 못 쓰고 새로 작성한
유일한 이유가 이 period 파라미터 하나뿐이라는 것을 명시해둔다.

측정 스크립트만 — scanner.py/app.py 미수정. §6과 동일한 조작적 정의
그대로 재사용(N=5거래일, 종가기준 이탈, 확인진입 거래량배수 1.5x) —
바뀐 건 표본 크기뿐.

【사전 등록 3개】
1. 지지 이탈률 KR vs US 격차 z>=1.96 (§6과 동일 정의, 표본만 확대)
2. (신규) 이탈 시 손실 크기: KR 중앙값이 US의 1.5배 이상 & Mann-Whitney U
   검정으로 분포 차이 유의 (scipy 미설치 환경이라 정규근사 Mann-Whitney를
   직접 구현 — 동순위 보정 포함, |z|>=1.96를 유의 기준으로 사용해 이
   프로젝트의 다른 z검정들과 일관성 유지)
3. (신규) 손절 미끄러짐: KR 눌림목 히트의 명목 손절폭(analyze() pullback이
   반환하는 hit["risk_pct"], _rr_block()에서 (base-stop_eff)/base*100로
   계산된 실제 값 — "3.3%"는 사용자가 든 대표예시일 뿐 실제 중앙값은
   이 스크립트가 직접 측정) vs 이탈이 실제 발생했을 때 이탈일 종가에서
   실현된 손실폭(§6과 동일 정의: (이탈일종가-신호일종가)/신호일종가) 비교.

규칙6: 대조군 개념이 원형 그대로 적용되진 않지만(§6과 동일 사유),
KR/US 둘 다 harness.passes_liquidity_filter를 통과한 히트만 사용.
규칙7: 이산 R분포 z검정(harness.ev_gap_zscore)은 여기선 안 씀(이 스크립트는
EV 비교가 아니라 비율·분포 비교) — 대신 표준 2표본 비율 z검정(§6과 동일
two_proportion_z)과 Mann-Whitney U 정규근사 z검정을 씀. 둘 다 "격차만으론
우연과 구분 안 된다"는 규칙7의 취지를 유지.
규칙8: KR/US 전 절 분리 보고, 혼합 수치 없음.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-31_kr_pullback_support_breach_1900d.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
import math
import statistics as stats
from concurrent.futures import ThreadPoolExecutor, as_completed

import naver_kr
from universe import get_universe

import harness
from scanner import analyze, CONFIG

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — jongga 확장측정과 동일 규모
N_WINDOW = 5
VOL_MULT = 1.5
KR_FETCH_DAYS = 1900          # jongga 확장측정과 동일(≈1274봉 실측)
US_FETCH_PERIOD = "5y"        # KR 1900일에 상당하는 규모로 자체 확장(위 docstring 참고)
MIN_BARS_AFTER_OFFSET = CONFIG["min_bars"]  # 210 — max offset(950)+210=1160, 양쪽 다 여유


def is_kr(t):
    return harness.is_kr_ticker(t)


# ── 확장 fetch (harness 고정 period로는 60~950 체크포인트를 못 감당해서 자체 구현) ──
def _fetch_kr_long(ticker):
    try:
        df = naver_kr.fetch_history(ticker, days=KR_FETCH_DAYS)
        if df is None or df.empty:
            return ticker, None
        return ticker, df
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
                           auto_adjust=True, group_by="ticker",
                           threads=True, progress=False)
    except Exception:
        return out
    if raw is None or len(raw) == 0:
        return out
    single = len(tickers) == 1
    for t in tickers:
        try:
            df = raw.copy() if single else raw[t].copy()
            df = df.dropna(how="all")
            if df is None or df.empty or "Close" not in df.columns:
                continue
            if df["Close"].dropna().empty:
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


def two_proportion_z(k1, n1, k2, n2):
    """§6과 동일 — 표준 2표본 비율 z검정."""
    if n1 == 0 or n2 == 0:
        return None, False
    p1, p2 = k1 / n1, k2 / n2
    p_pool = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None, False
    z = (p1 - p2) / se
    return z, abs(z) >= 1.96


def mannwhitney_u(x: list, y: list):
    """scipy 미설치라 정규근사 Mann-Whitney U를 직접 구현(동순위 보정 포함).
    반환: (u, z, p_two_tailed, significant). n이 작으면(<~10) 정규근사가
    부정확할 수 있음 — 호출부에서 n 같이 보고할 것."""
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return None, None, None, False
    combined = sorted([(v, 0) for v in x] + [(v, 1) for v in y])
    ranks = [0.0] * len(combined)
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    r1 = sum(ranks[k] for k in range(len(combined)) if combined[k][1] == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    n = n1 + n2
    # 동순위 보정 (tie correction)
    tie_term = 0.0
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        t = j - i
        if t > 1:
            tie_term += t ** 3 - t
        i = j
    mean_u = n1 * n2 / 2.0
    var_u = (n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0.0
    if var_u <= 0:
        return u1, None, None, False
    sd_u = math.sqrt(var_u)
    # 연속성 보정
    if u1 > mean_u:
        z = (u1 - mean_u - 0.5) / sd_u
    elif u1 < mean_u:
        z = (u1 - mean_u + 0.5) / sd_u
    else:
        z = 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return u1, z, p, abs(z) >= 1.96


def collect_pullback_hits(data, bench):
    """§6/tercile 스크립트와 동일 파이프라인(analyze()+CONFIG+
    passes_liquidity_filter), 체크포인트만 60~950(90개)로 확장. hit에
    risk_pct(명목 손절폭, %)도 같이 저장 — 3절 미끄러짐 측정용."""
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    hits = []
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < MIN_BARS_AFTER_OFFSET:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            ikr = is_kr(t)
            rr, rm = rs_ranks.get(t), rs_moms.get(t)
            try:
                hit = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=ikr)
            except Exception:
                continue
            if hit is None or not harness.passes_liquidity_filter(hit, ikr):
                continue
            sig_low = float(hist["Low"].iloc[-1])
            sig_close = float(hist["Close"].iloc[-1])
            trailing_vol50 = float(hist["Volume"].iloc[-50:].mean()) if len(hist) >= 50 else None
            future = harness.future_after(data[t], off)
            outcome = harness.race(hit.get("close"), hit.get("stop"), future)
            hits.append({
                "ticker": t, "off": off, "market": "KR" if ikr else "US",
                "sig_low": sig_low, "sig_close": sig_close,
                "trailing_vol50": trailing_vol50,
                "risk_pct": hit.get("risk_pct"),
                "future": future.head(N_WINDOW).copy(),
                "immediate_outcome": outcome,
            })
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            print(f"[collect] off={off} ({oi+1}/{len(OFFSETS)}) hits_so_far={len(hits)} "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)
    return hits


def breach_and_confirm(hit):
    """§6과 완전 동일 로직."""
    fut = hit["future"]
    sig_low = hit["sig_low"]
    trailing_vol50 = hit["trailing_vol50"]
    breached = False
    breach_ret = None
    confirm_entry_idx = None
    for i in range(len(fut)):
        close_i = float(fut["Close"].iloc[i])
        open_i = float(fut["Open"].iloc[i])
        vol_i = float(fut["Volume"].iloc[i])
        if close_i < sig_low:
            breached = True
            breach_ret = (close_i - hit["sig_close"]) / hit["sig_close"]
            break
        if confirm_entry_idx is None and trailing_vol50:
            if close_i > open_i and vol_i >= VOL_MULT * trailing_vol50:
                confirm_entry_idx = i
                break
    return breached, breach_ret, confirm_entry_idx


if __name__ == "__main__":
    _t0 = time.time()
    print("=" * 70)
    print(f"확장 fetch: KR days={KR_FETCH_DAYS}, US period={US_FETCH_PERIOD}")
    print("=" * 70)
    kr_data = fetch_kr_long_universe()
    us_data = fetch_us_long_universe()
    data = {**kr_data, **us_data}
    bench = harness.fetch_kr_benchmarks(days=KR_FETCH_DAYS)

    print("\n" + "=" * 70)
    print(f"눌림목 히트 수집 (KR+US, checkpoints {OFFSETS[0]}~{OFFSETS[-1]}, {len(OFFSETS)}개)")
    print("=" * 70)
    all_hits = collect_pullback_hits(data, bench)
    kr_hits = [h for h in all_hits if h["market"] == "KR"]
    us_hits = [h for h in all_hits if h["market"] == "US"]
    print(f"n_kr={len(kr_hits)} n_us={len(us_hits)}")

    print("\n" + "=" * 70)
    print(f"1절: 지지 이탈률 (N={N_WINDOW}봉, 종가기준), KR vs US — 표본확대판")
    print("=" * 70)
    for h in kr_hits + us_hits:
        breached, breach_ret, confirm_idx = breach_and_confirm(h)
        h["breached"] = breached
        h["breach_ret"] = breach_ret
        h["confirm_idx"] = confirm_idx

    kr_breach_n = sum(1 for h in kr_hits if h["breached"])
    us_breach_n = sum(1 for h in us_hits if h["breached"])
    kr_rate = kr_breach_n / len(kr_hits) if kr_hits else None
    us_rate = us_breach_n / len(us_hits) if us_hits else None
    z_breach, sig_breach = two_proportion_z(kr_breach_n, len(kr_hits), us_breach_n, len(us_hits))
    print(f"  KR: {kr_breach_n}/{len(kr_hits)} = {kr_rate:.1%}" if kr_rate is not None else "  KR: n/a")
    print(f"  US: {us_breach_n}/{len(us_hits)} = {us_rate:.1%}" if us_rate is not None else "  US: n/a")
    print(f"  z={z_breach} significant={sig_breach}")
    print(f"  (참고: §6 소표본 결과 KR 47.9%(268/559) vs US 43.2%(509/1179), z=1.87)")

    print("\n" + "=" * 70)
    print("2절: 이탈 시 손실크기 분포, KR vs US — Mann-Whitney U 검정")
    print("=" * 70)
    kr_losses = sorted(h["breach_ret"] for h in kr_hits if h["breached"])
    us_losses = sorted(h["breach_ret"] for h in us_hits if h["breached"])

    def med_iqr(xs):
        if not xs:
            return None
        n = len(xs)
        med = stats.median(xs)
        q1 = xs[n // 4]
        q3 = xs[(3 * n) // 4]
        return med, q1, q3

    kr_mi = med_iqr(kr_losses)
    us_mi = med_iqr(us_losses)
    print(f"  KR (n={len(kr_losses)}): median={kr_mi[0]:.2%} IQR=[{kr_mi[1]:.2%},{kr_mi[2]:.2%}]" if kr_mi else "  KR: n/a")
    print(f"  US (n={len(us_losses)}): median={us_mi[0]:.2%} IQR=[{us_mi[1]:.2%},{us_mi[2]:.2%}]" if us_mi else "  US: n/a")
    # 손실 "크기"이므로 절댓값 분포로 Mann-Whitney (부호는 항상 음수라 크기 비교 목적)
    kr_abs = [abs(v) for v in kr_losses]
    us_abs = [abs(v) for v in us_losses]
    u_stat, z_mw, p_mw, sig_mw = mannwhitney_u(kr_abs, us_abs)
    ratio = (abs(kr_mi[0]) / abs(us_mi[0])) if (kr_mi and us_mi and us_mi[0] != 0) else None
    print(f"  KR median / US median (절대값 비율) = {ratio}")
    print(f"  Mann-Whitney U={u_stat} z={z_mw} p={p_mw} significant={sig_mw}")

    print("\n" + "=" * 70)
    print(f"3절: 손절 미끄러짐 (KR 단독) — 명목 손절폭 vs 이탈일 실현손실")
    print("=" * 70)
    kr_nominal_stops = [h["risk_pct"] for h in kr_hits if h.get("risk_pct") is not None]
    kr_realized_losses_pct = [abs(h["breach_ret"]) * 100 for h in kr_hits if h["breached"] and h["breach_ret"] is not None]
    nominal_med = stats.median(kr_nominal_stops) if kr_nominal_stops else None
    realized_med = stats.median(kr_realized_losses_pct) if kr_realized_losses_pct else None
    slippage_gap = (realized_med - nominal_med) if (nominal_med is not None and realized_med is not None) else None
    slippage_ratio = (realized_med / nominal_med) if (nominal_med and realized_med) else None
    print(f"  명목 손절폭 중앙값 (KR 눌림목 히트 전체, n={len(kr_nominal_stops)}): {nominal_med:.2f}%" if nominal_med is not None else "  n/a")
    print(f"  이탈일 실현손실 중앙값 (이탈 발생분만, n={len(kr_realized_losses_pct)}): {realized_med:.2f}%" if realized_med is not None else "  n/a")
    print(f"  미끄러짐 격차: {slippage_gap:.2f}%p" if slippage_gap is not None else "  n/a")
    print(f"  실현/명목 배수: {slippage_ratio:.2f}x (이탈 시 실제 손실이 명목 손절폭의 몇 배로 실현되는지)" if slippage_ratio is not None else "  n/a")

    print("\n" + "=" * 70)
    print("사전 판정")
    print("=" * 70)
    cond1 = sig_breach and (kr_rate is not None and us_rate is not None and kr_rate > us_rate)
    cond2 = (ratio is not None and ratio >= 1.5) and sig_mw
    verdict = "경계선 기록"
    if cond1 and cond2:
        verdict = "채택 — KR 눌림은 붕괴 위험이 구조적으로 높음"
    print(f"  조건1(이탈률 KR>US, z>=1.96)={cond1} (z={z_breach})")
    print(f"  조건2(KR손실중앙값>=1.5x US & Mann-Whitney 유의)={cond2} (ratio={ratio}, mw_z={z_mw} sig={sig_mw})")
    print(f"  => {verdict}")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    import json
    with open("/tmp/kr_pullback_support_breach_1900d_result.json", "w") as f:
        json.dump({
            "n_kr": len(kr_hits), "n_us": len(us_hits),
            "kr_breach_rate": kr_rate, "us_breach_rate": us_rate,
            "breach_z": z_breach, "breach_sig": sig_breach,
            "kr_loss_med_iqr": kr_mi, "us_loss_med_iqr": us_mi,
            "mw_u": u_stat, "mw_z": z_mw, "mw_p": p_mw, "mw_sig": sig_mw, "loss_ratio": ratio,
            "nominal_stop_med": nominal_med, "realized_loss_med": realized_med,
            "slippage_gap_pp": slippage_gap, "slippage_ratio": slippage_ratio,
            "cond1": cond1, "cond2": cond2, "verdict": verdict,
        }, f, default=str, indent=2)
    print("[main] 결과 JSON: /tmp/kr_pullback_support_breach_1900d_result.json (커밋 대상 아님, 참고용)")

"""
KR 눌림목 "지지 붕괴" 가설 검증 (2026-08-31, 사용자 지시,
docs/kr_us_market_structure.md §6 후속 측정).

【사전 등록 가설】 "KR 눌림목이 약한 이유는 눌림이 지지에서 반등하지
못하고 붕괴로 이어지는 비율이 US보다 높기 때문이다."

측정 스크립트만 — scanner.py/app.py 미수정. 공통 하네스(harness.py) 재사용.
히트 수집은 2026-08-31_kr_pullback_liquidity_tercile_ev.py의
collect_all_hits와 100% 동일한 파이프라인(analyze()+CONFIG+
checkpoints(60,250,10)+harness.passes_liquidity_filter, KR+US 유니버스)을
눌림목 탭만 재사용한다 — 별도 구현 없음.

규칙6: 이 측정도 "히트 표본 내부 비교"(지지 이탈 여부, KR vs US)라
대조군 개념이 원형 그대로 적용되진 않지만, 두 그룹(KR/US) 다
harness.passes_liquidity_filter를 통과한 히트만 사용해 정신은 지킨다.
규칙7(z검정): 이탈률(비율) 비교이므로 harness.ev_gap_zscore(이산 R분포
전용)는 직접 적용 불가 — 표준 2-표본 비율 z검정을 별도 구현(아래
`two_proportion_z`), 그 취지(격차만으론 우연과 구분 안 됨)는 동일하게
적용. 확인진입 변형의 EV 비교는 harness.ev_gap_zscore를 그대로 쓴다.
규칙8: KR/US 전 절 분리 보고, 혼합 수치 없음.

【조작적 정의】
- N=5거래일 윈도우(신호일 다음날부터 5봉). 이 프로젝트의 다른 "며칠 뒤"
  측정(예: 안C의 N=3봉)과는 다른 값 — 여기선 "붕괴 vs 반등"을 판별하기에
  3봉은 너무 짧다고 판단해 5봉으로 설정(사용자 메시지가 정확한 N을 안
  줬음, 판단 근거로 명시).
- 지지 이탈(breach) = 신호일 저가를 N봉 이내 어느 날이든 "종가"가
  하회. (장중 저가가 아니라 종가 기준 — "붕괴"를 노이즈성 장중 터치가
  아니라 실제 마감 기준 이탈로 잡기 위함, 판단 사항.)
- 이탈 시 손실크기 = 진입가(신호일 종가) 대비 이탈일 종가 수익률.
- 확인진입("안C형" 변형) = 신호일 다음날부터 N=5봉 이내, "이탈이 먼저
  발생하면 그 시점에 제외"하고, 이탈 전에 (해당 봉 종가>시가) AND
  (해당 봉 거래량 >= 트레일링 50일 평균 거래량 * 1.5, 안C와 동일 배수로
  일관성 유지) 인 첫 봉이 나오면 그 종가로 진입, 손절=신호일 저가.
  N봉 안에 이탈도 확인도 없으면 미진입(레이스 제외) — 안C의 "N봉 안에
  못 뚫으면 미진입" 규칙과 동일한 처리.

【사전 판정 기준】 (1) KR vs US 지지이탈률 격차 z>=1.96 (KR이 더 높은
방향) AND (2) 확인진입 변형 EV가 기준선(아래) 대비 +0.15R 이상 ->
"KR 눌림목을 감시형으로 전환 검토"(코드 구현은 범위 밖, docs 기록만).
기준선: 오늘(2026-08-31) 재수집한 KR 눌림목 즉시진입 EV(이 스크립트가
직접 재계산, docs §5의 n=559 재수집치와 같은 방법론 재사용 — 별도
EV정합 태스크가 이 스크립트 실행 시점까지 커밋 안 됐으면 이 값을
기준선으로 확정 사용).

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-31_kr_pullback_support_breach.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
import math

import harness
from scanner import analyze, CONFIG

OFFSETS = harness.checkpoints(60, 250, 10)
N_WINDOW = 5
VOL_MULT = 1.5


def is_kr(t):
    return harness.is_kr_ticker(t)


def two_proportion_z(k1, n1, k2, n2):
    """표준 2-표본 비율 z검정 (harness.ev_gap_zscore는 이산 R분포 전용이라
    비율 비교엔 못 씀 — README 규칙7 취지를 비율 비교에 맞게 별도 구현)."""
    if n1 == 0 or n2 == 0:
        return None, False
    p1, p2 = k1 / n1, k2 / n2
    p_pool = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None, False
    z = (p1 - p2) / se
    return z, abs(z) >= 1.96


def collect_pullback_hits(data, bench):
    """2026-08-31_kr_pullback_liquidity_tercile_ev.py의 collect_all_hits와
    동일 파이프라인, 눌림목 탭만. hit에 신호일 저가/시가/트레일링50일
    평균거래량과 향후 N봉 원본 df를 같이 붙여서 이탈/확인진입 계산에
    쓴다."""
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
            if len(df) - off < CONFIG["min_bars"]:
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
                "future": future.head(N_WINDOW).copy(),
                "immediate_outcome": outcome,
            })
        print(f"[collect] off={off} ({oi+1}/{len(OFFSETS)}) hits_so_far={len(hits)} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    return hits


def breach_and_confirm(hit):
    """N봉 윈도우를 순서대로 훑어 (a) 이탈 여부/이탈일/이탈시 손실,
    (b) 확인진입 트리거 여부/진입일/진입가 를 계산. 이탈이 확인보다
    먼저 오면 확인진입은 무산(안C의 '못 뚫으면 미진입'과 동일 취급)."""
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


def confirm_race(hit, confirm_entry_idx):
    """확인진입 트리거 봉의 종가로 진입, 손절=신호일 저가. 레이스는
    확인봉 다음날부터(harness.race와 동일 규약 — 그날 자체는 이미
    트리거 판정에 썼으므로 레이스 대상에서 제외)."""
    fut_all = hit["future"]
    entry = float(fut_all["Close"].iloc[confirm_entry_idx])
    stop = hit["sig_low"]
    off = hit["off"]
    remaining_off = off - confirm_entry_idx - 1
    if remaining_off <= 0:
        return (None, None)
    ticker_full_df_future = harness.future_after(_DATA[hit["ticker"]], remaining_off)
    return harness.race(entry, stop, ticker_full_df_future)


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data()
    _DATA = data  # confirm_race에서 원본 df 접근용
    bench = harness.fetch_kr_benchmarks()

    print("\n" + "=" * 70)
    print("눌림목 히트 수집 (KR+US, checkpoints 60~250)")
    print("=" * 70)
    all_hits = collect_pullback_hits(data, bench)
    kr_hits = [h for h in all_hits if h["market"] == "KR"]
    us_hits = [h for h in all_hits if h["market"] == "US"]
    print(f"n_kr={len(kr_hits)} n_us={len(us_hits)}")

    print("\n" + "=" * 70)
    print(f"1절: 지지 이탈률 (N={N_WINDOW}봉, 종가기준), KR vs US")
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

    print("\n" + "=" * 70)
    print("2절: 이탈 시 손실크기 분포 (진입~이탈일 종가 수익률), KR vs US")
    print("=" * 70)
    import statistics as stats
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

    print("\n" + "=" * 70)
    print(f"3절: 확인진입('안C형') 변형 EV, KR 단독 (N={N_WINDOW}봉, 거래량>={VOL_MULT}x50일평균)")
    print("=" * 70)
    kr_confirm_outcomes = []
    kr_confirm_trigger_n = 0
    for h in kr_hits:
        if h["breached"]:
            continue  # 이탈이 확인보다 먼저 -> 미진입 처리
        if h["confirm_idx"] is None:
            continue  # N봉 안에 확인도 이탈도 없음 -> 미진입
        kr_confirm_trigger_n += 1
        outcome = confirm_race(h, h["confirm_idx"])
        kr_confirm_outcomes.append(outcome)
    confirm_ev = harness.ev_summary(kr_confirm_outcomes)
    print(f"  트리거={kr_confirm_trigger_n}/{len(kr_hits)} ({kr_confirm_trigger_n/len(kr_hits):.1%})" if kr_hits else "  n/a")
    print(f"  확인진입 EV: {confirm_ev}")

    print("\n" + "=" * 70)
    print("기준선: KR 눌림목 즉시진입 EV (오늘 재수집, §5와 동일 방법론)")
    print("=" * 70)
    immediate_ev_kr = harness.ev_summary([h["immediate_outcome"] for h in kr_hits])
    print(f"  즉시진입 EV: {immediate_ev_kr}")

    z_ev, sig_ev = harness.ev_gap_zscore(immediate_ev_kr, confirm_ev) if confirm_ev.get("ev_R") is not None else (None, False)
    ev_gap = None
    if confirm_ev.get("ev_R") is not None and immediate_ev_kr.get("ev_R") is not None:
        ev_gap = confirm_ev["ev_R"] - immediate_ev_kr["ev_R"]

    print("\n" + "=" * 70)
    print("사전 판정")
    print("=" * 70)
    cond1 = sig_breach and (kr_rate is not None and us_rate is not None and kr_rate > us_rate)
    cond2 = ev_gap is not None and ev_gap >= 0.15
    verdict = "미달"
    if cond1 and cond2:
        verdict = "채택 — KR 눌림목을 감시형으로 전환 검토"
    print(f"  조건1(이탈률 KR>US, z>=1.96)={cond1} (z={z_breach})")
    print(f"  조건2(확인진입 EV - 즉시진입EV >= +0.15R)={cond2} (gap={ev_gap}, z_ev={z_ev} sig={sig_ev})")
    print(f"  => {verdict}")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

    import json
    with open("/tmp/kr_pullback_support_breach_result.json", "w") as f:
        json.dump({
            "n_kr": len(kr_hits), "n_us": len(us_hits),
            "kr_breach_rate": kr_rate, "us_breach_rate": us_rate,
            "breach_z": z_breach, "breach_sig": sig_breach,
            "kr_loss_med_iqr": kr_mi, "us_loss_med_iqr": us_mi,
            "confirm_trigger_n": kr_confirm_trigger_n, "confirm_ev": confirm_ev,
            "immediate_ev_kr": immediate_ev_kr, "ev_gap": ev_gap,
            "ev_gap_z": z_ev, "ev_gap_sig": sig_ev, "verdict": verdict,
        }, f, default=str, indent=2)
    print("[main] 결과 JSON: /tmp/kr_pullback_support_breach_result.json (커밋 대상 아님, 참고용)")

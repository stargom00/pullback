"""
KR용 전략 탐색 1단계 — 매집봉(누적 장대양봉) 필터 측정 (2026-08-26,
사용자 지시). 외부 방법론(ABC패턴/"더양봉맨")의 핵심 주장 "과거 매집봉
(15%+ 장대양봉) 흔적이 있는 종목만 매매"가 KR 눌림목 히트를 선별할 수
있는지 검증. scanner.py는 전혀 수정하지 않는다.

【배경】
`docs/pullback_ev_kr_us_regime_investigation.md`에서 KR 눌림목 EV가
~0(0.002~0.008R)으로 확인됨 — KR 전용 개선 필터가 있는지 탐색하는
첫 걸음.

【8/17에 기각된 ABC 패턴 측정과의 차이 — 별개 검증임】
`docs/all_tabs_common_yardstick_investigation.md`(2026-08-08~09,
Script A 계열)의 ABC 패턴탭 측정(컵앤핸들/치솟은깃발/더블바닥/급등매집
전부 시점매칭 무작위 대조군 대비 역선택 또는 무차이로 기각)은 **완전히
다른 검증**이다:
  - 대상: `scanner.py`의 패턴 인식 함수(analyze_pattern 등)가 이미
    "패턴 완성"을 판정한 히트 — 패턴 인식 로직 자체의 성과를 쟀다.
  - 방법: 완전무작위 대조군(당시엔 유동성매칭도 안 된 상태 — 이번
    세션에서 그 자체가 문제였다고 이미 규칙6으로 정리됨) 대비 비교.
  - 이번 측정: 이미 유동성 필터를 통과한 **눌림목** 히트(패턴 함수와
    무관, `analyze()`/CONFIG)를 대상으로, "과거에 매집봉이 있었는가"
    라는 **새로운 이력 필드**로 반분해서 대조군 없이 히트 집합 내부
    비교(규칙6 문서대로 두 그룹 모두 이미 같은 유동성 컷을 통과한
    히트라 자동으로 매칭됨 — 별도 대조군 불필요).
  - 즉 "패턴 자체가 매수 신호로 유효한가"(8/17, 기각)와 "눌림목 신호에
    매집봉 이력을 곱하면 선별력이 생기는가"(이번)는 서로 다른 질문.
    8/17 기각이 이번 결과를 예단하지 않는다.

【필드 정의】
KR 눌림목 히트(신호일=off 기준 truncate된 마지막 봉)마다, **가용 데이터
전체**(fetch된 시계열 시작~신호일)에서 다음 중 하나라도 만족하는 봉을
매집봉으로 탐색:
  - 종가등락(전일종가 대비) ≥ +15%
  - 고가등락(전일종가 대비) ≥ +20%
  - 종가등락 ≥ +29%(상한가 근접) — 조건1(≥15%)의 상위집합이라 OR로는
    조건1에 이미 포함됨. 사용자 스펙 그대로 구현(결과에 영향 없음,
    투명성 위해 남김).
가장 최근 매집봉을 기준으로:
  - has_maejip: 매집봉 존재 여부
  - maejip_support: 신호일 종가 > 최근 매집봉 종가
  - maejip_recency: 최근 매집봉→신호일 경과 거래일, 구간(~60/61~250/250+)
  - mcap_band: 시총 1000억~10조 KRW 여부(방법론 명시 적용 범위) —
    `investor_flow.py`의 외국인보유주수/보유율 역산 시총 근사 재사용
    (2026-08-25 수급 캠페인과 동일 근사, 동일 캐비어트 적용됨)

【판정 기준 — 사전 명시】
has_maejip 유/무 반분 EV 격차 ≥ +0.05R(방향: 있음이 유리) + z검정
유의(|z|≥1.96, `harness.ev_gap_zscore`, 규칙7) → **KR 전용 필터 후보**.
격차 미미/역방향이거나 유의하지 않으면 → **기각**, "매집봉 필터는 KR
눌림목을 살리지 못함"으로 기록.

【제약 노트 — 600일선 C패턴은 이번 범위 밖】
"더양봉맨" 방법론의 600일선(핫핑크 이평) 기반 C패턴 진입 신호는 naver_kr
fetch가 ~730 캘린더일(≈500거래일)만 주는 현재 구조로는 초기 체크포인트
에서 600봉 이평을 계산할 데이터가 부족해 이번 범위에서 제외한다. 1단계
(이 스크립트)의 has_maejip 필터가 유의한 채택 후보로 나오면, fetch
기간을 늘린 뒤(naver_kr.py 확장 필요 — scanner.py/harness.py 프로덕션
경로는 안 건드리고 측정 스크립트 전용 장기 fetch 함수를 추가하는 방식
검토) 2단계로 진행한다.

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-26_maejip_candle_filter_kr.py`
(KR 유니버스 fetch ~2.5분 + 수급/시총 스크래핑 유니크 종목수×~2초, 총
10~15분 내외 예상)
"""
import sys
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
import investor_flow as ivf
from scanner import analyze, CONFIG

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = CONFIG["min_bars"]
MIN_N_FOR_JUDGMENT = 30
EV_GAP_THRESHOLD = 0.05  # R
MCAP_BAND_LOW = 1e11   # 1000억원
MCAP_BAND_HIGH = 1e13  # 10조원

MAEJIP_CLOSE_PCT = 0.15
MAEJIP_HIGH_PCT = 0.20
MAEJIP_LIMIT_PCT = 0.29


def scan_maejip(hist):
    """hist: 신호일까지 truncate된 DataFrame(마지막 행=신호일). 가용
    데이터 전체(0~len-1)에서 매집봉을 찾아 가장 최근 것의 (인덱스, 종가,
    경과거래일)을 반환. 없으면 None."""
    close = hist["Close"]
    high = hist["High"]
    n = len(hist)
    last_idx = None
    for i in range(1, n):
        prev_close = float(close.iloc[i - 1])
        if prev_close <= 0:
            continue
        c = float(close.iloc[i])
        h = float(high.iloc[i])
        close_chg = c / prev_close - 1
        high_chg = h / prev_close - 1
        if close_chg >= MAEJIP_CLOSE_PCT or high_chg >= MAEJIP_HIGH_PCT or close_chg >= MAEJIP_LIMIT_PCT:
            last_idx = i
    if last_idx is None:
        return None
    return {
        "idx": last_idx,
        "close": float(close.iloc[last_idx]),
        "recency": (n - 1) - last_idx,
    }


def recency_bucket(r):
    if r <= 60:
        return "~60"
    elif r <= 250:
        return "61~250"
    return "250+"


# ── 1단계: KR 눌림목 히트 수집(규칙8: KR 단독이라 시장분해 이슈 자체가 없음) ──
def collect_pullback_hits_kr(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())  # markets=("kr",)로 fetch됨

    hits = []
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)

        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            try:
                hit = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=True)
            except Exception:
                continue
            if hit is None:
                continue
            if not harness.passes_liquidity_filter(hit, is_kr=True):
                continue
            m = scan_maejip(hist)
            outcome = harness.race(hit.get("close"), hit.get("stop"), harness.future_after(data[t], off))
            hits.append({
                "ticker": t, "off": off, "signal_date": hist.index[-1],
                "close": hit.get("close"), "outcome": outcome,
                "has_maejip": m is not None,
                "maejip_support": (hit.get("close") > m["close"]) if m else None,
                "maejip_recency": m["recency"] if m else None,
                "maejip_recency_bucket": recency_bucket(m["recency"]) if m else None,
            })
        print(f"[PASS1] off={off} hits_so_far={len(hits)} elapsed={time.time()-t0:.0f}s ({oi+1}/{len(OFFSETS)})", flush=True)
    return hits


# ── 2단계: 시총 근사(수급 캠페인과 동일 접근, investor_flow.py 재사용) ──
def fetch_mcap_for_hits(hits, max_workers=10):
    unique_tickers = sorted({h["ticker"] for h in hits})
    print(f"[PASS2] unique tickers for mcap: {len(unique_tickers)}", flush=True)
    flow_data = {}
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(ivf.fetch_investor_flow, t, 300): t for t in unique_tickers}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                df = fut.result()
            except Exception:
                df = None
            flow_data[t] = df
            done += 1
            if done % 50 == 0:
                print(f"[PASS2] fetched {done}/{len(unique_tickers)} elapsed={time.time()-t0:.0f}s", flush=True)
    ok = sum(1 for v in flow_data.values() if v is not None)
    print(f"[PASS2] done: {ok}/{len(unique_tickers)} succeeded, elapsed={time.time()-t0:.0f}s", flush=True)
    return flow_data


def attach_mcap(hits, flow_data):
    n_no_flow = 0
    for h in hits:
        flow_df = flow_data.get(h["ticker"])
        if flow_df is None or flow_df.empty:
            h["mcap_approx"] = None
            n_no_flow += 1
            continue
        sub = flow_df[flow_df.index <= h["signal_date"]]
        if sub.empty:
            h["mcap_approx"] = None
            continue
        h["mcap_approx"] = ivf.market_cap_approx(sub.iloc[-1])
    print(f"[PASS3] mcap 부착 완료 (수급 데이터 없어 시총 미상={n_no_flow})", flush=True)
    for h in hits:
        m = h.get("mcap_approx")
        h["mcap_band"] = (m is not None and MCAP_BAND_LOW <= m <= MCAP_BAND_HIGH)
    return hits


def ev_of(hits):
    return harness.ev_summary([h["outcome"] for h in hits])


def judge_gap(label, lower_hits, upper_hits):
    ev_lower, ev_upper = ev_of(lower_hits), ev_of(upper_hits)
    if ev_lower["nv"] < MIN_N_FOR_JUDGMENT or ev_upper["nv"] < MIN_N_FOR_JUDGMENT:
        return {"label": label, "verdict": "표본부족", "lower": ev_lower, "upper": ev_upper}
    gap = ev_upper["ev_R"] - ev_lower["ev_R"]
    z, significant = harness.ev_gap_zscore(ev_lower, ev_upper)
    if gap < EV_GAP_THRESHOLD:
        verdict = "기각"
        reason = f"격차 {gap:+.3f}R < {EV_GAP_THRESHOLD}R"
    elif not significant:
        verdict = "기각"
        reason = f"격차 {gap:+.3f}R는 충분하나 z={z:.2f}로 유의하지 않음(|z|<1.96)"
    else:
        verdict = "채택후보"
        reason = f"격차 {gap:+.3f}R + z={z:.2f}(유의)"
    return {"label": label, "verdict": verdict, "reason": reason, "gap_R": gap, "z": z,
            "lower": ev_lower, "upper": ev_upper}


def run(data, bench, out_path=None):
    hits = collect_pullback_hits_kr(data, bench)
    daily_avg = round(len(hits) / len(OFFSETS), 1)
    print(f"[SUMMARY] KR 눌림목 히트 {len(hits)}건(일평균 {daily_avg}), "
          f"유니크 종목 {len({h['ticker'] for h in hits})}개", flush=True)

    flow_data = fetch_mcap_for_hits(hits)
    hits = attach_mcap(hits, flow_data)

    report = {"daily_avg_hits": daily_avg, "n_hits_total": len(hits),
              "n_unique_tickers": len({h["ticker"] for h in hits})}

    # 측정① has_maejip 유/무
    with_m = [h for h in hits if h["has_maejip"]]
    without_m = [h for h in hits if not h["has_maejip"]]
    j1 = judge_gap("has_maejip", without_m, with_m)
    report["①_has_maejip"] = j1
    print(f"[측정①] has_maejip: n_true={len(with_m)} n_false={len(without_m)} → {j1['verdict']} ({j1.get('reason')})", flush=True)

    # 측정② has_maejip=True 내에서 maejip_support 유/무
    sup_true = [h for h in with_m if h["maejip_support"] is True]
    sup_false = [h for h in with_m if h["maejip_support"] is False]
    j2 = judge_gap("maejip_support (within has_maejip)", sup_false, sup_true)
    report["②_maejip_support"] = j2
    print(f"[측정②] maejip_support: n_true={len(sup_true)} n_false={len(sup_false)} → {j2['verdict']} ({j2.get('reason')})", flush=True)

    # 측정③ recency 구간별 EV (has_maejip=True 내)
    recency_report = {}
    for bucket in ["~60", "61~250", "250+"]:
        sub = [h for h in with_m if h["maejip_recency_bucket"] == bucket]
        recency_report[bucket] = {"n": len(sub), **ev_of(sub)}
    report["③_recency_buckets"] = recency_report
    print(f"[측정③] recency buckets: {recency_report}", flush=True)

    # 측정④ mcap_band 내/외 교차 (has_maejip × mcap_band 2x2)
    cross = {}
    for maejip_flag, maejip_label in [(True, "has_maejip"), (False, "no_maejip")]:
        for band_flag, band_label in [(True, "mcap_band_in"), (False, "mcap_band_out")]:
            sub = [h for h in hits if h["has_maejip"] == maejip_flag and h["mcap_band"] == band_flag]
            cross[f"{maejip_label}×{band_label}"] = {"n": len(sub), **ev_of(sub)}
    report["④_mcap_band_cross"] = cross
    print(f"[측정④] mcap_band cross: {cross}", flush=True)

    if out_path:
        def _default(o):
            return str(o)
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=_default)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(markets=("kr",))
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-26_maejip_candle_filter_kr.results.json")
    run(data, bench, out_path=out)

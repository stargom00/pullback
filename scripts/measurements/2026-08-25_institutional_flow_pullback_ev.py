"""
기관/외국인 수급이 눌림목 신호의 EV를 개선하는가 — 신규 측정 캠페인
(2026-08-25, 사용자 지시). scanner.py는 전혀 수정하지 않는다.

【데이터 소스 확정 경위】
pykrx.stock.get_market_trading_value_by_date() 등 KRX 투자자별 거래 API를
직접 호출·추적: data.krx.co.kr의 모든 JSON 통계 엔드포인트가 이제 로그인
세션을 요구한다(비로그인 요청은 HTTP 400 + 본문 "LOGOUT" — 서버가 정상
응답하고 세션 쿠키까지 내려주므로 IP 차단이 아니라 애플리케이션 레벨
인증 거부). pykrx는 이미 최신 버전(1.2.8, PyPI 확인 완료)이고 KRX_ID/
KRX_PW 환경변수로 실계정 로그인을 요구하는 코드가 내장돼 있음 — stale
버전 문제가 아니라 KRX 정책 변경. 이 프로젝트는 정확히 같은 벽을 이미
한 번 겪고(universe.py: "pykrx는 KRX 로그인 요구로 폐기, v4.38.9") 가격
데이터를 네이버 스크래핑(naver_kr.py)으로 전환한 전례가 있어, 이번에도
같은 패턴으로 대체(scripts/measurements/investor_flow.py, 신규 공용
모듈 — naver_kr.py의 헤더/재시도/지터 관례 재사용).

【방법론 — README 규칙 6(유동성매칭 대조군) 준수】
분석 대상 자체가 "실제 눌림목 히트"(이미 하네스의 저유동성 하드필터를
통과한 종목)라 대조군이 필요한 구조가 아니다 — 이번 측정은 히트 집합
'내부'를 기관/외국인 수급 필드로 상위/하위 나눠 비교하는 것이라 두
그룹 모두 같은 유동성 컷을 통과한 히트에서 나온다(자동으로 규칙 6 충족).

【판정 기준 — 미리 명시, 사후 합리화 방지】
필드별로:
  - 상위/하위 반분 EV 격차가 +0.05R 이상이고 방향이 일관되면 "채택 후보"
  - 격차가 0.05R 미만(사실상 무차이)이거나 하위가 오히려 EV 높으면(역방향)
    "기각"
  - 채택 후보는 4분위 EV로 단조성 재확인: Q1→Q4 인접 3개 구간 중 최소
    2개가 기대 방향(반분 격차의 부호)과 일치하면 "대체로 단조" 통과,
    아니면 "비단조"로 강등해 기각(반분 격차가 우연일 수 있다는 뜻)
기각된 필드는 조용한 지표 7종과 같은 운명 — docs에 기록하고 재논쟁하지
않는다.

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-25_institutional_flow_pullback_ev.py`
1단계(가격 유니버스 KR only)는 harness로 2~3분, 2단계(수급 스크래핑)는
히트 유니버스 종목 수 × ~2초(동시성10) — 유니크 종목 수에 비례.
"""
import sys
import os
import json
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
import investor_flow as ivf
from scanner import analyze, CONFIG, analyze_super, SUPER_CONFIG

OFFSETS = harness.checkpoints(60, 250, 10)
MIN_BARS_FLOOR = max(CONFIG["min_bars"], SUPER_CONFIG["min_bars"])
CANDIDATE_FIELDS = ["inst_5d", "inst_20d", "frgn_5d", "frgn_20d", "streak"]
MIN_N_FOR_JUDGMENT = 30
EV_GAP_THRESHOLD = 0.05  # R


# ── 1단계: 눌림목 KR 히트 수집 (하네스 재사용, scanner.py 원본 함수 그대로 호출) ──
def collect_pullback_hits(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())  # data는 KR만 fetch됨(markets=("kr",))

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
            # app.py run_scan()의 is_super 부착 로직과 동일 호출(3946~3948행)
            try:
                is_super = analyze_super(hist, rs_rank=rr, rs_mom=rm, is_kr=True) is not None
            except Exception:
                is_super = False
            hits.append({
                "ticker": t, "off": off,
                "signal_date": hist.index[-1],
                "close": hit.get("close"), "stop": hit.get("stop"),
                "is_super": is_super,
                "future": harness.future_after(data[t], off),
            })
        print(f"[PASS1] off={off} hits_so_far={len(hits)} elapsed={time.time()-t0:.0f}s "
              f"({oi+1}/{len(OFFSETS)})", flush=True)
    return hits


# ── 2단계: 히트 종목 수급 스크래핑 (유니크 티커만, 동시성10) ──
def fetch_flow_for_hits(hits, max_workers=10):
    unique_tickers = sorted({h["ticker"] for h in hits})
    print(f"[PASS2] unique tickers to fetch investor flow: {len(unique_tickers)}", flush=True)
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


# ── 3단계: 히트에 수급 필드 부착 + 2R 레이스 ──
def attach_flow_and_race(hits, flow_data):
    enriched = []
    n_no_flow = 0
    for h in hits:
        flow_df = flow_data.get(h["ticker"])
        if flow_df is None or flow_df.empty:
            n_no_flow += 1
            continue
        fields = ivf.flow_fields_at(flow_df, h["signal_date"])
        outcome = harness.race(h["close"], h["stop"], h["future"])
        enriched.append({
            "ticker": h["ticker"], "off": h["off"], "is_super": h["is_super"],
            "outcome": outcome, **fields,
        })
    print(f"[PASS3] enriched={len(enriched)} (수급 데이터 없어 제외={n_no_flow})", flush=True)
    return enriched


# ── 측정 ① 상위/하위 반분 EV ──
def median_split(enriched, field):
    valid = [h for h in enriched if h.get(field) is not None]
    if len(valid) < MIN_N_FOR_JUDGMENT * 2:
        return None
    valid.sort(key=lambda h: h[field])
    mid = len(valid) // 2
    lower, upper = valid[:mid], valid[mid:]
    ev_lower = harness.ev_summary([h["outcome"] for h in lower])
    ev_upper = harness.ev_summary([h["outcome"] for h in upper])
    gap = (ev_upper["ev_R"] - ev_lower["ev_R"]) if (ev_upper["ev_R"] is not None and ev_lower["ev_R"] is not None) else None
    return {"n_valid": len(valid), "lower": ev_lower, "upper": ev_upper, "gap_R": gap}


# ── 측정 ② 4분위 EV (단조성) ──
def quartile_split(enriched, field):
    valid = [h for h in enriched if h.get(field) is not None]
    if len(valid) < MIN_N_FOR_JUDGMENT * 4:
        return None
    valid.sort(key=lambda h: h[field])
    n = len(valid)
    edges = [0, n // 4, n // 2, (3 * n) // 4, n]
    quartiles = {}
    for qi in range(4):
        seg = valid[edges[qi]:edges[qi + 1]]
        quartiles[f"Q{qi+1}"] = harness.ev_summary([h["outcome"] for h in seg])
    return quartiles


def monotonic_check(quartiles, expect_increasing):
    """Q1..Q4 인접 3구간 중 기대 방향과 일치하는 게 몇 개인지."""
    evs = [quartiles[f"Q{i}"]["ev_R"] for i in range(1, 5)]
    if any(e is None for e in evs):
        return None
    agree = 0
    for i in range(3):
        diff = evs[i + 1] - evs[i]
        if expect_increasing and diff >= 0:
            agree += 1
        elif not expect_increasing and diff <= 0:
            agree += 1
    return {"evs": evs, "agree_of_3": agree, "mostly_monotonic": agree >= 2}


# ── 측정 ③ 슈퍼대장 필터와의 독립성 ──
def super_interaction(enriched, top_field):
    valid = [h for h in enriched if h.get(top_field) is not None]
    if len(valid) < MIN_N_FOR_JUDGMENT * 2:
        return None
    valid.sort(key=lambda h: h[top_field])
    mid = len(valid) // 2
    lower, upper = valid[:mid], valid[mid:]
    n_super_in_upper = sum(1 for h in upper if h["is_super"])
    overlap_ratio = n_super_in_upper / len(upper) if upper else None
    n_super_total = sum(1 for h in valid if h["is_super"])
    super_base_ratio = n_super_total / len(valid) if valid else None

    super_only = [h for h in valid if h["is_super"]]
    result = {
        "overlap_ratio_top_half_is_super": overlap_ratio,
        "baseline_super_ratio_all_valid": super_base_ratio,
        "n_super_only": len(super_only),
    }
    if len(super_only) >= MIN_N_FOR_JUDGMENT * 2:
        super_only_sorted = sorted(super_only, key=lambda h: h[top_field])
        smid = len(super_only_sorted) // 2
        s_lower, s_upper = super_only_sorted[:smid], super_only_sorted[smid:]
        ev_s_lower = harness.ev_summary([h["outcome"] for h in s_lower])
        ev_s_upper = harness.ev_summary([h["outcome"] for h in s_upper])
        gap = (ev_s_upper["ev_R"] - ev_s_lower["ev_R"]) if (ev_s_upper["ev_R"] is not None and ev_s_lower["ev_R"] is not None) else None
        result["within_super_lower"] = ev_s_lower
        result["within_super_upper"] = ev_s_upper
        result["within_super_gap_R"] = gap
    else:
        result["within_super_note"] = f"슈퍼대장 소속 유효표본 부족(n={len(super_only)} < {MIN_N_FOR_JUDGMENT*2}) — 판단 보류"
    return result


def ev_gap_zscore(ev_lower: dict, ev_upper: dict):
    """두 그룹(각각 -1R/0R/+2R 이산분포) EV 격차의 z통계량. 결과가
    stop/target/unresolved 3값뿐이라 분산이 커서(표준편차 ~1.3~1.5R)
    작은 표본에선 겉보기 격차가 잡음일 수 있다 — 사전 기준(격차+단조성)
    만으론 "우연"을 못 거를 수 있음을 2026-08-25 inst_20d 케이스에서
    직접 확인(격차+0.111R·4분위 2/3 일치로 사전기준 통과했지만 z≈0.99로
    유의하지 않았음) → 이후 모든 "채택후보"는 이 z검정도 통과해야 최종
    채택. 반환: (z, significant:bool) 또는 계산 불가시 (None, False)."""
    def _stats(ev):
        n = ev.get("nv") or 0
        stop_r, target_r = ev.get("stop_rate"), ev.get("target_rate")
        e = ev.get("ev_R")
        if not n or stop_r is None or target_r is None or e is None:
            return None
        e2 = 1 * stop_r + 4 * target_r  # E[R^2]: (-1)^2*stop + 2^2*target
        var = max(e2 - e ** 2, 0)
        return var, n
    a, b = _stats(ev_lower), _stats(ev_upper)
    if a is None or b is None:
        return None, False
    (var_a, n_a), (var_b, n_b) = a, b
    se = ((var_a / n_a) + (var_b / n_b)) ** 0.5
    if se == 0:
        return None, False
    gap = ev_upper["ev_R"] - ev_lower["ev_R"]
    z = gap / se
    return z, abs(z) >= 1.96  # 양측 95%


def judge_field(field, split, quartiles):
    if split is None:
        return {"verdict": "표본부족", "reason": f"n_valid < {MIN_N_FOR_JUDGMENT*2}"}
    gap = split["gap_R"]
    if gap is None:
        return {"verdict": "표본부족", "reason": "EV 계산 불가(유효 표본 0)"}
    if gap < EV_GAP_THRESHOLD:
        direction = "역방향(하위가 오히려 EV 높음)" if gap < 0 else "격차 미미"
        return {"verdict": "기각", "reason": f"상위-하위 EV 격차 {gap:+.3f}R < {EV_GAP_THRESHOLD}R ({direction})", "gap_R": gap}
    # 격차는 양호 — 단조성으로 재확인
    if quartiles is None:
        return {"verdict": "채택후보(단조성 미확인)", "reason": f"격차 {gap:+.3f}R 충분하지만 4분위 표본 부족", "gap_R": gap}
    mono = monotonic_check(quartiles, expect_increasing=True)
    if mono is None:
        return {"verdict": "채택후보(단조성 미확인)", "reason": "4분위 중 EV 계산 불가 구간 있음", "gap_R": gap}
    if not mono["mostly_monotonic"]:
        return {"verdict": "기각", "reason": f"반분 격차({gap:+.3f}R)는 양호하나 4분위 비단조(3구간 중 {mono['agree_of_3']}개만 일치) — 우연 가능성",
                "gap_R": gap, "quartile_evs": mono["evs"]}
    # 격차+단조성 사전기준 통과 — 마지막으로 통계적 유의성 재확인.
    # 2026-08-25 실행에서 inst_20d가 이 사전기준을 통과하고도 z≈0.99로
    # 유의하지 않았던 걸 직접 확인(docs/institutional_flow_pullback_ev.md
    # "재검토" 절) — 이후 이 검정 없이 "채택"으로 확정하지 않는다.
    z, significant = ev_gap_zscore(split["lower"], split["upper"])
    if not significant:
        return {"verdict": "기각", "reason": f"격차({gap:+.3f}R)+단조성은 사전기준 통과했지만 z={z:.2f}로 유의하지 않음(|z|<1.96) — 표집 잡음과 구분 안 됨",
                "gap_R": gap, "quartile_evs": mono["evs"], "z": z}
    return {"verdict": "채택", "reason": f"격차 {gap:+.3f}R + 4분위 대체로 단조(3구간 중 {mono['agree_of_3']}개 일치) + z={z:.2f}(유의)",
            "gap_R": gap, "quartile_evs": mono["evs"], "z": z}


def run(data, bench, out_path=None):
    hits = collect_pullback_hits(data, bench)
    daily_avg = round(len(hits) / len(OFFSETS), 1)
    print(f"[SUMMARY] 눌림목 KR 히트 총 {len(hits)}건, 일평균 {daily_avg}건, "
          f"유니크 종목 {len({h['ticker'] for h in hits})}개", flush=True)

    flow_data = fetch_flow_for_hits(hits)
    enriched = attach_flow_and_race(hits, flow_data)

    report = {
        "daily_avg_hits": daily_avg,
        "n_hits_total": len(hits),
        "n_unique_tickers": len({h["ticker"] for h in hits}),
        "n_enriched": len(enriched),
        "fields": {},
    }

    for field in CANDIDATE_FIELDS:
        split = median_split(enriched, field)
        quartiles = quartile_split(enriched, field)
        verdict = judge_field(field, split, quartiles)
        report["fields"][field] = {"median_split": split, "quartiles": quartiles, "judgement": verdict}
        print(f"[측정①②] {field}: {verdict['verdict']} — {verdict['reason']}", flush=True)

    # 가장 유망한 필드(최종 채택 중 격차 최대, 없으면 전체 중 격차 최대) 선정
    candidates = [(f, r["judgement"].get("gap_R")) for f, r in report["fields"].items()
                  if r["judgement"]["verdict"] == "채택" and r["judgement"].get("gap_R") is not None]
    if candidates:
        best_field = max(candidates, key=lambda x: x[1])[0]
    else:
        scored = [(f, r["judgement"].get("gap_R")) for f, r in report["fields"].items() if r["judgement"].get("gap_R") is not None]
        best_field = max(scored, key=lambda x: x[1])[0] if scored else None
    report["best_field"] = best_field

    if best_field:
        interaction = super_interaction(enriched, best_field)
        report["super_interaction"] = {"field_used": best_field, **(interaction or {})}
        print(f"[측정③] 최유망 필드={best_field}, 슈퍼대장 교집합/독립성: {interaction}", flush=True)
    else:
        report["super_interaction"] = None
        print("[측정③] 유효 필드 없어 슈퍼대장 상호작용 측정 생략", flush=True)

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
                        "2026-08-25_institutional_flow_pullback_ev.results.json")
    run(data, bench, out_path=out)

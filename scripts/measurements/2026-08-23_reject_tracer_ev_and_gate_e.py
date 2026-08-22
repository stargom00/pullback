"""
reject_tracer 2차 후속 측정 (2026-08-23) — 게이트 변형 E(A∪B∪C)와 depth_atr
눌림폭 재정의가 "진짜" 좋은 신호를 늘리는지, 아니면 통과량만 늘리고 EV는
나쁜지. scanner.py 미수정. 결과는 커밋하지 않는다(요청).

선행:
  scripts/measurements/reject_tracer.py — 6종목 게이트별 탈락 원인
  scripts/measurements/2026-08-23_reject_tracer_rs_variants.py — RS 변형
    A/B/C/D 60거래일 통과일수 + 유니버스 반대급부(배율/시총/거래대금)

방법론 원칙: scanner.analyze(df, rs_rank, rs_mom, cfg, is_kr)의 `cfg` 인자는
원래 탭마다 다른 CONFIG를 주입하도록 설계된 공식 파라미터다(app.py가
BREAKOUT_CONFIG/TURN_CONFIG 등을 이런 식으로 주입). 이 스크립트는 CONFIG를
얕은 복사해 게이트 하나(rs_min 또는 pullback_min/max)만 무력화한 사본을
넘긴다 — analyze() 내부의 나머지 게이트(눌림폭/이평선지지/RSI/리스크
하드게이트/후기스테이지/M&A)는 전부 실제 scanner.analyze()가 그대로
판정한다. 이건 "조건을 다시 구현"하는 게 아니라 이미 지원되는 주입
지점을 쓰는 것 — CLAUDE.md가 경고하는 "리터럴 사본" 문제(게이트 조건
자체를 다른 파일에 베껴 쓰다 어긋나는 것)와는 다르다.

이번 측정 3가지:
  1) 게이트 변형 E = A∪B∪C 추가. 6종목 60거래일 통과일수 + 유니버스
     일평균 통과수/60일 누적 유니크/KR시총·거래대금 중앙값 — 앞선
     스크립트의 A/B/C/D 표와 같은 형식·같은 60거래일 창(offset 0~59).
  2) 눌림폭 게이트를 고정 %(pullback_min/max) 대신 depth_atr∈[0.5,3.0]로
     바꿨을 때 유니버스 통과량 변화 — 같은 방식(1)과 동일 창.
  3) 핵심 — harness.race() 2R 레이스로 실제 EV 비교. 코호트 3개:
       (a) 현행 A로 통과한 실제 눌림목 신호
       (b) E\\A 증분 — RS 게이트를 무력화한 뒤 A는 거짓·E는 참인 신호만
       (c) depth_atr 재정의 증분 — RS는 A(현행) 유지, 눌림폭 게이트만
           무력화한 뒤 표준 pullback_min/max 범위 밖인데 depth_atr는
           [0.5,3.0]인 신호만
     EV가 제대로 해소되려면 미래 봉이 필요해서 이 절만 harness의 기존
     검증된 체크포인트(offset 60~250, 10간격 — all_tabs_common_yardstick
     방법론, harness.checkpoints())를 쓴다. 1)/2)가 "최근 60거래일"을
     쓴 것과 표본 구간이 다른 이유: 그쪽은 미래를 안 보므로 최근일도
     그대로 쓸 수 있지만, EV는 신호 이후 최대 60봉의 실제 결과가 필요해
     최근일엔 아직 결과가 안 나와 있다(harness.race가 "insufficient"로
     분모에서 자동 제외하긴 하지만, 표본이 줄어드는 걸 피하려 처음부터
     충분히 과거인 체크포인트를 쓴다).

데이터 소스 한계: 1)/2)의 KR 시총은 naver_kr 실제 페이지 파싱(1회 스냅샷),
US 시총은 유니버스 규모상 계산 안 함(앞선 스크립트와 동일 이유).

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-23_reject_tracer_ev_and_gate_e.py
전체 유니버스 fetch(5~7분) + 60~80개 offset RS/depth 재계산(1,2용) +
20개 체크포인트(+rs_delta용 20개 추가) × analyze() 2변형(3용). 25~35분 예상.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import statistics
import time

import harness
from scanner import CONFIG, analyze, rs_raw_score, to_rs_rank, atr as scanner_atr, volume_info
import naver_kr

N_DAYS = 60                  # 1),2)용 — 최근 60거래일(offset 0~59)
RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200

TARGETS = [
    ("MSTR", False, "MSTR"),
    ("BMNR", False, "BMNR"),
    ("CRCL", False, "CRCL"),
    ("NBIS", False, "NBIS"),
    ("PLTR", False, "PLTR"),
    ("010060.KS", True, "OCI홀딩스"),
]

# cfg 얕은 복사 — RS 게이트 무력화(rs_min을 절대 안 걸리는 값으로)
CFG_NO_RS_GATE = dict(CONFIG)
CFG_NO_RS_GATE["rs_min"] = -999

# cfg 얕은 복사 — 눌림폭(%) 게이트 무력화, leader/non-leader 둘 다
CFG_NO_DEPTH_GATE = dict(CONFIG)
CFG_NO_DEPTH_GATE["pullback_min"] = 0.0
CFG_NO_DEPTH_GATE["leader_pullback_min"] = 0.0
CFG_NO_DEPTH_GATE["pullback_max_kr"] = 1.0
CFG_NO_DEPTH_GATE["pullback_max_us"] = 1.0
CFG_NO_DEPTH_GATE["pullback_max"] = 1.0


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


def depth_atr_for(hist):
    """눌림폭(고가60 대비 낙폭, scanner.analyze() 비돌파일 분기 산식과 동일
    산술)을 ATR%(scanner.atr 실함수) 배수로. scanner.py에 없는 신규 파생지표
    (앞선 rs_variants 스크립트와 동일 정의, 재사용 위해 복제)."""
    h, lo, c = hist["High"], hist["Low"], hist["Close"]
    if len(c) < 60:
        return None
    close = float(c.iloc[-1])
    high60 = float(h.iloc[-60:].max())
    if high60 <= 0 or close <= 0:
        return None
    pullback_pct = (high60 - close) / high60 * 100
    atr_val = scanner_atr(h, lo, c, 14)
    atr_pct = atr_val / close * 100 if close > 0 else 0.0
    if atr_pct <= 0:
        return None
    return pullback_pct / atr_pct


def fetch_kr_marketcap_eok(max_pages=40):
    """KR 종목별 시가총액(억원) 1회 스냅샷. naver_kr.fetch_high_marketcap_allowed
    와 동일 페이지네이션/실제 파서(_parse_marketcap_rows) 재사용, 임계값 컷만 없앰."""
    import requests
    out = {}
    for sosok, suffix in ((0, ".KS"), (1, ".KQ")):
        for page in range(1, max_pages + 1):
            try:
                resp = requests.get(
                    naver_kr._MARKETSUM_URL,
                    params={"sosok": sosok, "page": page},
                    headers=naver_kr._HEADERS,
                    timeout=naver_kr._TIMEOUT,
                )
                resp.raise_for_status()
                resp.encoding = "euc-kr"
                rows = naver_kr._parse_marketcap_rows(resp.text)
            except Exception:
                break
            if not rows:
                break
            for code, name, mcap_eok in rows:
                out[f"{code}{suffix}"] = mcap_eok
            time.sleep(0.12)
    return out


def variant_flags(rs, rs3m, rs_delta):
    a = rs is not None and rs >= 80
    b = rs3m is not None and rs3m >= 80
    c = (rs is not None and rs >= 50) and (rs_delta is not None and rs_delta >= 25)
    e = a or b or c
    return a, b, c, e


# ══════════════════════════════════════════════════════════════════
# 1)+2) 최근 60거래일 — RS 변형 E, 눌림폭 depth_atr 재정의 통과량
# ══════════════════════════════════════════════════════════════════
def run_daily(data, kospi_close, kosdaq_close, kr_mcap):
    t0 = time.time()
    tickers = list(data.keys())
    TOTAL_OFFSETS = N_DAYS + RS_DELTA_LOOKBACK
    per_offset = {}
    for off in range(TOTAL_OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < RS_MIN_BARS:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)
        r3_ranks = rs_3m_ranks(trunc_cache)
        rec = {"rs_ranks": rs_ranks, "r3_ranks": r3_ranks}
        if off < N_DAYS:
            depth, pullback_pct_map, turnover = {}, {}, {}
            for t, hist in trunc_cache.items():
                da = depth_atr_for(hist)
                if da is not None:
                    depth[t] = da
                h, c = hist["High"], hist["Close"]
                if len(c) >= 60:
                    close = float(c.iloc[-1])
                    high60 = float(h.iloc[-60:].max())
                    if high60 > 0:
                        pullback_pct_map[t] = (high60 - close) / high60
                try:
                    vi = volume_info(float(hist["Close"].iloc[-1]), hist["Volume"])
                    turnover[t] = vi["avg_turnover"]
                except Exception:
                    pass
            rec["depth_atr"] = depth
            rec["pullback_frac"] = pullback_pct_map
            rec["avg_turnover"] = turnover
        per_offset[off] = rec
        if (off + 1) % 10 == 0 or off == TOTAL_OFFSETS - 1:
            print(f"[daily] offset {off} 완료 ({off+1}/{TOTAL_OFFSETS}) elapsed={time.time()-t0:.0f}s", flush=True)

    part1_variant_e(data, per_offset, kr_mcap)
    part2_depth_redefinition(data, per_offset, kr_mcap)
    return per_offset


def part1_variant_e(data, per_offset, kr_mcap):
    print("\n" + "=" * 70)
    print("1) 게이트 변형 E = A∪B∪C — 6종목 60거래일 통과일수 + 유니버스 반대급부")
    print("=" * 70)
    header = f"  {'종목':<10}{'A':>6}{'E':>6}"
    print(header)
    for ticker, is_kr, name in TARGETS:
        cnt_a = cnt_e = 0
        for off in range(N_DAYS):
            rs = per_offset[off]["rs_ranks"].get(ticker)
            rs3m = per_offset[off]["r3_ranks"].get(ticker)
            rs_20ago = per_offset[off + RS_DELTA_LOOKBACK]["rs_ranks"].get(ticker)
            rs_delta = (rs - rs_20ago) if (rs is not None and rs_20ago is not None) else None
            a, b, c, e = variant_flags(rs, rs3m, rs_delta)
            cnt_a += int(a)
            cnt_e += int(e)
        print(f"  {name:<10}{cnt_a:>6}{cnt_e:>6}" + ("   (NBIS: A와 E 동일해야 정상)" if ticker == "NBIS" else ""))

    tickers = list(data.keys())
    daily_a, daily_e = [], []
    ever_a, ever_e = set(), set()
    for off in range(N_DAYS):
        rs_ranks = per_offset[off]["rs_ranks"]
        r3_ranks = per_offset[off]["r3_ranks"]
        rs_20ago = per_offset[off + RS_DELTA_LOOKBACK]["rs_ranks"]
        ca = ce = 0
        for t in tickers:
            rs = rs_ranks.get(t)
            rs3m = r3_ranks.get(t)
            rs_delta = (rs - rs_20ago.get(t)) if (rs is not None and t in rs_20ago) else None
            a, b, c, e = variant_flags(rs, rs3m, rs_delta)
            if a:
                ca += 1
                ever_a.add(t)
            if e:
                ce += 1
                ever_e.add(t)
        daily_a.append(ca)
        daily_e.append(ce)

    avg_a = sum(daily_a) / N_DAYS
    avg_e = sum(daily_e) / N_DAYS
    print(f"\n  변형A: 일평균 {avg_a:.0f}종목, 60일누적유니크 {len(ever_a)}종목")
    print(f"  변형E: 일평균 {avg_e:.0f}종목 (A 대비 {avg_e/avg_a:.2f}배), 60일누적유니크 {len(ever_e)}종목")

    for label, s in (("A", ever_a), ("E", ever_e)):
        kr_caps, turnovers = [], []
        for t in s:
            if harness.is_kr_ticker(t):
                cap = kr_mcap.get(t)
                if cap:
                    kr_caps.append(cap)
            to_ = per_offset[0]["avg_turnover"].get(t)
            if to_:
                turnovers.append(to_)
        med_cap = statistics.median(kr_caps) if kr_caps else None
        med_to = statistics.median(turnovers) if turnovers else None
        print(f"  변형{label}: KR시총중앙값={f'{med_cap:,.0f}억원' if med_cap else 'N/A'}  "
              f"50일평균거래대금중앙값={f'{med_to:,.0f}원' if med_to else 'N/A'}")


def part2_depth_redefinition(data, per_offset, kr_mcap):
    print("\n" + "=" * 70)
    print("2) 눌림폭 게이트 재정의: 고정%(pullback_min/max) vs depth_atr∈[0.5,3.0]")
    print("   (단순화: leader 완화밴드 무시하고 일반 pullback_min/max 기준 사용)")
    print("=" * 70)
    tickers = list(data.keys())
    daily_std, daily_depth = [], []
    ever_std, ever_depth = set(), set()
    for off in range(N_DAYS):
        pb = per_offset[off]["pullback_frac"]
        da = per_offset[off]["depth_atr"]
        cs = cd = 0
        for t in tickers:
            ikr = harness.is_kr_ticker(t)
            pb_max = CONFIG["pullback_max_kr"] if ikr else CONFIG["pullback_max_us"]
            frac = pb.get(t)
            if frac is not None and CONFIG["pullback_min"] <= frac <= pb_max:
                cs += 1
                ever_std.add(t)
            dv = da.get(t)
            if dv is not None and 0.5 <= dv <= 3.0:
                cd += 1
                ever_depth.add(t)
        daily_std.append(cs)
        daily_depth.append(cd)

    avg_s = sum(daily_std) / N_DAYS
    avg_d = sum(daily_depth) / N_DAYS
    print(f"  고정%(현행)  : 일평균 {avg_s:.0f}종목, 60일누적유니크 {len(ever_std)}종목")
    print(f"  depth_atr재정의: 일평균 {avg_d:.0f}종목 (현행 대비 {avg_d/avg_s:.2f}배), 60일누적유니크 {len(ever_depth)}종목")

    for label, s in (("고정%(현행)", ever_std), ("depth_atr재정의", ever_depth)):
        kr_caps, turnovers = [], []
        for t in s:
            if harness.is_kr_ticker(t):
                cap = kr_mcap.get(t)
                if cap:
                    kr_caps.append(cap)
            to_ = per_offset[0]["avg_turnover"].get(t)
            if to_:
                turnovers.append(to_)
        med_cap = statistics.median(kr_caps) if kr_caps else None
        med_to = statistics.median(turnovers) if turnovers else None
        print(f"  {label}: KR시총중앙값={f'{med_cap:,.0f}억원' if med_cap else 'N/A'}  "
              f"50일평균거래대금중앙값={f'{med_to:,.0f}원' if med_to else 'N/A'}")


# ══════════════════════════════════════════════════════════════════
# 3) 핵심 — harness 2R 레이스로 EV 비교 (checkpoint 60~250, 10간격)
# ══════════════════════════════════════════════════════════════════
def run_ev(data, kospi_close, kosdaq_close):
    t0 = time.time()
    tickers = list(data.keys())
    offsets = harness.checkpoints(60, 250, 10)          # 20개, EV용 실제 신호 체크포인트
    extra = sorted(set(offsets) | {o + RS_DELTA_LOOKBACK for o in offsets})  # rs_delta용 +20 추가

    rs_cache = {}   # off -> (rs_ranks, rs_moms)
    r3_cache = {}   # off -> r3_ranks
    for off in extra:
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
        print(f"[ev] rs precompute offset {off} 완료 elapsed={time.time()-t0:.0f}s", flush=True)

    cohort_a, cohort_b, cohort_c = [], [], []
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
            a, b, c, e = variant_flags(rs, rs3m, rs_delta)
            rm = rs_moms.get(t)
            future = harness.future_after(df, off)

            # (a)/(b): RS 게이트 무력화 후 전체 파이프라인 통과 여부로 후보 선정,
            # 실제 버킷은 real rs 기반 A/E 플래그로 사후 결정
            try:
                hit_norsgate = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CFG_NO_RS_GATE, is_kr=ikr)
            except Exception:
                hit_norsgate = None
            if hit_norsgate is not None and harness.passes_liquidity_filter(hit_norsgate, ikr):
                rec = {"ticker": t, "off": off, "close": hit_norsgate.get("close"),
                       "stop": hit_norsgate.get("stop"), "risk_pct": hit_norsgate.get("risk_pct"),
                       "future": future}
                if a:
                    cohort_a.append(rec)
                elif e:
                    cohort_b.append(rec)

            # (c): RS는 표준(A) 유지, 눌림폭 게이트만 무력화 — 표준 눌림폭 범위
            # 밖인데 depth_atr 범위 안인 것만 "증분"으로 채택
            if rs is None or rs < CONFIG["rs_min"]:
                continue
            try:
                hit_nodepth = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CFG_NO_DEPTH_GATE, is_kr=ikr)
            except Exception:
                hit_nodepth = None
            if hit_nodepth is None or not harness.passes_liquidity_filter(hit_nodepth, ikr):
                continue
            pb_frac = hit_nodepth.get("pullback_pct", 0.0) / 100.0
            pb_max = CONFIG["pullback_max_kr"] if ikr else CONFIG["pullback_max_us"]
            within_standard = CONFIG["pullback_min"] <= pb_frac <= pb_max
            if within_standard:
                continue  # 표준 범위 안 = 이미 (a)/(b)에서 잡히는 신호, 증분 아님
            da = depth_atr_for(hist)
            if da is None or not (0.5 <= da <= 3.0):
                continue
            cohort_c.append({"ticker": t, "off": off, "close": hit_nodepth.get("close"),
                              "stop": hit_nodepth.get("stop"), "risk_pct": hit_nodepth.get("risk_pct"),
                              "future": future})

        print(f"[ev] checkpoint {off} 완료 ({oi+1}/{len(offsets)}) elapsed={time.time()-t0:.0f}s "
              f"a={len(cohort_a)} b={len(cohort_b)} c={len(cohort_c)}", flush=True)

    part3_ev_report(cohort_a, cohort_b, cohort_c)


def part3_ev_report(cohort_a, cohort_b, cohort_c):
    print("\n" + "=" * 70)
    print("3) 핵심 — 코호트별 EV (harness 2R 레이스, checkpoint offset 60~250 10간격)")
    print("   (a)현행A 신호   (b)E\\A 증분   (c)depth_atr 재정의 증분(RS는 A 유지)")
    print("=" * 70)
    for label, hits in (("(a) 현행 A", cohort_a), ("(b) E\\A 증분", cohort_b), ("(c) depth_atr 증분", cohort_c)):
        outcomes = [harness.race(h["close"], h["stop"], h["future"]) for h in hits]
        summary = harness.ev_summary(outcomes)
        risks = [h["risk_pct"] for h in hits if h["risk_pct"] is not None]
        med_risk = statistics.median(risks) if risks else None
        print(f"\n  {label} — n={summary['n_hits']} (유효표본 nv={summary['nv']}, 데이터부족제외 {summary['n_insufficient']})")
        print(f"    EV = {summary['ev_R']:.3f}R" if summary['ev_R'] is not None else "    EV = N/A(표본부족)")
        print(f"    승률(2R도달률) = {summary['target_rate']*100:.1f}%" if summary['target_rate'] is not None else "    승률 = N/A")
        print(f"    손절률 = {summary['stop_rate']*100:.1f}%" if summary['stop_rate'] is not None else "    손절률 = N/A")
        print(f"    중앙 손절폭(risk_pct) = {med_risk:.2f}%" if med_risk is not None else "    중앙 손절폭 = N/A")


if __name__ == "__main__":
    _t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    _kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    _kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    print("[main] KR 시총 스냅샷 fetch 중...", flush=True)
    try:
        _KR_MCAP = fetch_kr_marketcap_eok()
    except Exception as _e:
        print(f"[WARN] KR 시총 fetch 실패: {_e}")
        _KR_MCAP = {}
    print(f"[main] KR 시총 {len(_KR_MCAP)}종목 확보, elapsed={time.time()-_t0:.0f}s", flush=True)

    run_daily(data, _kospi_close, _kosdaq_close, _KR_MCAP)
    run_ev(data, _kospi_close, _kosdaq_close)
    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

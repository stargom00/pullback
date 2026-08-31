"""
사전 등록: "테마 단계가 신호 EV를 개선하는가" (2026-08-31, 사용자 지시)
— theme_lifecycle.py 로테이션 활용의 첫 검증.

【표본】theme_map.json 매핑 테마 소속 KR 종목의 스캐너 히트(돌파·박스돌파·
추세전환·눌림목 4개 탭), KR 1900일 fetch(≈1273봉,
2026-08-31_kr_pullback_support_breach_1900d.py의 fetch_kr_long_universe()
재사용). 각 히트에 그날 소속 테마의 라이프사이클 단계(점화/확산/후기/
이탈/미분류)를 라벨링. 규칙9(체크포인트 90개 이상) 준수 —
harness.checkpoints(60, 950, 10). US는 대상 아님(theme_map.py 자체가
KR 전용 — 규칙8 N/A, theme_reignition.py와 동일 판단).

【사전 등록 3개 측정】
1. 단계별 EV·손절률·n — 확산 단계 히트가 미분류/이탈 단계보다 나은가.
2. 테마 무소속(theme_map에 없는 종목) 히트와의 비교 — "테마 매핑 있음"
   자체가 이득인가. 규칙6(대조군 유동성매칭): 별도 대조군을 구성하지
   않음 — 매핑/무소속 양쪽 다 harness.passes_liquidity_filter()를 이미
   동일하게 통과한 히트 모집단이라 유동성 컷 자체는 이미 동일 기준으로
   양쪽에 적용돼 있음(무작위 대조군을 새로 뽑는 종류의 측정이 아님).
3. 자금이동 예측력 — 테마 A 점유율 하락 "시작"(전일 비하락→당일 하락
   전환) 이후 TRIGGER_WINDOW 거래일 내 그 A와 유의한 음의 상관을 가진
   테마 B 소속 종목에 진입하면, 그렇지 않은 B 히트보다 EV가 높은가
   (사후 상관이 아니라 "A 하락이 시작된 시점에 이미 알 수 있는 정보만
   사용"이므로 예측 신호 성격 — 룩어헤드 없음).

【대표 테마 배정, 사전 등록】 한 종목이 여러 테마에 동시 소속되면
theme_map.json의 정적 rank(사업 직결도, 숫자가 낮을수록 그 테마와
직결)가 가장 낮은 테마를 대표 테마로 쓴다(사후에 유리한 쪽을 고르는
것을 막기 위한 객관적 사전 규칙). rank 없는 소속은 999로 취급(우선순위
최하).

【로테이션 상관, 사전 등록】 theme_lifecycle.rotation_matrix()를 재사용
하되(재구현 금지) lookback을 가용 전체 기간으로 줘서 "최근 20일 스냅샷"이
아니라 전체 표본 상관을 구한다 — 이 측정 자체가 "예측력이 있는가"를
장기간에 걸쳐 묻는 것이라 하네스 기본값(로테이션 탭 UI용, 최근 20일)과
다르게 재는 이유가 이것. 유의성은 Fisher z 변환(피어슨 상관계수 표준
검정, harness.py에 없는 신규 통계라 이 스크립트에 구현) + 최소 상관크기
|r|>=0.15를 함께 요구(표본이 1200일+로 커서 z검정만 쓰면 경제적으로
무의미한 상관(|r|<0.1)도 유의로 나올 수 있음 — 규칙7의 취지를 지키려면
"통계적으로 우연이 아님"과 "크기가 있음"을 둘 다 봐야 함).

【사전 판정】 확산 단계 EV가 미분류(매핑됨) 대비 +0.15R 이상 그리고
z>=1.96 → "강세 테마 소속 필터" 채택(카드에 테마 단계 배지 + 필터 칩
UI 반영 검토). 미달 → "테마 단계는 EV 무관, 라이프사이클은 관찰용"으로
기록. 측정 2·3은 별도 사전 기준 없음(탐색적 부기 — 사전 등록된 결과는
있는 그대로 기록, 판정이 필요하면 결과 확인 후 사용자 지시).

측정 스크립트만 — scanner.py/app.py 미수정.
실행: 리포 루트에서
  python3 scripts/measurements/2026-08-31_kr_theme_stage_signal_ev.py
"""
import sys
import os
import json
import time
import math
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                 "scripts", "measurements"))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

import harness
import theme_lifecycle as tl
import theme_map
from scanner import (
    CONFIG, analyze, to_rs_rank,
    analyze_turnaround, TURN_CONFIG,
    analyze_breakout, BREAKOUT_CONFIG,
    analyze_boxbreak, BOXBREAK_CONFIG,
)

# ── fetch_kr_long_universe() 재사용(재구현 안 함) ──────────────────────
_SB_PATH = os.path.join("scripts", "measurements",
                         "2026-08-31_kr_pullback_support_breach_1900d.py")
_spec = importlib.util.spec_from_file_location("sb1900d", _SB_PATH)
sb1900d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb1900d)

OFFSETS = harness.checkpoints(60, 950, 10)   # 규칙9 — 90개
RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200
TRIGGER_WINDOW = 10       # 사전 등록: A 하락 시작 후 며칠 내 진입을 "신호추종"으로 볼지
CORR_MIN_ABS = 0.15       # 사전 등록: 최소 상관크기(경제적 무의미 상관 배제)
CORR_SIG_Z = 1.96


def fisher_z_test(r, n):
    """피어슨 상관계수 표준 유의성 검정(Fisher z 변환). 반환 (z, 유의여부)."""
    if r is None or n < 4 or abs(r) >= 1:
        return None, False
    z = math.atanh(r) * math.sqrt(n - 3)
    return z, abs(z) >= CORR_SIG_Z


def rs_3m_ranks(trunc_cache):
    kr3 = {}
    for t, hist in trunc_cache.items():
        r3 = harness.ret_pct(hist["Close"], 63)
        if r3 is not None:
            kr3[t] = r3
    return to_rs_rank(kr3)


def precompute_rs(data, kospi_close, kosdaq_close):
    t0 = time.time()
    tickers = list(data.keys())
    extra_offsets = sorted(set(OFFSETS) | {o + RS_DELTA_LOOKBACK for o in OFFSETS})
    rs_cache, r3_cache = {}, {}
    for oi, off in enumerate(extra_offsets):
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
        if (oi + 1) % 20 == 0 or oi == len(extra_offsets) - 1:
            print(f"[rs-precompute] {oi+1}/{len(extra_offsets)} offset={off} elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache, r3_cache


def collect_simple_tab(data, analyze_fn, cfg, rs_cache, label, ticker_theme, phase_by_theme_date, date_to_pos):
    """돌파/박스돌파/추세전환 공용 — 히트마다 (ticker, hit_pos, phase_or_unmapped) 기록."""
    t0 = time.time()
    records = []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        for t, df in data.items():
            if len(df) - off < cfg["min_bars"]:
                continue
            hist = harness.truncate_at(df, off)
            try:
                hit = analyze_fn(hist, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t), cfg=cfg, is_kr=True)
            except Exception:
                continue
            if hit is None or not harness.passes_liquidity_filter(hit, True):
                continue
            future = harness.future_after(df, off)
            outcome = harness.race(hit["close"], hit["stop"], future)
            hit_date = hist.index[-1]
            label_ = _label_hit(t, hit_date, ticker_theme, phase_by_theme_date)
            records.append({"ticker": t, "date": hit_date, "pos": date_to_pos.get(hit_date),
                             "label": label_, "outcome": outcome})
        if (oi + 1) % 20 == 0 or oi == len(OFFSETS) - 1:
            n_ = len(records)
            print(f"[{label}] {oi+1}/{len(OFFSETS)} off={off} n={n_} elapsed={time.time()-t0:.0f}s", flush=True)
    return records


def collect_pullback(data, rs_cache, r3_cache, ticker_theme, phase_by_theme_date, date_to_pos):
    t0 = time.time()
    records = []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        r3_ranks = r3_cache[off]
        rs_20ago, _ = rs_cache.get(off + RS_DELTA_LOOKBACK, ({}, {}))
        for t, df in data.items():
            if len(df) - off < CONFIG["min_bars"]:
                continue
            hist = harness.truncate_at(df, off)
            rs = rs_ranks.get(t)
            rm = rs_moms.get(t)
            rs3m = r3_ranks.get(t)
            rs_delta = (rs - rs_20ago.get(t)) if (rs is not None and t in rs_20ago) else None
            try:
                hit = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CONFIG, is_kr=True,
                              rs_3m=rs3m, rs_delta=rs_delta)
            except Exception:
                hit = None
            if hit is None or not harness.passes_liquidity_filter(hit, True):
                continue
            future = harness.future_after(df, off)
            outcome = harness.race(hit["close"], hit["stop"], future)
            hit_date = hist.index[-1]
            label_ = _label_hit(t, hit_date, ticker_theme, phase_by_theme_date)
            records.append({"ticker": t, "date": hit_date, "pos": date_to_pos.get(hit_date),
                             "label": label_, "outcome": outcome})
        if (oi + 1) % 20 == 0 or oi == len(OFFSETS) - 1:
            n_ = len(records)
            print(f"[눌림목] {oi+1}/{len(OFFSETS)} off={off} n={n_} elapsed={time.time()-t0:.0f}s", flush=True)
    return records


def _label_hit(ticker, hit_date, ticker_theme, phase_by_theme_date):
    theme_name = ticker_theme.get(ticker)
    if theme_name is None:
        return ("무소속", None)
    phase = phase_by_theme_date.get(theme_name, {}).get(hit_date, "미분류")
    return (phase, theme_name)


def build_theme_context(data):
    """테마별 라이프사이클 phase맵 + 점유율 시계열 + 대표테마 배정."""
    entries = {name: theme_map.get(name) for name in theme_map.list_all().keys()}
    entries = {name: e for name, e in entries.items() if e and e.get("stocks")}
    market_turnover = tl.market_daily_turnover(data)
    max_window = len(market_turnover) - tl.BASELINE_WINDOW - 1

    theme_data_map = {}
    phase_by_theme_date = {}
    ticker_theme_rank = {}
    for name, entry in entries.items():
        stocks = entry["stocks"]
        theme_data = tl.compute_theme_series(stocks, data, market_turnover, window=max_window)
        if theme_data is None:
            print(f"[theme] {name}: 데이터 부족 — 스킵", flush=True)
            continue
        theme_data_map[name] = theme_data
        cycles = tl.find_cycles(theme_data)
        phase_map = {}
        for c in cycles:
            for p in c["phases"]:
                phase_map[p["date"]] = p["phase"]
        phase_by_theme_date[name] = phase_map
        for s in stocks:
            t = s["ticker"]
            rank = s.get("rank") if isinstance(s.get("rank"), int) else 999
            if t not in ticker_theme_rank or rank < ticker_theme_rank[t][1]:
                ticker_theme_rank[t] = (name, rank)
        print(f"[theme] {name}: {len(cycles)}개 사이클, phase라벨 {len(phase_map)}일", flush=True)

    ticker_theme = {t: v[0] for t, v in ticker_theme_rank.items()}
    # 모든 theme_data가 같은 market_turnover.index[-(window+BASELINE):]를 슬라이스해
    # 만들어졌으므로 row 날짜 시퀀스가 전부 동일 — 아무 테마에서나 date_to_pos를 뽑아도 됨.
    any_td = next(iter(theme_data_map.values()))
    date_to_pos = {r["date"]: i for i, r in enumerate(any_td["rows"])}
    return theme_data_map, phase_by_theme_date, ticker_theme, date_to_pos


def decline_start_positions(theme_data):
    """테마 점유율(turnover_share_pct)이 "하락 시작"(전일 비하락→당일 하락)한
    row index 리스트."""
    shares = [r["turnover_share_pct"] for r in theme_data["rows"]]
    out = []
    for i in range(2, len(shares)):
        a, b, c = shares[i - 2], shares[i - 1], shares[i]
        if a is None or b is None or c is None:
            continue
        if b >= a and c < b:
            out.append(i)
    return out


def main():
    t0 = time.time()
    print("[main] KR 1900일 유니버스 fetch 시작 (fetch_kr_long_universe 재사용)...", flush=True)
    data = sb1900d.fetch_kr_long_universe(concurrency=10)
    print(f"[main] fetch 완료 {len(data)}종목, {time.time()-t0:.0f}s", flush=True)

    print("\n" + "=" * 70)
    print("테마 컨텍스트(라이프사이클 단계 + 대표테마) 구축")
    print("=" * 70)
    theme_data_map, phase_by_theme_date, ticker_theme, date_to_pos = build_theme_context(data)
    print(f"[main] 대표테마 배정된 종목 수: {len(ticker_theme)}", flush=True)

    print("\n" + "=" * 70)
    print("RS 사전계산")
    print("=" * 70)
    bench = harness.fetch_kr_benchmarks(days=1900)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    rs_cache, r3_cache = precompute_rs(data, kospi_close, kosdaq_close)

    print("\n" + "=" * 70)
    print("4개 탭 히트 수집(라벨링 포함)")
    print("=" * 70)
    records = []
    records += collect_simple_tab(data, analyze_breakout, BREAKOUT_CONFIG, rs_cache, "돌파",
                                   ticker_theme, phase_by_theme_date, date_to_pos)
    records += collect_simple_tab(data, analyze_boxbreak, BOXBREAK_CONFIG, rs_cache, "박스돌파",
                                   ticker_theme, phase_by_theme_date, date_to_pos)
    records += collect_simple_tab(data, analyze_turnaround, TURN_CONFIG, rs_cache, "추세전환",
                                   ticker_theme, phase_by_theme_date, date_to_pos)
    records += collect_pullback(data, rs_cache, r3_cache, ticker_theme, phase_by_theme_date, date_to_pos)
    print(f"[main] 총 히트(라벨링 완료) {len(records)}건", flush=True)

    # ── 측정 1: 단계별 EV ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("측정 1 — 테마 단계별 EV")
    print("=" * 70)
    by_stage = {}
    for r in records:
        stage, _theme = r["label"]
        by_stage.setdefault(stage, []).append(r["outcome"])
    stage_summary = {}
    for stage, outcomes in by_stage.items():
        stage_summary[stage] = harness.ev_summary(outcomes)
        s = stage_summary[stage]
        print(f"  {stage}: n={s['n_hits']} nv={s['nv']} EV={s['ev_R']}R stop={s['stop_rate']}", flush=True)

    ev_diffusion = stage_summary.get("확산")
    ev_unclassified = stage_summary.get("미분류")
    z1, sig1 = (None, False)
    gap1 = None
    if ev_diffusion and ev_unclassified and ev_diffusion["nv"] and ev_unclassified["nv"]:
        z1, sig1 = harness.ev_gap_zscore(ev_unclassified, ev_diffusion)
        gap1 = ev_diffusion["ev_R"] - ev_unclassified["ev_R"]
    print(f"[main] 측정1 격차(확산-미분류)={gap1} z={z1} 유의={sig1}", flush=True)

    # ── 측정 2: 매핑 전체 vs 무소속 ───────────────────────────────────
    print("\n" + "=" * 70)
    print("측정 2 — 테마 매핑 여부 EV")
    print("=" * 70)
    mapped_outcomes = [r["outcome"] for r in records if r["label"][0] != "무소속"]
    unmapped_outcomes = [r["outcome"] for r in records if r["label"][0] == "무소속"]
    ev_mapped = harness.ev_summary(mapped_outcomes)
    ev_unmapped = harness.ev_summary(unmapped_outcomes)
    print(f"  매핑됨: n={ev_mapped['n_hits']} nv={ev_mapped['nv']} EV={ev_mapped['ev_R']}R", flush=True)
    print(f"  무소속: n={ev_unmapped['n_hits']} nv={ev_unmapped['nv']} EV={ev_unmapped['ev_R']}R", flush=True)
    z2, sig2 = (None, False)
    if ev_mapped["nv"] and ev_unmapped["nv"]:
        z2, sig2 = harness.ev_gap_zscore(ev_unmapped, ev_mapped)
    print(f"[main] 측정2 격차(매핑-무소속)={ev_mapped['ev_R']-ev_unmapped['ev_R'] if ev_mapped['ev_R'] and ev_unmapped['ev_R'] else None} z={z2} 유의={sig2}", flush=True)

    # ── 측정 3: 로테이션 예측력 ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("측정 3 — 로테이션 예측력(A 하락시작 → B 진입)")
    print("=" * 70)
    full_len = min(len(td["rows"]) for td in theme_data_map.values())
    matrix = tl.rotation_matrix(theme_data_map, lookback=full_len - 1)
    sig_pairs = []
    for a in matrix:
        for b in matrix[a]:
            if a == b:
                continue
            r = matrix[a][b]
            z, sig = fisher_z_test(r, full_len)
            if sig and r is not None and r <= -CORR_MIN_ABS:
                sig_pairs.append({"a": a, "b": b, "r": r, "z": z})
    print(f"[main] 유의한 음의 상관 쌍(|r|>={CORR_MIN_ABS}, |z|>={CORR_SIG_Z}): {len(sig_pairs)}건", flush=True)
    for p in sig_pairs:
        print(f"   {p['a']} -> {p['b']}  r={p['r']:.3f} z={p['z']:.2f}", flush=True)

    # theme -> 유의한 음의 상관 파트너(A) 목록
    partners_of = {}
    for p in sig_pairs:
        partners_of.setdefault(p["b"], []).append(p["a"])

    triggered_days = {}   # theme_b -> set(pos)
    for b, partners in partners_of.items():
        days = set()
        for a in partners:
            for i in decline_start_positions(theme_data_map[a]):
                for k in range(1, TRIGGER_WINDOW + 1):
                    days.add(i + k)
        triggered_days[b] = days

    m3_outcomes = {"신호추종": [], "비신호": []}
    for r in records:
        stage, theme_name = r["label"]
        if theme_name is None or theme_name not in triggered_days or r["pos"] is None:
            continue
        if r["pos"] in triggered_days[theme_name]:
            m3_outcomes["신호추종"].append(r["outcome"])
        else:
            m3_outcomes["비신호"].append(r["outcome"])

    ev_triggered = harness.ev_summary(m3_outcomes["신호추종"])
    ev_untriggered = harness.ev_summary(m3_outcomes["비신호"])
    print(f"  신호추종: n={ev_triggered['n_hits']} nv={ev_triggered['nv']} EV={ev_triggered['ev_R']}R", flush=True)
    print(f"  비신호:   n={ev_untriggered['n_hits']} nv={ev_untriggered['nv']} EV={ev_untriggered['ev_R']}R", flush=True)
    z3, sig3 = (None, False)
    if ev_triggered["nv"] and ev_untriggered["nv"]:
        z3, sig3 = harness.ev_gap_zscore(ev_untriggered, ev_triggered)
    print(f"[main] 측정3 격차(신호추종-비신호)={ev_triggered['ev_R']-ev_untriggered['ev_R'] if ev_triggered['ev_R'] and ev_untriggered['ev_R'] else None} z={z3} 유의={sig3}", flush=True)

    # ── 판정 ──────────────────────────────────────────────────────
    adopted = bool(gap1 is not None and gap1 >= 0.15 and sig1)
    print("\n" + "=" * 70)
    print("판정")
    print("=" * 70)
    print(f"  확산 vs 미분류: 격차={gap1} z={z1} → {'채택(강세 테마 소속 필터)' if adopted else '기록(테마 단계는 EV 무관, 관찰용)'}", flush=True)

    result = {
        "n_records": len(records),
        "stage_summary": stage_summary,
        "measure1": {"gap": gap1, "z": z1, "sig": sig1, "adopted": adopted},
        "measure2": {"ev_mapped": ev_mapped, "ev_unmapped": ev_unmapped, "z": z2, "sig": sig2},
        "measure3": {"sig_pairs": sig_pairs, "ev_triggered": ev_triggered, "ev_untriggered": ev_untriggered,
                     "z": z3, "sig": sig3},
        "elapsed_s": time.time() - t0,
    }
    with open("/tmp/kr_theme_stage_signal_ev_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("[main] 결과 JSON: /tmp/kr_theme_stage_signal_ev_result.json (커밋 대상 아님, 참고용)", flush=True)
    print(f"[main] 총 소요시간 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

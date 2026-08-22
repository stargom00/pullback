"""
주도주 눌림목 탈락 사유 진단 (2026-08-23) — 측정 전용, scanner.py/app.py 미수정.

대상 종목: MSTR, BMNR, CRCL, NBIS, PLTR(미국), OCI홀딩스(010060.KS, 한국)
기간: 최근 60거래일. offset=0(오늘)~59(59거래일 전)까지 매일, 그날을
"최신봉"으로 데이터를 잘라(harness.truncate_at) 눌림목 파이프라인을 그대로
돌리고 어느 게이트에서 탈락하는지 기록한다.

게이트 판정은 app.py의 `_trace_pullback(df, is_kr, rs_rank)`를 그대로
가져다 쓴다 — scanner.analyze()와 동일한 조건을 다시 구현한 게 아니라
scanner.py의 실제 함수(select_pivot/_risk_hard_ok/late_stage_info/
_merger_block 등)를 그대로 호출하는 app.py의 기존 진단 재현 함수다
(CLAUDE.md "_trace_* 사본" 절, test_trace_parity.py가 analyze()와의
값 일치를 검증). 이 스크립트가 조건을 새로 베껴 쓰면 CLAUDE.md가 경고하는
"리터럴 사본이 조용히 낡는" 문제를 그대로 반복하게 되므로 반드시 이 함수를
import해서 쓴다.

RS 랭크는 scripts/measurements/harness.py로 매 offset마다 전체 유니버스
기준으로 재계산한다(/api/debug의 rs_min=80 근사치와 달리 실제 스캔과
동일한 방식 — harness.py 2절 주석 참고).

부가 라벨링(③ "떴어야 할 날" 추정)만 이 스크립트 자체 로직이다: 20일선
근접 판정은 scanner.CONFIG["ma_proximity"](3.5%)를 실제 값 그대로 재사용
하고, "반등"은 스캐너에 없는 개념이라 이 스크립트가 정의한 휴리스틱
(향후 10거래일 내 종가가 그날 종가 대비 +5% 이상)이다 — 게이트 조건이
아니라 "관찰 후보일"을 추리는 보조 표시일 뿐임을 출력에도 명시한다.

실행: 리포 루트에서
  python3 scripts/measurements/reject_tracer.py
전체 유니버스 fetch(5~7분) + 60개 offset × 전체 유니버스 RS 재계산이 필요해
총 15~25분 정도 걸린다(네트워크 필요).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time

import harness
from scanner import CONFIG as PB_CONFIG
from app import _trace_pullback

N_DAYS = 60  # 최근 60거래일: offset 0(오늘) ~ 59
RS_FLOOR_BARS = 200  # rs_raw_score 자체 요구치(scanner.py 1276행) — 이 밑이면 RS 계산 자체가 None

TARGETS = [
    ("MSTR", False, "MSTR"),
    ("BMNR", False, "BMNR"),
    ("CRCL", False, "CRCL"),
    ("NBIS", False, "NBIS"),
    ("PLTR", False, "PLTR"),
    ("010060.KS", True, "OCI홀딩스"),
]

# app.py _trace_pullback의 fail_at 값 = 실제 analyze() 게이트 순서 그대로
GATE_ORDER = [
    "min_bars", "min_bars_dropna", "rs_min", "nan", "우상향추세",
    "avwap_extended", "눌림폭", "이평선지지", "RSI",
    "risk_hard", "late_stage_danger", "merger",
]
GATE_LABEL = {
    "min_bars": "데이터부족(min_bars)",
    "min_bars_dropna": "데이터부족(dropna후)",
    "rs_min": "RS낮음/계산불가",
    "nan": "지표계산불가(NaN)",
    "우상향추세": "추세게이트(MA배열/기울기)",
    "avwap_extended": "돌파일 연장가드(AVWAP)",
    "눌림폭": "눌림폭 범위밖",
    "이평선지지": "이평선 미근접",
    "RSI": "RSI 범위밖",
    "risk_hard": "리스크 하드게이트",
    "late_stage_danger": "후기스테이지 위험",
    "merger": "M&A/특수상황 배제",
    "PASS": "PASS(통과)",
    "티커없음": "티커없음(유니버스/fetch 실패)",
}


def _fmt_date(idx_val):
    try:
        return str(idx_val.date())
    except AttributeError:
        return str(idx_val)


def run():
    t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    for ticker, _, name in TARGETS:
        if ticker not in data:
            print(f"[WARN] {name}({ticker}) 유니버스 fetch 데이터 없음 — 결과에서 제외됨")

    tickers = list(data.keys())
    records = {ticker: [] for ticker, _, _ in TARGETS}

    for off in range(N_DAYS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)

        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < RS_FLOOR_BARS:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, _rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for ticker, is_kr, name in TARGETS:
            if ticker not in data:
                records[ticker].append({"off": off, "date": None, "fail_at": "티커없음",
                                         "rs_rank": None, "close": None, "dist20_pct": None,
                                         "steps": []})
                continue
            hist = harness.truncate_at(data[ticker], off)
            if len(hist) < PB_CONFIG["min_bars"]:
                records[ticker].append({"off": off, "date": _fmt_date(hist.index[-1]) if len(hist) else None,
                                         "fail_at": "min_bars", "rs_rank": rs_ranks.get(ticker),
                                         "close": None, "dist20_pct": None, "steps": []})
                continue
            rr = rs_ranks.get(ticker)
            trace = _trace_pullback(hist, is_kr, rr)
            close = float(hist["Close"].iloc[-1])
            ma20 = float(hist["Close"].rolling(PB_CONFIG["ma_mid"]).mean().iloc[-1])
            dist20_pct = (close - ma20) / ma20 * 100 if ma20 else None
            fail_at = "PASS" if trace["passed"] else trace["fail_at"]
            records[ticker].append({
                "off": off, "date": _fmt_date(hist.index[-1]),
                "fail_at": fail_at, "rs_rank": rr, "close": close,
                "dist20_pct": dist20_pct, "steps": trace["steps"],
            })
        if (off + 1) % 10 == 0 or off == N_DAYS - 1:
            print(f"[reject_tracer] offset {off} 완료 ({off+1}/{N_DAYS}) elapsed={time.time()-t0:.0f}s", flush=True)

    print_matrix(records)
    print_ranking(records)
    print_candidates(records, data)
    return records


def print_matrix(records):
    print("\n" + "=" * 70)
    print("1) 종목별 × 조건별 탈락 횟수 매트릭스 (최근 60거래일)")
    print("=" * 70)
    cols = GATE_ORDER + ["PASS", "티커없음"]
    header = "종목".ljust(12) + "".join(c[:8].center(10) for c in cols)
    print(header)
    for ticker, _, name in TARGETS:
        counts = {c: 0 for c in cols}
        for rec in records[ticker]:
            counts[rec["fail_at"]] = counts.get(rec["fail_at"], 0) + 1
        row = name.ljust(12) + "".join(str(counts.get(c, 0)).center(10) for c in cols)
        print(row)


def print_ranking(records):
    print("\n" + "=" * 70)
    print("2) 전체 탈락 사유 랭킹 (6종목 합산, PASS/티커없음 제외)")
    print("=" * 70)
    total = {c: 0 for c in GATE_ORDER}
    for ticker, _, _ in TARGETS:
        for rec in records[ticker]:
            if rec["fail_at"] in total:
                total[rec["fail_at"]] += 1
    ranked = sorted(total.items(), key=lambda kv: -kv[1])
    for gate, cnt in ranked:
        if cnt == 0:
            continue
        print(f"  {GATE_LABEL.get(gate, gate):<28} {cnt:>4}건")


def print_candidates(records, data):
    print("\n" + "=" * 70)
    print("3) '눌림목으로 떴어야 할 날' 추정 (20일선 근접 후 반등, 휴리스틱)")
    print(f"   근접 기준: scanner.CONFIG['ma_proximity']={PB_CONFIG['ma_proximity']*100:.1f}% 이내")
    print("   반등 기준: 이후 10거래일 내 종가가 그날 종가 대비 +5% 이상 (이 스크립트만의 라벨링, 게이트 아님)")
    print("=" * 70)
    for ticker, is_kr, name in TARGETS:
        if ticker not in data:
            continue
        full = data[ticker]
        recs_by_off = {r["off"]: r for r in records[ticker]}
        found_any = False
        for off in range(N_DAYS):
            idx = len(full) - 1 - off
            if idx < PB_CONFIG["ma_mid"] - 1:
                continue
            rec = recs_by_off.get(off)
            if rec is None or rec["dist20_pct"] is None:
                continue
            if abs(rec["dist20_pct"]) > PB_CONFIG["ma_proximity"] * 100:
                continue
            future = full.iloc[idx + 1: idx + 11]
            if future.empty:
                continue
            day_close = rec["close"]
            rebound = float(future["Close"].max()) >= day_close * 1.05
            if not rebound:
                continue
            found_any = True
            print(f"\n[{name}] {rec['date']} (offset={off}) close={day_close:.2f} "
                  f"20일선거리={rec['dist20_pct']:+.2f}% rs={rec['rs_rank']} "
                  f"→ 결과: {GATE_LABEL.get(rec['fail_at'], rec['fail_at'])}")
            for step in rec["steps"]:
                mark = "OK" if step["ok"] else "FAIL"
                print(f"    [{mark}] {step['gate']}: {step['detail']}")
            if rec["fail_at"] == "PASS":
                print("    → 이미 눌림목 탭에서 정상적으로 잡힘")
        if not found_any:
            print(f"\n[{name}] 최근 60거래일 내 20일선 근접+반등 후보일 없음")


if __name__ == "__main__":
    run()

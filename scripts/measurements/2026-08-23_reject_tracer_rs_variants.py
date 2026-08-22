"""
reject_tracer.py 후속 측정 (2026-08-23) — RS 정의를 바꾸면 주도주 포착이
얼마나 나아지는지, 그 대가로 전체 유니버스에 잡음이 얼마나 늘어나는지.
scanner.py/app.py는 미수정(읽기 전용 import). 결과는 커밋하지 않는다(요청).

선행: scripts/measurements/reject_tracer.py — 6종목 게이트별 탈락 원인 추적.
결론: "RS낮음"(rs_min=80 미달)이 압도적 1위 사유, MSTR/BMNR/CRCL/PLTR 4종목이
전부 이걸로 탈락, NBIS만 RS를 통과하고 눌림폭/추세 게이트에서 탈락.

이번 측정 4가지:
  1) "RS낮음"(랭크는 있으나 80 미만) vs "RS계산불가"(rs_raw_score가 None,
     즉 200봉 미만) 재분류. BMNR/CRCL 가용 데이터 길이 확인 + 12개월(4분기,
     scanner.rs_raw_score 4번째 분기 요구치 253봉) 미충족 구간에 대한
     대체 지표(가용 기간 전체 단일수익률) 비교.
  2) 전체 유니버스 대상 신규 파생 필드 3개(최근 60거래일): rs_3m / rs_delta
     / depth_atr — scanner.py에 없는 새 지표라 이 스크립트가 직접 정의.
  3) 진단 6종목에 대해 RS 게이트 변형 A/B/C/D 비교.
  4) 각 변형이 전체 유니버스에서 통과시키는 종목 수 배율 + 통과종목
     KR 시총 중앙값(naver_kr 실제 데이터) / 50일 평균거래대금 중앙값
     (scanner.volume_info 실함수).

RS 계산 자체(rs_raw_score/to_rs_rank, KR 벤치마크 차감, KR/US 분리 랭킹)는
scanner.py/harness.py의 실제 함수를 그대로 가져다 쓴다 — 이 스크립트가
새로 정의하는 건 rs_3m/rs_delta/depth_atr 3개 파생지표와 짧은 이력 fallback
뿐이고, 전부 "scanner.py에 아예 없는 새 개념"이라 원본 조건을 다시 구현하는
것과는 다르다(CLAUDE.md가 경고하는 "리터럴 사본" 문제는 기존 게이트를
베낄 때의 얘기).

데이터 소스 한계(CLAUDE.md 원칙 "처음에 선언"): 미국 종목 시가총액은
유니버스 규모(2000+)에서 종목당 개별 API 호출 없이는 얻을 수 없어(yfinance
bulk download는 OHLCV만 제공, market cap은 티커당 별도 호출 필요) 이번
측정에서 US 시총은 계산하지 않는다. 대신 50일 평균거래대금(양 시장 공통,
실제 scanner.volume_info 값)으로 대체.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-23_reject_tracer_rs_variants.py
전체 유니버스 fetch(5~7분) + rs_delta용 80개 offset(0~79) RS 재계산 +
60개 offset 전체 유니버스 depth_atr/거래대금 계산 필요. 20~30분 정도.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import math
import statistics
import time

import harness
from scanner import CONFIG as PB_CONFIG, rs_raw_score, to_rs_rank, atr as scanner_atr, volume_info
import naver_kr

N_DAYS = 60                    # 최근 60거래일: offset 0(오늘) ~ 59
RS_DELTA_LOOKBACK = 20         # rs_delta = rs[off] - rs[off+20]
TOTAL_OFFSETS = N_DAYS + RS_DELTA_LOOKBACK   # 0~79, 80개 — rs_delta가 off=59에서도 off+20=79를 참조
RS_MIN_BARS = 200              # rs_raw_score 자체 요구치 (scanner.py 1276행)
Q4_BARS = 253                  # rs_raw_score 4번째 분기(252일) 요구치 — 이 밑이면 재정규화(3분기 이하)

TARGETS = [
    ("MSTR", False, "MSTR"),
    ("BMNR", False, "BMNR"),
    ("CRCL", False, "CRCL"),
    ("NBIS", False, "NBIS"),
    ("PLTR", False, "PLTR"),
    ("010060.KS", True, "OCI홀딩스"),
]


# ── RS 계산 오케스트레이션 (harness.compute_rs_at_checkpoint과 동일 로직 +
#    None(계산불가) 집합을 추가로 반환) ──────────────────────────────────
def compute_rs_with_detail(trunc_cache, b_kospi, b_kosdaq):
    kr_raw, us_raw, none_set = {}, {}, set()
    for t, hist in trunc_cache.items():
        raw = rs_raw_score(hist["Close"])
        if raw is None:
            none_set.add(t)
            continue
        if harness.is_kr_ticker(t):
            bscore = b_kospi if t.endswith(".KS") else b_kosdaq
            kr_raw[t] = raw - bscore
        else:
            us_raw[t] = raw
    rs_ranks = {**to_rs_rank(kr_raw), **to_rs_rank(us_raw)}
    return rs_ranks, none_set, kr_raw, us_raw


def rs_3m_ranks(trunc_cache):
    """3개월(63거래일) 단순수익률만으로 계산한 RS 백분위. harness의 rs_mom
    내부 계산(rank3)과 동일 산식(벤치마크 차감 없음)이지만 여기선 그 자체를
    독립 필드로 노출."""
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


def rs_score_available_period(close, cap_days=252, min_days=60):
    """가용 이력이 짧아 scanner.rs_raw_score의 4분기 요구(≥253봉)를 못 채우는
    종목 전용 대체 지표 — scanner.py에는 없는 이 스크립트만의 실험적 정의.
    분기 가중 구조를 쓰지 않고 가용 기간 전체(최대 252봉)를 단일 구간
    로그수익률로 본다. scanner.rs_raw_score와 같은 클리핑(±0.7)만 재사용."""
    c = close.dropna()
    avail = len(c) - 1
    if avail < min_days:
        return None
    lookback = min(avail, cap_days)
    now = float(c.iloc[-1])
    past = float(c.iloc[-1 - lookback])
    if past <= 0:
        return None
    r = math.log(now / past)
    return max(-0.7, min(0.7, r))


def depth_atr_for(hist):
    """눌림폭(고가60 대비 낙폭 — scanner.analyze()의 비돌파일 분기 산식과
    동일 산술, pullback = (high60-close)/high60)을 ATR%(scanner.atr 실함수)
    배수로 표현. scanner.py에 없는 신규 파생지표. 돌파일(전일比+4%) 분기
    산식은 생략하고 비돌파일 산식을 전 종목에 통일 적용한 근사치."""
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
    """KR 종목별 시가총액(억원), 1회 스냅샷. naver_kr.fetch_high_marketcap_allowed
    와 동일 페이지네이션(같은 _MARKETSUM_URL/_parse_marketcap_rows 실함수
    재사용)이지만 임계값 컷 없이 전부 모은다 — 얘는 측정 스크립트 전용
    소규모 오케스트레이션이고, 실제 파싱 로직(_parse_marketcap_rows)은
    naver_kr.py 그대로."""
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


def variant_pass(rs, rs3m, rs_delta):
    a = rs is not None and rs >= 80
    b = rs3m is not None and rs3m >= 80
    c = (rs is not None and rs >= 50) and (rs_delta is not None and rs_delta >= 25)
    d = b or c
    return {"A": a, "B": b, "C": c, "D": d}


def run():
    t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())

    print("[v2] KR 시총 스냅샷 fetch 중...", flush=True)
    try:
        kr_mcap = fetch_kr_marketcap_eok()
    except Exception as e:
        print(f"[WARN] KR 시총 fetch 실패: {e}")
        kr_mcap = {}
    print(f"[v2] KR 시총 {len(kr_mcap)}종목 확보, elapsed={time.time()-t0:.0f}s", flush=True)

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
        rs_ranks, none_set, kr_raw, us_raw = compute_rs_with_detail(trunc_cache, b_kospi, b_kosdaq)
        r3_ranks = rs_3m_ranks(trunc_cache)
        rec = {"rs_ranks": rs_ranks, "none_set": none_set, "r3_ranks": r3_ranks,
               "kr_raw": kr_raw, "us_raw": us_raw}
        if off < N_DAYS:
            depth, turnover = {}, {}
            for t, hist in trunc_cache.items():
                da = depth_atr_for(hist)
                if da is not None:
                    depth[t] = da
                try:
                    vi = volume_info(float(hist["Close"].iloc[-1]), hist["Volume"])
                    turnover[t] = vi["avg_turnover"]
                except Exception:
                    pass
            rec["depth_atr"] = depth
            rec["avg_turnover"] = turnover
        per_offset[off] = rec
        if (off + 1) % 10 == 0 or off == TOTAL_OFFSETS - 1:
            print(f"[v2] offset {off} 완료 ({off+1}/{TOTAL_OFFSETS}) elapsed={time.time()-t0:.0f}s", flush=True)

    part1(data, per_offset)
    part2(per_offset)
    part3(data, per_offset)
    part4(data, per_offset, kr_mcap)


# ── 1) RS낮음 vs RS계산불가 + BMNR/CRCL 가용 이력 + fallback 비교 ──────
def part1(data, per_offset):
    print("\n" + "=" * 70)
    print("1) RS낮음 vs RS계산불가 재분류 (6종목, 최근 60거래일)")
    print("=" * 70)
    for ticker, is_kr, name in TARGETS:
        low = none_cnt = passed = 0
        for off in range(N_DAYS):
            rec = per_offset[off]
            if ticker in rec["none_set"]:
                none_cnt += 1
                continue
            rr = rec["rs_ranks"].get(ticker)
            if rr is None:
                none_cnt += 1
            elif rr >= PB_CONFIG["rs_min"]:
                passed += 1
            else:
                low += 1
        print(f"  {name:<10} RS낮음(계산됨,rank<80)={low:>3}건  RS계산불가(None)={none_cnt:>3}건  통과={passed:>3}건")

    print("\nBMNR/CRCL 가용 데이터 길이 (offset=0=오늘 기준, 이후 매일 -1봉):")
    for ticker in ("BMNR", "CRCL"):
        if ticker not in data:
            print(f"  {ticker}: 유니버스 fetch 데이터 없음")
            continue
        n = len(data[ticker])
        first = data[ticker].index[0]
        first_s = str(first.date()) if hasattr(first, "date") else str(first)
        status = "충족" if n >= Q4_BARS else "부족"
        crossover = max(0, n - Q4_BARS)
        print(f"  {ticker}: 전체 {n}봉 (첫 거래일 {first_s}) — 4분기(≥{Q4_BARS}봉) 요구치 대비 오늘 {status}, "
              f"offset={crossover} 이후부터는 4분기 미충족(3분기 이하로 재정규화)")

    print("\n짧은 이력 fallback(가용기간 전체 단일수익률, 이 스크립트 정의) vs "
          "표준(scanner.rs_raw_score 분기가중) 랭크 비교 (5일 간격 샘플):")
    header = f"  {'종목':<6}{'offset':>7}{'가용봉수':>9}{'4분기충족':>10}{'표준rank':>9}{'fallback_raw':>14}{'fallback_rank':>15}"
    print(header)
    for ticker in ("BMNR", "CRCL"):
        if ticker not in data:
            continue
        for off in range(0, N_DAYS, 5):
            hist = harness.truncate_at(data[ticker], off)
            avail = len(hist)
            has_q4 = avail >= Q4_BARS
            std_rank = per_offset[off]["rs_ranks"].get(ticker)
            fb_raw = rs_score_available_period(hist["Close"])
            fb_rank = None
            if fb_raw is not None:
                us_raw = dict(per_offset[off]["us_raw"])
                us_raw[ticker] = fb_raw
                fb_rank = to_rs_rank(us_raw).get(ticker)
            print(f"  {ticker:<6}{off:>7}{avail:>9}{('충족' if has_q4 else '부족'):>10}"
                  f"{str(std_rank):>9}{(f'{fb_raw:.3f}' if fb_raw is not None else 'None'):>14}{str(fb_rank):>15}")


# ── 2) 신규 파생 필드 3개 — 유니버스 분포 스냅샷으로 정합성 확인 ────────
def part2(per_offset):
    print("\n" + "=" * 70)
    print("2) 신규 파생 필드 3개 (전체 유니버스, offset=0 분포 스냅샷)")
    print("=" * 70)
    rec = per_offset[0]
    r3_vals = list(rec["r3_ranks"].values())
    rs_vals = list(rec["rs_ranks"].values())
    depth_vals = [v for v in rec["depth_atr"].values() if v is not None]
    rs_20ago = per_offset[20]["rs_ranks"]
    deltas = [rec["rs_ranks"][t] - rs_20ago[t] for t in rec["rs_ranks"] if t in rs_20ago]
    print(f"  rs_3m    : n={len(r3_vals)}  median={statistics.median(r3_vals):.0f}  (백분위라 이론상 1~99 균등)")
    print(f"  rs_delta : n={len(deltas)}  median={statistics.median(deltas):.0f}  "
          f"min={min(deltas)}  max={max(deltas)}  (+면 20일전보다 RS 상승)")
    print(f"  depth_atr: n={len(depth_vals)}  median={statistics.median(depth_vals):.2f}  "
          f"(눌림폭÷ATR% — 1.0=조정폭이 ATR 1배, NBIS 후보 기준 <=3.0)")
    print("\n  6종목 오늘(offset=0) 값:")
    for ticker, is_kr, name in TARGETS:
        rs = rec["rs_ranks"].get(ticker)
        r3 = rec["r3_ranks"].get(ticker)
        delta = (rs - rs_20ago.get(ticker)) if (rs is not None and ticker in rs_20ago) else None
        da = rec["depth_atr"].get(ticker)
        print(f"    {name:<10} rs={str(rs):>5}  rs_3m={str(r3):>5}  rs_delta={str(delta):>5}  "
              f"depth_atr={(f'{da:.2f}' if da is not None else 'None'):>6}")


# ── 3) 6종목 × 게이트 변형 A/B/C/D 매트릭스 ─────────────────────────────
def part3(data, per_offset):
    print("\n" + "=" * 70)
    print("3) 진단 6종목 × RS 게이트 변형 A/B/C/D — 60일 중 통과 일수")
    print("   A: rs(12개월)>=80(현행)  B: rs_3m>=80  C: rs>=50 AND rs_delta>=+25  D: B or C")
    print("=" * 70)
    header = f"  {'종목':<10}{'A':>6}{'B':>6}{'C':>6}{'D':>6}"
    print(header)
    for ticker, is_kr, name in TARGETS:
        counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        for off in range(N_DAYS):
            rs = per_offset[off]["rs_ranks"].get(ticker)
            rs3m = per_offset[off]["r3_ranks"].get(ticker)
            rs_20ago = per_offset[off + RS_DELTA_LOOKBACK]["rs_ranks"].get(ticker)
            rs_delta = (rs - rs_20ago) if (rs is not None and rs_20ago is not None) else None
            flags = variant_pass(rs, rs3m, rs_delta)
            for k, v in flags.items():
                if v:
                    counts[k] += 1
        print(f"  {name:<10}{counts['A']:>6}{counts['B']:>6}{counts['C']:>6}{counts['D']:>6}")

    if "NBIS" in data:
        nbis_pass = sum(
            1 for off in range(N_DAYS)
            if per_offset[off]["depth_atr"].get("NBIS") is not None
            and per_offset[off]["depth_atr"]["NBIS"] <= 3.0
        )
        print(f"\n  NBIS 눌림폭 게이트를 depth_atr<=3.0으로 교체 시: {nbis_pass}/60일 통과")
        print("  (참고: NBIS는 이미 A(현행 RS)를 대부분 통과하므로, 이 대체 조건이 "
              "실제 개선 여지가 있는 유일한 축은 눌림폭 게이트임 — reject_tracer.py 1차 조사 결론과 일치)")


# ── 4) 전체 유니버스 반대쪽 측정: 변형별 통과 종목 수 배율 + 규모 지표 ──
def part4(data, per_offset, kr_mcap):
    print("\n" + "=" * 70)
    print("4) 전체 유니버스 — 변형별 통과 종목 수 배율 + 통과종목 규모(60일)")
    print("=" * 70)
    tickers = list(data.keys())
    daily_counts = {v: [] for v in "ABCD"}
    ever_pass = {v: set() for v in "ABCD"}

    for off in range(N_DAYS):
        rs_ranks = per_offset[off]["rs_ranks"]
        r3_ranks = per_offset[off]["r3_ranks"]
        rs_20ago = per_offset[off + RS_DELTA_LOOKBACK]["rs_ranks"]
        cnts = {v: 0 for v in "ABCD"}
        for t in tickers:
            rs = rs_ranks.get(t)
            rs3m = r3_ranks.get(t)
            rs_delta = (rs - rs_20ago.get(t)) if (rs is not None and t in rs_20ago) else None
            flags = variant_pass(rs, rs3m, rs_delta)
            for k, v in flags.items():
                if v:
                    cnts[k] += 1
                    ever_pass[k].add(t)
        for v in "ABCD":
            daily_counts[v].append(cnts[v])

    avg_daily = {v: sum(daily_counts[v]) / N_DAYS for v in "ABCD"}
    base = avg_daily["A"] or 1.0
    print(f"  {'변형':<6}{'일평균 통과종목수':>18}{'A 대비 배율':>14}{'60일 누적 유니크종목수':>22}")
    for v in "ABCD":
        mult = avg_daily[v] / base
        print(f"  {v:<6}{avg_daily[v]:>18.0f}{mult:>13.2f}배{len(ever_pass[v]):>22}")

    print("\n  통과종목(60일 누적 유니크) 규모 — KR 시총(억원) 중앙값 / 50일평균거래대금(원) 중앙값(offset=0 기준):")
    print(f"  {'변형':<6}{'KR건수':>8}{'KR시총중앙값(억원)':>20}{'거래대금표본수':>16}{'50일평균거래대금중앙값':>24}")
    for v in "ABCD":
        kr_caps, turnovers = [], []
        for t in ever_pass[v]:
            if harness.is_kr_ticker(t):
                cap = kr_mcap.get(t)
                if cap:
                    kr_caps.append(cap)
            to_ = per_offset[0]["avg_turnover"].get(t)
            if to_:
                turnovers.append(to_)
        med_cap = statistics.median(kr_caps) if kr_caps else None
        med_to = statistics.median(turnovers) if turnovers else None
        print(f"  {v:<6}{len(kr_caps):>8}{(f'{med_cap:,.0f}' if med_cap else 'N/A'):>20}"
              f"{len(turnovers):>16}{(f'{med_to:,.0f}' if med_to else 'N/A'):>24}")
    print("\n  (US 시총은 계산 안 함 — 스크립트 상단 docstring '데이터 소스 한계' 참고. "
          "50일평균거래대금은 KR/US 공통으로 scanner.volume_info 실함수 값)")


if __name__ == "__main__":
    run()

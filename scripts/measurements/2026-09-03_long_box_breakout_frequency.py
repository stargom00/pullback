"""
장기 박스(120/250봉) 돌파 탐지 가능성 측정 — 히트 "개수"만, 2R 레이스 없음
(2026-09-03)

목적: analyze_boxbreak(BOXBREAK_CONFIG)은 box_windows=[20,40,60]만 본다.
1년 이상(약 250봉) 지속된 박스를 뚫는 종목까지 잡으면 새 신호가 되는지
"몇 건이나 나오는지"만 먼저 잰다 — 채택 여부 판단 이전 단계.

**scanner.py/app.py는 전혀 안 건드림**: BOXBREAK_CONFIG/analyze_boxbreak
원본 그대로 참조만 하고(리터럴 복사 없음), VERSION 범프 없음. 이 스크립트는
읽기 전용 측정이다.

── 0단계 결론(사용자 승인 사항, 요약) ──────────────────────────────────
1) min_bars = 252로 확정. analyze_boxbreak 실제 코드를 읽어 확인한 결과
   ma_long(120)과 box_window는 "같은 잘린 시리즈의 꼬리"에서 각자 슬라이스
   하지 쌓이지 않는다(`c.rolling(120).mean().iloc[-1]`는 전체 길이만
   ≥120이면 되고, `h.iloc[-(win+1):-1]`도 같은 시리즈 끝에서 win봉만
   본다) — 250+120=370이 아니라 max(box_window+2, 120)=252(box_window=250
   기준)가 실제 요구치.
2) fetch_universe_data(kr_days=1900, us_period="5y")는 **이 측정 스크립트
   전용**이다. 프로덕션(app.py)의 실제 fetch 기간(KR 730일≈483봉/US
   2y≈505봉)은 250봉 창이 요구하는 252봉(offset=0 기준)을 이미 넘기므로
   손댈 필요가 없다 — 채택되더라도 app.py의 fetch 기간 변경은 불필요
   (사용자 확인, "나중에 채택되더라도 app.py의 fetch 기간은 손댈 필요
   없음").
3) checkpoints(60,950,10)=90개, min_bars=252로 실측: need=950+252=1202일
   때 KR 결측 15.8%, US 11.9% — harness.assert_sufficient_depth의 20%
   기준 통과(2026-09-03 전체 유니버스 실측: KR 1504종목 fetched 1504,
   max 1275봉; US 2120종목 fetched 2108, max 1254봉).
4) **생존편향 경고(사용자 지적, 핵심)**: 유니버스가 "오늘 기준" 거래대금/
   시총 상위라, offset이 클수록(예 950≈4년 전) "그때 장기박스를 뚫고
   커진 종목이 바로 오늘 유니버스에 남아있다"는 순환이 측정 가설과 같은
   방향으로 작동할 수 있다. 1단계(개수 세기)에선 치명적이지 않지만 2R
   레이스 단계에서 터질 수 있다 — 그래서 모든 히트에 checkpoint offset을
   기록하고 60-250/250-600/600-950 구간별로 쪼개 출력한다. 구간별
   히트(율)가 offset이 커질수록 단조 증가하면 그 자체가 편향 신호로 읽을
   것(사용자 지시).

── 로직 ─────────────────────────────────────────────────────────────
analyze_boxbreak의 게이트를 그대로 복제하되(rs_min/vol_mult/ma_long/
extended_max/max_off_high는 scanner.BOXBREAK_CONFIG를 cfg[...]로 직접
참조 — 리터럴 복사 금지 원칙, CLAUDE.md) 아래 4개 차원만 스윕:
  box_windows:   20, 40, 60(대조군 — 원본과 동일 조건으로 같이 계산), 120, 250
  box_max_range: 0.30, 0.40, 0.50, 0.60
  box_low 정의:  "min"=구간 저가 최솟값(원본과 동일) / "p5"=저가 5퍼센타일
                 (장기 박스는 급락 스파이크 하나로 폭이 터질 수 있어 5%
                 분위수 버전도 같이 본다는 게 이 측정의 핵심 질문)
  min_touches:   2, 3 (significant_resistance)
바뀌지 않는 것: rs_min 75, vol_mult 1.5, ma_long 120, extended_max 0.12,
max_off_high 25, _risk_hard_ok, late_stage_exclude, 저유동성 필터
(harness.passes_liquidity_filter).

효율화: box_high/box_range는 (window, low_def, touches) 조합(20개)당
한 번만 계산한다 — box_max_range는 그 값에 대한 사후 임계값 비교일
뿐이라 4번 재계산할 이유가 없다. 원본 analyze_boxbreak도 box_range 체크가
breakout/ext/risk_hard_ok보다 먼저라, "loosest range(0.60) 통과분만
breakout/ext/risk_hard_ok/late_stage를 1회 계산 후 4개 max_range 값에
재사용"은 각 max_range를 따로 도는 것과 결과가 완전히 동일하다(순서를
바꾼 게 아니라 공통 부분을 앞으로 뺀 것뿐).

커밋: git commit -m longbox-freq-measure
"""
import os
import sys
import time
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                 "scripts", "measurements"))

import harness  # noqa: E402
import scanner  # noqa: E402
from scanner import BOXBREAK_CONFIG as BCFG  # noqa: E402

BOX_WINDOWS = [20, 40, 60, 120, 250]
CONTROL_WINDOWS = {20, 40, 60}
NEW_WINDOWS = {120, 250}
MAX_RANGES = [0.30, 0.40, 0.50, 0.60]
LOW_DEFS = ["min", "p5"]
TOUCHES_LIST = [2, 3]

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개(규칙9), 0단계에서 확정
OFFSET_BUCKETS = [("60-250", 60, 250), ("250-600", 251, 600), ("600-950", 601, 950)]

MIN_BARS_250 = 252   # box_window(250)+2 — 0단계에서 코드로 검증한 실제 요구치
STOP_ATR_MULT = 0.15  # analyze_boxbreak과 동일(박스돌파=0.15, 타이트 유지)


def log(msg):
    print(msg, flush=True)


def bucket_of(offset: int) -> str:
    for name, lo_, hi_ in OFFSET_BUCKETS:
        if lo_ <= offset <= hi_:
            return name
    return "?"


def compute_box_unit(h, lo, close, window, low_def, min_touches):
    """box_high/box_low/box_range — (window, low_def, min_touches) 조합당 1회.
    analyze_boxbreak과 동일 슬라이스(h.iloc[-(win+1):-1] 등, exclude=1)."""
    if len(h) < window + 2:
        return None
    box_h = h.iloc[-(window + 1):-1]
    box_l = lo.iloc[-(window + 1):-1]
    sig_high = scanner.significant_resistance(h, window, min_touches=min_touches, band=0.02, exclude=1)
    box_high = float(sig_high) if sig_high is not None else float(box_h.max())
    box_low = float(box_l.min()) if low_def == "min" else float(box_l.quantile(0.05))
    if box_high <= 0:
        return None
    box_range = (box_high - box_low) / box_high
    return box_high, box_low, box_range


def evaluate_ticker_checkpoint(hist, rs_rank, is_kr):
    """analyze_boxbreak 게이트 복제(공용 부분 1회) + 스윕 조합별 히트 목록.
    반환: [{"window","low_def","touches","max_range","box_range_pct","ext_pct"}...]"""
    if hist is None or len(hist) < BCFG["ma_long"]:
        return []
    hist = hist.dropna(subset=["Close", "Volume"])
    if len(hist) < BCFG["ma_long"]:
        return []
    if rs_rank is None or rs_rank < BCFG["rs_min"]:
        return []

    c, h, lo, v = hist["Close"], hist["High"], hist["Low"], hist["Volume"]
    close = float(c.iloc[-1])
    ma_long_series = c.rolling(BCFG["ma_long"]).mean()
    m_long = float(ma_long_series.iloc[-1])
    if math.isnan(m_long):
        return []
    if scanner.off_high_pct(c) < -BCFG["max_off_high"]:
        return []
    if close < m_long:
        return []

    vol_today = float(v.iloc[-1])
    vol_avg = scanner.nonzero_vol_mean(v.iloc[-51:-1])
    vol_mult = vol_today / vol_avg if vol_avg > 0 else 0.0
    if vol_mult < BCFG["vol_mult"]:
        return []

    # 저유동성 필터(harness 공통) — volume_info/price_frozen은 window와
    # 무관하므로 여기서 1회만 계산해 관문으로 쓴다.
    _vol_info = scanner.volume_info(close, v)
    _mg = scanner._price_frozen_block(c, h, lo, v)
    if not harness.passes_liquidity_filter({**_vol_info, **_mg}, is_kr):
        return []

    # late_stage_info도 window와 무관(원본 analyze_boxbreak과 동일하게
    # best 후보 확정 뒤가 아니라 여기서 먼저 판정해도 결과는 같음).
    _ls = scanner.late_stage_info(c, lo, h, v, is_kr)
    if _ls["late_level"] == "danger" and scanner.CONFIG.get("late_stage_exclude", True):
        return []

    out = []
    loosest_range = max(MAX_RANGES)
    for window in BOX_WINDOWS:
        if len(hist) < window + 2:
            continue
        for low_def in LOW_DEFS:
            for touches in TOUCHES_LIST:
                unit = compute_box_unit(h, lo, close, window, low_def, touches)
                if unit is None:
                    continue
                box_high, box_low, box_range = unit
                if box_range > loosest_range:
                    continue
                if close <= box_high * 1.005:
                    continue   # 돌파 미확정(+0.5% 미만)
                ext = (close - box_high) / box_high
                if ext > BCFG["extended_max"]:
                    continue   # 너무 연장됨
                stop = round(box_high * 0.97, 2)
                stop, stop_struct, atr_buf = scanner.apply_atr_buffer(stop, h, lo, c, STOP_ATR_MULT)
                rrb = scanner._rr_block(box_high, stop, h, lo, c, base_low=box_low, entry=close,
                                         warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct,
                                         atr_buf=atr_buf)
                if not scanner._risk_hard_ok(rrb, is_kr):
                    continue
                for max_range in MAX_RANGES:
                    if box_range <= max_range:
                        out.append({
                            "window": window, "low_def": low_def, "touches": touches,
                            "max_range": max_range,
                            "box_range_pct": round(box_range * 100, 1),
                            "ext_pct": round(ext * 100, 1),
                        })
    return out


def main():
    t0 = time.time()
    data, kr_u, us_u = harness.fetch_universe_data(
        markets=("kr", "us"), kr_days=1900, us_period="5y", validate_offsets=OFFSETS,
    )
    bench = harness.fetch_kr_benchmarks(days=1900)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    log(f"[main] 유니버스 fetch 완료 {len(data)}종목(KR {len(kr_u)}/US {len(us_u)}) "
        f"elapsed={time.time()-t0:.0f}s")

    all_hits = []
    excl_250_by_offset = {}   # offset -> (미달종목수, 유효trunc종목수)

    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < BCFG["ma_long"]:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, _rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        excl = sum(1 for hist in trunc_cache.values() if len(hist) < MIN_BARS_250)
        excl_250_by_offset[off] = (excl, len(trunc_cache))

        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            rs = rs_ranks.get(t)
            combos = evaluate_ticker_checkpoint(hist, rs, ikr)
            if not combos:
                continue
            date = hist.index[-1]
            for combo in combos:
                combo["ticker"] = t
                combo["is_kr"] = ikr
                combo["offset"] = off
                combo["date"] = date
                all_hits.append(combo)

        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            log(f"[cp] offset={off} 완료 ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
                f"누적히트레코드={len(all_hits)}")

    log(f"[main] 전체 체크포인트 완료, 총 히트 레코드 {len(all_hits)}건, elapsed={time.time()-t0:.0f}s")
    report(all_hits, excl_250_by_offset)
    log(f"\n[main] 총 소요시간 {time.time()-t0:.0f}s")


def report(all_hits, excl_250_by_offset):
    log("\n" + "=" * 78)
    log("① 조합별 일평균 히트 수 (KR/US 분리, offset 구간별)")
    log("=" * 78)
    log("  일평균 = 해당 구간 체크포인트 개수로 나눈 값(기존 스크립트 관례).")
    n_cp_total = len(OFFSETS)
    n_cp_by_bucket = {name: sum(1 for o in OFFSETS if lo_ <= o <= hi_) for name, lo_, hi_ in OFFSET_BUCKETS}

    for window in BOX_WINDOWS:
        tag = "대조군" if window in CONTROL_WINDOWS else "신규"
        log(f"\n-- box_window={window} ({tag}) --")
        for max_range in MAX_RANGES:
            for low_def in LOW_DEFS:
                for touches in TOUCHES_LIST:
                    subset = [h for h in all_hits if h["window"] == window and h["max_range"] == max_range
                              and h["low_def"] == low_def and h["touches"] == touches]
                    kr_n = sum(1 for h in subset if h["is_kr"])
                    us_n = len(subset) - kr_n
                    kr_avg = round(kr_n / n_cp_total, 2)
                    us_avg = round(us_n / n_cp_total, 2)
                    bucket_str = []
                    for name, lo_, hi_ in OFFSET_BUCKETS:
                        b_sub = [h for h in subset if lo_ <= h["offset"] <= hi_]
                        b_kr = sum(1 for h in b_sub if h["is_kr"])
                        b_us = len(b_sub) - b_kr
                        ncp = n_cp_by_bucket[name]
                        bucket_str.append(f"{name}:KR{round(b_kr/ncp,2)}/US{round(b_us/ncp,2)}")
                    log(f"  range<={max_range:.2f} low={low_def} touch={touches}: "
                        f"KR일평균={kr_avg} US일평균={us_avg}  [{' '.join(bucket_str)}]")

    log("\n" + "=" * 78)
    log("② 신규 창(120/250) 히트 vs 기존 창(20/40/60) 히트 중복률")
    log("=" * 78)
    log("  같은 (max_range, low_def, touches) 조합 안에서, 같은 (ticker, offset)이")
    log("  기존 창(20/40/60) 중 하나로도 잡혔으면 '중복'으로 센다.")
    for max_range in MAX_RANGES:
        for low_def in LOW_DEFS:
            for touches in TOUCHES_LIST:
                old_keys = {(h["ticker"], h["offset"]) for h in all_hits
                            if h["window"] in CONTROL_WINDOWS and h["max_range"] == max_range
                            and h["low_def"] == low_def and h["touches"] == touches}
                for window in sorted(NEW_WINDOWS):
                    new_keys = [(h["ticker"], h["offset"]) for h in all_hits
                                if h["window"] == window and h["max_range"] == max_range
                                and h["low_def"] == low_def and h["touches"] == touches]
                    if not new_keys:
                        continue
                    new_keys_set = set(new_keys)
                    overlap = sum(1 for k in new_keys_set if k in old_keys)
                    pct = round(overlap / len(new_keys_set) * 100, 1)
                    log(f"  range<={max_range:.2f} low={low_def} touch={touches} window={window}: "
                        f"신규유니크히트={len(new_keys_set)} 중복={overlap}건 ({pct}%)")

    log("\n" + "=" * 78)
    log("③ min_bars(250창=252봉) 미달로 제외된 종목 수")
    log("=" * 78)
    sample_offsets = [OFFSETS[0], OFFSETS[len(OFFSETS) // 2], OFFSETS[-1]]
    for off in sample_offsets:
        excl, tot = excl_250_by_offset[off]
        log(f"  offset={off}: {excl}/{tot}종목 미달({round(excl/tot*100,1) if tot else 0}%)")
    all_excl_fracs = [excl / tot for excl, tot in excl_250_by_offset.values() if tot]
    log(f"  전체 90개 체크포인트 평균 미달률: {round(sum(all_excl_fracs)/len(all_excl_fracs)*100,1)}%")
    log(f"  전체 90개 체크포인트 최대 미달률: {round(max(all_excl_fracs)*100,1)}% "
        f"(offset={max(excl_250_by_offset, key=lambda o: excl_250_by_offset[o][0]/excl_250_by_offset[o][1] if excl_250_by_offset[o][1] else 0)})")

    log("\n" + "=" * 78)
    log("④ 히트 샘플 20건 (신규 창 120/250, offset 전 구간에서 고르게)")
    log("=" * 78)
    new_hits = sorted([h for h in all_hits if h["window"] in NEW_WINDOWS], key=lambda h: (h["offset"], h["ticker"]))
    if new_hits:
        stride = max(1, len(new_hits) // 20)
        sample = new_hits[::stride][:20]
        for h in sample:
            log(f"  {h['ticker']} date={h['date'].date() if hasattr(h['date'],'date') else h['date']} "
                f"offset={h['offset']} window={h['window']} low_def={h['low_def']} touch={h['touches']} "
                f"box_range={h['box_range_pct']}% ext={h['ext_pct']}% max_range={h['max_range']}")
    else:
        log("  신규 창 히트가 없습니다.")


if __name__ == "__main__":
    main()

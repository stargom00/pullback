"""
장기 박스(250봉) 돌파 — 비중복 히트의 EV 측정, 2단계 (2026-09-04)

목적: 1단계(2026-09-03_long_box_breakout_frequency.py)에서 250봉 창 히트의
84~90%가 이미 기존 20/40/60봉 창으로도 잡히는 종목임을 확인했다. 진짜 질문은
"250봉 히트 전체의 EV"가 아니라 **"250봉에서만 잡히고 20/40/60에서는 안
잡힌 히트(비중복, 약 10~16%)의 EV"** — 중복분은 어차피 기존 창이 잡으므로
추가 가치는 비중복분에서만 나온다(사용자 지시).

**scanner.py/app.py는 전혀 안 건드림**: BOXBREAK_CONFIG/analyze_boxbreak
원본 참조만, VERSION 범프 없음. harness.py는 측정 인프라라 새 유틸(아래
one_sample_zscore) 하나를 추가했다 — README "하네스에 없는 새 로직이
필요하면 하네스를 확장" 원칙, scanner.py/app.py와는 별개.

──────────────────────────────────────────────────────────────────────
※ 사전등록 수정 기록 (결과 확인 전, 2026-09-04) ※
──────────────────────────────────────────────────────────────────────
원안: "3분위(60-250/250-600/600-950) 전부 z≥1.96 유의"를 채택 조건에 포함.
수정 사유(사용자 지시, 결과 계산 전): 3분위 각각 n≈30~40 수준이라(전체
n을 3등분) 전부 z≥1.96을 요구하면 실제 효과가 있어도 표본 부족만으로
"판정불가"가 되어 버린다 — 검정력 문제이지 신호가 없다는 뜻이 아님.

수정된 4번/채택기준의 "3분위 조건"을 아래로 교체:
  (a) 합산(pooled) 비중복 n≥100 & z≥1.96(단일표본, harness.one_sample_zscore)
  (b) 3분위 전부 EV>0 (방향 일치만 요구, 유의성 불요)
  (c) 600-950 구간 EV ≤ 60-250 구간 EV × 2
      (오래된 offset일수록 EV가 과도하게 커지면 생존편향 신호로 간주)
  구간별 z는 참고로 계속 병기(게이트 아님).
나머지 채택 기준(사전등록 그대로): EV≥+0.15R(pooled), 시기반분 재현(older/
recent 둘 다 EV≥+0.15R), n<100이면 그 range는 "판정불가"로 끝내고 억지로
결론 내지 않음.

── 대상 축소(사용자 지시) ──────────────────────────────────────────────
- window=120 전부 제외(1단계에서 91~94% 중복 확인 — 새 정보 없음).
- box_max_range: 0.30(프로덕션, n부족 알고도 참고용) / 0.45 / 0.60(주 판정
  후보, n≥100 기대) 세 수준만.
- low_def/min_touches는 스윕하지 않고 **"p5"/2로 고정**한다(사용자가 이번
  메시지에서 range만 재지정했고 low_def/touches는 언급 안 함 — 1단계에서
  "장기 박스는 급락 스파이크로 폭이 터질 수 있다"는 게 p5를 도입한 원래
  동기였고, min_touches=2는 analyze_boxbreak가 실제로 쓰는 하드코딩값과
  동일해 "프로덕션에 가장 가깝게 얹는 안"에 해당 — 이 선택 자체가 결과
  해석에 영향을 주므로 여기 명시한다. min/touch=3 조합까지 필요하면 후속
  측정으로 분리할 것).
- range를 풀수록 "장기 타이트 박스"라는 정의 자체가 흐려진다(0.60은 폭
  60%짜리를 "박스"라 부르는 셈) — 아래 리포트에 그대로 남겨서 해석 시
  주의하게 한다(사용자 지시).

── 안A/안C 정의 ────────────────────────────────────────────────────────
- **안A(즉시진입)**: 신호일(체크포인트 당일) 종가에 즉시 진입, 손절은
  analyze_boxbreak와 동일한 구조적 손절(box_high*0.97 + ATR×0.15 버퍼).
  레이스는 신호일 다음 봉부터.
- **안C(확인진입)**: 2026-09-04_confirm_entry_grid_search_5tabs.py가
  박스돌파에 적용한 정의와 동일 — vol_mult=1.5, margin=0.0(피벗초과),
  확인 윈도우 최대 3봉, trailing50_vol=`hist["Volume"].iloc[-50:].mean()`
  (원 analyze_boxbreak의 vol_mult 게이트에 쓰는 nonzero_vol_mean과는 다른
  단순평균 — 그 스크립트의 안C 정의를 그대로 따름). 손절은 안A와 동일(재
  정의 안 함). **그 스크립트는 다른 세션의 미커밋 산출물이라 import
  의존 대신 로직을 그대로 복사했다** — 그 파일이 나중에 바뀌거나 지워져도
  이 스크립트가 깨지지 않게.
- 오늘 KR 5탭 측정 경향대로 안A는 EV≈0에 가까울 가능성이 높고(예상),
  안C가 실제 채택 판정의 대상이다(사용자 지시) — 안A는 비교/참고용으로만
  같이 낸다.

커밋: 사용자 지시 대기(1단계는 longbox-freq-measure로 커밋됨, 이 스크립트는
아직 커밋 지시 없음).
"""
import os
import sys
import time
import math
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                 "scripts", "measurements"))

import harness  # noqa: E402
import scanner  # noqa: E402
from scanner import BOXBREAK_CONFIG as BCFG  # noqa: E402

CONTROL_WINDOWS = [20, 40, 60]
TARGET_WINDOW = 250
BOX_WINDOWS = CONTROL_WINDOWS + [TARGET_WINDOW]
RANGES = [0.30, 0.45, 0.60]
LOW_DEF = "p5"     # 고정(사유는 위 docstring)
MIN_TOUCHES = 2    # 고정 — analyze_boxbreak 하드코딩값과 동일

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개
TERTILES = [("60-250", 60, 250), ("250-600", 251, 600), ("600-950", 601, 950)]
OLDER_OFFSETS = set(OFFSETS[45:])    # 09-04 스크립트와 동일 정의(off 510~950)
RECENT_OFFSETS = set(OFFSETS[:45])   # off 60~500

# 안C(확인진입) — 2026-09-04_confirm_entry_grid_search_5tabs.py BASELINE=(1.5,0.0) 복사
CONFIRM_VOL_MULT = 1.5
CONFIRM_MARGIN = 0.0
CONFIRM_K_MAX = 3

GAP_MIN_R = 0.15
Z_MIN = 1.96
MIN_N = 100
STOP_ATR_MULT = 0.15


def log(msg):
    print(msg, flush=True)


def tertile_of(offset: int) -> str:
    for name, lo_, hi_ in TERTILES:
        if lo_ <= offset <= hi_:
            return name
    return "?"


def compute_box_unit(h, lo, close, window):
    """box_high/box_low/box_range — LOW_DEF/MIN_TOUCHES 고정, window만 인자.
    1단계 스크립트의 compute_box_unit과 동일 정의(코드는 별도 사본 —
    사유는 위 docstring "안C 정의" 절 참고 원칙과 동일: 다른 파일에
    import 의존 안 함)."""
    if len(h) < window + 2:
        return None
    box_h = h.iloc[-(window + 1):-1]
    box_l = lo.iloc[-(window + 1):-1]
    sig_high = scanner.significant_resistance(h, window, min_touches=MIN_TOUCHES, band=0.02, exclude=1)
    box_high = float(sig_high) if sig_high is not None else float(box_h.max())
    box_low = float(box_l.quantile(0.05)) if LOW_DEF == "p5" else float(box_l.min())
    if box_high <= 0:
        return None
    box_range = (box_high - box_low) / box_high
    return box_high, box_low, box_range


def evaluate_ticker_checkpoint(hist, rs_rank, is_kr):
    """analyze_boxbreak 게이트 복제(공용부 1회 계산) + window×range 조합별
    히트. 반환: [{"window","max_range","box_high","box_low","stop","close",
    "trailing50_vol"}...]"""
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

    _vol_info = scanner.volume_info(close, v)
    _mg = scanner._price_frozen_block(c, h, lo, v)
    if not harness.passes_liquidity_filter({**_vol_info, **_mg}, is_kr):
        return []

    _ls = scanner.late_stage_info(c, lo, h, v, is_kr)
    if _ls["late_level"] == "danger" and scanner.CONFIG.get("late_stage_exclude", True):
        return []

    # 안C 확인 로직에 쓸 50봉 평균거래량 — 2026-09-04 스크립트의 정의
    # 그대로(nonzero_vol_mean이 아니라 단순 평균, 오늘 포함).
    trailing50_vol = float(v.iloc[-50:].mean())

    out = []
    loosest_range = max(RANGES)
    for window in BOX_WINDOWS:
        if len(hist) < window + 2:
            continue
        unit = compute_box_unit(h, lo, close, window)
        if unit is None:
            continue
        box_high, box_low, box_range = unit
        if box_range > loosest_range:
            continue
        if close <= box_high * 1.005:
            continue
        ext = (close - box_high) / box_high
        if ext > BCFG["extended_max"]:
            continue
        stop = round(box_high * 0.97, 2)
        stop, stop_struct, atr_buf = scanner.apply_atr_buffer(stop, h, lo, c, STOP_ATR_MULT)
        rrb = scanner._rr_block(box_high, stop, h, lo, c, base_low=box_low, entry=close,
                                 warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
        if not scanner._risk_hard_ok(rrb, is_kr):
            continue
        for max_range in RANGES:
            if box_range <= max_range:
                out.append({
                    "window": window, "max_range": max_range,
                    "box_high": box_high, "box_low": box_low, "stop": stop,
                    "close": close, "trailing50_vol": trailing50_vol,
                    "box_range_pct": round(box_range * 100, 1), "ext_pct": round(ext * 100, 1),
                })
    return out


def find_confirm(box_high, trailing50_vol, future_df):
    """2026-09-04_confirm_entry_grid_search_5tabs.py find_confirm()과 동일
    정의(복사 — 사유는 상단 docstring)."""
    threshold = box_high * (1 + CONFIRM_MARGIN)
    avail = min(CONFIRM_K_MAX, len(future_df))
    for k in range(1, avail + 1):
        cl = float(future_df["Close"].iloc[k - 1])
        vv = float(future_df["Volume"].iloc[k - 1])
        if cl > threshold and trailing50_vol > 0 and vv >= CONFIRM_VOL_MULT * trailing50_vol:
            return k, cl
    return None


def main():
    t0 = time.time()
    log(f"[registration-note] {datetime.now().isoformat()} — 사전등록 수정 적용됨: "
        f"3분위 전부 z유의 요구 → (a)합산 n>=100&z>=1.96 (b)3분위 전부 EV>0 "
        f"(c)EV(600-950)<=EV(60-250)*2. 상세 사유는 스크립트 상단 docstring.")

    data, kr_u, us_u = harness.fetch_universe_data(
        markets=("kr", "us"), kr_days=1900, us_period="5y", validate_offsets=OFFSETS,
    )
    bench = harness.fetch_kr_benchmarks(days=1900)
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    log(f"[main] 유니버스 fetch 완료 {len(data)}종목(KR {len(kr_u)}/US {len(us_u)}) "
        f"elapsed={time.time()-t0:.0f}s")

    window250_by_range = {r: [] for r in RANGES}
    control_keys_by_range = {r: set() for r in RANGES}

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

        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            rs = rs_ranks.get(t)
            combos = evaluate_ticker_checkpoint(hist, rs, ikr)
            if not combos:
                continue
            for c in combos:
                r = c["max_range"]
                if c["window"] == TARGET_WINDOW:
                    c["ticker"] = t
                    c["is_kr"] = ikr
                    c["offset"] = off
                    window250_by_range[r].append(c)
                else:
                    control_keys_by_range[r].add((t, off))

        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            n250 = {r: len(window250_by_range[r]) for r in RANGES}
            log(f"[cp] offset={off} 완료 ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
                f"250창누적히트={n250}")

    log(f"[main] 체크포인트 완료, elapsed={time.time()-t0:.0f}s")

    for r in RANGES:
        report_range(r, window250_by_range[r], control_keys_by_range[r], data)

    log(f"\n[main] 총 소요시간 {time.time()-t0:.0f}s")


def race_entry_a(hit, data):
    future = harness.future_after(data[hit["ticker"]], hit["offset"])
    return harness.race(hit["close"], hit["stop"], future)


def race_entry_c(hit, data):
    """안C — 확인 안 되면 (None, None) 취급(레이스 자체를 안 함, 09-04
    스크립트의 combo_rows와 동일하게 미확인 건은 표본에서 제외)."""
    future = harness.future_after(data[hit["ticker"]], hit["offset"])
    conf = find_confirm(hit["box_high"], hit["trailing50_vol"], future)
    if conf is None:
        return None
    k, trigger_price = conf
    return harness.race(trigger_price, hit["stop"], future.iloc[k:])


def report_range(max_range, raw_hits, control_keys, data):
    log("\n" + "=" * 78)
    log(f"box_max_range <= {max_range}  (low_def={LOW_DEF}, min_touches={MIN_TOUCHES})")
    log("=" * 78)

    non_overlap = [h for h in raw_hits if (h["ticker"], h["offset"]) not in control_keys]
    n_raw, n_nonoverlap = len(raw_hits), len(non_overlap)
    overlap_pct = round((1 - n_nonoverlap / n_raw) * 100, 1) if n_raw else None
    log(f"  250창 원시 히트 {n_raw}건 → 비중복(20/40/60 미포착) {n_nonoverlap}건 "
        f"(중복률 {overlap_pct}%)")

    if max_range >= 0.6 and n_nonoverlap < 300:
        log("  [주의] range<=0.60까지 풀어도 '박스'라 부르기엔 폭이 넓다 — "
            "장기 타이트 박스라는 원래 정의가 흐려진 상태에서 나온 표본임에 유의.")
    elif max_range == 0.45:
        log("  [주의] range<=0.45는 프로덕션(0.30)보다 느슨한 정의 — 해석 시 감안할 것.")

    if n_nonoverlap < MIN_N:
        log(f"  → 판정불가(n={n_nonoverlap} < {MIN_N}). 억지로 결론 내지 않음. "
            f"(참고용 EV만 아래에 표시)")

    # 안A/안C 레이스
    outcomes_a = [race_entry_a(h, data) for h in non_overlap]
    ev_a = harness.ev_summary(outcomes_a)
    log(f"  [안A 즉시진입, 참고] n={ev_a['n_hits']} nv={ev_a['nv']} "
        f"EV={ev_a['ev_R']}" if ev_a['ev_R'] is not None else f"  [안A 즉시진입, 참고] EV=N/A")

    c_results = [race_entry_c(h, data) for h in non_overlap]
    n_confirmed = sum(1 for r in c_results if r is not None)
    confirm_rate = round(n_confirmed / n_nonoverlap, 3) if n_nonoverlap else None
    outcomes_c = [r for r in c_results if r is not None]
    ev_c_pooled = harness.ev_summary(outcomes_c)
    z_pooled, sig_pooled = harness.one_sample_zscore(ev_c_pooled) if ev_c_pooled["nv"] else (None, False)
    log(f"  [안C 확인진입] 확인율={confirm_rate}({n_confirmed}/{n_nonoverlap}) "
        f"n={ev_c_pooled['n_hits']} nv={ev_c_pooled['nv']} EV={ev_c_pooled['ev_R']} "
        f"z={round(z_pooled,2) if z_pooled is not None else None} sig={sig_pooled}")

    if n_nonoverlap < MIN_N:
        return

    # 3분위(offset tertile) — 안C, EV 부호 + z는 참고
    tertile_outcomes = {name: [] for name, _, _ in TERTILES}
    for h, r in zip(non_overlap, c_results):
        if r is not None:
            tertile_outcomes[tertile_of(h["offset"])].append(r)
    tertile_ev = {}
    for name, _, _ in TERTILES:
        ev_t = harness.ev_summary(tertile_outcomes[name])
        z_t, _ = harness.one_sample_zscore(ev_t) if ev_t["nv"] else (None, False)
        tertile_ev[name] = ev_t
        log(f"    3분위[{name}] n={ev_t['n_hits']} nv={ev_t['nv']} EV={ev_t['ev_R']} "
            f"z(참고)={round(z_t,2) if z_t is not None else None}")

    ev_near = tertile_ev["60-250"]["ev_R"]
    ev_far = tertile_ev["600-950"]["ev_R"]
    cond_all_positive = all(
        tertile_ev[name]["ev_R"] is not None and tertile_ev[name]["ev_R"] > 0
        for name, _, _ in TERTILES
    )
    cond_bias_ceiling = (ev_near is not None and ev_far is not None and ev_near > 0 and ev_far <= ev_near * 2)
    log(f"    (b)3분위 전부 EV>0: {cond_all_positive}")
    if ev_near is not None and ev_far is not None:
        log(f"    (c)EV(600-950)<=EV(60-250)x2: {ev_far}<={round(ev_near*2,4) if ev_near>0 else 'N/A(60-250<=0)'} "
            f"→ {cond_bias_ceiling}")
    else:
        log("    (c)EV(600-950)<=EV(60-250)x2: 계산불가(표본부족)")

    # 시기반분 재현
    older_outcomes = [r for h, r in zip(non_overlap, c_results) if r is not None and h["offset"] in OLDER_OFFSETS]
    recent_outcomes = [r for h, r in zip(non_overlap, c_results) if r is not None and h["offset"] in RECENT_OFFSETS]
    ev_older = harness.ev_summary(older_outcomes)
    ev_recent = harness.ev_summary(recent_outcomes)
    log(f"    시기반분 older(off510-950): n={ev_older['n_hits']} nv={ev_older['nv']} EV={ev_older['ev_R']}")
    log(f"    시기반분 recent(off60-500): n={ev_recent['n_hits']} nv={ev_recent['nv']} EV={ev_recent['ev_R']}")
    half_reproduced = (ev_older["ev_R"] is not None and ev_older["ev_R"] >= GAP_MIN_R and
                        ev_recent["ev_R"] is not None and ev_recent["ev_R"] >= GAP_MIN_R)

    cond_ev = ev_c_pooled["ev_R"] is not None and ev_c_pooled["ev_R"] >= GAP_MIN_R
    cond_z = z_pooled is not None and z_pooled >= Z_MIN
    verdict = (cond_ev and cond_z and cond_all_positive and cond_bias_ceiling and half_reproduced)

    log(f"  → 채택조건: EV>=+{GAP_MIN_R}R({cond_ev}) & z>=+{Z_MIN}({cond_z}) & "
        f"3분위전부양수({cond_all_positive}) & 편향상한({cond_bias_ceiling}) & "
        f"시기반분재현({half_reproduced})")
    log(f"  === 최종 판정(range<={max_range}): {'채택' if verdict else '미달 — 창 추가 안 함'} ===")


if __name__ == "__main__":
    main()

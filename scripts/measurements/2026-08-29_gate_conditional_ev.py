"""
게이트 조건부 EV 측정 (2026-08-29, 사용자 지시) — "게이트 신호등이 나쁜
시기를 실제로 걸러주는가". 측정 스크립트만 — scanner.py/app.py 미수정.
공통 하네스(harness.py) 재사용, RS/2R레이스/체크포인트/저유동성필터 새로
구현 안 함(README 규칙3).

【배경】
docs/pullback_ev_kr_us_regime_investigation.md 6절 — US 눌림목 EV가
이전 절반(off160~250) +0.334R → 최근 절반(off60~150) +0.029R로 약화
확인됨. 이 약화를 시장 게이트(지수 신호등, 🟢/🟡/🔴)가 이미 걸러주고
있었는지 검증한다 — 게이트가 유효하다면 "약화 구간엔 애초에 🔴/🟡이
많이 떠서 진입 자체가 줄었어야 한다"가 성립해야 한다.

【규칙 준수(scripts/measurements/README.md)】
- 규칙6(대조군 유동성매칭): 해당 없음 — 대조군과 비교하는 측정이 아니라
  실제 프로덕션 히트를 게이트 상태별로 나눠 보는 측정.
- 규칙7(z검정): harness.ev_gap_zscore로 🟢 vs 🔴 EV 격차 유의성 확인.
- 규칙8(KR+US 미혼합): US/KR 항상 분리 보고 — 섞은 단일 수치를 안 낸다.

【게이트 판정 — 재구현 금지, 프로덕션 함수 그대로 사용】
scanner.ftd_state()/dist_count()/gate_suggest() — 프로덕션이 실제로 쓰는
바로 그 함수를 그대로 import해서 호출한다(로직 재작성 없음). 그 위에
얹는 두 겹의 "매핑"도 새로 만들지 않고 기존 코드를 1:1 그대로 옮겼다:
  1) index_regime_at() = app.py `_index_regime()`(app.py:5727-5826)의
     "지수 fetch 이후" 부분(ma60/above60 계산 → ftd_state → dist_count →
     gate_suggest → gate_suggest 결과를 regime 문자열로 매핑)을 그대로
     복사 — fetch/ETF 대리거래량 폴백 부분만 제외(아래 참고).
  2) gate_of() = static/index.html의 `gateOf()`(~4888행, 🟢/🟡/🔴 3단계
     매핑: rg==='bad' or dist>=6 → bad, rg==='good' and dist<=3 → good,
     else → neutral)를 JS→Python 1:1 이식 — 조건 순서·임계값 전부 그대로.
  이 레포의 `_trace_*`(app.py) 관례와 동일한 방식: 판정 함수 자체는
  프로덕션 것을 호출하고, "어느 순서로 어떤 함수를 부르는지"만 재현한다.

  유일한 단순화: app.py의 실사용 _index_regime은 라이브 서비스에서 지수
  자체 거래량이 결측/무효일 때 ETF 대리 거래량으로 폴백한다
  (_fetch_proxy_volume). 이 스크립트는 이미 확보한 과거 데이터(나스닥은
  yfinance, 코스피/코스닥은 harness.fetch_kr_benchmarks 경유 네이버)의
  거래량이 충분히 채워져 있어 그 폴백 경로를 재현하지 않는다 —
  dist_count() 내부의 결측 30% 컷(scanner.py:336)이 그대로 안전장치로
  남아있어 거래량이 실제로 부실한 구간은 여전히 days=None(판정불가)으로
  걸러진다.

【측정 대상】
눌림목(analyze()+CONFIG) 단일 탭, US 위주 + KR 병기. checkpoints(60,250,10)
20개 지점 — docs 6절과 동일 스펙(최근 절반=off60~150, 이전 절반=off160~250).
KR 히트는 상장 시장(.KS→코스피, .KQ→코스닥)에 맞는 지수 게이트를 적용.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_gate_conditional_ev.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time

import yfinance as yf

import harness
from scanner import CONFIG, analyze, to_rs_rank, ftd_state, dist_count, gate_suggest

OFFSETS = harness.checkpoints(60, 250, 10)      # 20개 — docs 6절과 동일 스펙
RECENT_HALF = set(range(60, 151, 10))           # 최근 절반(후반부) — docs 6절 정의 그대로
EARLIER_HALF = set(range(160, 251, 10))         # 이전 절반(전반부)
RS_DELTA_LOOKBACK = 20
RS_MIN_BARS = 200

GATE_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴", None: "?"}


def fetch_nasdaq_history():
    """나스닥종합(^IXIC) 2y 일봉. harness.py엔 KR 벤치마크(코스피/코스닥)만
    있고 US 지수 fetch 헬퍼가 없어 이 스크립트에 직접 추가(README 규칙3 —
    다른 이유 명시). app.py _index_regime의 실사용 소스(yf.Ticker(code)
    .history)와 동일 호출, period만 6mo(라이브용)→2y로 늘려 250봉 전
    체크포인트+ftd_state 60봉 창까지 여유있게 커버."""
    df = yf.Ticker("^IXIC").history(period="2y", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError("나스닥 지수 fetch 실패")
    return df


def index_regime_at(close, vol):
    """app.py _index_regime()(app.py:5778-5824)의 계산부 그대로 재현 —
    scanner.ftd_state/dist_count/gate_suggest 실제 함수 호출, fetch/ETF
    대리거래량 폴백만 제외(모듈 docstring 참고). 반환: {'regime','dist_days'}
    또는 데이터 부족 시 None."""
    try:
        if close is None or len(close) < 60:
            return None
        ma60 = close.rolling(60).mean()
        cur = float(close.iloc[-1])
        m60 = float(ma60.iloc[-1])
        above60 = cur > m60
        if vol is not None and len(vol) == len(close):
            fs = ftd_state(close, vol)
        else:
            fs = {"in_correction": False, "rally_day": 0, "ftd": False,
                  "ftd_days_ago": None, "ftd_idx_back": None, "rally_low": None,
                  "peak_before": None, "drawdown_pct": 0.0, "recovered": False}
        dc = dist_count(close, vol, fs) if vol is not None else {"days": None}
        gate_sug, _why = gate_suggest(dc, fs, above60)
        d = dc.get("days")
        if gate_sug == "correction":
            regime = "bad"
        elif gate_sug == "pressure":
            regime = "neutral"
        else:
            regime = "good"
        return {"regime": regime, "dist_days": d}
    except Exception:
        return None


def gate_of(ix):
    """static/index.html gateOf()(~4888행) 1:1 이식 — 조건/임계값 변경 없음."""
    if ix is None:
        return None
    dist = ix.get("dist_days") or 0
    rg = ix.get("regime")
    if rg == "bad" or dist >= 6:
        return "red"
    if rg == "good" and dist <= 3:
        return "green"
    return "yellow"


def precompute_gates(nasdaq_df, kospi_df, kosdaq_df):
    t0 = time.time()
    gate_cache = {}
    for off in OFFSETS:
        nq = harness.truncate_at(nasdaq_df, off)
        kp = harness.truncate_at(kospi_df, off)
        kd = harness.truncate_at(kosdaq_df, off)
        gate_cache[off] = {
            "nasdaq": gate_of(index_regime_at(nq["Close"], nq["Volume"])),
            "kospi": gate_of(index_regime_at(kp["Close"], kp["Volume"])),
            "kosdaq": gate_of(index_regime_at(kd["Close"], kd["Volume"])),
        }
        g = gate_cache[off]
        print(f"[gate-precompute] offset {off}: 나스닥={GATE_EMOJI[g['nasdaq']]} "
              f"코스피={GATE_EMOJI[g['kospi']]} 코스닥={GATE_EMOJI[g['kosdaq']]} "
              f"elapsed={time.time()-t0:.0f}s", flush=True)
    return gate_cache


def rs_3m_ranks(trunc_cache):
    """2026-08-29_kr_us_decomposition_final.py와 동일 정의(3개월 수익률 RS
    백분위) — 눌림목 프로덕션 E게이트(rs_3m)에 필요."""
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


def precompute_rs(data, kospi_close, kosdaq_close):
    t0 = time.time()
    tickers = list(data.keys())
    extra_offsets = sorted(set(OFFSETS) | {o + RS_DELTA_LOOKBACK for o in OFFSETS})
    rs_cache, r3_cache = {}, {}
    for off in extra_offsets:
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
        print(f"[rs-precompute] offset {off} 완료 elapsed={time.time()-t0:.0f}s", flush=True)
    return rs_cache, r3_cache


def collect_pullback_with_gate(data, rs_cache, r3_cache, gate_cache):
    """눌림목 프로덕션 analyze()+CONFIG(E게이트 포함) 히트를 신호일 체크포인트의
    게이트 상태와 함께 수집. 반환: [{'market','off','gate','outcome'}, ...]."""
    t0 = time.time()
    records = []
    for oi, off in enumerate(OFFSETS):
        rs_ranks, rs_moms = rs_cache[off]
        r3_ranks = r3_cache[off]
        rs_20ago, _ = rs_cache.get(off + RS_DELTA_LOOKBACK, ({}, {}))
        gates = gate_cache[off]
        n_before = len(records)
        for t, df in data.items():
            if len(df) - off < CONFIG["min_bars"]:
                continue
            ikr = harness.is_kr_ticker(t)
            hist = harness.truncate_at(df, off)
            rs = rs_ranks.get(t)
            rm = rs_moms.get(t)
            rs3m = r3_ranks.get(t)
            rs_delta = (rs - rs_20ago.get(t)) if (rs is not None and t in rs_20ago) else None
            try:
                hit = analyze(hist, rs_rank=rs, rs_mom=rm, cfg=CONFIG, is_kr=ikr,
                              rs_3m=rs3m, rs_delta=rs_delta)
            except Exception:
                hit = None
            if hit is None or not harness.passes_liquidity_filter(hit, ikr):
                continue
            if ikr:
                gate = gates["kospi"] if t.endswith(".KS") else gates["kosdaq"]
                market = "KR"
            else:
                gate = gates["nasdaq"]
                market = "US"
            future = harness.future_after(df, off)
            outcome = harness.race(hit["close"], hit["stop"], future)
            records.append({"market": market, "off": off, "gate": gate, "outcome": outcome})
        print(f"[pullback+gate] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) "
              f"신규={len(records)-n_before} 누적={len(records)} elapsed={time.time()-t0:.0f}s", flush=True)
    return records


def report_gate_ev(records, market_label):
    print(f"\n  -- {market_label} 게이트 상태별 EV --")
    by_gate = {}
    for g in ("green", "yellow", "red"):
        outcomes = [r["outcome"] for r in records if r["gate"] == g]
        ev = harness.ev_summary(outcomes)
        by_gate[g] = ev
        if ev["ev_R"] is not None:
            print(f"    {GATE_EMOJI[g]} {g}: n={ev['n_hits']} (nv={ev['nv']}) "
                  f"EV={ev['ev_R']:.3f}R 손절률={ev['stop_rate']*100:.1f}% 목표도달률={ev['target_rate']*100:.1f}%")
        else:
            print(f"    {GATE_EMOJI[g]} {g}: n={ev['n_hits']} EV=N/A(표본부족)")
    unknown_n = sum(1 for r in records if r["gate"] is None)
    if unknown_n:
        print(f"    ? 판정불가(거래량 데이터 부족): n={unknown_n}")
    return by_gate


def report_gate_distribution(records, market_label, label):
    total = len(records)
    if total == 0:
        print(f"  {label}: 히트 0건")
        return
    counts = {"green": 0, "yellow": 0, "red": 0, None: 0}
    for r in records:
        counts[r["gate"]] += 1
    print(f"  {label} (n={total}): "
          f"🟢{counts['green']}건({counts['green']/total*100:.0f}%) "
          f"🟡{counts['yellow']}건({counts['yellow']/total*100:.0f}%) "
          f"🔴{counts['red']}건({counts['red']/total*100:.0f}%) "
          f"?{counts[None]}건({counts[None]/total*100:.0f}%)")
    return counts


if __name__ == "__main__":
    _t0 = time.time()
    print("나스닥 지수 fetch 중...", flush=True)
    nasdaq_df = fetch_nasdaq_history()
    print(f"나스닥 {len(nasdaq_df)}봉 확보", flush=True)

    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    kospi_df = bench["kospi"]
    kosdaq_df = bench["kosdaq"]
    kospi_close = kospi_df["Close"].dropna() if kospi_df is not None else None
    kosdaq_close = kosdaq_df["Close"].dropna() if kosdaq_df is not None else None

    gate_cache = precompute_gates(nasdaq_df, kospi_df, kosdaq_df)
    rs_cache, r3_cache = precompute_rs(data, kospi_close, kosdaq_close)

    print("\n" + "=" * 70)
    print("눌림목 히트 + 신호일 게이트 상태 수집")
    print("=" * 70)
    records = collect_pullback_with_gate(data, rs_cache, r3_cache, gate_cache)
    us_records = [r for r in records if r["market"] == "US"]
    kr_records = [r for r in records if r["market"] == "KR"]

    print("\n" + "=" * 70)
    print("【측정 1】 게이트 상태별 EV/승률/n — US 위주, KR 병기")
    print("=" * 70)
    us_by_gate = report_gate_ev(us_records, "US")
    kr_by_gate = report_gate_ev(kr_records, "KR")

    print("\n" + "=" * 70)
    print("【측정 2】 사전 등록 검정 — 🟢 EV vs 🔴 EV (게이트 유효성)")
    print("=" * 70)
    print("  기준: 🟢 EV - 🔴 EV >= +0.15R 그리고 z >= 1.96 → '게이트 유효' 채택")

    def verdict(by_gate, label):
        g, r = by_gate["green"], by_gate["red"]
        if g["ev_R"] is None or r["ev_R"] is None:
            print(f"  {label}: 🟢 또는 🔴 표본 부족 — 검정 불가")
            return None
        gap = g["ev_R"] - r["ev_R"]
        z, sig = harness.ev_gap_zscore(r, g)  # gap = green - red
        z_s = f"{z:.2f}" if z is not None else "N/A"
        print(f"  {label}: 🟢({g['n_hits']}건, {g['ev_R']:.3f}R) vs 🔴({r['n_hits']}건, {r['ev_R']:.3f}R) "
              f"격차={gap:.3f}R z={z_s}")
        adopt = (z is not None) and (gap >= 0.15) and (z >= 1.96)
        print(f"    → {'기준 충족: 게이트 유효' if adopt else '기준 미달: 게이트는 진입 타이밍 필터로 무효(방향성 관찰만)'}")
        return adopt

    us_adopt = verdict(us_by_gate, "US")
    kr_adopt = verdict(kr_by_gate, "KR(병기, 참고)")

    print("\n" + "=" * 70)
    print("【측정 3】 핵심 교차 확인 — 최근 절반(약화 구간)의 게이트 상태 분포")
    print("=" * 70)
    us_recent = [r for r in us_records if r["off"] in RECENT_HALF]
    us_earlier = [r for r in us_records if r["off"] in EARLIER_HALF]
    kr_recent = [r for r in kr_records if r["off"] in RECENT_HALF]
    kr_earlier = [r for r in kr_records if r["off"] in EARLIER_HALF]
    print("  US:")
    report_gate_distribution(us_earlier, "US", "  이전 절반(off160~250, EV+0.334R 구간)")
    report_gate_distribution(us_recent, "US", "  최근 절반(off60~150, EV+0.029R 약화 구간)")
    print("  KR(병기):")
    report_gate_distribution(kr_earlier, "KR", "  이전 절반(off160~250)")
    report_gate_distribution(kr_recent, "KR", "  최근 절반(off60~150)")

    print("\n" + "=" * 70)
    print("【측정 4】 게이트 상태별 히트 수 분포 (전체 기간)")
    print("=" * 70)
    report_gate_distribution(us_records, "US", "US 전체")
    report_gate_distribution(kr_records, "KR", "KR 전체")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

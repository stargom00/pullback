"""
서열별 낙수효과 측정 — "2·3등은 따라가고 4등부터 금지" 실측 검증
(2026-08-29, 사용자 지시). 측정 스크립트만 — scanner.py/app.py/
theme_map.py 미수정. theme_map.json에 저장된 매핑(v5.100,
`theme_map.generate_theme_map()`으로 이미 생성된 실 데이터 — 2차전지/
반도체/조선 3개 테마, 종목당 Claude+web_search 검증, 환각 0건)을
그대로 읽어 쓴다.

【이벤트 정의 — "테마 점화일"(D0)】
테마 매핑 종목들의 합산 거래대금(종가×거래량)이 자기 자신의 과거 60일
롤링 평균 대비 z>=IGNITION_Z(=2.0)로 급증 **AND** 정적 rank=1(Claude가
매긴 대장주)이 당일 +5%↑ 상승한 날. 시장 전체 대비가 아니라 그 테마
바스켓 자신의 시계열 대비 z-스코어로 정의(잉여 시장 데이터 없이
자기완결적, jongga/gap-trade 백테스트의 "20일평균 대비 2배" 조건과
같은 자기참조 방식).

【측정 대상 4갈래】
1. 정적 rank 2~3 중 D0에 미상승(+2% 미만)인 종목 → D0종가매수, D+1/D+2/
   D+3 수익률(비용 0.3% 차감, D+3을 주 지표로).
2. 정적 rank 4+ 중 D0 미상승 종목 → 동일 측정("4등 금지" 가설 검증).
3. 동적 rank(D0 당일 거래대금으로 테마 바스켓 내부 재정렬) 기준으로
   1·2를 다시 계산 — 정적 rank(Claude 서열)와 동적 rank(당일 실제 매기)
   중 어느 쪽 "2~3등"이 더 잘 따라가는지 이원 대조.
4. 대조군: 같은 D0에 거래대금 상위 100(테마 종목 제외, 규칙6 — 동일
   유동성 컷) 전체의 D0종가매수→D+3종가매도 수익률.

시간 반분은 이번엔 안 한다 — 사전 등록 기준 자체에 요구되지 않았고
(사용자 지시 원문에 없음), 테마 3개·약 5년 히스토리로는 이벤트 수가
적어(아래 결과 참고) 반으로 가르면 표본이 더 쪼그라들어 오히려 정보
손실이 크다고 판단(계산 자체는 어렵지 않으나 이번 라운드에서는 보류 —
이벤트가 충분히 쌓이면 후속으로 가능).

【사전 등록 판정 기준】
- 정적(또는 동적) rank2~3 미상승주의 D+1~3 EV(비용차감후) >= +1% AND
  대조군 대비 z>=1.96 → "낙수 유효" 채택 → 테마로테이션 탭 설계 진행.
- rank4+가 유의하게 나쁘면(대조군 또는 rank2~3 대비) → "4등 금지" 확정.

규칙6: 대조군을 거래대금 상위100(유동성 매칭)에서 뽑음. 규칙7: 연속형
% 수익률 2표본 z검정(jongga/gap-trade와 동일 공식, harness.ev_gap_zscore
는 이산 R분포 전용이라 부적합). 규칙8: KR 단일 시장만 다뤄 해당 없음.

실행: 리포 루트에서 (theme_map.json이 이미 있어야 함 — 없으면
python3 -c "..." 로 POST /api/theme_map 상당 작업을 먼저 해야 함)
  python3 scripts/measurements/2026-08-29_theme_trickle_down_backtest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import naver_kr
import theme_map
from universe import get_universe

FETCH_DAYS = 1900            # ≈1274봉 — 이 세션의 다른 KR 백테스트와 동일 스펙
COST = 0.003                 # 왕복 수수료+슬리피지 0.3%
IGNITION_Z = 2.0              # 테마 합산거래대금 급증 판정 z 기준
BASELINE_WINDOW = 60          # z-스코어 롤링 베이스라인(일)
LEADER_MIN_RET = 0.05         # 대장주 D0 상승 조건(+5%)
FOLLOWER_MAX_RET = 0.02       # "미상승" 판정 상한(+2% 미만)
FORWARD_DAYS = 3
CONTROL_TOP_N = 100


def fetch_kr_long_universe(concurrency=10):
    kr_u = get_universe("kr")

    def _fetch_one(ticker):
        try:
            df = naver_kr.fetch_history(ticker, days=FETCH_DAYS)
            if df is None or df.empty:
                return ticker, None
            return ticker, df
        except Exception:
            return ticker, None

    data = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(_fetch_one, t): t for t in kr_u}
        done = 0
        for fut in as_completed(futs):
            t, df = fut.result()
            if df is not None:
                data[t] = df
            done += 1
            if done % 300 == 0:
                print(f"[fetch] {done}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"[fetch] 완료 {len(data)}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    return data


def combined_turnover_series(data, tickers):
    """테마 바스켓 합산 거래대금 시계열(날짜 인덱스 outer join, 결측=0)."""
    series_list = []
    for t in tickers:
        df = data.get(t)
        if df is None or df.empty:
            continue
        s = (df["Close"] * df["Volume"]).rename(t)
        series_list.append(s)
    if not series_list:
        return None
    combined = pd.concat(series_list, axis=1).fillna(0.0)
    return combined.sum(axis=1)


def detect_ignition_days(data, theme_entry):
    """정적 rank1 티커 기준 +5%↑ AND 바스켓 합산거래대금 z>=IGNITION_Z
    인 날짜(Timestamp) 리스트."""
    tickers = [s["ticker"] for s in theme_entry["stocks"]]
    leader = next((s for s in theme_entry["stocks"] if s.get("rank") == 1), None)
    if leader is None or not tickers:
        return []
    leader_df = data.get(leader["ticker"])
    if leader_df is None or leader_df.empty:
        return []

    turnover = combined_turnover_series(data, tickers)
    if turnover is None or len(turnover) < BASELINE_WINDOW + 10:
        return []
    baseline_mean = turnover.shift(1).rolling(BASELINE_WINDOW).mean()
    baseline_std = turnover.shift(1).rolling(BASELINE_WINDOW).std()
    z = (turnover - baseline_mean) / baseline_std

    leader_ret = leader_df["Close"].pct_change()

    ignition_days = []
    common_idx = z.index.intersection(leader_ret.index)
    for d in common_idx:
        zv = z.get(d)
        rv = leader_ret.get(d)
        if zv is None or rv is None or pd.isna(zv) or pd.isna(rv):
            continue
        if zv >= IGNITION_Z and rv >= LEADER_MIN_RET:
            ignition_days.append(d)
    return ignition_days


def _fwd_returns(df, d0):
    """d0(Timestamp) 종가 매수 가정 — D+1/D+2/D+3 종가 대비 누적수익률
    (비용 전). d0가 인덱스에 없거나 D+3 데이터가 없으면 None."""
    if d0 not in df.index:
        return None
    loc = df.index.get_loc(d0)
    if loc + FORWARD_DAYS >= len(df):
        return None
    close0 = float(df["Close"].iloc[loc])
    if close0 <= 0:
        return None
    out = {}
    for k in range(1, FORWARD_DAYS + 1):
        out[f"d{k}"] = float(df["Close"].iloc[loc + k]) / close0 - 1.0
    return out


def _change_pct_on(df, d0):
    if d0 not in df.index:
        return None
    loc = df.index.get_loc(d0)
    if loc < 1:
        return None
    prev = float(df["Close"].iloc[loc - 1])
    cur = float(df["Close"].iloc[loc])
    if prev <= 0:
        return None
    return cur / prev - 1.0


def _turnover_on(df, d0):
    if d0 not in df.index:
        return None
    loc = df.index.get_loc(d0)
    try:
        return float(df["Close"].iloc[loc]) * float(df["Volume"].iloc[loc])
    except Exception:
        return None


def dynamic_ranks_at(data, tickers, d0):
    """d0 당일 거래대금 기준 테마 바스켓 내부 재정렬. 반환:
    {ticker: dynamic_rank(1=최고)}."""
    turnovers = {}
    for t in tickers:
        df = data.get(t)
        if df is None:
            continue
        tv = _turnover_on(df, d0)
        if tv is not None:
            turnovers[t] = tv
    ranked = sorted(turnovers.items(), key=lambda kv: kv[1], reverse=True)
    return {t: i + 1 for i, (t, _) in enumerate(ranked)}


def control_pool_at(data, d0, exclude_tickers):
    """d0 당일 KR 전체 거래대금 상위 CONTROL_TOP_N(테마 종목 제외) —
    규칙6(유동성 매칭 대조군)."""
    turnovers = {}
    for t, df in data.items():
        if t in exclude_tickers:
            continue
        tv = _turnover_on(df, d0)
        if tv is not None:
            turnovers[t] = tv
    ranked = sorted(turnovers.items(), key=lambda kv: kv[1], reverse=True)[:CONTROL_TOP_N]
    return [t for t, _ in ranked]


def stats(values, cost=COST):
    n = len(values)
    if n == 0:
        return {"n": 0}
    vals = sorted(values)
    mean_v = sum(vals) / n
    median_v = vals[n // 2] if n % 2 == 1 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    win_rate = sum(1 for x in vals if x > 0) / n
    return {"n": n, "mean": mean_v, "median": median_v, "win_rate": win_rate, "net_mean": mean_v - cost}


def print_stats(label, values):
    s = stats(values)
    if s["n"] == 0:
        print(f"    {label}: n=0")
        return s
    print(f"    {label}: n={s['n']} 평균={s['mean']*100:+.2f}% 중앙값={s['median']*100:+.2f}% "
          f"승률={s['win_rate']*100:.1f}% 비용차감후={s['net_mean']*100:+.2f}%")
    return s


def mean_zscore(values_a, values_b):
    na, nb = len(values_a), len(values_b)
    if na < 2 or nb < 2:
        return None, False
    ma_ = sum(values_a) / na
    mb_ = sum(values_b) / nb
    var_a = sum((x - ma_) ** 2 for x in values_a) / (na - 1)
    var_b = sum((x - mb_) ** 2 for x in values_b) / (nb - 1)
    se = (var_a / na + var_b / nb) ** 0.5
    if se == 0:
        return None, False
    z = (mb_ - ma_) / se
    return z, abs(z) >= 1.96


if __name__ == "__main__":
    _t0 = time.time()
    all_themes = theme_map.list_all()
    theme_names = [name for name, meta in all_themes.items()]
    print(f"theme_map.json에 등록된 테마: {theme_names}")
    if not theme_names:
        print("theme_map.json에 매핑된 테마가 없습니다 — 먼저 생성 필요. 종료.")
        sys.exit(1)

    data = fetch_kr_long_universe()

    # ── 이벤트 탐지 ──
    events = []   # {"theme":, "d0":, "entry":dict(theme_entry)}
    for name in theme_names:
        entry = theme_map.get(name)
        if not entry or not entry.get("stocks"):
            continue
        days = detect_ignition_days(data, entry)
        print(f"[{name}] 점화일 {len(days)}건: {[d.strftime('%Y-%m-%d') for d in days]}")
        for d in days:
            events.append({"theme": name, "d0": d, "entry": entry})

    print(f"\n총 점화 이벤트 {len(events)}건 (테마 {len(theme_names)}개)")

    static_r23, static_r4plus = [], []
    dynamic_r23, dynamic_r4plus = [], []
    control_returns = []
    event_detail = []

    for ev in events:
        name, d0, entry = ev["theme"], ev["d0"], ev["entry"]
        tickers = [s["ticker"] for s in entry["stocks"]]
        static_rank = {s["ticker"]: s.get("rank") for s in entry["stocks"]}
        dyn_rank = dynamic_ranks_at(data, tickers, d0)

        detail = {"theme": name, "d0": d0.strftime("%Y-%m-%d"), "static": [], "dynamic": []}

        for t in tickers:
            df = data.get(t)
            if df is None:
                continue
            chg = _change_pct_on(df, d0)
            if chg is None or chg >= FOLLOWER_MAX_RET:
                continue   # D0 이미 상승한 종목은 "미상승" 대상 아님
            fwd = _fwd_returns(df, d0)
            if fwd is None:
                continue
            ret3 = fwd["d3"]

            srk = static_rank.get(t)
            if srk in (2, 3):
                static_r23.append(ret3)
                detail["static"].append({"ticker": t, "rank": srk, "d0_chg": chg, "d3": ret3})
            elif srk is not None and srk >= 4:
                static_r4plus.append(ret3)

            drk = dyn_rank.get(t)
            if drk in (2, 3):
                dynamic_r23.append(ret3)
                detail["dynamic"].append({"ticker": t, "rank": drk, "d0_chg": chg, "d3": ret3})
            elif drk is not None and drk >= 4:
                dynamic_r4plus.append(ret3)

        # ── 대조군: 같은 d0의 거래대금 상위100(테마 종목 제외) ──
        pool = control_pool_at(data, d0, set(tickers))
        for t in pool:
            df = data.get(t)
            fwd = _fwd_returns(df, d0)
            if fwd is not None:
                control_returns.append(fwd["d3"])

        event_detail.append(detail)

    print("\n" + "=" * 70)
    print("【이벤트 상세】")
    print("=" * 70)
    for d in event_detail:
        print(f"\n  {d['theme']} — D0={d['d0']}")
        print(f"    정적 rank2-3 미상승주: {d['static']}")
        print(f"    동적 rank2-3 미상승주: {d['dynamic']}")

    print("\n" + "=" * 70)
    print("【측정 1·2】 정적 rank 기준 — rank2-3 vs rank4+ (D0종가매수→D+3종가매도)")
    print("=" * 70)
    s_r23 = print_stats("정적 rank2-3 미상승주", static_r23)
    s_r4 = print_stats("정적 rank4+ 미상승주", static_r4plus)

    print("\n" + "=" * 70)
    print("【측정 3】 동적 rank 기준(D0 당일 거래대금 재정렬) — rank2-3 vs rank4+")
    print("=" * 70)
    d_r23 = print_stats("동적 rank2-3 미상승주", dynamic_r23)
    d_r4 = print_stats("동적 rank4+ 미상승주", dynamic_r4plus)

    print("\n" + "=" * 70)
    print("【측정 4】 대조군 — 같은 D0의 거래대금 상위100(테마 제외)")
    print("=" * 70)
    s_ctrl = print_stats("대조군(top100 거래대금)", control_returns)

    print("\n" + "=" * 70)
    print("【사전 등록 판정】")
    print("=" * 70)

    def verdict(label, group_vals, ctrl_vals):
        s = stats(group_vals)
        if s["n"] == 0 or len(ctrl_vals) < 2:
            print(f"  {label}: 표본 부족 — 판정 불가(n={s.get('n',0)})")
            return
        z, sig = mean_zscore(ctrl_vals, group_vals)
        cond_a = s["net_mean"] >= 0.01
        cond_c = sig and (s["mean"] - (sum(ctrl_vals) / len(ctrl_vals))) > 0
        z_s = f"{z:.2f}" if z is not None else "N/A"
        print(f"  {label}: n={s['n']} 비용차감후={s['net_mean']*100:+.2f}% z(대조군대비)={z_s} "
              f"{'유의' if sig else '유의하지 않음'}")
        print(f"    → (a)+1%↑ {'충족' if cond_a else '미달'}  (c)대조군대비유의 {'충족' if cond_c else '미달'}  "
              f"==> {'채택(낙수 유효)' if (cond_a and cond_c) else '미달'}")

    verdict("정적 rank2-3", static_r23, control_returns)
    verdict("동적 rank2-3", dynamic_r23, control_returns)

    print("\n  -- rank4+ '4등 금지' 확인(대조군·rank2-3 대비 유의하게 나쁜가) --")
    for label, vals, rank23_vals in [("정적 rank4+", static_r4plus, static_r23), ("동적 rank4+", dynamic_r4plus, dynamic_r23)]:
        s4 = stats(vals)
        if s4["n"] < 2:
            print(f"  {label}: 표본 부족(n={s4.get('n',0)})")
            continue
        z_ctrl, sig_ctrl = mean_zscore(vals, control_returns) if len(control_returns) >= 2 else (None, False)
        z_r23, sig_r23 = mean_zscore(vals, rank23_vals) if len(rank23_vals) >= 2 else (None, False)
        z_ctrl_s = f"{z_ctrl:.2f}" if z_ctrl is not None else "N/A"
        z_r23_s = f"{z_r23:.2f}" if z_r23 is not None else "N/A"
        print(f"  {label}: n={s4['n']} 비용차감후={s4['net_mean']*100:+.2f}% "
              f"| 대조군대비 z={z_ctrl_s}({'유의' if sig_ctrl else '유의하지 않음'}) "
              f"| rank2-3대비 z={z_r23_s}({'유의' if sig_r23 else '유의하지 않음'})")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)

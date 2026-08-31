"""
테마 라이프사이클 분석 (v5.121, 사용자 지시) — theme_map.json 기반 테마별
최근 60거래일 재구성(거래대금 점유율·breadth·서열별 수익률·확산 lag·집중도)
과 명시적 임계값 기반 4단계(점화/확산/후기/이탈) 판정. "서사가 아니라
수치 기반 단계 판정"이 목적 — classify_phase()는 항상 판정 근거 수치를
evidence dict로 같이 반환한다(라벨만 던지지 않음).

money_flow.py/theme_map.py와 같은 원칙: app.py에 대한 의존성 없음(app.py →
theme_lifecycle.py 방향으로만 import). 시장 데이터(data: {ticker: df},
Close/Volume 포함)를 인자로 받아 계산만 한다.

money_flow.py의 "테마"(sector_of 기반 광범위 업종, top100 거래대금 종목
한정)와는 다른 개념 — 여기서는 theme_map.json의 종목-리스트형 "관련주
테마"(전체 유니버스에서 그 종목들만 추적, top100 제한 없음)를 쓴다.

KR 전용 — theme_map.py 자체가 "KR 관련주 전용"(money_flow.py의 동일 결정,
1번째 줄 근처 주석 참고)이라 US는 대상 밖.
"""
from __future__ import annotations

import math

import pandas as pd

WINDOW = 60             # 분석/출력 창(거래일)
BASELINE_WINDOW = 20    # z-score 기준선 창(거래일)
NEWHIGH_WINDOW = 20     # N일 신고가 판정 창
D0_Z_THRESHOLD = 2.0
D0_LEADER_RET_PCT = 5.0
DIFFUSION_TARGET_RET_PCT = 5.0
DIFFUSION_BREADTH_MIN_PCT = 50.0
LATE_STAGE_LOOKBACK = 5      # 후기 판정: 대장주 추세둔화 비교용 5일 수익률
CONCENTRATION_TREND_LOOKBACK = 5   # 확산 판정: 집중도 하락 추세 비교 기준일
EXIT_STREAK_DAYS = 3
EXIT_BREADTH_MAX_PCT = 40.0


# ── 원시 시계열 헬퍼 ─────────────────────────────────────────────────
def _daily_turnover_series(df) -> pd.Series:
    if df is None or df.empty or "Close" not in df.columns or "Volume" not in df.columns:
        return pd.Series(dtype=float)
    s = (df["Close"] * df["Volume"]).dropna()
    s.index = pd.to_datetime(s.index).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def _close_series(df) -> pd.Series:
    if df is None or df.empty or "Close" not in df.columns:
        return pd.Series(dtype=float)
    s = df["Close"].dropna()
    s.index = pd.to_datetime(s.index).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def market_daily_turnover(data: dict) -> pd.Series:
    """전체 유니버스(인자로 받은 data의 전 종목) 합산 일별 거래대금."""
    parts = [_daily_turnover_series(df) for df in data.values()]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).sum(axis=1, skipna=True).sort_index()


# ── 1) 테마별 일별 재구성 ────────────────────────────────────────────
def compute_theme_series(stocks: list[dict], data: dict, market_turnover: pd.Series,
                          window: int = WINDOW) -> dict | None:
    """stocks: theme_map.json entry의 stocks 리스트([{ticker,name,rank,reason}]).
    반환: {"tickers": [...], "closes": {ticker: Series}, "rows": [일자별 dict, ...]}
    rows[i]["ranked"] = 그날 거래대금 순으로 정렬된 [(ticker, {close,turnover,
    ret_pct,up,newhigh}), ...] — 대장/2등/3등 서열의 원천."""
    tickers = [s["ticker"] for s in stocks]
    closes, turnovers = {}, {}
    for t in tickers:
        c = _close_series(data.get(t))
        if c.empty:
            continue
        closes[t] = c
        turnovers[t] = _daily_turnover_series(data.get(t))
    if len(closes) < 2 or market_turnover.empty:
        return None

    dates = market_turnover.index[-(window + BASELINE_WINDOW):]
    rows = []
    for d in dates:
        theme_turnover = 0.0
        n_up = n_newhigh = n_counted = 0
        per_stock = {}
        for t, c in closes.items():
            if d not in c.index:
                continue
            tv = turnovers[t].get(d)
            if tv is None or (isinstance(tv, float) and math.isnan(tv)):
                continue
            price = c.loc[d]
            hist = c.loc[c.index <= d].tail(NEWHIGH_WINDOW)
            is_newhigh = len(hist) >= 2 and price >= hist.max()
            prior = c.loc[c.index < d]
            ret = float((price / prior.iloc[-1] - 1) * 100) if len(prior) else None
            is_up = ret is not None and ret > 0
            n_counted += 1
            n_up += int(is_up)
            n_newhigh += int(is_newhigh)
            theme_turnover += float(tv)
            per_stock[t] = {"close": float(price), "turnover": float(tv), "ret_pct": ret,
                             "up": is_up, "newhigh": bool(is_newhigh)}
        mkt_tv = market_turnover.get(d)
        share = (theme_turnover / mkt_tv * 100) if mkt_tv and mkt_tv > 0 else None
        ranked = sorted(per_stock.items(), key=lambda kv: kv[1]["turnover"], reverse=True)
        top3_tv = sum(v["turnover"] for _, v in ranked[:3])
        conc = (top3_tv / theme_turnover * 100) if theme_turnover > 0 else None
        rows.append({"date": d, "turnover_share_pct": share,
                      "breadth_pct": (n_up / n_counted * 100) if n_counted else None,
                      "newhigh_pct": (n_newhigh / n_counted * 100) if n_counted else None,
                      "concentration_top3_pct": conc, "n_counted": n_counted, "ranked": ranked})
    return {"tickers": list(closes.keys()), "closes": closes, "rows": rows}


# ── 2) D0(점화일) 판정 — "점유율 z>=2 & rank1 +5%↑" 첫 날 ────────────
def find_d0(theme_data: dict) -> dict | None:
    rows = theme_data["rows"]
    shares = [r["turnover_share_pct"] for r in rows]
    for i, r in enumerate(rows):
        if i < BASELINE_WINDOW or not r["ranked"]:
            continue
        baseline = [shares[j] for j in range(i - BASELINE_WINDOW, i) if shares[j] is not None]
        if len(baseline) < 5 or r["turnover_share_pct"] is None:
            continue
        mean = sum(baseline) / len(baseline)
        std = math.sqrt(sum((x - mean) ** 2 for x in baseline) / len(baseline))
        if std <= 0:
            continue
        z = (r["turnover_share_pct"] - mean) / std
        if z < D0_Z_THRESHOLD:
            continue
        leader_ticker, leader_info = r["ranked"][0]
        if leader_info["ret_pct"] is not None and leader_info["ret_pct"] >= D0_LEADER_RET_PCT:
            return {"index": i, "date": r["date"], "z": round(z, 2), "leader": leader_ticker,
                    "leader_ret_pct": leader_info["ret_pct"], "turnover_share_pct": r["turnover_share_pct"],
                    "baseline_mean": round(mean, 3), "baseline_std": round(std, 3)}
    return None


def assign_rank_groups(theme_data: dict, d0: dict | None) -> dict | None:
    """D0 당일 거래대금 서열로 rank1/2/3/4+를 고정 — 이후 각 그룹의
    "확산 lag"을 추적하려면 서열이 매일 바뀌면 안 되므로 점화일 기준 고정."""
    if d0 is None:
        return None
    ranked = theme_data["rows"][d0["index"]]["ranked"]
    groups = {"rank1": None, "rank2": None, "rank3": None, "rank4plus": []}
    for i, (t, _) in enumerate(ranked):
        if i == 0:
            groups["rank1"] = t
        elif i == 1:
            groups["rank2"] = t
        elif i == 2:
            groups["rank3"] = t
        else:
            groups["rank4plus"].append(t)
    return groups


def _cum_return_since_d0(theme_data: dict, d0: dict, ticker: str) -> pd.Series | None:
    c = theme_data["closes"].get(ticker)
    if c is None or d0["date"] not in c.index:
        return None
    base = c.loc[d0["date"]]
    sub = c[c.index >= d0["date"]]
    return (sub / base - 1) * 100


def diffusion_lag(theme_data: dict, d0: dict | None, groups: dict | None) -> dict | None:
    """rank2/rank3/rank4+ 각각 D0 대비 며칠 뒤(거래일 수) +5%에 처음
    도달했는지. 도달 못 했으면 None."""
    if d0 is None or groups is None:
        return None
    trading_dates = [r["date"] for r in theme_data["rows"] if r["date"] >= d0["date"]]

    def lag_for(ticker):
        if not ticker:
            return None
        cum = _cum_return_since_d0(theme_data, d0, ticker)
        if cum is None:
            return None
        hit = cum[cum >= DIFFUSION_TARGET_RET_PCT]
        if hit.empty:
            return None
        first_date = hit.index[0]
        try:
            return trading_dates.index(first_date)
        except ValueError:
            return None

    rank4_lags = {t: lag_for(t) for t in groups["rank4plus"]}
    rank4_valid = [v for v in rank4_lags.values() if v is not None]
    return {"rank2": lag_for(groups["rank2"]), "rank3": lag_for(groups["rank3"]),
            "rank4plus_first": min(rank4_valid) if rank4_valid else None,
            "rank4plus_detail": rank4_lags}


# ── 3) 단계 판정 — 4개 규칙, 명시된 임계값 그대로, 근거 수치 항상 동반 ──
def classify_phase(theme_data: dict, d0: dict | None, groups: dict | None,
                    as_of_index: int = -1) -> dict:
    rows = theme_data["rows"]
    i = len(rows) + as_of_index if as_of_index < 0 else as_of_index
    r = rows[i]
    ranked_today = dict(r["ranked"])
    ev = {"date": r["date"], "turnover_share_pct": r["turnover_share_pct"],
          "breadth_pct": r["breadth_pct"], "newhigh_pct": r["newhigh_pct"],
          "concentration_top3_pct": r["concentration_top3_pct"]}

    baseline = [rows[j]["turnover_share_pct"] for j in range(max(0, i - BASELINE_WINDOW), i)
                if rows[j]["turnover_share_pct"] is not None]
    z = None
    if len(baseline) >= 5 and r["turnover_share_pct"] is not None:
        mean = sum(baseline) / len(baseline)
        std = math.sqrt(sum((x - mean) ** 2 for x in baseline) / len(baseline))
        z = round((r["turnover_share_pct"] - mean) / std, 2) if std > 0 else None
    ev["turnover_share_z"] = z

    conc_today = r["concentration_top3_pct"]
    conc_prev = rows[i - CONCENTRATION_TREND_LOOKBACK]["concentration_top3_pct"] \
        if i >= CONCENTRATION_TREND_LOOKBACK else None
    conc_falling = conc_today is not None and conc_prev is not None and conc_today < conc_prev
    ev["concentration_falling_vs_5d_ago"] = conc_falling
    ev["concentration_5d_ago"] = conc_prev

    hist_conc = sorted(x["concentration_top3_pct"] for x in rows[:i + 1]
                        if x["concentration_top3_pct"] is not None)
    conc_median = hist_conc[len(hist_conc) // 2] if hist_conc else None
    conc_high = conc_today is not None and conc_median is not None and conc_today > conc_median
    ev["concentration_high_vs_window_median"] = conc_high
    ev["concentration_window_median"] = conc_median

    leader = groups["rank1"] if groups else (r["ranked"][0][0] if r["ranked"] else None)
    leader_up = ranked_today.get(leader, {}).get("up") if leader else None
    ev["leader"] = leader
    ev["leader_up_today"] = leader_up

    followers = [t for t in ([groups["rank2"], groups["rank3"]] if groups else []) if t]
    any_follower_up = any(ranked_today.get(f, {}).get("up") for f in followers) if followers else None
    ev["any_follower_up_today"] = any_follower_up

    lag = diffusion_lag(theme_data, d0, groups) if (d0 and groups) else None
    rank23_reached = None
    if lag and d0:
        def reached(lag_val):
            return lag_val is not None and (i - d0["index"]) >= lag_val
        rank23_reached = reached(lag.get("rank2")) and reached(lag.get("rank3"))
    ev["rank2_3_diffusion_target_reached"] = rank23_reached

    def newhigh_rate(tickers):
        vals = [ranked_today.get(t, {}).get("newhigh") for t in tickers if t]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals) * 100, 1) if vals else None

    top_group = [t for t in ([groups["rank1"], groups["rank2"], groups["rank3"]] if groups else []) if t]
    bottom_group = groups["rank4plus"] if groups else []
    nh_top = newhigh_rate(top_group)
    nh_bottom = newhigh_rate(bottom_group)
    ev["newhigh_pct_rank1_3"] = nh_top
    ev["newhigh_pct_rank4plus"] = nh_bottom

    def trailing_ret(ticker, n=LATE_STAGE_LOOKBACK):
        c = theme_data["closes"].get(ticker)
        if c is None:
            return None
        sub = c[c.index <= r["date"]]
        return float((sub.iloc[-1] / sub.iloc[-1 - n] - 1) * 100) if len(sub) > n else None

    leader_5d = trailing_ret(leader) if leader else None
    theme_5d_vals = [v for v in (trailing_ret(t) for t in theme_data["tickers"]) if v is not None]
    theme_5d_avg = sum(theme_5d_vals) / len(theme_5d_vals) if theme_5d_vals else None
    ev["leader_5d_ret_pct"] = round(leader_5d, 2) if leader_5d is not None else None
    ev["theme_5d_avg_ret_pct"] = round(theme_5d_avg, 2) if theme_5d_avg is not None else None

    share_win = [x["turnover_share_pct"] for x in rows[max(0, i - EXIT_STREAK_DAYS):i + 1]]
    exit_decline = (len(share_win) == EXIT_STREAK_DAYS + 1 and all(v is not None for v in share_win)
                     and all(share_win[k] < share_win[k - 1] for k in range(1, len(share_win))))
    ev["turnover_share_declining_3d"] = exit_decline

    phase = "미분류"
    if z is not None and z >= D0_Z_THRESHOLD and leader_up and any_follower_up is False and conc_high:
        phase = "점화"
    elif (r["breadth_pct"] is not None and r["breadth_pct"] >= DIFFUSION_BREADTH_MIN_PCT
          and rank23_reached and conc_falling):
        phase = "확산"
    elif (nh_top is not None and nh_bottom is not None and nh_bottom > nh_top
          and leader_5d is not None and theme_5d_avg is not None and leader_5d < theme_5d_avg):
        phase = "후기"
    elif (exit_decline and r["breadth_pct"] is not None and r["breadth_pct"] < EXIT_BREADTH_MAX_PCT):
        phase = "이탈"

    return {"phase": phase, "evidence": ev}


# ── 4) 자금 이동 매트릭스 — 테마 간 점유율 변화(delta) 상관 ────────────
def rotation_matrix(theme_series_map: dict, lookback: int = 20) -> dict:
    """theme_series_map: {테마명: compute_theme_series() 결과}. 반환:
    {테마A: {테마B: 상관계수 or None}}."""
    deltas = {}
    for name, td in theme_series_map.items():
        rows = td["rows"][-(lookback + 1):]
        shares = [r["turnover_share_pct"] for r in rows]
        deltas[name] = [shares[k] - shares[k - 1] if shares[k] is not None and shares[k - 1] is not None else None
                         for k in range(1, len(shares))]
    names = list(deltas.keys())
    matrix = {}
    for a in names:
        matrix[a] = {}
        for b in names:
            pairs = [(x, y) for x, y in zip(deltas[a], deltas[b]) if x is not None and y is not None]
            if len(pairs) < 5:
                matrix[a][b] = None
                continue
            xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            cov = sum((x - mx) * (y - my) for x, y in pairs)
            varx = sum((x - mx) ** 2 for x in xs)
            vary = sum((y - my) ** 2 for y in ys)
            denom = math.sqrt(varx * vary)
            matrix[a][b] = round(cov / denom, 3) if denom > 0 else None
    return matrix


# ── 5) 메인 진입점 ────────────────────────────────────────────────────
def analyze_theme(theme_name: str, stocks: list[dict], data: dict,
                   market_turnover: pd.Series | None = None) -> dict | None:
    """theme_map.get(theme_name)["stocks"]를 stocks로 받아 한 테마를 분석.
    반환 없음(None) = 데이터 부족(구성종목 2개 미만 데이터 확보) — 호출부가
    "표본부족"으로 표시할 것(money_flow.py의 동일 원칙)."""
    if market_turnover is None:
        market_turnover = market_daily_turnover(data)
    theme_data = compute_theme_series(stocks, data, market_turnover)
    if theme_data is None:
        return None
    d0 = find_d0(theme_data)
    groups = assign_rank_groups(theme_data, d0)
    lag = diffusion_lag(theme_data, d0, groups)
    phase = classify_phase(theme_data, d0, groups)
    latest = theme_data["rows"][-1]
    ticker_rank_today = {t: i + 1 for i, (t, _) in enumerate(latest["ranked"])}
    ticker_rank_at_d0 = ({t: i + 1 for i, (t, _) in enumerate(theme_data["rows"][d0["index"]]["ranked"])}
                          if d0 else None)
    return {
        "theme": theme_name,
        "d0": ({"date": str(d0["date"].date()), "z": d0["z"], "leader": d0["leader"],
                "leader_ret_pct": d0["leader_ret_pct"]} if d0 else None),
        "rank_groups": groups,
        "diffusion_lag_trading_days": lag,
        "phase": phase["phase"],
        "phase_evidence": {k: (str(v.date()) if hasattr(v, "date") else v) for k, v in phase["evidence"].items()},
        "latest": {"date": str(latest["date"].date()), "turnover_share_pct": latest["turnover_share_pct"],
                   "breadth_pct": latest["breadth_pct"], "newhigh_pct": latest["newhigh_pct"],
                   "concentration_top3_pct": latest["concentration_top3_pct"]},
        "series": [{"date": str(r["date"].date()), "turnover_share_pct": r["turnover_share_pct"],
                     "breadth_pct": r["breadth_pct"], "newhigh_pct": r["newhigh_pct"],
                     "concentration_top3_pct": r["concentration_top3_pct"]}
                    for r in theme_data["rows"][-WINDOW:]],
        "ticker_rank_today": ticker_rank_today,
        "ticker_rank_at_d0": ticker_rank_at_d0,
        "_theme_data": theme_data,   # 내부용(rotation_matrix 등 다중 테마 계산 재사용) — API 응답 시 제거할 것
    }

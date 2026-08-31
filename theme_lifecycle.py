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
TURNOVER_RATIO_WINDOW = 20   # 회전율(오늘 거래대금 ÷ 자기 트레일링 평균) 기준 창
D0_Z_THRESHOLD = 2.0
D0_LEADER_RET_PCT = 5.0
DIFFUSION_TARGET_RET_PCT = 5.0
DIFFUSION_BREADTH_MIN_PCT = 50.0
LATE_STAGE_LOOKBACK = 5      # 후기 판정: 대장주 추세둔화 비교용 5일 수익률
CONCENTRATION_TREND_LOOKBACK = 5   # 확산 판정: 집중도 하락 추세 비교 기준일
EXIT_STREAK_DAYS = 3
EXIT_BREADTH_MAX_PCT = 40.0

# v5.123(사용자 지시) — D0/리더 판정에 쓰는 서열 방식. 3종(정적/거래대금/
# 회전율) 중 4개 실사이클(제약바이오 2·반도체 2) 백테스트 결과 회전율이
# 3/4로 최고 적중(정적 2/4, 거래대금 2/4) — 특히 제약바이오 cycle1(6/17)
# 에서 거래대금 1위였던 알테오젠이 아니라 회전율 26배로 튄 JW신약이 실제
# 당일 최대상승(+29.9%) 종목이었음을 회전율만 잡아냄. n=4라 확정적이진
# 않음(반도체 cycle2처럼 테마 전체가 동반 급등한 날은 회전율이 오히려
# 저유동 종목을 과대평가해 오판 — 4건 중 유일한 오답 사례). 상세 근거는
# docs/theme_lifecycle_leader_rank_backtest.md.
LEADER_RANK_METHOD = "turnover_ratio"


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
    theme_map.json의 rank는 "시총순"이 아니라 Claude 생성 프롬프트가 요구하는
    "테마 사업 직결도 순위(1=대장주)" — 정적 서열은 이 정의를 그대로 쓴다.

    반환: {"tickers": [...], "closes": {ticker: Series}, "rows": [일자별 dict, ...]}
    rows[i]에 서열 3종을 병기:
      "ranked"              = 거래대금(절대값) 순 [(ticker, {...}), ...]
      "ranked_static"       = theme_map.json의 정적 rank(사업직결도) 순
      "ranked_turnover_ratio" = 오늘 거래대금 ÷ 자기 트레일링 20일 평균
                                거래대금(당일 제외) 배수 순 — "회전율" 서열.
                                평균을 못 구하는 종목(이력 부족)은 맨 뒤로.
    각 방식의 rank1이 D0/리더 판정의 후보 — LEADER_RANK_METHOD가 실제 채택."""
    static_rank_map = {s["ticker"]: s.get("rank") for s in stocks}
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
            ts = turnovers[t]
            avg20 = ts.loc[ts.index < d].tail(TURNOVER_RATIO_WINDOW)
            avg20_val = float(avg20.mean()) if len(avg20) >= 5 else None
            turnover_ratio = (float(tv) / avg20_val) if avg20_val and avg20_val > 0 else None
            per_stock[t] = {"close": float(price), "turnover": float(tv), "ret_pct": ret,
                             "up": is_up, "newhigh": bool(is_newhigh),
                             "static_rank": static_rank_map.get(t),
                             "turnover_ratio": turnover_ratio}
        mkt_tv = market_turnover.get(d)
        share = (theme_turnover / mkt_tv * 100) if mkt_tv and mkt_tv > 0 else None
        ranked = sorted(per_stock.items(), key=lambda kv: kv[1]["turnover"], reverse=True)
        ranked_static = sorted(
            per_stock.items(),
            key=lambda kv: kv[1]["static_rank"] if kv[1]["static_rank"] is not None else 999)
        ranked_turnover_ratio = sorted(
            per_stock.items(),
            key=lambda kv: (kv[1]["turnover_ratio"] is None, -(kv[1]["turnover_ratio"] or 0.0)))
        top3_tv = sum(v["turnover"] for _, v in ranked[:3])
        conc = (top3_tv / theme_turnover * 100) if theme_turnover > 0 else None
        rows.append({"date": d, "turnover_share_pct": share,
                      "breadth_pct": (n_up / n_counted * 100) if n_counted else None,
                      "newhigh_pct": (n_newhigh / n_counted * 100) if n_counted else None,
                      "concentration_top3_pct": conc, "n_counted": n_counted,
                      "ranked": ranked, "ranked_static": ranked_static,
                      "ranked_turnover_ratio": ranked_turnover_ratio})
    return {"tickers": list(closes.keys()), "closes": closes, "rows": rows}


def _leader_ranked(row: dict, method: str = LEADER_RANK_METHOD) -> list:
    """D0/리더 판정에 실제로 쓰는 서열 리스트 선택. method는 '정적/거래대금/
    회전율' 3종 중 하나 — LEADER_RANK_METHOD(모듈 상수, 백테스트로 확정)가
    기본값. 다른 두 방식은 API 응답에 참고용으로 계속 노출되지만 D0/리더
    판정 로직 자체는 이 함수를 거친 것만 쓴다."""
    key = {"static": "ranked_static", "turnover": "ranked",
           "turnover_ratio": "ranked_turnover_ratio"}.get(method, "ranked_turnover_ratio")
    return row.get(key) or []


# ── 2) D0(점화일) 판정 — "점유율 z>=2 & rank1 +5%↑" 단일일 판정 ────────
def _ignition_at(theme_data: dict, i: int) -> dict | None:
    """i번째 날이 점화 조건을 충족하는지 단일 판정(find_d0/find_cycles 공용).
    baseline은 항상 i일 기준 직전 BASELINE_WINDOW 창(과거 데이터만 사용,
    사이클 경계와 무관하게 계산 — 진행 중이던 이전 사이클의 하락 구간이
    baseline에 섞여도 z가 오히려 보수적으로 나오므로 문제 아님)."""
    rows = theme_data["rows"]
    if i < BASELINE_WINDOW:
        return None
    r = rows[i]
    leader_list = _leader_ranked(r)
    if not leader_list or r["turnover_share_pct"] is None:
        return None
    shares = [rr["turnover_share_pct"] for rr in rows]
    baseline = [shares[j] for j in range(i - BASELINE_WINDOW, i) if shares[j] is not None]
    if len(baseline) < 5:
        return None
    mean = sum(baseline) / len(baseline)
    std = math.sqrt(sum((x - mean) ** 2 for x in baseline) / len(baseline))
    if std <= 0:
        return None
    z = (r["turnover_share_pct"] - mean) / std
    if z < D0_Z_THRESHOLD:
        return None
    leader_ticker, leader_info = leader_list[0]
    if leader_info["ret_pct"] is None or leader_info["ret_pct"] < D0_LEADER_RET_PCT:
        return None
    return {"index": i, "date": r["date"], "z": round(z, 2), "leader": leader_ticker,
            "leader_ret_pct": leader_info["ret_pct"], "turnover_share_pct": r["turnover_share_pct"],
            "baseline_mean": round(mean, 3), "baseline_std": round(std, 3)}


def find_d0(theme_data: dict) -> dict | None:
    """하위호환용: 창 전체에서 첫 D0 하나만(단일 사이클 가정). 여러 사이클을
    구분하려면 find_cycles() 사용."""
    for i in range(len(theme_data["rows"])):
        d0 = _ignition_at(theme_data, i)
        if d0:
            return d0
    return None


def find_cycles(theme_data: dict) -> list[dict]:
    """사이클 단위 D0 탐지(v5.122, 사용자 지시) — 이탈(exit) 판정 이후 새
    점화 조건 충족 시 새 D0로 리셋. 이탈 이전에는 새 점화가 있어도 무시
    (같은 사이클이 계속 진행 중인 것으로 취급 — "이탈 후에만 리셋"이라는
    지시 그대로). 서열 그룹(rank1~4+)도 사이클별 자기 D0 기준으로 재확정.

    반환: 시간순 사이클 리스트. 각 사이클 dict:
      {d0, groups, lag, closed(bool), exit_index, exit_date,
       phases: [{index,date,phase,evidence}, ...]}  (phases는 D0일부터
      사이클 종료일 또는 창 끝까지 매일의 classify_phase 결과)
    창 끝까지 이탈이 안 뜨면 마지막 사이클은 closed=False(진행 중)로 반환."""
    rows = theme_data["rows"]
    cycles: list[dict] = []
    active: dict | None = None
    i = 0
    while i < len(rows):
        if active is None:
            d0 = _ignition_at(theme_data, i)
            if d0 is None:
                i += 1
                continue
            groups = assign_rank_groups(theme_data, d0)
            active = {"d0": d0, "groups": groups, "phases": [],
                      "closed": False, "exit_index": None, "exit_date": None}
        cls = classify_phase(theme_data, active["d0"], active["groups"], as_of_index=i)
        active["phases"].append({"index": i, "date": rows[i]["date"], **cls})
        if cls["phase"] == "이탈":
            active["closed"] = True
            active["exit_index"] = i
            active["exit_date"] = rows[i]["date"]
            active["lag"] = diffusion_lag(theme_data, active["d0"], active["groups"])
            cycles.append(active)
            active = None
        i += 1
    if active is not None:
        active["lag"] = diffusion_lag(theme_data, active["d0"], active["groups"])
        cycles.append(active)
    return cycles


def assign_rank_groups(theme_data: dict, d0: dict | None) -> dict | None:
    """D0 당일 LEADER_RANK_METHOD(회전율) 서열로 rank1/2/3/4+를 고정 — 이후
    각 그룹의 "확산 lag"을 추적하려면 서열이 매일 바뀌면 안 되므로 점화일
    기준 고정. v5.123까지는 거래대금 서열이었으나, 4개 실사이클 백테스트
    (docs/theme_lifecycle_leader_rank_backtest.md)에서 회전율이 실제 점화
    종목을 더 잘 맞혀(3/4 vs 거래대금 2/4) 이 서열로 교체."""
    if d0 is None:
        return None
    ranked = _leader_ranked(theme_data["rows"][d0["index"]])
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

    _fallback_ranked = _leader_ranked(r)
    leader = groups["rank1"] if groups else (_fallback_ranked[0][0] if _fallback_ranked else None)
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
    # v5.122(사용자 지시): D0를 사이클 단위로 탐지 — 이탈 이후 새 점화 조건
    # 충족 시 새 D0로 리셋. 상단 d0/rank_groups/diffusion_lag_trading_days/
    # phase 필드는 하위호환을 위해 "가장 최근 사이클" 기준으로 채운다(사이클이
    # 하나뿐이면 기존 v5.121과 동일 동작). 전체 사이클 이력은 "cycles"에 별도.
    cycles = find_cycles(theme_data)
    last_cycle = cycles[-1] if cycles else None
    d0 = last_cycle["d0"] if last_cycle else None
    groups = last_cycle["groups"] if last_cycle else None
    lag = last_cycle.get("lag") if last_cycle else None
    latest_idx = len(theme_data["rows"]) - 1
    phase = classify_phase(theme_data, d0, groups, as_of_index=latest_idx)
    latest = theme_data["rows"][-1]

    def _rank_map(row):
        return {t: i + 1 for i, (t, _) in enumerate(row["ranked"])}

    def _rank_map_static(row):
        return {t: i + 1 for i, (t, _) in enumerate(row["ranked_static"])}

    def _rank_map_ratio(row):
        return {t: i + 1 for i, (t, _) in enumerate(row["ranked_turnover_ratio"])}

    # v5.123: 정적/거래대금/회전율 3종 병기(사용자 지시) — D0/리더 판정
    # 자체는 LEADER_RANK_METHOD(회전율) 하나만 쓰지만, 나머지 2종도 참고용
    # 으로 항상 같이 노출한다("판정만 던지지 말고 근거 병기"와 같은 원칙).
    ticker_rank_today = _rank_map(latest)
    ticker_rank_today_static = _rank_map_static(latest)
    ticker_rank_today_turnover_ratio = _rank_map_ratio(latest)
    d0_row = theme_data["rows"][d0["index"]] if d0 else None
    ticker_rank_at_d0 = _rank_map(d0_row) if d0_row else None
    ticker_rank_at_d0_static = _rank_map_static(d0_row) if d0_row else None
    ticker_rank_at_d0_turnover_ratio = _rank_map_ratio(d0_row) if d0_row else None
    cycles_out = [{
        "d0": {"date": str(c["d0"]["date"].date()), "z": c["d0"]["z"], "leader": c["d0"]["leader"],
               "leader_ret_pct": c["d0"]["leader_ret_pct"]},
        "closed": c["closed"],
        "exit_date": str(c["exit_date"].date()) if c["exit_date"] is not None else None,
        "rank_groups": c["groups"],
        "diffusion_lag_trading_days": c.get("lag"),
    } for c in cycles]
    return {
        "theme": theme_name,
        "d0": ({"date": str(d0["date"].date()), "z": d0["z"], "leader": d0["leader"],
                "leader_ret_pct": d0["leader_ret_pct"]} if d0 else None),
        "rank_groups": groups,
        "diffusion_lag_trading_days": lag,
        "phase": phase["phase"],
        "phase_evidence": {k: (str(v.date()) if hasattr(v, "date") else v) for k, v in phase["evidence"].items()},
        "cycles": cycles_out,   # v5.122: 창 내 전체 사이클 이력(시간순, 마지막이 최신)
        "latest": {"date": str(latest["date"].date()), "turnover_share_pct": latest["turnover_share_pct"],
                   "breadth_pct": latest["breadth_pct"], "newhigh_pct": latest["newhigh_pct"],
                   "concentration_top3_pct": latest["concentration_top3_pct"]},
        "series": [{"date": str(r["date"].date()), "turnover_share_pct": r["turnover_share_pct"],
                     "breadth_pct": r["breadth_pct"], "newhigh_pct": r["newhigh_pct"],
                     "concentration_top3_pct": r["concentration_top3_pct"]}
                    for r in theme_data["rows"][-WINDOW:]],
        "leader_rank_method": LEADER_RANK_METHOD,   # D0/리더 판정에 실제로 쓰인 서열 방식
        "ticker_rank_today": ticker_rank_today,                    # 거래대금 순
        "ticker_rank_today_static": ticker_rank_today_static,      # 정적(테마 사업직결도) 순
        "ticker_rank_today_turnover_ratio": ticker_rank_today_turnover_ratio,  # 회전율 순(판정 기준)
        "ticker_rank_at_d0": ticker_rank_at_d0,
        "ticker_rank_at_d0_static": ticker_rank_at_d0_static,
        "ticker_rank_at_d0_turnover_ratio": ticker_rank_at_d0_turnover_ratio,
        "_theme_data": theme_data,   # 내부용(rotation_matrix 등 다중 테마 계산 재사용) — API 응답 시 제거할 것
    }

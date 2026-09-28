"""분류·추정 매입단가·cluster 밀집도·컨센서스 — 순수 계산만(네트워크 없음).

**모든 산출물은 "관심 신호 · 측정 전"이다** — 이 파일에는 승률·수익률 주장이 없고,
Phase 1 범위에서 백테스트도 하지 않는다(Phase 3).

[공식 상수는 이 파일 상단에 모아둔다 — 사용자 지시]
"""
from __future__ import annotations

from datetime import date

import statistics

# ── 공식 상수 ────────────────────────────────────────────────────────
# 주식 수 변화 판정 허용오차(%). 분할 보정을 하면 주식 수가 float이 되어 정확한
# 같음 비교가 깨진다(예: 1234567 × 4 / 4). 이 값보다 작은 상대변화는 HOLD로 본다.
# **AI 판단 어림값**(웹 검색·백테스트 근거 없음, 재검토 필요) — Dataroma 같은 공개
# 집계는 1주 차이도 ADD로 세므로, 이 값 때문에 분류 일치율이 떨어질 수 있다
# (그래서 대조 검증에서 이 값의 영향을 따로 본다).
CLASSIFY_EPS_PCT = 0.1

# 컨센서스 = Σ(NEW/ADD 여부 × 포트폴리오 비중%). 사용자 지시: "투자자 수만 세지 말 것".
# 두 가지를 같이 낸다 — 최근 분기만(consensus_latest_pct)과 5개 분기 창
# (consensus_5q_pct, 투자자별로 NEW/ADD였던 분기 중 최대 비중 1회만 계산).
CONSENSUS_CLASSES = ("NEW", "ADD")

# cluster 중심 정의 — 중앙값(median). 평균은 한 투자자의 극단 단가에 끌린다.
CLUSTER_CENTER = "median"
# 밀집도 = (max−min)/median. 아래 조건이면 **N/A**(숫자를 만들지 않는다):
#   · 추정단가가 나온 투자자가 2명 미만
#   · 추정단가에 쓰인 **서로 다른 분기 수가 1**(같은 분기 VWAP는 모두 같은 값이라
#     분산이 항상 0 → 밀집도가 "매우 밀집"으로 보이는 가짜 신호가 된다, 사용자 지시)
CLUSTER_MIN_INVESTORS = 2
CLUSTER_MIN_DISTINCT_QUARTERS = 2
# "서로 다른 분기 수"를 **단순 합집합으로 세면 규칙이 새어나간다**(2026-09-28 실측 EQH):
# Harris는 2026-03-31에 28,245주 + 2026-06-30에 9,489,037주를 샀고 Viking은 2026-06-30에만
# 샀다. 합집합은 2분기지만 Harris 추정단가의 99.7%가 Q2 VWAP이라 두 단가가 41.8964 vs
# 41.8965 — 밀집도 0.000이라는 **가짜 정밀도**가 그대로 출력됐다. 그래서 투자자별로
# "증가주식수의 이 비율 이상을 차지하는 분기"를 지배분기로 보고, 지배분기 집합의 크기로
# 판정한다. 90%는 **AI 판단 어림값**(측정 근거 없음, 재검토 필요) — 합집합 개수도
# n_distinct_quarters로 같이 내보내 숨기지 않는다.
DOMINANT_QUARTER_SHARE_PCT = 90.0

SIGNAL_DISCLAIMER = "관심 신호 · 측정 전"


# ── 분류 ─────────────────────────────────────────────────────────────

def classify(prev_shares: float | None, cur_shares: float | None,
             eps_pct: float = CLASSIFY_EPS_PCT) -> str:
    """직전 분기 대비 분류. prev/cur가 None이면 '그 분기 미보고'라 호출부가
    아예 부르지 않는다(가짜 NEW/EXIT 방지 — secdata의 13F-NT 주석 참고).

    반환: NEW(신규) · ADD(증가) · HOLD(유지) · REDUCE(감소) · EXIT(청산)
    """
    prev = float(prev_shares or 0.0)
    cur = float(cur_shares or 0.0)
    if prev <= 0 and cur > 0:
        return "NEW"
    if prev > 0 and cur <= 0:
        return "EXIT"
    if prev <= 0 and cur <= 0:
        return "HOLD"            # 둘 다 없음 — 호출부가 이 행을 만들지 않는다
    change_pct = (cur - prev) / prev * 100
    if abs(change_pct) < eps_pct:
        return "HOLD"
    return "ADD" if change_pct > 0 else "REDUCE"


def classify_history(shares_by_period: dict, periods: list[date],
                     split_factor: "callable | None" = None,
                     eps_pct: float = CLASSIFY_EPS_PCT) -> list[dict]:
    """한 (투자자, 종목)의 분기별 분류 이력.

    shares_by_period: {period: shares or None}. **None = 그 분기 미보고**
    (13F-NT만 냈거나 데이터셋 구간에 없음) → 그 분기가 끼면 전후 비교를 건너뛴다.
    split_factor(period) -> 분기말 이후 누적 분할 배수(함정 6). 넘기지 않으면 1.0 —
    **넘기지 않으면 분할이 있는 종목에서 가짜 ADD가 생긴다**(예: 4:1 분할 후 주식
    수가 4배로 보인다). 호출부는 반드시 넘긴다.

    반환: [{period, shares_adj, prev_period, prev_shares_adj, klass}] (분류 가능한 분기만)
    """
    f = split_factor or (lambda p: 1.0)
    adj = {}
    for p in periods:
        s = shares_by_period.get(p)
        adj[p] = None if s is None else float(s) * float(f(p))
    out = []
    for i, p in enumerate(periods):
        if adj[p] is None:
            continue
        prev_p = None
        for q in reversed(periods[:i]):          # 가장 가까운 "보고된" 이전 분기
            if adj[q] is not None:
                prev_p = q
                break
        if prev_p is None:
            continue                             # 비교 기준이 없으면 분류하지 않는다
        if (adj[prev_p] or 0) <= 0 and (adj[p] or 0) <= 0:
            continue
        out.append({"period": p, "shares_adj": adj[p], "prev_period": prev_p,
                    "prev_shares_adj": adj[prev_p],
                    "klass": classify(adj[prev_p], adj[p], eps_pct)})
    return out


# ── 추정 매입단가 ────────────────────────────────────────────────────

def estimate_investor_cost(increases: list[dict]) -> dict | None:
    """투자자별 추정 매입단가 = Σ(증가주식수 × 분기VWAP근사) / Σ(증가주식수).
    **증가분만** 쓴다(감소 분기는 매입이 아니다).

    increases: [{period, delta_shares, vwap_approx, low, high}] — delta_shares > 0만.
    반환 None: 증가분이 없거나 VWAP를 못 구한 경우(가격 데이터 없음 등).
    반환의 `dominant_quarter`: 증가주식수의 DOMINANT_QUARTER_SHARE_PCT 이상을 한 분기가
    차지하면 그 분기(= 사실상 그 분기 VWAP), 아니면 None(여러 분기 혼합).
    """
    usable = [r for r in increases
              if (r.get("delta_shares") or 0) > 0 and r.get("vwap_approx")]
    if not usable:
        return None
    tot = sum(r["delta_shares"] for r in usable)
    cost = sum(r["delta_shares"] * r["vwap_approx"] for r in usable) / tot
    lows = [r["low"] for r in usable if r.get("low")]
    highs = [r["high"] for r in usable if r.get("high")]
    shares_by_q = {}
    for r in usable:
        shares_by_q[r["period"]] = shares_by_q.get(r["period"], 0.0) + r["delta_shares"]
    top_q, top_share = max(shares_by_q.items(), key=lambda kv: kv[1])
    dominant = top_q if (top_share / tot * 100) >= DOMINANT_QUARTER_SHARE_PCT else None
    return {"est_cost": round(cost, 4), "shares_added": tot,
            "quarters": sorted(shares_by_q),
            "quarter_share_pct": {q: round(v / tot * 100, 2) for q, v in shares_by_q.items()},
            "dominant_quarter": dominant,
            "band_low": round(min(lows), 4) if lows else None,
            "band_high": round(max(highs), 4) if highs else None}


# ── cluster 밀집도 ───────────────────────────────────────────────────

def cluster_metrics(per_investor: dict) -> dict:
    """per_investor: {investor_key: estimate_investor_cost() 반환값}.

    반환: {cluster_center, cluster_dispersion(None 가능), n_investors_with_cost,
           n_distinct_quarters(합집합), n_effective_quarters(지배분기 기준),
           dispersion_na_reason}
    밀집도가 None이면 CSV/화면은 'N/A'로 표시해야 한다 — 0으로 쓰면 "완벽히 밀집"으로
    읽히는데 실제로는 **계산 불가**다(같은 분기 VWAP는 동일값, 사용자 지시).
    판정은 합집합이 아니라 **지배분기 집합**으로 한다(DOMINANT_QUARTER_SHARE_PCT 주석의
    EQH 사례 — 합집합으로 세면 무의미한 소액 분기 하나가 규칙을 무력화한다).
    """
    entries = {k: v for k, v in per_investor.items() if v and v.get("est_cost")}
    costs = [v["est_cost"] for v in entries.values()]
    quarters = sorted({q for v in entries.values() for q in (v.get("quarters") or [])})
    signatures = set()
    for k, v in entries.items():
        dom = v.get("dominant_quarter")
        # 지배분기가 없으면(여러 분기 혼합) 그 투자자만의 서명 — 같은 분기 조합이면
        # 보수적으로 같은 서명으로 묶는다(가짜 정밀도보다 N/A가 낫다)
        signatures.add(dom if dom is not None else ("mixed", tuple(v.get("quarters") or [])))
    n_inv, n_q, n_eff = len(costs), len(quarters), len(signatures)
    out = {"n_investors_with_cost": n_inv, "n_distinct_quarters": n_q,
           "n_effective_quarters": n_eff,
           "cluster_center": None, "cluster_dispersion": None, "dispersion_na_reason": None}
    if not costs:
        out["dispersion_na_reason"] = "추정단가 0명"
        return out
    center = statistics.median(costs)
    out["cluster_center"] = round(center, 4)
    if n_inv < CLUSTER_MIN_INVESTORS:
        out["dispersion_na_reason"] = f"투자자 {n_inv}명(<{CLUSTER_MIN_INVESTORS})"
        return out
    if n_eff < CLUSTER_MIN_DISTINCT_QUARTERS:
        out["dispersion_na_reason"] = (f"사실상 같은 분기(지배분기 1개, 합집합 {n_q}분기) "
                                      "— 같은 분기 VWAP는 동일값")
        return out
    if center <= 0:
        out["dispersion_na_reason"] = "중심값 <= 0"
        return out
    out["cluster_dispersion"] = round((max(costs) - min(costs)) / center, 4)
    return out


def band_position(price: float | None, low: float | None, high: float | None) -> str | None:
    """현재가가 추정 매수구간의 아래/안/위 — 괴리율(VWAP 기준)과 **병기**하려고
    따로 낸다(사용자 지시)."""
    if price is None or low is None or high is None:
        return None
    if price < low:
        return "below"
    if price > high:
        return "above"
    return "in"


def gap_pct(price: float | None, center: float | None) -> float | None:
    """현재가 괴리율(%) = 현재가 / cluster 중심 − 1. 중심은 median(CLUSTER_CENTER)."""
    if price is None or not center:
        return None
    return round((price / center - 1) * 100, 2)


def consensus_pct(classes_by_investor: dict, weights_by_investor: dict,
                  latest_period: date) -> dict:
    """컨센서스 — 투자자 수만 세지 않고 **포트폴리오 비중 합**으로 센다.

    classes_by_investor: {investor: {period: klass}}
    weights_by_investor: {investor: {period: weight_pct}}
    """
    latest_sum, latest_n, win_sum, win_n = 0.0, 0, 0.0, 0
    for inv, by_p in classes_by_investor.items():
        w = weights_by_investor.get(inv) or {}
        if by_p.get(latest_period) in CONSENSUS_CLASSES:
            latest_sum += float(w.get(latest_period) or 0.0)
            latest_n += 1
        hits = [w.get(p) or 0.0 for p, k in by_p.items() if k in CONSENSUS_CLASSES]
        if hits:
            win_sum += float(max(hits))          # 투자자별 1회만(중복 가산 방지)
            win_n += 1
    return {"consensus_latest_pct": round(latest_sum, 4), "n_new_add_latest": latest_n,
            "consensus_5q_pct": round(win_sum, 4), "n_new_add_5q": win_n}

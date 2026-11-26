"""거래대금 상위 순위 기록(v5.403) — 순수 함수. 표시·알림 전용 · 측정 전.

사용자 2026-11-26: 트레이더 율 채널 글("장 마감 후에는 0186 거래대금 상위 … 특히 거래대금 상위 1~10위 종목이 그 순위를 계속
유지하는지 매일 기록하면서 순위의 변동이 생기는지 한번씩 보면 큰 도움이 됩니다 … 치고 올라오는 애들이 있거든요")를 공부하고,
Claude가 "상위 10위의 순위 유지·새 진입을 기록하는 화면이나 봇 알림으로 붙일 수 있다"고 하자 "응 붙여줘".

데이터: naver marketValue 목록의 accumulatedTradingValue(naver_kr.fetch_market_rows). 키움 0186과 값이 조금 다를 수 있다(집계 기준이
같은지 확인 안 함 — 애프터마켓 포함 여부 등). ETF·ETN은 뺀다(돈이 들어간 **종목·섹터**를 보려는 용도).
숫자(상위 20 저장·10 표시·90일 보관)는 AI(Claude) 어림값.
"""
from __future__ import annotations

from collections import Counter

KEEP_TOP = 20      # 하루에 저장하는 순위 — 10위 밖으로 밀린 종목의 순위도 보이게
SHOW_TOP = 10      # 사용자 글의 "1~10위"
KEEP_DAYS = 90


def snapshot(rows: list, n: int = KEEP_TOP) -> tuple[str | None, list]:
    """fetch_market_rows 결과 → (기준 날짜, 상위 n [{rank, code, ticker, name, value_mil, close, chg_pct}]).
    기준 날짜 = 종목들 시세 날짜 중 가장 많은 것. 그 날짜가 아닌 줄(거래정지 등 옛 시세)과 ETF·ETN은 뺀다."""
    stocks = [r for r in rows if r.get("is_stock")]
    if not stocks:
        return None, []
    day = Counter(r["date"] for r in stocks).most_common(1)[0][0]
    top = sorted((r for r in stocks if r["date"] == day), key=lambda r: -r["value_mil"])[:n]
    return day, [{"rank": i + 1, **{k: r[k] for k in ("code", "ticker", "name", "value_mil", "close", "chg_pct")}}
                 for i, r in enumerate(top)]


def trim(days: dict, keep: int = KEEP_DAYS) -> dict:
    return {d: days[d] for d in sorted(days)[-keep:]}


def analyze(days: dict, day: str | None = None, show: int = SHOW_TOP) -> dict | None:
    """저장된 날짜별 순위 → 기준일(없으면 마지막 날)의 상위 show와 변화.
    top[i]: prev_rank(전 기록일 순위, 상위 KEEP_TOP 밖이면 None) · move(+ = 올라옴) · new(전 기록일엔 상위 show 밖) ·
            streak(기준일까지 연속으로 상위 show에 든 기록일 수) · in_top_20d(최근 20 기록일 중 상위 show에 든 횟수).
    dropped: 전 기록일 상위 show였는데 오늘 빠진 종목(오늘 순위, 상위 KEEP_TOP 밖이면 None)."""
    if not days:
        return None
    order = sorted(days)
    day = day if day in days else order[-1]
    i = order.index(day)
    prev = order[i - 1] if i else None
    rank_of = lambda d, code: next((r["rank"] for r in days[d] if r["code"] == code), None)
    in_top = lambda d, code: (rank_of(d, code) or 10 ** 9) <= show
    top = []
    for r in days[day][:show]:
        pr = rank_of(prev, r["code"]) if prev else None
        streak = 0
        for d in reversed(order[:i + 1]):
            if not in_top(d, r["code"]):
                break
            streak += 1
        recent = order[max(0, i - 19):i + 1]
        top.append({**r, "prev_rank": pr, "move": (pr - r["rank"]) if pr else None,
                    "new": bool(prev) and not in_top(prev, r["code"]), "streak": streak,
                    "in_top_20d": sum(in_top(d, r["code"]) for d in recent)})
    dropped = []
    if prev:
        for r in days[prev][:show]:
            if not in_top(day, r["code"]):
                dropped.append({"code": r["code"], "ticker": r["ticker"], "name": r["name"], "prev_rank": r["rank"],
                                "rank": rank_of(day, r["code"])})
    return {"day": day, "prev_day": prev, "top": top, "dropped": dropped, "n_days": len(order),
            "first_day": order[0]}

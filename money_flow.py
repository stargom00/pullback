"""
돈의 흐름 데일리 리포트 — 1단계 계산 모듈 (2026-08-26, 사용자 지시).
"투자하는범" 3단계 방법론(거래대금→가격→테마)의 1단계(거래대금 순위·
테마 집계)를 자동화. **진입 신호가 아니라 정보 레이어** — 여기서 나온
어떤 필드도 매수/매도 판단에 직접 쓰지 않는다(scanner.py의 게이트·
필터와 완전히 분리된 별도 모듈).

scanner.py는 참조하지 않는다(눌림목 등 매매 신호 로직과 무관) — app.py
가 이미 갖고 있는 시장 데이터(`_fetch_market_data`의 {ticker: df})와
섹터 판정 함수(`_sector_of`)를 인자로 받아 계산만 한다. 이 모듈은
app.py에 대한 의존성이 없다(app.py → money_flow 방향으로만 import).

【핵심 판단 2가지 — 설계 근거】
1. **테마 지속성(streak)**: "거래대금 상위에 등장" = 그 테마가 당일
   테마별 거래대금 점유율 기준 상위 THEME_STREAK_RANK_THRESHOLD(=10)위
   이내에 든 날. 모든 테마를 "등장"으로 치면(거의 항상 최소 1종목은
   top100에 있어 사실상 전부 매일 등장) streak가 무의미해지므로,
   "눈에 띄게 강한 테마"만 카운트되도록 순위 컷을 둠 — 단발 등장(순위
   컷 밖으로 밀려나면 streak 리셋)과 연속 등장을 구분하는 게 목적.
2. **확산 단계**: 테마 내 top100 편입 종목을 거래대금 순으로 대장주/
   2등주/3등주로 서열화하고, 등락 여부로 4단계 규칙 분류:
   - 초기: 대장주만 상승, 2·3등주는 상승 안 함
   - 확산(본격): 대장주 상승 + 2·3등주 중 1개 이상도 상승
   - 말기 경계: 대장주는 상승 안 함, 2·3등주만 상승(후발주 추격)
   - 비확산: 전부 상승 안 함
   테마 내 top100 편입 종목이 2개 미만이면 판단 보류("표본부족").
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

SURGE_PCT = 10.0          # ±10% 급등락 표시 기준
MICRO_CAP_EOK = 1000      # 초소형주 기준(시총 1000억원 미만) — app.py의
                           # _MCAP_MIN_EOK(스캔 제외 하한)과 동일 값 재사용,
                           # "너무 작아 주의가 필요한 종목"이라는 취지가 같음.
THEME_STREAK_RANK_THRESHOLD = 10  # 테마 거래대금 점유율 상위 N위 이내만 "등장"
TOP_N = 100


def _resolve_money_flow_dir() -> str:
    """일자별 JSON/리포트 저장 디렉터리. app.py의 _resolve_persistent_path와
    동일한 우선순위(JOURNAL_DIR 환경변수 → /data → 앱 폴더)를 이 모듈
    안에서 독립적으로 재현 — app.py를 import하지 않기 위함(의존 방향
    유지: app.py → money_flow.py만 허용)."""
    candidates = []
    env_dir = os.environ.get("JOURNAL_DIR")
    if env_dir:
        candidates.append(os.path.join(env_dir, "money_flow"))
    candidates.append("/data/money_flow")
    candidates.append(os.path.join(os.path.dirname(__file__), "money_flow_data"))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.remove(test)
            return d
        except OSError:
            continue
    return os.path.join(os.path.dirname(__file__), "money_flow_data")


def snapshot_path(market: str, daykey: str) -> str:
    d = os.path.join(_resolve_money_flow_dir(), market)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{daykey}.json")


def report_path(market: str, daykey: str) -> str:
    d = os.path.join(_resolve_money_flow_dir(), market)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{daykey}.md")


def load_snapshot(market: str, daykey: str) -> dict | None:
    p = snapshot_path(market, daykey)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_report_markdown(market: str, daykey: str, markdown: str):
    p = report_path(market, daykey)
    with open(p, "w", encoding="utf-8") as f:
        f.write(markdown)


def load_report_markdown(market: str, daykey: str) -> str | None:
    p = report_path(market, daykey)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def list_available_dates(market: str) -> list[str]:
    d = os.path.join(_resolve_money_flow_dir(), market)
    if not os.path.isdir(d):
        return []
    dates = [f[:-5] for f in os.listdir(d) if f.endswith(".json")]
    return sorted(dates, reverse=True)


def _previous_daykey(market: str, before: str) -> str | None:
    """`before`보다 이전 날짜 중 가장 최근에 저장된 스냅샷의 날짜키."""
    dates = [d for d in list_available_dates(market) if d < before]
    return dates[0] if dates else None


# ── 1) 종목별 원시 지표 ─────────────────────────────────────────────
def _ticker_metrics(ticker: str, df) -> dict | None:
    if df is None or len(df) < 2:
        return None
    close = df["Close"]
    volume = df["Volume"]
    if close.dropna().empty or volume.dropna().empty:
        return None
    c = float(close.iloc[-1])
    prev_c = float(close.iloc[-2])
    v = float(volume.iloc[-1])
    if c <= 0 or prev_c <= 0:
        return None
    change_pct = (c / prev_c - 1) * 100
    turnover = c * v
    return {"ticker": ticker, "close": c, "change_pct": round(change_pct, 2),
            "turnover": turnover, "volume": v}


# ── 2) 시총 근사(KR만 — investor_flow.py 재사용, US는 한계 명시) ──────
def _attach_kr_mcap(top_rows: list[dict]):
    """KR 종목만 investor_flow로 시총 근사 부착. 실패해도 리포트 전체를
    막지 않게 개별 실패는 None으로 남김(fail-open)."""
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "measurements"))
        import investor_flow as ivf
    except Exception:
        for r in top_rows:
            r["mcap_approx"] = None
            r["micro_cap"] = None
        return
    from concurrent.futures import ThreadPoolExecutor, as_completed
    tickers = [r["ticker"] for r in top_rows]

    def _one(t):
        try:
            df = ivf.fetch_investor_flow(t, min_days=1, max_pages=1)
            if df is None or df.empty:
                return t, None
            return t, ivf.market_cap_approx(df.iloc[-1])
        except Exception:
            return t, None

    results = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_one, t): t for t in tickers}
        for fut in as_completed(futs):
            t, mcap = fut.result()
            results[t] = mcap
    for r in top_rows:
        mcap = results.get(r["ticker"])
        r["mcap_approx"] = mcap
        r["micro_cap"] = bool(mcap < MICRO_CAP_EOK * 1e8) if mcap is not None else None


def _mark_us_mcap_unavailable(top_rows: list[dict]):
    for r in top_rows:
        r["mcap_approx"] = None
        r["micro_cap"] = None


# ── 3) 테마 내 서열판 + 확산 단계 ────────────────────────────────────
def _diffusion_stage(theme_rows: list[dict]) -> dict:
    ranked = sorted(theme_rows, key=lambda r: r["turnover"], reverse=True)
    if len(ranked) < 2:
        return {"leaderboard": [r["ticker"] for r in ranked], "stage": "표본부족"}
    leader, second = ranked[0], ranked[1]
    third = ranked[2] if len(ranked) >= 3 else None
    leader_up = leader["change_pct"] > 0
    second_up = second["change_pct"] > 0
    third_up = (third is not None and third["change_pct"] > 0)
    followers_up = second_up or third_up
    if leader_up and not followers_up:
        stage = "초기(대장주만)"
    elif leader_up and followers_up:
        stage = "확산(본격)"
    elif not leader_up and followers_up:
        stage = "말기 경계(후발주만)"
    else:
        stage = "비확산"
    leaderboard = [{"ticker": r["ticker"], "rank": i + 1, "change_pct": r["change_pct"],
                     "turnover": r["turnover"]} for i, r in enumerate(ranked[:5])]
    return {"leaderboard": leaderboard, "stage": stage,
            "leader_up": leader_up, "followers_up": followers_up}


# ── 4) 테마별 집계 ───────────────────────────────────────────────────
def _aggregate_themes(top_rows: list[dict]) -> dict:
    by_theme: dict[str, list[dict]] = {}
    for r in top_rows:
        by_theme.setdefault(r["theme"], []).append(r)
    total_turnover = sum(r["turnover"] for r in top_rows) or 1.0

    themes = {}
    for theme, rows in by_theme.items():
        n = len(rows)
        up_n = sum(1 for r in rows if r["change_pct"] > 0)
        turnover_sum = sum(r["turnover"] for r in rows)
        themes[theme] = {
            "n": n,
            "breadth_pct": round(up_n / n * 100, 1) if n else None,
            "avg_change_pct": round(sum(r["change_pct"] for r in rows) / n, 2) if n else None,
            "turnover_sum": turnover_sum,
            "turnover_share_pct": round(turnover_sum / total_turnover * 100, 2),
            **_diffusion_stage(rows),
        }
    return themes


# ── 5) 지속성(streak) + 전일 대비 점유율 변화 — 전일 스냅샷과 대조 ────
def _attach_streak(themes: dict, prev_snapshot: dict | None) -> dict:
    ranked_today = sorted(themes.items(), key=lambda kv: kv[1]["turnover_share_pct"], reverse=True)
    today_top_set = {name for name, _ in ranked_today[:THEME_STREAK_RANK_THRESHOLD]}
    prev_streaks = {}
    prev_shares = {}
    if prev_snapshot:
        for name, info in (prev_snapshot.get("themes") or {}).items():
            prev_streaks[name] = info.get("streak_days", 0)
            prev_shares[name] = info.get("turnover_share_pct")
    for name, info in themes.items():
        in_top_today = name in today_top_set
        info["in_streak_rank"] = in_top_today
        if in_top_today:
            info["streak_days"] = prev_streaks.get(name, 0) + 1
        else:
            info["streak_days"] = 0
        prev_share = prev_shares.get(name)
        info["turnover_share_change_pct"] = (
            round(info["turnover_share_pct"] - prev_share, 2) if prev_share is not None else None
        )
        info["is_new_theme"] = name not in prev_shares if prev_snapshot else None
    return themes


# ── 메인 진입점 ──────────────────────────────────────────────────────
def compute_snapshot(market: str, data: dict, universe: dict, sector_of,
                      prev_snapshot: dict | None, methodology_note: str = "") -> dict:
    """market: 'kr'|'us'. data: {ticker: OHLCV df}(app.py `_fetch_market_data`
    반환분 재사용, 새 fetch 없음). universe: {ticker: name}. sector_of:
    app.py `_sector_of` 콜러블(의존 역전 방지 — 인자로 주입)."""
    rows = []
    for t in data.keys():
        m = _ticker_metrics(t, data[t])
        if m is None:
            continue
        m["name"] = universe.get(t, t)
        m["theme"] = sector_of(t) or "미분류"
        if m["theme"] == "기타":
            m["theme"] = "미분류"
        rows.append(m)

    rows.sort(key=lambda r: r["turnover"], reverse=True)
    top_rows = rows[:TOP_N]
    for i, r in enumerate(top_rows):
        r["rank"] = i + 1
        r["surge"] = r["change_pct"] >= SURGE_PCT
        r["plunge"] = r["change_pct"] <= -SURGE_PCT

    prev_ranks = {}
    if prev_snapshot:
        for r in (prev_snapshot.get("top") or []):
            prev_ranks[r["ticker"]] = r["rank"]
    for r in top_rows:
        prev_rank = prev_ranks.get(r["ticker"])
        r["prev_rank"] = prev_rank
        r["rank_change"] = (prev_rank - r["rank"]) if prev_rank is not None else None
        r["is_new_entrant"] = prev_rank is None

    if market == "kr":
        _attach_kr_mcap(top_rows)
    else:
        _mark_us_mcap_unavailable(top_rows)

    themes = _aggregate_themes(top_rows)
    prev_themes_snapshot = prev_snapshot if prev_snapshot else None
    themes = _attach_streak(themes, prev_themes_snapshot)

    return {
        "market": market,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
        "methodology_note": methodology_note,
        "top": top_rows,
        "themes": themes,
        "surge_tickers": [r["ticker"] for r in top_rows if r["surge"]],
        "plunge_tickers": [r["ticker"] for r in top_rows if r["plunge"]],
    }


def save_snapshot(market: str, daykey: str, snapshot: dict):
    p = snapshot_path(market, daykey)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def run_daily(market: str, daykey: str, data: dict, universe: dict, sector_of,
              methodology_note: str = "") -> dict:
    """스케줄러/수동재실행 공통 진입점: 전일 스냅샷 로드 → 오늘 계산 →
    저장 → 반환."""
    prev_daykey = _previous_daykey(market, daykey)
    prev_snapshot = load_snapshot(market, prev_daykey) if prev_daykey else None
    snap = compute_snapshot(market, data, universe, sector_of, prev_snapshot, methodology_note)
    save_snapshot(market, daykey, snap)
    return snap

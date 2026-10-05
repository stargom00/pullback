"""저점 관찰(v5.325) — 주·월봉 저점 히트를 히트 종가 대비 +5% 도달까지 추적하는 순수 계산.

사용자 지시 요지: "주·월봉 저점 히트가 수십 개라 한번에 진입 불가. 히트 시점 종가를 기준가로 기록하고 이후 +5% 도달
여부를 자동 추적 — 도달한 종목은 활성 목록에서 빠지고, 미도달 종목만 진입 후보로 남는 관찰 페이지."

레코드(서버 /data/lowpoint_watch.json, 저장 규칙은 app.py — 저점 매매 기록과 같은 레코드 단위 rev):
  id          w_{tf}_{label}_{code} — **기준일(코호트)별 한 건**: 같은 종목이 다음 스캔에 또 나오면 별도 레코드
  tf          week | month
  label       스캔 기준봉 라벨(주봉 금요일·월봉 말일 — 저점 게시 파일의 bar_date) — 화면 그룹 키
  base_date   그 종목의 실제 마지막 거래일(행 bar_date) — 이 날 **다음 거래일부터** 고가를 본다
  base_price  기준가 = 스캔 기준일 종가(행 close0 — 저점 스크린이 판정에 쓴 바로 그 값)
  code·name·market(KOSPI|KOSDAQ|US)·mkt(KR|US)
  status      active | reached;  reached_date·reached_days(기준일 → 도달일 달력 일수)
  last_close·last_date·checked_at — 마지막 추적 결과(활성 종목만 갱신)

판정: 기준일 **이후** 일봉 고가 ≥ 기준가 × (1 + REACH_PCT/100) → 도달(정확히 1.05배도 도달). 기준일 당일·이전 고가는
보지 않는다. 가격 소스는 저점 스크린과 같다 — KR naver 통합 시세(naver_kr.fetch_history), US yfinance 배당 미조정
(lowpoint.US_AUTO_ADJUST) — 기준가와 고가가 같은 기준이어야 비교가 맞는다.
조회는 **관찰 중(active) 종목만**(수십 건) — 유니버스 전수 조회 없음.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

import lowpoint as lp
import lowpoint_eval as ev

REACH_PCT = ev.SHORT_GOAL_PCT   # +5% — 평가 페이지 단기 목표와 같은 값(새 임계값 아님)
TFS = ("week", "month")


def watch_id(tf: str, label: str, code: str) -> str:
    return f"w_{tf}_{label}_{code}"


def records_from_entry(entry: dict, tf: str) -> list:
    """저점 게시 한 칸(publish_entry 결과) → 관찰 레코드(신규 상태). 기준가 = 행 close0."""
    if not isinstance(entry, dict) or not entry.get("bar_date"):
        return []
    label = entry["bar_date"]
    out = []
    for r in entry.get("rows") or []:
        code, price = r.get("code"), r.get("close0")
        if not code or price is None or not r.get("bar_date"):
            continue
        out.append({"id": watch_id(tf, label, code), "tf": tf, "label": label, "base_date": r["bar_date"],
                    "base_price": float(price), "code": code, "name": r.get("name") or code,
                    "market": r.get("market"), "mkt": "US" if r.get("market") == "US" else "KR",
                    "status": "active", "reached_date": None, "reached_days": None,
                    "last_close": None, "last_date": None, "checked_at": None})
    return out


def reach_threshold(base_price: float, pct: float = REACH_PCT) -> float:
    return base_price * (100 + pct) / 100


def reach_info(base_price: float, base_date: str, daily: "pd.DataFrame | None", pct: float = REACH_PCT) -> dict:
    """일봉(High·Close, 날짜 인덱스) → {reached, reached_date, reached_days, last_close, last_date}.
    base_date 다음 날부터만 본다. 고가 비교는 부동소수 오차만 허용(정확히 1.05배 = 도달)."""
    out = {"reached": False, "reached_date": None, "reached_days": None, "last_close": None, "last_date": None}
    if daily is None or daily.empty:
        return out
    d = daily.dropna(subset=["Close"]).copy()
    d.index = pd.to_datetime(d.index).normalize()
    out["last_close"] = float(d["Close"].iloc[-1])
    out["last_date"] = str(d.index[-1].date())
    after = d[d.index > pd.Timestamp(base_date)]
    thr = reach_threshold(base_price, pct)
    hi = after["High"] if "High" in after.columns else after["Close"]
    hit = after[hi >= thr * (1 - 1e-12)]
    if len(hit):
        rd = hit.index[0].date()
        out.update(reached=True, reached_date=str(rd), reached_days=(rd - date.fromisoformat(base_date)).days)
    return out


def _us_period(earliest: str, today: date) -> str:
    """기준일을 덮는 가장 짧은 yfinance period(기준일 이후 일봉만 필요)."""
    days = (today - date.fromisoformat(earliest)).days
    for p, n in (("1mo", 28), ("3mo", 88), ("6mo", 180), ("1y", 360), ("2y", 725)):
        if days <= n:
            return p
    return "5y"


def fetch_daily(records: list, today: date) -> dict:
    """**이 레코드들의 종목만** 일봉 조회 → {code: DataFrame}. KR naver(통합 시세), US yfinance 배당 미조정."""
    import harness
    import naver_kr
    if not records:
        return {}
    earliest = min(r["base_date"] for r in records)
    kr = sorted({r["code"] for r in records if r.get("mkt") != "US"})
    us = sorted({r["code"] for r in records if r.get("mkt") == "US"})
    out = {}
    days = (today - date.fromisoformat(earliest)).days + 10   # 기준일 앞 여유(주말·휴장)
    for code in kr:
        try:
            df = naver_kr.fetch_history(code, days=days)
        except Exception:
            df = None
        if df is not None and not df.empty:
            out[code] = df
    if us:
        out.update(harness._fetch_us_batch(us, period=_us_period(earliest, today), auto_adjust=lp.US_AUTO_ADJUST))
    return out


def track(records: list, today: date, now_iso: str, fetch=fetch_daily) -> tuple[dict, dict]:
    """활성 레코드만 추적 → ({id: 바뀔 필드}, 집계). 도달한 레코드는 다시 조회하지 않는다."""
    active = [r for r in records if r.get("status") == "active"]
    data = fetch(active, today) if active else {}
    updates, failed, reached = {}, [], 0
    for r in active:
        info = reach_info(float(r["base_price"]), r["base_date"], data.get(r["code"]))
        if info["last_close"] is None:
            failed.append(r["code"])
            continue
        u = {"last_close": info["last_close"], "last_date": info["last_date"], "checked_at": now_iso}
        if info["reached"]:
            u.update(status="reached", reached_date=info["reached_date"], reached_days=info["reached_days"])
            reached += 1
        updates[r["id"]] = u
    return updates, {"active": len(active), "fetched": len(data), "reached_new": reached,
                     "failed": sorted(set(failed))}


# ── v5.327(사용자 지시) 보유 추적 — "매매 기록의 보유 종목 수익률을 매일 자동 추적 — 관찰(진입 전)과 짝이 되는
# 보유 중 페이지". 대상 = 매매 기록 중 종료 안 된(sellDate 없는) 기록. 관찰과 같은 작업(매일 07:00 KST + 수동 갱신)이
# 이어서 부른다. 저장하는 것은 종목별 마지막 종가뿐 — 수익률·목표가는 화면이 매매 기록(평단·목표%)으로 계산한다.
HOLD_LOOKBACK_DAYS = 10      # 마지막 종가만 필요 — 주말·연휴를 넘길 만큼(AI 판단 어림값, 결과값엔 영향 없음)


def fetch_last_closes(items: list, today: date) -> dict:
    """**이 종목들만** 마지막 종가 → {code: {last_close, last_date}}. KR naver(통합), US yfinance 배당 미조정(저점
    기준과 같음), 코인 업비트 일봉."""
    import harness
    import naver_kr
    import upbit
    out = {}

    def put(code, df):
        if df is None or df.empty or "Close" not in df:
            return
        c = df["Close"].dropna()
        if len(c):
            out[code] = {"last_close": float(c.iloc[-1]), "last_date": str(pd.to_datetime(c.index[-1]).date())}
    kr = sorted({i["code"] for i in items if i.get("mkt") == "KR"})
    us = sorted({i["code"] for i in items if i.get("mkt") == "US"})
    coin = sorted({i["code"] for i in items if i.get("mkt") == "UPBIT"})
    for code in kr:
        try:
            put(code, naver_kr.fetch_history(code, days=HOLD_LOOKBACK_DAYS))
        except Exception:
            pass
    if us:
        for code, df in harness._fetch_us_batch(us, period="1mo", auto_adjust=lp.US_AUTO_ADJUST).items():
            put(code, df)
    for code in coin:
        put(code, upbit.fetch_candles(code, "days", count=HOLD_LOOKBACK_DAYS))
    return out


def track_holdings(trades: list, today: date, now_iso: str, prev: dict | None = None,
                   fetch=fetch_last_closes) -> tuple[dict, dict]:
    """보유 기록(sellDate 없음)의 종목만 조회 → ({code: {mkt, last_close, last_date, checked_at}}, 집계).
    조회 실패한 종목은 이전 값을 그대로 둔다(날짜가 남아 낡은 값임을 화면이 보여준다). 더 이상 보유하지 않는 종목은 뺀다."""
    held = {}
    for t in trades or []:
        if t.get("sellDate") or not t.get("code"):
            continue
        held[t["code"]] = t.get("mkt")
    got = fetch([{"code": c, "mkt": m} for c, m in held.items()], today) if held else {}
    prev = prev or {}
    out, failed = {}, []
    for code, mkt in held.items():
        if code in got:
            out[code] = {"mkt": mkt, **got[code], "checked_at": now_iso}
        else:
            failed.append(code)
            if code in prev:
                out[code] = prev[code]
    return out, {"held": len(held), "fetched": len(got), "failed": sorted(failed)}

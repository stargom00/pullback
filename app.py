"""
눌림목 스캐너 v3 — 웹 서버
모드: pullback(눌림목) / turnaround(추세전환)
RS 모멘텀: 3개월 수익률 백분위 - 12개월 수익률 백분위 (시장별)
실행: uvicorn app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from scanner import analyze, analyze_turnaround, analyze_leader, analyze_super, analyze_breakout, analyze_surge, rs_raw_score, to_rs_rank
from sectors import get_sector
from universe import get_universe, load_alerts

app = FastAPI(title="눌림목 스캐너")

VERSION = "v4.2"
CACHE_TTL = 600
_cache: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=12)


def _fetch(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def _ret_pct(close, days):
    c = close.dropna()
    if len(c) < days + 1:
        return None
    past = float(c.iloc[-days - 1])
    return float(c.iloc[-1]) / past - 1 if past > 0 else None


async def run_scan(market: str, mode: str) -> dict:
    universe = get_universe(market)
    loop = asyncio.get_event_loop()

    tickers = list(universe.keys())
    dfs = await asyncio.gather(
        *[loop.run_in_executor(_executor, _fetch, t) for t in tickers]
    )
    data = {t: df for t, df in zip(tickers, dfs) if df is not None}

    # RS 등급 + RS 모멘텀 (시장별 백분위)
    kr, us = {}, {}
    kr3, us3, kr12, us12 = {}, {}, {}, {}
    for t, df in data.items():
        bucket_rs = kr if t.endswith((".KS", ".KQ")) else us
        bucket3 = kr3 if t.endswith((".KS", ".KQ")) else us3
        bucket12 = kr12 if t.endswith((".KS", ".KQ")) else us12
        bucket_rs[t] = rs_raw_score(df["Close"])
        bucket3[t] = _ret_pct(df["Close"], 63)
        bucket12[t] = _ret_pct(df["Close"], 252)
    rs_ranks = {**to_rs_rank(kr), **to_rs_rank(us)}
    rank3 = {**to_rs_rank(kr3), **to_rs_rank(us3)}
    rank12 = {**to_rs_rank(kr12), **to_rs_rank(us12)}
    rs_moms = {
        t: rank3[t] - rank12[t]
        for t in data if t in rank3 and t in rank12
    }

    fn = {"turnaround": analyze_turnaround, "leader": analyze_leader, "super": analyze_super, "breakout": analyze_breakout, "surge": analyze_surge}.get(mode, analyze)
    alerts = load_alerts()
    hits = []
    for t, df in data.items():
        result = fn(df, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t))
        if result is None:
            continue
        mkt = "KR" if t.endswith((".KS", ".KQ")) else "US"
        alert_kind = alerts.get(t.upper())
        hits.append({"ticker": t, "name": universe[t], "market": mkt,
                     "sector": get_sector(t), "alert": alert_kind, **result})

    hits.sort(key=lambda x: (x.get("triggered", False), x.get("setup_score") or x["score"]), reverse=True)

    # 섹터 요약: 2개 이상 잡힌 섹터를 개수 내림차순 (기타 제외)
    from collections import Counter
    sec_count = Counter(h["sector"] for h in hits if h["sector"] != "기타")
    sector_summary = [
        {"sector": s, "count": n} for s, n in sec_count.most_common() if n >= 2
    ]

    warn_count = sum(1 for h in hits if h.get("alert") or h.get("risk_warn"))

    return {
        "version": VERSION,
        "market": market,
        "mode": mode,
        "scanned": len(universe),
        "fetched": len(data),
        "hits": hits,
        "sector_summary": sector_summary,
        "warn_count": warn_count,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }


@app.get("/api/scan")
async def scan(market: str = "all", mode: str = "pullback", refresh: bool = False):
    market = market if market in ("kr", "us", "all") else "all"
    mode = mode if mode in ("pullback", "turnaround", "leader", "super", "breakout", "surge") else "pullback"
    key = f"{market}:{mode}"
    cached = _cache.get(key)
    if cached and not refresh and time.time() - cached["ts"] < CACHE_TTL:
        return JSONResponse({**cached, "cached": True})
    result = await run_scan(market, mode)
    _cache[key] = result
    return JSONResponse({**result, "cached": False})


ALERTS_USER_PATH = os.path.join(os.path.dirname(__file__), "alerts_user.txt")


@app.get("/api/alerts")
async def get_alerts():
    return JSONResponse(load_alerts())


@app.post("/api/alerts")
async def add_alert(request: Request):
    """대시보드에서 경보 종목 추가/삭제. {ticker, kind} 또는 {ticker, remove:true}"""
    body = await request.json()
    ticker = (body.get("ticker") or "").upper().strip()
    if not ticker:
        return JSONResponse({"ok": False, "error": "ticker 필요"}, status_code=400)
    kind = body.get("kind", "경보")
    remove = body.get("remove", False)

    # alerts_user.txt 읽기 → 수정 → 쓰기
    entries = {}
    if os.path.exists(ALERTS_USER_PATH):
        with open(ALERTS_USER_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=1)
                entries[parts[0].upper()] = parts[1] if len(parts) > 1 else "경보"
    if remove:
        entries.pop(ticker, None)
    else:
        entries[ticker] = kind
    with open(ALERTS_USER_PATH, "w", encoding="utf-8") as f:
        f.write("# 대시보드에서 추가한 경보 종목 (자동 생성)\n")
        for tk, kd in sorted(entries.items()):
            f.write(f"{tk} {kd}\n")
    # 캐시 무효화 (다음 스캔에 반영)
    _cache.clear()
    return JSONResponse({"ok": True, "alerts": entries})


@app.get("/")
async def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")

"""
눌림목 스캐너 v3 — 웹 서버
모드: pullback(눌림목) / turnaround(추세전환)
RS 모멘텀: 3개월 수익률 백분위 - 12개월 수익률 백분위 (시장별)
실행: uvicorn app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from scanner import analyze, analyze_turnaround, rs_raw_score, to_rs_rank
from universe import get_universe

app = FastAPI(title="눌림목 스캐너")

VERSION = "v3.0"
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

    fn = analyze_turnaround if mode == "turnaround" else analyze
    hits = []
    for t, df in data.items():
        result = fn(df, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t))
        if result is None:
            continue
        mkt = "KR" if t.endswith((".KS", ".KQ")) else "US"
        hits.append({"ticker": t, "name": universe[t], "market": mkt, **result})

    hits.sort(key=lambda x: x["score"], reverse=True)
    return {
        "version": VERSION,
        "market": market,
        "mode": mode,
        "scanned": len(universe),
        "fetched": len(data),
        "hits": hits,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }


@app.get("/api/scan")
async def scan(market: str = "all", mode: str = "pullback", refresh: bool = False):
    market = market if market in ("kr", "us", "all") else "all"
    mode = mode if mode in ("pullback", "turnaround") else "pullback"
    key = f"{market}:{mode}"
    cached = _cache.get(key)
    if cached and not refresh and time.time() - cached["ts"] < CACHE_TTL:
        return JSONResponse({**cached, "cached": True})
    result = await run_scan(market, mode)
    _cache[key] = result
    return JSONResponse({**result, "cached": False})


@app.get("/")
async def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")

"""
눌림목 스캐너 — 웹 서버
실행: uvicorn app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from scanner import analyze
from universe import get_universe

app = FastAPI(title="눌림목 스캐너")

CACHE_TTL = 600  # 10분
_cache: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=8)


def _fetch_and_analyze(ticker: str, name: str):
    try:
        df = yf.Ticker(ticker).history(period="8mo", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        result = analyze(df)
        if result is None:
            return None
        market = "KR" if ticker.endswith((".KS", ".KQ")) else "US"
        return {"ticker": ticker, "name": name, "market": market, **result}
    except Exception:
        return None


async def run_scan(market: str) -> dict:
    universe = get_universe(market)
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(_executor, _fetch_and_analyze, t, n)
        for t, n in universe.items()
    ]
    results = await asyncio.gather(*tasks)
    hits = sorted(
        [r for r in results if r], key=lambda x: x["score"], reverse=True
    )
    return {
        "market": market,
        "scanned": len(universe),
        "hits": hits,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }


@app.get("/api/scan")
async def scan(market: str = "all", refresh: bool = False):
    market = market if market in ("kr", "us", "all") else "all"
    cached = _cache.get(market)
    if cached and not refresh and time.time() - cached["ts"] < CACHE_TTL:
        return JSONResponse({**cached, "cached": True})
    result = await run_scan(market)
    _cache[market] = result
    return JSONResponse({**result, "cached": False})


@app.get("/")
async def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")

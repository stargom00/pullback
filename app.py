"""
눌림목 스캐너 v2 — 웹 서버
1단계: 유니버스 전체 일봉(1년치) 수집 → RS 원점수 계산
2단계: 백분위 RS 등급(1~99) 산출 → 눌림목 분석에 전달
실행: uvicorn app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from scanner import analyze, rs_raw_score, to_rs_rank
from universe import get_universe

app = FastAPI(title="눌림목 스캐너")

CACHE_TTL = 600  # 10분
_cache: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=12)


def _fetch(ticker: str):
    """1년치 일봉 수집 (RS·200일선 계산용)"""
    try:
        df = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


async def run_scan(market: str) -> dict:
    universe = get_universe(market)
    loop = asyncio.get_event_loop()

    # ── 1단계: 전체 데이터 수집 ──
    tickers = list(universe.keys())
    dfs = await asyncio.gather(
        *[loop.run_in_executor(_executor, _fetch, t) for t in tickers]
    )
    data = {t: df for t, df in zip(tickers, dfs) if df is not None}

    # ── 2단계: RS 등급 (유니버스 내 백분위 1~99) ──
    # KR/US를 섞어 스캔할 때도 각 시장 안에서만 순위를 매겨 공정하게 비교
    kr_scores, us_scores = {}, {}
    for t, df in data.items():
        s = rs_raw_score(df["Close"])
        (kr_scores if t.endswith((".KS", ".KQ")) else us_scores)[t] = s
    rs_ranks = {**to_rs_rank(kr_scores), **to_rs_rank(us_scores)}

    # ── 3단계: 눌림목 분석 ──
    hits = []
    for t, df in data.items():
        result = analyze(df, rs_rank=rs_ranks.get(t))
        if result is None:
            continue
        mkt = "KR" if t.endswith((".KS", ".KQ")) else "US"
        hits.append({"ticker": t, "name": universe[t], "market": mkt, **result})

    hits.sort(key=lambda x: x["score"], reverse=True)
    return {
        "market": market,
        "scanned": len(universe),
        "fetched": len(data),
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

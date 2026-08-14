"""
공통 측정 하네스 (v5.68) — scanner.py/app.py를 직접 참조하는 측정 스크립트들이
각자 조금씩 다르게 구현하다 기준선이 갈리는 문제(docs/pullback_stop_width_and_entry_timing.md
"기준선 불일치 조사" 참고)를 막기 위한 공용 모듈.

이 파일이 고정하는 것 4가지 — 전부 실제 서비스 경로(app.py run_scan)와
독립적으로 대조 검증됨(RS는 3512종목 전원 랭크 일치, US 벤치마크 생략
가정은 ^IXIC로 2036종목 전원 일치 확인, v5.67):
  1. 유니버스 데이터 fetch (naver_kr/yfinance, app.py `_fetch`/`_fetch_us_batch`와 동일)
  2. RS/RS모멘텀 계산 (app.py `_fetch_market_data_inner`와 동일 알고리즘,
     체크포인트 시점 기준 재계산)
  3. 프로덕션 사후 필터 재현 (`run_scan()`의 저유동성 하드 필터 — analyze()
     안에는 없고 app.py에만 있어서 analyze()를 직접 부르는 측정 스크립트가
     빠뜨리기 쉬움, v5.67에서 실제로 빠뜨렸던 것)
  4. 2R 레이스 구현 (진입/손절/목표/미결·데이터부족 처리)

측정 스크립트는 이 모듈의 함수를 가져다 쓰고, 새로 구현하지 않는다.
새 측정에서 이 스펙과 다르게 재야 할 이유가 있으면(예: 다른 R배수, 다른
체크포인트 간격) 그 자체를 스크립트 안에 "왜 다른지" 주석으로 남길 것 —
말없이 갈라지는 게 이번 사고의 원인이었다.

의존: 이 파일은 /Users/seulkicho/pullback을 sys.path에 넣고 실행하는 걸
전제로 scanner.py/app.py/naver_kr.py/universe.py를 import한다(리포 루트에서
실행하거나, 스크립트 쪽에서 sys.path.insert 해줄 것).
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from scanner import rs_raw_score, to_rs_rank

# ── 1) 유니버스 데이터 fetch ──────────────────────────────────────────
# app.py의 _fetch/_fetch_us_batch와 동일 호출(같은 naver_kr.fetch,
# 같은 yf.download 파라미터) — 프로덕션이 실제로 받는 것과 같은 데이터.


def _downcast(df):
    try:
        if "Close" in df.columns:
            df = df[df["Close"].notna()]
    except Exception:
        pass
    return df


def _fetch_kr_one(ticker):
    import naver_kr
    try:
        df = naver_kr.fetch(ticker)
        if df is None or df.empty:
            return ticker, None
        return ticker, _downcast(df)
    except Exception:
        return ticker, None


def _fetch_us_batch(tickers):
    import yfinance as yf
    out = {}
    if not tickers:
        return out
    try:
        raw = yf.download(tickers, period="2y", interval="1d",
                           auto_adjust=True, group_by="ticker",
                           threads=True, progress=False)
    except Exception:
        return out
    if raw is None or len(raw) == 0:
        return out
    single = len(tickers) == 1
    for t in tickers:
        try:
            df = raw.copy() if single else raw[t].copy()
            df = df.dropna(how="all")
            if df is None or df.empty or "Close" not in df.columns:
                continue
            if df["Close"].dropna().empty:
                continue
            out[t] = _downcast(df)
        except Exception:
            continue
    return out


def fetch_universe_data(markets=("kr", "us"), kr_concurrency=10, us_batch_size=100,
                         progress=True):
    """{ticker: df} 전체 유니버스 페치. app.py의 실제 fetch 함수와 동일 소스
    (naver_kr.fetch / yf.download 2y). 반환: (data, kr_universe, us_universe)."""
    from universe import get_universe
    t0 = time.time()
    kr_u = get_universe("kr") if "kr" in markets else {}
    us_u = get_universe("us") if "us" in markets else {}
    data = {}
    if kr_u:
        with ThreadPoolExecutor(max_workers=kr_concurrency) as ex:
            futs = {ex.submit(_fetch_kr_one, t): t for t in kr_u}
            done = 0
            for fut in as_completed(futs):
                t, df = fut.result()
                if df is not None:
                    data[t] = df
                done += 1
                if progress and done % 300 == 0:
                    print(f"[harness] kr fetched {done}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    if us_u:
        us_tickers = list(us_u.keys())
        batches = [us_tickers[i:i + us_batch_size] for i in range(0, len(us_tickers), us_batch_size)]
        for i, b in enumerate(batches):
            data.update(_fetch_us_batch(b))
            if progress:
                print(f"[harness] us batch {i+1}/{len(batches)} elapsed={time.time()-t0:.0f}s", flush=True)
    if progress:
        print(f"[harness] fetched {len(data)} tickers total, elapsed={time.time()-t0:.0f}s", flush=True)
    return data, kr_u, us_u


def fetch_kr_benchmarks(days=900):
    """코스피/코스닥 지수 히스토리. 체크포인트 truncate용으로 넉넉히(900일)."""
    import naver_kr
    return {
        "kospi": naver_kr.fetch_index_history("KOSPI", days=days),
        "kosdaq": naver_kr.fetch_index_history("KOSDAQ", days=days),
    }


# ── 2) RS / RS모멘텀 (체크포인트 시점 기준 재계산) ────────────────────
# app.py `_fetch_market_data_inner`(2921~2939행)와 동일 알고리즘. 미국은
# 단일 벤치마크라 전원에게 같은 상수를 빼는 것과 같아 랭킹 불변 — 이 가정은
# 실제 ^IXIC로 검증됨(2036종목 전원 랭크 일치, docs 참고). 따라서 US는
# 벤치마크 차감을 생략(코드/속도 절약, 결과 동일).


def is_kr_ticker(t: str) -> bool:
    return t.endswith((".KS", ".KQ"))


def ret_pct(close: pd.Series, days: int):
    c = close.dropna()
    if len(c) < days + 1:
        return None
    past = float(c.iloc[-days - 1])
    return float(c.iloc[-1]) / past - 1 if past > 0 else None


def bench_score_at(bench_close: pd.Series, off: int) -> float:
    """벤치마크(코스피/코스닥) 지수의 off봉 전 시점 rs_raw_score. None이면 0.0
    (전종목에 동일하게 적용되는 값이라 0.0 폴백이어도 랭킹엔 영향 없음)."""
    if bench_close is None or len(bench_close) == 0:
        return 0.0
    n = len(bench_close)
    trunc = bench_close.iloc[: n - off] if off > 0 and n - off > 0 else bench_close
    s = rs_raw_score(trunc)
    return s if s is not None else 0.0


def compute_rs_at_checkpoint(trunc_cache: dict, b_kospi: float, b_kosdaq: float):
    """trunc_cache: {ticker: 그 체크포인트까지 잘린 df}. 반환: (rs_ranks, rs_moms)."""
    kr_raw, us_raw = {}, {}
    for t, hist in trunc_cache.items():
        raw = rs_raw_score(hist["Close"])
        if raw is None:
            continue
        if is_kr_ticker(t):
            bscore = b_kospi if t.endswith(".KS") else b_kosdaq
            kr_raw[t] = raw - bscore
        else:
            us_raw[t] = raw  # 벤치마크 차감 생략 (순위불변 검증됨)
    rs_ranks = {**to_rs_rank(kr_raw), **to_rs_rank(us_raw)}

    kr3, kr12, us3, us12 = {}, {}, {}, {}
    for t, hist in trunc_cache.items():
        r3 = ret_pct(hist["Close"], 63)
        r12 = ret_pct(hist["Close"], 252)
        if is_kr_ticker(t):
            if r3 is not None: kr3[t] = r3
            if r12 is not None: kr12[t] = r12
        else:
            if r3 is not None: us3[t] = r3
            if r12 is not None: us12[t] = r12
    rank3 = {**to_rs_rank(kr3), **to_rs_rank(us3)}
    rank12 = {**to_rs_rank(kr12), **to_rs_rank(us12)}
    rs_moms = {t: rank3[t] - rank12[t] for t in trunc_cache if t in rank3 and t in rank12}
    return rs_ranks, rs_moms


# ── 3) 프로덕션 사후 필터 재현 ─────────────────────────────────────────
# app.py run_scan()의 저유동성 하드 필터(v4.52) 그대로 — KR 3억원/일,
# US $2M/일 미만인 avg_turnover는 스캔 결과에서 탈락. analyze() 안이
# 아니라 run_scan()에만 있어서 analyze()를 직접 부르면 빠뜨리기 쉽다
# (v5.66→v5.67에서 실제로 빠뜨렸던 gap — 원인은 아니었지만 실서비스와
# 안 맞는 진짜 차이였음).
KR_LIQUIDITY_FLOOR = 3e8
US_LIQUIDITY_FLOOR = 2e6


def passes_liquidity_filter(hit: dict, is_kr: bool) -> bool:
    avg_turn = hit.get("avg_turnover") or 0
    floor_ = KR_LIQUIDITY_FLOOR if is_kr else US_LIQUIDITY_FLOOR
    if avg_turn > 0 and avg_turn < floor_:
        return False
    return True


# ── 4) 체크포인트 ──────────────────────────────────────────────────────
def checkpoints(start=60, end=250, step=10):
    """all_tabs_common_yardstick_investigation.md 방법론 원문: off=60..250,
    10간격, 20지점. 다른 범위가 필요하면 호출부에서 인자로 바꾸되, 왜
    바꿨는지 스크립트에 주석을 남길 것."""
    return list(range(start, end + 1, step))


def truncate_at(df: pd.DataFrame, off: int) -> pd.DataFrame:
    n = len(df)
    return df.iloc[: n - off] if off > 0 else df


def future_after(df: pd.DataFrame, off: int) -> pd.DataFrame:
    n = len(df)
    return df.iloc[n - off:] if off > 0 else df.iloc[0:0]


# ── 5) 2R 레이스 ────────────────────────────────────────────────────────
def race(entry, stop, future_df: pd.DataFrame, max_bars: int = 60):
    """진입 후 최대 max_bars봉 레이스. 그날 저가≤손절이면 손절(같은 날 둘 다
    걸리면 보수적으로 손절 우선), 고가≥목표(진입+2×(진입-손절))면 도달.
    max_bars 다 써도 미해결이면: 가용 봉이 max_bars 이상이었으면 "unresolved"
    (0R, EV 분모 포함) / 가용 봉이 부족해서였으면 "insufficient"(EV 분모 제외).
    반환: (outcome:str, r:float|None)."""
    if entry is None or stop is None or entry <= stop:
        return (None, None)
    risk = entry - stop
    target = entry + 2 * risk
    avail = min(max_bars, len(future_df))
    for i in range(avail):
        lo = float(future_df["Low"].iloc[i])
        hi = float(future_df["High"].iloc[i])
        if lo <= stop:
            return ("stop", -1.0)
        if hi >= target:
            return ("target", 2.0)
    if len(future_df) >= max_bars:
        return ("unresolved", 0.0)
    return ("insufficient", None)


def ev_summary(outcomes: list) -> dict:
    """race() 결과 리스트 → n/nv/EV/손절률/도달률. nv는 데이터부족 제외한
    유효 표본 수(all_tabs 방법론과 동일 정의)."""
    valid = [o for o in outcomes if o[1] is not None]
    nv = len(valid)
    ev = sum(o[1] for o in valid) / nv if nv else None
    stop_n = sum(1 for o in valid if o[0] == "stop")
    target_n = sum(1 for o in valid if o[0] == "target")
    n_insuff = sum(1 for o in outcomes if o[0] == "insufficient")
    return {
        "n_hits": len(outcomes), "nv": nv, "ev_R": ev,
        "stop_rate": stop_n / nv if nv else None,
        "target_rate": target_n / nv if nv else None,
        "n_insufficient": n_insuff,
    }

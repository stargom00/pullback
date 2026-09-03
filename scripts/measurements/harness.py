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


def _fetch_kr_one(ticker, days=None):
    import naver_kr
    try:
        df = naver_kr.fetch(ticker) if days is None else naver_kr.fetch_history(ticker, days=days)
        if df is None or df.empty:
            return ticker, None
        return ticker, _downcast(df)
    except Exception:
        return ticker, None


def _fetch_us_batch(tickers, period="2y"):
    import yfinance as yf
    out = {}
    if not tickers:
        return out
    try:
        raw = yf.download(tickers, period=period, interval="1d",
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


def assert_sufficient_depth(data: dict, offsets: list, min_bars: int = 210):
    """fetch 직후, 체크포인트 루프 시작 전에 호출할 것 — checkpoints(...)의
    max offset을 감당 못 하는 fetch면 즉시 AssertionError로 실패시킨다.

    배경(2026-09-01): depth_atr 90개 체크포인트 재검증 최초 실행에서
    `fetch_universe_data()` 기본값(730일/2년)으로 `checkpoints(60,950,10)`을
    돌려 31번째 체크포인트 이후 신규 히트가 **조용히 0건으로 멎는** 사고가
    있었다. 당시엔 그 자리에서 발견해 결과가 발표되기 전에 잡았지만(전체
    측정 재감사 결과 실제로 오염된 기발표 측정은 0건, 2026-09-01 감사),
    다음에도 사람이 기억해서 fetch 깊이를 맞추는 방식은 구조적으로
    재발한다 — docstring 경고만으로는 "경고를 실패로 만든다"는 이
    프로젝트의 원칙(CLAUDE.md)에 안 맞는다. 이 함수가 그 경고를 실제
    실패로 바꾼다.

    판정: 필요봉수=max(offsets)+min_bars 미만인 티커 비율이 20% 넘으면
    실패(상장 초기 종목 소수가 못 채우는 건 정상이라 그 정도는 허용 —
    "다수가 못 채운다"만 진짜 fetch-depth 문제로 간주)."""
    if not data or not offsets:
        return
    need = max(offsets) + min_bars
    short = sum(1 for df in data.values() if len(df) < need)
    frac = short / len(data)
    if frac > 0.2:
        raise AssertionError(
            f"[harness] fetch 깊이 부족: 필요봉수={need}(max_offset={max(offsets)}+"
            f"min_bars={min_bars}), {short}/{len(data)}종목({frac:.0%})이 미달 — "
            f"fetch_universe_data(kr_days=1900, us_period='5y')처럼 확장 fetch를 쓸 것 "
            f"(harness.py fetch_universe_data() 문서 참고)."
        )


def fetch_universe_data(markets=("kr", "us"), kr_concurrency=10, us_batch_size=100,
                         progress=True, kr_days=None, us_period="2y", validate_offsets=None):
    """{ticker: df} 전체 유니버스 페치. app.py의 실제 fetch 함수와 동일 소스
    (naver_kr.fetch / yf.download). 반환: (data, kr_universe, us_universe).

    ⚠️ **기본값(kr_days=None→naver_kr.fetch() 내부 기본 730일, us_period="2y")
    은 checkpoints 최대 offset 250까지만 안전하다** — KR 730일(≈483봉)/
    US 2년(≈505봉)은 offset 250 + min_bars 210 = 460봉 요구치를 간신히
    채우는 수준이라, 그보다 큰 offset(예: 규칙9 표준 checkpoints(60,950,10)
    =90개, max offset 950)을 쓰면 필요 봉수(950+210=1160)를 못 채워
    **조용히 후반 체크포인트 전부가 신규 히트 0건으로 멎는다**(2026-09-01
    depth_atr 90개 체크포인트 재검증 최초 실행에서 실제로 겪은 사고 —
    docs/kr_us_strategy_map.md "재검증 결과 — 우선순위1" 참고). 90개+
    체크포인트를 쓰는 측정은 반드시 `kr_days=1900, us_period="5y"`를
    명시적으로 넘길 것(이 조합이 KR≈1274봉/US≈1260봉 확보, 여러 스크립트가
    각자 복붙해 쓰던 걸 여기로 통합 — README 규칙3). 기존 20개 체크포인트
    스크립트들은 인자를 안 바꾸는 한 기존과 100% 동일하게 동작한다
    (kr_days=None이면 naver_kr.fetch()로 이전과 동일 호출).

    `validate_offsets`에 그대로 쓸 `OFFSETS` 리스트를 넘기면(예:
    `fetch_universe_data(kr_days=1900, us_period="5y", validate_offsets=OFFSETS)`)
    fetch 직후 `assert_sufficient_depth()`를 자동 호출해 깊이 부족을
    즉시 실패시킨다 — **새 스크립트는 이 인자를 항상 넘길 것**(경고
    문서를 읽고 기억하는 것보다 실패가 훨씬 안전하다)."""
    from universe import get_universe
    t0 = time.time()
    kr_u = get_universe("kr") if "kr" in markets else {}
    us_u = get_universe("us") if "us" in markets else {}
    data = {}
    if kr_u:
        with ThreadPoolExecutor(max_workers=kr_concurrency) as ex:
            futs = {ex.submit(_fetch_kr_one, t, kr_days): t for t in kr_u}
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
            data.update(_fetch_us_batch(b, period=us_period))
            if progress:
                print(f"[harness] us batch {i+1}/{len(batches)} elapsed={time.time()-t0:.0f}s", flush=True)
    if progress:
        print(f"[harness] fetched {len(data)} tickers total, elapsed={time.time()-t0:.0f}s", flush=True)
    if validate_offsets:
        assert_sufficient_depth(data, validate_offsets)
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
    """저유동성 컷 + 가격고정(M&A 의심) 제외 — analyze_*()가 hit 딕셔너리에
    price_frozen을 정보용으로 항상 붙여주므로(v5.90, scanner.price_frozen_check)
    여기서 체크만 하면 기존(v4.80~) 측정 파이프라인의 배제 동작이 그대로
    유지된다 — 실제 게이트는 scanner.py에서 완전제외로 하드코딩돼 있지 않고
    app.py 표시 레이어가 처리하지만(전 탭 공통, 숨김+펼치기), 측정 스크립트는
    이 한 곳만 거치면 예전과 동일하게 제외돼 EV 수치에 영향이 없다."""
    avg_turn = hit.get("avg_turnover") or 0
    floor_ = KR_LIQUIDITY_FLOOR if is_kr else US_LIQUIDITY_FLOOR
    if avg_turn > 0 and avg_turn < floor_:
        return False
    if hit.get("price_frozen"):
        return False
    return True


# ── 4) 체크포인트 ──────────────────────────────────────────────────────
def checkpoints(start=60, end=250, step=10):
    """all_tabs_common_yardstick_investigation.md 방법론 원문: off=60..250,
    10간격, 20지점. 다른 범위가 필요하면 호출부에서 인자로 바꾸되, 왜
    바꿨는지 스크립트에 주석을 남길 것.

    ⚠️ end>250(예: 규칙9 표준 90개 = checkpoints(60,950,10))을 쓸 거면
    `fetch_universe_data(kr_days=1900, us_period="5y")`도 같이 써야 한다
    — 기본 fetch(2년치)로는 큰 offset에서 필요 봉수를 못 채워 후반
    체크포인트가 조용히 전부 빈다(fetch_universe_data 문서 참고). 이
    함수의 반환값을 그대로 `fetch_universe_data(..., validate_offsets=이_반환값)`에
    넘기면 fetch 직후 자동으로 깊이를 검증해 실패시킨다 — 새 스크립트는
    이렇게 쓸 것(2026-09-01 depth_atr 사고 재발 방지, `assert_sufficient_depth()`
    참고)."""
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


# ── 6) 두 그룹 EV 격차 유의성 (규칙 7, v5.68 README 참고) ──────────────
# 결과가 -1R/0R/+2R 세 값뿐인 이산분포라 분산이 커서, 반분·사분위 비교의
# 겉보기 격차만으론 "우연"을 못 거를 수 있다 — 2026-08-25 기관/외국인
# 수급 캠페인에서 inst_20d가 격차+단조성 기준을 통과하고도 z≈0.98로
# 유의하지 않았던 사례로 확인(scripts/measurements/README.md 규칙7).
# 원래 그 캠페인 스크립트에 로컬로 있던 함수를 이후 재사용을 위해 여기로
# 승격 — 표본을 나눠 EV를 비교하는 측정은 이 함수를 쓴다.
def ev_gap_zscore(ev_lower: dict, ev_upper: dict):
    """ev_summary() 결과 두 개(하위/상위 그룹)의 EV 격차 z통계량.
    반환: (z, significant:bool) — 계산 불가시 (None, False). 양측 95%
    기준(|z|>=1.96)으로 유의성 판정."""
    def _stats(ev):
        n = ev.get("nv") or 0
        stop_r, target_r = ev.get("stop_rate"), ev.get("target_rate")
        e = ev.get("ev_R")
        if not n or stop_r is None or target_r is None or e is None:
            return None
        e2 = 1 * stop_r + 4 * target_r  # E[R^2]: (-1)^2*stop + 2^2*target
        var = max(e2 - e ** 2, 0)
        return var, n
    a, b = _stats(ev_lower), _stats(ev_upper)
    if a is None or b is None:
        return None, False
    (var_a, n_a), (var_b, n_b) = a, b
    se = ((var_a / n_a) + (var_b / n_b)) ** 0.5
    if se == 0:
        return None, False
    gap = ev_upper["ev_R"] - ev_lower["ev_R"]
    z = gap / se
    return z, abs(z) >= 1.96


# ── 7) 연속표본 두 그룹 격차 유의성 + 랭킹 겹침 유의성 (규칙7) ──────────
# 2026-09-01 매크로 레짐/단기반응 섹터 측정에서 처음 필요해져 그 스크립트
# 로컬에 구현했다가, 두 번째 스크립트(단기반응)에서도 똑같이 필요해져
# 여기로 승격(README "하네스에 없는 새 로직이 필요하면 하네스를 확장"
# 원칙). ev_gap_zscore는 -1R/0R/+2R 이산분포 전용이라 연속값(월간/주간
# 수익률)에는 못 써 별도로 둔다.
def welch_zscore(sample_a: pd.Series, sample_b: pd.Series):
    """두 연속표본(예: 하위/상위 섹터의 수익률 관측치 풀) 평균격차
    z통계량 — Welch z(표본분산 사용, 표본이 충분히 크다는 가정)."""
    a = sample_a.dropna()
    b = sample_b.dropna()
    if len(a) < 3 or len(b) < 3:
        return None, False
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = (va / len(a) + vb / len(b)) ** 0.5
    if se == 0:
        return None, False
    z = (b.mean() - a.mean()) / se
    return z, abs(z) >= 1.96


# ── 8) 단일 표본 EV의 유의성(귀무가설: EV=0) ────────────────────────────
# ev_gap_zscore는 "두 그룹 격차"용이라, "이 표본의 EV가 그냥 0(우연)과
# 다른가"를 묻는 단일표본 검정엔 못 쓴다(비교 대상 그룹이 없음). 2026-09-04
# 장기박스(250봉) 비중복 히트 EV 측정에서 처음 필요해져 여기로 승격
# (README "하네스에 없는 새 로직이 필요하면 하네스를 확장" 원칙) — -1R/0R/
# +2R 이산분포의 분산 공식(E[R^2]=1*stop_rate+4*target_rate)은
# ev_gap_zscore와 동일, 비교 대상만 0으로 고정.
def one_sample_zscore(ev: dict):
    """ev_summary() 결과 하나의 EV가 0과 유의하게 다른지. 반환: (z,
    significant:bool) — 계산 불가시 (None, False). 양측 95% 기준
    (|z|>=1.96)으로 유의성 판정 — 방향(양/음)은 호출부가 z 부호로 직접 판단."""
    n = ev.get("nv") or 0
    stop_r, target_r, e = ev.get("stop_rate"), ev.get("target_rate"), ev.get("ev_R")
    if not n or stop_r is None or target_r is None or e is None:
        return None, False
    e2 = 1 * stop_r + 4 * target_r
    var = max(e2 - e ** 2, 0)
    se = (var / n) ** 0.5
    if se == 0:
        return None, False
    z = e / se
    return z, abs(z) >= 1.96


def hypergeom_overlap_pvalue(overlap: int, s_total: int, k: int = 3):
    """무작위로 독립적인 두 top-k 선택이 overlap개 이상 겹칠 확률(단측
    초과확률, 우연 기준선). 초기하분포: 모집단 s_total, "성공"집합 크기
    k(첫 반기 top-k), 두번째 반기에서 k개 뽑을 때 겹침 개수의 분포."""
    import math
    if s_total < k or overlap > k:
        return None

    def _pmf(x):
        if x > k or x > s_total - k:
            return 0.0
        num = math.comb(k, x) * math.comb(s_total - k, k - x)
        den = math.comb(s_total, k)
        return num / den if den else 0.0

    return sum(_pmf(x) for x in range(overlap, k + 1))

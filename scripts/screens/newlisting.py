"""신규상장 스크린 — 상장 13~20개월차 KR·US 전 상장종목 (v5.314, 사용자 지시).

사용자 지시 원문 요약: "매월 말 장 마감 후, KR·US 전 상장종목 중 상장 13~20개월차 종목을
보고 싶다(신규상장 베이스 탐색). 유니버스 컷 무관하게 전 목록 대상."

[개월수 정의 — 월 산술, 말일 기준]
  months = (기준월 − 상장월) = (ref.year − listed.year) × 12 + (ref.month − listed.month)
  기준일(ref)은 항상 그 달 **말일**이라 상장일의 '일(day)'은 결과에 영향을 주지 않는다
  (말일 기준이면 "그 달 안에 상장한 종목은 모두 같은 개월수"). 포함 범위는 13 ≤ months ≤ 20
  (양 끝 포함). 예: 기준일 2026-09-30 → 상장월 2025-01 ~ 2025-08.
  13·20은 사용자가 지정한 값이다(AI 임의 설정 아님).

[소스]
  KR: KIND 상장법인 목록(corpList download, 공개 다운로드 — 로그인·페이지 크롤링 없음,
      시장별 1회씩 총 2요청). 저점 스크린과 같은 URL·파서·보드 정의(lowpoint.py)를 그대로
      import한다(사본 없음). KOSPI·KOSDAQ만(저점과 같은 범위 — 코넥스 제외).
      ⚠ 상장일 = **현재 시장의 상장일**이다. 코넥스→코스닥·코스닥→코스피 이전상장 종목은
      이전일이 상장일로 잡힌다(예: 덕산넵코어스 266690 → 2026-09-30).
  US: 저점 스크린의 미국 보통주 목록(us_listings — Nasdaq Trader 심볼 디렉터리, ETF·우선주·
      워런트 제외) 전 종목 → yahoo 차트 메타 `firstTradeDate`(첫 거래일, ET 날짜).
      ⚠ 첫 거래일은 **IPO일의 근사치**다 — 티커 변경·재상장·SPAC 합병 상장은 yahoo가 가진
      첫 거래 기록에 따라 실제 IPO일과 다를 수 있다(결과 파일 us_date_caveat에 명기).
      첫 거래일은 바뀌지 않는 값이라 심볼별로 캐시해 다음 달엔 새 심볼만 묻는다.

[종가·하락률] 필터를 통과한 종목**만** 저점 스크린의 fetch_kr(naver 일봉 1900일)/fetch_us(yahoo 5년)로
받는다(전수 조회 없음). 13~20개월 종목은 상장일이 이 기간 안이라 그 시계열의 **첫 봉 = 첫 거래일**이고,
같은 응답에서 세 값을 뽑는다(추가 요청 0): 최신 종가(close), 기준일 종가(기준일 이하 마지막 봉),
첫 거래일 종가(첫 봉), 상장 후 최고 종가(첫 봉~기준일 최댓값)와 그 날짜. v5.315(사용자 지시)부터
하락률 = (기준일 종가 − 상장 후 최고 종가)/최고 종가 — "고점에서 반토막 이상 난 종목"을 보려는 것이라
기준을 첫 거래일 종가에서 신고가로 바꿨다(v5.314는 첫 거래일 종가 기준). 수정주가라 분할이 있어도
같은 기준이다.

실행(로컬):
  python3 scripts/screens/newlisting.py --ref 2026-09-30 --publish
  → data/newlisting_latest.json + data/us_first_trade_dates.json(첫 거래일 시드 캐시)
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lowpoint as lp   # noqa: E402 — KIND·US 목록·종가 조회를 그대로 재사용

PUBLISH_PATH = os.path.join(_ROOT, "data", "newlisting_latest.json")
FIRST_TRADE_SEED_PATH = os.path.join(_ROOT, "data", "us_first_trade_dates.json")
MONTHS_MIN, MONTHS_MAX = 13, 20          # 사용자 지시 "상장 13~20개월차"
KR_MARKET_NAME = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}
US_DATE_CAVEAT = ("US 상장일 = yahoo firstTradeDate(첫 거래일). IPO일의 근사치 — 티커 변경·재상장·"
                  "SPAC 합병 상장은 실제 IPO일과 다를 수 있다.")
KR_DATE_CAVEAT = ("KR 상장일 = KIND 상장법인 목록의 상장일(현재 시장 기준). 코넥스→코스닥·코스닥→코스피 "
                  "이전상장은 이전일이 상장일이다.")
DRAWDOWN_DEFINITION = ("하락률 = (기준일 종가 − 상장 후 최고 종가) / 상장 후 최고 종가 × 100. **상장 후 최고 종가(신고가) "
                       "기준 — 공모가·첫 거래일 종가가 아니다.** 최고 종가 = 결과 종목 일봉의 첫 봉부터 기준일까지 종가의 최댓값"
                       "(KR naver·US yahoo, 둘 다 수정주가), 기준일 종가 = 기준일 이하 마지막 봉. 첫 봉 날짜가 상장일과 다르면 "
                       "표에 따로 표시한다 — KR 이전상장은 첫 봉이 이전 시장(코넥스 등) 거래일이라 **최고 종가에 코넥스 시절이 "
                       "섞일 수 있다.** US 큰 하락은 역분할이 수정주가에 반영된 값이다.")
# v5.315 추가 지시(사용자 지정값 — 5배·365일): 급등 전력 제외
SURGE_RATIO = 5.0
SURGE_WINDOW_DAYS = 365
SURGE_DEFINITION = (f"급등 전력 제외: 기준일까지의 일봉에서 어느 날 t든 close[t] / min(close[t−{SURGE_WINDOW_DAYS}일..t]) "
                    f"≥ {SURGE_RATIO:g}(배)인 날이 하루라도 있으면 목록에서 뺀다(창은 과거 방향 — t 이후의 저점은 "
                    f"t의 판정에 안 들어간다). 결과 파일에는 남기고 surge_excluded로 표시한다.")
MONTHS_DEFINITION = ("개월수 = (기준월 − 상장월), 기준일은 월말 — 상장일의 일(day)은 무관. "
                     f"{MONTHS_MIN} ≤ 개월수 ≤ {MONTHS_MAX} 포함.")


class NoData(RuntimeError):
    """소스가 비었다(개편·차단 의심) — 조용한 빈 결과 대신 실패시킨다."""


class RateLimited(RuntimeError):
    """yahoo가 429를 돌려 일부 심볼을 못 물었다 — 그때까지 받은 첫 거래일은 캐시에 저장한 뒤
    이번 시도를 실패시킨다. 서버는 저점 러너의 기존 재시도 규칙(60분·최대 3회·KR 장중 차단)을
    그대로 타고, 다음 시도는 캐시 덕에 **못 물은 심볼만** 다시 묻는다(새 대기시간 없음)."""


# yahoo 응답 판정 결과 — 날짜가 아니면 이 둘 중 하나(조용히 섞지 않는다)
NOT_FOUND = "not_found"        # 심볼이 없거나 firstTradeDate가 없다
RATE_LIMITED = "rate_limited"  # HTTP 429 또는 네트워크 오류(일시적 — 재시도 대상)


# ── 개월수 ─────────────────────────────────────────────────────────────

def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def months_since(listed: date, ref: date) -> int:
    """월 산술(말일 기준) — 모듈 docstring 정의."""
    return (ref.year - listed.year) * 12 + (ref.month - listed.month)


def in_window(months: int) -> bool:
    return MONTHS_MIN <= months <= MONTHS_MAX


def parse_kr_date(v) -> date | None:
    """KIND 상장일 'YYYY-MM-DD'(문자열). 비거나 형식이 다르면 None(호출부가 실패 건수로 센다)."""
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def first_trade_date_from_epoch(epoch) -> date | None:
    """yahoo firstTradeDate(초, UTC) → ET 달력 날짜. 장 시작(09:30 ET)이 UTC로 같은 날이라
    UTC 날짜와 같지만, 명시적으로 ET로 바꾼다."""
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone(lp.ET).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# ── KR ────────────────────────────────────────────────────────────────

def kr_listings() -> tuple[list, dict]:
    """KIND corpList(공개 다운로드)에서 KOSPI·KOSDAQ 전 종목의 코드·이름·시장·상장일."""
    import requests
    rows, bad = [], []
    for board in ("kospi", "kosdaq"):
        corp_mt, _admin_mt, suffix, min_n = lp.KR_BOARDS[board]
        r = requests.get(lp.KIND_CORPLIST_URL, params={"method": "download", "marketType": corp_mt},
                         headers=lp._KIND_HEADERS, timeout=30)
        r.raise_for_status()
        r.encoding = "euc-kr"
        corp = lp._read_kind_table(r.text)
        if len(corp) < min_n:   # 저점 스크린과 같은 하한(소스 개편 → 조용한 빈 결과 방지)
            raise NoData(f"KIND corpList {board} {len(corp)}건 — 비정상(소스 개편 의심)")
        for code, name, listed in zip(corp["종목코드"], corp["회사명"], corp["상장일"]):
            code = str(code).strip().zfill(6)
            d = parse_kr_date(listed)
            if d is None:
                bad.append(f"{code}{suffix}")
                continue
            rows.append({"market": KR_MARKET_NAME[board], "code": f"{code}{suffix}", "name": str(name).strip(),
                         "listed": d.isoformat()})
    return rows, {"total": len(rows), "date_unparsed": bad}


# ── US ────────────────────────────────────────────────────────────────

def _yahoo_first_trade(sym: str):
    """yahoo 차트 메타의 firstTradeDate. 반환: date | NOT_FOUND | RATE_LIMITED.
    yfinance 세션(쿠키·crumb)으로 부른다(get_history_metadata). 2026-10-02 실측 경위:
    ① 첫 로컬 실행에서 yfinance를 동시 10개로 몰아 약 3,000건 뒤 YFRateLimitError(일시 429)가 났고,
    예전 코드는 이를 None("없음")과 섞어 2,548건이 조용히 빠졌다.
    ② 그래서 쿠키 없는 차트 API 직접 호출로 바꿔 봤는데, 그 경로는 같은 IP에서 **8시간 넘게**
    "Too Many Requests"로 막혔고(18:51→03:03 KST), 같은 시각 yfinance 세션 경로는 정상이었다
    → 직접 호출은 버리고 yfinance로 되돌렸다.
    결론: 429(YFRateLimitError)와 네트워크 오류는 RATE_LIMITED(일시적 — build가 받은 만큼 캐시에
    저장하고 실패, 다음 시도는 나머지만), 그 외(상폐·없는 심볼·firstTradeDate 없음)는 NOT_FOUND."""
    import requests
    import yfinance as yf
    from yfinance.exceptions import YFRateLimitError
    try:
        from curl_cffi.requests.exceptions import RequestException as CurlError   # yfinance 1.5 전송 계층
    except ImportError:
        CurlError = requests.RequestException
    try:
        md = yf.Ticker(sym).get_history_metadata() or {}
    except (YFRateLimitError, requests.RequestException, CurlError):
        return RATE_LIMITED
    except Exception:
        return NOT_FOUND          # 없는 심볼은 yfinance가 KeyError 등으로 끝난다(실측: ZZZZQ)
    d = first_trade_date_from_epoch(md.get("firstTradeDate")) if md.get("firstTradeDate") else None
    return d or NOT_FOUND


def load_first_trade_cache(paths: list) -> dict:
    """{심볼: 'YYYY-MM-DD'} — 앞의 경로가 우선(/data → 레포 시드)."""
    out = {}
    for p in reversed([p for p in paths if p]):
        try:
            with open(p, encoding="utf-8") as f:
                out.update((json.load(f) or {}).get("dates") or {})
        except (OSError, ValueError):
            pass
    return out


def save_first_trade_cache(path: str, dates: dict):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"source": "yahoo chart meta firstTradeDate", "caveat": US_DATE_CAVEAT,
                   "updated_at": datetime.now(lp.KST).isoformat(timespec="seconds"),
                   "dates": dict(sorted(dates.items()))}, f, ensure_ascii=False, indent=0)
        f.write("\n")
    os.replace(tmp, path)


def us_first_trade_dates(symbols: list, cache: dict, lookup=_yahoo_first_trade,
                         concurrency: int = lp.FETCH_CONCURRENCY) -> tuple[dict, list, list, int]:
    """캐시에 없는 심볼만 yahoo에 묻는다.
    반환: (전체 {심볼: 날짜문자열}, 없음 목록, 429로 못 물은 목록, 새로 물은 수)."""
    dates = {s: cache[s] for s in symbols if s in cache}
    todo = [s for s in symbols if s not in cache]
    not_found, limited = [], []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(lookup, s): s for s in todo}
        for fut in as_completed(futs):
            s = futs[fut]
            d = fut.result()
            if d == RATE_LIMITED:
                limited.append(s)
            elif isinstance(d, date):
                dates[s] = d.isoformat()
            else:
                not_found.append(s)
    return dates, sorted(not_found), sorted(limited), len(todo)


# ── 조립 ──────────────────────────────────────────────────────────────

def select(rows: list, ref: date) -> list:
    """상장일이 있는 행 중 개월수 창 안만 — months 필드를 붙여 개월수 오름차순."""
    out = []
    for r in rows:
        m = months_since(date.fromisoformat(r["listed"]), ref)
        if in_window(m):
            out.append({**r, "months": m})
    return sorted(out, key=lambda r: (r["months"], r["listed"], r["market"], r["code"]))


def drawdown_pct(ref_close, base_close):
    """(기준일 종가 − 기준값)/기준값 × 100, 소수 2자리. 값이 없거나 0 이하면 None.
    v5.315부터 기준값 = 상장 후 최고 종가(apply_series)."""
    if ref_close is None or base_close is None or not base_close > 0 or not ref_close > 0:
        return None
    return round((float(ref_close) - float(base_close)) / float(base_close) * 100, 2)


def surge_check(c) -> tuple[float | None, str | None]:
    """시계열 c(날짜 인덱스·오름차순, 기준일까지 자른 것)에서 close[t]/min(close[t−365일..t])의
    최댓값과 그 날짜. 창은 달력 365일, 양 끝 포함(rolling closed='both'). 값이 없으면 (None, None)."""
    if c is None or len(c) == 0:
        return None, None
    lo = c.rolling(f"{SURGE_WINDOW_DAYS}D", closed="both").min()
    ratio = c / lo
    t = ratio.idxmax()
    return round(float(ratio.max()), 4), str(t.date())


def apply_series(r: dict, c, ref: date):
    """결과 행 하나에 종가 시계열 c(날짜 인덱스, 오름차순)의 값들과 하락률을 붙인다(순수).
    최고 종가는 **첫 봉~기준일** 구간만 본다 — 기준일 뒤 고점이 섞이면 기준일 시점의 하락률이 아니다."""
    keys = ("close", "close_date", "ref_close", "ref_close_date", "first_close", "first_close_date",
            "peak_close", "peak_close_date", "surge_max_ratio", "surge_date")
    r.update(dict.fromkeys(keys))
    r["drawdown_pct"] = None
    r["surge_excluded"] = False
    if c is None or not len(c):
        return r
    r["close"], r["close_date"] = round(float(c.iloc[-1]), 4), str(c.index[-1].date())
    r["first_close"], r["first_close_date"] = round(float(c.iloc[0]), 4), str(c.index[0].date())
    upto = c[[d.date() <= ref for d in c.index]]
    if len(upto):
        r["ref_close"], r["ref_close_date"] = round(float(upto.iloc[-1]), 4), str(upto.index[-1].date())
        peak_at = upto.idxmax()                     # 같은 최고가가 여러 번이면 처음 날짜
        r["peak_close"], r["peak_close_date"] = round(float(upto.max()), 4), str(peak_at.date())
        r["surge_max_ratio"], r["surge_date"] = surge_check(upto)
        r["surge_excluded"] = r["surge_max_ratio"] is not None and r["surge_max_ratio"] >= SURGE_RATIO
    r["drawdown_pct"] = drawdown_pct(r["ref_close"], r["peak_close"])
    return r


def _attach_closes(rows: list, ref: date):
    """결과 종목만 조회(전수 조회 금지) — 호출 수 = 결과 종목 수(테스트로 고정)."""
    kr = [r["code"] for r in rows if r["market"] != "US"]
    us = [r["code"] for r in rows if r["market"] == "US"]
    closes = {}
    if kr:
        data, _ = lp.fetch_kr(kr, "week")
        closes.update(data)
    if us:
        data, _, _ = lp.fetch_us(us, "week", auto_adjust=True)   # 저점 v5.324 배당 미조정 전환의 범위 밖 — 기존 값 유지
        closes.update(data)
    for r in rows:
        apply_series(r, closes.get(r["code"]), ref)


def build(ref: date, *, us_listings_path: str | None = None, cache_paths: list | None = None,
          cache_write_path: str | None = None, is_trading_day=None, us_lookup=_yahoo_first_trade,
          check_us_ready: bool = True) -> dict:
    """한 번 실행 = 결과 엔트리(dict). 쓰기는 호출부(write_publish)."""
    import harness
    t0 = time.time()
    if ref != month_end(ref):
        raise ValueError(f"기준일은 월말이어야 한다: {ref}")
    if check_us_ready:
        # 저점 월봉과 같은 US 데이터 준비 선체크(v5.309) — 미도착이면 DataNotReady로 재시도 규칙을 탄다
        lp.check_us_data_ready("month", ref, is_trading_day)
    kr_rows, kr_meta = kr_listings()
    us_names, us_stats = lp.us_universe(path=us_listings_path) if us_listings_path else lp.us_universe()
    cache = load_first_trade_cache(cache_paths or [])
    t_us = time.time()
    dates, us_failed, us_limited, asked = us_first_trade_dates(sorted(us_names), cache, lookup=us_lookup)
    us_secs = time.time() - t_us
    if cache_write_path:
        save_first_trade_cache(cache_write_path, dates)   # 429여도 받은 만큼은 남긴다(다음 시도는 나머지만)
    if us_limited:
        raise RateLimited(f"yahoo 429 — {len(us_limited)}건 미조회(받은 {len(dates)}건은 캐시에 저장) · "
                          f"예: {', '.join(us_limited[:5])}")
    if us_names and not dates:
        raise NoData(f"US 첫 거래일 0건 조회(대상 {len(us_names)}) — yahoo 차단·개편 의심")
    us_rows = [{"market": "US", "code": s, "name": us_names[s], "listed": d} for s, d in dates.items()]
    rows = select(kr_rows, ref) + select(us_rows, ref)
    rows.sort(key=lambda r: (r["months"], r["listed"], r["market"], r["code"]))
    _attach_closes(rows, ref)
    return {
        "ref_date": ref.isoformat(),
        "months_window": [MONTHS_MIN, MONTHS_MAX],
        "months_definition": MONTHS_DEFINITION,
        "kr_date_caveat": KR_DATE_CAVEAT,
        "us_date_caveat": US_DATE_CAVEAT,
        "drawdown_definition": DRAWDOWN_DEFINITION,
        "surge_definition": SURGE_DEFINITION,
        "run_stamp": harness.run_stamp(),
        "counts": {
            "kr": {"listed_total": kr_meta["total"], "date_unparsed": len(kr_meta["date_unparsed"]),
                   "hits": sum(r["market"] != "US" for r in rows)},
            "us": {"listed_total": len(us_names), "first_trade_found": len(dates),
                   "first_trade_failed": len(us_failed), "asked_yahoo": asked,
                   "yahoo_seconds": round(us_secs, 1), "hits": sum(r["market"] == "US" for r in rows)},
            "no_close": sum(r.get("close") is None for r in rows),
            "no_drawdown": sum(r.get("drawdown_pct") is None for r in rows),
            # 급등 전력으로 목록에서 빠지는 13~20개월 종목 수(시장별)
            "excluded_surge": {"kr": sum(bool(r.get("surge_excluded")) and r["market"] != "US" for r in rows),
                               "us": sum(bool(r.get("surge_excluded")) and r["market"] == "US" for r in rows)},
            # 첫 봉 날짜가 상장일과 다르면(데이터가 상장일보다 늦게 시작) 그 하락률은 첫 거래일 기준이 아니다 — 건수로 드러낸다
            "first_bar_after_listed": sum(bool(r.get("first_close_date")) and r["first_close_date"] > r["listed"]
                                          for r in rows),
            # 첫 봉이 상장일보다 앞서면(KR 이전상장 — 코넥스 등 이전 시장 거래 이력) 하락률 기준이 그 시점이다
            "first_bar_before_listed": sum(bool(r.get("first_close_date")) and r["first_close_date"] < r["listed"]
                                           for r in rows),
        },
        "us_first_trade_failed": us_failed,
        "kr_date_unparsed": kr_meta["date_unparsed"],
        "elapsed_seconds": round(time.time() - t0, 1),
        "rows": rows,
    }


def write_publish(entry: dict, path: str = PUBLISH_PATH) -> dict:
    """원자적 쓰기(tmp → os.replace). 실패하면 기존 파일은 그대로."""
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    return entry


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ref", required=True, help="기준일(월말) YYYY-MM-DD")
    ap.add_argument("--publish", action="store_true", help="data/newlisting_latest.json + 첫 거래일 시드 캐시에 기록")
    a = ap.parse_args(argv)
    entry = build(date.fromisoformat(a.ref), cache_paths=[FIRST_TRADE_SEED_PATH],
                  cache_write_path=FIRST_TRADE_SEED_PATH if a.publish else None)
    c = entry["counts"]
    print(f"[newlisting] 기준일 {entry['ref_date']} · KR {c['kr']['hits']}종목 (전체 {c['kr']['listed_total']}) · "
          f"US {c['us']['hits']}종목 (전체 {c['us']['listed_total']}, 첫거래일 {c['us']['first_trade_found']} · "
          f"실패 {c['us']['first_trade_failed']} · yahoo 조회 {c['us']['asked_yahoo']}건 {c['us']['yahoo_seconds']}s) · "
          f"종가 없음 {c['no_close']} · 총 {entry['elapsed_seconds']}s")
    if a.publish:
        write_publish(entry)
        print(f"[newlisting] 기록: {PUBLISH_PATH}")


if __name__ == "__main__":
    main()

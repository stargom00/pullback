"""저점종목 주간·월간 후보 스크린 (키움 조건검색 대체, 맥 로컬 전용).

프로덕션(app.py·static)과 무관한 로컬 스크립트다 — 배포되지 않는다.

조건(키움 조건식 그대로, 주봉·월봉 공통 — 사용자 지시 원문:
"A: 1봉전 종가 < 0봉전 종가 / B: 1봉전 RSI(14) 30 하향돌파 (RSI[1] < 30
AND RSI[2] >= 30)"):
  A: close[1] < close[0]
  B: rsi[1] < 30 and rsi[2] >= 30
RSI는 scanner.rsi()(Wilder, ewm alpha=1/14) 그대로. 일봉 종가를 주봉(W-FRI)/
월봉(ME)으로 리샘플한 뒤 계산한다.

0봉 = 마지막 "마감된" 봉. 진행 중인 봉(이번 주/이번 달)은 반드시 버린다 —
주중·월중에 돌려도 결과가 바뀌지 않게. 마감 판정은 봉 구간의 달력상 마지막
날(주봉=금요일, 월봉=말일)의 시장 마감 확정 시각이 지났는가로 한다
(금요일 휴장이어도 금요일 확정 시각까지 기다린다 — 보수적이지만 그 사이에
세션이 없으니 결과는 같다).

실행:
  python3 scripts/screens/lowpoint.py --market kr|kospi|kosdaq|us|all --tf week|month
  (kr = 코스피+코스닥)
출력: 콘솔 표 + scripts/screens/out/lowpoint_{market}_{tf}_{기준봉날짜}.csv
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_ROOT, os.path.join(_ROOT, "scripts", "measurements")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scanner import rsi  # noqa: E402  (Wilder RSI — 재구현 금지)

OUT_DIR = os.path.join(_HERE, "out")

RSI_PERIOD = 14
RSI_LEVEL = 30.0

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
# KR 일봉 확정 시각 — app.py `KR_CLOSE_CONFIRMED_HM`(KST 20:10, 애프터마켓 종료+10분)
# 과 동기화 필요. app.py를 import하면 무거워서 값만 복사했고,
# test_lowpoint_screen.py가 두 값이 같은지 app.py 텍스트로 검사한다.
KR_CLOSE_CONFIRMED_HM = 20 * 60 + 10
# US 일봉 확정 시각 — 정규장 16:00 ET + 여유 60분. 여유는 AI 판단 어림값
# (측정 근거 없음, yfinance 반영 지연 감안). ZoneInfo라 서머타임 자동 처리.
US_CLOSE_CONFIRMED_HM = 17 * 60

# 리샘플 봉 최소 개수(번인). Wilder RSI는 첫 봉부터 ewm으로 시작해 초기값
# 영향이 (13/14)^n으로 줄어든다 — 주봉 52봉이면 ≈2%, 월봉 36봉이면 ≈7%.
# AI 판단 어림값(재검토 필요). 미달 종목은 조용히 빼지 않고 끝에 목록으로 출력.
MIN_BARS = {"week": 52, "month": 36}

# fetch 창: 주봉 ≈5년(≈260봉), 월봉 ≈10년(≈120봉). 사용자 지시 "월봉은 RSI14 +
# 번인 → 최소 3년 이상"보다 넉넉하게 — naver siseJson은 기간과 무관하게
# 요청당 ~1초라(naver_kr.fetch_history docstring) 길게 받아도 비용이 같다.
KR_DAYS = {"week": 1900, "month": 3700}
US_PERIOD = {"week": "5y", "month": "10y"}

RULE = {"week": "W-FRI", "month": "ME"}

KIND_CORPLIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"
KIND_ADMIN_URL = "https://kind.krx.co.kr/investwarn/adminissue.do"
_KIND_HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/124.0"}


# ── 순수 로직 (테스트 대상) ─────────────────────────────────────────────

def lowpoint_signal(c0: float, c1: float, r1: float, r2: float) -> bool:
    """A: 1봉전 종가 < 0봉전 종가, B: RSI[1] < 30 and RSI[2] >= 30."""
    cond_a = c1 < c0
    cond_b = r1 < RSI_LEVEL and r2 >= RSI_LEVEL
    return bool(cond_a and cond_b)


def resample_bars(close: pd.Series, tf: str) -> pd.DataFrame:
    """일봉 종가 → 주봉/월봉. index=봉 구간 라벨(W-FRI 금요일 / 월말일),
    columns: close(구간 마지막 종가), bar_date(구간 내 실제 마지막 거래일)."""
    c = close.dropna()
    s = pd.Series(c.index, index=c.index)
    g_close = c.resample(RULE[tf]).last()
    g_date = s.resample(RULE[tf]).last()
    out = pd.DataFrame({"close": g_close, "bar_date": g_date}).dropna()
    return out


def _confirm_dt(label: pd.Timestamp, market: str) -> datetime:
    tz, hm = (KST, KR_CLOSE_CONFIRMED_HM) if market == "kr" else (ET, US_CLOSE_CONFIRMED_HM)
    d = label.date()
    return datetime(d.year, d.month, d.day, hm // 60, hm % 60, tzinfo=tz)


def is_bar_closed(label: pd.Timestamp, market: str, now: datetime) -> bool:
    """봉 구간의 달력상 마지막 날(label) 시장 확정 시각이 지났으면 마감."""
    return now >= _confirm_dt(label, market)


def drop_in_progress(bars: pd.DataFrame, market: str, now: datetime) -> pd.DataFrame:
    """진행 중인(아직 마감 안 된) 봉을 버린다 — 보통 마지막 1봉만 해당."""
    keep = [is_bar_closed(lbl, market, now) for lbl in bars.index]
    return bars[pd.Series(keep, index=bars.index)]


def last_closed_label(tf: str, market: str, now: datetime) -> pd.Timestamp:
    """now 기준 마지막으로 마감된 봉 구간 라벨(헤더/파일명용, 데이터 무관)."""
    today = now.astimezone(KST if market == "kr" else ET).date()
    days = pd.date_range(today - timedelta(days=70), today + timedelta(days=40), freq="D")
    labels = pd.Series(1, index=days).resample(RULE[tf]).last().index
    closed = [l for l in labels if is_bar_closed(l, market, now)]
    return closed[-1]


def evaluate(close: pd.Series, tf: str, market: str, now: datetime) -> dict:
    """한 종목 판정. 반환 dict의 status: 'hit' | 'no' | 'short'(번인 부족)."""
    bars = drop_in_progress(resample_bars(close, tf), market, now)
    if len(bars) < max(MIN_BARS[tf], 3):
        return {"status": "short", "n_bars": len(bars)}
    r = rsi(bars["close"], RSI_PERIOD)
    c0, c1 = float(bars["close"].iloc[-1]), float(bars["close"].iloc[-2])
    r0, r1, r2 = float(r.iloc[-1]), float(r.iloc[-2]), float(r.iloc[-3])
    hit = lowpoint_signal(c0, c1, r1, r2)
    return {"status": "hit" if hit else "no", "label": bars.index[-1],
            "bar_date": bars["bar_date"].iloc[-1], "c0": c0, "c1": c1,
            "r2": r2, "r1": r1, "r0": r0}


# ── 유니버스 ───────────────────────────────────────────────────────────

def _read_kind_table(text: str) -> pd.DataFrame:
    return pd.read_html(io.StringIO(text), converters={"종목코드": str})[0]


# 보드별 KIND 파라미터: (corpList marketType, adminissue marketType, 티커 접미사, 최소 건수)
# 최소 건수는 소스 개편으로 조용히 빈 결과가 오는 것을 실패로 만들기 위한 하한
# (2026-09-27 실측 KOSPI 831·KOSDAQ 1818 고유코드의 대략 60%, AI 판단 어림값).
KR_BOARDS = {
    "kospi": ("stockMkt", "1", ".KS", 500),
    "kosdaq": ("kosdaqMkt", "2", ".KQ", 1000),
}
_KIND_EMPTY = "조회된 결과값이 없습니다."


def kr_universe(board: str = "kospi") -> tuple[dict, dict]:
    """KIND corpList 상장법인(회사 단위 → 보통주 대표코드) − KIND adminissue
    같은 시장 관리종목 스냅샷. board: 'kospi' | 'kosdaq'.
    반환: ({ticker: name}, meta)."""
    import requests
    corp_mt, admin_mt, suffix, min_n = KR_BOARDS[board]
    r = requests.get(KIND_CORPLIST_URL, params={"method": "download", "marketType": corp_mt},
                     headers=_KIND_HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = "euc-kr"
    corp = _read_kind_table(r.text)
    corp["종목코드"] = corp["종목코드"].str.strip().str.zfill(6)
    uni = {f"{c}{suffix}": n for c, n in zip(corp["종목코드"], corp["회사명"])}
    if len(uni) < min_n:
        raise RuntimeError(f"KIND corpList {board} {len(uni)}건 — 비정상(소스 개편 의심)")

    r = requests.post(KIND_ADMIN_URL, data={"method": "searchAdminIssueSub", "marketType": admin_mt,
                                            "currentPageSize": "3000", "pageIndex": "1",
                                            "forward": "adminissue_down"},
                      headers=_KIND_HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = "euc-kr"
    adm = _read_kind_table(r.text)
    adm = adm[adm["종목코드"].str.strip() != _KIND_EMPTY]  # 0건이면 안내문 1행이 온다
    adm["종목코드"] = adm["종목코드"].str.strip().str.zfill(6)
    if adm.empty:
        raise RuntimeError(f"KIND adminissue {board} 관리종목 0건 — 비정상(소스 개편 의심)")
    admin = {f"{c}{suffix}": n for c, n in zip(adm["종목코드"], adm["종목명"])}
    excluded = {t: admin[t] for t in admin if t in uni}
    for t in excluded:
        uni.pop(t)
    return uni, {"kind_total": len(uni) + len(excluded), "kind_rows": len(corp),
                 "admin_snapshot": len(admin), "admin_excluded": excluded}


def us_universe() -> dict:
    from universe import get_universe
    return get_universe("us")


# ── 데이터 ─────────────────────────────────────────────────────────────

def fetch_kr(tickers: list, tf: str, concurrency: int = 10) -> tuple[dict, list]:
    import naver_kr
    data, failed = {}, []

    def one(t):
        try:
            df = naver_kr.fetch_history(t, days=KR_DAYS[tf])
            return t, (None if df is None or df.empty else df["Close"].dropna())
        except Exception:
            return t, None

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for fut in as_completed([ex.submit(one, t) for t in tickers]):
            t, c = fut.result()
            if c is None or c.empty:
                failed.append(t)
            else:
                data[t] = c
    return data, failed


def fetch_us(tickers: list, tf: str, batch: int = 100) -> tuple[dict, list]:
    """스캐너와 같은 소스·파라미터(yf.download, auto_adjust=True) — 기간만 길게."""
    import harness
    data = {}
    for i in range(0, len(tickers), batch):
        got = harness._fetch_us_batch(tickers[i:i + batch], period=US_PERIOD[tf])
        data.update({t: df["Close"].dropna() for t, df in got.items()})
    failed = [t for t in tickers if t not in data or data[t].empty]
    return {t: c for t, c in data.items() if not c.empty}, failed


# ── 실행 ───────────────────────────────────────────────────────────────

_MKT_ORDER = {"KOSPI": 0, "KOSDAQ": 1, "US": 2}
COLS = ["시장", "코드", "종목명", "기준봉날짜", "0봉종가", "1봉종가", "RSI[2]", "RSI[1]", "RSI[0]"]


def clock_of(market: str) -> str:
    """마감 시각 판정용 시장: kospi/kosdaq → 'kr'."""
    return "kr" if market in KR_BOARDS or market == "kr" else "us"


def screen_market(market: str, tf: str, now: datetime) -> dict:
    """market: 'kospi' | 'kosdaq' | 'us'."""
    t0 = time.time()
    meta = {}
    if market in KR_BOARDS:
        uni, meta = kr_universe(market)
        data, failed = fetch_kr(list(uni), tf)
    else:
        uni = us_universe()
        data, failed = fetch_us(list(uni), tf)

    # 거래정지/상폐 추정: 일봉 마지막 날짜가 시장 직전 거래일(전 종목 최빈값)보다 오래됨
    last_dates = Counter(c.index[-1].normalize() for c in data.values())
    session = last_dates.most_common(1)[0][0] if last_dates else None
    stale = {t: str(c.index[-1].date()) for t, c in data.items() if c.index[-1].normalize() < session}

    rows, short = [], {}
    for t, c in data.items():
        if t in stale:
            continue
        res = evaluate(c, tf, clock_of(market), now)
        if res["status"] == "short":
            short[t] = res["n_bars"]
            continue
        if res["status"] == "hit":
            rows.append({"시장": market.upper(), "코드": t, "종목명": uni.get(t, ""),
                         "기준봉날짜": str(res["bar_date"].date()),
                         "0봉종가": round(res["c0"], 2), "1봉종가": round(res["c1"], 2),
                         "RSI[2]": round(res["r2"], 2), "RSI[1]": round(res["r1"], 2),
                         "RSI[0]": round(res["r0"], 2)})
    return {"market": market, "universe": len(uni), "fetched": len(data), "failed": sorted(failed),
            "session": session, "stale": stale, "short": short, "rows": rows, "meta": meta,
            "elapsed": time.time() - t0, "names": uni}


def main(argv=None):
    ap = argparse.ArgumentParser(description="저점종목 주간·월간 후보 스크린")
    ap.add_argument("--market", choices=["kr", "kospi", "kosdaq", "us", "all"], required=True,
                    help="kr = kospi+kosdaq, all = kospi+kosdaq+us")
    ap.add_argument("--tf", choices=["week", "month"], required=True)
    args = ap.parse_args(argv)

    import harness
    now = datetime.now().astimezone()
    stamp = harness.run_stamp()
    markets = {"kr": ["kospi", "kosdaq"], "all": ["kospi", "kosdaq", "us"]}.get(args.market, [args.market])
    labels = {m: last_closed_label(args.tf, clock_of(m), now) for m in markets}

    tf_ko = "주봉" if args.tf == "week" else "월봉"
    print(f"=== 저점종목 스크린 ({tf_ko}) ===")
    print(f"run_stamp: local={stamp['run_at_local']} (UTC{stamp['local_tz_utc_offset_hours']:+g}) "
          f"| {stamp['run_at_kst']}")
    for m in markets:
        print(f"[{m.upper()}] 0봉 = {labels[m].date()} 마감 봉 ({tf_ko} 구간 라벨, 진행 중 봉 제외)")
    print("조건: A 1봉전 종가 < 0봉 종가 / B RSI(14)[1] < 30 and RSI[2] >= 30\n")

    results = [screen_market(m, args.tf, now) for m in markets]
    all_rows = []
    for res in results:
        m = res["market"].upper()
        print(f"── {m}: 유니버스 {res['universe']} / 조회성공 {res['fetched']} / "
              f"정지추정 제외 {len(res['stale'])} / 번인부족 제외 {len(res['short'])} / "
              f"신호 {len(res['rows'])} ({res['elapsed']:.0f}s)")
        if res["meta"]:
            ex = res["meta"]["admin_excluded"]
            print(f"   KIND {m} {res['meta']['kind_total']}종목(원본 {res['meta']['kind_rows']}행, 중복 병합), "
                  f"관리종목 스냅샷 {res['meta']['admin_snapshot']}건 중 {len(ex)}건 제외")
        print(f"   직전 거래일(일봉 최빈값) = {res['session'].date() if res['session'] is not None else None}")
        all_rows.extend(res["rows"])

    df = pd.DataFrame(all_rows, columns=COLS).sort_values(["시장", "RSI[1]"], key=lambda col: col.map(_MKT_ORDER) if col.name == "시장" else col) if all_rows \
        else pd.DataFrame(columns=COLS)
    print()
    print(df.to_string(index=False) if len(df) else "(신호 없음)")

    os.makedirs(OUT_DIR, exist_ok=True)
    base_label = max(labels.values()).date()
    path = os.path.join(OUT_DIR, f"lowpoint_{args.market}_{args.tf}_{base_label}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\nCSV: {os.path.relpath(path, _ROOT)}")

    # 조용한 누락 금지 — 제외/실패 전부 목록으로
    for res in results:
        m = res["market"].upper()
        print(f"\n[{m}] 조회 실패 {len(res['failed'])}종목: "
              + (", ".join(f"{t}({res['names'].get(t, '')})" for t in res["failed"]) or "없음"))
        if res["stale"]:
            print(f"[{m}] 정지추정 제외 {len(res['stale'])}종목(마지막 일봉 < 직전 거래일): "
                  + ", ".join(f"{t}({res['names'].get(t, '')}) {d}" for t, d in sorted(res["stale"].items())))
        if res["short"]:
            print(f"[{m}] 번인부족 제외 {len(res['short'])}종목(마감 봉 < {MIN_BARS[args.tf]}): "
                  + ", ".join(f"{t}({n})" for t, n in sorted(res["short"].items())))
        if res["meta"]:
            print(f"[{m}] 관리종목 제외: "
                  + (", ".join(f"{t}({n})" for t, n in sorted(res["meta"]["admin_excluded"].items())) or "없음"))
    return df


if __name__ == "__main__":
    main()

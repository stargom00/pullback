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
                                      [--publish]
  (kr = 코스피+코스닥)
출력: 콘솔 표 + scripts/screens/out/lowpoint_{market}_{tf}_{기준봉날짜}.csv
--publish: data/lowpoint_latest.json에 이번 실행분을 기록(앱 캘린더 홈 카드가
읽는 파일). **주봉·월봉은 별도 키라 week 실행이 month 결과를 지우지 않는다**
(merge_publish) — 파일 전체를 새로 쓰는 게 아니라 tf 칸만 갈아끼운다.
커밋·push는 하지 않는다(사용자가 직접 지시) — 실행 끝에 다음 할 일만 출력.
"""
from __future__ import annotations

import argparse
import io
import json
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
# 앱(app.py `_load_lowpoint_latest()`)이 읽는 게시 파일 — Railway 볼륨(/data)이
# 아니라 **레포 안의 data/ 디렉터리**다(git으로 배포된다). 계산은 이 맥 로컬
# 스크립트만 하고 앱은 표시만 한다.
PUBLISH_PATH = os.path.join(_ROOT, "data", "lowpoint_latest.json")

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


# ── 게시(--publish) ────────────────────────────────────────────────────

def publish_entry(results: list, tf: str, labels: dict, stamp: dict) -> dict:
    """이번 실행분 한 칸(tf) 페이로드. 앱은 이 dict를 그대로 표시만 한다."""
    rows = []
    for res in results:
        for r in res["rows"]:
            rows.append({
                "market": r["시장"], "code": r["코드"], "name": r["종목명"],
                "bar_date": r["기준봉날짜"],       # 구간 내 실제 마지막 거래일
                "close0": r["0봉종가"], "close1": r["1봉종가"],
                "rsi2": r["RSI[2]"], "rsi1": r["RSI[1]"], "rsi0": r["RSI[0]"],
            })
    excluded = {res["market"].upper(): {
        "universe": res["universe"], "fetched": res["fetched"],
        "failed": len(res["failed"]), "stale": len(res["stale"]),
        "short": len(res["short"]),
        "admin_excluded": len((res["meta"] or {}).get("admin_excluded") or {}),
    } for res in results}
    return {
        # 봉 구간 라벨(주봉=금요일, 월봉=말일) — 낡음 판정은 앱이 이 값으로 한다
        # (`_lowpoint_expected_label()`). 행마다 있는 bar_date(실제 마지막
        # 거래일)와 다른 값이므로 섞지 말 것.
        "bar_date": str(max(labels.values()).date()),
        "run_stamp": stamp,
        "markets": [res["market"].upper() for res in results],
        "rows": rows,
        "excluded_counts": excluded,
    }


def merge_publish(existing: dict | None, tf: str, entry: dict) -> dict:
    """기존 파일 내용에 tf 칸만 갈아끼운다 — 다른 tf는 그대로 보존한다.
    (week 실행이 month 결과를 지우면 안 된다 — 사용자 지시.
    test_lowpoint_publish.py가 사보타주로 이 병합을 검사한다.)"""
    out = dict(existing or {})
    out[tf] = entry
    return out


def write_publish(tf: str, entry: dict, path: str = PUBLISH_PATH) -> dict:
    existing = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            existing = loaded if isinstance(loaded, dict) else None
        except (ValueError, OSError) as e:
            # 조용히 덮어쓰면 다른 tf 결과가 사라진다 — 실패로 만든다.
            raise RuntimeError(f"{path} 읽기 실패({e}) — 병합 불가, 수동 확인 필요")
    merged = merge_publish(existing, tf, entry)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return merged


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


def us_universe(refresh: bool = False) -> tuple[dict, dict]:
    """미국 보통주 전체(Nasdaq Trader 심볼 디렉터리) — **이 스크린 전용**.
    스캐너 공용 `universe.get_universe("us")`를 쓰지 않는 이유는 us_listings.py
    docstring 참고(시총 $500M+ 필터 때문에 ZUMZ 같은 소형주가 빠진다).
    반환: ({yahoo심볼: 이름}, stats)"""
    import us_listings
    return us_listings.build_universe(refresh=refresh)


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


# 옵션 필터(--us-min-price / --us-min-avg-volume)용 평균 거래량 창. 기본 필터가
# 꺼져 있으므로 이 값은 "옵션을 켰을 때 무엇을 재는지"만 정한다(임계값 아님).
US_AVG_VOLUME_WINDOW = 60


def fetch_us(tickers: list, tf: str, batch: int = 100) -> tuple[dict, list, dict]:
    """스캐너와 같은 소스·파라미터(yf.download, auto_adjust=True) — 기간만 길게.
    반환: (종가 시리즈 dict, 실패 목록, {티커: {last_close, avg_volume}})
    세 번째 값은 CLI 옵션 필터용 부가정보다 — 기본값에서는 쓰이지 않는다."""
    import harness
    data, extra = {}, {}
    for i in range(0, len(tickers), batch):
        got = harness._fetch_us_batch(tickers[i:i + batch], period=US_PERIOD[tf])
        for t, df in got.items():
            c = df["Close"].dropna()
            if c.empty:
                continue
            data[t] = c
            vol = df["Volume"].dropna().tail(US_AVG_VOLUME_WINDOW)
            extra[t] = {"last_close": float(c.iloc[-1]),
                        "avg_volume": float(vol.mean()) if len(vol) else None}
    failed = [t for t in tickers if t not in data]
    return data, failed, extra


# ── 실행 ───────────────────────────────────────────────────────────────

_MKT_ORDER = {"KOSPI": 0, "KOSDAQ": 1, "US": 2}
COLS = ["시장", "코드", "종목명", "기준봉날짜", "0봉종가", "1봉종가", "RSI[2]", "RSI[1]", "RSI[0]"]


def clock_of(market: str) -> str:
    """마감 시각 판정용 시장: kospi/kosdaq → 'kr'."""
    return "kr" if market in KR_BOARDS or market == "kr" else "us"


def screen_market(market: str, tf: str, now: datetime, refresh_universe: bool = False,
                  min_price: float | None = None, min_avg_volume: float | None = None) -> dict:
    """market: 'kospi' | 'kosdaq' | 'us'.
    min_price·min_avg_volume은 **US 전용 옵션 필터**로 기본은 None(끔) — 임계값을
    임의로 정하지 않는다(사용자 지시). 켜면 몇 건이 빠졌는지 결과에 남는다."""
    t0 = time.time()
    meta = {}
    opt_dropped = {}
    if market in KR_BOARDS:
        uni, meta = kr_universe(market)
        data, failed = fetch_kr(list(uni), tf)
    else:
        uni, meta = us_universe(refresh=refresh_universe)
        data, failed, extra = fetch_us(list(uni), tf)
        if min_price is not None or min_avg_volume is not None:
            for t in list(data):
                x = extra.get(t) or {}
                if min_price is not None and (x.get("last_close") or 0) < min_price:
                    opt_dropped[t] = f"price {x.get('last_close')}"
                elif min_avg_volume is not None and (x.get("avg_volume") or 0) < min_avg_volume:
                    opt_dropped[t] = f"avg_volume {x.get('avg_volume')}"
            for t in opt_dropped:
                data.pop(t, None)

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
            "opt_dropped": opt_dropped, "elapsed": time.time() - t0, "names": uni}


def exclusion_detail_lines(res: dict, tf: str) -> list:
    """실행 끝에 찍는 "무엇이 왜 빠졌나" 목록. 시장마다 meta 모양이 다르다 —
    KR은 KIND 관리종목(`admin_excluded`), US는 상장목록 통계(`excluded_by_reason`).
    US 유니버스 분리(2026-09-29) 직후 이 블록이 `meta["admin_excluded"]`를 무조건
    읽어 KeyError로 죽었다(CSV는 이미 쓰인 뒤라 결과는 멀쩡했지만 실행이 비정상 종료).
    시장별 분기를 한 곳에 모으고 테스트로 고정한다."""
    m = res["market"].upper()
    out = [f"\n[{m}] 조회 실패 {len(res['failed'])}종목: "
           + (", ".join(f"{t}({res['names'].get(t, '')})" for t in res["failed"]) or "없음")]
    if res["stale"]:
        out.append(f"[{m}] 정지추정 제외 {len(res['stale'])}종목(마지막 일봉 < 직전 거래일): "
                   + ", ".join(f"{t}({res['names'].get(t, '')}) {d}"
                               for t, d in sorted(res["stale"].items())))
    if res["short"]:
        out.append(f"[{m}] 번인부족 제외 {len(res['short'])}종목(마감 봉 < {MIN_BARS[tf]}): "
                   + ", ".join(f"{t}({n})" for t, n in sorted(res["short"].items())))
    meta = res.get("meta") or {}
    if "admin_excluded" in meta:                       # KR(KIND)
        out.append(f"[{m}] 관리종목 제외: "
                   + (", ".join(f"{t}({n})" for t, n in sorted(meta["admin_excluded"].items()))
                      or "없음"))
    elif "excluded_by_reason" in meta:                  # US(Nasdaq Trader 상장목록)
        out.append(f"[{m}] 상장목록 제외(사유별): {meta['excluded_by_reason']} "
                   f"— 원본 {meta['total']}행 → 보통주 {meta['kept']}")
    if res.get("opt_dropped"):
        out.append(f"[{m}] 옵션 필터 제외 {len(res['opt_dropped'])}종목: "
                   + ", ".join(f"{t}({v})" for t, v in sorted(res["opt_dropped"].items())[:20]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="저점종목 주간·월간 후보 스크린")
    ap.add_argument("--market", choices=["kr", "kospi", "kosdaq", "us", "all"], required=True,
                    help="kr = kospi+kosdaq, all = kospi+kosdaq+us")
    ap.add_argument("--tf", choices=["week", "month"], required=True)
    ap.add_argument("--refresh-universe", action="store_true",
                    help="US 상장목록 캐시를 다시 받는다(기본은 캐시 재사용, us_listings.py)")
    ap.add_argument("--us-min-price", type=float, default=None,
                    help="US 옵션 필터: 마지막 종가 하한(기본 없음 — 임계값을 임의로 두지 않는다)")
    ap.add_argument("--us-min-avg-volume", type=float, default=None,
                    help=f"US 옵션 필터: 최근 {US_AVG_VOLUME_WINDOW}일 평균 거래량 하한(기본 없음)")
    ap.add_argument("--publish", action="store_true",
                    help="data/lowpoint_latest.json에 이번 실행분 기록(앱 캘린더 카드용). "
                         "다른 tf 결과는 보존된다. 커밋·push는 안 함.")
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

    results = [screen_market(m, args.tf, now, refresh_universe=args.refresh_universe,
                             min_price=args.us_min_price,
                             min_avg_volume=args.us_min_avg_volume) for m in markets]
    all_rows = []
    for res in results:
        m = res["market"].upper()
        print(f"── {m}: 유니버스 {res['universe']} / 조회성공 {res['fetched']} / "
              f"정지추정 제외 {len(res['stale'])} / 번인부족 제외 {len(res['short'])} / "
              f"신호 {len(res['rows'])} ({res['elapsed']:.0f}s)")
        if res["meta"] and "admin_excluded" in res["meta"]:
            ex = res["meta"]["admin_excluded"]
            print(f"   KIND {m} {res['meta']['kind_total']}종목(원본 {res['meta']['kind_rows']}행, 중복 병합), "
                  f"관리종목 스냅샷 {res['meta']['admin_snapshot']}건 중 {len(ex)}건 제외")
        elif res["meta"]:
            st = res["meta"]
            print(f"   Nasdaq Trader 심볼목록 원본 {st['total']}행 → 보통주 {st['kept']} "
                  f"(파일생성 {st.get('file_creation_time')}, 캐시 {st.get('fetched_at')})")
            print(f"   제외: {st['excluded_by_reason']}")
        if res.get("opt_dropped"):
            print(f"   옵션 필터 제외 {len(res['opt_dropped'])}종목(--us-min-price/--us-min-avg-volume)")
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

    if args.publish:
        entry = publish_entry(results, args.tf, labels, stamp)
        write_publish(args.tf, entry)
        rel = os.path.relpath(PUBLISH_PATH, _ROOT)
        print(f"게시: {rel} ({args.tf} 칸 갱신, 신호 {len(entry['rows'])}건, "
              f"기준봉 {entry['bar_date']})")
        print(f"다음 할 일: git add {rel} && git commit -m \"data: lowpoint {args.tf} "
              f"{entry['bar_date']}\" 후 push (커밋·push는 지시 후 실행)")

    # 조용한 누락 금지 — 제외/실패 전부 목록으로
    for res in results:
        for line in exclusion_detail_lines(res, args.tf):
            print(line)
    return df


if __name__ == "__main__":
    main()

"""저점종목 주간·월간 후보 스크린 (키움 조건검색 대체, 맥 로컬 전용).

프로덕션(app.py·static)과 무관한 로컬 스크립트다 — 배포되지 않는다.

조건(키움 조건식 그대로, 주봉·월봉 공통 — 사용자 지시 원문:
"A: 1봉전 종가 < 0봉전 종가 / B: 1봉전 RSI(14) 30 하향돌파 (RSI[1] < 30
AND RSI[2] >= 30)"):
  A: close[1] < close[0]
  B: rsi[1] < 30 and rsi[2] >= 30
RSI는 **고전 Wilder**(v5.323, rsi_wilder_sma — 첫 14개 변화량의 단순평균으로 시작한 뒤 Wilder 평활) — 키움·트레이딩뷰
(ta.rsi) 표준과 같은 시딩. 5탭 스캐너의 scanner.rsi()(첫 변화량부터 ewm)와는 시작부가 다르다(이력이 짧을수록 차이가
크고, 길면 사라진다). 일봉 종가를 주봉(W-FRI)/월봉(ME)으로 리샘플한 뒤 계산한다. KR 가격은 naver 통합 시세(애프터
포함) — 키움 조건검색과 같은 기준(CLAUDE.md "KR 가격 기준 — naver 통합 단일 소스").

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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_ROOT, os.path.join(_ROOT, "scripts", "measurements")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

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
#
# ⚠️ **월봉 창을 줄이면 결과가 바뀐다 — 2026-10-01 실측으로 확인, 축소 보류.**
# 운영 메모리(월봉 실행 피크 +399MB) 때문에 월봉을 10년→5년(KR 3,700→1,900일)으로
# 줄여보고 같은 기준봉(2026-09-30)으로 비교했더니 **hit 8건 → 7건, 공통 2건뿐**이었다:
# KR 3건(008040.KS·160550.KQ·016670.KQ)이 사라지고 US 5건(CMRC·CUVL·MQ·QTRX·SITC)이
# 새로 들어왔으며, 양쪽에 다 있는 TEAD조차 RSI[2] 30.14 → 30.02로 달라졌다.
# 원인: Wilder RSI는 `ewm(adjust=False)`라 **첫 봉의 초기값 영향이 (13/14)^n으로만
# 감쇠**한다 — 월봉 120봉(10년)이면 ≈0.02%지만 60봉(5년)이면 ≈1.3%가 남는다. 저점
# 조건은 RSI 30 경계 판정이고 실제 히트가 28.9~31.0에 몰려 있어 이 정도 차이가 hit/miss를
# 뒤집는다. 번인부족 제외 수는 거의 같았으므로(표본 문제가 아니라 **값 자체**의 차이다).
# 함의: `MIN_BARS["month"]=36`은 "번인 충분" 보장이 아니다(그 지점의 잔존 영향 ≈7%).
# 월봉 결과는 창 길이에 민감하므로 **창을 바꾸려면 반드시 같은 기준봉 재현 비교를 먼저**
# 할 것. 메모리는 창 축소가 아닌 다른 수단(배치·동시성 축소 등)으로 다룬다.
KR_DAYS = {"week": 1900, "month": 3700}
US_PERIOD = {"week": "5y", "month": "10y"}

RULE = {"week": "W-FRI", "month": "ME"}

# v5.309(사용자 지시) — **US 데이터 준비 선체크.**
# 2026-10-01 10:48 KST 월봉 실행에서 US 15종목이 "정지추정"으로 탈락했는데, 그 안에
# SITC·ADEA·HUBB·SHEL·UA·WLY 같은 **대형주**가 들어 있었다. 거래정지가 아니라 야후가
# 09-30 일봉을 아직 안 올린 것이었다(12:02 실행에선 4종목, 13시엔 0종목). 즉 월말·주말
# 직후 실행은 종목별 봉 도착 시차 때문에 **비재현적**이고, 그 결과가 조용히 "정지추정
# 제외"로 기록된다. KR에는 확정 시각 규칙이 있는데(KR_CLOSE_CONFIRMED_HM) US엔 없었다.
# 그래서 US 파트를 시작하기 전에 **초유동 기준 종목 3개**의 최신 일봉이 목표 거래일에
# 도달했는지 보고, 미달이면 이번 시도를 실패시킨다(DataNotReady) — 호출부(app.py
# _maybe_run_lowpoint)가 기존 재시도 규칙(60분 간격·최대 3회·KR 장중 차단)을 그대로 쓴다.
# 새 대기시간·임계값을 만들지 않는다(사용자 지시).
# 기준 종목은 사용자가 지정: AAPL·MSFT·NVDA(미국 최대 거래량 종목이라 데이터가 늦게
# 올라올 이유가 없다 — 이 셋이 비어 있으면 시장 전체가 아직 안 온 것이다).
US_DATA_CHECK_TICKERS = ("AAPL", "MSFT", "NVDA")


class DataNotReady(RuntimeError):
    """US 일봉이 목표 거래일까지 도착하지 않았다 — 이번 시도를 포기하고 재시도 대상."""


def expected_us_session(label, is_trading_day=None) -> "date":
    """봉 라벨(주봉=금요일 / 월봉=말일) 이하의 **마지막 US 거래일**.
    `is_trading_day(market, YYYY-MM-DD)`를 받으면 공휴일까지 반영한다 — app.py의 것을
    그대로 주입받아 쓴다(휴장일 목록 사본을 만들지 않는다). 없으면 주말만 걸러낸다
    (CLI 로컬 실행용 폴백 — 월말이 공휴일인 드문 경우만 보수적으로 어긋난다)."""
    d = label.date() if hasattr(label, "date") else label
    for _ in range(10):
        ok = is_trading_day("us", d.isoformat()) if is_trading_day else d.weekday() < 5
        if ok:
            return d
        d -= timedelta(days=1)
    return d


def check_us_data_ready(tf: str, label, is_trading_day=None) -> dict:
    """기준 3종목의 최신 일봉 날짜가 목표 거래일에 도달했는지 확인.
    미달이면 DataNotReady를 올린다. 반환: {expected, seen: {티커: 날짜}}."""
    import harness
    target = expected_us_session(label, is_trading_day)
    got = harness._fetch_us_batch(list(US_DATA_CHECK_TICKERS), period="1mo")
    seen, behind = {}, []
    for t in US_DATA_CHECK_TICKERS:
        df = got.get(t)
        last = None if df is None or df.empty else df.index[-1].date()
        seen[t] = str(last) if last else None
        if last is None or last < target:
            behind.append(f"{t}={seen[t]}")
    if behind:
        raise DataNotReady(
            f"US 일봉 미도착 — 목표 거래일 {target}, 기준 종목 {', '.join(behind)} "
            f"(tf={tf}, 라벨 {getattr(label, 'date', lambda: label)()})")
    return {"expected": str(target), "seen": seen}


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


def rsi_wilder_sma(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """고전 Wilder RSI(v5.323, 사용자 지시 "저점·평가의 RSI를 고전 Wilder(첫 14봉 SMA 시드)로 교체"):
    첫 평균 상승·하락 = 처음 period개 변화량의 단순평균(그 봉에 첫 값), 이후 avg = (avg×(period−1) + 이번 값)/period.
    앞 period개 봉은 NaN. 하락 평균 0이면 100(상승도 0이면 NaN — 판정 불가). 키움·트레이딩뷰(ta.rsi = rma)와 같은 정의.
    내부 요구 봉수: period+1(이보다 짧으면 전부 NaN)."""
    c = close.astype(float)
    out = np.full(len(c), np.nan)
    if len(c) <= period:
        return pd.Series(out, index=c.index)
    d = np.diff(c.to_numpy())
    g, l = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = g[:period].mean(), l[:period].mean()

    def val(ag, al):
        if al == 0:
            return 100.0 if ag > 0 else np.nan
        return 100 - 100 / (1 + ag / al)
    out[period] = val(ag, al)
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        out[i + 1] = val(ag, al)
    return pd.Series(out, index=c.index)


def evaluate(close: pd.Series, tf: str, market: str, now: datetime) -> dict:
    """한 종목 판정. 반환 dict의 status: 'hit' | 'no' | 'short'(번인 부족)."""
    bars = drop_in_progress(resample_bars(close, tf), market, now)
    if len(bars) < max(MIN_BARS[tf], 3):
        return {"status": "short", "n_bars": len(bars)}
    r = rsi_wilder_sma(bars["close"], RSI_PERIOD)
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


def us_universe(refresh: bool = False, path: str | None = None) -> tuple[dict, dict]:
    """미국 보통주 전체(Nasdaq Trader 심볼 디렉터리) — **이 스크린 전용**.
    스캐너 공용 `universe.get_universe("us")`를 쓰지 않는 이유는 us_listings.py
    docstring 참고(시총 $500M+ 필터 때문에 ZUMZ 같은 소형주가 빠진다).
    반환: ({yahoo심볼: 이름}, stats)"""
    import us_listings
    # path: 상장목록 캐시 위치 — 맥 CLI는 기본(scripts/screens/cache/), 서버 자동 실행은
    # /data 볼륨(v5.301). 목록을 만드는 로직은 us_listings 하나다(사본 없음).
    if path:
        return us_listings.build_universe(refresh=refresh, path=path)
    return us_listings.build_universe(refresh=refresh)


# ── 데이터 ─────────────────────────────────────────────────────────────

# 원천 조회 동시성 — fetch_kr 기본값을 이름으로 뺀 것(값 불변). v5.314 신규상장 스크린이
# yahoo 메타 조회에 같은 값을 재사용한다(새 임계값 금지 — 사용자 지시).
FETCH_CONCURRENCY = 10


def fetch_kr(tickers: list, tf: str, concurrency: int = FETCH_CONCURRENCY) -> tuple[dict, list]:
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


# ── 계산 기준(v5.323, 사용자 결정) ──────────────────────────────────────────────────────────
# KR 가격 = naver 통합 시세(애프터마켓 포함) 단일 소스, RSI = 고전 Wilder(rsi_wilder_sma).
# 경위: v5.321에서 인바이오젠(101140.KS) 10-02 오히트(naver 5,230 vs 정규장 4,820)를 보고 KR을 정규장(2025-03-04 이전
# naver + 이후 yfinance, 이음새 비율 검증)으로 바꿨는데, 키움 10-02 주봉 목록과 대조하니 **일치가 6/8 → 3/9로
# 나빠졌다**. naver 통합 + 고전 Wilder + 최소 봉 수 조건 없음이면 키움 KR 7종목이 7/7 재현됐다 — 키움 조건검색도 통합
# 시세를 쓴다(KRX 공식 종가도 애프터 포함). 단일 소스가 이음새·yfinance 과거 일봉 오류(하루 튐·수정주가 차이)도 없다.
# 그래서 되돌렸다. 아래 B안 코드(splice_regular·fetch_kr_regular*·price_note)는 **비활성**으로 남겨 둔다(호출 0곳 —
# test_lowpoint_kr_basis.py가 고정) — 같은 조사를 반복하지 않도록 근거로 둔다. 상세: CLAUDE.md "KR 가격 기준" 절.
# v5.324(사용자 결정 "저점 스크린 US + 평가 페이지 US를 배당 미조정(분할만 조정)으로 전환"): 키움 10-02 주봉 US 12종목
# 대조에서 배당 조정 가격이면 8/12, 미조정이면 10/12(JBGS·AVA가 맞아지고 BIT가 빠짐, 깨지는 일치 0) — 키움 US는 배당
# 미조정으로 추정(직접 확인은 못 함). yfinance auto_adjust=False의 Close = 분할만 반영된 종가. 상세: CLAUDE.md
# "US 배당 기준" 절. 신규상장(newlisting)은 이 결정 범위 밖이라 auto_adjust=True를 명시해 그대로 둔다.
US_AUTO_ADJUST = False
CALC_BASIS = "naver_integrated+wilder_sma+us_splits_only"   # 서버 실행 상태에 기록 — 바뀌면 그 주·달 결과를 창 안에서 다시 만든다


# [비활성 — v5.321 B안, v5.323에 철회] 원래 주석:
# v5.321(사용자 지시) — **저점 스크린·평가의 KR 가격 = KRX 정규장 종가(B안 하이브리드).**
# naver 일봉(fetch_kr)의 종가는 정규장(15:30) 종가가 아니라 애프터마켓(통합 시세, NXT 16:00~20:00) 마지막 체결가다
# (2026-10-04 조사: 인바이오젠 10-02 naver 5,230 vs 정규장 4,820 → 주봉 A조건이 뒤집혀 잘못 히트, 10-02 KR 표본
# 40종목 중 39종목이 naver ≠ 정규장). 이 스크린은 키움 조건검색 대체라 키움·트레이딩뷰와 같은 정규장 종가를 써야
# 한다. naver 공개 엔드포인트(siseJson·모바일 일별 시세·차트 API)에 KRX 정규장 전용 옵션이 없어(파라미터 11종
# 시도) 정규장 값은 yfinance(.KS/.KQ, auto_adjust=False — 분할 반영·배당 미반영)에서 온다. 표본 10종목에서 yfinance
# 종가가 KRX 공식 "전일 종가"와 10/10 일치. 그런데 yfinance KR **과거** 일봉은 품질 문제가 있다(같은 날짜에 여러
# 종목이 하루씩 튐 — NXT 이전 일봉의 1.31%, 수정주가 방식 차이 — 인바이오젠 1.25배·디모아 2024 값 엉킴).
# 그래서 사용자 확정 B안: **NXT 개장(2025-03-04) 이전 = naver**(애프터마켓이 없던 시기라 정규장과 같고 수정주가가
# 일관됨), **이후 = yfinance 정규장**. 이음새는 경계 직전 공통 거래일의 종가 비율로 검증해, 1±0.5% 밖이면
# (수정주가 어긋남) 그 비율로 과거 구간을 재조정하고, 비율이 일정하지 않아 재조정할 수 없으면 경고 플래그를
# 단다 — 조용히 섞지 않는다(사용자 지시). 남는 한계: NXT 이후 yfinance 일봉의 하루 튐은 걸러지지 않는다.
# **나머지 KR 경로(5탭 스캐너·종가베팅·현재가·신규상장)는 naver 통합 시세 그대로다** — 그쪽 측정·백테스트가
# 그 데이터 정의(애프터 포함, KR_CLOSE_CONFIRMED_HM 20:10 이후 확정)로 이뤄져 있어 바꾸면 근거가 깨진다.
# (v5.321 당시 기록 — v5.323에 철회. 현재 기준은 CLAUDE.md "KR 가격 기준" 절.)

NXT_START = pd.Timestamp("2025-03-04")   # 넥스트레이드(NXT) 개장일 — 이날부터 naver 일봉 종가가 통합 시세
SEAM_TOL = 0.005                          # 이음새 비율 허용 ±0.5%(사용자 지시 값)
SEAM_DAYS = 20                            # 경계 직전 공통 거래일 수 — 비율 중앙값을 재는 창(AI 판단 어림값, 재검토 필요)
SEAM_MIN_DAYS = 5                         # 공통 거래일이 이보다 적으면 비율을 못 믿는다(AI 판단 어림값)


def splice_regular(nv: "pd.DataFrame | None", rg: "pd.DataFrame | None") -> tuple:
    """naver(경계 이전) + yfinance 정규장(경계부터)을 잇는다. 같은 열 구성의 일봉 DataFrame 둘(종가 열 'Close'
    필수, 가격 열 Open/High/Low/Close와 Volume은 있으면 함께 처리). 반환 (DataFrame | None, info).
    info["status"]: ok(비율 1±0.5% 안) · rescaled(과거 구간을 비율로 재조정 — 가격 ÷비율, 거래량 ×비율)
                    · unverified(경계 앞뒤 비율이 일정하지 않거나 공통일이 부족 — 재조정 못 함, 그대로 이음)
                    · regular_only(NXT 이후 상장 — yfinance만) · no_regular(yfinance 없음 — naver 통합 시세 그대로)."""
    has_nv = nv is not None and not nv.empty
    has_rg = rg is not None and not rg.empty
    if not has_rg:
        return (nv if has_nv else None), {"status": "no_regular", "ratio": None}
    post = rg[rg.index >= NXT_START]
    if not has_nv or nv.index[0] >= NXT_START:
        return rg, {"status": "regular_only", "ratio": None}
    pre = nv[nv.index < NXT_START]
    common = pre.index.intersection(rg.index)[-SEAM_DAYS:]
    if len(common) < SEAM_MIN_DAYS:
        out = pd.concat([pre, post])
        return out, {"status": "unverified", "ratio": None, "why": f"경계 직전 공통 거래일 {len(common)}일"}
    ratio = (pre.loc[common, "Close"] / rg.loc[common, "Close"]).astype(float)
    m = float(ratio.median())
    half = len(ratio) // 2
    m1, m2 = float(ratio.iloc[:half].median()), float(ratio.iloc[half:].median())
    info = {"ratio": round(m, 4), "common_days": len(common)}
    if abs(m - 1) <= SEAM_TOL:
        return pd.concat([pre, post]), {**info, "status": "ok"}
    if abs(m1 / m2 - 1) > SEAM_TOL:   # 창 안에서 비율이 바뀌었다 — 한 배수로 재조정할 근거가 없다
        return pd.concat([pre, post]), {**info, "status": "unverified",
                                         "why": f"경계 앞 비율이 일정하지 않음({m1:.4f}→{m2:.4f})"}
    pre = pre.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in pre.columns:
            pre[col] = pre[col] / m
    if "Volume" in pre.columns:
        pre["Volume"] = pre["Volume"] * m
    return pd.concat([pre, post]), {**info, "status": "rescaled"}


def price_note(info: dict) -> "str | None":
    """결과 화면에 붙일 데이터 경고(조용히 섞지 않는다). ok·regular_only는 None."""
    st = (info or {}).get("status")
    if st == "rescaled":
        return f"수정주가 재조정 ×{info['ratio']}(naver↔정규장 이음새)"
    if st == "unverified":
        return f"이음새 미검증 — {info.get('why', '')}"
    if st == "no_regular":
        return "정규장 시세 없음 — naver 통합 시세(애프터 포함) 사용"
    return None


def fetch_kr_regular_frames(tickers: list, tf: str, batch: int = 100,
                            concurrency: int = FETCH_CONCURRENCY) -> tuple[dict, dict]:
    """KR 정규장 일봉(B안 하이브리드, OHLCV 그대로). 반환 ({티커: DataFrame}, {티커: 이음새 info}).
    naver는 fetch_kr과 같은 조회 기간(KR_DAYS), yfinance는 US와 같은 US_PERIOD·묶음 100·auto_adjust=False.
    스크린(fetch_kr_regular)과 평가 페이지(lowpoint_eval)가 이 한 함수를 쓴다(사본 금지)."""
    import harness
    import naver_kr
    cols = ["Open", "High", "Low", "Close", "Volume"]

    def one(t):
        try:
            df = naver_kr.fetch_history(t, days=KR_DAYS[tf])
            return t, (None if df is None or df.empty else df[[c for c in cols if c in df.columns]].dropna(subset=["Close"]))
        except Exception:
            return t, None
    nv = {}
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for fut in as_completed([ex.submit(one, t) for t in tickers]):
            t, df = fut.result()
            if df is not None and not df.empty:
                nv[t] = df
    rg = {}
    for i in range(0, len(tickers), batch):
        rg.update(harness._fetch_us_batch(tickers[i:i + batch], period=US_PERIOD[tf], auto_adjust=False))
    data, flags = {}, {}
    for t in tickers:
        r = rg.get(t)
        if r is not None:
            r = r[[c for c in cols if c in r.columns]].dropna(subset=["Close"])
        df, info = splice_regular(nv.get(t), r)
        flags[t] = info
        if df is not None and not df.empty:
            data[t] = df
    return data, flags


def fetch_kr_regular(tickers: list, tf: str) -> tuple[dict, list, dict]:
    """스크린용 — 종가 시리즈만. 반환 ({티커: 종가}, 실패 목록, {티커: 이음새 info})."""
    frames, flags = fetch_kr_regular_frames(tickers, tf)
    data = {t: df["Close"] for t, df in frames.items()}
    return data, [t for t in tickers if t not in data], flags


# 옵션 필터(--us-min-price / --us-min-avg-volume)용 평균 거래량 창. 기본 필터가
# 꺼져 있으므로 이 값은 "옵션을 켰을 때 무엇을 재는지"만 정한다(임계값 아님).
US_AVG_VOLUME_WINDOW = 60


def fetch_us(tickers: list, tf: str, batch: int = 100, auto_adjust: bool = US_AUTO_ADJUST) -> tuple[dict, list, dict]:
    """yf.download 일봉 — 기간만 길게. 기본은 저점 기준 **배당 미조정(분할만 조정, US_AUTO_ADJUST=False)**.
    5탭 스캐너(app._fetch_us_batch, auto_adjust=True)와는 다르다 — 그쪽은 측정 기반이라 불변.
    반환: (종가 시리즈 dict, 실패 목록, {티커: {last_close, avg_volume}})
    세 번째 값은 CLI 옵션 필터용 부가정보다 — 기본값에서는 쓰이지 않는다."""
    import harness
    data, extra = {}, {}
    for i in range(0, len(tickers), batch):
        got = harness._fetch_us_batch(tickers[i:i + batch], period=US_PERIOD[tf], auto_adjust=auto_adjust)
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
                  min_price: float | None = None, min_avg_volume: float | None = None,
                  us_listings_path: str | None = None, is_trading_day=None) -> dict:
    """market: 'kospi' | 'kosdaq' | 'us'.
    min_price·min_avg_volume은 **US 전용 옵션 필터**로 기본은 None(끔) — 임계값을
    임의로 정하지 않는다(사용자 지시). 켜면 몇 건이 빠졌는지 결과에 남는다."""
    t0 = time.time()
    meta = {}
    opt_dropped = {}
    if market in KR_BOARDS:
        uni, meta = kr_universe(market)
        data, failed = fetch_kr(list(uni), tf)   # v5.323: naver 통합 시세 단일 소스로 복귀(v5.321 정규장 B안 철회 — CALC_BASIS 주석)
    else:
        # v5.309: 유니버스·일봉을 받기 **전에** 데이터 도착부터 확인(위 docstring)
        meta["data_ready"] = check_us_data_ready(tf, last_closed_label(tf, "us", now),
                                                is_trading_day=is_trading_day)
        uni, uni_meta = us_universe(refresh=refresh_universe, path=us_listings_path)
        meta.update(uni_meta)
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


ALL_MARKETS = ["kospi", "kosdaq", "us"]


def screen_all(tf: str, now: datetime, markets: list | None = None, refresh_universe: bool = False,
               us_listings_path: str | None = None, is_trading_day=None) -> tuple[list, dict]:
    """KOSPI·KOSDAQ·US를 한 번에 — CLI `--market all`과 **같은 경로**(v5.301, 서버 자동
    실행이 이 함수를 그대로 부른다). 반환: (screen_market 결과 목록, {market: 0봉 라벨})."""
    markets = markets or ALL_MARKETS
    labels = {m: last_closed_label(tf, clock_of(m), now) for m in markets}
    results = [screen_market(m, tf, now, refresh_universe=refresh_universe,
                             us_listings_path=us_listings_path,
                             is_trading_day=is_trading_day) for m in markets]
    return results, labels


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

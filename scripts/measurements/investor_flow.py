"""
네이버 금융 종목별 기관/외국인 순매매 스크래핑 (2026-08-25, 기관/외국인 수급
EV 캠페인 전용 — scripts/measurements/2026-08-25_institutional_flow_pullback_ev.py).

【pykrx 진단 결과, 사용 불가 확정】
pykrx.stock.get_market_trading_value_by_date() 등 투자자별 거래 API를
직접 호출·추적한 결과: data.krx.co.kr의 모든 JSON 통계 엔드포인트(투자자
수급뿐 아니라 기본 OHLCV 포함)가 이제 로그인 세션을 요구한다 — 비로그인
요청은 HTTP 400 + 본문 "LOGOUT"으로 거부됨(서버가 정상 응답하고 세션
쿠키까지 내려주므로 IP 차단이 아니라 애플리케이션 레벨 인증 거부).
pykrx는 이미 최신 버전(1.2.8, PyPI 확인)이고 KRX_ID/KRX_PW 환경변수로
실계정 로그인을 요구하는 코드가 내장돼 있음 — 즉 stale 버전 문제가
아니라 KRX 정책 변경이며, 이 프로젝트는 정확히 같은 벽을 이미 한 번
겪고(universe.py: "pykrx는 KRX 로그인 요구로 폐기, v4.38.9") 가격
데이터를 네이버 스크래핑(naver_kr.py)으로 이미 전환한 전례가 있다.
계정 로그인 자격증명을 스크립트에 넣는 건 이 프로젝트 범위 밖이라
채택하지 않음 — 이번에도 같은 패턴(네이버 스크래핑)으로 대체.

【데이터 소스】
finance.naver.com/item/frgn.naver?code={6자리}&page={n}
페이지당 20거래일, 테이블: 날짜·종가·전일비·등락률·거래량·기관순매매량·
외국인순매매량·외국인보유주수·외국인보유율. 기관/외국인 값은 "거래량"
(주 단위)이지 순매수 금액(원)이 아니다.

【정규화 근사와 오차 캐비어트】
- 순매수 금액 근사 = 순매매거래량 × 그날 종가 (하루 전체 체결이 종가
  1점에서 일어났다고 가정 — 실제로는 VWAP에 가까운 값이 맞지만 이
  페이지는 VWAP을 안 준다). 일중 변동폭이 큰 날일수록 오차 커짐.
  근사 오차 크기는 docs/institutional_flow_pullback_ev.md에 실측
  (고저폭/종가 비율)으로 정량화해서 캐비어트로 남긴다.
- 시가총액 근사 = 외국인보유주수 / (외국인보유율/100) — 외국인 보유율이
  보고되는 종목만 가능(0%나 비공개면 None, 해당 정규화 필드만 제외).
"""
from __future__ import annotations

import random
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}
_URL = "https://finance.naver.com/item/frgn.naver"
_TIMEOUT = 8


def _to_code(ticker: str) -> str:
    return ticker.split(".")[0].zfill(6)


def _parse_num(s: str) -> float | None:
    s = s.strip().replace(",", "").replace("+", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fetch_page(code: str, page: int) -> list[dict]:
    params = {"code": code, "page": page}
    for attempt in range(3):
        try:
            resp = requests.get(_URL, headers=_HEADERS, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            break
        except Exception:
            if attempt == 2:
                return []
            time.sleep(0.5 * (attempt + 1) + random.uniform(0, 0.3))
    html = resp.content.decode("euc-kr", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    # v1(2026-08-25 최초 구현): tables[3] 고정 인덱스로 찾았다가, 종목마다
    # 앞쪽 테이블 개수가 달라(예: 카카오 035720은 투자의견 테이블이 없어
    # 순매매 테이블이 인덱스 2로 밀림) 빈 결과가 나오는 걸 확인 — summary
    # 속성으로 의미 매칭하도록 수정(포지션에 안 흔들림).
    tbl = None
    for t in soup.find_all("table"):
        summary = t.get("summary") or ""
        if "순매매" in summary:
            tbl = t
            break
    if tbl is None:
        return []
    rows = []
    for tr in tbl.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 9:
            continue
        cells = [td.get_text(strip=True) for td in tds]
        date_s, close_s = cells[0], cells[1]
        inst_s, frgn_s, hold_s, pct_s = cells[5], cells[6], cells[7], cells[8]
        try:
            date = datetime.strptime(date_s, "%Y.%m.%d")
        except ValueError:
            continue
        close = _parse_num(close_s)
        inst = _parse_num(inst_s)
        frgn = _parse_num(frgn_s)
        hold = _parse_num(hold_s)
        pct = _parse_num(pct_s.replace("%", ""))
        if close is None or inst is None or frgn is None:
            continue
        rows.append({
            "date": date, "close": close, "inst_net_vol": inst, "frgn_net_vol": frgn,
            "frgn_hold_shares": hold, "frgn_hold_pct": pct,
        })
    return rows


def fetch_investor_flow(ticker: str, min_days: int = 300, max_pages: int = 20) -> pd.DataFrame | None:
    """종목의 일별 기관/외국인 순매매거래량 + 종가 + 외국인 보유주수/보유율.
    date 오름차순 DatetimeIndex DataFrame 반환. 실패/데이터없음 시 None."""
    code = _to_code(ticker)
    all_rows: list[dict] = []
    for page in range(1, max_pages + 1):
        rows = _fetch_page(code, page)
        if not rows:
            break
        all_rows.extend(rows)
        time.sleep(random.uniform(0.05, 0.15))
        if len(all_rows) >= min_days:
            break
    if not all_rows:
        return None
    df = pd.DataFrame(all_rows).drop_duplicates(subset="date").sort_values("date")
    df = df.set_index("date")
    return df


def market_cap_approx(row: pd.Series) -> float | None:
    """시총 근사(원) = 외국인보유주수 / (외국인보유율/100) × 종가."""
    pct = row.get("frgn_hold_pct")
    hold = row.get("frgn_hold_shares")
    close = row.get("close")
    if not pct or not hold or not close or pct <= 0:
        return None
    shares_out = hold / (pct / 100.0)
    return shares_out * close


def flow_fields_at(flow_df: pd.DataFrame, signal_date, windows=(5, 20)) -> dict:
    """signal_date(그 종목이 히트한 날) 기준, 그날을 포함한 최근 N거래일
    누적 순매수 금액근사를 시총 대비 %로 정규화 + 연속 순매수일수(streak).
    flow_df에 signal_date 이후 데이터가 섞이지 않도록 그 이하만 사용
    (룩어헤드 방지 — 신호 시점에 알 수 있던 정보만)."""
    sub = flow_df[flow_df.index <= signal_date]
    if sub.empty:
        return {}
    last_row = sub.iloc[-1]
    mcap = market_cap_approx(last_row)
    out = {}
    for w in windows:
        if len(sub) < w:
            out[f"inst_{w}d"] = None
            out[f"frgn_{w}d"] = None
            continue
        win = sub.iloc[-w:]
        inst_val = float((win["inst_net_vol"] * win["close"]).sum())
        frgn_val = float((win["frgn_net_vol"] * win["close"]).sum())
        out[f"inst_{w}d"] = (inst_val / mcap * 100) if mcap else None
        out[f"frgn_{w}d"] = (frgn_val / mcap * 100) if mcap else None
    # streak: signal_date부터 거슬러 올라가며 (기관+외국인) 합산 순매수(>0)가
    # 연속되는 일수. 첫 날이 순매도면 streak=0.
    streak = 0
    combined = (sub["inst_net_vol"] + sub["frgn_net_vol"]).values
    for v in combined[::-1]:
        if v > 0:
            streak += 1
        else:
            break
    out["streak"] = streak
    out["mcap_approx"] = mcap
    return out

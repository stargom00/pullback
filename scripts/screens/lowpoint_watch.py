"""저점 관찰(v5.325) — 주·월봉 저점 히트를 히트 종가 대비 +5% 도달까지 추적하는 순수 계산.

사용자 지시 요지: "주·월봉 저점 히트가 수십 개라 한번에 진입 불가. 히트 시점 종가를 기준가로 기록하고 이후 +5% 도달
여부를 자동 추적 — 도달한 종목은 활성 목록에서 빠지고, 미도달 종목만 진입 후보로 남는 관찰 페이지."

레코드(서버 /data/lowpoint_watch.json, 저장 규칙은 app.py — 저점 매매 기록과 같은 레코드 단위 rev):
  id          w_{tf}_{label}_{code} — **기준일(코호트)별 한 건**: 같은 종목이 다음 스캔에 또 나오면 별도 레코드
  tf          week | month
  label       스캔 기준봉 라벨(주봉 금요일·월봉 말일 — 저점 게시 파일의 bar_date) — 화면 그룹 키
  base_date   그 종목의 실제 마지막 거래일(행 bar_date) — 이 날 **다음 거래일부터** 고가를 본다
  base_price  기준가 = 스캔 기준일 종가(행 close0 — 저점 스크린이 판정에 쓴 바로 그 값)
  code·name·market(KOSPI|KOSDAQ|US)·mkt(KR|US)
  status      active | reached;  reached_date·reached_days(기준일 → 도달일 달력 일수)
  last_close·last_date·checked_at — 마지막 추적 결과(활성 종목만 갱신)
  stage       v5.337 watch | departed | resting | restart | invalid — 출발 이후 단계(stage_info), 종료(restart·invalid)면 고정
  invalid_line·departure_high·stage_date·stage_close·last_checked_date·departure_volume·last_volume·stage_warning
  departure_vol_mult·departure_close_pos·departure_upper_wick — 출발일 모양(표시 전용, departure_shape)

판정(v5.333): 기준일 **이후** 거래일의 **정규장 고가** ≥ 기준가 × (1 + REACH_PCT/100) → 도달(정확히 1.05배도 도달).
기준일 당일·이전은 보지 않는다. KR 정규장 고가 = naver 분봉 09:00~15:30 최고가(장외 체결 제외 — 아래 REACH_RULE 주석,
꿈비 사례), US = yfinance 일봉 고가(정규장만). 기준가는 스캔 기준일 통합 종가 그대로. 현재가 표시는 통합 일봉 종가.
조회는 **관찰 중(active) 종목만**(수십 건) — 유니버스 전수 조회 없음.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

import lowpoint as lp
import lowpoint_eval as ev

REACH_PCT = ev.SHORT_GOAL_PCT   # +5% — 평가 페이지 단기 목표와 같은 값(새 임계값 아님)
TFS = ("week", "month")


def watch_id(tf: str, label: str, code: str) -> str:
    return f"w_{tf}_{label}_{code}"


def records_from_entry(entry: dict, tf: str) -> list:
    """저점 게시 한 칸(publish_entry 결과) → 관찰 레코드(신규 상태). 기준가 = 행 close0."""
    if not isinstance(entry, dict) or not entry.get("bar_date"):
        return []
    label = entry["bar_date"]
    out = []
    for r in entry.get("rows") or []:
        code, price = r.get("code"), r.get("close0")
        if not code or price is None or not r.get("bar_date"):
            continue
        out.append({"id": watch_id(tf, label, code), "tf": tf, "label": label, "base_date": r["bar_date"],
                    "base_price": float(price), "code": code, "name": r.get("name") or code,
                    "market": r.get("market"), "mkt": "US" if r.get("market") == "US" else "KR",
                    "status": "active", "reached_date": None, "reached_days": None,
                    "last_close": None, "last_date": None, "checked_at": None})
    return out


def reach_threshold(base_price: float, pct: float = REACH_PCT) -> float:
    return base_price * (100 + pct) / 100


def reach_info(base_price: float, base_date: str, daily: "pd.DataFrame | None", pct: float = REACH_PCT) -> dict:
    """일봉(High·Close, 날짜 인덱스) → {reached, reached_date, reached_days, last_close, last_date}.
    base_date 다음 날부터만 본다. 고가 비교는 부동소수 오차만 허용(정확히 1.05배 = 도달)."""
    out = {"reached": False, "reached_date": None, "reached_days": None, "last_close": None, "last_date": None}
    if daily is None or daily.empty:
        return out
    d = daily.dropna(subset=["Close"]).copy()
    d.index = pd.to_datetime(d.index).normalize()
    out["last_close"] = float(d["Close"].iloc[-1])
    out["last_date"] = str(d.index[-1].date())
    after = d[d.index > pd.Timestamp(base_date)]
    thr = reach_threshold(base_price, pct)
    hi = after["High"] if "High" in after.columns else after["Close"]
    hit = after[hi >= thr * (1 - 1e-12)]
    if len(hit):
        rd = hit.index[0].date()
        out.update(reached=True, reached_date=str(rd), reached_days=(rd - date.fromisoformat(base_date)).days)
    return out


# ── v5.333(사용자 결정) KR 도달 판정 = 정규장 고가 ─────────────────────────────────────────────
# 경위: 꿈비(407400.KQ) 10-02 주봉 코호트(기준가 1,969 = 10-02 통합 종가)가 10-06 "도달"로 분류됐는데, 그 고가 2,115는
# 넥스트레이드 애프터마켓 16:04의 **1주 체결**이었다(정규장 최고 2,010 = +2.1%, 실제로는 미도달). naver 통합 일봉 고가는
# 장외 체결을 포함한다 — **고가 한 번을 보는 규칙은 장외 얇은 체결에 취약하다.** 사용자 결정: "정규장 장중 고가는 인정하고,
# 장외(프리·애프터) 체결은 판정에서 뺀다. 정규장 고가는 naver 분봉 09:00~15:30 최고가 … yfinance 사용 금지 … 분봉을 못
# 받은 날은 판정하지 않고 다음 실행 때 다시 시도. 통합 일봉 고가로 대체하는 폴백은 금지." 기준가 정의(스캔 기준일 통합 종가)는
# 그대로다.
# naver 분봉 보존: 최근 6거래일뿐(2026-10-07 실측 — 1·3·5·10·30·60분봉 모두 같음). 그래서 레코드마다 "어느 거래일까지
# 판정했나"(regular_checked_through)를 저장하고 그 다음 거래일만 받는다. 분봉이 빈 거래일에서 멈춘다(판정 보류 —
# 뒷날을 먼저 보면 도달일이 틀어진다). 서버가 6거래일 넘게 추적을 못 돌리면 그 날은 영영 못 받아 보류가 풀리지 않는다 —
# 화면에 "판정 보류(분봉 없음 MM-DD)"로 드러난다(조용히 넘기지 않는다).
# US는 그대로 yfinance 일봉 고가다(yfinance 일봉은 정규장만 담는다 — 장외 문제 없음).
REACH_RULE = "regular_high"                   # 이 규칙으로 판정한 레코드 표시 — 없는 도달 레코드는 다음 추적 때 재판정
KR_REGULAR_HM = ("090000", "153000")          # KRX 정규장(시장 시간 — 임계값 아님). 15:30 종가 단일가 봉까지 포함
MINUTE_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/minute"


def fetch_minutes(code: str, day: date) -> "list | None":
    """naver 1분봉(그날 08:00~20:00 — 통합). 실패면 None, 빈 응답이면 [](보존 기간 밖·거래 없음)."""
    import requests
    import naver_kr
    d = day.strftime("%Y%m%d")
    try:
        r = requests.get(MINUTE_URL.format(code=naver_kr.to_code(code)),
                         params={"startDateTime": d + "0800", "endDateTime": d + "2000"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200:
            return None
        j = r.json()
        return j if isinstance(j, list) else None
    except Exception:
        return None


def regular_high(bars: list) -> "float | None":
    """분봉 중 정규장(09:00:00~15:30:00) 봉의 최고가. 정규장 봉이 없으면 None."""
    lo, hi = KR_REGULAR_HM
    vals = [float(b["highPrice"]) for b in bars or []
            if lo <= str(b.get("localDateTime", ""))[8:14] <= hi and b.get("highPrice") is not None]
    return max(vals) if vals else None


def judge_kr_regular(rec: dict, daily: "pd.DataFrame | None", today: date, session_done_today: bool,
                     fetch_min=fetch_minutes, pct: float = REACH_PCT) -> dict:
    """KR 레코드 한 건 — 기준일 다음 거래일부터, 아직 판정 안 한 거래일을 순서대로 정규장 고가로 판정.
    거래일 목록은 naver 일봉 날짜(거래량 0인 날은 정규장 체결이 없으니 넘긴다). 반환 필드:
      reached·reached_date·reached_days·reached_high(정규장 고가)·reached_pct(기준가 대비 %)
      regular_checked_through(끝까지 판정한 마지막 거래일) · pending_day(분봉을 못 받아 멈춘 거래일, 없으면 None)
    오늘 봉은 정규장이 끝나기 전이면 넘어서 도달한 경우만 인정하고, 아니면 판정 완료로 치지 않는다."""
    out = {"reached": False, "regular_checked_through": rec.get("regular_checked_through"), "pending_day": None}
    if daily is None or daily.empty:
        return out
    d = daily.copy()
    d.index = pd.to_datetime(d.index).normalize()
    start = max(pd.Timestamp(rec["base_date"]), pd.Timestamp(out["regular_checked_through"] or rec["base_date"]))
    thr = reach_threshold(float(rec["base_price"]), pct)
    for ts, row in d[d.index > start].iterrows():
        day = ts.date()
        done = day < today or session_done_today
        if "Volume" in d.columns and float(row.get("Volume") or 0) <= 0:
            if done:
                out["regular_checked_through"] = str(day)
            continue                                   # 정규장 체결 없음(거래정지 등)
        bars = fetch_min(rec["code"], day)
        if not bars:                                   # None(실패)·[](없음) — 판정 보류, 다음 실행에 다시
            if done:
                out["pending_day"] = str(day)
            break
        hi = regular_high(bars)
        if hi is not None and hi >= thr * (1 - 1e-12):
            out.update(reached=True, reached_date=str(day),
                       reached_days=(day - date.fromisoformat(rec["base_date"])).days,
                       reached_high=hi, reached_pct=round((hi / float(rec["base_price"]) - 1) * 100, 2),
                       regular_checked_through=str(day))
            break
        if not done:
            break                                      # 오늘 정규장 진행 중 — 아직 미도달, 내일 다시
        out["regular_checked_through"] = str(day)
    return out


# ── v5.337(사용자 지시) 출발 이후 단계 — 관찰 → 출발 → 숨고르기 → 재출발 / 무효 ─────────────────────────────────────
# 지시 요지: "저점일지 관찰은 지금 +5% 도달을 '이미 올라버림 → 종료'로 처리한다. 그런데 WSI처럼 바닥에서 1차 출발한 뒤
# 숨고르기하는 종목은 도달 이후가 진입 자리다. … 관심 신호이고 측정 전이다. 새 임계값은 만들지 않는다."
#   출발      = 지금의 도달 판정 그대로(v5.333 — KR 정규장 고가, US 일봉 고가). 출발일 = 도달일(reached_date).
#   출발 고가 = 출발일의 판정 고가(레코드에 저장된 reached_high).
#   무효선    = 기준일부터 출발 전날까지 **통합 종가** 최고값(기준일 종가 포함, 출발일 종가 제외).
#   숨고르기  = 출발 다음 거래일부터 무효선 ≤ 종가 ≤ 출발 고가.
#   재출발    = 출발 다음 거래일 이후 처음으로 종가 > 출발 고가 → 종료.
#   무효      = 출발 다음 거래일 이후 처음으로 종가 < 무효선 → 종료.
# 판정은 **확정된 일봉 종가만**(KR naver 통합 종가, US yfinance 종가 — 분봉 사용 금지). 시간 제한 없음.
# 무효선 > 출발 고가(KR은 출발 고가가 정규장 고가라 출발 전 장외 종가가 더 높을 수 있다)면 판정하지 않고 경고로 보고한다.
STAGES = ("watch", "departed", "resting", "restart", "invalid")
TERMINAL_STAGES = ("restart", "invalid")
STAGE_FIELDS = ("stage", "invalid_line", "departure_high", "stage_date", "last_checked_date", "stage_close",
                "departure_volume", "last_volume", "stage_warning")


def is_target(r: dict) -> bool:
    """추적 대상 — 관찰 중, v5.333 규칙 전 도달(재판정), 또는 종료(재출발·무효) 전 단계의 출발 레코드."""
    if r.get("status") == "active":
        return True
    if r.get("status") != "reached":
        return False
    return r.get("reach_rule") != REACH_RULE or r.get("stage") not in TERMINAL_STAGES


def _kr_close_confirmed_hm() -> int:
    from app import KR_CLOSE_CONFIRMED_HM      # 애프터마켓 종료 후 일봉 확정 시각(KST 분) — 사본 금지(CLAUDE.md)
    return KR_CLOSE_CONFIRMED_HM


def confirmed_through(mkt: str, now_iso: str) -> "str | None":
    """지금 시각에 **종가가 확정된** 마지막 날짜(그 날짜까지의 일봉만 단계 판정에 쓴다). 장중·애프터 중 오늘 봉은 종가가
    아직 움직이므로 제외 — 종료(재출발·무효)는 되돌리지 않는 판정이라 미확정 종가로 내리면 안 된다.
    KR = KST 오늘, app.KR_CLOSE_CONFIRMED_HM(20:10) 전이면 어제까지. US = 뉴욕 오늘, 정규장 마감 16:00 전이면 어제까지.
    시각을 못 읽으면 None(제한 없음이 아니라 판정 보류 — 호출부가 단계를 계산하지 않는다)."""
    from datetime import datetime, timedelta, timezone
    try:
        n = datetime.fromisoformat(now_iso)
        if n.tzinfo is None:
            return None
    except (TypeError, ValueError):
        return None
    if mkt == "US":
        from zoneinfo import ZoneInfo
        t = n.astimezone(ZoneInfo("America/New_York"))
        done = t.hour * 60 + t.minute >= 16 * 60          # NYSE 정규장 마감(시장 시간 — 임계값 아님)
    else:
        t = n.astimezone(timezone(timedelta(hours=9)))
        done = t.hour * 60 + t.minute >= _kr_close_confirmed_hm()
    day = t.date() if done else t.date() - timedelta(days=1)
    return str(day)


def stage_info(base_date: str, reached_date: str, departure_high: "float | None", daily: "pd.DataFrame | None",
               through: "str | None") -> dict:
    """출발 레코드 한 건의 단계 — 확정 일봉(through까지) 종가로 처음부터 다시 계산한다(상태 없음 → 같은 데이터면 같은 답).
    반환: stage(departed|resting|restart|invalid)·invalid_line·departure_high·stage_date·stage_close·last_checked_date·
    departure_volume·last_volume·stage_warning(판정 못 함 사유 — 있으면 stage는 departed로 두고 판정하지 않는다)."""
    out = {"stage": "departed", "invalid_line": None, "departure_high": departure_high, "stage_date": reached_date,
           "stage_close": None, "last_checked_date": None, "departure_volume": None, "last_volume": None,
           "stage_warning": None}
    if daily is None or daily.empty or through is None:
        out["stage_warning"] = "일봉 없음" if through is not None else "확정 시각 판정 불가"
        return out
    d = daily.dropna(subset=["Close"]).copy()
    d.index = pd.to_datetime(d.index).normalize()
    d = d[d.index <= pd.Timestamp(through)]
    if len(d):
        out["last_checked_date"] = str(d.index[-1].date())
    base, dep = pd.Timestamp(base_date), pd.Timestamp(reached_date)
    if base not in d.index:
        out["stage_warning"] = f"기준일 {base_date} 일봉 없음"
        return out
    pre = d[(d.index >= base) & (d.index < dep)]["Close"]
    inv = float(pre.max())
    out["invalid_line"] = inv
    if dep in d.index and "Volume" in d.columns:
        out["departure_volume"] = float(d.loc[dep, "Volume"])
    if departure_high is None:
        out["stage_warning"] = "출발 고가 없음"
        return out
    if inv > float(departure_high):
        out["stage_warning"] = f"무효선 {inv:g} > 출발 고가 {float(departure_high):g}"
        return out
    after = d[d.index > dep]
    if not len(after):
        return out                                               # 출발 — 숨고르기 0일
    if "Volume" in after.columns:
        out["last_volume"] = float(after["Volume"].iloc[-1])
    for ts, c in after["Close"].items():
        c = float(c)
        if c > float(departure_high):
            return {**out, "stage": "restart", "stage_date": str(ts.date()), "stage_close": c}
        if c < inv:
            return {**out, "stage": "invalid", "stage_date": str(ts.date()), "stage_close": c}
    first = after.index[0]
    return {**out, "stage": "resting", "stage_date": str(first.date()), "stage_close": float(after["Close"].iloc[-1])}


# ── v5.337(사용자 지시 "추가 2") 출발일 모양 — 기록·표시 전용, **단계 판정에 쓰지 않는다** ─────────────────────────────
# "출발일 거래량 배수(50일 평균 대비), 출발일 종가 위치 = (종가−저가)/(고가−저가), 윗꼬리 비율 = (고가−max(시가,종가))/
# (고가−저가). … 기존 상수만 사용, 새 임계값 금지." 50일 = abc_screener.ABC_CONFIG["gate_break_vol_avg"](ABC 돌파봉 거래량 ÷
# 직전 50일 평균과 같은 정의 — 출발일 제외 직전 N거래일). 봉은 단계 판정과 같은 확정 일봉(KR naver 통합 OHLC — 고가·저가에
# 장외 체결 포함, US yfinance). 직전 봉이 N개 미만이면 배수는 None(짧은 평균으로 대신하지 않는다). 고가 = 저가면 위치·꼬리 None.
def _vol_avg_bars() -> int:
    import abc_screener
    return int(abc_screener.ABC_CONFIG["gate_break_vol_avg"])


def shape_lookback_days() -> int:
    """출발일 직전 N거래일 평균을 위해 기준일 앞으로 더 받을 달력 일수(주 5거래일 환산 — 휴장 여유는 fetch_daily의 +10)."""
    return -(-_vol_avg_bars() * 7 // 5)


def departure_shape(daily: "pd.DataFrame | None", reached_date: str, through: "str | None") -> dict:
    out = {"departure_vol_mult": None, "departure_close_pos": None, "departure_upper_wick": None}
    if daily is None or daily.empty or through is None or reached_date > through:
        return out                                               # 출발일 봉 미확정 — 다음 추적 때 다시
    d = daily.copy()
    d.index = pd.to_datetime(d.index).normalize()
    dep = pd.Timestamp(reached_date)
    if dep not in d.index:
        return out
    row = d.loc[dep]
    o, h, l, c = (float(row[k]) if k in d.columns else float("nan") for k in ("Open", "High", "Low", "Close"))
    if h > l:                                                    # 고가 = 저가(또는 열 없음 — nan 비교는 False)면 None
        out["departure_close_pos"] = (c - l) / (h - l)
        out["departure_upper_wick"] = (h - max(o, c)) / (h - l)
    n = _vol_avg_bars()
    prev = d[d.index < dep]["Volume"].iloc[-n:] if "Volume" in d.columns else []
    if len(prev) == n:
        avg = float(prev.mean())
        if avg > 0:
            out["departure_vol_mult"] = float(row["Volume"]) / avg
    return out


def _us_period(earliest: str, today: date) -> str:
    """기준일을 덮는 가장 짧은 yfinance period(기준일 이후 일봉만 필요)."""
    days = (today - date.fromisoformat(earliest)).days
    for p, n in (("1mo", 28), ("3mo", 88), ("6mo", 180), ("1y", 360), ("2y", 725)):
        if days <= n:
            return p
    return "5y"


def fetch_daily(records: list, today: date) -> dict:
    """**이 레코드들의 종목만** 일봉 조회 → {code: DataFrame}. KR naver(통합 시세), US yfinance 배당 미조정."""
    import harness
    import naver_kr
    if not records:
        return {}
    # v5.337: 출발일 모양(직전 50거래일 평균 거래량)을 위해 기준일 앞으로 더 받는다
    from datetime import timedelta
    earliest = str(date.fromisoformat(min(r["base_date"] for r in records)) - timedelta(days=shape_lookback_days()))
    kr = sorted({r["code"] for r in records if r.get("mkt") != "US"})
    us = sorted({r["code"] for r in records if r.get("mkt") == "US"})
    out = {}
    days = (today - date.fromisoformat(earliest)).days + 10   # 기준일 앞 여유(주말·휴장)
    for code in kr:
        try:
            df = naver_kr.fetch_history(code, days=days)
        except Exception:
            df = None
        if df is not None and not df.empty:
            out[code] = df
    if us:
        out.update(harness._fetch_us_batch(us, period=_us_period(earliest, today), auto_adjust=lp.US_AUTO_ADJUST))
    return out


def _session_done_today(now_iso: str) -> bool:
    try:
        from datetime import datetime, timedelta, timezone
        n = datetime.fromisoformat(now_iso).astimezone(timezone(timedelta(hours=9)))
        return n.strftime("%H%M%S") > KR_REGULAR_HM[1]
    except (TypeError, ValueError):
        return False


def track(records: list, today: date, now_iso: str, fetch=fetch_daily, fetch_min=fetch_minutes) -> tuple[dict, dict]:
    """활성 레코드 + **정규장 규칙으로 판정되지 않은 옛 도달 레코드**(reach_rule 없음)만 추적 → ({id: 바뀔 필드}, 집계).
    KR = 정규장 고가(judge_kr_regular), US = yfinance 일봉 고가(정규장만 담김). 옛 도달 레코드는 기준일부터 다시 판정해
    미도달이면 관찰로 되돌린다(reverted). 새 규칙으로 도달한 레코드는 다시 조회하지 않는다."""
    old_reached = [r for r in records if r.get("status") == "reached" and r.get("reach_rule") != REACH_RULE]
    staging = [r for r in records if r.get("status") == "reached" and r.get("reach_rule") == REACH_RULE and is_target(r)]
    targets = [r for r in records if r.get("status") == "active"] + old_reached + staging
    data = fetch(targets, today) if targets else {}
    done_today = _session_done_today(now_iso)
    updates, failed, reached, reverted, pending = {}, [], 0, [], []
    transitions, first_staged, warnings = [], [], []
    for r in targets:
        was_reached = r.get("status") == "reached"
        daily = data.get(r["code"])
        last = reach_info(float(r["base_price"]), r["base_date"], daily)      # 현재가(통합 종가) 표시용
        if last["last_close"] is None:
            failed.append(r["code"])
            continue
        u = {"last_close": last["last_close"], "last_date": last["last_date"], "checked_at": now_iso, "reach_rule": REACH_RULE}
        if was_reached and r.get("reach_rule") == REACH_RULE:
            # 정규장 규칙으로 이미 출발 — 도달 판정은 다시 안 하고(분봉 조회 없음) 단계만 일봉 종가로
            hit = {"reached": True, "reached_date": r.get("reached_date"), "reached_days": r.get("reached_days"),
                   "reached_high": r.get("reached_high"), "reached_pct": r.get("reached_pct")}
        elif r.get("mkt") == "US":
            hit = {"reached": last["reached"], "reached_date": last["reached_date"], "reached_days": last["reached_days"]}
            if last["reached"]:
                d = daily.copy()
                d.index = pd.to_datetime(d.index).normalize()
                hi = float(d.loc[pd.Timestamp(last["reached_date"]), "High"])
                hit.update(reached_high=hi, reached_pct=round((hi / float(r["base_price"]) - 1) * 100, 2))
            u["pending_day"] = None
        else:
            base = {**r, "regular_checked_through": None} if was_reached else r   # 옛 도달은 기준일부터 다시
            hit = judge_kr_regular(base, daily, today, done_today, fetch_min)
            u.update(regular_checked_through=hit["regular_checked_through"], pending_day=hit["pending_day"])
            if hit["pending_day"]:
                pending.append(f"{r['code']}@{hit['pending_day']}")
        tag = f"{r['code']}({r.get('label')} {r.get('tf')})"
        if hit["reached"]:
            u.update(status="reached", reached_date=hit["reached_date"], reached_days=hit["reached_days"],
                     reached_high=hit.get("reached_high"), reached_pct=hit.get("reached_pct"))
            if not was_reached:
                reached += 1
            thru = confirmed_through(r.get("mkt"), now_iso)
            si = stage_info(r["base_date"], hit["reached_date"], hit.get("reached_high"), daily, thru)
            u.update(si)
            u.update(departure_shape(daily, hit["reached_date"], thru))      # 표시 전용 — 판정은 위 si에서 끝났다
            if si["stage_warning"]:
                warnings.append(f"{tag} {si['stage_warning']}")
            prev = r.get("stage") if was_reached else "watch"
            if was_reached and r.get("stage") is None:
                first_staged.append((si["stage"], tag))
            elif si["stage"] != prev:
                transitions.append(f"{tag} {prev}→{si['stage']}")
        else:
            u.update(stage="watch", invalid_line=None, departure_high=None, stage_date=None, stage_close=None,
                     departure_volume=None, last_volume=None, stage_warning=None, last_checked_date=last["last_date"],
                     departure_vol_mult=None, departure_close_pos=None, departure_upper_wick=None)
            if was_reached:
                u.update(status="active", reached_date=None, reached_days=None, reached_high=None, reached_pct=None)
                reverted.append(tag)
        updates[r["id"]] = u
    return updates, {"active": len(targets) - len(old_reached) - len(staging), "rejudged": len(old_reached),
                     "staged": len(staging), "fetched": len(data), "reached_new": reached, "reverted": reverted,
                     "pending": pending, "failed": sorted(set(failed)), "transitions": transitions,
                     "first_staged": first_staged, "stage_warnings": warnings}


# ── v5.327(사용자 지시) 보유 추적 — "매매 기록의 보유 종목 수익률을 매일 자동 추적 — 관찰(진입 전)과 짝이 되는
# 보유 중 페이지". 대상 = 매매 기록 중 종료 안 된(sellDate 없는) 기록. 관찰과 같은 작업(매일 07:00 KST + 수동 갱신)이
# 이어서 부른다. 저장하는 것은 종목별 마지막 종가뿐 — 수익률·목표가는 화면이 매매 기록(평단·목표%)으로 계산한다.
HOLD_LOOKBACK_DAYS = 10      # 마지막 종가만 필요 — 주말·연휴를 넘길 만큼(AI 판단 어림값, 결과값엔 영향 없음)


def fetch_last_closes(items: list, today: date) -> dict:
    """**이 종목들만** 마지막 종가 → {code: {last_close, last_date}}. KR naver(통합), US yfinance 배당 미조정(저점
    기준과 같음), 코인 업비트 일봉."""
    import harness
    import naver_kr
    import upbit
    out = {}

    def put(code, df):
        if df is None or df.empty or "Close" not in df:
            return
        c = df["Close"].dropna()
        if len(c):
            out[code] = {"last_close": float(c.iloc[-1]), "last_date": str(pd.to_datetime(c.index[-1]).date())}
    kr = sorted({i["code"] for i in items if i.get("mkt") == "KR"})
    us = sorted({i["code"] for i in items if i.get("mkt") == "US"})
    coin = sorted({i["code"] for i in items if i.get("mkt") == "UPBIT"})
    for code in kr:
        try:
            put(code, naver_kr.fetch_history(code, days=HOLD_LOOKBACK_DAYS))
        except Exception:
            pass
    if us:
        for code, df in harness._fetch_us_batch(us, period="1mo", auto_adjust=lp.US_AUTO_ADJUST).items():
            put(code, df)
    for code in coin:
        put(code, upbit.fetch_candles(code, "days", count=HOLD_LOOKBACK_DAYS))
    return out


def track_holdings(trades: list, today: date, now_iso: str, prev: dict | None = None,
                   interest: list | None = None, fetch=fetch_last_closes) -> tuple[dict, dict]:
    """보유 기록(sellDate 없음)과 v5.328 관심 추적 항목의 종목만 조회 → ({code: {mkt, last_close, last_date,
    checked_at}}, 집계). 조회 실패한 종목은 이전 값을 그대로 둔다(날짜가 남아 낡은 값임을 화면이 보여준다).
    보유도 관심도 아닌 종목은 뺀다."""
    held = {}
    for t in trades or []:
        if t.get("sellDate") or not t.get("code"):
            continue
        held[t["code"]] = t.get("mkt")
    n_held = len(held)
    watch = {i["code"]: i.get("mkt") for i in interest or [] if i.get("code")}
    targets = {**watch, **held}
    got = fetch([{"code": c, "mkt": m} for c, m in targets.items()], today) if targets else {}
    prev = prev or {}
    out, failed = {}, []
    for code, mkt in targets.items():
        if code in got:
            out[code] = {"mkt": mkt, **got[code], "checked_at": now_iso}
        else:
            failed.append(code)
            if code in prev:
                out[code] = prev[code]
    return out, {"held": n_held, "interest": len(watch), "fetched": len(got), "failed": sorted(failed)}

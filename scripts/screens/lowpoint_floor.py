"""저점관리(v5.365) — 저점 히트 봉의 저가(바닥선)를 종가가 지키는지 · 깨졌다가 돌아오는지 매일 추적하는 순수 계산.

사용자 지시(2026-11-07): "눌림목 스캐너에 신규상장 탭이 있어. 그리고 저점종목도 매주, 매월 업데이트되고 있어(홈 화면에).
그 종목들을 추적해서 데이터를 쌓아줘. 즉, 무효선을 깨고 다시 올라오는지, 혹은 일정한 자리를 오르락내리락하면서 지키고
있는지, 그렇게 움직이는 종목들을 걸러주고 무효선과의 거리를 %로 표시해줘. 새로운 탭을 하나 만들자. 저점관리종목 등으로."

이름 주의: 사용자가 말한 "무효선"은 **저점 히트 봉(RSI14<30 마감 다음 양봉)의 저가** — 이 모듈은 `floor_line`(화면 "바닥선")
으로 부른다. 관찰(lowpoint_watch)의 `invalid_line`(출발 뒤 숨고르기 무효선 = 기준일~출발 전날 종가 최고)과 **다른 값**이라
같은 이름을 쓰지 않는다(CLAUDE.md "필드 이름이 의미와 어긋나면 이름을 바꾼다").

정의(전부 확정 일봉 종가 — 장중 값은 안 본다):
  히트      저점 스크린 함수 그대로(lp.resample_bars · rsi_wilder_sma · lowpoint_signal, 진행 중 봉 제외) — 주봉·월봉 각각의
            마지막 히트 중 더 최근 것(v5.366, 같은 날이면 주봉).
  바닥선    그 히트 봉 구간(주·월)의 일봉 최저가(정규장 일봉, app._downcast 정제 뒤 — 무거래일 OHLC=0 봉 제외).
  추적      히트 봉 마지막 거래일 **다음 날**부터 확정 일봉을 차례로:
    holding        종가가 바닥선 아래로 마감한 적 없음. 저가가 바닥선 +TEST_PCT% 안으로 내려왔다 종가가 지킨 날 = "지지 테스트"
                   (v5.371: 연속된 테스트 날을 한 **회차**로 묶는다 — test_episodes[{start,end,days,low,vol_avg,bounce_pct,
                   vol_dir,low_dir}]. 회차 사이 반등 = 범위를 벗어난 뒤 다음 회차 전까지 최고 종가의 바닥선 대비 %.
                   방향 = 앞 회차 대비 평균 거래량·최저가. 사용자 지시 2026-11-07 "해줘"(회차 + 거래량 방향 제안에 대한 답).)
    broken         종가 < 바닥선(이탈). 이탈일 거래량 기록, 이탈 중 최저가 추적
    spring         이탈 후 SPRING_MAX_DAYS 거래일 안에 종가 > 바닥선, 그 봉 거래량 ≥ 이탈 봉 거래량, 종가가 봉 범위 위쪽 절반
    weak_recovery  SPRING_MAX_DAYS 안에 종가 회복했지만 거래량 또는 종가 위치 조건 미달
    late_recovery  SPRING_MAX_DAYS 넘어서 종가 회복 — 스프링이 아니라 새 바닥(새로 분석)
    rebroken       회복(위 셋) 뒤 **새 바닥선**(이탈 중 최저가)을 종가로 깸 — 종료, 이후 조회만
  새 바닥선  회복 순간부터 기준선 = 이탈 구간 최저가. 거리 % = 마지막 확정 종가 / 현재 기준선 − 1.

새 임계값 출처(CLAUDE.md 출처 기록 원칙):
  SPRING_MAX_DAYS = 10 — 2026-11-07 대화에서 Claude가 "이탈 후 며칠~1~2주 안에 돌아와야 스프링"이라고 설명하고 사용자가 그
            설명으로 문제 출제·통과(훈련장 10-7). 2주 = 10거래일로 옮긴 것. 웹 검색·백테스트 근거 없음 — 재검토 대상.
  TEST_PCT = 3.0 — "일정한 자리를 오르락내리락하면서 지키는" 근접 범위. AI 판단 어림값(근거 없음) — 재검토 대상.
  회복 조건(거래량 ≥ 이탈 봉, 종가 위쪽 절반) — 같은 대화의 설명("회복 봉 거래량 ≥ 이탈 봉 거래량, 종가는 그 봉의 위쪽에서
            마감")을 옮긴 것. 근거 없음.
  PRIOR_LOW_DAYS = 120 — 전저점 창(v5.369). 사용자 지시 2026-11-07 "바닥선: 둘다표시"(히트 봉 저가 + 직전 전저점) — 계기는
            꿈비 "전저점이 1604인데 지금 1650 — 저점보다 위에 있는 거 아냐?"(8월 저점). 창 길이 120거래일(약 6개월)은 AI 판단
            어림값(그 8월 저점을 포함하는 길이) — 근거 없음, 재검토 대상.
  FAR_PCT = 30 / NEAR_PCT = 10 — 표시 묶음(v5.370). 사용자 지시 2026-11-07 "해줘. 정렬을 할수잇게. 바닥봉에서의 거리 뿐만아니라
            상태도" — 계기는 바이오비쥬(바닥선 5,630, +134%인데 "지킴"). 숫자 둘은 AI 판단 어림값 — 근거 없음, 재검토 대상.
이 상태들은 **관심 분류·표시 전용**이다 — 측정 전, 승률·우위 주장 금지.

가격 기준(v5.369, 사용자 지시 2026-11-07 "종가로" — 저점관리 탭만 정규장 종가):
  히트(언제 저점 신호가 떴나)는 저점 스크린과 같은 naver 통합 종가로 찾는다(목록과 같은 히트 날짜). 바닥선·전저점·상태·거리는
  **KR 정규장 일봉**(lp.splice_regular — 2025-03-04 NXT 개장 전 naver, 이후 yfinance auto_adjust=False)으로 계산한다.
  계기: 꿈비 11-06 통합 종가 1,770(애프터마켓 얇은 체결) vs 정규장 1,650 — 탭 거리가 실제와 달랐다.
  정규장 시세가 없는 종목은 통합 시세로 계산하고 행에 price_note를 남긴다(조용히 섞지 않는다). US는 원래 정규장(yfinance).
  다른 KR 경로(5탭·저점 스크린·관찰)는 그대로 naver 통합이다 — 이 결정은 저점관리 탭 범위만.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

import lowpoint as lp

SPRING_MAX_DAYS = 10
TEST_PCT = 3.0
PRIOR_LOW_DAYS = 120
FAR_PCT = 30.0     # 멀어짐 = 기준선 대비 +30% 이상(v5.370 — AI 판단 어림값, 근거 없음, 재검토 대상)
NEAR_PCT = 10.0    # 가까움 = 기준선 ±10% 안(v5.370 — AI 판단 어림값, 근거 없음, 재검토 대상)
STATES = ("holding", "broken", "spring", "weak_recovery", "late_recovery", "rebroken")
RECOVERED = ("spring", "weak_recovery", "late_recovery")
STATE_LABEL = {"holding": "지킴", "broken": "이탈", "spring": "스프링", "weak_recovery": "약한 회복",
               "late_recovery": "늦은 회복", "rebroken": "재이탈"}
DEFINITION = ("바닥선 = 가장 최근 저점 히트 봉(주봉·월봉 중 더 최근 — RSI14<30 마감 다음 양봉, 저점 스크린과 같은 판정)의 일봉 최저가. "
              f"확정 종가만 본다. 지킴 = 종가가 바닥선 아래로 마감한 적 없음(지지 테스트 = 저가가 바닥선 +{TEST_PCT:g}% 안까지 왔다가 "
              f"종가가 지킨 날). 이탈 = 종가 < 바닥선. 스프링 = 이탈 후 {SPRING_MAX_DAYS}거래일 안에 종가 회복 + 그 봉 거래량 ≥ 이탈 봉 + "
              f"종가가 봉 위쪽 절반. 약한 회복 = {SPRING_MAX_DAYS}거래일 안 회복이지만 거래량·종가 위치 미달. 늦은 회복 = "
              f"{SPRING_MAX_DAYS}거래일 뒤 회복(새 바닥). 회복 뒤 기준선 = 이탈 중 최저가, 그것도 종가로 깨면 재이탈. "
              f"직전봉 = 신호 봉 바로 앞 봉(RSI 30 아래로 마감한 하락 봉)의 최저가. "
              f"전저점 = 히트 봉 직전 {PRIOR_LOW_DAYS}거래일 최저가(둘 다 참고선 — 상태 판정엔 안 씀). "
              f"KR 가격은 정규장(15:30) 일봉 — 애프터마켓 체결 제외. "
              f"기간({SPRING_MAX_DAYS}·{PRIOR_LOW_DAYS}거래일)·테스트 범위({TEST_PCT:g}%)는 어림값 — 관심 분류일 뿐 측정 전.")


def _confirmed(df: "pd.DataFrame | None", through: "str | None") -> "pd.DataFrame | None":
    if df is None or df.empty or not through:
        return None
    return df[df.index <= pd.Timestamp(through)]


def latest_hit(close: pd.Series, market: str, now: datetime) -> "dict | None":
    """원본 종가(저점 스크린과 같은 입력) → 가장 최근 히트 {tf, label, bar_date, prev_bar_date, close0}.
    주봉·월봉 각각의 마지막 히트 중 bar_date가 늦은 쪽(같으면 주봉). v5.366 전엔 '주봉 먼저'라 몇 달 전 주봉 히트가
    이번 달 월봉 히트를 가렸다(하이딥 실데이터 — 7월 주봉 히트로 추적).
    market: 'kr' | 'us'(lp 규칙 — 마감 시각 판정)."""
    found = []
    for tf in ("week", "month"):
        bars = lp.drop_in_progress(lp.resample_bars(close, tf), market, now)
        n_min = max(lp.MIN_BARS[tf], 3)
        if len(bars) < n_min:
            continue
        r = lp.rsi_wilder_sma(bars["close"], lp.RSI_PERIOD)
        c = bars["close"].astype(float)
        for i in range(len(bars) - 1, n_min - 2, -1):
            r1, r2 = float(r.iloc[i - 1]), float(r.iloc[i - 2])
            if r1 != r1 or r2 != r2:
                continue
            if lp.lowpoint_signal(float(c.iloc[i]), float(c.iloc[i - 1]), r1, r2):
                found.append({"tf": tf, "label": str(bars.index[i].date()), "bar_date": str(pd.Timestamp(bars["bar_date"].iloc[i]).date()),
                              "prev_bar_date": str(pd.Timestamp(bars["bar_date"].iloc[i - 1]).date()),
                              "prev2_bar_date": str(pd.Timestamp(bars["bar_date"].iloc[i - 2]).date()), "close0": float(c.iloc[i])})
                break
    if not found:
        return None
    return max(found, key=lambda h: (h["bar_date"], h["tf"] == "week"))


def floor_of(clean: pd.DataFrame, hit: dict) -> "float | None":
    """히트 봉 구간(직전 봉 마지막 거래일 다음 날 ~ 히트 봉 마지막 거래일)의 일봉 최저가(정제된 일봉)."""
    seg = clean[(clean.index > pd.Timestamp(hit["prev_bar_date"])) & (clean.index <= pd.Timestamp(hit["bar_date"]))]["Low"].dropna()
    seg = seg[seg > 0]
    return float(seg.min()) if len(seg) else None


def classify(clean: pd.DataFrame, hit: dict, floor: float, through: str) -> dict:
    """정제된 일봉·히트·바닥선·확정 날짜 → 상태와 이벤트. 상태 없음(같은 데이터면 같은 답)."""
    d = _confirmed(clean, through)
    d = d[d.index > pd.Timestamp(hit["bar_date"])] if d is not None else None
    out = {"state": "holding", "floor_line": floor, "active_line": floor, "new_line": None, "tests": 0, "last_test_date": None,
           "test_episodes": [],
           "break_date": None, "break_close": None, "break_vol": None, "recover_date": None, "recover_days": None,
           "recover_vol_ratio": None, "rebreak_date": None, "days_tracked": 0, "max_close": None, "max_close_date": None,
           "last_close": None, "last_date": None, "dist_pct": None, "events": []}
    if d is None or d.empty:
        # 히트 봉 직후라 추적할 날이 아직 없다 — 거리는 히트 봉 마지막 종가로(v5.366 전엔 거리 비어 '—')
        h = clean[clean.index <= pd.Timestamp(hit["bar_date"])]["Close"].dropna()
        if len(h):
            lc = float(h.iloc[-1])
            out.update(last_close=lc, last_date=str(pd.Timestamp(h.index[-1]).date()),
                       dist_pct=round((lc / floor - 1) * 100, 2) if floor else None)
        return out
    state, active = "holding", floor
    brk_i = brk_vol = lowest = None
    ev = out["events"]
    for i, (ts, row) in enumerate(d.iterrows()):
        c, h, l, v = float(row["Close"]), float(row["High"]), float(row["Low"]), float(row.get("Volume") or 0)
        day = str(pd.Timestamp(ts).date())
        if out["max_close"] is None or c > out["max_close"]:
            out["max_close"], out["max_close_date"] = c, day
        if state == "rebroken":
            continue
        if state == "holding":
            if c < floor:
                state, brk_i, brk_vol, lowest = "broken", i, v, l
                out.update(break_date=day, break_close=c, break_vol=v)
                ev.append({"date": day, "ev": "break", "close": c, "line": floor})
            elif l <= floor * (1 + TEST_PCT / 100):
                out["tests"] += 1
                out["last_test_date"] = day
                eps = out["test_episodes"]
                if eps and eps[-1]["_open"]:                       # 연속된 테스트 날 = 한 회차
                    e = eps[-1]
                    e.update(end=day, days=e["days"] + 1, low=min(e["low"], l), _vsum=e["_vsum"] + v)
                else:
                    eps.append({"start": day, "end": day, "days": 1, "low": l, "_vsum": v, "bounce_pct": None, "_open": True})
            else:
                eps = out["test_episodes"]
                if eps:
                    eps[-1]["_open"] = False                       # 테스트 범위를 벗어남 — 회차 끝, 반등 높이 추적
                    b = round((c / floor - 1) * 100, 2)
                    if eps[-1]["bounce_pct"] is None or b > eps[-1]["bounce_pct"]:
                        eps[-1]["bounce_pct"] = b
            continue
        if state == "broken":
            lowest = min(lowest, l)
            if c > floor:
                days = i - brk_i
                rng = h - l
                upper = rng > 0 and (c - l) / rng >= 0.5
                ratio = (v / brk_vol) if brk_vol else None
                if days > SPRING_MAX_DAYS:
                    state = "late_recovery"
                elif ratio is not None and ratio >= 1 and upper:
                    state = "spring"
                else:
                    state = "weak_recovery"
                active = lowest
                out.update(recover_date=day, recover_days=days, recover_vol_ratio=round(ratio, 2) if ratio else None, new_line=lowest)
                ev.append({"date": day, "ev": state, "close": c, "line": lowest, "days": days,
                           "vol_ratio": round(ratio, 2) if ratio else None})
            continue
        # 회복 상태 — 새 바닥선을 종가로 깨면 재이탈(종료)
        if c < active:
            state = "rebroken"
            out["rebreak_date"] = day
            ev.append({"date": day, "ev": "rebroken", "close": c, "line": active})
    prev_e = None
    for e in out["test_episodes"]:                                 # 회차 요약 — 앞 회차 대비 거래량·저가 방향
        e["vol_avg"] = round(e.pop("_vsum") / e["days"], 1)
        e.pop("_open")
        e["vol_dir"] = None if prev_e is None else ("up" if e["vol_avg"] > prev_e["vol_avg"] else "down" if e["vol_avg"] < prev_e["vol_avg"] else "flat")
        e["low_dir"] = None if prev_e is None else ("up" if e["low"] > prev_e["low"] else "down" if e["low"] < prev_e["low"] else "flat")
        prev_e = e
    last = d.iloc[-1]
    lc = float(last["Close"])
    out.update(state=state, active_line=active, days_tracked=len(d), last_close=lc,
               last_date=str(pd.Timestamp(d.index[-1]).date()),
               dist_pct=round((lc / active - 1) * 100, 2) if active else None)
    if state == "broken" and lowest is not None:
        out["new_line"] = lowest          # 아직 회복 전 — 이탈 중 최저가(회복하면 이것이 기준선이 된다)
    return out


def prior_low(clean: pd.DataFrame, hit: dict, through: "str | None") -> dict:
    """전저점 = 히트 봉 구간 시작 전(prev_bar_date까지) PRIOR_LOW_DAYS 거래일의 일봉 최저가(정제된 일봉, 0 이하 제외).
    히트 이후 확정 종가가 처음 그 아래로 마감한 날 = prior_break_date. 참고선 — 상태 판정엔 쓰지 않는다."""
    out = {"prior_low": None, "prior_low_date": None, "prior_break_date": None, "prior_dist_pct": None}
    seg = clean[clean.index <= pd.Timestamp(hit["prev_bar_date"])]["Low"].dropna()
    seg = seg[seg > 0].iloc[-PRIOR_LOW_DAYS:]
    if seg.empty:
        return out
    pl = float(seg.min())
    out.update(prior_low=pl, prior_low_date=str(pd.Timestamp(seg.idxmin()).date()))
    d = _confirmed(clean, through)
    if d is None or d.empty:
        return out
    after = d[d.index > pd.Timestamp(hit["bar_date"])]["Close"].dropna()
    below = after[after < pl]
    if len(below):
        out["prior_break_date"] = str(pd.Timestamp(below.index[0]).date())
    last = d["Close"].dropna()
    if len(last):
        out["prior_dist_pct"] = round((float(last.iloc[-1]) / pl - 1) * 100, 2)
    return out


def pre_low(clean: pd.DataFrame, hit: dict, through: "str | None") -> dict:
    """직전 봉 저가(v5.373) = 신호 봉 바로 앞 봉(RSI14 < 30으로 마감한 하락 봉)의 일봉 최저가 — 구간 (prev2_bar_date,
    prev_bar_date]. 사용자 지시 2026-11-07 "응 표시해줘"(제안: 신호 봉 저가보다 아래인 실제 최저점을 세 번째 선으로) — 계기는
    코오롱티슈진 "전 저점은 10월 15일 13000원 부근이잖아"(바닥선 13,770 = 신호 주 저가, 10-15 13,090 = 직전 하락 주 저가).
    히트 뒤 확정 종가가 처음 그 아래로 마감한 날 = pre_break_date. 참고선 — 상태 판정엔 쓰지 않는다."""
    out = {"pre_low": None, "pre_low_date": None, "pre_break_date": None, "pre_dist_pct": None}
    if not hit.get("prev2_bar_date"):
        return out
    seg = clean[(clean.index > pd.Timestamp(hit["prev2_bar_date"])) & (clean.index <= pd.Timestamp(hit["prev_bar_date"]))]["Low"].dropna()
    seg = seg[seg > 0]
    if seg.empty:
        return out
    pl = float(seg.min())
    out.update(pre_low=pl, pre_low_date=str(pd.Timestamp(seg.idxmin()).date()))
    d = _confirmed(clean, through)
    if d is None or d.empty:
        return out
    after = d[d.index > pd.Timestamp(hit["bar_date"])]["Close"].dropna()
    below = after[after < pl]
    if len(below):
        out["pre_break_date"] = str(pd.Timestamp(below.index[0]).date())
    last = d["Close"].dropna()
    if len(last):
        out["pre_dist_pct"] = round((float(last.iloc[-1]) / pl - 1) * 100, 2)
    return out


def evaluate(df: "pd.DataFrame | None", market: str, now: datetime, through: "str | None", downcast,
             reg: "pd.DataFrame | None" = None) -> dict:
    """원본 일봉 한 종목 → {hit, …classify, …prior_low}. downcast = app._downcast(사본 금지 — 호출부가 넘긴다).
    df = 히트를 찾는 일봉(KR naver 통합 — 저점 스크린과 같은 입력). reg = 바닥선·상태·거리를 잴 정규장 일봉(없으면 df)."""
    if df is None or df.empty or not through:
        return {"status": "no_data"}
    hit = latest_hit(df["Close"].dropna(), market, now)
    if hit is None:
        return {"status": "no_hit"}
    clean = downcast(reg if reg is not None and not reg.empty else df)
    floor = floor_of(clean, hit)
    if floor is None:
        return {"status": "no_floor", "hit": hit}
    return {"status": "ok", "hit": hit, **classify(clean, hit, floor, through), **prior_low(clean, hit, through),
            **pre_low(clean, hit, through)}


# ── 대상 모으기 · 조회 · 추적(서버 작업이 부른다) ─────────────────────────────
def targets_from(watch_records: list, newlisting_rows: list, prev: dict, history: "list | None" = None) -> dict:
    """대상 종목 {code: {code,name,market,mkt,sources[]}} — 저점 관찰 레코드 전부(주·월 코호트) + 신규상장 결과 + **이전에 추적하던
    종목 전부**(사용자 지시 "데이터를 쌓아줘" — 목록에서 빠져도 추적은 계속)."""
    out = {}

    def add(code, name, market, src):
        if not code:
            return
        t = out.setdefault(code, {"code": code, "name": name or code, "market": market,
                                  "mkt": "US" if market == "US" else "KR", "sources": []})
        if name and t["name"] == code:
            t["name"] = name
        if src not in t["sources"]:
            t["sources"].append(src)

    for code, r in (prev or {}).items():
        add(code, r.get("name"), r.get("market"), None)
        out[code]["sources"] = [s for s in (r.get("sources") or []) if s]
    for r in watch_records or []:
        add(r.get("code"), r.get("name"), r.get("market"), f"저점 {'주봉' if r.get('tf') == 'week' else '월봉'}")
    for r in newlisting_rows or []:
        add(r.get("code"), r.get("name"), r.get("market"), "신규상장")
    for r in history or []:   # v5.374 — 8월 이후 과거 히트(lowpoint 게시 week.history). market "KOSPI"/"KOSDAQ"/"US"
        add(r.get("code"), r.get("name"), r.get("market"), f"저점 {'주봉' if r.get('tf') == 'week' else '월봉'}(과거)")
    return out


_US_SPAC_RE = __import__("re").compile(r"\bacquisition\s+(corp|corporation|co|company|inc|ltd|limited)\b|\bspac\b", __import__("re").I)


def is_spac(t: dict) -> bool:
    """스팩(기업인수목적회사) — v5.370 사용자 지시 2026-11-07 "그리고 스팩은 제외시켜줘".
    KR: 종목명에 "스팩" 또는 "기업인수목적"(측정 스크립트 2026-10-09_ipo_decline_recovery_exploration.py와 같은 규칙).
    US: 이름에 "Acquisition Corp/Co/Inc/Ltd…" 또는 "SPAC"(미국 스팩 관례 이름 — AI 판단 규칙, 이름에 안 드러나는 스팩은 못 거른다)."""
    name = str(t.get("name") or "")
    if t.get("mkt") == "US":
        return bool(_US_SPAC_RE.search(name))
    return "스팩" in name or "기업인수목적" in name


def drop_spacs(targets: dict) -> tuple[dict, list]:
    """→ (스팩 뺀 대상, 뺀 코드 목록). 이전에 추적하던 스팩도 여기서 빠진다(다음 저장부터 행이 사라짐)."""
    out, gone = {}, []
    for code, t in targets.items():
        (gone.append(code) if is_spac(t) else out.__setitem__(code, t))
    return out, sorted(gone)


def fetch_daily(targets: list, today: date) -> dict:
    """대상만 조회 → {code: {"raw": 히트용 일봉, "reg": 정규장 일봉, "note": 데이터 경고|None}}.
    KR: raw = naver 통합(저점 스크린과 같은 창 KR_DAYS["week"]), reg = lp.splice_regular(naver, yfinance auto_adjust=False).
    US: raw = reg = yfinance 배당 미조정(원래 정규장)."""
    import harness
    import naver_kr
    from concurrent.futures import ThreadPoolExecutor, as_completed
    kr = [t["code"] for t in targets if t["mkt"] != "US"]
    us = [t["code"] for t in targets if t["mkt"] == "US"]
    out = {}

    def one(code):
        try:
            return code, naver_kr.fetch_history(code, days=lp.KR_DAYS["week"])
        except Exception:
            return code, None
    nv = {}
    with ThreadPoolExecutor(max_workers=lp.FETCH_CONCURRENCY) as ex:
        for f in as_completed([ex.submit(one, c) for c in kr]):
            code, df = f.result()
            if df is not None and not df.empty:
                nv[code] = df
    rg = {}
    for i in range(0, len(kr), 100):
        rg.update(harness._fetch_us_batch(kr[i:i + 100], period=lp.US_PERIOD["week"], auto_adjust=False))
    for code, df in nv.items():
        r = rg.get(code)
        if r is not None:
            r = r[[c for c in ("Open", "High", "Low", "Close", "Volume") if c in r.columns]].dropna(subset=["Close"])
        reg, info = lp.splice_regular(df, r)
        out[code] = {"raw": df, "reg": reg, "note": lp.price_note(info)}
    for i in range(0, len(us), 100):
        for code, df in harness._fetch_us_batch(us[i:i + 100], period=lp.US_PERIOD["week"], auto_adjust=lp.US_AUTO_ADJUST).items():
            out[code] = {"raw": df, "reg": df, "note": None}
    return out


def track(targets: dict, prev: dict, now: datetime, downcast, confirmed_through, fetch=None) -> tuple[dict, dict]:
    """→ ({code: 행}, 집계). 행 = 대상 정보 + evaluate 결과 + first_seen·transitions(이전 행에서 이어받아 쌓는다)."""
    today = now.date()
    items = list(targets.values())
    fetch = fetch or fetch_daily              # 모듈 속성을 호출 시점에 찾는다(conftest가 네트워크 차단용으로 바꿀 수 있게)
    data = fetch(items, today) if items else {}
    rows, failed, changes = {}, [], []
    counts = {s: 0 for s in STATES}
    counts.update(no_hit=0, no_data=0)
    now_iso = now.isoformat()
    for t in items:
        p = (prev or {}).get(t["code"]) or {}
        through = confirmed_through(t["mkt"], now_iso)
        got = data.get(t["code"])
        if isinstance(got, pd.DataFrame):           # 일봉 하나만 준 호출(테스트·옛 경로) — 히트·계산 같은 일봉
            got = {"raw": got, "reg": got, "note": None}
        got = got or {}
        r = evaluate(got.get("raw"), "us" if t["mkt"] == "US" else "kr", now, through, downcast, reg=got.get("reg"))
        row = {**t, "first_seen": p.get("first_seen") or str(today), "checked_at": now_iso,
               "transitions": list(p.get("transitions") or []), "price_note": got.get("note")}
        if r["status"] != "ok":
            if r["status"] == "no_data":
                failed.append(t["code"])
                counts["no_data"] += 1
                # 조회 실패 — 이전 결과를 그대로 둔다(빈 값으로 덮지 않음)
                rows[t["code"]] = {**p, **t, "first_seen": row["first_seen"], "fetch_failed_at": now_iso} if p else {**row, "status": "no_data"}
                continue
            counts["no_hit"] += 1
            rows[t["code"]] = {**row, "status": r["status"], "hit": r.get("hit")}
            continue
        row.update(r)
        counts[r["state"]] += 1
        prev_key = (p.get("hit") or {}).get("label"), p.get("state")
        if p and prev_key != (r["hit"]["label"], r["state"]):
            note = {"at": str(today), "from": p.get("state"), "to": r["state"], "hit": r["hit"]["label"]}
            row["transitions"] = (row["transitions"] + [note])[-30:]
            changes.append(f"{t['code']} {STATE_LABEL.get(p.get('state'), p.get('state'))}→{STATE_LABEL[r['state']]}"
                           + ("" if (p.get("hit") or {}).get("label") == r["hit"]["label"] else f"(새 히트 {r['hit']['label']})"))
        rows[t["code"]] = row
    return rows, {"targets": len(items), "fetched": len(data), "failed": sorted(failed), "counts": counts, "changes": changes}

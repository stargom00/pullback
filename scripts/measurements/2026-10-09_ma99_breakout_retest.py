"""바닥형(ABC A 통과) 종목의 MA99 돌파 → MA99 되돌림 지지 진입 EV.

사전등록: docs/ma99_breakout_retest.md §1(원문)·§1-구현(실행 전 확정한 해석).
이 스크립트는 그 문서를 그대로 구현한다 — 조건·문턱·판정식을 여기서 바꾸지 않는다.
격자탐색 금지, 미달이어도 조건 수정 재실행 금지(사전등록 원문).

이벤트(사전등록 원문):
  E1 돌파      직전 20봉(ABC_CONFIG["strong_window"] 재사용) 종가가 모두 MA99 아래였다가
               처음으로 종가 > MA99
  E2 되돌림지지 E1 이후 20봉 안에서 처음으로 저가 ≤ MA99 그리고 종가 ≥ MA99인 봉
군(群):
  A 되돌림 진입  E2 봉 종가 진입, 손절 = E2 봉 저가
  B 돌파일 진입  E1 봉 종가 진입, 손절 = E1 봉 저가
  C 대조        A 진입일마다 그날 바닥형인 다른 종목을 무작위로 C_PER_EVENT개, 그날 종가 진입·손절 = 그날 저가
청산: harness.race() 2R 레이스 그대로(max_bars=60, 같은 날 손절·목표면 손절 우선).
필터: harness 표준 저유동성 컷(passes_liquidity_filter)을 세 군 진입봉에 똑같이 적용. 시총 필터 없음(§1-확인 3).
판정: A군 EV ≥ 0.15R 그리고 A−C 격차 z ≥ 1.96, 시기 반분 양쪽(A EV ≥ 0.15R·A > C), 각 군 nv ≥ 100.
      보조: A vs B(서술).

바닥형 판정: abc_screener.analyze_abc(그 시점까지 자르고 harness.clean_at_checkpoint로 정제한 일봉)
["verdict"] == "ABC" — 프로덕션 저점 유형(lowpoint_watch.setup_type)과 같은 함수·같은 정제. 600봉 미만은
analyze_abc가 "MA600 불가"를 돌려주므로 바닥형이 아니다(프로덕션과 같다).

룩어헤드 방어: 모든 판정은 그 봉까지 자른 df를 clean_at_checkpoint로 정제한 뒤에만 본다(fetch 시점 정제 금지 —
CLAUDE.md v5.242 항목). 무효봉이 하나도 없는 종목은 어느 접두 구간을 정제해도 결과가 같으므로 전체를 한 번만
정제해 잘라 쓴다(_View.dirty=False — 아래 assert로 확인). 무효봉이 있는 종목은 봉마다 접두 구간을 정제한다.

실행 시각: KST 20:10~22:30만(사전등록 [실행]). 그 밖이면 즉시 종료한다.
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-10-09_ma99_breakout_retest.py
      (MEAS_CACHE가 있으면 fetch 결과를 저장·재사용 — 같은 날 재실행용)
"""
import json
import os
import pickle
import random
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness  # noqa: E402
import abc_screener  # noqa: E402
from scanner import price_frozen_check, volume_info  # noqa: E402

# ── 사전등록 원문의 값 ────────────────────────────────────────────────
MA_N = 99                                            # 원문 "MA99"
PRE_BELOW = abc_screener.ABC_CONFIG["strong_window"]  # 원문 "직전 20봉(ABC strong_window 재사용)"
E2_WINDOW = 20                                       # 원문 "E1 이후 20봉 안에서"
VOL_AVG_N = abc_screener.ABC_CONFIG["gate_break_vol_avg"]  # 원문 공변량 "거래량 배수(50일 평균)" — ABC와 같은 50
SLOPE_LAG = 20                                       # 원문 공변량 "MA99 기울기(E1 시점 20봉 전 대비)"
MAX_BARS = 60                                        # harness.race 기본값 그대로

EV_MIN = 0.15
Z_MIN = 1.96
N_MIN = 100

# ── §1-구현에서 실행 전에 정한 값 (AI 판단 — 문서에 명시) ──────────────
C_PER_EVENT = 3          # A 이벤트 1건당 대조 표본 수. 근거 없음, 대조군 잡음을 줄이려는 어림값
C_MAX_TRIES = 400        # 한 A 이벤트에서 대조 후보를 찾는 최대 시도 수(부족분은 기록)
SEED = 20261009
KR_DAYS = 1900           # harness 90cp 표준 깊이(≈1270봉) — 600봉 판정 + 이벤트 구간 확보
MIN_MEDIAN_BARS = 1000   # fetch 깊이 하드 체크(중앙값 봉수가 이 미만이면 실패)

# 실행 시각 창(KST, 사전등록 [실행]) — app.KR_CLOSE_CONFIRMED_HM(20:10)과 미장 개장(22:30)
RUN_WINDOW_KST = ((20, 10), (22, 30))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "2026-10-09_ma99_breakout_retest.results.json")


def check_run_window():
    kst = datetime.now(timezone(timedelta(hours=9)))
    hm = (kst.hour, kst.minute)
    lo, hi = RUN_WINDOW_KST
    if not (lo <= hm < hi):
        raise SystemExit(f"[중단] 실행 창 밖: 지금 {kst:%Y-%m-%d %H:%M} KST — "
                         f"{lo[0]:02d}:{lo[1]:02d}~{hi[0]:02d}:{hi[1]:02d} KST에만 실행한다(사전등록 [실행]).")


# ══════════════════════════════════════════════════════════════════════
# 데이터
# ══════════════════════════════════════════════════════════════════════
def load_data():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    data, kr_u, _ = harness.fetch_universe_data(markets=("kr",), kr_days=KR_DAYS)
    blob = {"data": data, "kr_u": dict(kr_u), "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


class _View:
    """한 종목의 '그 봉까지 자르고 정제한' 일봉 접근자.

    prefix(j)  = clean_at_checkpoint(raw.iloc[:j+1]) — j봉 시점에 프로덕션이 봤을 데이터
    valid(j)   = j봉이 정제 후에도 남는가(무효봉이면 False — 그 봉에선 아무 판정도 하지 않는다)
    """

    def __init__(self, raw: pd.DataFrame):
        self.raw = raw
        self.full = harness.clean_at_checkpoint(raw)
        # 무효봉이 하나도 없으면 정제는 행을 안 바꾼다(float32 캐스트만) → 접두 구간 정제 = 전체 정제의 접두.
        self.dirty = not (len(self.full) == len(raw) and self.full.index.equals(raw.index))
        self._cache = {}

    def prefix(self, j: int) -> pd.DataFrame:
        if not self.dirty:
            return self.full.iloc[:j + 1]
        p = self._cache.get(j)
        if p is None:
            p = harness.clean_at_checkpoint(self.raw.iloc[:j + 1])
            self._cache[j] = p
        return p

    def valid(self, j: int) -> bool:
        if not self.dirty:
            return True
        p = self.prefix(j)
        return len(p) > 0 and p.index[-1] == self.raw.index[j]

    def future(self, j: int) -> pd.DataFrame:
        """j봉 다음부터의 **유효** 봉(각 봉이 자기 시점 정제에서 남는 봉만). 레이스 입력."""
        if not self.dirty:
            return self.full.iloc[j + 1:]
        keep = [k for k in range(j + 1, len(self.raw)) if self.valid(k)]
        return self.raw.iloc[keep]


def is_bottom(p: pd.DataFrame) -> bool:
    return abc_screener.analyze_abc(p)["verdict"] == "ABC"


def e1_at_end(p: pd.DataFrame) -> bool:
    """p의 마지막 봉이 E1인가: 직전 PRE_BELOW봉 종가가 각 봉의 MA99보다 모두 작고, 마지막 봉 종가 > MA99."""
    if len(p) < MA_N + PRE_BELOW:
        return False
    c = p["Close"].astype(float)
    ma = c.iloc[-(MA_N + PRE_BELOW):].rolling(MA_N).mean().iloc[-(PRE_BELOW + 1):]
    tail = c.iloc[-(PRE_BELOW + 1):]
    if ma.isna().any():
        return False
    return bool(tail.iloc[-1] > ma.iloc[-1] and (tail.iloc[:-1] < ma.iloc[:-1]).all())


def e1_candidates_fast(full: pd.DataFrame):
    """무효봉 없는 종목용 벡터화 E1 탐지 — e1_at_end와 같은 정의(아래 self-check가 대조)."""
    c = full["Close"].astype(float)
    ma = c.rolling(MA_N).mean()
    below = (c < ma).astype(float)
    below_prev = below.shift(1).rolling(PRE_BELOW).sum() == PRE_BELOW
    hit = (c > ma) & below_prev
    return [i for i, h in enumerate(hit.to_numpy()) if h]


def covariates(p: pd.DataFrame) -> dict:
    """기록 전용 공변량(판정 반영 금지). p = E1 봉까지 정제한 df."""
    c, v = p["Close"].astype(float), p["Volume"].astype(float)
    prev = v.iloc[-VOL_AVG_N - 1:-1]
    avg = float(prev.mean()) if len(prev) else 0.0
    ma = c.rolling(MA_N).mean()
    m0 = float(ma.iloc[-1])
    m20 = float(ma.iloc[-1 - SLOPE_LAG]) if len(ma) > SLOPE_LAG else float("nan")
    return {"e1_vol_mult50": (float(v.iloc[-1]) / avg) if avg > 0 else None,
            "ma99_slope20": (m0 / m20 - 1) if m20 == m20 and m20 > 0 else None}


def liquid(p: pd.DataFrame) -> bool:
    """harness 표준 저유동성 컷(KR 일평균 거래대금 3억 미만·가격고정 제외)을 진입봉 시점 정제 df로 판정.
    §1-확인 3(사용자 결정 2026-10-09 "harness 표준 저유동성 필터를 세 군 동일하게 적용"). hit 딕셔너리 필드는
    프로덕션 analyze_*와 같은 함수(scanner.volume_info·price_frozen_check)로 만든다 — 재구현 금지."""
    c, h, lo, v = p["Close"], p["High"], p["Low"], p["Volume"]
    hit = {**volume_info(float(c.iloc[-1]), v), **price_frozen_check(c, h, lo, v)}
    return harness.passes_liquidity_filter(hit, True)


def run_race(view: _View, j: int, p: pd.DataFrame, stats: Counter, group: str):
    if not liquid(p):
        stats[f"{group.lower()}_illiquid"] += 1
        return None                                   # 저유동성 — 그 군에서 제외
    entry, stop = float(p["Close"].iloc[-1]), float(p["Low"].iloc[-1])
    if not entry > stop:
        stats[f"{group.lower()}_zero_risk"] += 1
        return None                                   # 손절폭 0 — 레이스 불가, 별도 집계
    fut = view.future(j)
    assert len(fut) == 0 or fut.index[0] > p.index[-1], "future lookahead"
    outcome, r = harness.race(entry, stop, fut, MAX_BARS)
    return {"entry": entry, "stop": stop, "risk_pct": (entry - stop) / entry * 100,
            "outcome": outcome, "r": r}


# ══════════════════════════════════════════════════════════════════════
# 이벤트 추출
# ══════════════════════════════════════════════════════════════════════
def scan_ticker(t, view: _View, stats: Counter):
    raw = view.raw
    n = len(raw)
    if view.dirty:
        stats["dirty_tickers"] += 1
        cands = [j for j in range(MA_N + PRE_BELOW - 1, n) if view.valid(j) and e1_at_end(view.prefix(j))]
    else:
        cands = e1_candidates_fast(view.full)
    events = []
    for j in cands:
        p = view.prefix(j)
        assert p.index[-1] == raw.index[j], f"lookahead/align {t} {raw.index[j]}"
        assert e1_at_end(p), f"E1 fast/slow mismatch {t} {raw.index[j]}"
        stats["e1_all"] += 1
        if not is_bottom(p):
            continue
        stats["e1_bottom"] += 1
        e1_date = p.index[-1]
        ev = {"ticker": t, "e1_date": str(e1_date.date()), **covariates(p)}
        ev["B"] = run_race(view, j, p, stats, "B")
        # E2: E1 다음 유효봉부터 정제 기준 20봉 안
        ev["A"] = None
        ev["e2_date"] = None
        ev["e1_e2_gap"] = None
        for k in range(j + 1, n):
            if not view.valid(k):
                continue
            q = view.prefix(k)
            if e1_date not in q.index:
                stats["e2_chain_truncated"] += 1      # 정제 갈래B가 E1을 잘라냄 — 이벤트 체인 종료
                break
            gap = len(q) - 1 - q.index.get_loc(e1_date)
            if gap > E2_WINDOW:
                break
            ma = float(q["Close"].astype(float).iloc[-MA_N:].mean())
            lo, cl = float(q["Low"].iloc[-1]), float(q["Close"].iloc[-1])
            if lo <= ma and cl >= ma:
                ev["e2_date"] = str(q.index[-1].date())
                ev["e1_e2_gap"] = gap
                ev["A"] = run_race(view, k, q, stats, "A")
                stats["e2_found"] += 1
                break
        events.append(ev)
    return events


def draw_controls(views, events, rng: random.Random, stats: Counter):
    """A 진입일마다 그날 바닥형인 다른 종목을 C_PER_EVENT개(무작위·비복원) 뽑아 같은 레이스."""
    tickers = sorted(views)
    pos_cache = {}
    out = []
    for ev in events:
        if ev["A"] is None:
            continue
        d = pd.Timestamp(ev["e2_date"])
        pool = [u for u in tickers if u != ev["ticker"]]
        rng.shuffle(pool)
        got, tries = 0, 0
        for u in pool:
            if got >= C_PER_EVENT or tries >= C_MAX_TRIES:
                break
            v = views[u]
            key = (u, d)
            if key not in pos_cache:
                pos_cache[key] = v.raw.index.get_loc(d) if d in v.raw.index else None
            j = pos_cache[key]
            if j is None or not v.valid(j):
                continue
            tries += 1
            p = v.prefix(j)
            if len(p) < abc_screener._min_bars() or not is_bottom(p):
                continue
            res = run_race(v, j, p, stats, "C")
            if res is None:
                continue                              # 저유동성·손절폭 0 — 대조군에서도 빼고 계속 찾는다
            out.append({"ticker": u, "date": str(d.date()), "for": f"{ev['ticker']}@{ev['e2_date']}", "C": res})
            got += 1
        if got < C_PER_EVENT:
            stats["c_shortfall"] += C_PER_EVENT - got
    return out


# ══════════════════════════════════════════════════════════════════════
# 집계·판정
# ══════════════════════════════════════════════════════════════════════
def summarize(rows):
    return harness.ev_summary([(r["outcome"], r["r"]) for r in rows])


def verdict(sum_a, sum_c, halves):
    z, _ = harness.ev_gap_zscore(sum_c, sum_a)        # z > 0 = A가 C보다 큼
    checks = {
        "ev_a_ge_min": sum_a["ev_R"] is not None and sum_a["ev_R"] >= EV_MIN,   # A군 절대 EV
        "z_ge_min": z is not None and z >= Z_MIN,                                # A−C 격차 z(§1-확인 1)
        "halves_hold": all(h["A"]["ev_R"] is not None and h["C"]["ev_R"] is not None
                           and h["A"]["ev_R"] >= EV_MIN and h["A"]["ev_R"] > h["C"]["ev_R"]
                           for h in halves.values()),
        "n_each_ge_min": None,                        # main()에서 채움(B 포함)
    }
    return z, checks


def main():
    check_run_window()
    t0 = time.time()
    blob = load_data()
    data = {t: df for t, df in blob["data"].items() if harness.is_kr_ticker(t)}
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    bars = sorted(len(df) for df in data.values())
    med = bars[len(bars) // 2] if bars else 0
    assert med >= MIN_MEDIAN_BARS, f"fetch 깊이 부족: 중앙값 {med}봉 < {MIN_MEDIAN_BARS}"

    stats = Counter()
    views = {t: _View(df) for t, df in data.items()}
    events = []
    for i, (t, v) in enumerate(sorted(views.items())):
        events.extend(scan_ticker(t, v, stats))
        if (i + 1) % 200 == 0:
            print(f"[scan] {i+1}/{len(views)} events={len(events)} {time.time()-t0:.0f}s", flush=True)

    rng = random.Random(SEED)
    controls = draw_controls(views, events, rng, stats)

    a_rows = [dict(e["A"], date=e["e2_date"]) for e in events if e["A"] is not None]
    b_rows = [dict(e["B"], date=e["e1_date"]) for e in events if e["B"] is not None]
    c_rows = [dict(c["C"], date=c["date"]) for c in controls]

    # 하드 실패: 어느 군이든 표본 0이면 측정 자체가 성립하지 않는다(CLAUDE.md "표본 0은 하드 실패").
    for name, rows in (("A", a_rows), ("B", b_rows), ("C", c_rows)):
        assert rows, f"{name}군 표본 0 — 파이프라인 점검 필요(판정 불가)"

    sum_a, sum_b, sum_c = summarize(a_rows), summarize(b_rows), summarize(c_rows)
    for name, s in (("A", sum_a), ("B", sum_b), ("C", sum_c)):
        assert s["nv"] > 0, f"{name}군 유효표본(nv) 0"

    # 시기 반분: A 진입일 중앙값으로 자른다. C는 A 진입일과 같은 날짜라 같은 칼로 갈린다. B는 E1 날짜로.
    a_dates = sorted(r["date"] for r in a_rows)
    split = a_dates[len(a_dates) // 2]
    halves = {}
    for label, f in (("early", lambda d: d < split), ("late", lambda d: d >= split)):
        halves[label] = {g: summarize([r for r in rows if f(r["date"])])
                         for g, rows in (("A", a_rows), ("B", b_rows), ("C", c_rows))}

    z_ac, checks = verdict(sum_a, sum_c, halves)
    checks["n_each_ge_min"] = all(s["nv"] >= N_MIN for s in (sum_a, sum_b, sum_c))
    adopted = all(checks.values())
    z_ab, _ = harness.ev_gap_zscore(sum_b, sum_a)     # 보조: z > 0 = A가 B보다 큼(서술)

    result = {
        "run_stamp": stamp, "fetched_at": blob.get("fetched_at"),
        "n_tickers": len(data), "bars_median": med, "elapsed_s": round(time.time() - t0),
        "params": {"MA_N": MA_N, "PRE_BELOW": PRE_BELOW, "E2_WINDOW": E2_WINDOW, "MAX_BARS": MAX_BARS,
                   "EV_MIN": EV_MIN, "Z_MIN": Z_MIN, "N_MIN": N_MIN, "C_PER_EVENT": C_PER_EVENT,
                   "C_MAX_TRIES": C_MAX_TRIES, "SEED": SEED, "KR_DAYS": KR_DAYS},
        "stats": dict(stats),
        "summary": {"A": sum_a, "B": sum_b, "C": sum_c},
        "z_A_vs_C": z_ac, "z_A_vs_B": z_ab,
        "half_split_date": split, "halves": halves,
        "checks": checks, "adopted": adopted,
        "events": events, "controls": controls,
    }
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("summary", "z_A_vs_C", "z_A_vs_B", "half_split_date",
                                             "halves", "checks", "adopted", "stats")},
                     ensure_ascii=False, indent=1, default=str))
    print(f"[done] {OUT}")


if __name__ == "__main__":
    main()

"""600일선 돌파 장대양봉(A) vs 600일선 아래 장대양봉(C) — 다음 날 시가 진입 EV.

사전등록: docs/ma600_bull_candle_breakout.md §1(원문)·§1-구현·§1-부록. 저점 출발 계열 셋 + 600·99 → 같은 아이디어 다섯 번째 검정,
Bonferroni 5 — z 문턱 2.58(2026-10-09 사용자 지시, 실행 전 2.50에서 수정). 99일선 측정(2026-10-09_ma99_bull_candle_breakout.py)은
이 모듈을 import해 선 길이(MA_N·MIN_BARS·OTHER_N·OUT)만 바꾼다.
유니버스(KIND 코스피+코스닥 − 관리종목)·fetch·저유동성 컷은 저점 출발 스크립트, 장대양봉 정의는 기준양봉 스크립트에서 import한다(사본 금지).
데이터 = naver 일봉 **3,000일**(실행 전 수정 — 1,900일로는 600일선 이벤트 구간이 약 2.8년뿐). 1,900일 캐시와 다른 경로에 저장.

이벤트(원문): 장대양봉(종가 ≥ 전일 종가 × 1.15 그리고 종가 > 시가)이면서 전일 종가 < 전일 MA600 —
  A = 당일 종가 > 당일 MA600, C = 당일 종가 ≤ 당일 MA600. MA600 = 정제 일봉 종가 단순이동평균 600.
진입: 장대양봉 다음 유효봉 시가. 손절 = 장대양봉 시가(harness.race — 저가 ≤ 손절 −1R, 고가 ≥ 2R, 60봉, 같은 날 손절 우선).
진입가 ≤ 장대양봉 시가면 제외. 같은 종목에서 포지션 보유 중(진입일~청산일)에 나온 새 이벤트는 건너뛴다(A·C 공통).
판정: A EV ≥ 0.15R · A−C z ≥ 2.58 · 반분 양쪽(A ≥ 0.15R·A > C) · A·C nv ≥ 100. A(또는 C) nv < 100이면 "판정 불가".
룩어헤드: 모든 판정은 그 봉까지 자르고 정제한 df(harness.CleanView.prefix)에서만 본다.
실행 시각: harness.check_run_window(["KR"]). 실행은 해석 기본값 사용자 확인 뒤.
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-10-09_ma600_bull_candle_breakout.py  (결과 JSON 커밋 안 함)
"""
import importlib.util
import json
import os
import random
import time
from collections import Counter, defaultdict
from statistics import median

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bc = _load("bull_candle_departure", "2026-10-09_bull_candle_departure_entry.py")   # 장대양봉 정의
prev = bc.prev                                                                       # 데이터·저유동성·요약(저점 출발)
harness = prev.harness

# ── 사전등록 원문의 값 ────────────────────────────────────────────────
MA_N = 600                       # "600일선 = naver 일봉(공식 종가) 기준 단순이동평균 600"
MIN_BARS = MA_N + 1              # 전일 MA600까지 필요 — 정제 이력 601봉(§1-확인 4)
OTHER_N = 99                     # 기록 전용 — 같은 장대양봉이 다른 선(99) 기준으로도 A인지
DATA_DAYS = 3000                 # "같은 유니버스로 3,000일 이력을 새로 받는다"(2026-10-09, 실행 전)
D_FWD = 63                       # 기록 전용 "장대양봉 종가 대비 D+63 종가 변화율"
D_BUCKET_EDGES = [-30, -10, 0, 10, 30, 100]   # 원문 구간(%): −30 이하 / −30~−10 / −10~0 / 0~+10 / +10~+30 / +30~+100 / +100 이상
EV_MIN = 0.15
Z_MIN = 2.58                     # "Bonferroni 5를 적용한다. 두 가설 모두 z ≥ 2.58"(2026-10-09, 실행 전 2.50에서 수정)
N_MIN = 100
MAX_BARS = prev.MAX_BARS         # 60 — 이전 측정 규칙
MA_CHECK_N = 3                   # "무작위 3종목 3날짜에 대해 수기 계산과 대조"
RUN_MARKETS = ["KR"]
SEED = 20261009

OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def ma_at(p, back: int = 0, n: "int | None" = None) -> float:
    """정제 prefix p의 끝에서 back봉 전 시점의 n일선(그 봉 포함 n개 종가 평균, float64). n 기본 = MA_N."""
    n = n or MA_N
    c = p["Close"].astype(float)
    end = len(c) - back
    assert end >= n, f"MA{n} 이력 부족({end}봉) — 부분 창 평균 금지"
    return float(c.iloc[end - n:end].mean())


def classify(p, n: "int | None" = None) -> "str | None":
    """p = 장대양봉 후보 봉까지 정제한 df. 'A' / 'C' / None(장대양봉 아님·전일이 이미 선 위·이력 부족). n 기본 = MA_N."""
    n = n or MA_N
    if len(p) < n + 1:
        return None
    pc, o, c = float(p["Close"].iloc[-2]), float(p["Open"].iloc[-1]), float(p["Close"].iloc[-1])
    if not bc.is_bull_candle(pc, o, c):
        return None
    if not pc < ma_at(p, 1, n):
        return None
    return "A" if c > ma_at(p, 0, n) else "C"


def forward_63(view, k: int, close_k: float) -> dict:
    """기록 전용: 장대양봉(원본 k) 종가 대비 이후 D_FWD번째 유효봉 종가 변화율과 그 사이 최고 고가·최저 저가(%). 데이터가 모자라면 incomplete."""
    fut = view.future(k)
    if len(fut) < D_FWD:
        return {"d63_incomplete": True}
    w = fut.iloc[:D_FWD]
    return {"d63_incomplete": False, "d63_pct": (float(w["Close"].iloc[-1]) / close_k - 1) * 100,
            "d63_max_pct": (float(w["High"].max()) / close_k - 1) * 100, "d63_min_pct": (float(w["Low"].min()) / close_k - 1) * 100}


def candidate_bars(view) -> list:
    """싼 1차 거름: 원본에서 OHLC > 0인 봉 중, 직전의 OHLC > 0 봉 종가 대비 장대양봉인 봉. 정제 prefix는 원본의 부분이고 직전 유효봉
    종가는 바로 이 값이거나(그 봉이 남음) prefix가 더 짧아져 후보가 사라지는 쪽뿐이라 이 거름은 정확 판정의 상위집합이다."""
    raw = view.raw
    o, h, lo, c = (raw[k].astype(float).values for k in ("Open", "High", "Low", "Close"))
    ok = (o > 0) & (h > 0) & (lo > 0) & (c > 0)
    out, last = [], None
    for k in range(len(raw)):
        if not ok[k]:
            continue
        if last is not None and bc.is_bull_candle(c[last], o[k], c[k]) and k >= MIN_BARS - 1:
            out.append(k)
        last = k
    return out


def exit_index(entry: float, stop: float, fut, outcome: str) -> int:
    """레이스가 끝난 봉의 fut 위치(0 = 진입봉). harness.race를 앞 n봉으로 잘라 다시 불러 처음 결론이 나는 n을 찾는다(재구현 아님).
    unresolved = 60번째 봉, insufficient = 데이터 끝."""
    if outcome == "unresolved":
        return MAX_BARS - 1
    if outcome == "insufficient":
        return len(fut) - 1
    for n in range(1, MAX_BARS + 1):
        if harness.race(entry, stop, fut.iloc[:n], n)[0] in ("stop", "target"):
            return n - 1
    raise AssertionError("exit not found")


def below_run(p) -> "tuple[int, bool]":
    """기록용: 장대양봉 전일부터 거슬러 종가 < MA600이 연속된 유효봉 수. MA600을 계산할 수 있는 첫 봉에 닿으면 (수, True=하한)."""
    c = p["Close"].astype(float)
    ma = c.rolling(MA_N).mean()
    n = 0
    for i in range(len(c) - 2, -1, -1):
        if ma.iloc[i] != ma.iloc[i]:
            return n, True
        if not c.iloc[i] < ma.iloc[i]:
            return n, False
        n += 1
    return n, True


def scan_ticker(t, view, stats: Counter) -> list:
    """한 종목의 A·C 이벤트를 시간순으로 — 포지션 보유 중 이벤트는 건너뛴다."""
    out = []
    busy_until = None                                     # 원본 인덱스(포함)
    for k in candidate_bars(view):
        if not view.valid(k):
            continue
        p = view.prefix(k)
        if len(p) < MIN_BARS:
            continue
        g = classify(p)
        if g is None:
            continue
        stats[f"{g}_signal"] += 1
        if busy_until is not None and k <= busy_until:
            stats[f"{g}_skipped_in_position"] += 1
            continue
        if not prev.liquid(p):
            stats[f"{g}_illiquid"] += 1
            continue
        fut = view.future(k)
        if len(fut) == 0:
            stats[f"{g}_no_next_bar"] += 1
            continue
        assert fut.index[0] > p.index[-1], "future lookahead"
        entry, stop = float(fut["Open"].iloc[0]), float(p["Open"].iloc[-1])
        if not entry > stop:
            stats[f"{g}_entry_not_above_stop"] += 1
            continue
        outcome, r = harness.race(entry, stop, fut, MAX_BARS)
        x = exit_index(entry, stop, fut, outcome)
        busy_until = int(view.raw.index.get_loc(fut.index[x]))
        run, capped = below_run(p)
        other = classify(p, OTHER_N) if len(p) >= OTHER_N + 1 else None
        out.append({"also_A_other_line": other == "A", "other_line_class": other,
                    **forward_63(view, k, float(p["Close"].iloc[-1])),"ticker": t, "group": g, "bull_date": str(p.index[-1].date()), "date": str(fut.index[0].date()),
                    "exit_date": str(fut.index[x].date()), "entry": entry, "stop": stop,
                    "risk_pct": (entry - stop) / entry * 100, "outcome": outcome, "r": r,
                    "below_ma_bars": run, "below_ma_capped": capped,
                    "ma_line_n": MA_N, "ma_line_prev": ma_at(p, 1), "ma_line": ma_at(p, 0), "prev_close": float(p["Close"].iloc[-2]),
                    "close": float(p["Close"].iloc[-1])})
    return out


def ma_hand_check(data: dict, views: dict, rng: random.Random) -> list:
    """원문: 무작위 3종목 3날짜 — ma_at(pandas 평균)과 손 계산(파이썬 합 ÷ 600)을 대조. 0.01% 넘게 다르면 하드 실패."""
    pool = sorted(t for t, df in data.items() if len(views[t].full) >= max(MIN_BARS, MA_N + MA_CHECK_N) + 10)
    rows = []
    for t in rng.sample(pool, MA_CHECK_N):
        v = views[t]
        ks = [k for k in range(len(v.raw)) if v.valid(k) and len(v.prefix(k)) >= MA_N]
        for k in sorted(rng.sample(ks, MA_CHECK_N)):
            p = v.prefix(k)
            closes = [float(x) for x in p["Close"].tolist()[-MA_N:]]
            hand = 0.0
            for x in closes:
                hand += x
            hand /= MA_N
            got = ma_at(p, 0)
            assert abs(got - hand) <= abs(hand) * 1e-4, f"MA{MA_N} 대조 실패 {t} {p.index[-1].date()}: {got} vs {hand}"
            rows.append({"ticker": t, "date": str(p.index[-1].date()), "ma_line_n": MA_N, "ma_line": got, "hand": hand,
                         "first_close": closes[0], "last_close": closes[-1]})
    return rows


def verdict(sum_a, sum_c, z, halves):
    if (sum_a["nv"] or 0) < N_MIN or (sum_c["nv"] or 0) < N_MIN:
        return "판정 불가", None
    checks = {"ev_a_ge_min": sum_a["ev_R"] is not None and sum_a["ev_R"] >= EV_MIN,
              "z_ge_min": z is not None and z >= Z_MIN,
              "halves_hold": all(v["A"]["ev_R"] is not None and v["C"]["ev_R"] is not None and v["A"]["ev_R"] >= EV_MIN
                                 and v["A"]["ev_R"] > v["C"]["ev_R"] for v in halves.values()),
              "n_each_ge_min": True}
    return ("통과" if all(checks.values()) else "기각"), checks


def _dist(xs):
    xs = sorted(xs)
    if not xs:
        return None
    return {"n": len(xs), "median": median(xs), "p25": xs[len(xs) // 4], "p75": xs[len(xs) * 3 // 4],
            "p90": xs[int(len(xs) * 0.9)], "max": xs[-1], "min": xs[0]}


def d63_buckets(xs) -> dict:
    """원문 구간별 건수. 경계: "−30 이하"(x ≤ −30), 가운데는 (아래, 위](예: −30 < x ≤ −10), "+100 이상"(x ≥ 100) — 30~100은 (30, 100)."""
    e = D_BUCKET_EDGES
    labels = ["<=-30", "-30~-10", "-10~0", "0~+10", "+10~+30", "+30~+100", ">=+100"]
    out = dict.fromkeys(labels, 0)
    for x in xs:
        if x <= e[0]:
            out[labels[0]] += 1
        elif x >= e[-1]:
            out[labels[-1]] += 1
        else:
            for i in range(1, len(e)):
                if x <= e[i]:
                    out[labels[i]] += 1
                    break
    assert sum(out.values()) == len(xs)
    return out


def d63_summary(rows) -> dict:
    """기록 전용 D+63 요약 — 상승·하락 비율, 중앙값·25%·75%, 구간별 건수, 63일 안 최고·최저 % 중앙값, 미완(63거래일 전 데이터 끝) 건수."""
    done = [r for r in rows if not r["d63_incomplete"]]
    xs = sorted(r["d63_pct"] for r in done)
    out = {"n": len(rows), "n_complete": len(done), "n_incomplete": len(rows) - len(done)}
    if not xs:
        return out
    out.update({"up_rate": sum(1 for x in xs if x > 0) / len(xs), "down_rate": sum(1 for x in xs if x < 0) / len(xs),
                "median": median(xs), "p25": xs[len(xs) // 4], "p75": xs[len(xs) * 3 // 4],
                "buckets": d63_buckets(xs),
                "max_pct_median": median(r["d63_max_pct"] for r in done), "min_pct_median": median(r["d63_min_pct"] for r in done)})
    return out


def load_data():
    """저점 출발 스크립트의 load_data(같은 유니버스·같은 동시성)를 조회 기간만 DATA_DAYS로 바꿔 부른다. MEAS_CACHE는 1,900일 캐시와 다른 경로."""
    saved = prev.KR_DAYS
    prev.KR_DAYS = DATA_DAYS
    try:
        return prev.load_data()
    finally:
        prev.KR_DAYS = saved


def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    blob = load_data()
    span = max(len(df) for df in blob["data"].values())
    assert span > 1900, f"3,000일 데이터가 아니다(최대 {span}봉) — 1,900일 캐시를 잘못 넘겼는지 확인"
    data = blob["data"]
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    stats = Counter()
    views = {t: harness.CleanView(df) for t, df in data.items()}
    rng = random.Random(SEED)
    hand = ma_hand_check(data, views, rng)
    print(f"[ma{MA_N} hand check] {hand}", flush=True)
    stats["tickers"] = len(data)
    stats["tickers_ge_min_bars_valid"] = sum(1 for v in views.values() if len(v.full) >= MIN_BARS)

    rows = []
    for i, (t, v) in enumerate(sorted(views.items()), 1):
        rows.extend(scan_ticker(t, v, stats))
        if i % 500 == 0:
            print(f"[scan] {i}/{len(views)} rows={len(rows)} {time.time() - t0:.0f}s", flush=True)
    a_rows = [r for r in rows if r["group"] == "A"]
    c_rows = [r for r in rows if r["group"] == "C"]
    sum_a, sum_c = prev.summarize(a_rows), prev.summarize(c_rows)
    z, _ = harness.ev_gap_zscore(sum_c, sum_a) if a_rows and c_rows else (None, False)
    split = sorted(r["date"] for r in a_rows)[len(a_rows) // 2] if a_rows else None
    halves = ({"early": {"A": prev.summarize([r for r in a_rows if r["date"] < split]), "C": prev.summarize([r for r in c_rows if r["date"] < split])},
               "late": {"A": prev.summarize([r for r in a_rows if r["date"] >= split]), "C": prev.summarize([r for r in c_rows if r["date"] >= split])}}
              if split else None)
    v, checks = verdict(sum_a, sum_c, z, halves) if halves else ("판정 불가", None)

    years = defaultdict(lambda: {"A": 0, "C": 0})
    for r in rows:
        years[r["date"][:4]][r["group"]] += 1
    result = {
        "run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
        "universe": len(blob.get("names") or {}), "fetched": len(data), "failed": len(blob.get("failed") or []),
        "elapsed_s": round(time.time() - t0),
        "params": {"MA_N": MA_N, "MIN_BARS": MIN_BARS, "OTHER_N": OTHER_N, "DATA_DAYS": DATA_DAYS, "D_FWD": D_FWD,
                   "BULL_CLOSE_RATIO": bc.BULL_CLOSE_RATIO, "EV_MIN": EV_MIN, "Z_MIN": Z_MIN, "N_MIN": N_MIN,
                   "MAX_BARS": MAX_BARS, "SEED": SEED},
        "ma_hand_check": hand, "stats": dict(stats),
        "A": sum_a, "C": sum_c, "z_A_vs_C": z, "half_split_date": split, "halves": halves,
        "verdict": v, "checks": checks,
        "record_only_by_year": dict(sorted(years.items())),
        "record_only_below_ma_bars": {g: {"dist": _dist([r["below_ma_bars"] for r in rows if r["group"] == g]),
                                          "capped_n": sum(1 for r in rows if r["group"] == g and r["below_ma_capped"])}
                                      for g in ("A", "C")},
        "record_only_risk_pct": {g: _dist([r["risk_pct"] for r in rows if r["group"] == g]) for g in ("A", "C")},
        "record_only_also_A_other_line": {g: sum(1 for r in rows if r["group"] == g and r["also_A_other_line"]) for g in ("A", "C")},
        "record_only_d63": {g: d63_summary([r for r in rows if r["group"] == g]) for g in ("A", "C")},
        "rows": rows,
    }
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()

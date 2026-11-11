"""측정만 — 저점 주봉 신호: S100(+100% 또는 756거래일) vs S5(+5%), 상장폐지 종목 포함.

사전등록: docs/lowpoint_hold_100.md §1 — 결과 보고 규칙·값을 바꾸지 않는다.
재사용: 11-10 매도 규칙 모듈(weekly_hits·self_check_hits·WARMUP) → 10-09(liquid·lp) → 시스템 측정(system_sim2 = S5 대조용) → harness.
데이터: 현재 상장 KR(naver 통합 일봉, 4,000일) + 상장폐지 KOSPI·KOSDAQ 보통주(목록 KIND 상장폐지현황, 시세 naver → FDR — delisted_source.py).
        상장폐지 데이터가 기준에 못 미치면 **하드 실패** — 생존편향이 큰 결과를 만들지 않는다(사전등록 한계 절).
실행 시각: harness.check_run_window(("kr",)).
실행: (FDR 대안을 쓰려면) python3 -m pip install finance-datareader
      MEAS_CACHE_LONG=/tmp/lp_4000.pkl python3 scripts/measurements/2026-11-11_lowpoint_hold_100.py
      (파일명 날짜는 실행일 — KST — 로 바꾼다. 결과 JSON은 커밋하지 않는다)
"""
import importlib.util
import json
import os
import pickle
import random
import time
from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lp_sys", os.path.join(HERE, "2026-11-10_lowpoint_system_expectancy_listing.py"))
sysm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sysm)
m, base, harness, lp = sysm.m, sysm.base, sysm.harness, sysm.lp

# ── 사전등록 값(docs/lowpoint_hold_100.md §1) ──
KR_DAYS_LONG = 4000          # 약 11년
HOLD = 756                   # 약 3년
T100, T5 = 1.00, 0.05
BUY_SLIP = 0.002
COST = sysm.COST             # 매도 0.25%
GAP_SKIP = m.GAP_SKIP
DEDUP_DAYS = m.DEDUP_DAYS
LISTED_MARGIN_DAYS = 30
MERGER_WORDS = ("합병", "완전자회사", "지주회사", "주식교환", "주식이전", "자진")   # 보상(대가·공개매수)으로 끝나는 폐지 — §1-부록
TRANSFER_WORDS = ("유가증권시장 상장", "코스닥시장 상장")                           # 이전상장 — 폐지가 아님, 제외 — §1-부록
EXCLUDE_NAME = ("스팩", "ETN", "ETF", "리츠", "인프라", "선박", "기업인수목적")
MIN_DELISTED_IN_WINDOW = 50
MIN_DELISTED_FETCH_OK = 0.8
SIM_CHECK_N = 200
SEED = 20261111
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


# ══ 데이터 ══
def load_listed(cache):
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    uni = {}
    for board in ("kospi", "kosdaq"):
        u, _ = lp.kr_universe(board)
        uni.update(u)
    import naver_kr
    from concurrent.futures import ThreadPoolExecutor, as_completed
    data, failed, t0 = {}, [], time.time()

    def one(t):
        try:
            df = naver_kr.fetch_history(t, days=KR_DAYS_LONG)
            return t, (None if df is None or df.empty else df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]))
        except Exception:
            return t, None
    with ThreadPoolExecutor(max_workers=lp.FETCH_CONCURRENCY) as ex:
        for i, f in enumerate(as_completed([ex.submit(one, t) for t in uni]), 1):
            t, df = f.result()
            (failed.append(t) if df is None or df.empty else data.__setitem__(t, df))
            if i % 300 == 0:
                print(f"[fetch] {i}/{len(uni)} {time.time() - t0:.0f}s", flush=True)
    blob = {"data": data, "names": uni, "failed": sorted(failed), "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


def load_delisted(window_start, cache, stats, log, listed_codes=frozenset()):
    """KIND 상장폐지현황 목록 → 시세 naver → FDR(delisted_source). 하한 미달이면 하드 실패."""
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    import sys
    sys.path.insert(0, HERE)
    import delisted_source as ds
    lst = ds.kind_delisted(str(window_start.date()), "2099-12-31", log)
    stats["delisted_list_rows"] = len(lst)
    f = lst[lst["delisted"] > window_start + pd.Timedelta(days=LISTED_MARGIN_DAYS)]
    f = f[~f["name"].str.contains("|".join(EXCLUDE_NAME))]
    f = f[f["code"].str[-1] == "0"]
    f = f.drop_duplicates("code", keep="last")
    n0 = len(f)
    f = f[~f["reason"].astype(str).str.contains("|".join(TRANSFER_WORDS))]
    stats["delisted_transfer_excluded"] = n0 - len(f)
    n1 = len(f)
    f = f[~f["code"].isin(listed_codes)]                 # 같은 코드가 지금 상장 유니버스에 있으면 중복 — 제외
    stats["delisted_dup_listed_excluded"] = n1 - len(f)
    stats["delisted_in_window"] = len(f)
    assert len(f) >= MIN_DELISTED_IN_WINDOW, f"창 안 상장폐지 보통주 {len(f)}건 — 목록 소스 실패 의심(하드 실패)"
    data, meta, fails, src = {}, {}, [], Counter()
    for i, r in enumerate(f.itertuples(index=False), 1):
        df, s = ds.fetch_prices(r.code, KR_DAYS_LONG, log)
        if df is not None:
            df = df[(df.index >= window_start) & (df.index <= r.delisted)]
        if df is not None and len(df):
            key = f"{r.code}.DL"
            data[key] = df
            src[s] += 1
            meta[key] = {"name": r.name, "delisted": str(r.delisted.date()), "reason": r.reason, "market": r.market,
                         "merger": any(w in str(r.reason) for w in MERGER_WORDS), "source": s,
                         "last_bar": str(df.index[-1].date())}
        else:
            fails.append(r.code)
        if i % 50 == 0:
            print(f"[delisted] {i}/{len(f)} ok {len(data)}", flush=True)
    ok = len(data) / len(f)
    stats["delisted_fetch_ok"] = len(data)
    stats["delisted_fetch_fail"] = len(fails)
    stats.update({f"delisted_source_{k}": v for k, v in src.items()})
    assert ok >= MIN_DELISTED_FETCH_OK, f"상장폐지 시세 성공률 {ok:.0%} — 기준 {MIN_DELISTED_FETCH_OK:.0%} 미달(하드 실패)"
    blob = {"data": data, "meta": meta, "fails": fails}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


# ══ 매매 규칙 ══
def sim(buy, path, target):
    """sysm.system_sim2를 목표만 바꿔 일반화(+물타기·덜어내기 같음). 1차 매수금 대비 %·묶인 돈×일."""
    T, half = buy * (1 + target), buy * 0.5
    held, added, cash, paid = [buy], None, 0.0, buy
    adds, cap_days, hit50 = 0, 0.0, None
    for k in range(len(path)):
        o, h, lo = float(path["Open"].iloc[k]), float(path["High"].iloc[k]), float(path["Low"].iloc[k])
        cap_days += sum(held) / buy
        if hit50 is None and h >= buy * 1.5:
            hit50 = k + 1
        if h >= T:
            cash += len(held) * max(o, T) * (1 - COST)
            return {"ret": (cash - paid) / buy * 100, "exit": "target", "days": k + 1, "adds": adds, "cap_days": cap_days, "hit50": hit50}
        if added is not None and h >= (buy + added) / 2:
            cash += max(o, (buy + added) / 2) * (1 - COST)
            held.remove(added)
            added = None
            continue
        if added is None and lo <= half:
            px = min(o, half)
            held.append(px)
            paid += px
            added = px
            adds += 1
    cash += len(held) * float(path["Close"].iloc[-1]) * (1 - COST)
    return {"ret": (cash - paid) / buy * 100, "exit": "cap", "days": len(path), "adds": adds, "cap_days": cap_days, "hit50": hit50}


def check_sim_equals_system_sim2(paths, rng, stats):
    """회귀: sim(target=5%)이 11-10 system_sim2와 같은 값을 내는가(하나라도 다르면 실패)."""
    for _ in range(SIM_CHECK_N):
        p = rng.choice(paths)
        buy = float(p["Open"].iloc[0])
        a, b = sim(buy, p, T5), sysm.system_sim2(buy, p)
        assert abs(a["ret"] - b["ret"]) < 1e-9 and a["exit"] == b["exit"] and a["days"] == b["days"], f"sim≠system_sim2 {a} {b}"
    stats["sim_check"] = SIM_CHECK_N


def future_n(view, j, n):
    """CleanView.future와 같은 정의를 n봉까지만(긴 이력에서 전체 future 계산을 피함)."""
    if not view.dirty:
        return view.full.iloc[j + 1:j + 1 + n]
    keep = []
    for k in range(j + 1, len(view.raw)):
        if view.valid(k):
            keep.append(k)
            if len(keep) >= n:
                break
    return view.raw.iloc[keep]


def gap_suspect(path):
    """시가/전일 종가가 ±60% 넘게 뛴 날 — 수정주가 미반영(분할·병합) 의심. 상장폐지 시세(KRX)는 수정 여부를 확인 못 했다."""
    c = path["Close"].astype(float).shift()
    r = (path["Open"].astype(float) / c - 1).abs()
    return bool((r > 0.6).any())


# ══ 신호 ══
def build(data, delisted_meta, now, window_start, data_end, stats):
    hits_by_label = defaultdict(list)
    for t, df in data.items():
        truncated = df.index[0] <= window_start + pd.Timedelta(days=LISTED_MARGIN_DAYS)
        wk_idx = lp.resample_bars(df["Close"], m.TF).index
        for label, bar_date, c0 in m.weekly_hits(df["Close"], now):
            if truncated and wk_idx.get_loc(label) < m.WARMUP_WEEKS:
                stats["hit_warmup_skip"] += 1
                continue
            hits_by_label[label].append({"ticker": t, "label": label, "base_date": pd.Timestamp(bar_date), "base_price": c0})
    week_mode = {}
    for t, df in data.items():
        for lbl, bd in lp.resample_bars(df["Close"], m.TF)["bar_date"].items():
            week_mode.setdefault(lbl, Counter())[pd.Timestamp(bd)] += 1
    hits = []
    for label, hs in hits_by_label.items():
        mode = week_mode[label].most_common(1)[0][0]
        hits += [h for h in hs if h["base_date"] >= mode]
    trades, last_kept, views = [], {}, {}
    for h in sorted(hits, key=lambda x: (x["ticker"], x["base_date"])):
        t = h["ticker"]
        if t in last_kept and (h["base_date"] - last_kept[t]).days < DEDUP_DAYS:
            stats["dedup_skip"] += 1
            continue
        last_kept[t] = h["base_date"]
        df = data[t]
        view = views.get(t) or views.setdefault(t, harness.CleanView(df))
        if h["base_date"] not in df.index:
            continue
        jb = df.index.get_loc(h["base_date"])
        if not view.valid(jb):
            continue
        pre = view.prefix(jb)
        if not base.liquid(pre):
            stats["illiquid"] += 1
            continue
        path = future_n(view, jb, HOLD)
        is_dl = t in delisted_meta
        if len(path) == 0:
            stats["no_future"] += 1
            continue
        if len(path) < HOLD and not is_dl:
            stats["short_future_listed"] += 1      # 아직 3년이 안 지난 신호 — 제외
            continue
        assert path.index[0] > pre.index[-1], "future lookahead"
        o = float(path["Open"].iloc[0])
        if not o > 0 or o >= h["base_price"] * (1 + GAP_SKIP):
            stats["gap_skip"] += 1
            continue
        buy = o * (1 + BUY_SLIP)
        dl_end = is_dl and len(path) < HOLD
        trades.append({"ticker": t, "base_date": str(h["base_date"].date()), "year": h["base_date"].year,
                       "delisted_exit": dl_end, "merger": bool(dl_end and delisted_meta[t]["merger"]),
                       "gap_suspect": gap_suspect(path), "S100": sim(buy, path, T100), "S5": sim(buy, path, T5)})
    stats["trades"] = len(trades)
    return trades


def summarize(rows):
    if not rows:
        return {"n": 0}
    out = {"n": len(rows)}
    for rule in ("S100", "S5"):
        r = [x[rule] for x in rows]
        rets = [x["ret"] for x in r]
        years = sum(x["cap_days"] for x in r) / 252
        tgt = [x for x in r if x["exit"] == "target"]
        out[rule] = {"ret": sysm.dist(rets), "target_rate": len(tgt) / len(r), "days_to_target": sysm.dist([x["days"] for x in tgt]),
                     "cap_exit_ret": sysm.dist([x["ret"] for x in r if x["exit"] == "cap"]),
                     "add_rate": sum(x["adds"] > 0 for x in r) / len(r),
                     "annualized_pct": sum(rets) / years if years > 0 else None,      # Σ손익(1차 대비 %) ÷ Σ(투입금×연수)
                     "capital_years_per_trade": years / len(r)}
    out["reach50_within_hold"] = sum(x["S100"]["hit50"] is not None for x in rows) / len(rows)
    return out


def main():
    run_window = harness.check_run_window(("kr",))
    t0 = time.time()
    now = datetime.now(m.KST)
    stats, rng = Counter(), random.Random(SEED)
    window_start = pd.Timestamp(now.date()) - pd.Timedelta(days=KR_DAYS_LONG)
    listed = load_listed(os.environ.get("MEAS_CACHE_LONG"))
    lc = os.environ.get("MEAS_CACHE_LONG")
    src_log = {}
    listed_codes = frozenset(t.split(".")[0] for t in listed["data"])
    dl = load_delisted(window_start, (lc + ".delisted") if lc else None, stats, src_log, listed_codes)
    stamp = harness.run_stamp(listed["data"])
    m.self_check_hits(listed["data"], now, rng, stats)
    data = {**listed["data"], **dl["data"]}
    data_end = max(df.index[-1] for df in listed["data"].values())
    stats["listed"] = len(listed["data"])
    stats["delisted"] = len(dl["data"])
    trades = build(data, dl["meta"], now, window_start, data_end, stats)
    check_sim_equals_system_sim2([sysm.harness.CleanView(data[t]).full.iloc[:HOLD] for t in rng.sample(sorted(listed["data"]), 50)], rng, stats)
    assert stats["trades"] >= 100, f"매매 {stats['trades']}건 — 표본 부족(하드 실패)"
    by_year = defaultdict(list)
    for x in trades:
        by_year[str(x["year"])].append(x)
    result = {
        "run_stamp": stamp, "run_window": run_window, "elapsed_s": round(time.time() - t0),
        "params": {"KR_DAYS_LONG": KR_DAYS_LONG, "HOLD": HOLD, "T100": T100, "T5": T5, "BUY_SLIP": BUY_SLIP, "COST": COST,
                   "window_start": str(window_start.date()), "data_end": str(data_end.date())},
        "stats": dict(stats), "delisted_source_log": {k: (v if k != "kind_attempts" else v[:12]) for k, v in src_log.items() if k in ("kind_variant", "kind_attempts")},
        "delisted_meta": dl["meta"], "delisted_fails": dl["fails"],
        "all": summarize(trades),
        "excluding_compensated_delisted": summarize([x for x in trades if not x["merger"]]),
        "excluding_gap_suspect": summarize([x for x in trades if not x["gap_suspect"]]),
        "listed_only": summarize([x for x in trades if not x["ticker"].endswith(".DL")]),
        "delisted_only": summarize([x for x in trades if x["ticker"].endswith(".DL")]),
        "delisted_exit_only": summarize([x for x in trades if x["delisted_exit"]]),
        "by_signal_year": {y: summarize(v) for y, v in sorted(by_year.items())},
        "trades": trades,
        "limitations": ["상장폐지 시세(KRX)의 수정주가 반영 여부 미확인 — gap_suspect 제외 결과 같이 보고",
                        "합병 폐지는 마지막 거래일 종가로 정리(실제 대가와 다를 수 있음) — 제외 결과 같이 보고",
                        "현재 유니버스는 실행일 기준 관리종목 제외 — 과거 시점 유니버스와 다름",
                        "같은 신호 데이터를 여러 번 쓴 뒤의 측정 — 표본 밖 확인 아님"]}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    brief = {k: v for k, v in result.items() if k not in ("trades",)}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()

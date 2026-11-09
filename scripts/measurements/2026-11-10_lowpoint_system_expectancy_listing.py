"""저점 매매 시스템 전체 기대값(H1, 측정만) + 상장 후 큰 상승 없음 그룹의 +5% 이후 보유 규칙(H2).

사전등록: docs/lowpoint_system_expectancy_listing.md §1·§1-구현 — 결과 보고 조건·문턱을 바꾸지 않는다.
재사용(사본 금지): 11-10 측정 모듈(신호·워밍업 정의·simulate·zstat) → 10-09 모듈(load_data·liquid) → harness·lowpoint.
실행 시각: harness.check_run_window(("kr",)).
실행: MEAS_CACHE=/tmp/lp_1900.pkl python3 scripts/measurements/2026-11-11_lowpoint_system_expectancy_listing.py
      (파일명 날짜는 실행일로 바꾼다. 결과 JSON은 커밋하지 않는다)
"""
import importlib.util
import json
import os
import random
import time
from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lp_1110", os.path.join(HERE, "2026-11-10_lowpoint_after5_exit_rules.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
base, harness, lp = m.base, m.harness, m.lp

# ── 사전등록 값 ──
BIG_RISE = 2.0           # 상장가 × 2.0 이상이면 "큰 상승 있었음"(사용자 "100%이상")
LISTED_MARGIN_DAYS = 30  # 데이터 시작이 조회 창 시작 +30일 이후면 창 안 상장
COST = 0.0025            # 매도 금액의 0.25%(AI 어림값)
HOLD_MAX, TARGET, GAP_SKIP, DEDUP_DAYS, MA_N = m.HOLD_MAX, m.TARGET, m.GAP_SKIP, m.DEDUP_DAYS, m.MA_N
Z_MIN, N_MIN, RULES, WARMUP_WEEKS = m.Z_MIN, m.N_MIN, m.RULES, m.WARMUP_WEEKS
SEED = 20261111
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def system_sim2(buy, path):
    """사용자 규칙 전체: +5% 전량 익절 / −50% 같은 수량 추가 / 평단에서 추가분 덜어내기(반복). 1차 매수금 대비 %.
    매수 대금 = 1차 + 모든 추가 매수가, 매도 대금은 비용 차감."""
    T, half = buy * (1 + TARGET), buy * 0.5
    held, added, cash, paid = [buy], None, 0.0, buy
    adds, max_units, cap_days = 0, 1, 0.0
    for k in range(len(path)):
        o, h, lo = float(path["Open"].iloc[k]), float(path["High"].iloc[k]), float(path["Low"].iloc[k])
        cap_days += sum(held) / buy
        if h >= T:
            px = max(o, T)
            cash += len(held) * px * (1 - COST)
            return {"ret": (cash - paid) / buy * 100, "ret_on_paid": (cash - paid) / paid * 100, "exit": "target", "days": k + 1, "adds": adds,
                    "max_units": max_units, "cap_days": cap_days, "open_at_cap": False}
        if added is not None and h >= (buy + added) / 2:
            px = max(o, (buy + added) / 2)
            cash += px * (1 - COST)
            held.remove(added)
            added = None
            continue
        if added is None and lo <= half:
            px = min(o, half)
            held.append(px)
            paid += px
            added = px
            adds += 1
            max_units = max(max_units, len(held))
    last = float(path["Close"].iloc[-1])
    cash += len(held) * last * (1 - COST)
    return {"ret": (cash - paid) / buy * 100, "ret_on_paid": (cash - paid) / paid * 100, "exit": "cap", "days": len(path), "adds": adds,
            "max_units": max_units, "cap_days": cap_days, "open_at_cap": True}


def dist(v):
    return {"n": len(v), "mean": sum(v) / len(v) if v else None, "median": m.pct(v, 0.5), "p5": m.pct(v, 0.05), "p95": m.pct(v, 0.95)}


def compare(rows, label):
    if not rows:
        return {"n": 0}
    split = sorted(x["touch_date"] for x in rows)[len(rows) // 2]
    out = {"n": len(rows), "half_split": split}
    for rk in RULES:
        d = [x[rk] - x["R0"] for x in rows]
        e = [x[rk] - x["R0"] for x in rows if x["touch_date"] < split]
        l_ = [x[rk] - x["R0"] for x in rows if x["touch_date"] >= split]
        z = m.zstat(d)
        checks = {"z_ge_min": z is not None and z >= Z_MIN,
                  "halves_positive": bool(e) and bool(l_) and sum(e) / len(e) > 0 and sum(l_) / len(l_) > 0,
                  "n_ge_min": len(d) >= N_MIN}
        out[rk] = {"mean_diff_pp": sum(d) / len(d), "z": z, "early": sum(e) / len(e) if e else None,
                   "late": sum(l_) / len(l_) if l_ else None, "checks": checks, "passed": all(checks.values()),
                   "mean": sum(x[rk] for x in rows) / len(rows), "median": m.pct([x[rk] for x in rows], 0.5)}
    out["R0_mean"] = sum(x["R0"] for x in rows) / len(rows)
    return out


def main():
    run_window = harness.check_run_window(("kr",))
    t0 = time.time()
    now = datetime.now(m.KST)
    blob = base.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    rng, stats = random.Random(SEED), Counter()
    m.self_check_hits(data, now, rng, stats)

    window_start = pd.Timestamp(now.date()) - pd.Timedelta(days=base.KR_DAYS)
    hits_by_label, listed = defaultdict(list), {}
    for t, df in data.items():
        truncated = df.index[0] <= window_start + pd.Timedelta(days=LISTED_MARGIN_DAYS)
        listed[t] = None if truncated else float(df["Open"].iloc[0])     # 상장가 = 첫 거래일 시가
        wk_idx = lp.resample_bars(df["Close"], m.TF).index
        for label, bar_date, c0 in m.weekly_hits(df["Close"], now):
            if truncated and wk_idx.get_loc(label) < WARMUP_WEEKS:
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
        for h in hs:
            if h["base_date"] < mode:
                stats["hit_stale"] += 1
            else:
                hits.append(h)
    stats["hits"] = len(hits)
    assert hits, "히트 0건"

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
            stats["no_base_bar"] += 1
            continue
        jb = df.index.get_loc(h["base_date"])
        if not view.valid(jb):
            stats["base_invalid"] += 1
            continue
        pre = view.prefix(jb)
        if not base.liquid(pre):
            stats["illiquid"] += 1
            continue
        fut = view.future(jb)
        assert len(fut) == 0 or fut.index[0] > pre.index[-1], "future lookahead"
        if len(fut) < HOLD_MAX:
            stats["insufficient_hold_window"] += 1
            continue
        path = fut.iloc[:HOLD_MAX]
        buy = float(path["Open"].iloc[0])
        if not buy > 0:
            stats["bad_open"] += 1
            continue
        if buy >= h["base_price"] * (1 + GAP_SKIP):
            stats["gap_skip"] += 1
            continue
        lp_px = listed[t]
        group = (lp_px is not None and lp_px > 0 and float(pre["Close"].max()) < lp_px * BIG_RISE)
        closes = pd.concat([pre["Close"], path["Close"]]).astype(float)
        ma = closes.rolling(MA_N).mean().loc[path.index]
        sim = m.simulate(buy, path, ma)
        sysr = system_sim2(buy, path)
        trades.append({"ticker": t, "label": str(h["label"].date()), "buy_date": str(path.index[0].date()), "buy": buy,
                       "listed_in_window": lp_px is not None, "listing_price": lp_px,
                       "max_close_to_base_vs_listing": (float(pre["Close"].max()) / lp_px) if lp_px else None,
                       "group_no_big_rise": group, **sim, "system": sysr})
    stats["trades"] = len(trades)
    assert trades, "매매 0건"

    # H1 — 측정만
    sysrets = [x["system"]["ret"] for x in trades]
    caps = [x["system"]["cap_days"] for x in trades]
    h1 = {"all": dist(sysrets), "all_ret_on_paid": dist([x["system"]["ret_on_paid"] for x in trades]),
          "target_rate": sum(x["system"]["exit"] == "target" for x in trades) / len(trades),
          "open_at_cap_rate": sum(x["system"]["open_at_cap"] for x in trades) / len(trades),
          "add_rate": sum(x["system"]["adds"] > 0 for x in trades) / len(trades),
          "max_units_dist": dict(Counter(x["system"]["max_units"] for x in trades)),
          "open_at_cap": dist([x["system"]["ret"] for x in trades if x["system"]["open_at_cap"]]),
          "days_held": dist([x["system"]["days"] for x in trades]),
          "capital_days": dist(caps),
          "annualized_ref_pct": (sum(sysrets) / len(sysrets)) / (sum(caps) / len(caps)) * 250 if caps else None,
          "by_group": {"no_big_rise": dist([x["system"]["ret"] for x in trades if x["group_no_big_rise"]]),
                       "other": dist([x["system"]["ret"] for x in trades if not x["group_no_big_rise"]])}}
    # H2
    touched = [x for x in trades if x["touched"]]
    grp = [x for x in touched if x["group_no_big_rise"]]
    oth = [x for x in touched if not x["group_no_big_rise"]]
    stats["touched"], stats["group_touched"], stats["group_trades"] = len(touched), len(grp), sum(x["group_no_big_rise"] for x in trades)
    h2 = {"group": compare(grp, "group"), "other_record_only": compare(oth, "other")}
    result = {"run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
              "elapsed_s": round(time.time() - t0),
              "params": {"BIG_RISE": BIG_RISE, "LISTED_MARGIN_DAYS": LISTED_MARGIN_DAYS, "COST": COST, "HOLD_MAX": HOLD_MAX,
                         "TARGET": TARGET, "GAP_SKIP": GAP_SKIP, "DEDUP_DAYS": DEDUP_DAYS, "WARMUP_WEEKS": WARMUP_WEEKS,
                         "Z_MIN": Z_MIN, "N_MIN": N_MIN, "SEED": SEED},
              "stats": dict(stats), "H1_record_only": h1, "H2": h2, "trades": trades}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in ("stats", "H1_record_only", "H2")}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()

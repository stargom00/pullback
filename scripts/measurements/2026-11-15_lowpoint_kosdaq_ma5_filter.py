"""저점 신호 × 코스닥 5일선 필터 — H1 판정 측정. 사전등록: docs/lowpoint_kosdaq_ma5_filter.md §1.

입력: 11-10 시스템 기대값 스크립트의 결과 JSON(trades) — 매매 정의는 그 스크립트가 유일한 원본(사본 없음).
실행: python3 scripts/measurements/2026-11-15_lowpoint_kosdaq_ma5_filter.py <system_expectancy.results.json>
"""
import json
import math
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import harness  # noqa: E402
import naver_kr  # noqa: E402

Z_MIN, N_MIN, MA = 2.24, 100, 5
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def welch(a, b):
    na, nb = len(a), len(b)
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    return (ma - mb) / se if se > 0 else None


def prop_z(k1, n1, k2, n2):
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (k1 / n1 - k2 / n2) / se if se > 0 else None


def state_map():
    idx = naver_kr.fetch_index_history("KOSDAQ", days=2400)
    assert idx is not None and len(idx) > 1000, "코스닥 지수 조회 실패"
    c = idx["Close"].astype(float)
    above = c > c.rolling(MA).mean()
    return above[c.rolling(MA).mean().notna()], str(idx.index[-1].date())


def side(rows):
    r = [x["system"]["ret"] for x in rows]
    cap = sum(x["system"]["exit"] == "cap" for x in rows)
    return {"n": len(rows), "ret_mean": sum(r) / len(r), "ret_median": sorted(r)[len(r) // 2], "cap_rate": cap / len(rows), "cap": cap}


def judge(rows):
    up = [x for x in rows if x["k5"]]
    dn = [x for x in rows if not x["k5"]]
    if len(up) < N_MIN or len(dn) < N_MIN:
        raise SystemExit(f"표본 부족: 위 {len(up)} 아래 {len(dn)}")
    split = sorted(x["buy_date"] for x in rows)[len(rows) // 2]
    half = {}
    for nm, sel in (("early", lambda x: x["buy_date"] < split), ("late", lambda x: x["buy_date"] >= split)):
        u = [x["system"]["ret"] for x in up if sel(x)]
        d = [x["system"]["ret"] for x in dn if sel(x)]
        half[nm] = {"diff": (sum(u) / len(u) - sum(d) / len(d)) if u and d else None, "n_up": len(u), "n_dn": len(d)}
    z1 = welch([x["system"]["ret"] for x in up], [x["system"]["ret"] for x in dn])
    su, sd = side(up), side(dn)
    z2 = prop_z(sd["cap"], sd["n"], su["cap"], su["n"])
    halves_pos = all(h["diff"] is not None and h["diff"] > 0 for h in half.values())
    halves_neg = all(h["diff"] is not None and h["diff"] < 0 for h in half.values())
    return {"up": su, "down": sd, "diff_ret_pp": su["ret_mean"] - sd["ret_mean"], "O1_z": z1, "O2_z_down_minus_up_cap": z2,
            "halves": half, "split": split,
            "adopt": bool(z1 is not None and z1 >= Z_MIN and halves_pos),
            "reverse": bool(z1 is not None and z1 <= -Z_MIN and halves_neg),
            "O2_flag": bool(z2 is not None and abs(z2) >= Z_MIN)}


def main():
    run_window = harness.check_run_window(("kr",))
    src = json.load(open(sys.argv[1]))
    trades = src["trades"]
    assert trades, "매매 0건"
    st, idx_last = state_map()
    rows, miss = [], 0
    for x in trades:
        bd = pd.Timestamp(x["buy_date"])
        prev = st[st.index < bd]
        if prev.empty:
            miss += 1
            continue
        rows.append({**x, "k5": bool(prev.iloc[-1]), "k5_date": str(prev.index[-1].date())})
    res = {"run_window": run_window, "source_stamp": src.get("run_stamp"), "kosdaq_last": idx_last, "n_trades": len(trades), "no_index_day": miss,
           "all": judge(rows), "kosdaq_only_record": judge([x for x in rows if x["ticker"].endswith(".KQ")])}
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()

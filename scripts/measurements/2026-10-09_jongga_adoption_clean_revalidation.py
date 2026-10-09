"""종가베팅 채택(조합 A) 재현 검증 — 데이터 정제만 적용.

사전등록: docs/jongga_adoption_clean_revalidation.md. 등록부: docs/measurement_registry.md.

원문(채택 근거): 2026-08-29 확장 측정(`2026-08-29_kr_jongga_betting_backtest_extended.py`) + 2026-09-01 신규 유니버스 재측정
(`2026-09-01_jongga_universe_v2_revalidation.py`, 비용차감 +0.80%, z 3.54, n 292). **진입·청산·유니버스·기간 정의·판정식은 원문 그대로**
— 원 스크립트를 모듈로 import해 `turnover_rank_at`·`evaluate`·`stats`·`mean_gap_zscore`·`OFFSETS`와 09-01 스크립트의
유니버스 생성·fetch를 그대로 쓴다. 바꾸는 것은 **데이터 정제 하나뿐**: 원 함수들이 부르는 데이터 접근(`harness.truncate_at`·
`harness.future_after`)을 정제판으로 바꾼다.
  truncate_at(df, off)  → T봉(= df.iloc[-1-off])까지 자른 df를 `harness.clean_at_checkpoint`로 정제(= `harness.CleanView.prefix`).
                          T봉이 무효(정제에서 빠짐)면 빈 df — 그날 그 종목은 거래대금 순위·평가 모두에서 빠진다.
  future_after(df, off) → T봉 다음의 **유효봉**만(`CleanView.future`) — T+1 시가 = 다음 유효봉 시가. T봉이 무효면 빈 df.
판정(원문 그대로): (a) 조합 A 비용차감 평균 ≥ +0.5% (b) 이전·최근 절반 둘 다 ≥ +0.5% (c) base 대비 z ≥ 1.96 그리고 격차 > 0.

진단(판정 미사용 — 사전등록 §1-구현에 미리 적음): 같은 오늘 데이터로 ① 지금 기간 ② 09-10 실행과 같은 기간 ③ 09-01 실행과 같은 기간을
정제 전·후 각각 돌려 3.54 → 1.80 차이를 오염·기간·그 밖(유니버스 변동·확정 전 봉)으로 나눈다. 기간 맞추기 = 오프셋에 "그 실행의
마지막 봉 이후 쌓인 봉 수"를 더한다(전체 종목 최빈 날짜 축 기준).

실행 시각: harness.check_run_window(["KR"]).
실행: MEAS_CACHE=<경로.pkl> python3 scripts/measurements/2026-10-09_jongga_adoption_clean_revalidation.py
      (결과 JSON 커밋 안 함 — README 규칙5)
"""
import importlib.util
import json
import os
import pickle
import sys
import time
from collections import Counter

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import harness  # noqa: E402


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


orig = _load("jongga_extended", "2026-08-29_kr_jongga_betting_backtest_extended.py")   # 조건·판정 원문
v2 = _load("jongga_v2", "2026-09-01_jongga_universe_v2_revalidation.py")               # 유니버스·fetch 원문
os.chdir(ROOT)

RUN_MARKETS = ["KR"]
NET_MIN = 0.005                  # 원문 (a)(b) "+0.5%"
Z_MIN = 1.96                     # 원문 (c)
# 진단용 과거 실행의 마지막 봉 날짜(결과 파일 시각으로 추정 — 문서 §1-구현 D2)
ANCHORS = {"2026-09-01 실행": "2026-09-01", "2026-09-10 실행": "2026-09-10"}
REF = {"2026-09-01 실행": {"n": 292, "net_mean": 0.008022545442634744, "z": 3.537034396585564,
                          "earlier": 0.0074, "recent": 0.0086},
       "2026-09-10 실행": {"n": 276, "net_mean": 0.008176634954149685, "z": 1.8010455976011452}}
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


# ══════════════════════════════════════════════════════════════════════
# 데이터 — 09-01 재측정과 같은 유니버스 생성(정적 KR_UNIVERSE ∪ fetch_top_turnover_v2 상위 1500, 실행일 기준)·같은 fetch
# ══════════════════════════════════════════════════════════════════════
def load_data():
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        return pickle.load(open(cache, "rb"))
    new_dyn, v2_stats = v2.naver_kr.fetch_top_turnover_v2(top_n=1500)
    tickers = {**v2.universe_mod.KR_UNIVERSE, **new_dyn}
    data = v2.fetch_universe_history(list(tickers))
    blob = {"data": data, "universe_count": len(tickers), "v2_stats": v2_stats,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    if cache:
        pickle.dump(blob, open(cache, "wb"))
    return blob


# ══════════════════════════════════════════════════════════════════════
# 정제 접근 계층 — 원 함수가 부르는 harness.truncate_at / future_after만 바꾼다
# ══════════════════════════════════════════════════════════════════════
class CleanAccess:
    """원 스크립트의 df 객체마다 CleanView를 붙여 두고, 같은 시그니처의 정제판 truncate_at/future_after를 제공."""

    def __init__(self, data: dict):
        self.views = {id(df): harness.CleanView(df) for df in data.values()}

    def _jv(self, df, off):
        v = self.views[id(df)]
        return v, len(df) - 1 - off

    def truncate_at(self, df, off):
        v, j = self._jv(df, off)
        if j < 0 or not v.valid(j):
            return df.iloc[0:0]
        return v.prefix(j)

    def future_after(self, df, off):
        v, j = self._jv(df, off)
        if j < 0 or not v.valid(j):
            return df.iloc[0:0]
        return v.future(j)


class _Patched:
    """with 블록 안에서만 원 모듈(orig)이 보는 harness 접근 함수를 바꾼다(원 코드 무수정)."""

    def __init__(self, access):
        self.access = access

    def __enter__(self):
        self.saved = (orig.harness.truncate_at, orig.harness.future_after)
        orig.harness.truncate_at, orig.harness.future_after = self.access.truncate_at, self.access.future_after
        return self

    def __exit__(self, *a):
        orig.harness.truncate_at, orig.harness.future_after = self.saved


def run(data, offsets, clean: bool, label: str):
    """원 판정 그대로 — offsets(체크포인트)·halves만 인자로. 반환: 집계 + 조합 A·base 레코드 키."""
    half_idx = len(offsets) // 2
    saved = (orig.OFFSETS, orig.RECENT_HALF, orig.EARLIER_HALF)
    orig.OFFSETS, orig.RECENT_HALF, orig.EARLIER_HALF = list(offsets), set(offsets[:half_idx]), set(offsets[half_idx:])
    t0 = time.time()
    recs = []
    try:
        ctx = _Patched(CleanAccess(data)) if clean else None
        if ctx:
            ctx.__enter__()
        try:
            for off in offsets:
                rank = orig.turnover_rank_at(data, off)
                recs.extend(orig.evaluate(data, off, rank))
        finally:
            if ctx:
                ctx.__exit__()
    finally:
        orig.OFFSETS, orig.RECENT_HALF, orig.EARLIER_HALF = saved
    base = [r for r in recs if r["base"]]
    combo = [r for r in base if r["candle"] and r["volume"] and r["position"]]
    s_c, s_b = orig.stats(combo), orig.stats(base)
    z, sig = orig.mean_gap_zscore(base, combo)
    e, rc = [r for r in combo if r["half"] == "earlier"], [r for r in combo if r["half"] == "recent"]
    s_e, s_r = orig.stats(e), orig.stats(rc)
    gap = (s_c["mean_gap"] - s_b["mean_gap"]) if s_c.get("n") and s_b.get("n") else None
    checks = {"a_net_mean_ge_0.5pct": s_c.get("net_mean") is not None and s_c["net_mean"] >= NET_MIN,
              "b_halves_ge_0.5pct": bool(s_e.get("n") and s_r.get("n") and s_e["net_mean"] >= NET_MIN and s_r["net_mean"] >= NET_MIN),
              "c_z_ge_1.96_and_gap_pos": z is not None and z >= Z_MIN and gap is not None and gap > 0}
    print(f"[{label}] clean={clean} recs={len(recs)} base={len(base)} comboA={len(combo)} z={z} {time.time() - t0:.0f}s", flush=True)
    return {"label": label, "clean": clean, "offsets": [offsets[0], offsets[-1], len(offsets)],
            "n_records": len(recs), "n_base": len(base), "combo_a": s_c, "base": s_b, "z_vs_base": z, "gap_vs_base": gap,
            "half_earlier": s_e, "half_recent": s_r, "checks": checks, "passed": all(checks.values()),
            "_combo_keys": [(r["ticker"], r["off"]) for r in combo], "_combo": combo}


def date_axis(data):
    """전 종목 최빈 마지막 날짜를 가진 종목들의 날짜 축(오프셋 ↔ 날짜 변환용)."""
    last = Counter(df.index[-1] for df in data.values()).most_common(1)[0][0]
    longest = max((df for df in data.values() if df.index[-1] == last), key=len)
    return longest.index


def invalid_rows(data):
    """정제로 빠지는 행 — 갈래A(OHLC ≤ 0 행)는 행마다, 갈래B(장기 무효 구간 앞 절단)는 종목마다 절단 날짜·행 수."""
    rows, trunc = [], []
    for t, df in sorted(data.items()):
        bad = (df[["Open", "High", "Low", "Close"]] <= 0).any(axis=1)
        for d in df.index[bad]:
            rows.append({"ticker": t, "date": str(d.date()), "reason": "OHLC≤0(무거래일 이월 등)"})
        full = harness.clean_at_checkpoint(df)
        if len(full) and full.index[0] > df.index[0]:
            cut = full.index[0]
            trunc.append({"ticker": t, "first_kept": str(cut.date()), "rows_before": int((df.index < cut).sum()),
                          "reason": "장기 무효 구간(5거래일+) 이전 절단(갈래B — 그 이후 체크포인트에서만 적용)"})
    return rows, trunc


def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    blob = load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    offsets = list(orig.OFFSETS)

    # 판정 실행(정제) + 같은 기간 정제 전(원 방법 그대로)
    J = run(data, offsets, True, "판정_지금기간_정제")
    R = run(data, offsets, False, "진단_지금기간_정제전")

    # 진단 — 과거 실행과 같은 기간(오프셋 이동)
    axis = date_axis(data)
    diag = {}
    for name, anchor in ANCHORS.items():
        a = pd.Timestamp(anchor)
        shift = int((axis > a).sum())                       # 그 실행의 마지막 봉 이후 쌓인 봉 수
        sh = [o + shift for o in offsets]
        diag[name] = {"anchor": anchor, "shift_bars": shift,
                      "raw": run(data, sh, False, f"진단_{name}기간_정제전"),
                      "clean": run(data, sh, True, f"진단_{name}기간_정제")}

    # 정제로 바뀐 조합 A·base 레코드(같은 기간 정제 전 vs 후)
    def diff_keys(raw, cln):
        rk, ck = set(raw["_combo_keys"]), set(cln["_combo_keys"])
        return sorted(rk - ck), sorted(ck - rk)
    removed_now, added_now = diff_keys(R, J)
    rows, trunc = invalid_rows(data)

    def off_date(t, off):
        df = data[t]
        return str(df.index[len(df) - 1 - off].date()) if len(df) - 1 - off >= 0 else None

    def strip(x):
        return {k: v for k, v in x.items() if not k.startswith("_")}
    result = {
        "run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
        "universe_count": blob.get("universe_count"), "fetched": len(data), "v2_stats": blob.get("v2_stats"),
        "elapsed_s": round(time.time() - t0),
        "judgment": strip(J), "diag_now_raw": strip(R),
        "diag_periods": {k: {"anchor": v["anchor"], "shift_bars": v["shift_bars"], "raw": strip(v["raw"]), "clean": strip(v["clean"])}
                         for k, v in diag.items()},
        "reference_reported": REF,
        "combo_a_removed_by_clean_now": [{"ticker": t, "off": o, "date_t": off_date(t, o)} for t, o in removed_now],
        "combo_a_added_by_clean_now": [{"ticker": t, "off": o, "date_t": off_date(t, o)} for t, o in added_now],
        "invalid_rows_ohlc_le0": rows, "invalid_rows_count": len(rows),
        "gap_truncations": trunc, "gap_truncation_count": len(trunc),
        "combo_a_records_judgment": J["_combo"],
    }
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    show = {k: result[k] for k in ("judgment", "diag_now_raw")}
    show["diag_periods"] = result["diag_periods"]
    show["counts"] = {"invalid_rows": len(rows), "gap_truncations": len(trunc), "combo_removed_now": len(removed_now),
                      "combo_added_now": len(added_now)}
    print(json.dumps(show, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()

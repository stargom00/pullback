"""
재점화 확인진입(안C형) Close vs High 기준 비교 (2026-09-01, 사용자 지시).

배경: v5.141에서 돌파임박 확인진입(안C)을 고가(High)기준→종가(Close)기준
으로 통일했더니 90개 창 EV가 오히려 개선됐다(0.658→1.062R, z 22.0→34.7).
사용자가 같은 비교를 재점화 확인진입(theme_reignition.check_confirm(),
docs/kr_theme_leader_reignition.md의 "확인진입(안C형) EV=+0.755R,n=53")에도
적용해보라고 지시 — 재점화도 원래 안C형(고가돌파+거래량)을 그대로 썼기
때문에 같은 가설이 성립할 수 있다.

방법론: 재현 안 하고 재구현하면 기준선이 갈릴 위험(harness.py 도입
계기와 같은 유형) — 원 스크립트
(`2026-08-31_kr_theme_leader_reignition.py`)를 모듈로 그대로 import해서
fetch/테마사이클추출/재점화판정(check_reignition)을 재사용하고,
`confirm_entry_race`만 고가/종가 두 버전으로 나눠 **같은 reignited_leader
이벤트 집합**에 대해 양쪽 다 실행한다(페어링까진 아니지만 최소한 같은
이벤트 풀에서 나온 비교 — 별도 실행으로 다시 뽑으면 재점화 판정 자체가
동일 시드라도 fetch 순서 의존성 때문에 달라질 수 있음, 9절 캐비어트 참고).

**표본 규모 주의**: 이건 20개 vs 90개 체크포인트 문제가 아니라 애초에
"재점화"라는 사건 자체가 희귀해서(리더 137건 중 71건만 재점화, 그 중
confirm 조건까지 충족하는 게 50~60건대) 표본이 원천적으로 작다. 이
비교는 참고용이며, 여기서 나온 차이가 작으면(또는 방향이 불안정하면)
"판단 불가"로 정직하게 보고할 것 — 임의로 어느 한쪽을 채택하지 않는다.

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-01_reignition_confirm_close_vs_high.py`
(KR 1900일 fetch 포함 약 4분).
"""
import sys
import os
import json
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import harness

_ORIG_PATH = os.path.join("scripts", "measurements", "2026-08-31_kr_theme_leader_reignition.py")
_spec = importlib.util.spec_from_file_location("reignition_orig", _ORIG_PATH)
orig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orig)

CONFIRM_BARS = orig.CONFIRM_BARS
CONFIRM_VOL_MULT = orig.CONFIRM_VOL_MULT
CONFIRM_VOL_AVG_WINDOW = orig.CONFIRM_VOL_AVG_WINDOW


def confirm_entry_race_close(df, reignite_pos, max_confirm_bars=CONFIRM_BARS, max_race_bars=60):
    """원 스크립트 confirm_entry_race()의 종가(Close) 기준 변형 —
    entry/stop 정의(신호일 고가/저가)는 그대로, 확인조건만 고가→종가."""
    n = len(df)
    sig_high = float(df["High"].iloc[reignite_pos])
    sig_low = float(df["Low"].iloc[reignite_pos])
    vols = df["Volume"]
    closes = df["Close"]
    for k in range(1, max_confirm_bars + 1):
        j = reignite_pos + k
        if j >= n:
            break
        avg50 = vols.iloc[max(0, j - CONFIRM_VOL_AVG_WINDOW):j].mean()
        if not avg50 or avg50 <= 0:
            continue
        cl = float(closes.iloc[j])
        if cl >= sig_high and float(vols.iloc[j]) >= CONFIRM_VOL_MULT * avg50:
            entry = sig_high
            stop = sig_low
            future = df.iloc[j:j + max_race_bars] if j + max_race_bars <= n else df.iloc[j:n]
            outcome, r = harness.race(entry, stop, future, max_bars=max_race_bars)
            return outcome, r
    return None, None


def main():
    data = orig.sb1900d.fetch_kr_long_universe(concurrency=10)
    print(f"[main] fetch 완료 {len(data)}종목", flush=True)

    with open("theme_map.json") as f:
        theme_map = json.load(f)

    tl = orig.tl
    market_turnover = tl.market_daily_turnover(data)
    max_window = len(market_turnover) - tl.BASELINE_WINDOW - 1

    leader_events = []
    theme_data_map = {}
    for name, entry in theme_map.items():
        stocks = entry.get("stocks", [])
        theme_data = tl.compute_theme_series(stocks, data, market_turnover, window=max_window)
        if theme_data is None:
            continue
        theme_data_map[name] = theme_data
        cycles = tl.find_cycles(theme_data)
        for c in cycles:
            leader = c["d0"]["leader"]
            d0_date = c["d0"]["date"]
            df_t = data.get(leader)
            if df_t is None or d0_date not in df_t.index:
                continue
            d0_pos = df_t.index.get_loc(d0_date)
            leader_events.append({"theme": name, "ticker": leader, "d0_date": str(d0_date.date()),
                                   "d0_pos": d0_pos})

    print(f"[main] 총 D0 리더 이벤트: {len(leader_events)}", flush=True)

    leader_results = []
    for e in leader_events:
        df_t = data[e["ticker"]]
        res = orig.check_reignition(df_t, e["d0_pos"])
        leader_results.append({**e, **res})

    leader_valid = [r for r in leader_results if not r["insufficient"] or r["reignited"]]
    reignited_leader = [r for r in leader_valid if r["reignited"]]
    print(f"[main] 리더 재점화: {len(reignited_leader)}/{len(leader_valid)} (원측정 71/137과 대조)", flush=True)

    outcomes_high, outcomes_close = [], []
    for r in reignited_leader:
        df_t = data[r["ticker"]]
        out_h, rr_h = orig.confirm_entry_race(df_t, r["reignite_pos"])
        if out_h is not None:
            outcomes_high.append((out_h, rr_h))
        out_c, rr_c = confirm_entry_race_close(df_t, r["reignite_pos"])
        if out_c is not None:
            outcomes_close.append((out_c, rr_c))

    ev_high = harness.ev_summary(outcomes_high)
    ev_close = harness.ev_summary(outcomes_close)
    z, sig = harness.ev_gap_zscore(ev_high, ev_close)

    report = {
        "n_reignited_leader_events": len(reignited_leader),
        "확인진입_High(원정의)": ev_high,
        "확인진입_Close(변형)": ev_close,
        "z_close_vs_high": z, "significant": sig,
    }
    print(f"[HIGH(원정의)] {ev_high}", flush=True)
    print(f"[CLOSE(변형)] {ev_close}", flush=True)
    print(f"[z] {z} significant={sig}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "2026-09-01_reignition_confirm_close_vs_high.results.json")
    with open(out_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    main()

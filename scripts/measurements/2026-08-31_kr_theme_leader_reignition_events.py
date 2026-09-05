"""
재점화 확인진입(안C형) 개별 이벤트 저장 + 시기반분 방향 체크 (2026-09-06,
사용자 지시). 배경: docs/kr_theme_leader_reignition.md의 확인진입
EV(+0.755R, n=53) 표본이 90 미만(README 규칙9)이고 시기반분 재현검증도
받은 적이 없다는 지적(v5.190 "reignition-downgrade" 커밋에서 🔴→🔎
관심(재량)으로 하향한 근거) — 값싼 방향 체크를 하고 싶었지만 원측정
(2026-08-31_kr_theme_leader_reignition.py)이 개별 이벤트를 날짜와 함께
저장하지 않았고(집계치만 /tmp에, 재fetch 없이는 복구 불가), 후속 비교
스크립트(2026-09-01_reignition_confirm_close_vs_high.py)의 결과 파일도
집계치뿐이라 시기별로 못 쪼갰다 — 그래서 이번엔 재fetch(약 4분)해서
개별 이벤트 자체를 저장해둔다(다음에 또 같은 질문이 나오면 이 파일만
읽으면 됨, 재fetch 불필요).

방법론: 재현 안 하고 재구현하면 기준선이 갈릴 위험(harness.py 도입
계기와 같은 유형, 2026-09-01 스크립트와 동일 원칙) — 원 스크립트
(2026-08-31_kr_theme_leader_reignition.py)를 모듈로 그대로 import해서
fetch/테마사이클추출/재점화판정(check_reignition)을 재사용한다.
confirm_entry_race()도 판정 조건 자체는 한 글자도 안 바꾸고(원 함수와
나란히 두고 비교 가능하게, entry/stop/confirm 조건 전부 동일), 그 안에서
"터치"(가격조건만 충족, 거래량 무관 — v5.190에서 theme_reignition.py
실시간 감시에 추가한 것과 같은 개념)가 최초로 일어난 날짜도 같이
기록하도록만 확장했다(confirm_entry_race_with_touch).

저장 필드(이벤트당): ticker/theme/d0_date/reignite_date(check_reignition이
찾은 팝일)/touch_date(재점화일 다음 최대 3거래일 내 최초 고가≥신호일고가
날짜, 거래량 무관)/confirm_date(고가+거래량 둘 다 충족한 날, None이면
그 이벤트는 미확인)/entry(신호일 고가)/stop(신호일 저가)/outcome/r.

시기반분: 사용자 지시대로 z검정 없이 EV·승률만 반씩 나눠 보고 — 확인일
(confirm_date) 오름차순 정렬 후 정확히 반으로. n=53(전후 26/27)이라
z 자체가 무의미한 수준(신뢰구간이 극단적으로 넓음)인 걸 이미 알고
시작하는 조사라, 여기서 등급을 올리거나 내리지 않는다(사용자 지시:
"등급 변경 없음") — 참고용 방향 확인일 뿐.

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-31_kr_theme_leader_reignition_events.py`
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


def confirm_entry_race_with_touch(df, reignite_pos, max_confirm_bars=CONFIRM_BARS, max_race_bars=60):
    """원 스크립트 confirm_entry_race()와 판정 조건 완전히 동일(고가+
    거래량 둘 다 충족한 첫 날에서 레이스 시작) — 그 과정에서 "터치"(고가
    조건만, 거래량 무관)가 최초로 일어난 위치도 같이 추적만 한다. 확인
    실패(반환 confirm_pos=None) 이벤트는 원 스크립트와 동일하게 EV 표본
    (nv)에서 빠진다(outcome=None)."""
    n = len(df)
    sig_high = float(df["High"].iloc[reignite_pos])
    sig_low = float(df["Low"].iloc[reignite_pos])
    vols = df["Volume"]
    touch_pos = None
    for k in range(1, max_confirm_bars + 1):
        j = reignite_pos + k
        if j >= n:
            break
        hi = float(df["High"].iloc[j])
        if touch_pos is None and hi >= sig_high:
            touch_pos = j
        avg50 = vols.iloc[max(0, j - CONFIRM_VOL_AVG_WINDOW):j].mean()
        if not avg50 or avg50 <= 0:
            continue
        if hi >= sig_high and float(vols.iloc[j]) >= CONFIRM_VOL_MULT * avg50:
            entry = sig_high
            stop = sig_low
            future = df.iloc[j:j + max_race_bars] if j + max_race_bars <= n else df.iloc[j:n]
            outcome, r = harness.race(entry, stop, future, max_bars=max_race_bars)
            return {"confirm_pos": j, "touch_pos": touch_pos, "entry": entry, "stop": stop,
                    "outcome": outcome, "r": r}
    return {"confirm_pos": None, "touch_pos": touch_pos, "entry": None, "stop": None,
            "outcome": None, "r": None}


def summarize(events):
    outcomes = [(e["outcome"], e["r"]) for e in events]
    return harness.ev_summary(outcomes)


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

    events = []
    for r in reignited_leader:
        df_t = data[r["ticker"]]
        res = confirm_entry_race_with_touch(df_t, r["reignite_pos"])
        reignite_date = str(df_t.index[r["reignite_pos"]].date())
        touch_date = str(df_t.index[res["touch_pos"]].date()) if res["touch_pos"] is not None else None
        confirm_date = str(df_t.index[res["confirm_pos"]].date()) if res["confirm_pos"] is not None else None
        events.append({
            "ticker": r["ticker"], "theme": r["theme"], "d0_date": r["d0_date"],
            "reignite_date": reignite_date, "touch_date": touch_date, "confirm_date": confirm_date,
            "entry": res["entry"], "stop": res["stop"], "outcome": res["outcome"], "r": res["r"],
        })

    valid_events = [e for e in events if e["outcome"] is not None]
    print(f"[main] 확인진입 유효 이벤트: {len(valid_events)}/{len(events)} (원측정 53/56과 대조)", flush=True)

    # ── 시기반분(사용자 지시): z검정 없이 EV·승률만 반씩 — confirm_date 오름차순 정렬 후 정확히 반 ──
    valid_sorted = sorted(valid_events, key=lambda e: e["confirm_date"])
    half = len(valid_sorted) // 2
    first_half, second_half = valid_sorted[:half], valid_sorted[half:]
    ev_first = summarize(first_half)
    ev_second = summarize(second_half)
    dir_first = (ev_first.get("ev_R") or 0) > 0
    dir_second = (ev_second.get("ev_R") or 0) > 0
    direction_consistent = dir_first == dir_second

    print(f"[초반 n={len(first_half)}] {first_half[0]['confirm_date'] if first_half else '-'}"
          f"~{first_half[-1]['confirm_date'] if first_half else '-'}: EV={ev_first.get('ev_R')} "
          f"승률(target_rate)={ev_first.get('target_rate')}", flush=True)
    print(f"[후반 n={len(second_half)}] {second_half[0]['confirm_date'] if second_half else '-'}"
          f"~{second_half[-1]['confirm_date'] if second_half else '-'}: EV={ev_second.get('ev_R')} "
          f"승률(target_rate)={ev_second.get('target_rate')}", flush=True)
    print(f"[방향] 초반 {'양수' if dir_first else '음수/0'} · 후반 {'양수' if dir_second else '음수/0'} "
          f"— {'방향 일관' if direction_consistent else '방향 불일치'} (z검정 안 함, 참고용 — 등급 변경 없음)",
          flush=True)

    report = {
        "n_reignited_leader_events": len(reignited_leader),
        "n_events_total": len(events), "n_valid": len(valid_events),
        "overall_ev": summarize(valid_events),
        "first_half": {
            "n": len(first_half),
            "date_range": [first_half[0]["confirm_date"], first_half[-1]["confirm_date"]] if first_half else None,
            "ev_R": ev_first.get("ev_R"), "target_rate": ev_first.get("target_rate"),
            "stop_rate": ev_first.get("stop_rate"),
        },
        "second_half": {
            "n": len(second_half),
            "date_range": [second_half[0]["confirm_date"], second_half[-1]["confirm_date"]] if second_half else None,
            "ev_R": ev_second.get("ev_R"), "target_rate": ev_second.get("target_rate"),
            "stop_rate": ev_second.get("stop_rate"),
        },
        "direction_consistent": direction_consistent,
        "note": "z검정 없음(사용자 지시) — 참고용 방향 체크일 뿐, 등급 변경 근거 아님. "
                "n=53 반분이면 각 반 n≈26이라 z를 냈어도 신뢰구간이 매우 넓었을 것.",
        "events": events,
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "2026-08-31_kr_theme_leader_reignition_events.results.json")
    with open(out_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    main()

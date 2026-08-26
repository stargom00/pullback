"""
US 눌림목 EV — 시간 분할 재현 확인 (2026-08-26, 사용자 지시)

`docs/pullback_ev_kr_us_regime_investigation.md`에서 확인된 US 눌림목
단독 EV **+0.206R**(n=1328, off=60~250 20개 체크포인트, KR+US 혼합
재실행 후 시장별 분해)이 GUIDE.md에서 신뢰 근거로 쓰이고 있다. 이번엔
그 값이 측정 기간(off=60~250, 약 1년) 전체에 걸쳐 안정적인지, 아니면
특정 구간에만 몰린 결과인지 시간 분할로 확인한다.

같은 문서 "③ 슈퍼대장 소속 EV — KR/US 분해 + 기간 분해" 절에서 이미
쓴 관례(off를 절반으로 나눠 최근/이전 비교 — 슈퍼대장 KR 서브셋에
적용됨)를 이번엔 **US 눌림목 전체(슈퍼대장 아님)**에 그대로 적용한다:
  - 최근 절반(off 60~150, 10개 체크포인트) = "후반부"
  - 이전 절반(off 160~250, 10개 체크포인트) = "전반부"

【사전 판정 기준 — 실행 전 확정, 사용자 지시】
- 전반부·후반부 **둘 다** EV가 양수 **AND** 각각 **+0.1R 이상**이면
  → "시간 안정적 엣지"로 확인: GUIDE.md의 US 수치(+0.206R)에 신뢰
  근거로 기록.
- 한쪽이라도 음수이거나 ~0(0.1R 미만)이면 → US 엣지도 시기 의존적
  이라는 뜻이므로 캐비어트로 기록(GUIDE.md/docs에 "이 시기에만"
  명시).

【US만 fetch하는 이유】
`harness.compute_rs_at_checkpoint`는 KR/US를 분리 집계해 각자
`to_rs_rank()`하므로(코드 확인, 이미 여러 조사에서 검증됨) US 랭크는
KR 유니버스 존재 여부와 무관하다 — KR을 안 섞어도 US 결과가 동일해
US만 fetch해 시간을 절약한다. `compute_rs_at_checkpoint`의
b_kospi/b_kosdaq 인자는 KR 종목에만 쓰이므로 0.0 고정.

harness.py 재사용(README 규칙3), scanner.py/app.py 무수정.

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-26_pullback_us_ev_time_split.py`
(US 유니버스만 fetch — KR 포함 조사보다 빠름, 2~3분 내외 예상)
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze, CONFIG

OFFSETS = harness.checkpoints(60, 250, 10)  # 원 조사(pullback_ev_kr_us_regime_investigation)와 동일
MIN_BARS_FLOOR = CONFIG["min_bars"]
RECENT_OFFSETS = set(OFFSETS[:10])   # off 60~150 — 측정기간 내 "최근"(후반부)
OLDER_OFFSETS = set(OFFSETS[10:])    # off 160~250 — 측정기간 내 "이전"(전반부)

EV_MIN_R = 0.1  # 사전 정의: "시간 안정적 엣지" 확인에 필요한 절반당 최소 EV


def collect_us_pullback_hits(data):
    hits = []
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        trunc_cache = {}
        for t, df in data.items():
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, 0.0, 0.0)

        for t, hist in trunc_cache.items():
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            try:
                hit = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=False)
            except Exception:
                continue
            if hit is None:
                continue
            if not harness.passes_liquidity_filter(hit, is_kr=False):
                continue
            outcome = harness.race(hit.get("close"), hit.get("stop"), harness.future_after(data[t], off))
            hits.append({"ticker": t, "off": off, "outcome": outcome})
        print(f"[PASS1] off={off} hits_so_far={len(hits)} elapsed={time.time()-t0:.0f}s ({oi+1}/{len(OFFSETS)})", flush=True)
    return hits


def ev_of(hits):
    return harness.ev_summary([h["outcome"] for h in hits])


def run(data):
    hits = collect_us_pullback_hits(data)
    daily_avg = round(len(hits) / len(OFFSETS), 1)
    print(f"[SUMMARY] US 눌림목 히트 {len(hits)}건(일평균 {daily_avg}), "
          f"유니크 종목 {len({h['ticker'] for h in hits})}개", flush=True)

    overall = ev_of(hits)
    recent = [h for h in hits if h["off"] in RECENT_OFFSETS]   # 후반부
    older = [h for h in hits if h["off"] in OLDER_OFFSETS]     # 전반부
    ev_recent = ev_of(recent)
    ev_older = ev_of(older)
    print(f"[전체] n={len(hits)} {overall}", flush=True)
    print(f"[전반부(이전) off160~250] n={len(older)} {ev_older}", flush=True)
    print(f"[후반부(최근) off60~150] n={len(recent)} {ev_recent}", flush=True)

    evs = [ev_older["ev_R"], ev_recent["ev_R"]]
    both_positive = all(e is not None and e > 0 for e in evs)
    both_above_min = all(e is not None and e >= EV_MIN_R for e in evs)
    if both_positive and both_above_min:
        verdict = "시간 안정적 엣지 확인 — GUIDE 신뢰 근거로 기록"
    else:
        verdict = "시기 의존적 — 캐비어트 기록"
    print(f"\n[최종판정] {verdict} — 전반부(이전)={evs[0]} 후반부(최근)={evs[1]}", flush=True)
    return overall, ev_older, ev_recent, verdict


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(markets=("us",))
    run(data)

"""KR fetch 창 730일 → 1900일 확대의 **타 탭 회귀** 검사 (v5.268 전제).

배경: 🔺 ABC 탭의 기준선을 MA200 → MA600으로 바꾸려면 600봉이 필요한데,
현재 KR 창 730일은 **487봉**이라 MA600이 한 봉도 안 나온다(실측). 창을
1900일(≈1275봉)로 넓혀야 한다. 그런데 이 번들은 **전 탭이 공유**하므로,
52주 고저·베이스 카운트처럼 lookback에 기대는 지표가 달라져 기존 탭 히트가
바뀔 수 있다.

사용자 지시(2026-09-18): "730/1900 두 번 돌려 탭별 hit 건수·순위 비교.
0건 차이 → 진행. 차이 있으면 멈추고 어느 탭·어느 지표가 원인인지 보고.
**RS는 252봉 고정이라 안 바뀌어야 정상. 바뀌면 그게 버그다.**"

v5.28(400→730일)에서 같은 방식으로 "hit 건수·순위 완전 동일"을 확인한 선례가
있다(CLAUDE.md "알려진 설계 갭"). 그 관례를 그대로 따른다.

부작용 차단: 이 스크립트는 **프로덕션 상태를 건드리면 안 된다** —
신호 스냅샷 기록(`_record_signal_snapshot`)과 디스크 캐시 읽기/쓰기를 전부
막고 돌린다. 막지 않으면 회귀 검사가 실거래 기록을 오염시킨다.

**실행 시각이 중요하다 — 반드시 KST 20:10 이후**(애프터마켓 종료 + 여유,
`app.KR_CLOSE_CONFIRMED_HM`). 이건 90cp 측정 규칙(CLAUDE.md) 때문만이 아니라
이 검사 고유의 이유가 있다: 730 스캔과 1900 스캔은 **몇 분 간격으로 순차
실행**되므로, 장중이면 그 사이 마지막 봉이 갱신돼 **창 폭과 무관한 차이가
섞인다**(회귀로 오판하게 된다). 두 스캔이 같은 확정봉을 봐야 비교가 성립한다.
약 10~15분 소요.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import app            # noqa: E402
import naver_kr       # noqa: E402

MODES = list(app.GATE_MODE_LABELS)          # pullback/turnaround/breakout/boxbreak/imminent
OUT = Path(__file__).with_suffix(".json")


def _neutralize():
    """프로덕션 상태 오염 차단 — 스냅샷 기록·디스크 캐시 전부 무력화."""
    app._record_signal_snapshot = lambda *a, **k: False
    app._save_disk_cache = lambda *a, **k: None
    app._load_disk_cache = lambda *a, **k: None


async def _scan_all(days: int) -> dict:
    """KR 창을 days로 고정하고 전 탭을 스캔한다."""
    orig = naver_kr.fetch
    naver_kr.fetch = lambda ticker: naver_kr.fetch_history(ticker, days=days)
    app._data_cache.clear()
    try:
        out = {}
        t0 = time.time()
        for i, mode in enumerate(MODES):
            # 첫 모드만 force=True — 번들을 새로 받고, 나머지 모드는 그 번들을 재사용
            # (프로덕션도 모드 전환 시 같은 번들을 쓴다).
            r = await app.run_scan("kr", mode, refresh=(i == 0))
            hits = r.get("hits") or []
            out[mode] = {
                "n": len(hits),
                "order": [h["ticker"] for h in hits],
                "score": {h["ticker"]: h.get("score") for h in hits},
                "rs": {h["ticker"]: h.get("rs_rank") for h in hits},
            }
            print(f"  {mode:12s} {len(hits):4d}건", flush=True)
        bundle = app._data_cache.get("data:kr") or {}
        data = bundle.get("data") or {}
        bars = sorted(len(d) for d in data.values())
        out["_meta"] = {
            "days": days, "elapsed_s": round(time.time() - t0, 1),
            "n_tickers": len(data),
            "bars_median": bars[len(bars) // 2] if bars else 0,
            "bars_min": bars[0] if bars else 0, "bars_max": bars[-1] if bars else 0,
            "rs_ranks": dict(list((bundle.get("rs_ranks") or {}).items())),
        }
        return out
    finally:
        naver_kr.fetch = orig


def _cmp(a: dict, b: dict) -> list[str]:
    """730 대비 1900의 차이를 사람이 읽을 수 있게 뽑는다."""
    diffs = []
    for mode in MODES:
        x, y = a[mode], b[mode]
        if x["n"] != y["n"]:
            only_a = [t for t in x["order"] if t not in set(y["order"])]
            only_b = [t for t in y["order"] if t not in set(x["order"])]
            diffs.append(f"[{mode}] 건수 {x['n']}→{y['n']} · 730만 {only_a[:8]} · 1900만 {only_b[:8]}")
        elif x["order"] != y["order"]:
            moved = [t for i, t in enumerate(x["order"]) if y["order"][i] != t]
            diffs.append(f"[{mode}] 건수 같음({x['n']})인데 **순위 다름** — {moved[:8]}")
        # 점수가 흔들리면 건수·순위가 같아도 경계 종목이 다음 날 갈린다
        sd = [t for t in x["order"]
              if t in y["score"] and x["score"][t] != y["score"][t]]
        if sd:
            diffs.append(f"[{mode}] score 불일치 {len(sd)}건 — 예: "
                         + ", ".join(f"{t} {x['score'][t]}→{y['score'][t]}" for t in sd[:4]))
    # RS는 252봉 고정 — 창이 넓어져도 **바뀌면 버그**다(사용자 지시).
    ra, rb = a["_meta"]["rs_ranks"], b["_meta"]["rs_ranks"]
    common = set(ra) & set(rb)
    rs_diff = [t for t in common if ra[t] != rb[t]]
    if rs_diff:
        diffs.append(f"⚠️ **RS 랭크가 바뀌었다 {len(rs_diff)}/{len(common)}건** — 252봉 고정이라 "
                     f"바뀌면 버그다. 예: "
                     + ", ".join(f"{t} {ra[t]}→{rb[t]}" for t in rs_diff[:5]))
    return diffs


async def main():
    _neutralize()
    print("[730일] 스캔", flush=True)
    a = await _scan_all(730)
    print(f"  → {a['_meta']}", flush=True)
    print("[1900일] 스캔", flush=True)
    b = await _scan_all(1900)
    print(f"  → {b['_meta']}", flush=True)

    diffs = _cmp(a, b)
    OUT.write_text(json.dumps({"d730": a, "d1900": b, "diffs": diffs},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + "=" * 60)
    if not diffs:
        print("✅ 회귀 0건 — 탭별 hit 건수·순위·score·RS 전부 동일")
    else:
        print(f"❌ 차이 {len(diffs)}건 — **진행 중단하고 보고**")
        for d in diffs:
            print("  " + d)
    print(f"결과: {OUT}")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

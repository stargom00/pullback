"""측정 D 1단계 — 표본 건수만 센다(판정 없음).

사용자 지시: "먼저 +15% 이상 건수만 세서 보고. 문턱 못 채우면 판정 불가로 종결.
n>=30이면 진행."

표본: docs/kr_jongga_betting_backtest.md의 **조합 A(base+candle) 276건**.
독립변수: T일 등락률(ret_t) >= +15% vs 미만. +15%는 임의값(이노메트리 +22.5%에서
눈으로 잡음, 측정·문헌 근거 없음).

이 스크립트는 **건수와 분포만** 출력한다. 갭 비교·검정은 사전등록 문서를 쓰고
커밋으로 고정한 뒤 별도 스크립트에서 한다(결과를 보고 문턱을 정하지 않기 위해).
따라서 여기서는 **군별 갭 통계를 계산·출력하지 않는다** — 보면 블라인드가 깨진다.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_m = __import__("2026-08-29_kr_jongga_betting_backtest_extended")

SURGE_MIN = 0.15          # 임의값(사용자 지시) — 격자탐색 금지
N_MIN = 30                # 사용자 지시: n>=30이면 진행


REF_TOTAL, REF_COMBO_A = 121698, 276   # docs/kr_jongga_betting_backtest.md 원측정
GATE_TOL = 0.05                        # ±5% — 앵커 날짜가 2주 다르니 ±2%는 과하다


def main():
    import pickle
    cache = os.environ.get("MEAS_CACHE")
    if cache and os.path.exists(cache):
        print(f"[cache] load {cache}", flush=True)
        data = pickle.load(open(cache, "rb"))
    else:
        data = _m.fetch_kr_long_universe()
        if cache:
            pickle.dump(data, open(cache, "wb"))
    print(f"[data] {len(data)} tickers", flush=True)

    records = []
    for off in _m.OFFSETS:
        rank_at_off = _m.turnover_rank_at(data, off)
        records.extend(_m.evaluate(data, off, rank_at_off))
    print(f"[records] 전체 {len(records)}", flush=True)

    # 조합 A = base + candle + volume + position (4조건 전부).
    # 원본 스크립트 main()의 combo_a 정의(271행)와 문자 그대로 같게 맞춘다 —
    # 처음엔 base+candle 둘만으로 잡아 n=1103이 나왔고(문서 276의 4배),
    # 재현 게이트에서 바로 걸렸다. 문서 표의 라벨 "(A) 원조합(+candle)"에서
    # "원조합"이 base+volume+position을 가리킨다는 걸 놓친 것.
    combo_a = [r for r in records
               if r["base"] and r["candle"] and r["volume"] and r["position"]]
    surge = [r for r in combo_a if r["ret_t"] >= SURGE_MIN]
    rest = [r for r in combo_a if r["ret_t"] < SURGE_MIN]

    # ── 재현 게이트 — 파이프라인이 원측정과 같게 도는지 먼저 확인 ──
    for label, got, ref in (("전체 평가", len(records), REF_TOTAL),
                            ("조합 A", len(combo_a), REF_COMBO_A)):
        diff = got / ref - 1
        mark = "OK" if abs(diff) <= GATE_TOL else "FAIL"
        print(f"[gate] {label}: {got} vs 원측정 {ref} ({diff*100:+.2f}%) -> {mark}", flush=True)

    print(f"\n=== 조합 A(base+candle+volume+position) n = {len(combo_a)}  (문서 기준 {REF_COMBO_A})")
    print(f"  +{SURGE_MIN*100:.0f}% 이상 : {len(surge)}")
    print(f"  미만        : {len(rest)}")

    # 표본이 얼마나 빠듯한지 보이려고 경계 주변 분포만 — 갭은 보지 않는다
    print("\n[참고] 조합 A의 당일 등락률 분포(건수만):")
    for lo, hi in [(0.03, 0.05), (0.05, 0.08), (0.08, 0.10), (0.10, 0.15),
                   (0.15, 0.20), (0.20, 0.30), (0.30, 9.99)]:
        n = sum(1 for r in combo_a if lo <= r["ret_t"] < hi)
        print(f"  {lo*100:5.0f}% ~ {hi*100:5.0f}% : {n}")

    # 시기 반분도 미리 본다 — 사전등록의 '시기 반분 재현' 문턱을 걸 수 있는지 판단용
    for half in ("recent", "earlier"):
        n = sum(1 for r in surge if r["half"] == half)
        print(f"  [시기] {half}: {n}")

    ok = len(surge) >= N_MIN
    print(f"\n판정: n={len(surge)} {'>=' if ok else '<'} {N_MIN} → "
          f"{'진행 가능' if ok else '판정 불가로 종결'}")


if __name__ == "__main__":
    main()

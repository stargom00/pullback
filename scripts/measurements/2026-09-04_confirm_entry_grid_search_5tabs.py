"""
확인진입(안C류) 조건 정밀화 — 거래량배수×종가위치 격자탐색, 5탭 ×
KR/US (2026-09-04, 사용자 지시, README 규칙6·7·8·9).

【배경】현재 확인진입 조건("종가 > 피벗 & 거래량 ≥ 1.5배")은 측정으로
정해진 값이 아니다 — `_pending_watch_confirm_check()`(app.py)가 원래
눌림목 안C'의 정의(2026-08-14 조사 당시 사람이 고른 값)를 그대로
가져다 돌파임박에도 공용 적용한 것이고, 그 1.5배/0%라는 숫자 자체를
격자탐색으로 검증한 적은 없다. 이번엔 그 두 파라미터(거래량배수·종가
위치)를 격자로 바꿔가며 EV가 개선되는 지점이 있는지 확인한다.

**⚠️ 정정(초판 실행 후 발견) — "피벗"의 두 가지 서로 다른 의미.**
초판은 "피벗"을 `analyze_*()`가 반환하는 구조적 저항선 필드
(`hit['pivot']`)로 해석해 돌렸는데, 결과 EV가 0.08~0.18R대로 나와
이미 확립·채택된 안C/안C' EV(0.6~1.06R, `docs/kr_us_strategy_map.md`
"KR 확인진입 5탭 재검증(2026-09-04)" 절, 근거 스크립트
`2026-09-04_kr_confirm_entry_all_tabs_90cp.py`)와 7배 가까이 벌어져
원인을 추적했다. 확인 결과 그 기확립 측정(과 원조인
`2026-09-01_confirm_entry_90cp_revalidation.py`)은 "피벗"을
**신호일 실제 고가**(`hist["High"].iloc[-1]`, `signal_high`)로 정의하고,
**진입가도 확인일 종가가 아니라 그 신호일고가 레벨 자체**(지정가
주문이 그 레벨에서 체결된다고 가정)를 쓴다 — 확인 조건 판정에만
종가(또는 원 정의는 고가)를 쓸 뿐, 실제 레이스 진입가는 항상 그
레벨로 고정. 초판은 이 두 가지(기준선=피벗필드, 진입가=확인일
실제종가)를 전부 다르게 구현해 기확립 정의와 괴리됐던 것 — 아래
코드는 기확립 정의(신호일고가 기준선 + 그 레벨 자체가 진입가)로
수정해 재실행한 결과다. "피벗"이라는 단어 자체가 production 주석
(`_pending_watch_confirm_check` docstring: "피벗(신호일 고가 레벨)")
에서도 두 의미를 섞어 쓰고 있어 헷갈리기 쉽다는 점을 기록해둔다 —
`hit['pivot']` 필드와 "신호일 고가"는 특히 눌림목/추세전환처럼 피벗이
아직 안 뚫린 탭에서 값 차이가 클 수 있다(돌파임박은 정의상 피벗
근접(-5%~0%) 구간이라 둘이 비교적 가깝다).

【대상 5탭】눌림목/돌파임박/돌파/박스돌파/추세전환 — pivot(구조적
저항선)+stop(구조적 손절)이 있는 게이트형 탭 전부. 현재 프로덕션에
확인규칙이 실제로 있는 탭은 눌림목·돌파임박 둘뿐(`CONFIRM_RULE_BY_TAB`,
app.py) — 돌파/박스돌파/추세전환은 확인규칙 자체가 없어 "현행 유지"가
곧 "프로덕션 동작 불변"을 뜻하지 않는다(원래도 없었음). 이 3탭에 대해선
이번 측정이 "새 확인규칙을 도입할 근거가 있는가"를 묻는 것이고, 나머지
2탭에 대해선 "현재 값(1.5배/0%)을 다른 값으로 바꿀 근거가 있는가"를
묻는 것이다 — 성격이 다르므로 결과 해석 시 구분해서 본다.

**돌파/박스돌파는 이미 "돌파완료" 신호라는 점에 유의**: 눌림목/돌파임박/
추세전환은 피벗을 아직 안 뚫은(또는 막 뚫은) 시점의 신호라 "확인"이
"진짜 돌파인지 재확인"의 의미를 갖지만, 돌파/박스돌파는 신호일 자체가
이미 피벗을 거래량 동반 돌파한 날이라 여기서 또 "종가>피벗+거래량≥X배"를
요구하면 사실상 "돌파 다음날 이후 며칠 내 재돌파(연속 강세)를 요구"하는
것에 가깝다 — 원래의 "확인 후 진입" 개념과는 다른 질문이지만, 기계적으로
같은 격자를 적용해 EV가 개선되는지는 여전히 잘 정의된 질문이라 그대로
측정한다.

【격자】거래량배수 {1.0,1.3,1.5,2.0,3.0} × 종가위치(신호일고가 기준)
{초과(0%), +0.5%, +1%} = 15조합. 확인 윈도우는 원 정의 그대로 신호일
다음 최대 3봉(고정, 격자 축 아님) — k_max를 격자에 넣으면 축이 3개가
돼 과적합 위험이 더 커지고, 사용자가 준 격자에도 없다.

**손절 정의는 이번 탐색에서 건드리지 않는다** — 각 탭 `analyze_*()`가
반환하는 구조적 stop을 그대로 쓴다(돌파임박의 "신호일저가손절" 같은
재정의는 안 함). 이유: 요인분해 원칙(2026-09-01 조사와 동일 논리) —
확인조건(거래량배수·종가위치)과 손절정의를 동시에 바꾸면 EV 변화가
어느 축 때문인지 구분 불가.

확인 판정은 **종가** 기준(원 정의 그대로 — 09-01 조사에서 종가기준이
고가기준보다 우수함이 이미 확인됨, 재검증 안 함). **진입가는 확인일의
실제 종가가 아니라 기준선(신호일고가×(1+margin)) 그 자체**다 — 기확립
정의(위 정정 참고)와 동일하게, 그 가격에 지정가 주문이 체결된다고
가정한다(확인일 종가가 이미 그 레벨을 넘어선 값이라 실제 종가로
진입가를 잡으면 체결 낙관 편향이 생김 — 기확립 스크립트가 이미 피한
함정을 그대로 재사용).

【통계】README 규칙6(대조군 유동성 매칭 — harness.passes_liquidity_filter
공통 적용으로 자동 충족)·규칙7(z검정, harness.ev_gap_zscore로 현행
(1.5배,0%)과의 격차 유의성)·규칙8(KR/US 분해 병기 — 그리드 표는 혼합
코호트로만 내고, 채택 후보로 뽑힌 조합만 KR/US 분해까지 별도 확인)·
규칙9(90개 체크포인트, 채택 판정은 시기반분 재현까지).

**과적합 경계(사용자 주의사항 반영)**: 15개 조합 중 "가장 좋은 조합
하나"를 채택 기준으로 삼지 않는다 — 사전 등록 기준(현행 대비 +0.15R &
z≥1.96)을 통과한 모든 후보에 대해 시기반분(전반부/후반부) 재현을
따로 확인하고, 재현된 조합만 최종 채택 후보로 남긴다. 인접 조합들이
집단으로 좋아야("안정적으로 좋은 영역") 신뢰하고, 격자 안에서 고립된
극값 하나만 좋으면(주변 조합은 안 좋은데 그 점만 튀면) 과적합으로
간주해 후보에서 배제(아래 "고립점 감지" 절 참고).

확인율 5% 미만 조합은 표에는 남기되 채택 후보에서 제외(실용성 없음,
사전 등록).

harness.py 재사용, 무수정(README 규칙3) — 다른 세션이 GUIDE.md/
static/index.html을 작업 중일 수 있어 이번에도 기존 파일은 건드리지
않는다. 스크립트만 신규, 커밋 안 함(사용자 지시).

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-04_confirm_entry_grid_search_5tabs.py`
(5탭 × 90 체크포인트 확장 fetch — 총 20~40분 예상, 네트워크 필요).
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import (analyze, CONFIG, analyze_imminent, IMMINENT_CONFIG,
                      analyze_breakout, BREAKOUT_CONFIG, analyze_boxbreak, BOXBREAK_CONFIG,
                      analyze_turnaround, TURN_CONFIG)

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — README 규칙9 표준
VOL_MULTS = [1.0, 1.3, 1.5, 2.0, 3.0]
MARGINS = [0.0, 0.005, 0.01]        # 신호일고가 초과 / +0.5% / +1%
MARGIN_LABEL = {0.0: "신호일고가초과", 0.005: "+0.5%", 0.01: "+1%"}
BASELINE = (1.5, 0.0)               # 현행(프로덕션 _pending_watch_confirm_check와 동일)
K_MAX = 3
GAP_MIN_R = 0.15
Z_MIN = 1.96
MIN_CONFIRM_RATE = 0.05

RECENT_OFFSETS = set(OFFSETS[:45])   # off 60~500 — 최근(후반부)
OLDER_OFFSETS = set(OFFSETS[45:])    # off 510~950 — 이전(전반부)

TAB_SPECS = {
    "눌림목": (analyze, CONFIG),
    "돌파임박": (analyze_imminent, IMMINENT_CONFIG),
    "돌파": (analyze_breakout, BREAKOUT_CONFIG),
    "박스돌파": (analyze_boxbreak, BOXBREAK_CONFIG),
    "추세전환": (analyze_turnaround, TURN_CONFIG),
}
MIN_BARS_FLOOR = max(cfg["min_bars"] for _, cfg in TAB_SPECS.values())


# ── 1) 히트 수집 (체크포인트별, 5개 탭 동시) ────────────────────────────
def collect_hits(data, bench):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None
    tickers = list(data.keys())
    hits = {tab: [] for tab in TAB_SPECS}
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)

        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            future = harness.future_after(data[t], off)
            if len(future) < 1:
                continue

            for tab, (fn, cfg) in TAB_SPECS.items():
                try:
                    h = fn(hist, rs_rank=rr, rs_mom=rm, cfg=cfg, is_kr=ikr)
                except Exception:
                    h = None
                if h is None or not harness.passes_liquidity_filter(h, ikr):
                    continue
                stop = h.get("stop")
                if stop is None:
                    continue
                signal_high = float(hist["High"].iloc[-1])
                if signal_high <= 0:
                    continue
                trailing50_vol = float(hist["Volume"].iloc[-50:].mean())
                hits[tab].append({
                    "ticker": t, "off": off, "is_kr": ikr,
                    "signal_high": signal_high, "stop": stop,
                    "trailing50_vol": trailing50_vol, "future": future,
                })

        print(f"[collect] off={off} done ({oi+1}/{len(OFFSETS)}) elapsed={time.time()-t0:.0f}s "
              + " ".join(f"{k}={len(v)}" for k, v in hits.items()), flush=True)
    return hits


# ── 2) 확인 판정 + 레이스 (격자 콤보별, 인메모리 — 재fetch/재analyze 없음) ──
def find_confirm(h, vol_mult, margin):
    """기준선=신호일고가(established 정의, 위 정정 참고). 반환 k만 —
    진입가는 confirm_rows에서 threshold 자체로 고정(확인일 실제 종가
    아님)."""
    base_vol = h["trailing50_vol"]
    threshold = h["signal_high"] * (1 + margin)
    fut = h["future"]
    avail = min(K_MAX, len(fut))
    for k in range(1, avail + 1):
        c = float(fut["Close"].iloc[k - 1])
        v = float(fut["Volume"].iloc[k - 1])
        if c > threshold and base_vol > 0 and v >= vol_mult * base_vol:
            return k, threshold
    return None


def combo_rows(tab_hits, vol_mult, margin):
    rows = []
    for h in tab_hits:
        conf = find_confirm(h, vol_mult, margin)
        if conf is None:
            continue
        k, trigger_price = conf
        fut_after = h["future"].iloc[k:]
        outcome = harness.race(trigger_price, h["stop"], fut_after)
        rows.append({"ticker": h["ticker"], "off": h["off"], "is_kr": h["is_kr"], "outcome": outcome})
    return rows


def market_split(rows):
    kr = [r for r in rows if r["is_kr"]]
    us = [r for r in rows if not r["is_kr"]]
    return harness.ev_summary([r["outcome"] for r in kr]), harness.ev_summary([r["outcome"] for r in us])


# ── 3) 시기반분 재현 (채택 후보만) ──────────────────────────────────────
def half_check(tab_hits, vol_mult, margin):
    older_hits = [h for h in tab_hits if h["off"] in OLDER_OFFSETS]
    recent_hits = [h for h in tab_hits if h["off"] in RECENT_OFFSETS]
    out = {}
    for label, half_hits in (("전반부(이전, off510~950)", older_hits), ("후반부(최근, off60~500)", recent_hits)):
        base_rows = combo_rows(half_hits, *BASELINE)
        cand_rows = combo_rows(half_hits, vol_mult, margin)
        base_ev = harness.ev_summary([r["outcome"] for r in base_rows])
        cand_ev = harness.ev_summary([r["outcome"] for r in cand_rows])
        z, sig = harness.ev_gap_zscore(base_ev, cand_ev) if (base_ev["ev_R"] is not None and cand_ev["ev_R"] is not None) else (None, False)
        gap = None if (base_ev["ev_R"] is None or cand_ev["ev_R"] is None) else round(cand_ev["ev_R"] - base_ev["ev_R"], 4)
        out[label] = {"baseline": base_ev, "candidate": cand_ev, "gap_R": gap, "z": z, "significant": sig}
    return out


# ── 4) 고립점 감지 — "가장 좋은 조합 하나"가 과적합인지 확인 ────────────
def isolation_check(tab_report_combos, key, vol_mult, margin):
    """같은 vol_mult에서 margin 이웃, 같은 margin에서 vol_mult 이웃의 gap을
    같이 보고 — 이웃 대부분이 그 조합만큼 안 좋으면(격자상 고립된 봉우리)
    과적합 의심으로 표시만 한다(자동 배제는 안 함, 사용자 판단 참고용)."""
    neighbors = []
    for vm in VOL_MULTS:
        if vm == vol_mult:
            continue
        k2 = f"vol{vm}_m{int(margin*1000)}"
        c = tab_report_combos.get(k2)
        if c and c["gap_vs_baseline"] is not None:
            neighbors.append(c["gap_vs_baseline"])
    for mg in MARGINS:
        if mg == margin:
            continue
        k2 = f"vol{vol_mult}_m{int(mg*1000)}"
        c = tab_report_combos.get(k2)
        if c and c["gap_vs_baseline"] is not None:
            neighbors.append(c["gap_vs_baseline"])
    if not neighbors:
        return "이웃없음(판단불가)"
    better_neighbors = sum(1 for g in neighbors if g >= GAP_MIN_R * 0.5)
    return f"이웃 {len(neighbors)}개 중 {better_neighbors}개가 절반 문턱(+{GAP_MIN_R*0.5:.3f}R) 이상"


# ── 5) 실행 ─────────────────────────────────────────────────────────
def run(data, bench, out_path=None):
    hits = collect_hits(data, bench)
    report = {"offsets": f"{OFFSETS[0]}..{OFFSETS[-1]} step10 ({len(OFFSETS)}개)", "tabs": {}}
    candidates = []

    for tab, tab_hits in hits.items():
        n_total = len(tab_hits)
        baseline_rows = combo_rows(tab_hits, *BASELINE)
        baseline_ev = harness.ev_summary([r["outcome"] for r in baseline_rows])
        tab_report = {"n_hits_total": n_total, "baseline_ev": baseline_ev, "combos": {}}

        for vm in VOL_MULTS:
            for mg in MARGINS:
                rows = combo_rows(tab_hits, vm, mg)
                ev = harness.ev_summary([r["outcome"] for r in rows])
                n_confirmed = len(rows)
                confirm_rate = round(n_confirmed / n_total, 4) if n_total else 0.0
                is_baseline = (vm, mg) == BASELINE
                if ev["ev_R"] is not None and baseline_ev["ev_R"] is not None:
                    z, sig = harness.ev_gap_zscore(baseline_ev, ev)
                    gap = round(ev["ev_R"] - baseline_ev["ev_R"], 4)
                else:
                    z, sig, gap = None, False, None
                excluded = confirm_rate < MIN_CONFIRM_RATE
                key = f"vol{vm}_m{int(mg*1000)}"
                cell = {
                    "vol_mult": vm, "margin": mg, "margin_label": MARGIN_LABEL[mg],
                    "is_baseline": is_baseline, "n_confirmed": n_confirmed, "confirm_rate": confirm_rate,
                    "ev": ev, "gap_vs_baseline": gap, "z_vs_baseline": z, "significant": sig,
                    "excluded_low_confirm_rate": excluded,
                }
                tab_report["combos"][key] = cell
                print(f"[{tab}][{key}] confirm_rate={confirm_rate} n={n_confirmed} "
                      f"EV={ev['ev_R']} gap={gap} z={z} sig={sig} excluded={excluded}", flush=True)
                if (not is_baseline and not excluded and gap is not None and gap >= GAP_MIN_R and sig):
                    candidates.append((tab, vm, mg))

        for key, cell in tab_report["combos"].items():
            if cell["gap_vs_baseline"] is not None:
                cell["isolation_note"] = isolation_check(tab_report["combos"], key, cell["vol_mult"], cell["margin"])
        report["tabs"][tab] = tab_report

    # ── 채택 후보 시기반분 재현 + KR/US 분해 ──
    report["채택_후보_검증"] = {}
    if candidates:
        for tab, vm, mg in candidates:
            key = f"{tab}_vol{vm}_m{int(mg*1000)}"
            tab_hits = hits[tab]
            split = half_check(tab_hits, vm, mg)
            older = split["전반부(이전, off510~950)"]
            recent = split["후반부(최근, off60~500)"]
            reproduced = (older["gap_R"] is not None and older["gap_R"] >= GAP_MIN_R and
                          recent["gap_R"] is not None and recent["gap_R"] >= GAP_MIN_R)
            cand_rows = combo_rows(tab_hits, vm, mg)
            kr_ev, us_ev = market_split(cand_rows)
            base_kr_ev, base_us_ev = market_split(combo_rows(tab_hits, *BASELINE))
            verdict = "채택(확인조건 변경)" if reproduced else "미달 — 현행 유지, 기록만"
            report["채택_후보_검증"][key] = {
                "시기반분": split, "재현": reproduced,
                "KR": {"후보": kr_ev, "기존(1.5배,0%)": base_kr_ev},
                "US": {"후보": us_ev, "기존(1.5배,0%)": base_us_ev},
                "verdict": verdict,
            }
            print(f"[채택검증][{key}] 전반부gap={older['gap_R']} 후반부gap={recent['gap_R']} "
                  f"재현={reproduced} → {verdict}", flush=True)
            print(f"[채택검증][{key}] KR 후보={kr_ev['ev_R']}(n={kr_ev['nv']}) vs 기존={base_kr_ev['ev_R']}(n={base_kr_ev['nv']})", flush=True)
            print(f"[채택검증][{key}] US 후보={us_ev['ev_R']}(n={us_ev['nv']}) vs 기존={base_us_ev['ev_R']}(n={base_us_ev['nv']})", flush=True)
    else:
        print("[결론] 사전 판정 기준(gap>=0.15R & z>=1.96, 확인율>=5%) 통과 조합 0건 "
              "→ 5탭×15조합 전부 현행(1.5배,신호일고가초과) 유지", flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(
        kr_days=1900, us_period="5y", validate_offsets=OFFSETS)
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-09-04_confirm_entry_grid_search_5tabs.results.json")
    run(data, bench, out_path=out)

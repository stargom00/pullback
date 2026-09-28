"""미국 슈퍼인베스터 13F 추정 매입단가 스크리너 — Phase 1 실행기.

    python3 tools/superinvestor/run.py [--quarters 5] [--windows 6] [--no-prices]

출력(전부 `tools/superinvestor/output/`):
  · superinvestor_YYYYMMDD.csv   종목별 1행
  · unmapped_cusips.csv          CUSIP→티커 매핑 실패(버리지 않는다)
콘솔: NEW/ADD 2명 이상 종목 수 + cluster 밀집도 상위 10(N/A 제외).

**모든 출력 머리에 "관심 신호 · 측정 전"을 적는다** — 이 스크리너는 후보를 고르는
장치일 뿐 검증된 수익 신호가 아니다(Phase 2 펀더멘털·밸류, Phase 3 백테스트는 범위 밖).
"""
from __future__ import annotations

import argparse
import csv
import os
from datetime import date, datetime

import figi
import prices as px
import screen
import secdata

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_HERE, "output")

CSV_HEADER_NOTE = (f"# {screen.SIGNAL_DISCLAIMER} — 13F 공개분 기반 관심 신호이고 "
                   "백테스트로 검증된 신호가 아니다(Phase 3에서 검증 예정). "
                   "pandas로 읽을 때: pd.read_csv(path, comment='#')")


def _fmt_investors(names: list[str], limit: int = 6) -> str:
    if len(names) <= limit:
        return "; ".join(names)
    return "; ".join(names[:limit]) + f"; …+{len(names) - limit}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="슈퍼인베스터 13F 스크리너(Phase 1)")
    ap.add_argument("--quarters", type=int, default=5, help="분류할 분기 수(기본 5) — "
                    "비교 기준으로 직전 1분기를 더 받는다")
    ap.add_argument("--windows", type=int, default=6, help="받을 데이터셋 접수구간 수")
    ap.add_argument("--no-prices", action="store_true", help="가격 단계 생략(13F 집계만 점검)")
    ap.add_argument("--asof", default=None, help="기준일(YYYY-MM-DD, 기본 오늘)")
    ap.add_argument("--allow-missing-investors", action="store_true",
                    help="보유명세를 한 분기도 못 찾은 투자자가 있어도 계속 진행"
                         "(기본은 실패 — CIK 오류를 조용히 넘기지 않는다)")
    args = ap.parse_args(argv)

    asof = datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else date.today()
    run_date = date.today().isoformat()
    investors = secdata.load_investors()
    periods = secdata.quarter_ends(args.quarters + 1, asof)   # +1 = 최초 분기의 비교 기준
    classify_periods = periods[1:]
    latest = periods[-1]

    print(f"=== 슈퍼인베스터 13F 스크리너 (Phase 1) — {screen.SIGNAL_DISCLAIMER} ===")
    print(f"실행일 {run_date} · 기준일 {asof} · 투자자 {len(investors)}명")
    print(f"대상 분기(분류) {classify_periods[0]} ~ {latest} "
          f"(비교 기준 분기 {periods[0]} 포함 {len(periods)}개 로드)")

    ciks = {c for i in investors for c in i["ciks_plain"]}
    holdings, used = secdata.load_holdings(ciks, periods, windows=args.windows)
    print(f"[sec] 데이터셋 구간: {', '.join(used)}")

    # ── 투자자별 분기 스냅샷: 비중·순위 ──
    weights: dict[tuple, dict] = {}          # (short, period) -> {cusip: weight_pct}
    ranks: dict[tuple, dict] = {}
    filer: dict[tuple, object] = {}          # (short, period) -> PeriodHoldings(그 분기 보고 법인)
    filer_cik: dict[tuple, str] = {}
    missing = []
    for inv in investors:
        for p in periods:
            cik, ph = secdata.pick_filer(inv, p, holdings)
            if ph is None:
                missing.append((inv["short"], str(p)))
                continue
            filer[(inv["short"], p)] = ph
            filer_cik[(inv["short"], p)] = cik
            tot = ph.total_value_usd
            weights[(inv["short"], p)] = {c: h["value_usd"] / tot * 100
                                         for c, h in ph.holdings.items()}
            order = sorted(ph.holdings.items(), key=lambda kv: -kv[1]["value_usd"])
            ranks[(inv["short"], p)] = {c: i + 1 for i, (c, _) in enumerate(order)}
    print(f"[sec] (투자자×분기) 보고 {len(filer)} / 기대 {len(investors) * len(periods)}")
    # 주 CIK가 아닌 법인이 보고한 분기는 **조용히 넘기지 않고** 출력한다(13F-NT 함정)
    alt_used = [(short, str(p), c) for (short, p), c in sorted(filer_cik.items(), key=lambda kv: str(kv[0]))
                if c != next(i["cik_plain"] for i in investors if i["short"] == short)]
    for short, p, c in alt_used:
        print(f"[sec] {short} {p}: 대체 법인 CIK {c}가 보유명세를 보고(주 CIK는 13F-NT)")
    if missing:
        print(f"[sec] 미보고(투자자×분기) {len(missing)}건: "
              + ", ".join(f"{s}/{p}" for s, p in missing[:12])
              + (" …" if len(missing) > 12 else ""))
    dead = [i["short"] for i in investors
            if not any((i["short"], p) in filer for p in periods)]
    if dead and not args.allow_missing_investors:
        raise RuntimeError(
            f"보유명세를 한 분기도 못 찾은 투자자 {len(dead)}명: {', '.join(dead)} — "
            "CIK가 틀렸거나(13F-NT만 내는 법인) 제출을 멈춘 것이다. investors.json의 "
            "alt_ciks를 확인하라. 의도한 상황이면 --allow-missing-investors로 통과시킨다.")
    if dead:
        print(f"[sec] 경고: 보유명세 0분기 투자자 {len(dead)}명 통과(--allow-missing-investors): "
              f"{', '.join(dead)}")

    # ── 1차 분류(분할 미보정) → 후보 CUSIP 추림 ──
    shares_map: dict[tuple, dict] = {}       # (short, cusip) -> {period: shares|None}
    names: dict[str, str] = {}
    for inv in investors:
        # 그 투자자가 창 전체에서 한 번이라도 들고 있던 CUSIP 전부를 먼저 모은다
        # (나중 분기에 처음 나온 종목도 이전 분기를 0/None으로 채워야 NEW 판정이 된다)
        all_cusips = set()
        for p in periods:
            ph = filer.get((inv["short"], p))
            if ph:
                all_cusips |= set(ph.holdings)
                for c, h in ph.holdings.items():
                    names.setdefault(c, h["name"])
        for c in all_cusips:
            d = {}
            for p in periods:
                ph = filer.get((inv["short"], p))
                # 미보고 분기는 0이 아니라 None — 가짜 NEW/EXIT 방지(secdata 13F-NT 주석)
                d[p] = None if ph is None else float(ph.holdings.get(c, {}).get("shares", 0.0))
            shares_map[(inv["short"], c)] = d

    def hist(key, cusip, split_factor=None):
        return screen.classify_history(shares_map.get((key, cusip), {}), periods,
                                       split_factor=split_factor)

    candidates = set()
    for (short, cusip), by_p in shares_map.items():
        for row in hist(short, cusip):
            if row["period"] == latest and row["klass"] in screen.CONSENSUS_CLASSES:
                candidates.add(cusip)
    print(f"[13f] 최근 분기({latest}) NEW/ADD 후보 CUSIP {len(candidates)}건 (분할 미보정 1차)")

    # ── 함정 8 — CUSIP→티커 + 증권 유형(ETF/ETN 제외, ADR·우선주 표시) ──
    mapped = figi.map_cusips(sorted(candidates))
    os.makedirs(OUT_DIR, exist_ok=True)
    unmapped_path = os.path.join(OUT_DIR, "unmapped_cusips.csv")
    unmapped = {c: m for c, m in mapped.items() if not m.get("ok") or not m.get("ticker")}
    with open(unmapped_path, "w", newline="", encoding="utf-8-sig") as f:
        f.write(CSV_HEADER_NOTE + "\n")
        w = csv.writer(f)
        w.writerow(["cusip", "issuer_name_13f", "error", "run_date"])
        for c in sorted(unmapped):
            w.writerow([c, names.get(c, ""), unmapped[c].get("error", ""), run_date])
    print(f"[figi] 매핑 실패 {len(unmapped)}건 → {os.path.relpath(unmapped_path, _HERE)}")

    kinds = {}
    for c, m in mapped.items():
        kinds[c] = m.get("kind") if m.get("ok") else None
    excluded_etf = [c for c in candidates if kinds.get(c) == "etf"]
    excluded_nonq = [c for c in candidates if kinds.get(c) == "non_equity"]
    tradable = {c: mapped[c]["ticker"] for c in candidates
                if mapped[c].get("ok") and mapped[c].get("ticker")
                and kinds.get(c) not in ("etf", "non_equity")}
    print(f"[figi] ETF/ETN 제외 {len(excluded_etf)}건 · 비주식(채권 등) 제외 "
          f"{len(excluded_nonq)}건 · 대상 티커 {len(set(tradable.values()))}개 "
          f"(ADR {sum(1 for c in tradable if kinds.get(c) == 'adr')}, "
          f"우선주 {sum(1 for c in tradable if kinds.get(c) == 'preferred')}, "
          f"기타유형 {sum(1 for c in tradable if kinds.get(c) == 'other')})")

    if args.no_prices:
        print("--no-prices — 여기서 중단(가격·cluster 계산 생략)")
        return None

    # ── 가격 ── (야후는 클래스 구분자가 달라 심볼을 변환해 받는다, px.yahoo_symbol)
    ysym = {t: px.yahoo_symbol(t) for t in set(tradable.values())}
    fetched = px.fetch_prices(sorted(set(ysym.values())))
    price_df = {t: fetched[y] for t, y in ysym.items() if y in fetched}
    no_price = sorted({f"{t}({ysym[t]})" for t in tradable.values() if t not in price_df})
    if no_price:
        print(f"[prices] 일봉 없음 {len(no_price)}개: {', '.join(no_price[:15])}"
              f"{' …' if len(no_price) > 15 else ''}")

    qstats: dict[tuple, dict] = {}            # (ticker, period) -> quarter_stats
    for t, df in price_df.items():
        for p in periods:
            st = px.quarter_stats(df, p)
            if st:
                qstats[(t, p)] = st
    ctx = {t: px.price_context(df) for t, df in price_df.items()}

    # ── 2차 분류(분할 보정) + 종목별 행 조립 ──
    rows = []
    dropped_by_split = []
    for cusip, ticker in sorted(tradable.items()):
        df = price_df.get(ticker)
        if df is None:
            continue

        def sf(period, _df=df):
            return px.split_factor_after(_df, period)

        classes, weight_map, per_inv_cost, ranks_min, wmax = {}, {}, {}, None, 0.0
        late_any = restated_any = conf_any = False
        for inv in investors:
            rows_h = hist(inv["short"], cusip, split_factor=sf)
            if not rows_h:
                continue
            by_p = {r["period"]: r["klass"] for r in rows_h}
            classes[inv["short"]] = by_p
            weight_map[inv["short"]] = {p: (weights.get((inv["short"], p), {}).get(cusip) or 0.0)
                                        for p in periods}
            incs = []
            for r in rows_h:
                if r["klass"] not in screen.CONSENSUS_CLASSES:
                    continue
                st = qstats.get((ticker, r["period"]))
                if not st:
                    continue
                incs.append({"period": r["period"],
                             "delta_shares": r["shares_adj"] - r["prev_shares_adj"],
                             "vwap_approx": st["vwap_approx"], "low": st["low"], "high": st["high"]})
            est = screen.estimate_investor_cost(incs)
            if est:
                per_inv_cost[inv["short"]] = est
            for p in periods:
                ph = filer.get((inv["short"], p))
                if ph and cusip in ph.holdings:
                    late_any = late_any or bool(ph.holdings[cusip].get("late_disclosed"))
                    restated_any = restated_any or ph.restated
                    conf_any = conf_any or ph.conf_denied_expired
                    r_ = ranks.get((inv["short"], p), {}).get(cusip)
                    if r_ and (ranks_min is None or r_ < ranks_min):
                        ranks_min = r_
                    wmax = max(wmax, weights.get((cik, p), {}).get(cusip) or 0.0)

        cons = screen.consensus_pct(classes, weight_map, latest)
        if cons["n_new_add_latest"] == 0:
            dropped_by_split.append(f"{ticker}({cusip})")
            continue                        # 분할 보정 후 NEW/ADD가 사라진 종목

        cl = screen.cluster_metrics(per_inv_cost)
        band_low = min([v["band_low"] for v in per_inv_cost.values() if v.get("band_low")] or [0]) or None
        band_high = max([v["band_high"] for v in per_inv_cost.values() if v.get("band_high")] or [0]) or None
        c = ctx.get(ticker) or {}
        last_close = c.get("last_close")
        latest_new_add = sorted([inv for inv, by_p in classes.items()
                                 if by_p.get(latest) in screen.CONSENSUS_CLASSES])
        win_new_add = sorted([inv for inv, by_p in classes.items()
                              if any(k in screen.CONSENSUS_CLASSES for k in by_p.values())])
        fdates = [filer[(i["short"], latest)].filing_date
                  for i in investors if (i["short"], latest) in filer
                  and filer[(i["short"], latest)].filing_date
                  and i["short"] in latest_new_add]
        rows.append({
            "disclaimer": screen.SIGNAL_DISCLAIMER,
            "ticker": ticker, "yahoo_symbol": px.yahoo_symbol(ticker),
            "cusip": cusip, "issuer_name": names.get(cusip, ""),
            "security_kind": kinds.get(cusip), "security_type_raw": mapped[cusip].get("security_type"),
            "n_new_add_latest": cons["n_new_add_latest"], "n_new_add_5q": cons["n_new_add_5q"],
            "consensus_latest_pct": cons["consensus_latest_pct"],
            "consensus_5q_pct": cons["consensus_5q_pct"],
            "investors_new_add_latest": _fmt_investors(latest_new_add),
            "investors_new_add_5q": _fmt_investors(win_new_add),
            "best_portfolio_rank": ranks_min, "max_portfolio_weight_pct": round(wmax, 4),
            "cluster_center_median": cl["cluster_center"],
            "cluster_dispersion": (cl["cluster_dispersion"] if cl["cluster_dispersion"] is not None else "N/A"),
            "cluster_dispersion_na_reason": cl["dispersion_na_reason"] or "",
            "cluster_n_investors": cl["n_investors_with_cost"],
            "cluster_n_distinct_quarters": cl["n_distinct_quarters"],
            "cluster_n_effective_quarters": cl["n_effective_quarters"],
            "est_band_low": band_low, "est_band_high": band_high,
            "last_close": last_close,
            "gap_vs_cluster_pct": screen.gap_pct(last_close, cl["cluster_center"]),
            "band_position": screen.band_position(last_close, band_low, band_high),
            "above_ma20": c.get("above_ma20"), "above_ma60": c.get("above_ma60"),
            "above_ma120": c.get("above_ma120"), "above_ma200": c.get("above_ma200"),
            "ma200_slope_pct_20d": c.get("ma200_slope_pct_20d"),
            "off_52w_high_pct": c.get("off_52w_high_pct"), "high_52w": c.get("high_52w"),
            "late_disclosed": late_any, "restated_any": restated_any,
            "conf_denied_expired_any": conf_any,
            "period_end_latest": str(latest),
            "filing_date_first": str(min(fdates)) if fdates else "",
            "filing_date_last": str(max(fdates)) if fdates else "",
            "price_last_bar_date": c.get("last_bar_date"),
            "list_fixed_date_max": max(i["list_fixed_date"] for i in investors),
            "run_date": run_date,
        })

    if dropped_by_split:
        print(f"[13f] 분할 보정 후 NEW/ADD 아님 → 제외 {len(dropped_by_split)}건: "
              f"{', '.join(dropped_by_split[:10])}{' …' if len(dropped_by_split) > 10 else ''}")

    rows.sort(key=lambda r: (-r["consensus_latest_pct"], -r["n_new_add_latest"], r["ticker"]))
    out_path = os.path.join(OUT_DIR, f"superinvestor_{date.today():%Y%m%d}.csv")
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        f.write(CSV_HEADER_NOTE + "\n")
        f.write(f"# 실행일 {run_date} · 13F 분기말 {latest} · 투자자 {len(investors)}명 · "
                f"분류 분기 {classify_periods[0]}~{latest}\n")
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"\nCSV: {os.path.relpath(out_path, _HERE)} ({len(rows)}행)")

    # ── 콘솔 요약 ──
    multi = [r for r in rows if r["n_new_add_latest"] >= 2]
    print(f"\n[{screen.SIGNAL_DISCLAIMER}] 요약 — 실행일 {run_date} / 13F 분기말 {latest} / "
          f"공개일 {min((r['filing_date_first'] for r in rows if r['filing_date_first']), default='?')}"
          f"~{max((r['filing_date_last'] for r in rows if r['filing_date_last']), default='?')}")
    print(f"NEW/ADD 2명 이상 종목: {len(multi)}건")
    for r in multi[:15]:
        print(f"  {r['ticker']:<6} {r['n_new_add_latest']}명 컨센서스 {r['consensus_latest_pct']:.2f}% "
              f"· 추정중심 {r['cluster_center_median']} · 현재가 {r['last_close']} "
              f"({r['gap_vs_cluster_pct']:+.1f}%, 구간 {r['band_position']}) · {r['investors_new_add_latest']}")
    ranked = [r for r in rows if r["cluster_dispersion"] != "N/A"]
    ranked.sort(key=lambda r: r["cluster_dispersion"])
    print(f"\ncluster 밀집도 상위 10(낮을수록 밀집, N/A {len(rows) - len(ranked)}건 제외):")
    for r in ranked[:10]:
        print(f"  {r['ticker']:<6} 밀집도 {r['cluster_dispersion']:.3f} "
              f"({r['cluster_n_investors']}명/지배분기 {r['cluster_n_effective_quarters']}"
              f"·합집합 {r['cluster_n_distinct_quarters']}) "
              f"· 중심 {r['cluster_center_median']} · 현재가 {r['last_close']} "
              f"({r['gap_vs_cluster_pct']:+.1f}%)")
    na = [r for r in rows if r["cluster_dispersion"] == "N/A"]
    if na:
        from collections import Counter
        print("N/A 사유:", dict(Counter(r["cluster_dispersion_na_reason"] for r in na)))
    return rows


if __name__ == "__main__":
    main()

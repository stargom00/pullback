"""검증 — Dataroma 공개 NEW/ADD와 우리 분류를 대조하고 **일치율을 보고**한다.

    python3 tools/superinvestor/validate_dataroma.py [--investors BRK,psc,tp] [--refresh]

Dataroma는 같은 13F를 사람이 집계해 공개하는 사이트다. 우리 파이프라인(SEC 벌크 →
함정 처리 → 분류)이 **다른 경로로 같은 답에 도달하는지** 보는 대조군이라, 일치율이
낮으면 우리 쪽 함정 처리나 Dataroma 쪽 집계 관례(아래) 중 하나가 원인이다.

[알려진 관례 차이 — 불일치가 곧 버그는 아니다]
· Dataroma의 "Recent Activity"는 직전 분기 대비 변화다: Buy=신규, Add x%=증가,
  Reduce x%=감소, Sell=청산, 빈칸=유지. 우리 NEW/ADD/REDUCE/EXIT/HOLD와 1:1로 맞춘다.
· Dataroma는 **1주 차이도 Add로** 센다. 우리는 `screen.CLASSIFY_EPS_PCT`(0.1%) 미만
  변화를 HOLD로 본다(분할 보정으로 주식 수가 float이 되는 문제 때문) — 그래서
  "우리 HOLD vs Dataroma Add" 불일치가 구조적으로 조금 생긴다. 이 스크립트는 그
  경우를 `eps_boundary`로 따로 세어 보고한다(숨기지 않는다).
· Dataroma는 ETF·옵션을 자체 기준으로 걸러내고 일부 종목을 합치기도 한다.
· 우리는 미보고(13F-NT) 분기를 건너뛰지만 Dataroma는 표시가 다를 수 있다.

**이 대조는 "관심 신호 · 측정 전" 검증이다** — 수익률 검증이 아니라 분류 정확도 확인.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
from datetime import date

import screen
import secdata

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "cache", "dataroma")
BASE_URL = "https://www.dataroma.com/m/holdings.php?m={code}"
# Dataroma는 봇 UA를 막는다 — 브라우저 UA로 요청한다(개인 검증용, 요청은 투자자당 1회).
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ACTIVITY_TO_CLASS = {"BUY": "NEW", "ADD": "ADD", "REDUCE": "REDUCE", "SELL": "EXIT"}


def fetch_page(code: str, refresh: bool = False) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{code}_{date.today():%Y%m%d}.html")
    if refresh or not os.path.exists(path):
        import requests
        r = requests.get(BASE_URL.format(code=code), headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        with open(path, "w", encoding="utf-8") as f:
            f.write(r.text)
    return open(path, encoding="utf-8", errors="replace").read()


def _cells(row_html: str) -> list[str]:
    out = []
    for c in re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row_html):
        out.append(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip())
    return out


def parse_holdings(page: str) -> dict[str, dict]:
    """{ticker: {klass, shares, weight_pct, activity_raw}}."""
    m = re.search(r"<table[^>]*id=[\"']grid[\"'][\s\S]*?</table>", page)
    if not m:
        raise RuntimeError("Dataroma grid 테이블을 못 찾음 — 페이지 개편 의심")
    out = {}
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", m.group(0)):
        c = _cells(row)
        if len(c) < 7 or c[1].upper().startswith("STOCK"):
            continue
        ticker = c[1].split("-")[0].strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", ticker):
            continue
        act = c[3].strip()
        word = act.split()[0].upper() if act else ""
        klass = ACTIVITY_TO_CLASS.get(word, "HOLD" if not act else None)
        shares = float(re.sub(r"[^\d.]", "", c[4]) or 0)
        try:
            weight = float(c[2])
        except ValueError:
            weight = None
        out[ticker] = {"klass": klass, "shares": shares, "weight_pct": weight,
                       "activity_raw": act}
    if not out:
        raise RuntimeError("Dataroma 보유 행 0건 — 파서/페이지 불일치")
    return out


def our_classes(code: str, periods: list[date], holdings: dict, investors: list[dict],
                cusip_to_ticker: dict) -> dict[str, dict]:
    """우리 파이프라인의 (그 투자자, 최근 분기) 분류 {ticker: {klass, shares}}.
    분할 보정은 하지 않는다 — Dataroma도 **그 분기에 보고된 주식 수 그대로**를 쓰므로
    같은 기준으로 비교해야 한다(보정을 넣으면 분할 종목만 인위적으로 어긋난다)."""
    inv = next((i for i in investors if i.get("dataroma_code") == code), None)
    if inv is None:
        raise RuntimeError(f"investors.json에 dataroma_code={code} 없음")
    latest = periods[-1]
    # 분기별 보고 법인 선택은 run.py와 **같은 함수**를 쓴다(사본 금지) — 13F-NT만 내는
    # 법인을 주 CIK로 두면 여기서도 0건이 된다(2026-09-28 Pershing 사례).
    per_q = {p: secdata.pick_filer(inv, p, holdings)[1] for p in periods}
    cusips = {c for ph in per_q.values() if ph for c in ph.holdings}
    out = {}
    for c in cusips:
        by_p = {}
        for p in periods:
            ph = per_q[p]
            by_p[p] = None if ph is None else float(ph.holdings.get(c, {}).get("shares", 0.0))
        rows = screen.classify_history(by_p, periods)
        row = next((r for r in rows if r["period"] == latest), None)
        if row is None:
            continue
        t = cusip_to_ticker.get(c)
        if not t:
            continue
        cur = out.setdefault(t, {"klass": row["klass"], "shares": row["shares_adj"]})
        if cur["shares"] < row["shares_adj"]:       # 같은 티커 여러 CUSIP(클래스) — 큰 쪽
            out[t] = {"klass": row["klass"], "shares": row["shares_adj"]}
    return out


def compare(ours: dict, theirs: dict, eps_pct: float = screen.CLASSIFY_EPS_PCT) -> dict:
    common = sorted(set(ours) & set(theirs))
    agree, disagree, eps_boundary = [], [], []
    for t in common:
        o, d = ours[t]["klass"], theirs[t]["klass"]
        if d is None:
            continue
        if o == d:
            agree.append(t)
            continue
        # 우리 HOLD vs 저쪽 ADD/REDUCE인데 변화폭이 eps 미만이면 관례 차이로 분리
        if o == "HOLD" and d in ("ADD", "REDUCE"):
            pct = re.search(r"([\d.]+)\s*%", theirs[t]["activity_raw"] or "")
            if pct and float(pct.group(1)) < eps_pct:
                eps_boundary.append((t, d, theirs[t]["activity_raw"]))
                continue
        disagree.append((t, o, d, theirs[t]["activity_raw"]))
    n = len(agree) + len(disagree) + len(eps_boundary)
    return {"n_common": n, "agree": agree, "disagree": disagree, "eps_boundary": eps_boundary,
            "match_rate": (len(agree) / n) if n else None,
            "only_ours": sorted(set(ours) - set(theirs)),
            "only_theirs": sorted(set(theirs) - set(ours))}


def run(codes: list[str], refresh: bool = False, quarters: int = 5,
        windows: int = 6, asof: date | None = None) -> dict:
    import figi
    investors = secdata.load_investors()
    periods = secdata.quarter_ends(quarters + 1, asof or date.today())
    ciks = {c for i in investors if i.get("dataroma_code") in codes for c in i["ciks_plain"]}
    if not ciks:
        raise RuntimeError(f"대상 투자자 없음: {codes}")
    holdings, _ = secdata.load_holdings(ciks, periods, windows=windows)
    all_cusips = {c for ph in holdings.values() if ph.reported for c in ph.holdings}
    mapped = figi.map_cusips(sorted(all_cusips))
    c2t = {c: m["ticker"] for c, m in mapped.items() if m.get("ok") and m.get("ticker")}
    print(f"=== Dataroma 대조 ({screen.SIGNAL_DISCLAIMER}) — 분기말 {periods[-1]} ===")
    print(f"CUSIP→티커 매핑 {len(c2t)}/{len(all_cusips)}")
    results = {}
    for code in codes:
        theirs = parse_holdings(fetch_page(code, refresh))
        ours = our_classes(code, periods, holdings, investors, c2t)
        res = compare(ours, theirs)
        results[code] = res
        rate = f"{res['match_rate'] * 100:.1f}%" if res["match_rate"] is not None else "N/A"
        print(f"\n[{code}] 공통 {res['n_common']}종목 · 분류 일치율 {rate} "
              f"(일치 {len(res['agree'])} / 불일치 {len(res['disagree'])} / "
              f"eps관례차 {len(res['eps_boundary'])})")
        print(f"  우리만 {len(res['only_ours'])}종목 · Dataroma만 {len(res['only_theirs'])}종목")
        for t, o, d, raw in res["disagree"][:10]:
            print(f"   불일치 {t}: 우리={o} / Dataroma={d}({raw})")
    rates = [r["match_rate"] for r in results.values() if r["match_rate"] is not None]
    if rates:
        print(f"\n투자자 {len(rates)}명 평균 분류 일치율 {sum(rates) / len(rates) * 100:.1f}% "
              f"— {screen.SIGNAL_DISCLAIMER}")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Dataroma 분류 대조(검증)")
    ap.add_argument("--investors", default="BRK,psc,tp", help="Dataroma 코드 콤마 구분(기본 3명)")
    ap.add_argument("--refresh", action="store_true", help="캐시 무시하고 다시 받기")
    ap.add_argument("--quarters", type=int, default=5)
    ap.add_argument("--windows", type=int, default=6)
    args = ap.parse_args(argv)
    res = run([c.strip() for c in args.investors.split(",") if c.strip()], refresh=args.refresh,
              quarters=args.quarters, windows=args.windows)
    return 0 if res else 1


if __name__ == "__main__":
    sys.exit(main())

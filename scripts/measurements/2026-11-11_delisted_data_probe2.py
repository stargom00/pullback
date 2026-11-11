"""데이터 확인만(측정 아님) 2차 — KIND 상장폐지현황 목록 + 표본 시세(naver → FDR 새 심볼).

1차(2026-11-11_delisted_data_probe.py): FDR StockListing('KRX-DELISTING') 0행(조용한 실패) · 예비 2종목 중 096610만 naver·FDR 시세 있음.
실행(시간 무관): python3 scripts/measurements/2026-11-11_delisted_data_probe2.py
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import delisted_source as ds  # noqa: E402

OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def main():
    log = {}
    lst = ds.kind_delisted("2015-01-01", "2026-12-31", log)
    out = {"kind_rows": len(lst), **log}
    if len(lst):
        common = lst[(lst["code"].str[-1] == "0") & ~lst["name"].str.contains("스팩|기업인수목적|리츠|ETF|ETN")]
        out["common_rows"] = len(common)
        out["by_year"] = dict(Counter(common["delisted"].dt.year.astype(str)))
        out["by_market"] = dict(Counter(common["market"]))
        out["reasons_top"] = Counter(common["reason"]).most_common(12)
        recent = common[common["delisted"] >= "2021-01-01"]
        older = common[common["delisted"] < "2021-01-01"]
        sample = list(recent.sample(min(14, len(recent)), random_state=1).itertuples()) + \
                 list(older.sample(min(6, len(older)), random_state=1).itertuples())
        rows = []
        for r in sample:
            df, src = ds.fetch_prices(r.code, 4000, log)
            rows.append({"code": r.code, "name": r.name, "delisted": str(r.delisted.date()), "source": src,
                         "rows": 0 if df is None else len(df),
                         "first": None if df is None else str(df.index[0].date()),
                         "last": None if df is None else str(df.index[-1].date())})
            print(rows[-1], flush=True)
        out["sample"] = rows
        out["price_ok_recent"] = sum(1 for x in rows if x["rows"] and x["delisted"] >= "2021")
        out["price_ok_older"] = sum(1 for x in rows if x["rows"] and x["delisted"] < "2021")
        out["source_count"] = dict(Counter(x["source"] for x in rows))
    out.update({k: v for k, v in log.items() if k not in out})
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if k != "sample"}, ensure_ascii=False, indent=1, default=str))
    print(f"[done] {OUT}")


if __name__ == "__main__":
    main()

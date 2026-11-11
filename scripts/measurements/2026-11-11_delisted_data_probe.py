"""데이터 확인만(측정 아님) — 상장폐지 종목 목록과 과거 시세를 받을 수 있는가.

목적: docs/lowpoint_hold_100.md(+100%까지 1~3년 보유 측정)의 생존편향 한계를 줄이려면 상장폐지 종목을 유니버스에 넣어야 한다.
     그 전에 소스가 실제로 되는지 확인한다. 결과를 보고 측정 스크립트의 데이터 경로를 정한다.
소스 후보:
  ① FinanceDataReader: fdr.StockListing('KRX-DELISTING') 목록 · fdr.DataReader(code, exchange='KRX-DELISTING') 시세
     (https://github.com/FinanceData/FinanceDataReader/wiki/Quick-Start — data.krx.co.kr 경유)
  ② naver siseJson(운영 소스) — 폐지 종목 코드로 일봉이 나오는지
시장 시간 무관(시세 측정이 아니라 소스 확인). 맥에서 실행 — 클라우드는 naver·krx 접속 차단.
실행: pip install finance-datareader  (없으면)
      python3 scripts/measurements/2026-11-11_delisted_data_probe.py
결과: 같은 폴더 .results.json (커밋하지 않음)
"""
import json
import os
import random
import sys
import time
import traceback
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))
SINCE = "2015-01-01"
SAMPLE = 12
SEED = 20261111
EXCLUDE_NAME = ("스팩", "ETN", "ETF", "리츠", "인프라", "선박", "기업인수목적")


def main():
    out = {"since": SINCE}
    try:
        import FinanceDataReader as fdr
        out["fdr_version"] = getattr(fdr, "__version__", "?")
    except Exception as e:
        out["fdr_import_error"] = repr(e)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
        return

    # ① 목록
    try:
        t0 = time.time()
        lst = fdr.StockListing("KRX-DELISTING")
        out["list_rows_total"] = len(lst)
        out["list_columns"] = list(lst.columns)
        out["list_elapsed_s"] = round(time.time() - t0, 1)
        if len(lst) < 1000:     # 실패해도 빈 표를 돌려주는 경우가 있다 — 조용한 0건을 성공으로 읽지 않는다
            out["list_warning"] = f"행 수 {len(lst)} — 목록 다운로드 실패 의심"
        cols = {c.lower(): c for c in lst.columns}
        sym = cols.get("symbol") or cols.get("code") or list(lst.columns)[0]
        nam = cols.get("name") or list(lst.columns)[1]
        dd = cols.get("delistingdate") or next((c for c in lst.columns if "date" in c.lower() or "폐지" in c), None)
        mk = cols.get("market")
        rs = cols.get("reason")
        import pandas as pd
        lst = lst.copy()
        lst["_d"] = pd.to_datetime(lst[dd], errors="coerce") if dd else pd.NaT
        f = lst[lst["_d"] >= SINCE]
        if mk:
            out["markets"] = dict(Counter(f[mk].astype(str)))
            f = f[f[mk].astype(str).str.upper().isin(["KOSPI", "KOSDAQ", "유가", "코스닥", "유가증권"])]
        f = f[~f[nam].astype(str).str.contains("|".join(EXCLUDE_NAME))]
        f = f[f[sym].astype(str).str[-1] == "0"]     # 보통주(끝자리 0) — 우선주(5·7 등) 제외
        out["since_kospi_kosdaq_common"] = len(f)
        out["by_year"] = dict(Counter(f["_d"].dt.year.astype(int).astype(str)))
        if rs:
            out["reasons_top"] = Counter(f[rs].astype(str)).most_common(15)
        codes = list(f[sym].astype(str).str.zfill(6))
        names = dict(zip(codes, f[nam].astype(str)))
        dates = dict(zip(codes, f["_d"].dt.strftime("%Y-%m-%d")))
    except Exception:
        out["list_error"] = traceback.format_exc()[-1500:]
        codes, names, dates = [], {}, {}

    # ② 시세 표본
    rng = random.Random(SEED)
    sample = rng.sample(codes, min(SAMPLE, len(codes))) if codes else ["036360", "096610"]
    rows = []
    try:
        import naver_kr
    except Exception as e:
        naver_kr = None
        out["naver_import_error"] = repr(e)
    for c in sample:
        r = {"code": c, "name": names.get(c), "delisted": dates.get(c)}
        try:
            df = fdr.DataReader(c, "2010-01-01", exchange="KRX-DELISTING")
            r["fdr_rows"] = len(df)
            if len(df):
                r["fdr_first"] = str(df.index[0].date()); r["fdr_last"] = str(df.index[-1].date())
                r["fdr_last_close"] = float(df["Close"].iloc[-1])
                r["fdr_columns"] = list(df.columns)[:12]
        except Exception as e:
            r["fdr_error"] = repr(e)[:300]
        if naver_kr:
            try:
                nd = naver_kr._fetch_sise_history(c, 5000)
                r["naver_rows"] = 0 if nd is None else len(nd)
                if nd is not None and len(nd):
                    r["naver_first"] = str(nd.index[0].date()); r["naver_last"] = str(nd.index[-1].date())
            except Exception as e:
                r["naver_error"] = repr(e)[:300]
        rows.append(r)
        print(r, flush=True)
        time.sleep(0.3)
    out["sample"] = rows
    out["fdr_price_ok"] = sum(1 for r in rows if r.get("fdr_rows"))
    out["naver_price_ok"] = sum(1 for r in rows if r.get("naver_rows"))
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if k != "sample"}, ensure_ascii=False, indent=1, default=str))
    print(f"[done] {OUT}")


if __name__ == "__main__":
    main()

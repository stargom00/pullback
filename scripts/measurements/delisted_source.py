"""상장폐지 종목 목록·시세 소스(측정 전용, 2026-11-11).

목록: KIND 상장폐지현황(kind.krx.co.kr/investwarn/delcompany.do) — 운영 저점 유니버스가 쓰는 KIND와 같은 사이트·같은 POST 꼴
      (adminissue: method=search…Sub · currentPageSize · forward=…_down). 정확한 파라미터는 공개 문서가 없어 변형 몇 개를 차례로 시도하고,
      어떤 변형이 됐는지 결과에 남긴다. FinanceDataReader StockListing('KRX-DELISTING')은 2026-11-11 맥에서 0행(조용한 실패)이라 마지막 대안.
시세: naver siseJson(운영 소스, 수정주가) → 안 나오면 FinanceDataReader('KRX-DELISTING:코드').
"""
import io
import os
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))

KIND_DEL_URL = "https://kind.krx.co.kr/investwarn/delcompany.do"
HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/124.0", "Referer": "https://kind.krx.co.kr/investwarn/delcompany.do?method=searchDelCompanyMain"}
# marketType: 1 = 유가증권, 2 = 코스닥 (KIND adminissue와 같은 값 — lowpoint.KR_BOARDS)
MARKETS = {"1": "KOSPI", "2": "KOSDAQ"}


def _variants(mt, frm, to):
    common = {"marketType": mt, "currentPageSize": "3000", "pageIndex": "1", "fromDate": frm, "toDate": to,
              "orderMode": "1", "orderStat": "D", "searchMode": "", "searchCodeType": "", "searchCorpName": "", "repIsuSrtCd": ""}
    yield "search_sub_down", {**common, "method": "searchDelCompanySub", "forward": "delcompany_down"}
    yield "download", {**common, "method": "download", "forward": "delcompany_down"}
    yield "search_sub", {**common, "method": "searchDelCompanySub", "forward": "delcompany_sub"}


def _parse(text):
    for t in pd.read_html(io.StringIO(text), converters={"종목코드": str}):
        cols = [str(c) for c in t.columns]
        if any("폐지" in c for c in cols) and any("코드" in c for c in cols):
            return t
    return None


def kind_delisted(frm: str, to: str, log: dict) -> pd.DataFrame:
    """→ DataFrame[code, name, delisted(Timestamp), reason, market]. 실패하면 빈 표(호출부가 하한으로 하드 실패시킨다)."""
    import requests
    frames = []
    for mt, mname in MARKETS.items():
        got = None
        for vname, data in _variants(mt, frm, to):
            try:
                r = requests.post(KIND_DEL_URL, data=data, headers=HEADERS, timeout=60)
                r.encoding = "euc-kr"
                t = _parse(r.text) if r.ok else None
                log.setdefault("kind_attempts", []).append({"market": mname, "variant": vname, "status": r.status_code,
                                                            "bytes": len(r.content), "rows": 0 if t is None else len(t)})
                if t is not None and len(t) > 1:
                    got = t
                    log.setdefault("kind_variant", {})[mname] = vname
                    break
            except Exception as e:
                log.setdefault("kind_attempts", []).append({"market": mname, "variant": vname, "error": repr(e)[:200]})
        if got is None:
            continue
        cols = {str(c): c for c in got.columns}
        code_c = next(cols[c] for c in cols if "코드" in c)
        name_c = next((cols[c] for c in cols if "회사명" in c or "종목명" in c), None)
        date_c = next(cols[c] for c in cols if "폐지일" in c)
        reason_c = next((cols[c] for c in cols if "사유" in c), None)
        frames.append(pd.DataFrame({"code": got[code_c].astype(str).str.strip().str.zfill(6),
                                    "name": got[name_c].astype(str) if name_c is not None else "",
                                    "delisted": pd.to_datetime(got[date_c], errors="coerce"),
                                    "reason": got[reason_c].astype(str) if reason_c is not None else "", "market": mname}))
    if not frames:
        return pd.DataFrame(columns=["code", "name", "delisted", "reason", "market"])
    return pd.concat(frames, ignore_index=True).dropna(subset=["delisted"])


def fetch_prices(code: str, days: int, log: dict):
    """naver → FDR. → (df[Open..Volume] | None, source)."""
    try:
        import naver_kr
        df = naver_kr._fetch_sise_history(code, days)
        if df is not None and len(df):
            return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]), "naver"
    except Exception as e:
        log.setdefault("naver_errors", []).append(f"{code}: {repr(e)[:120]}")
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(f"KRX-DELISTING:{code}")
        if df is not None and len(df):
            return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]), "fdr"
    except Exception as e:
        log.setdefault("fdr_errors", []).append(f"{code}: {repr(e)[:120]}")
    return None, None

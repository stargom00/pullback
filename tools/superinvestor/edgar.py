"""EDGAR 개별 제출(13F infotable XML) 읽기 — **검증용 독립 경로**.

스크리너 본체는 SEC Form 13F **Data Sets(벌크 TSV)** 를 쓴다. 이 모듈은 같은 제출을
EDGAR의 **원본 XML**에서 따로 읽어, 두 경로가 같은 답을 주는지 대조하는 데 쓴다
(test_superinvestor.py). 즉 "공개값과 대조"의 공개값 쪽이다 — 벌크 파서가 조용히
틀리면 여기서 어긋난다.

전부 캐시(cache/edgar/)하고, SEC 요청 예절(User-Agent 이메일, 초당 제한)은
secdata의 것을 그대로 재사용한다(사본 금지).
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date

import secdata

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "cache", "edgar")

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"
ARCHIVE_DIR_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/index.json"
ARCHIVE_FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{name}"


def _cached(name: str, fetch) -> bytes:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    if not os.path.exists(path):
        data = fetch()
        with open(path, "wb") as f:
            f.write(data)
    return open(path, "rb").read()


def list_13f_filings(cik: str, include_older: bool = False) -> list[dict]:
    """[{form, accession, filing_date, period}] — 최신순. 13F-HR/13F-HR/A만.
    include_older=True면 submissions JSON의 과거 파일(files[])도 같이 읽는다
    (2022년 제출분처럼 recent에 없는 것)."""
    cik_p = cik.lstrip("0")
    raw = _cached(f"submissions_{cik_p}.json",
                  lambda: secdata._http_get(SUBMISSIONS_URL.format(cik=cik_p), timeout=60))
    doc = json.loads(raw)
    blocks = [doc["filings"]["recent"]]
    if include_older:
        for f in doc["filings"].get("files", []):
            older = _cached(f["name"],
                            lambda n=f["name"]: secdata._http_get(
                                f"https://data.sec.gov/submissions/{n}", timeout=60))
            blocks.append(json.loads(older))
    out = []
    for b in blocks:
        for i, form in enumerate(b["form"]):
            if not form.startswith("13F-HR"):
                continue
            out.append({"form": form, "accession": b["accessionNumber"][i],
                        "filing_date": date.fromisoformat(b["filingDate"][i]),
                        "period": date.fromisoformat(b["reportDate"][i])})
    out.sort(key=lambda r: r["filing_date"], reverse=True)
    return out


def infotable_rows(cik: str, accession: str) -> list[dict]:
    """그 제출의 information table XML을 파싱해 행 목록으로.
    반환: [{cusip, name, title, value_raw, shares, type, putcall}] — **정규화 전 원본값**
    (value_raw는 천 달러/달러 구분 전. 단위 규칙 검증에 쓰려고 그대로 둔다)."""
    cik_p = cik.lstrip("0")
    acc_nodash = accession.replace("-", "")
    idx = json.loads(_cached(f"dir_{acc_nodash}.json",
                             lambda: secdata._http_get(
                                 ARCHIVE_DIR_URL.format(cik=cik_p, acc_nodash=acc_nodash),
                                 timeout=60)))
    names = [i["name"] for i in idx["directory"]["item"]]
    xmls = [n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()]
    if not xmls:
        raise RuntimeError(f"{accession}: information table XML을 못 찾음 ({names})")
    raw = _cached(f"infotable_{acc_nodash}.xml",
                  lambda: secdata._http_get(
                      ARCHIVE_FILE_URL.format(cik=cik_p, acc_nodash=acc_nodash, name=xmls[0]),
                      timeout=60))
    text = raw.decode("utf-8", errors="replace")
    root = ET.fromstring(re.sub(r'\sxmlns="[^"]+"', "", text, count=1))
    rows = []
    for it in root.findall(".//infoTable"):
        def g(path, default=""):
            el = it.find(path)
            return (el.text or "").strip() if el is not None and el.text else default
        rows.append({
            "cusip": g("cusip").upper(), "name": g("nameOfIssuer"), "title": g("titleOfClass"),
            "value_raw": float(g("value", "0") or 0),
            "shares": float(g("shrsOrPrnAmt/sshPrnamt", "0") or 0),
            "type": g("shrsOrPrnAmt/sshPrnamtType").upper(), "putcall": g("putCall").upper(),
        })
    if not rows:
        raise RuntimeError(f"{accession}: infoTable 0건 — 파서/스키마 불일치")
    return rows

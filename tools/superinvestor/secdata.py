"""SEC Form 13F Data Sets(분기 벌크) 로더 + 13F 함정 처리.

이 모듈은 **13F 원천 데이터만** 다룬다(가격·티커·스코어는 다른 모듈).
Phase 1 전용 — 결과는 전부 "관심 신호 · 측정 전"이다.

[데이터 소스] https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
데이터셋은 **보고 분기가 아니라 "제출 접수 구간"(예: 01jun2026-31aug2026)** 단위로
묶여 있다. 그래서 어떤 분기의 보유를 보려면 그 분기 제출이 들어간 구간을 받아야 하고,
정정(13F-HR/A)은 원본보다 **나중 구간**에 들어올 수 있다 — 이 모듈은 구간을 여러 개
받아 전부 `PERIODOFREPORT` 기준으로 재색인한다(구간 경계를 신뢰하지 않는다).
파일 경로 접두사는 SEC가 최근 구간부터 바꿨다(`structureddata` → `datastandardsinnovation`)
— 그래서 URL을 하드코딩하지 않고 목록 페이지를 파싱한다.

[13F 함정 — 이 모듈이 처리하는 것] 번호는 사용자 지시서의 번호와 같다.
1. `PUTCALL`이 있는 행(PUT/CALL 옵션) 제외 → `keep_holding_row()`
2. `SSHPRNAMTTYPE != "SH"`(PRN=원금) 제외 → `keep_holding_row()`
3. `VALUE` 단위: 2023-01-03 **이전 제출분은 천 달러**, 이후는 달러 → `normalize_value()`
   근거(추측 아님): 데이터셋 동봉 readme(FORM13F_readme.htm) 원문 —
   "VALUE ... Starting on January 3, 2023, market value is reported rounded to the
   nearest dollar. Previously, market value was reported in thousands."
4. `13F-HR/A`: RESTATEMENT(전체 재작성) vs NEW HOLDINGS(추가분) 구분 → `apply_amendments()`
5. 같은 CUSIP 여러 행 합산 → `aggregate_by_cusip()`
7. 컨피덴셜은 **사전 탐지 불가**(제출 자체가 비공개). 뒤늦게 13F-HR/A(NEW HOLDINGS)로
   올라온 보유분만 `late_disclosed=True`로 표시한다 → `apply_amendments()`
   (`CONFDENIEDEXPIRED`/`DATEDENIEDEXPIRED`는 "기밀 요청이 거부·만료됐다"는 표시라
   같이 담아두지만, 이것도 사후 정보다.)
6(분할 보정)·8(증권 유형)은 티커·가격이 필요해 `prices.py`/`figi.py`에 있다.

[또 하나의 함정 — 13F-NT] "다른 운용사가 대신 보고함" 통지서다. 보유 행이 0건인데
이걸 "전부 매도"로 읽으면 가짜 EXIT가 쏟아진다. 이 모듈은 그 분기를 **0건이 아니라
`None`(미보고)** 으로 남기고(`PeriodHoldings.reported=False`), 분류는 미보고 분기를
건너뛴다(`screen.classify_history()`).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "cache")
SEC_CACHE_DIR = os.path.join(CACHE_DIR, "sec")

# SEC는 User-Agent에 연락 가능한 이메일을 요구한다(차단 사유 1순위).
SEC_UA = "pullback-superinvestor/0.1 (pamabear@gmail.com)"
DATASET_INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
# SEC 요청 제한: 초당 10회. 여유를 둬 8회/초로 제한한다(측정 근거 없는 여유값 — 다운로드가
# 몇 건뿐이라 더 조여도 비용이 없다).
_MIN_INTERVAL_SEC = 1 / 8
_last_request_at = 0.0

# 함정 3 — 이 날짜 **이후 제출분**의 VALUE는 달러, 이전은 천 달러(위 docstring의 readme 인용).
VALUE_IN_DOLLARS_FROM = date(2023, 1, 3)
THOUSANDS = 1000

# 함정 4 — AMENDMENTTYPE 값. SEC readme: "Amendment type is a restatement or adds new
# holdings entries."
AMEND_RESTATEMENT = "RESTATEMENT"
AMEND_NEW_HOLDINGS = "NEW HOLDINGS"

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def parse_sec_date(s: str) -> date | None:
    """SEC 데이터셋 날짜('30-JUN-2026') → date. 빈 값/이상값은 None."""
    s = (s or "").strip().upper()
    m = re.fullmatch(r"(\d{1,2})-([A-Z]{3})-(\d{4})", s)
    if not m:
        return None
    day, mon, year = int(m.group(1)), _MONTHS.get(m.group(2)), int(m.group(3))
    if not mon:
        return None
    return date(year, mon, day)


def _throttle() -> None:
    global _last_request_at
    wait = _MIN_INTERVAL_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _http_get(url: str, timeout: int = 300) -> bytes:
    """requests로 받는다 — 이 맥의 python.org 런타임은 urllib에서
    CERTIFICATE_VERIFY_FAILED가 난다(로컬 CA 번들 없음). requests는 certifi를
    들고 있어 그 문제가 없고, 이 레포가 이미 쓰는 의존성이다."""
    import requests
    _throttle()
    r = requests.get(url, headers={"User-Agent": SEC_UA}, timeout=timeout)
    r.raise_for_status()
    return r.content


# ── 데이터셋 목록·다운로드(전부 캐시) ────────────────────────────────

def list_datasets(refresh: bool = False) -> list[tuple[str, str]]:
    """[(label, url)] — 최근 구간부터. label은 zip 파일명에서 딴 구간 이름
    (예: '01jun2026-31aug2026', 구형은 '2023q4')."""
    os.makedirs(SEC_CACHE_DIR, exist_ok=True)
    idx_path = os.path.join(SEC_CACHE_DIR, "dataset_index.html")
    if refresh or not os.path.exists(idx_path):
        with open(idx_path, "wb") as f:
            f.write(_http_get(DATASET_INDEX_URL, timeout=60))
    html = open(idx_path, encoding="utf-8", errors="replace").read()
    out, seen = [], set()
    for href in re.findall(r'href="([^"]*form-13f-data-sets/[^"]*_form13f\.zip)"', html):
        url = href if href.startswith("http") else "https://www.sec.gov" + href
        label = os.path.basename(href).replace("_form13f.zip", "")
        if label in seen:
            continue
        seen.add(label)
        out.append((label, url))
    if not out:
        raise RuntimeError(f"{DATASET_INDEX_URL} 에서 zip 링크를 못 찾음 — 페이지 개편 의심")
    return out


def dataset_path(label: str, url: str) -> str:
    """zip을 캐시에 두고 경로 반환(있으면 재다운로드 안 함)."""
    os.makedirs(SEC_CACHE_DIR, exist_ok=True)
    path = os.path.join(SEC_CACHE_DIR, f"{label}_form13f.zip")
    if not os.path.exists(path) or os.path.getsize(path) < 1_000_000:
        print(f"[sec] 다운로드 {label} …", flush=True)
        data = _http_get(url)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        print(f"[sec] 저장 {label} ({len(data) / 1e6:.0f}MB)", flush=True)
    return path


# ── 함정 1·2·3·5 ────────────────────────────────────────────────────

def keep_holding_row(row: dict) -> bool:
    """롱 보유(주식)만 남긴다 — 함정 1(PUT/CALL 제외) + 함정 2(PRN 제외)."""
    if (row.get("PUTCALL") or "").strip():
        return False
    return (row.get("SSHPRNAMTTYPE") or "").strip().upper() == "SH"


def normalize_value(value: float | str, filing_date: date) -> float:
    """함정 3 — 2023-01-03 이전 제출분 VALUE는 천 달러 단위. 달러로 정규화."""
    v = float(value or 0)
    return v if filing_date >= VALUE_IN_DOLLARS_FROM else v * THOUSANDS


def aggregate_by_cusip(rows: list[dict]) -> dict[str, dict]:
    """함정 5 — 같은 CUSIP 여러 행(클래스·매니저 분리 등)을 합산.
    반환: {cusip: {shares, value_usd, name, late_disclosed}} (late는 OR 합성)."""
    out: dict[str, dict] = {}
    for r in rows:
        cusip = (r["cusip"] or "").strip().upper()
        if not cusip:
            continue
        cur = out.setdefault(cusip, {"cusip": cusip, "shares": 0.0, "value_usd": 0.0,
                                     "name": r.get("name") or "", "late_disclosed": False})
        cur["shares"] += float(r["shares"] or 0)
        cur["value_usd"] += float(r["value_usd"] or 0)
        cur["late_disclosed"] = cur["late_disclosed"] or bool(r.get("late_disclosed"))
        if not cur["name"]:
            cur["name"] = r.get("name") or ""
    return out


# ── 함정 4·7 — 정정 제출 적용 ────────────────────────────────────────

@dataclass
class Filing:
    accession: str
    cik: str
    period: date
    filing_date: date
    submission_type: str
    is_amendment: bool
    amendment_no: int
    amendment_type: str
    conf_denied_expired: bool
    rows: list = field(default_factory=list)   # infotable 행(정규화 후)


@dataclass
class PeriodHoldings:
    cik: str
    period: date
    reported: bool                  # 13F-HR(보유 명세)을 실제로 받았는가 — 13F-NT만 있으면 False
    holdings: dict                  # {cusip: {...}}
    total_value_usd: float
    filing_date: date | None        # 최초 원본(13F-HR) 제출일 = 공개일
    last_filing_date: date | None   # 정정 포함 마지막 제출일
    restated: bool
    late_disclosed_cusips: set
    conf_denied_expired: bool
    accessions: list


def apply_amendments(filings: list[Filing]) -> PeriodHoldings | None:
    """한 (투자자, 분기)의 제출들을 시간순으로 합성한다 — 함정 4·7.

    - 13F-HR(원본): 기준 행. 같은 분기에 여럿이면 **마지막 것**을 기준으로 한다.
    - 13F-HR/A + RESTATEMENT: 지금까지의 행을 **전부 버리고** 정정 행으로 교체.
    - 13F-HR/A + NEW HOLDINGS: 정정 행을 **추가**하고 그 행들에 `late_disclosed=True`.
      (컨피덴셜이었다가 뒤늦게 공개된 보유분이 전형적으로 이 형태로 들어온다.)
    - AMENDMENTTYPE이 비어 있으면 **RESTATEMENT로 간주**하고 경고를 남긴다
      (추가분으로 오해하면 옛 보유가 그대로 살아남아 주식 수가 부풀려진다 —
      보수적인 쪽은 교체다. 조용히 넘기지 않고 로그로 남긴다.)
    - 13F-NT(통지)만 있으면 `reported=False` — 0건이 아니라 "미보고"다.
    """
    if not filings:
        return None
    ordered = sorted(filings, key=lambda f: (f.filing_date or date.min, f.amendment_no, f.accession))
    base = [f for f in ordered if not f.is_amendment and f.submission_type.upper().startswith("13F-HR")]
    amends = [f for f in ordered if f.is_amendment and f.submission_type.upper().startswith("13F-HR")]
    cik, period = ordered[0].cik, ordered[0].period
    rows: list[dict] = []
    late: set = set()
    restated = False
    if base:
        rows = [dict(r) for r in base[-1].rows]
    for a in amends:
        atype = (a.amendment_type or "").strip().upper()
        if atype == AMEND_NEW_HOLDINGS:
            for r in a.rows:
                r = dict(r)
                r["late_disclosed"] = True
                late.add((r["cusip"] or "").strip().upper())
                rows.append(r)
        else:
            if atype != AMEND_RESTATEMENT:
                print(f"[sec] 경고: {a.accession} (cik={a.cik} {a.period}) AMENDMENTTYPE="
                      f"{a.amendment_type!r} — RESTATEMENT로 간주", flush=True)
            rows = [dict(r) for r in a.rows]
            late = set()          # 전체 재작성이면 '뒤늦게 추가된 행'이라는 구분이 사라진다
            restated = True
    reported = bool(base) or bool(amends)
    holdings = aggregate_by_cusip(rows)
    for cusip in late:
        if cusip in holdings:
            holdings[cusip]["late_disclosed"] = True
    return PeriodHoldings(
        cik=cik, period=period, reported=reported, holdings=holdings,
        total_value_usd=sum(h["value_usd"] for h in holdings.values()),
        filing_date=(base[-1].filing_date if base else (amends[0].filing_date if amends else None)),
        last_filing_date=ordered[-1].filing_date,
        restated=restated, late_disclosed_cusips=late,
        conf_denied_expired=any(f.conf_denied_expired for f in ordered),
        accessions=[f.accession for f in ordered],
    )


# ── 데이터셋 읽기 ────────────────────────────────────────────────────

def _member(zf: zipfile.ZipFile, basename: str) -> str:
    """zip 안의 실제 멤버 경로. 구간에 따라 파일이 **하위 디렉터리에 들어 있다**
    (예: '01JUN2025-31AUG2025_form13f/SUBMISSION.tsv') — 루트 고정이면 KeyError로
    죽는다(2026-09-28 실측). basename으로 찾는다."""
    for n in zf.namelist():
        if os.path.basename(n).upper() == basename.upper():
            return n
    raise RuntimeError(f"{os.path.basename(zf.filename)}: {basename} 없음 "
                       f"(멤버: {zf.namelist()[:5]})")


def _read_tsv(zf: zipfile.ZipFile, name: str):
    with zf.open(_member(zf, name)) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"),
                                  delimiter="\t")


def load_window(zip_path: str, ciks: set[str], periods: set[date] | None = None) -> list[Filing]:
    """한 구간 zip에서 대상 CIK(+선택적으로 대상 분기)의 제출만 뽑아 Filing 목록으로."""
    ciks = {c.lstrip("0") for c in ciks}
    filings: dict[str, Filing] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for r in _read_tsv(zf, "SUBMISSION.tsv"):
            cik = (r["CIK"] or "").lstrip("0")
            if cik not in ciks:
                continue
            period = parse_sec_date(r["PERIODOFREPORT"])
            if period is None or (periods is not None and period not in periods):
                continue
            filings[r["ACCESSION_NUMBER"]] = Filing(
                accession=r["ACCESSION_NUMBER"], cik=cik, period=period,
                filing_date=parse_sec_date(r["FILING_DATE"]) or date.min,
                submission_type=(r["SUBMISSIONTYPE"] or "").strip(),
                is_amendment=False, amendment_no=0, amendment_type="",
                conf_denied_expired=False)
        if not filings:
            return []
        for r in _read_tsv(zf, "COVERPAGE.tsv"):
            f = filings.get(r["ACCESSION_NUMBER"])
            if not f:
                continue
            f.is_amendment = (r.get("ISAMENDMENT") or "").strip().upper() in ("Y", "1", "TRUE")
            try:
                f.amendment_no = int(float(r.get("AMENDMENTNO") or 0))
            except ValueError:
                f.amendment_no = 0
            f.amendment_type = (r.get("AMENDMENTTYPE") or "").strip()
            f.conf_denied_expired = (r.get("CONFDENIEDEXPIRED") or "").strip().upper() in ("Y", "1", "TRUE")
            if f.submission_type.upper().endswith("/A"):
                f.is_amendment = True          # 유형이 /A면 커버페이지 체크박스보다 유형을 믿는다
        for r in _read_tsv(zf, "INFOTABLE.tsv"):
            f = filings.get(r["ACCESSION_NUMBER"])
            if not f:
                continue
            if not keep_holding_row(r):        # 함정 1·2
                continue
            f.rows.append({
                "cusip": (r["CUSIP"] or "").strip().upper(),
                "name": (r["NAMEOFISSUER"] or "").strip(),
                "title": (r["TITLEOFCLASS"] or "").strip(),
                "shares": float(r["SSHPRNAMT"] or 0),
                "value_usd": normalize_value(r["VALUE"], f.filing_date),   # 함정 3
                "late_disclosed": False,
            })
    return list(filings.values())


def quarter_ends(n: int, asof: date) -> list[date]:
    """asof 기준, 이미 **제출 기한(분기말+45일)이 지난** 분기말 n개(오래된 것부터).
    13F는 분기말 후 45일 안에 제출한다 — 그 기한 전 분기는 아직 공개분이 없다."""
    y, m = asof.year, ((asof.month - 1) // 3) * 3 + 3
    ends = []
    for _ in range(n + 2):
        nxt_first = date(y + (1 if m == 12 else 0), (m % 12) + 1, 1)
        ends.append(nxt_first - timedelta(days=1))
        m -= 3
        if m <= 0:
            y, m = y - 1, m + 12
    ends = [e for e in ends if asof >= e + timedelta(days=45)]
    return sorted(ends)[-n:]


def load_holdings(ciks: set[str], periods: list[date], windows: int = 6,
                  refresh_index: bool = False) -> tuple[dict, list[str]]:
    """대상 분기들의 (cik, period) → PeriodHoldings. 구간 zip을 최근 것부터
    `windows`개 받아 **PERIODOFREPORT 기준으로 재색인**한다(정정이 나중 구간에
    들어오는 것을 놓치지 않으려고 — 위 docstring)."""
    datasets = list_datasets(refresh=refresh_index)[:windows]
    by_key: dict[tuple, list[Filing]] = {}
    used = []
    for label, url in datasets:
        path = dataset_path(label, url)
        got = load_window(path, ciks, set(periods))
        used.append(f"{label}({len(got)}건)")
        for f in got:
            by_key.setdefault((f.cik, f.period), []).append(f)
    out = {}
    for key, filings in by_key.items():
        ph = apply_amendments(filings)
        if ph is not None:
            out[key] = ph
    return out, used


def pick_filer(investor: dict, period: date, holdings: dict):
    """그 분기에 **실제로 보유명세(13F-HR)를 보고한 법인**의 (cik, PeriodHoldings).
    없으면 (None, None).

    13F는 통지(13F-NT)와 보유명세를 다른 법인이 낼 수 있고, 보고 주체가 분기 중에
    바뀌기도 한다(investors.json `_alt_ciks_note`의 Icahn·ValueAct·Pershing 실측 사례).
    여러 법인이 같은 분기에 HR을 내면 **합치지 않고 총액이 큰 쪽 하나만** 쓴다 —
    두 법인의 보유가 겹칠 수 있어 합산하면 중복 계상이 된다(보수적 선택).
    """
    best = (None, None)
    for cik in investor["ciks_plain"]:
        ph = holdings.get((cik, period))
        if ph is None or not ph.reported or ph.total_value_usd <= 0:
            continue
        if best[1] is None or ph.total_value_usd > best[1].total_value_usd:
            best = (cik, ph)
    return best


def load_investors(path: str | None = None) -> list[dict]:
    path = path or os.path.join(_HERE, "investors.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    investors = cfg["investors"]
    seen = set()
    for inv in investors:
        cik = inv["cik"].lstrip("0")
        if cik in seen:
            raise RuntimeError(f"investors.json CIK 중복: {inv['cik']}")
        seen.add(cik)
        if not inv.get("list_fixed_date"):
            raise RuntimeError(f"investors.json {inv['name']}: list_fixed_date 없음 "
                               "(Phase 3 사후선택 편향 방지에 필수)")
        inv["cik_plain"] = cik
        # 같은 투자자의 다른 보고 법인(위 pick_filer 주석) — 주 CIK 먼저
        inv["ciks_plain"] = [cik] + [c.lstrip("0") for c in (inv.get("alt_ciks") or [])]
    return investors


if __name__ == "__main__":   # 수동 점검용
    print(f"투자자 {len(load_investors())}명")
    print("대상 분기:", [str(d) for d in quarter_ends(5, date.today())])

"""CUSIP → 티커 매핑(OpenFIGI) + 증권 유형 판정(함정 8).

[소스] OpenFIGI v3 mapping API(무료, API 키 없이 사용 중).
- 키 없이: 요청당 job 10개, 분당 25요청 — 그래서 요청 간 2.5초 간격(`_MIN_INTERVAL_SEC`).
  이 수치는 OpenFIGI 공식 rate-limit 문서값이고, 키를 넣으면 `OPENFIGI_API_KEY`
  환경변수로 자동 상향된다(요청당 100 job, 6초당 25요청).
- **매핑 실패 CUSIP은 버리지 않는다** — 호출부가 output/unmapped_cusips.csv로 뽑는다
  (사용자 지시. 조용한 누락 금지 원칙과 같다).
- 결과는 파일 캐시(cache/figi_cusip.json) — 같은 CUSIP을 두 번 묻지 않는다.

[함정 8 — 증권 유형] ETF/ETN은 제외하고, ADR·우선주는 **제외하지 않고 표시**한다.
판정은 OpenFIGI `securityType`/`securityType2` 문자열 키워드로 한다(아래 상수).
키워드 목록은 실제 응답값을 보고 만든 것이라 새 유형이 나오면 `security_kind()`가
"other"로 떨어지고, 호출부가 그걸 그대로 CSV에 남긴다(조용히 섞이지 않게).
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_HERE, "cache", "figi_cusip.json")

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
# **CUSIP 하나로는 부족하다** — 13F에는 외국 법인 종목이 CINS(예: AON PLC=G0403H108,
# CHUBB=H1467J104, DEUTSCHE BANK=D18190898)로 들어온다. OpenFIGI는 CINS를 별도
# idType으로 받아서, ID_CUSIP만 물으면 이 종목들이 전부 no_match로 떨어진다
# (2026-09-28 첫 실행에서 실패 38건 중 대부분이 이 경우였다 — Linde·Chubb·Aptiv 등
# 미국 상장 대형주). 실패하면 다음 idType으로 재시도한다.
ID_TYPES = ("ID_CUSIP", "ID_CINS")
# 캐시 스키마 버전 — pick_us_listing/security_kind 판정이 바뀌면 올린다(옛 항목은
# 자동으로 다시 조회된다). 안 올리면 낡은 판정이 캐시에 남아 조용히 계속 쓰인다.
CACHE_SCHEMA = 2
_MIN_INTERVAL_SEC = 2.5          # 키 없음 = 분당 25요청
_JOBS_PER_REQUEST = 10           # 키 없음 = 요청당 10개
_MIN_INTERVAL_SEC_KEYED = 0.25
_JOBS_PER_REQUEST_KEYED = 100
_last_request_at = 0.0

# 함정 8 — 유형 키워드(OpenFIGI securityType/securityType2 실제 값 기준)
ETF_KEYWORDS = ("ETP", "ETF", "ETN", "OPEN-END FUND", "CLOSED-END FUND", "MUTUAL FUND",
                "UNIT INVESTMENT TRUST", "FUND OF FUNDS")
ADR_KEYWORDS = ("ADR", "DEPOSITARY RECEIPT", "GDR", "NVDR")
PREFERRED_KEYWORDS = ("PREFERENCE", "PREFERRED", "PFD")
COMMON_KEYWORDS = ("COMMON STOCK", "REIT", "ORDINARY", "SHS", "LTD PARTNERSHIP",
                   "MLP", "ROYALTY TRUST", "TRACKING STOCK", "SAVINGS SHARE")


def security_kind(security_type: str, security_type2: str = "", market_sector: str = "") -> str:
    """'etf' | 'adr' | 'preferred' | 'common' | 'non_equity' | 'other'.
    우선순위: 비주식(marketSector≠Equity) → ETF/ETN → 우선주 → ADR → 보통주 → other.
    (우선주 ADR도 있어서 둘이 겹치면 우선주로 본다 — 자본구조상 더 중요한 구분.)

    `non_equity`는 2026-09-28 첫 실행에서 발견한 실제 사례 때문에 생겼다 — OpenFIGI가
    어떤 CUSIP에 대해 회사채 티커('GOOGL 6.25 05/15/29 A')를 돌려줘 그게 후보에 섞였다.
    13F 함정 2(PRN 제외)로 대부분 걸러지지만 SH로 보고된 비주식이 남는다. 이 스크리너는
    주식 스크리너이므로 호출부가 `non_equity`를 제외하고 **개수를 출력**한다."""
    if market_sector and market_sector.strip().upper() != "EQUITY":
        return "non_equity"
    hay = f"{security_type or ''} | {security_type2 or ''}".upper()
    if any(k in hay for k in ETF_KEYWORDS):
        return "etf"
    if any(k in hay for k in PREFERRED_KEYWORDS):
        return "preferred"
    if any(k in hay for k in ADR_KEYWORDS):
        return "adr"
    if any(k in hay for k in COMMON_KEYWORDS):
        return "common"
    return "other"


def pick_us_listing(data: list[dict]) -> dict | None:
    """OpenFIGI가 같은 CUSIP에 여러 항목(거래소·증권 종류별)을 준다 — 미국 상장 **주식**을
    고른다. 우선순위: Equity+exchCode US → Equity 아무거나 → exchCode US → 첫 항목.
    Equity를 먼저 보는 이유: 같은 CUSIP에 채권 항목이 섞여 오면 그게 선택돼 티커가
    'GOOGL 6.25 05/15/29 A'처럼 나온다(2026-09-28 실측)."""
    if not data:
        return None
    def is_eq(d):
        return (d.get("marketSector") or "").upper() == "EQUITY"
    def is_us(d):
        return (d.get("exchCode") or "").upper() == "US"
    for pred in (lambda d: is_eq(d) and is_us(d), is_eq, is_us):
        for d in data:
            if pred(d):
                return d
    return data[0]


def _throttle(interval: float) -> None:
    global _last_request_at
    wait = interval - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (ValueError, OSError) as e:
            raise RuntimeError(f"{CACHE_PATH} 읽기 실패({e}) — 조용히 덮어쓰지 않는다")
    return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, CACHE_PATH)


def _post(jobs: list[dict], api_key: str | None) -> list[dict]:
    """requests 사용 이유는 secdata._http_get() 주석과 같다(urllib CA 문제)."""
    import requests
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    for attempt in range(4):
        r = requests.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=60)
        if r.status_code == 429 and attempt < 3:      # 제한 초과 — 지수 백오프
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("OpenFIGI 재시도 실패(429 반복)")


def map_cusips(cusips, verbose: bool = True) -> dict[str, dict]:
    """{cusip: {ticker, name, security_type, security_type2, kind, ok, error, tried}}.
    실패도 dict에 남긴다(ok=False) — 호출부가 unmapped CSV로 뽑는다(버리지 않는다).
    idType을 ID_CUSIP → ID_CINS 순으로 시도한다(위 ID_TYPES 주석)."""
    api_key = os.environ.get("OPENFIGI_API_KEY")
    interval = _MIN_INTERVAL_SEC_KEYED if api_key else _MIN_INTERVAL_SEC
    per_req = _JOBS_PER_REQUEST_KEYED if api_key else _JOBS_PER_REQUEST
    cache = _load_cache()
    want = sorted({c.strip().upper() for c in cusips if c and c.strip()})

    # 스키마가 낡은 성공 항목은 **통째로 버리고** 처음부터 다시 조회한다.
    # (2026-09-28 버그: "낡았으면 다시 물어본다"를 성공 항목 갱신으로 구현했더니,
    #  ID_CINS로 성공했던 항목을 ID_CUSIP으로 재조회해 no_match로 **덮어썼고**
    #  tried에 ID_CINS가 남아 재시도까지 막혀 Aon·Chubb·ICLR·STX가 조용히 사라졌다.
    #  항목을 지우면 ID_CUSIP→ID_CINS 폴백 체인이 다시 처음부터 돈다.)
    # 실패 항목도 같이 버린다 — 실패에도 판정 로직(폴백 체인)이 반영돼 있어서
    # 성공만 갱신하면 낡은 실패가 영구히 남는다(위 버그의 잔재가 실제로 그랬다).
    stale = [c for c in want if c in cache and cache[c].get("schema") != CACHE_SCHEMA]
    if stale:
        if verbose:
            print(f"[figi] 캐시 스키마 갱신 — {len(stale)}건 재조회", flush=True)
        for c in stale:
            cache.pop(c, None)

    for id_type in ID_TYPES:
        todo = [c for c in want
                if c not in cache
                or (not cache[c].get("ok") and id_type not in (cache[c].get("tried") or []))]
        if not todo:
            continue
        if verbose:
            print(f"[figi] {id_type} 조회 {len(todo)}건 (캐시 {len(cache)}건, "
                  f"요청 {(len(todo) + per_req - 1) // per_req}회, 간격 {interval}s)", flush=True)
        for i in range(0, len(todo), per_req):
            batch = todo[i:i + per_req]
            _throttle(interval)
            try:
                resp = _post([{"idType": id_type, "idValue": c} for c in batch], api_key)
            except Exception as e:      # 네트워크/HTTP 실패 — 배치 전체를 실패로 기록(조용히 넘기지 않음)
                for c in batch:
                    prev = cache.get(c) or {}
                    cache[c] = {"ok": False, "error": f"request_failed({id_type}): {e}",
                                "tried": sorted(set((prev.get("tried") or []) + [id_type])),
                                "schema": CACHE_SCHEMA}
                _save_cache(cache)
                continue
            for c, r in zip(batch, resp):
                prev = cache.get(c) or {}
                tried = sorted(set((prev.get("tried") or []) + [id_type]))
                pick = pick_us_listing(r.get("data") or [])
                if pick is None:
                    cache[c] = {"ok": False, "error": (r.get("error") or "no_match"),
                                "tried": tried, "schema": CACHE_SCHEMA}
                    continue
                st, st2 = pick.get("securityType") or "", pick.get("securityType2") or ""
                sector = pick.get("marketSector") or ""
                cache[c] = {"ok": True, "ticker": (pick.get("ticker") or "").strip().upper(),
                            "name": pick.get("name") or "", "security_type": st,
                            "security_type2": st2, "market_sector": sector,
                            "kind": security_kind(st, st2, sector),
                            "exch_code": pick.get("exchCode") or "",
                            "composite_figi": pick.get("compositeFIGI") or "",
                            "id_type": id_type, "tried": tried, "schema": CACHE_SCHEMA}
            _save_cache(cache)
            if verbose and ((i // per_req) % 20 == 0):
                print(f"[figi]   {min(i + per_req, len(todo))}/{len(todo)}", flush=True)
    return {c: cache.get(c, {"ok": False, "error": "not_requested"}) for c in want}

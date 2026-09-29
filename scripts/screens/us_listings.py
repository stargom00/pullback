"""저점종목 스크린 **전용** 미국 상장 보통주 목록 (로컬 전용, 배포 안 됨).

왜 별도인가: `universe.get_universe("us")`(스캐너 공용, 2120개)는 `us_universe_auto.py`
스냅샷의 **시총 $500M+ · 거래량 30만주+** 필터를 통과한 종목만 담는다. 저점 스크린은
키움 조건검색과 대조해야 하고 키움은 소형주까지 잡는다 — 2026-09-28 조사에서 ZUMZ
(시총 ≈$217~269M)가 그 시총 필터에서 탈락해 미검출된 것이 확인됐다(조건 계산은 정상,
직접 투입 시 hit). 스캐너 유니버스는 메모리·스캔시간 때문에 늘리면 안 되므로
**이 스크린만 쓰는 유니버스를 여기서 따로 만든다.** universe.py / app.py / scanner.py /
KR 경로는 건드리지 않는다.

[소스] Nasdaq Trader 공식 심볼 디렉터리(매일 갱신, 무료·무인증)
  https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt   (Nasdaq 상장)
  https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt    (NYSE·AMEX 등)
`us_universe_auto.py` 주석의 출처(rreichel3/US-Stock-Symbols)도 접근되지만 **그 쪽은
증권 유형 플래그가 없다**(symbol·name·marketCap 등만) — 즉 ETF/우선주/워런트 제외를
이름 추측으로만 해야 한다. Nasdaq Trader 파일은 `ETF`·`Test Issue` 플래그를 직접 주므로
제외 근거가 데이터에 있다. 그래서 이쪽을 택했다(2026-09-29 확인).

[제외 규칙] **시총·거래량 필터 없음**(사용자 지시 — 임계값을 임의로 정하지 않는다).
  · `ETF = Y`          → ETF/ETN 류
  · `Test Issue = Y`   → 거래소 테스트 심볼
  · 이름 패턴          → 우선주·워런트·유닛·라이트·채권성 증권(아래 `_NAME_EXCLUDE`)
  · 심볼에 `.`/`-` 외 특수문자(`+ = ~ ^ $ *` 등) → 워런트·유닛·라이트용 접미 표기
ADR(American Depositary Shares)은 **남긴다** — 보통주에 대한 예탁증서다(제외 목록에 없음).
제외는 사유별 개수를 항상 반환해 호출부가 출력한다(조용한 누락 금지).

[캐시] `scripts/screens/cache/us_listings.json` — 한 번 받으면 재사용하고,
`--refresh-universe`(lowpoint.py CLI)로만 다시 받는다.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "cache")
CACHE_PATH = os.path.join(CACHE_DIR, "us_listings.json")

SOURCES = {
    "nasdaq": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "other": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
# 파일 끝 "File Creation Time: ..." 줄 — 데이터 시점 기록용(조용히 낡는 것 방지)
_CREATION_RE = re.compile(r"File Creation Time:\s*([^|]+)")

# 이름 패턴 제외 — 대소문자 무시. 각 패턴이 몇 건을 걸렀는지 통계로 보고된다.
_NAME_EXCLUDE = (
    ("preferred", r"\bpreferred\b"),
    ("pfd", r"\bpfd\b"),
    ("warrant", r"\bwarrants?\b"),
    ("unit", r"\bunits?\b"),
    ("right", r"\brights?\b"),
    ("note", r"\bnotes?\b"),
    ("debenture", r"\bdebentures?\b"),
    ("bond", r"\bbonds?\b"),
    ("etn", r"\bexchange[- ]traded notes?\b|\betns?\b"),
    ("fund", r"\bfund\b|\bportfolio\b|\bindex trust\b|\bunit investment trust\b"),
    ("liquidating", r"\bliquidating trust\b"),
    ("subordinated", r"\bsubordinated\b"),
    ("tracking", r"\bsubscription\b"),
)
_NAME_EXCLUDE = tuple((tag, re.compile(rx, re.I)) for tag, rx in _NAME_EXCLUDE)

# 심볼에 허용되는 문자 — 알파벳 + '.'(클래스 구분) + '-'(클래스 구분). 그 외 기호가
# 붙은 심볼은 워런트/유닛/라이트/우선주 표기다(예: 'AACQ+', 'XYZ=', 'ABRpD').
_SYMBOL_OK = re.compile(r"^[A-Z]{1,6}([.\-][A-Z]{1,4})?$")


def yahoo_symbol(symbol: str) -> str:
    """Nasdaq Trader 심볼 → yfinance 심볼. 클래스 구분자가 '.'/'-'로 오는데
    야후는 '-'를 쓴다(예: 'BRK.B' → 'BRK-B')."""
    return symbol.strip().upper().replace(".", "-")


def _fetch(url: str) -> str:
    import requests
    r = requests.get(url, headers={"User-Agent": "pullback-lowpoint/0.1 (pamabear@gmail.com)"},
                     timeout=60)
    r.raise_for_status()
    return r.text


def _parse(text: str, kind: str) -> tuple[list[dict], str | None]:
    """파이프 구분 파일 → [{symbol, name, etf, test_issue, exchange}], 파일생성시각."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError(f"{kind}: 빈 응답 — 소스 개편/차단 의심")
    header = [h.strip() for h in lines[0].split("|")]
    creation = None
    rows = []
    for line in lines[1:]:
        m = _CREATION_RE.search(line)
        if m:
            creation = m.group(1).strip()
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != len(header):
            continue
        rec = dict(zip(header, parts))
        sym = (rec.get("Symbol") or rec.get("ACT Symbol") or "").strip().upper()
        if not sym:
            continue
        rows.append({
            "symbol": sym,
            "name": rec.get("Security Name") or "",
            "etf": (rec.get("ETF") or "").upper(),
            "test_issue": (rec.get("Test Issue") or "").upper(),
            "exchange": rec.get("Exchange") or ("NASDAQ" if kind == "nasdaq" else ""),
            "source": kind,
        })
    if not rows:
        raise RuntimeError(f"{kind}: 파싱 결과 0건 — 형식 변경 의심(헤더: {header})")
    return rows, creation


def refresh_cache(path: str = CACHE_PATH) -> dict:
    """두 소스를 받아 캐시 파일로 저장하고 그 내용을 반환."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "sources": SOURCES, "file_creation_time": {}, "rows": []}
    for kind, url in SOURCES.items():
        rows, creation = _parse(_fetch(url), kind)
        payload["file_creation_time"][kind] = creation
        payload["rows"].extend(rows)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)
    return payload


def load_cache(path: str = CACHE_PATH, refresh: bool = False) -> dict:
    if refresh or not os.path.exists(path):
        return refresh_cache(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data.get("rows"):
        raise RuntimeError(f"{path}: 캐시가 비었거나 형식이 다르다 — --refresh-universe로 다시 받을 것")
    return data


def exclusion_reason(row: dict) -> str | None:
    """제외 사유 문자열 또는 None(보통주로 통과). 시총·거래량은 보지 않는다."""
    if row.get("etf") == "Y":
        return "etf_flag"
    if row.get("test_issue") == "Y":
        return "test_issue"
    name = row.get("name") or ""
    for tag, rx in _NAME_EXCLUDE:
        if rx.search(name):
            return f"name:{tag}"
    if not _SYMBOL_OK.match(row.get("symbol") or ""):
        return "symbol_suffix"
    return None


def build_universe(refresh: bool = False, path: str = CACHE_PATH,
                   apply_exclusions: bool = True) -> tuple[dict, dict]:
    """({yahoo_symbol: name}, stats).

    apply_exclusions=False는 **테스트(사보타주)용**이다 — 그 경우 ETF가 그대로 들어온다.
    stats: {total, kept, excluded_by_reason, file_creation_time, fetched_at, dupes}
    """
    data = load_cache(path, refresh=refresh)
    uni, reasons, dupes = {}, {}, 0
    for row in data["rows"]:
        if apply_exclusions:
            why = exclusion_reason(row)
            if why:
                reasons[why] = reasons.get(why, 0) + 1
                continue
        y = yahoo_symbol(row["symbol"])
        if y in uni:
            dupes += 1
            continue
        uni[y] = row["name"]
    stats = {"total": len(data["rows"]), "kept": len(uni), "excluded_by_reason": reasons,
             "dupes": dupes, "file_creation_time": data.get("file_creation_time"),
             "fetched_at": data.get("fetched_at"), "cache_path": path}
    return uni, stats


if __name__ == "__main__":   # 수동 점검
    import sys
    uni, st = build_universe(refresh="--refresh" in sys.argv)
    print(f"보통주 {st['kept']} / 원본 {st['total']} (중복 {st['dupes']})")
    print("제외 사유:", json.dumps(st["excluded_by_reason"], ensure_ascii=False))
    print("파일 생성시각:", st["file_creation_time"], "| 캐시 시각:", st["fetched_at"])
    for t in ("ZUMZ", "AAPL", "SPY", "BRK-B"):
        print(f"  {t}: {'포함' if t in uni else '없음'}")

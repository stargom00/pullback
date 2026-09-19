#!/usr/bin/env python3
"""프로덕션 메모리 1회 측정 → 누적 JSONL 기록 (v5.271, 사용자 지시).

배경: RSS를 여러 번 쟀지만 **매번 조건이 달랐다.** 09-17의 1,464.6MB는 EOD를
겪은 프로세스, 09-19~20의 437~606MB는 갓 기동한 프로세스였다(peak−current가
7.9MB뿐이고 최대 할당이 `pickle.load`인 게 근거였다). 그래서 "가동 시작 시각을
아는 프로세스"에서 **정해진 두 시점**에 재기로 했다:

    1. 2026-09-21(월) KST 20:15 — KR EOD 1회 통과
    2. 2026-09-22(화) KST 07:00 — US EOD까지 통과

기록 항목은 매번 같다: RSS · tracemalloc current/peak · _data_cache.
**peak − current**가 작으면 그 프로세스는 무거운 fetch 사이클을 안 겪었다는
뜻이라(=최근 재시작 의심) 해석에 꼭 필요하다. 재시작 여부 자체는 사용자가
Railway Activity로 확인한다.

토큰은 ~/pullback/.env에서 읽고 **출력하지 않는다.**
사용: python3 scripts/maintenance/memory_probe.py [라벨]
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
URL = "https://pullback2-production.up.railway.app/api/debug/memory"
OUT = Path(__file__).resolve().parent / "memory_probe.jsonl"
BASELINE_0917 = 1464.6          # 09-17 EOD 직후 실측 — 대조 기준


def _token() -> str:
    env = Path.home() / "pullback" / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_READ_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("API_READ_TOKEN을 .env에서 못 찾음")


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "manual"
    try:
        raw = subprocess.run(
            ["curl", "-s", "--max-time", "60", "-H", f"X-Api-Read-Token: {_token()}", URL],
            capture_output=True, text=True, timeout=90).stdout
        d = json.loads(raw)
    except Exception as e:
        rec = {"at": datetime.now(KST).isoformat(), "label": label, "error": str(e)}
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[memory-probe] 실패: {e}")
        return 1

    if not d.get("enabled"):
        print("[memory-probe] MEMORY_DIAG=0 — tracemalloc 값이 없다(RSS만 유효)")
    tm = d.get("tracemalloc") or {}
    dc = (d.get("caches") or {}).get("_data_cache") or {}
    rec = {
        "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "label": label,
        "rss_mb": d.get("rss_mb"),
        "tm_current_mb": tm.get("current_mb"),
        "tm_peak_mb": tm.get("peak_mb"),
        "tm_peak_minus_current": (round(tm["peak_mb"] - tm["current_mb"], 1)
                                  if tm.get("peak_mb") is not None else None),
        "data_cache_mb": dc.get("mb"), "data_cache_len": dc.get("len"),
        "top_alloc": (tm.get("top") or [{}])[0].get("where"),
        "vs_0917_pct": (round(d["rss_mb"] / BASELINE_0917 * 100, 1)
                        if d.get("rss_mb") else None),
    }
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[memory-probe] {rec['label']} {rec['at']}")
    print(f"  RSS {rec['rss_mb']}MB  (09-17 {BASELINE_0917}MB 대비 {rec['vs_0917_pct']}%)")
    print(f"  tracemalloc cur {rec['tm_current_mb']} / peak {rec['tm_peak_mb']}"
          f"  (차 {rec['tm_peak_minus_current']} — 작으면 무거운 fetch 미경험 = 재시작 의심)")
    print(f"  _data_cache {rec['data_cache_mb']}MB / {rec['data_cache_len']}개"
          f"  | 최대할당 {rec['top_alloc']}")
    print(f"  → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

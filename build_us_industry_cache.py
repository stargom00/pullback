"""US 업종 캐시 최초/수동 빌드 진입점 (v5.195, 사용자 지시 — 섹터 층 1단계 [2]).

수동 실행(백그라운드 nohup):
    nohup python3 build_us_industry_cache.py > us_industry_cache_build.log 2>&1 &

app.py의 _ensure_us_industry_cache_fresh()도 캐시가 30일 넘게 지났으면
정확히 이 스크립트를 서브프로세스로 띄운다(월 1회 자동 갱신) — 수동 실행과
자동 실행이 같은 진입점을 쓴다.
"""
import sys
import time

import us_industry_cache as cache
from universe import get_universe


def main():
    if not cache.acquire_lock():
        print("[us_industry_cache] 이미 진행 중이거나 최근 락이 있음 — 종료")
        sys.exit(1)
    try:
        tickers = list(get_universe("us").keys())
        print(f"[us_industry_cache] 빌드 시작 — 대상 {len(tickers)}종목")
        t0 = time.time()
        result = cache.build_cache(tickers, throttle=0.5)
        elapsed = time.time() - t0
        coverage = (len(result) / len(tickers) * 100) if tickers else 0.0
        print(
            f"[us_industry_cache] 완료 — {len(result)}/{len(tickers)}종목 "
            f"커버리지 {coverage:.1f}%, {elapsed / 60:.1f}분 소요 → {cache.cache_path()}"
        )
    finally:
        cache.release_lock()


if __name__ == "__main__":
    main()

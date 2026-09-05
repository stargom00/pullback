"""미국 유니버스 업종(yfinance info['industry']) 캐시 (v5.195, 사용자 지시
— 섹터 층 1단계 [2]).

us_sectors_auto.py(rreichel3 정적 데이터셋)는 미국 금융을 전부 "금융" 한
버킷으로 뭉쳐놔서 은행/보험/증권 세분류가 불가능하다. yfinance의
info['industry'] 필드는 GICS 서브인더스트리급 분류를 주고, 실측 확인 결과
한국 종목에도 똑같이 먹혀서(001450.KS 현대해상 → "Insurance - Property &
Casualty", kr_sectors_auto의 "손해보험"과 사실상 일치) 미국 쪽 세분류
해법으로 채택.

벌크 API가 없어 종목당 1회 .info 호출이 필요 — 유니버스 전체(2000+종목)를
app.py 프로세스 안에서 동기로 돌리면 워커를 오래(수십 분) 묶으므로,
빌드 자체는 build_us_industry_cache.py로 분리해 nohup 백그라운드 프로세스로
돌린다. 이 모듈은 그 스크립트와 app.py가 공유하는 경로/락/빌드 로직만
갖고 있고, yfinance는 build_cache() 안에서만 지연 import한다(app.py가 이
모듈을 가볍게 import만 해도 되게).
"""
from __future__ import annotations

import json
import os
import time

_CACHE_FILENAME = "us_industry_cache.json"
_LOCK_FILENAME = "us_industry_cache.lock"
STALE_DAYS = 30           # 월 1회 갱신
LOCK_MAX_AGE_SEC = 3 * 3600   # 이보다 오래된 락은 죽은 프로세스로 간주(재시도 허용)


def _dir() -> str:
    return os.environ.get("JOURNAL_DIR") or ("/data" if os.path.isdir("/data") else os.path.dirname(__file__))


def cache_path() -> str:
    return os.path.join(_dir(), _CACHE_FILENAME)


def lock_path() -> str:
    return os.path.join(_dir(), _LOCK_FILENAME)


def load_cache() -> dict:
    try:
        with open(cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_stale(days: int = STALE_DAYS) -> bool:
    p = cache_path()
    if not os.path.exists(p):
        return True
    return (time.time() - os.path.getmtime(p)) > days * 86400


def is_locked(max_age_sec: int = LOCK_MAX_AGE_SEC) -> bool:
    p = lock_path()
    if not os.path.exists(p):
        return False
    return (time.time() - os.path.getmtime(p)) < max_age_sec


def acquire_lock() -> bool:
    if is_locked():
        return False
    try:
        with open(lock_path(), "w") as f:
            f.write(str(time.time()))
        return True
    except OSError:
        return False


def release_lock() -> None:
    try:
        os.remove(lock_path())
    except OSError:
        pass


def _save(data: dict) -> None:
    tmp = cache_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, cache_path())


def build_cache(tickers: list, throttle: float = 0.5, save_every: int = 50) -> dict:
    """yfinance info['industry']로 tickers 전체 순회, 실패 시 1회 재시도.
    기존 캐시 위에 갱신(실패한 종목은 이전 값 유지 — 매월 재빌드가 일시적
    실패로 커버리지를 후퇴시키지 않게). save_every개마다 중간 저장해
    중단돼도 그때까지 결과는 보존."""
    import yfinance as yf

    result = load_cache()
    ok, fail = 0, 0
    for i, t in enumerate(tickers):
        industry = None
        for attempt in range(2):   # 최초 1회 + 재시도 1회
            try:
                info = yf.Ticker(t).info
                industry = info.get("industry")
            except Exception:
                industry = None
            if industry:
                break
            time.sleep(throttle)
        if industry:
            result[t.upper()] = industry
            ok += 1
        else:
            fail += 1
        time.sleep(throttle)
        if (i + 1) % save_every == 0:
            _save(result)
            print(f"[us_industry_cache] {i + 1}/{len(tickers)} (성공 {ok} 실패 {fail})", flush=True)
    _save(result)
    return result

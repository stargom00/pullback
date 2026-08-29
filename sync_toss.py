"""
sync_toss.py — 토스증권 잔고를 스캐너 서버(포지션 보드)로 동기화.

맥 로컬 전용 스크립트다. Railway는 토스 Open API를 직접 호출할 수 없다
(허용 IP 방식인데 Railway 아웃바운드 IP가 배포마다 안 고정됨) — 그래서
"조회는 로컬에서, 결과만 서버로" 구조를 쓴다.

읽어서 보내는 값: 티커·종목명·시장(KR/US)·수량·평단·통화 뿐이다.
계좌번호 등 식별정보는 절대 만지지 않는다(toss_client.py 자체가 계좌 관련
호출 결과에서 이 값들을 쓰지도 않는다). 주문 관련 호출은 toss_client.py에
아예 없다(조회 전용, 모듈 docstring 참고) — 이 스크립트도 마찬가지로
조회 → 전송만 한다.

환경변수(.env):
  TOSS_CLIENT_ID / TOSS_CLIENT_SECRET   — toss_client.py가 사용
  SYNC_TOKEN                            — 서버 인증 공유 시크릿
                                           (Railway 환경변수에도 동일 값 설정 필요)
  PULLBACK_SERVER_URL                   — 기본값: 프로덕션 URL

수동 실행:
  python3 sync_toss.py
자동 실행(launchd) 설정은 docs/toss_position_sync_setup.md 참고.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from toss_client import TossApiError, TossAuthError, TossClient

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [sync_toss] %(message)s")
logger = logging.getLogger("sync_toss")

DEFAULT_SERVER_URL = "https://pullback-production.up.railway.app"
SERVER_URL = os.environ.get("PULLBACK_SERVER_URL", DEFAULT_SERVER_URL).rstrip("/")
SYNC_TOKEN = os.environ.get("SYNC_TOKEN")
REQUEST_TIMEOUT = 15.0
KST = timezone(timedelta(hours=9))

# launchd는 StartInterval=1800(30분마다, 하루 종일)으로 단순하게 두고, "장중만
# 돈다"는 실제 요구사항은 여기서 판정한다 — StartCalendarInterval로 30분 단위
# 시각을 30개 넘게 나열하는 것보다 이 쪽이 테스트하기 쉽고 읽기 쉽다(DST로
# 미국장 KST 시각이 밀려도 여기 숫자만 고치면 됨, plist는 안 건드림).
# KR: 09:00~15:30 KST. US: 정규장 09:30~16:00 ET가 서머타임에 따라 22:30~05:00
# 또는 23:30~06:00 KST로 밀리므로, 두 경우를 다 포함하게 22:00~06:30로 넉넉히 잡음.
def _is_market_hours(now_kst: datetime) -> bool:
    hm = now_kst.hour * 60 + now_kst.minute
    kr = 9 * 60 <= hm <= 15 * 60 + 30
    us = hm >= 22 * 60 or hm <= 6 * 60 + 30
    return kr or us


def _extract_positions(holdings: dict) -> list:
    """toss_client.get_holdings() 원본 응답에서 최소 정보만 추린다.
    필드명은 실제 openapi.json 스펙(getHoldings, HoldingsOverview.items) 기준:
    symbol/name/marketCountry/quantity/averagePurchasePrice/currency."""
    out = []
    for item in holdings.get("items", []):
        try:
            qty = float(item["quantity"])
        except (KeyError, TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        try:
            avg_price = float(item["averagePurchasePrice"])
        except (KeyError, TypeError, ValueError):
            avg_price = 0.0
        out.append({
            "ticker": item.get("symbol"),
            "name": item.get("name"),
            "market": item.get("marketCountry"),   # "KR" | "US"
            "quantity": qty,
            "avg_price": avg_price,
            "currency": item.get("currency"),
        })
    return out


def main() -> int:
    force = "--force" in sys.argv   # 수동 실행 시 장중 게이트 무시(테스트용)
    now_kst = datetime.now(KST)
    # 주말 판정은 일부러 안 함 — 미국장이 KST 자정을 넘기게(전날 22시~다음날
    # 6시반) 걸쳐 있어서 "토요일 새벽 KST"가 실제로는 금요일 미국 정규장인
    # 경계 케이스가 생김(요일 보정하려면 미묘해짐). 순수 휴장일(주말 낮·공휴일)
    # 시간대에 헛돌아도 토스 API 조회 1회일 뿐이라 비용이 거의 없어, 정교한
    # 요일 계산보다 이 쪽이 더 안전하고 단순하다.
    if not force and not _is_market_hours(now_kst):
        logger.info("장중 시간대가 아니라 스킵 (%s KST)", now_kst.strftime("%a %H:%M"))
        return 0

    if not SYNC_TOKEN:
        logger.error("SYNC_TOKEN이 .env에 없습니다. 서버와 공유하는 시크릿을 먼저 설정하세요.")
        return 1

    try:
        client = TossClient()
    except TossAuthError as e:
        logger.error("토스 클라이언트 초기화 실패: %s", e.message)
        return 1

    try:
        holdings = client.get_holdings()
    except TossApiError as e:
        logger.error("보유종목 조회 실패: [%s] %s", e.code, e.message)
        return 1

    positions = _extract_positions(holdings)
    logger.info("보유 종목 %d건 조회됨 (수량>0만)", len(positions))

    try:
        resp = requests.post(
            f"{SERVER_URL}/api/positions/sync",
            headers={"X-Sync-Token": SYNC_TOKEN, "Content-Type": "application/json"},
            json={"positions": positions},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("서버 전송 실패: %s", e)
        return 1

    if resp.status_code != 200:
        logger.error("서버 응답 오류 (HTTP %d): %s", resp.status_code, resp.text[:300])
        return 1

    body = resp.json()
    logger.info("동기화 완료: %d건 저장됨", body.get("count", 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())

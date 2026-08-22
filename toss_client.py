"""
toss_client.py — 토스증권 Open API 조회 전용 클라이언트.

공식 문서: https://developers.tossinvest.com/docs
스펙 소스: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json (v1.2.14, 2026-08-23 확인)

⚠️ 의도적으로 조회 전용이다. 주문 생성/정정/취소, 조건주문 관련 엔드포인트
(POST /api/v1/orders, .../orders/{id}/modify, .../orders/{id}/cancel,
/api/v1/conditional-orders 등)는 이 모듈에 절대 추가하지 않는다.

인증 흐름(OAuth2 Client Credentials):
  1. POST /oauth2/token 으로 access_token 발급 (expires_in=86400초, 즉 24시간).
     client당 유효 토큰은 1개뿐이고 재발급 시 이전 토큰이 즉시 무효화되므로
     (공식 문서 명시), 캐시가 만료되기 전까지는 절대 재발급하지 않는다.
  2. 계좌 관련 API(보유종목 등)는 Authorization 헤더 외에 X-Tossinvest-Account
     헤더(GET /api/v1/accounts 응답의 accountSeq)가 추가로 필요하다.

Rate Limit 그룹(공식 문서 overview.md 기준, 초당 요청 수):
  AUTH=5, ACCOUNT=1, ASSET=5, ORDER=10, ORDER_HISTORY=5, MARKET_DATA=15
이 상수들은 참고용일 뿐 자체 스로틀링에는 안 쓴다 — 429 응답의 Retry-After/
X-RateLimit-Reset 헤더를 그대로 따르는 쪽이 서버가 실제로 준 값이라 더 정확하다.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Union

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("toss_client")

BASE_URL = "https://openapi.tossinvest.com"

RATE_LIMIT_GROUPS = {
    "AUTH": 5,
    "ACCOUNT": 1,
    "ASSET": 5,
    "ORDER": 10,
    "ORDER_HISTORY": 5,
    "MARKET_DATA": 15,
}

# 토큰 만료 이만큼 전에 미리 재발급 취급 — 만료 경계에서 요청이 401 맞는 상황 방지.
TOKEN_EXPIRY_MARGIN_SECONDS = 60


class TossApiError(Exception):
    """토스증권 API가 에러 응답(4xx/5xx)을 반환했을 때 공통으로 발생시키는 예외."""

    def __init__(self, status_code, code, message, request_id=None, data=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.data = data
        super().__init__(f"[HTTP {status_code}] {code}: {message}")


class TossAuthError(TossApiError):
    """토큰 발급/인증 실패 — 자격증명 누락, client_id/secret 오류, IP 미허용 등.
    재시도로 해결되지 않는 클래스의 에러."""


class TossRateLimitError(TossApiError):
    """재시도를 max_retries만큼 다 소진하고도 여전히 429인 경우에만 최종적으로 발생."""


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # time.monotonic() 기준 절대 시각


class TossClient:
    """토스증권 Open API 조회 전용 클라이언트.

    사용 예:
        client = TossClient()  # .env의 TOSS_CLIENT_ID/TOSS_CLIENT_SECRET 사용
        holdings = client.get_holdings()
        prices = client.get_prices(["005930", "AAPL"])
    """

    TOKEN_ENDPOINT = "/oauth2/token"
    ACCOUNTS_ENDPOINT = "/api/v1/accounts"
    HOLDINGS_ENDPOINT = "/api/v1/holdings"
    PRICES_ENDPOINT = "/api/v1/prices"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: str = BASE_URL,
        max_retries: int = 3,
        timeout: float = 10.0,
    ):
        self.client_id = client_id or os.environ.get("TOSS_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("TOSS_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise TossAuthError(
                status_code=0,
                code="missing-credentials",
                message="TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 환경변수가 없습니다. "
                ".env 파일에 두 값을 설정하세요.",
            )
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout

        self._session = requests.Session()
        self._token_lock = threading.Lock()
        self._token: Optional[_CachedToken] = None
        self._account_seq_cache: Optional[int] = None

    # ── 토큰 발급/캐싱 ──────────────────────────────────────────────

    def _fetch_new_token(self) -> _CachedToken:
        resp = self._session.post(
            self.base_url + self.TOKEN_ENDPOINT,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            self._raise_oauth_error(resp)
        body = resp.json()
        expires_in = body.get("expires_in", 0)
        return _CachedToken(
            access_token=body["access_token"],
            expires_at=time.monotonic() + expires_in - TOKEN_EXPIRY_MARGIN_SECONDS,
        )

    @staticmethod
    def _raise_oauth_error(resp: requests.Response):
        try:
            body = resp.json()
        except ValueError:
            body = {}
        error = body.get("error", "unknown_error")
        description = body.get("error_description") or resp.text[:200]
        if resp.status_code == 403:
            description += " (토스증권 WTS > Open API > 허용 IP 관리에 이 서버 IP가 등록되어 있는지 확인하세요)"
        raise TossAuthError(resp.status_code, error, description)

    def _get_token(self) -> str:
        """캐시된 토큰이 유효하면 그대로 재사용하고, 없거나 만료 임박일 때만
        새로 발급한다. client당 유효 토큰이 1개뿐이라(재발급 시 이전 토큰
        즉시 무효화, 공식 문서 명시) 불필요한 재발급은 절대 하지 않는다."""
        with self._token_lock:
            if self._token is None or time.monotonic() >= self._token.expires_at:
                logger.info("토스 access token 재발급 중...")
                self._token = self._fetch_new_token()
            return self._token.access_token

    def _invalidate_token(self):
        with self._token_lock:
            self._token = None

    # ── 공통 요청 헬퍼(429/403/5xx 재시도 포함) ──────────────────────

    def _request(self, method: str, path: str, *, headers=None, params=None) -> object:
        headers = dict(headers or {})
        retried_401 = False

        for attempt in range(self.max_retries + 1):
            headers["Authorization"] = f"Bearer {self._get_token()}"
            resp = self._session.request(
                method, self.base_url + path, headers=headers, params=params,
                timeout=self.timeout,
            )

            if resp.status_code == 200:
                return resp.json()["result"]

            # 토큰이 (다른 프로세스의 재발급 등으로) 무효화됐을 가능성 — 한 번만
            # 강제로 다시 받아서 재시도. 그래도 401이면 자격증명 자체 문제로 판단.
            if resp.status_code == 401 and not retried_401:
                retried_401 = True
                self._invalidate_token()
                continue

            # 429: 재시도 여지가 있으면 서버가 알려준 대기시간만큼 쉬고 재시도.
            if resp.status_code == 429 and attempt < self.max_retries:
                wait = self._retry_after_seconds(resp)
                logger.warning(
                    "429 rate limit (%s %s) — %.1f초 대기 후 재시도 (%d/%d)",
                    method, path, wait, attempt + 1, self.max_retries,
                )
                time.sleep(wait)
                continue

            # 5xx: 일시적 서버 오류일 수 있어 지수 백오프로 재시도.
            if resp.status_code >= 500 and attempt < self.max_retries:
                wait = min(2 ** attempt, 8)
                logger.warning(
                    "%d 서버 오류 (%s %s) — %.1f초 대기 후 재시도 (%d/%d)",
                    resp.status_code, method, path, wait, attempt + 1, self.max_retries,
                )
                time.sleep(wait)
                continue

            # 403(IP 미허용 등)·400·404, 그리고 재시도를 다 소진한 401/429/5xx는
            # 재시도로 해결되지 않으므로 여기서 즉시 실패시킨다.
            self._raise_api_error(resp)

        # 위 for문은 항상 return 또는 raise로 끝나므로 여기 도달하지 않는다.
        raise TossApiError(0, "unreachable", "재시도 로직 오류")

    @staticmethod
    def _retry_after_seconds(resp: requests.Response) -> float:
        for header in ("Retry-After", "X-RateLimit-Reset"):
            value = resp.headers.get(header)
            if value:
                try:
                    return max(float(value), 0.1)
                except ValueError:
                    pass
        return 1.0

    @staticmethod
    def _raise_api_error(resp: requests.Response):
        try:
            body = resp.json()
            err = body.get("error", {})
        except ValueError:
            err = {}
        code = err.get("code", f"http-{resp.status_code}")
        message = err.get("message") or resp.text[:200]
        cls = TossRateLimitError if resp.status_code == 429 else TossApiError
        raise cls(
            resp.status_code, code, message,
            request_id=err.get("requestId"), data=err.get("data"),
        )

    # ── 계좌 ──────────────────────────────────────────────────────

    def get_accounts(self) -> list:
        """GET /api/v1/accounts — 종합매매 계좌 목록.
        Rate limit 그룹: ACCOUNT(1req/s)."""
        return self._request("GET", self.ACCOUNTS_ENDPOINT)

    def get_default_account_seq(self) -> int:
        """계좌가 1개뿐인 일반적인 경우를 위한 헬퍼. 첫 번째 계좌의 accountSeq를
        메모리에 캐싱해서 반환한다(호출마다 /accounts를 다시 부르지 않도록 —
        ACCOUNT 그룹 rate limit이 초당 1회로 가장 빠듯함). 계좌가 여러 개면
        get_accounts()로 직접 원하는 계좌를 선택해서 써야 한다."""
        if self._account_seq_cache is not None:
            return self._account_seq_cache
        accounts = self.get_accounts()
        if not accounts:
            raise TossApiError(0, "no-accounts", "조회 가능한 종합매매 계좌가 없습니다.")
        self._account_seq_cache = accounts[0]["accountSeq"]
        return self._account_seq_cache

    def get_holdings(
        self, account_seq: Optional[int] = None, symbol: Optional[str] = None,
    ) -> dict:
        """GET /api/v1/holdings — 보유 종목 및 평가금액/손익 요약.
        국내(KR)·미국(US) 주식만 포함(해외 옵션·채권 제외).
        account_seq를 생략하면 get_default_account_seq()로 자동 조회한다.
        symbol을 주면 해당 종목 기준으로 요약도 재계산된 결과를 받는다.
        Rate limit 그룹: ASSET(5req/s)."""
        account_seq = account_seq or self.get_default_account_seq()
        params = {"symbol": symbol} if symbol else None
        return self._request(
            "GET", self.HOLDINGS_ENDPOINT,
            headers={"X-Tossinvest-Account": str(account_seq)},
            params=params,
        )

    # ── 시세 ──────────────────────────────────────────────────────

    def get_prices(self, symbols: Union[str, Iterable[str]]) -> list:
        """GET /api/v1/prices — 국내/미국 주식 현재가(같은 호출에 KR·US 심볼
        혼합 가능, 최대 200개). symbols는 리스트(["005930","AAPL"])나
        콤마구분 문자열("005930,AAPL") 둘 다 받는다.
        Rate limit 그룹: MARKET_DATA(15req/s)."""
        if not isinstance(symbols, str):
            symbols = ",".join(symbols)
        return self._request("GET", self.PRICES_ENDPOINT, params={"symbols": symbols})

    def get_price(self, symbol: str) -> Optional[dict]:
        """단일 종목 현재가 조회 — get_prices()의 편의 래퍼."""
        result = self.get_prices([symbol])
        return result[0] if result else None

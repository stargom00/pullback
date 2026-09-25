"""v5.287 — 저널 입력의 KR 시장(.KS/.KQ) 판정은 **유니버스**로 한다.

[사고] 접미사 없는 코드를 저장할 때 프론트가 `[코드+".KQ", 코드+".KS"]`를
`/api/prices`에 순서대로 던져 먼저 값이 오는 쪽을 골랐다. 그런데
`naver_kr.to_code()`는 접미사를 검증하지 않고 `split(".")[0]`으로 잘라내기만
한다(naver siseJson이 6자리 코드만 받고 시장 구분을 안 씀) — **어떤 접미사를
붙여도 값이 온다.** 그래서 항상 `.KQ`가 이겼고, 코스피 003490(대한항공)이
`.KQ`로 저널에 저장됐다(CLAUDE.md에 미해결 과제로 기록돼 있던 것).

판정을 가격 조회가 아니라 유니버스 멤버십으로 옮기고, 유니버스에 없으면
**추측하지 않고** 사용자에게 시장을 묻는다.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app

ROOT = Path(__file__).resolve().parent
TEXT = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

FAKE_KR = {"003490.KS": "대한항공", "005930.KS": "삼성전자", "247540.KQ": "에코프로비엠"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app, "APP_PASSWORD", "", raising=False)
    monkeypatch.setattr(app, "get_universe", lambda m: dict(FAKE_KR))
    return TestClient(app.app)


# ── 서버: 유니버스 멤버십으로 판정 ──────────────────────────────────────
def test_kospi_code_resolves_to_ks(client):
    """003490은 코스피다 — 순차 시도 시절엔 .KQ가 나왔다."""
    d = client.get("/api/kr-suffix/003490").json()
    assert d == {"ok": True, "ticker": "003490.KS", "name": "대한항공"}, d


def test_kosdaq_code_resolves_to_kq(client):
    d = client.get("/api/kr-suffix/247540").json()
    assert d["ok"] is True and d["ticker"] == "247540.KQ", d


def test_unknown_code_is_not_guessed(client):
    d = client.get("/api/kr-suffix/999999").json()
    assert d["ok"] is False, d
    assert "ticker" not in d, "유니버스에 없는데 티커를 만들어냈다"


def test_resolver_is_shared_with_lookup():
    """판정 사본 금지 — /api/lookup과 같은 resolve_name_to_ticker를 써야 한다."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index('@app.get("/api/kr-suffix/{code}")'):src.index('@app.get("/api/lookup/{ticker}")')]
    assert "resolve_name_to_ticker" in body, body[-400:]


# ── 프론트: 판정 함수를 그대로 꺼내 node로 실행 ─────────────────────────
def _fn(name: str) -> str:
    i = TEXT.index(f"function {name}(")
    b = TEXT.index("{", i)
    d = 0
    for k in range(b, len(TEXT)):
        if TEXT[k] == "{":
            d += 1
        elif TEXT[k] == "}":
            d -= 1
            if d == 0:
                return TEXT[i:k + 1]
    raise AssertionError(name)


def _decide(resolved):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = _fn("krSuffixDecision")
    harness = f"{src}\nconsole.log(JSON.stringify(krSuffixDecision({json.dumps(resolved)})));"
    p = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip())


def test_front_uses_the_server_answer(client):
    assert _decide({"ok": True, "ticker": "003490.KS", "name": "대한항공"})["ticker"] == "003490.KS"
    assert _decide({"ok": True, "ticker": "247540.KQ"})["ticker"] == "247540.KQ"


def test_front_asks_the_user_when_unmapped():
    for resolved in ({"ok": False, "reason": "not_in_universe"}, None, {}):
        d = _decide(resolved)
        assert d.get("needsMarketChoice") is True, resolved
        assert "ticker" not in d, "매핑이 없는데 티커를 정했다"
        assert ".KS" in d["message"] and ".KQ" in d["message"]


def test_sequential_price_probing_is_gone():
    """순차 시도가 되살아나면 같은 사고가 그대로 재발한다."""
    assert "'.KQ', ticker + '.KS'" not in TEXT
    assert TEXT.count("cand.find(t => prices[t] != null)") == 0
    # 두 저장 경로 모두 새 판정을 거쳐야 한다(호출부 2곳)
    assert TEXT.count("await _resolveKrTicker(") == 2, TEXT.count("await _resolveKrTicker(")

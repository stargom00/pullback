"""v5.351 — 코인 일지 GET만 봇 토큰 읽기 경로. 쓰기 메서드는 여전히 아님."""
import app


def test_coin_journal_get_is_bot_read_path():
    assert app._is_bot_read_path("GET", "/api/coin-journal")


def test_coin_journal_write_methods_not_bot_read():
    for m in ("POST", "PUT", "DELETE"):
        assert not app._is_bot_read_path(m, "/api/coin-journal")
    assert not app._is_bot_read_path("PUT", "/api/coin-journal/abc")
    assert not app._is_bot_read_path("DELETE", "/api/coin-journal/abc")

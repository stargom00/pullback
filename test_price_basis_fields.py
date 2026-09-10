"""서버 is_live/market_open_now 계산(_price_basis_fields) 테스트 (v5.234,
사용자 지시).

배경: v5.233에서 프론트 문구 판정(priceBasisNoteText)을 가짜 데이터
(KR×US · 장중×장전×장중-stale)로 테스트했는데, 그 테스트는 "서버가
is_live=False·market_open_now=True 조합을 실제로 만들어낼 수 있다"는
걸 그냥 가정하고 넣은 값이었다 — 서버 쪽(daykey/ts + _is_market_open_now
조합) 자체는 한 번도 검증된 적이 없었다(사용자 지적). 이 파일은 그
가정을 직접 확인한다: `_price_basis_fields(cached_scan, is_kr)`을
`_is_market_open_now`만 monkeypatch로 고정해(실제 "지금 몇 시인지"에
테스트가 의존하면 실행 시각마다 결과가 달라져 flaky해진다 — 그래서
현재 시각 자체는 안 쓰고 이 함수만 대체) daykey 유무 × 장중여부 4개
조합을 전부 실행하고, 그중 "장중인데 캐시가 stale"(daykey가 남아있는데
지금은 장중)이 정확히 test_price_basis_note.py가 가정한
(is_live=False, market_open_now=True)를 만들어내는지 명시적으로 증명한다.

daykey/ts 의미: `_warm_market()`의 EOD 분기가 daykey를 채우고(장 마감
확정 스냅샷), 장중 워밍 분기는 daykey=None·ts=<시각>만 채운다(app.py
_warm_market 참고) — "daykey가 남아있는데 지금은 장중"은 장이 막 다시
열렸는데 첫 장중 재워밍(최대 4~8분 주기)이 아직 한 번도 안 돈 좁은
창에서 실제로 발생 가능한 상태다(가상의 조합이 아님)."""
import app


def _patch_market_open(monkeypatch, is_open: bool):
    monkeypatch.setattr(app, "_is_market_open_now", lambda is_kr: is_open)


def test_warming_live_when_market_open(monkeypatch):
    """daykey 없음(장중 워밍 캐시) + 시장 열림 → 라이브."""
    _patch_market_open(monkeypatch, True)
    is_live, market_open_now = app._price_basis_fields({"daykey": None, "ts": 123.0}, is_kr=False)
    assert (is_live, market_open_now) == (True, True)


def test_warming_stale_when_market_closed(monkeypatch):
    """daykey 없음(장중 워밍 캐시) + 시장 닫힘 → 장전/스테일 취급."""
    _patch_market_open(monkeypatch, False)
    is_live, market_open_now = app._price_basis_fields({"daykey": None, "ts": 123.0}, is_kr=False)
    assert (is_live, market_open_now) == (False, False)


def test_eod_daykey_but_market_reopened_is_stale_while_open(monkeypatch):
    """★ 사용자가 직접 확인을 요청한 조합: EOD daykey가 아직 남아있는데
    (전날 장마감 확정 스냅샷) 지금은 그 시장이 다시 열려 있는 상태 —
    "장중인데 캐시 stale". test_price_basis_note.py의 "US 장중인데
    카드가 stale"/"KR 장중인데 카드가 stale" 케이스가 가정한
    (is_live=False, market_open_now=True)가 서버 로직에서 실제로
    나오는지 여기서 직접 증명한다."""
    _patch_market_open(monkeypatch, True)
    is_live, market_open_now = app._price_basis_fields({"daykey": "2026-09-09"}, is_kr=False)
    assert (is_live, market_open_now) == (False, True), (
        "서버가 '장중인데 캐시 stale' 조합을 못 만들어낸다 — "
        "test_price_basis_note.py의 가정이 서버 로직과 안 맞을 수 있음"
    )


def test_eod_daykey_and_market_closed_is_premarket(monkeypatch):
    """EOD daykey + 시장 닫힘 → 장전(가장 흔한 정상 케이스, 장마감
    직후~다음 개장 전)."""
    _patch_market_open(monkeypatch, False)
    is_live, market_open_now = app._price_basis_fields({"daykey": "2026-09-09"}, is_kr=False)
    assert (is_live, market_open_now) == (False, False)


def test_no_cache_is_conservatively_not_live(monkeypatch):
    """스캔이 한 번도 안 돌아 캐시 자체가 없으면(cached_scan=None) 라이브
    라고 주장하면 안 된다 — 보수적으로 is_live=False. market_open_now는
    캐시 유무와 무관하게(그 시장이 지금 열려있는지만 보므로) True 그대로."""
    _patch_market_open(monkeypatch, True)
    is_live, market_open_now = app._price_basis_fields(None, is_kr=False)
    assert is_live is False
    assert market_open_now is True


def test_is_kr_flag_is_forwarded(monkeypatch):
    """is_kr 인자가 실제로 _is_market_open_now에 그대로 전달되는지 —
    market-agnostic 설계(프론트 테스트 test_price_basis_note_market_
    agnostic 참고)의 전제인 "그 시장 자체는 파라미터로 명시적으로 받는다"
    를 서버 쪽에서도 확인."""
    seen = []
    monkeypatch.setattr(app, "_is_market_open_now", lambda is_kr: seen.append(is_kr) or True)
    app._price_basis_fields({"daykey": None}, is_kr=True)
    app._price_basis_fields({"daykey": None}, is_kr=False)
    assert seen == [True, False]

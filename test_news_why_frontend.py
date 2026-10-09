"""v5.346 카드 "🗞 왜?" 프론트 — static/index.html의 newsWhyHtml·newsWhyBoxHtml을 Node로 실행(재구현 아님, CLAUDE.md 레시피).

사보타주 확인(2026-10-10, FAIL 확인 후 원복): newsWhyHtml의 no_key 문구 변경 → test_status_texts FAIL ·
_lpeCard의 박스 호출 삭제 → test_box_on_scan_cards_and_lowpoint_eval FAIL.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEXT = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name):
    i = TEXT.index(f"function {name}(")
    b = TEXT.index("{", TEXT.index(")", i))
    d = 0
    for k in range(b, len(TEXT)):
        d += {"{": 1, "}": -1}.get(TEXT[k], 0)
        if d == 0:
            return TEXT[i:k + 1]
    raise AssertionError(name)


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    src = "\n".join(_fn(n) for n in ("_escapeHtml", "_newsAttr", "newsWhyHtml", "newsWhyBoxHtml"))
    p = subprocess.run(["node", "-e", src + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_status_texts():
    ok = _js('newsWhyHtml({status:"ok",market:"KR",lines:["1. 무슨 일: a","2. 성격: 희석(유증)","3. 근거: b"],model:"claude-haiku-4-5-20251001",'
             'tokens_in:1200,tokens_out:80,news_count:3,disclosure_count:1,generated_at:"2026-10-10 09:00:00"})')
    assert "2. 성격: 희석(유증)" in ok and "토큰 1200/80" in ok and "공시 1건" in ok and "강제 갱신" in ok and "캐시" not in ok
    cached = _js('newsWhyHtml({status:"cached",market:"US",lines:["1.","2.","3."],model:"m",tokens_in:1,tokens_out:2,news_count:2})')
    assert "캐시" in cached and "공시" not in cached
    assert "API 키 미설정" in _js('newsWhyHtml({status:"no_key"})')
    assert "오늘 한도 초과 (하루 50회)" in _js('newsWhyHtml({status:"limit",limit:50})')
    ne = _js('newsWhyHtml({status:"news_error",message:"뉴스 수집 실패: HTTP 503"})')
    se = _js('newsWhyHtml({status:"summary_error",message:"요약 실패: 응답이 3줄 형식이 아님",raw:"<b>x</b>"})')
    assert "뉴스 수집 실패" in ne and "요약 실패" not in ne
    assert "요약 실패" in se and "뉴스 수집 실패" not in se and "&lt;b&gt;x&lt;/b&gt;" in se          # 원문은 escape
    cd = _js('newsWhyHtml({status:"cooldown",retry_after_sec:42,lines:["1.","2.","3."]})')
    assert "42초 뒤" in cd and "강제 갱신</button>" not in cd
    assert "이미 요약 중" in _js('newsWhyHtml({status:"in_progress"})')


def test_box_escapes_attributes():
    h = _js('newsWhyBoxHtml("005930", "KR", "삼성\\"전자\'<x>")')
    assert 'data-ticker="005930"' in h and "&quot;" in h and "&#39;" in h and "&lt;x&gt;" in h and "🗞 왜?" in h
    assert _js('newsWhyBoxHtml("", "KR", "x")') == ""


def test_box_on_scan_cards_and_lowpoint_eval():
    assert "const NEWS_WHY_MODES = new Set(['pullback', 'turnaround', 'imminent', 'boxbreak', 'breakout']);" in TEXT
    card_src, lpe_src = _fn("card"), _fn("_lpeCard")
    assert card_src.count("NEWS_WHY_MODES.has(s.mode) ? newsWhyBoxHtml(s.ticker, s.market, s.name)") == 1
    assert lpe_src.count("(rec.mkt === 'KR' || rec.mkt === 'US') ? newsWhyBoxHtml(rec.code, rec.mkt, lpDisplayName(rec))") == 1   # 코인 등 제외
    assert TEXT.count("newsWhyBoxHtml(") == 3                                  # 정의 1 + 호출 2(스캔 카드·저점 평가 카드)
    assert "event.stopPropagation();newsWhy(this,false)" in TEXT and "event.stopPropagation();newsWhy(this,true)" in TEXT

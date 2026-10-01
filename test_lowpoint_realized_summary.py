"""v5.311 — 저점 탭 상단 실현 수익금 요약(이번 달·올해).

CLAUDE.md "텍스트 추출 + Node 실행" 레시피: `static/index.html`의 판정 함수를
**그대로 꺼내 node로 실행**한다(파이썬 재구현 금지 — 두 구현이 갈라질 위험 제거).

규칙: 매도일(KST 문자열) 기준 · KR/US 합산 금지 · 보유(미종료)는 제외.

사보타주 확인(2026-10-01, 전부 FAIL 확인 후 원복):
① `lpRealizedSummary`에서 매도일 필터(year/month 비교) 제거 → 4건 FAIL
② 보유(sellDate 없음) 제외 조건 제거 → **통과(FAIL 아님). 테스트 결함이 아니라 그 가드가
   중복이기 때문이다** — sellDate가 없으면 `String(undefined).slice(0,4)`가 "unde"라서
   연·월 비교가 이미 배제한다(빈 문자열도 같다). 실증: 가드 있는 버전과 없는 버전의
   출력이 동일(2026-10-01 확인). 가독성용으로 가드는 남겼고, 실제 방어는 ①의 날짜 비교다.
③ KR/US를 한 버킷에 합산 → test_kr_us_not_merged 외 3건 FAIL
④ 숫자 가드 제거(Number(null)=0 통과) → test_bad_numbers_skipped FAIL
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

IDX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")


def _extract(name: str) -> str:
    """중괄호 깊이를 세어 함수 본문을 그대로 잘라낸다(정규식만으론 중첩을 못 자른다)."""
    src = open(IDX, encoding="utf-8").read()
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"{name}: 닫는 중괄호를 못 찾음")


def _node(call: str, *fns: str):
    """필요한 production 함수들을 그대로 꺼내 붙이고 호출식을 실행한다.
    의존 함수를 빼먹으면 ReferenceError로 즉시 드러난다(조용한 통과 없음)."""
    src = "\n".join(_extract(f) for f in fns) + f"\nconsole.log(JSON.stringify({call}));"
    p = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _run(closed, today="2026-10-01"):
    return _node(f"lpRealizedSummary({json.dumps(closed, ensure_ascii=False)},"
                 f" {json.dumps(today)})",
                 "lpRealizedPnl", "lpRealizedSummary")


def _months(closed):
    """월간 요약(lpMonthlySummary) — 같은 공용 가드를 쓴다."""
    return _node(f"lpMonthlySummary({json.dumps(closed, ensure_ascii=False)})",
                 "lpRealizedPnl", "lpReturnPct", "lpMonthlySummary")


def _t(mkt, sell_date, buy, sell, qty, **kw):
    r = {"mkt": mkt, "sellDate": sell_date, "buyPrice": buy, "sellPrice": sell, "qty": qty}
    r.update(kw)
    return r


# 이번 달 KR 2건(+100,000 / −30,000) · 지난달 KR 1건(+500,000) · 이번 달 US 1건(+250.50)
SAMPLE = [
    _t("KR", "2026-10-01", 10000, 11000, 100),      # +100,000
    _t("KR", "2026-10-15", 5000, 4700, 100),        # −30,000
    _t("KR", "2026-09-30", 20000, 25000, 100),      # +500,000 (지난달)
    _t("US", "2026-10-02", 50.0, 55.01, 50),        # +250.50
]


def test_month_and_year_sums():
    got = _run(SAMPLE)
    assert got["month"]["KR"] == 70000      # 100,000 − 30,000
    assert got["year"]["KR"] == 570000      # + 지난달 500,000
    assert got["month"]["US"] == 250.5
    assert got["year"]["US"] == 250.5
    assert got["n"]["month"]["KR"] == 2 and got["n"]["year"]["KR"] == 3
    assert got["n"]["month"]["US"] == 1


def test_month_boundary_excludes_previous_month():
    """매도일이 지난달 말일(09-30)이면 이번 달 집계에 들어가면 안 된다."""
    only_prev = [_t("KR", "2026-09-30", 20000, 25000, 100)]
    got = _run(only_prev)
    assert got["month"]["KR"] == 0 and got["n"]["month"]["KR"] == 0
    assert got["year"]["KR"] == 500000 and got["n"]["year"]["KR"] == 1


def test_previous_year_excluded_from_year():
    got = _run([_t("KR", "2025-12-31", 10000, 12000, 100)])
    assert got["year"]["KR"] == 0 and got["month"]["KR"] == 0
    assert got["n"]["year"]["KR"] == 0


def test_open_positions_excluded():
    """보유(sellDate 없음)는 실현이 아니다.

    픽스처에 **유효한 sellPrice**를 넣는다 — 매도 취소·이행 중처럼 값은 남았는데
    매도일만 빈 레코드가 실현으로 새지 않는지를 본다. (참고: `if (!r.sellDate) continue`를
    지워도 결과는 같다 — 날짜 비교가 이미 배제하기 때문이고, 그래서 그 줄은 가독성용
    중복 가드다. 실제 방어는 연·월 비교다.)"""
    got = _run([{"mkt": "KR", "buyPrice": 10000, "sellPrice": 99000, "qty": 100},
                _t("KR", "2026-10-05", 10000, 11000, 10)])
    assert got["month"]["KR"] == 10000 and got["n"]["month"]["KR"] == 1
    assert got["year"]["KR"] == 10000
    assert got["skipped"] == 0, "보유는 '값 누락'이 아니라 집계 대상 자체가 아니다"


def test_kr_us_not_merged():
    got = _run(SAMPLE)
    assert got["month"]["KR"] != got["month"]["US"]
    assert got["month"]["KR"] == 70000, "US 금액이 KR에 섞였다"
    assert got["month"]["US"] == 250.5, "KR 금액이 US에 섞였다"


def test_zero_us_keeps_zero_counts():
    got = _run([_t("KR", "2026-10-01", 10000, 11000, 100)])
    assert got["n"]["month"]["US"] == 0 and got["n"]["year"]["US"] == 0
    assert got["month"]["US"] == 0


def test_kr_zero_is_zero_not_missing():
    """KR 기록이 0건이어도 0으로 나와야 한다(화면에 ₩0원을 찍는 근거)."""
    got = _run([_t("US", "2026-10-02", 50.0, 55.0, 10)])
    assert got["month"]["KR"] == 0 and got["year"]["KR"] == 0


def test_unknown_market_counts_as_kr():
    """mkt가 비거나 이상하면 KR로 본다(US로 새서 $에 섞이지 않게)."""
    got = _run([_t("", "2026-10-01", 10000, 11000, 100)])
    assert got["month"]["KR"] == 100000 and got["month"]["US"] == 0


def test_bad_numbers_skipped():
    got = _run([_t("KR", "2026-10-01", None, 11000, 100),
                _t("KR", "2026-10-02", 10000, 11000, 10)])
    assert got["month"]["KR"] == 10000, "계산 불가 레코드가 NaN으로 번졌다"


def test_partial_close_records_counted():
    """분할 종료(partial)도 종료 기록이다 — 따로 빼지 않는다."""
    got = _run([_t("KR", "2026-10-01", 10000, 11000, 50, partial=True, partial_of=1)])
    assert got["month"]["KR"] == 50000


# ── 화면 결합부(렌더가 이 함수를 실제로 쓰는지) ──────────────────────

def test_render_uses_summary_above_the_form():
    """요약 줄이 제목 아래·기록 추가 폼 **위**에 들어가야 한다(사용자 지시)."""
    src = open(IDX, encoding="utf-8").read()
    assert "lpRealizedSummary(closed, today)" in src
    assert "`${head}${statHtml}${addHtml}" in src, "요약 줄 위치가 head→폼 사이가 아니다"


def test_render_hides_dollar_when_no_us_records():
    src = open(IDX, encoding="utf-8").read()
    i = src.index("const _rsCell")
    body = src[i:i + 700]
    assert "rs.n[bucket].US || rs.n.year.US" in body, "US 0건일 때 $ 숨김 조건이 없다"


def test_summary_uses_theme_tokens_and_color_rule():
    """양수 빨강·음수 파랑 + 테마 토큰(하드코딩 색 금지)."""
    src = open(IDX, encoding="utf-8").read()
    i = src.index("const _rsMoney")
    body = src[i:i + 400]
    assert "var(--c-red-fg3)" in body and "var(--c-blue-fg4)" in body
    assert "var(--n-muted)" in body
    assert not re.search(r"#[0-9a-fA-F]{3,6}", body), "하드코딩 색상값이 있다"


def test_other_lowpoint_ui_untouched():
    """기존 보유/종료/월간 요약/목표% 섹션은 그대로 — 헤더 문구로 확인."""
    src = open(IDX, encoding="utf-8").read()
    for label in (">보유<", ">종료<", ">월간 요약<"):
        assert label in src, f"{label} 섹션이 사라졌다"
    assert "function lpMonthlySummary(" in src


def test_version_badge_bumped():
    src = open(IDX, encoding="utf-8").read()
    m = re.search(r'id="verBadge"[^>]*>(v[\d.]+)<', src)
    assert m and m.group(1) == "v5.311"


def test_invalid_records_are_counted_not_hidden():
    """값 누락 레코드는 **건너뛴 사실을 남긴다**(조용히 빼지 않는다)."""
    got = _run([_t("KR", "2026-10-01", None, 11000, 100),
                _t("KR", "2026-10-02", 10000, 11000, 10),
                _t("KR", "2026-10-03", 10000, 11000, 0)])
    assert got["skipped"] == 2
    assert got["month"]["KR"] == 10000


def test_render_shows_skipped_notice():
    src = open(IDX, encoding="utf-8").read()
    i = src.index("const statHtml")
    assert "rs.skipped" in src[i:i + 900], "건너뛴 건수를 화면에 알리지 않는다"


# ── 월간 요약도 같은 가드를 쓴다(v5.311 보강, 사용자 지시) ───────────

def test_monthly_summary_skips_invalid_records():
    """결측 레코드가 월간 요약에서 **가짜 손익**으로 집계되지 않는다.
    (가드 전: 매수가 null → (11000-0)×100 = +1,100,000원이 그 달 수익금에 들어갔다.)"""
    rows = _months([
        _t("KR", "2026-10-01", None, 11000, 100),      # 매수가 없음 → 제외
        _t("KR", "2026-10-02", 10000, 11000, 10),      # +10,000
        _t("KR", "2026-10-03", 10000, None, 10),       # 매도가 없음 → 제외
        _t("KR", "2026-10-04", 10000, 11000, 0),       # 수량 0 → 제외
    ])
    assert len(rows) == 1
    m = rows[0]
    assert m["pnl"] == 10000, "결측 레코드가 손익에 섞였다"
    assert m["n"] == 1, "건수에 결측 레코드가 포함됐다"
    assert m["skipped"] == 3


def test_monthly_summary_reports_skipped_per_group():
    """제외 건수는 그 월·시장 행에 남는다(다른 달로 새지 않는다)."""
    rows = {(r["month"], r["mkt"]): r for r in _months([
        _t("KR", "2026-10-01", None, 11000, 100),
        _t("KR", "2026-09-01", 10000, 11000, 10),
        _t("US", "2026-10-02", 50.0, 55.0, 10),
    ])}
    assert rows[("2026-10", "KR")]["skipped"] == 1
    assert rows[("2026-10", "KR")]["n"] == 0
    assert rows[("2026-09", "KR")]["skipped"] == 0
    assert rows[("2026-10", "US")]["skipped"] == 0


def test_monthly_summary_all_invalid_group_has_no_fake_rate():
    """그 달이 전부 결측이면 승률·평균 수익률은 숫자를 만들지 않는다(0%가 아니라 null)."""
    rows = _months([_t("KR", "2026-10-01", None, 11000, 100)])
    assert rows[0]["n"] == 0 and rows[0]["skipped"] == 1
    assert rows[0]["winRate"] is None and rows[0]["avgRet"] is None
    assert rows[0]["pnl"] == 0


def test_monthly_summary_normal_case_unchanged():
    """정상 레코드만 있을 때의 값은 기존과 동일해야 한다(회귀 방지)."""
    rows = _months([
        _t("KR", "2026-10-01", 10000, 11000, 100),     # +100,000, 승
        _t("KR", "2026-10-15", 5000, 4700, 100),       # −30,000, 패
    ])
    m = rows[0]
    assert (m["n"], m["wins"], m["winRate"], m["pnl"], m["skipped"]) == (2, 1, 50.0, 70000, 0)
    assert m["avgRet"] == pytest.approx(2.0, abs=0.01)   # (+10.00% − 6.00%)/2 = +2.00%


def test_both_aggregations_share_one_guard():
    """두 집계가 **같은 함수**를 쓰는지 — 사본이 생기면 어긋난다(사용자 지시)."""
    src = open(IDX, encoding="utf-8").read()
    assert src.count("function lpRealizedPnl(") == 1
    for fn in ("lpRealizedSummary", "lpMonthlySummary"):
        assert "lpRealizedPnl(r)" in _extract(fn), f"{fn}이 공용 가드를 안 쓴다"
    # 가드 조건식이 두 곳에 복사돼 있지 않은지
    assert src.count("!(buy > 0) || !(sell > 0) || !(qty > 0)") == 1


def test_monthly_summary_shows_skipped_in_ui():
    src = open(IDX, encoding="utf-8").read()
    i = src.index("const monthRows")
    body = src[i:i + 700]
    assert "m.skipped" in body and "값 누락" in body, "월간 요약 행에 제외 표시가 없다"

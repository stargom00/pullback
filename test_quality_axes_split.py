"""측정 B 분할 로직 — 타입별 정확성 + **항목 독립성**.

사전등록(docs/pullback_quality_axes.md)의 핵심 제약: 항목을 **각각 독립**으로 본다.
한 항목의 분할이 다른 항목의 분할에 영향을 주면 "항목별 독립 판정"이 성립하지 않는다.
"""
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "measurements"))

_spec = importlib.util.spec_from_file_location(
    "qaxes", ROOT / "scripts" / "measurements" / "2026-09-14_pullback_quality_axes.py")
qx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qx)


def hit(**kw):
    base = {"ticker": "T", "off": 60, "ret20": 0.0}
    for key, _kind, _g, _n in qx.ITEMS:
        base[key] = None
    base.update(kw)
    return base


def split_of(hits, key):
    return [h.get(f"_g_{key}") for h in hits]


# ── 타입별 분할 ───────────────────────────────────────────────────────
def test_continuous_top_and_bottom_30pct():
    hits = [hit(ticker=str(i), rs=80 + i) for i in range(10)]   # 80..89
    qx.assign_splits(hits, defaultdict(int))
    g = split_of(hits, "rs")
    assert g.count("top") == 3 and g.count("bot") == 3, g
    assert g[-1] == "top" and g[0] == "bot", "높은 값이 top이어야 한다"
    assert g.count(None) == 4, "중간 40%는 제외"


def test_continuous_skips_checkpoint_below_min_hits():
    hits = [hit(ticker=str(i), rs=80 + i) for i in range(4)]    # 5 미만
    st = defaultdict(int)
    qx.assign_splits(hits, st)
    assert all(h.get("_g_rs") is None for h in hits)
    assert st["cp_skip_rs"] == 1


def test_none_values_excluded_and_counted():
    st = defaultdict(int)
    hits = [hit(ticker=str(i), rs=(80 + i if i < 6 else None)) for i in range(8)]
    qx.assign_splits(hits, st)
    assert st["none_rs"] == 2
    assert all(h.get("_g_rs") is None for h in hits if h["rs"] is None)


def test_bool_split():
    hits = [hit(tt_pass=True), hit(tt_pass=False), hit(tt_pass=None)]
    qx.assign_splits(hits, defaultdict(int))
    assert split_of(hits, "tt_pass") == ["top", "bot", None]


def test_categorical_best_vs_worst_only():
    hits = [hit(grade="A"), hit(grade="B"), hit(grade="C"), hit(grade="D")]
    qx.assign_splits(hits, defaultdict(int))
    assert split_of(hits, "grade") == ["top", None, None, "bot"], "중간 등급은 제외"


def test_late_level_levels():
    hits = [hit(late_level="none"), hit(late_level="caution"), hit(late_level="danger")]
    qx.assign_splits(hits, defaultdict(int))
    # danger는 게이트에서 제외되므로 실제로는 안 나오지만, 나와도 어느 군도 아님
    assert split_of(hits, "late_level") == ["top", "bot", None]


def test_absolute_cuts_for_range10_and_vol_rel():
    hits = [hit(range10=5.0, vol_rel=50.0), hit(range10=9.0, vol_rel=90.0),
            hit(range10=15.0, vol_rel=80.0)]
    qx.assign_splits(hits, defaultdict(int))
    assert split_of(hits, "range10") == ["bot", None, "top"], "중간 7~12%는 제외"
    assert split_of(hits, "vol_rel") == ["bot", "top", "top"], "80%는 top 쪽(>=)"


def test_qa_uses_top20_and_rest_as_bottom():
    """(c) 축만 기존 정의 유지 — 상위 20% vs **나머지 전부**."""
    hits = [hit(ticker=str(i), qa_score=i * 10) for i in range(10)]
    qx.assign_splits(hits, defaultdict(int))
    g = split_of(hits, "qa_score")
    assert g.count("top") == 2 and g.count("bot") == 8 and g.count(None) == 0


# ── 독립성 (사용자 지시: 사보타주) ────────────────────────────────────
def _full_hits():
    return [hit(ticker=str(i), rs=80 + i, rsi=40 + (i * 7) % 10, atr_pct=1.0 + (i * 3) % 10,
                pullback_pct=float((i * 5) % 10), vol_ratio=float((i * 9) % 10),
                rs_mom=float((i * 2) % 10), rs_3m=float((i * 4) % 10),
                rs_delta=float((i * 6) % 10), ext200_pct=float((i * 8) % 10),
                qa_score=float(i), tt_pass=(i % 2 == 0), tightening=(i % 3 == 0),
                vol_dry=(i % 4 == 0), grade=("A" if i < 3 else "D"),
                late_level=("none" if i % 2 else "caution"),
                range10=float(i * 2), vol_rel=float(i * 12))
            for i in range(10)]


def test_each_item_split_depends_only_on_its_own_values():
    """**순서에 의존하지 않는 독립성 검사.**

    항목 k의 분할은 k의 값만으로 결정돼야 한다 → 다른 항목을 전부 None으로
    지워도 k의 분할이 같아야 한다. 어떤 항목이 다른 항목의 결과를 참조하면
    (계산 순서와 무관하게) 여기서 걸린다.

    앞서 "한 항목 값을 뒤집어 본다"로 짰더니 ITEMS 순서 때문에 사보타주를
    못 잡았다(rsi가 rs보다 먼저 처리돼 복사 대상이 아직 None이었음).
    """
    full = _full_hits()
    qx.assign_splits(full, defaultdict(int))
    expected = {k: split_of(full, k) for k, *_ in qx.ITEMS}

    for key, *_ in qx.ITEMS:
        isolated = _full_hits()
        for h in isolated:
            for other, *_ in qx.ITEMS:
                if other != key:
                    h[other] = None
        qx.assign_splits(isolated, defaultdict(int))
        assert split_of(isolated, key) == expected[key], (
            f"{key} 분할이 다른 항목 값에 의존한다")


def test_independence_check_has_detection_power():
    """위 검사가 실제로 결합을 잡는지 — 인위적 결합을 만들어 확인한다."""
    full = _full_hits()
    qx.assign_splits(full, defaultdict(int))
    # rsi 분할을 rs 분할로 강제 복사(=결합) 했다고 가정하고 같은 비교를 수행
    coupled = _full_hits()
    qx.assign_splits(coupled, defaultdict(int))
    for h in coupled:
        h["_g_rsi"] = h.get("_g_rs")
    isolated = _full_hits()
    for h in isolated:
        for other, *_ in qx.ITEMS:
            if other != "rsi":
                h[other] = None
    qx.assign_splits(isolated, defaultdict(int))
    for h in isolated:
        h["_g_rsi"] = h.get("_g_rs")          # 결합 재현: rs가 None이라 전부 None
    assert split_of(isolated, "rsi") != split_of(coupled, "rsi"), \
        "결합을 넣었는데 두 결과가 같다 — 독립성 검사가 무의미하다"


def test_removing_one_item_value_does_not_shift_others():
    """한 항목을 전부 None으로 만들어도 다른 항목 분할 불변."""
    def make():
        return [hit(ticker=str(i), rs=80 + i, rsi=40 + i, atr_pct=1.0 + i) for i in range(10)]

    a = make()
    qx.assign_splits(a, defaultdict(int))
    before = split_of(a, "rsi")

    b = make()
    for h in b:
        h["rs"] = None
    qx.assign_splits(b, defaultdict(int))
    assert split_of(b, "rsi") == before


# ── 보정 문턱 ─────────────────────────────────────────────────────────
def test_bonferroni_threshold_matches_prereg():
    assert qx.K == 17, f"항목 수가 사전등록(17)과 다르다: {qx.K}"
    assert qx.Z_BONF == 2.974, qx.Z_BONF
    assert qx.bonferroni_z(15) == 2.935


def test_item_list_has_no_duplicates_and_known_kinds():
    keys = [k for k, *_ in qx.ITEMS]
    assert len(keys) == len(set(keys)), "항목 중복"
    assert {kind for _k, kind, *_ in qx.ITEMS} <= {"cont", "bool", "cat", "abs", "qa"}


def test_judge_requires_all_checks(monkeypatch):
    """문턱 하나라도 미달이면 통과가 아니다."""
    hits = ([{"_g_rs": "top", "ret20": 10.0, "off": 60}] * 200
            + [{"_g_rs": "bot", "ret20": 0.0, "off": 60}] * 200)
    r = qx.judge("rs", "", "A", hits)
    # 전부 recent(off=60)라 older 반분이 비어 half 조건에서 걸려야 한다
    assert r["checks"]["half_n"] is False and r["passed"] is False


def test_every_item_actually_produces_both_groups():
    """모든 항목이 실제로 top/bot을 만들어내는가.

    독립성 비교만으로는 "그 항목이 조용히 전부 None이 되는" 결합을 못 잡는다
    (실제로 사보타주 하나가 이 구멍으로 빠져나갔다 — 계산 순서상 참조 대상이
    아직 None이라 full/isolated 양쪽 다 None이 되어 '같다'로 통과).
    항목이 측정 자체에서 누락되면 여기서 걸린다.
    """
    hits = _full_hits()
    qx.assign_splits(hits, defaultdict(int))
    empty = []
    for key, *_ in qx.ITEMS:
        g = split_of(hits, key)
        if g.count("top") == 0 or g.count("bot") == 0:
            empty.append((key, g.count("top"), g.count("bot")))
    assert not empty, f"군이 비어 측정되지 않는 항목: {empty}"


# ══════════════════════════════════════════════════════════════════════
# B-2 (2026-09-14) — B에서 3항목이 조용히 미측정된 것의 재발 방지
# ══════════════════════════════════════════════════════════════════════
_spec_b2 = importlib.util.spec_from_file_location(
    "qaxes_b2", ROOT / "scripts" / "measurements" / "2026-09-14_pullback_quality_axes_b2.py")
qb2 = importlib.util.module_from_spec(_spec_b2)
_spec_b2.loader.exec_module(qb2)


def _b2_hit(**kw):
    base = {"ticker": "T", "off": 60, "ret20": 0.0,
            "rs_3m": None, "rs_delta": None, "tt_pass": None}
    base.update(kw)
    return base


def test_b2_real_data_none_case_yields_empty_groups():
    """**실데이터 재현**: 필드가 전부 None이면 군이 비어야 한다.

    B에서 rs_3m/rs_delta는 analyze()에 안 넘겨서, tt_pass는 타입 오분류로
    전부 None이었다. 합성 데이터로 모든 필드를 채워 돌린 기존 테스트는
    이 상태를 재현하지 못했다.
    """
    hits = [_b2_hit(ticker=str(i)) for i in range(10)]
    qb2.assign_splits_b2(hits, defaultdict(int))
    for key, _ in qb2.ITEMS_B2:
        g = [h.get(f"_g_{key}") for h in hits]
        assert g.count("top") == 0 and g.count("bot") == 0, (key, g)


def test_b2_tt_pass_is_treated_as_integer_not_bool():
    """tt_pass는 0~8 정수다. bool로 다루면 전부 None이 된다(B의 실패)."""
    hits = [_b2_hit(ticker=str(i), tt_pass=i % 9) for i in range(18)]
    qb2.assign_splits_b2(hits, defaultdict(int))
    g = [h.get("_g_tt_pass") for h in hits]
    assert g.count("top") > 0 and g.count("bot") > 0, g


def test_b2_true_false_are_not_counted_as_numbers():
    """파이썬에서 bool은 int의 하위형 — 진짜 불리언이 섞이면 걸러야 한다."""
    hits = [_b2_hit(ticker=str(i), tt_pass=(i % 2 == 0)) for i in range(10)]
    st = defaultdict(int)
    qb2.assign_splits_b2(hits, st)
    assert st["none_tt_pass"] == 10
    assert all(h.get("_g_tt_pass") is None for h in hits)


def test_b2_injected_fields_are_measurable_when_present():
    hits = [_b2_hit(ticker=str(i), rs_3m=i * 3, rs_delta=i - 5, tt_pass=i % 9)
            for i in range(10)]
    qb2.assign_splits_b2(hits, defaultdict(int))
    for key, _ in qb2.ITEMS_B2:
        g = [h.get(f"_g_{key}") for h in hits]
        assert g.count("top") == 3 and g.count("bot") == 3, (key, g)


def test_b2_threshold_is_k17_not_k14():
    """사용자 지시: 문턱은 원래 설계 k=17 기준 2.974를 그대로 쓴다."""
    assert qb2.Z_BONF == 2.974, qb2.Z_BONF
    assert qb2.Z_BONF == qx.Z_BONF, "B와 B-2의 문턱이 갈렸다"


def test_b2_script_hard_fails_on_empty_group():
    """n=0을 만나면 **하드 실패**여야 한다 — B는 nan으로 조용히 넘어갔다."""
    src = (ROOT / "scripts" / "measurements"
           / "2026-09-14_pullback_quality_axes_b2.py").read_text(encoding="utf-8")
    assert "raise SystemExit" in src
    i = src.index('empty = [(r["item"]')
    assert 'r["n_top"] == 0 or r["n_bot"] == 0' in src[i:i + 300]


def test_b2_injects_into_analyze():
    """rs_3m/rs_delta를 analyze()에 실제로 넘기는지 — B의 누락 지점."""
    src = (ROOT / "scripts" / "measurements"
           / "2026-09-14_pullback_quality_axes_b2.py").read_text(encoding="utf-8")
    i = src.index("r = analyze(h, rs_rank=rs_m.get(t)")
    call = src[i:i + 300]                     # 호출 한 줄로 안 끝나므로 넉넉히
    assert "rs_3m=rs3_m.get(t)" in call, call
    assert "rs_delta=rs_delta_m.get(t)" in call, call


def test_harness_rank_by_return_matches_inline_definition():
    """harness 리팩터가 기존 rank3 정의를 바꾸지 않았는가."""
    import numpy as np
    import pandas as pd
    import harness
    from scanner import to_rs_rank
    rng = np.random.default_rng(11)
    cache = {(f"{i:06d}.KS" if i % 2 else f"T{i}"):
             pd.DataFrame({"Close": np.cumsum(rng.normal(0, 1, 300)) + 200},
                          index=pd.bdate_range("2024-01-01", periods=300))
             for i in range(30)}
    kr, us = {}, {}
    for t, h in cache.items():
        r = harness.ret_pct(h["Close"], 63)
        if r is None:
            continue
        (kr if harness.is_kr_ticker(t) else us)[t] = r
    assert harness.rank_by_return(cache, 63) == {**to_rs_rank(kr), **to_rs_rank(us)}

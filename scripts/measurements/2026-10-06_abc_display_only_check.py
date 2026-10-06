"""v5.331 ABC 표시 전용 변경 검증 — 수정 전(base 커밋)·수정 후 abc_screener로 같은 KR 일봉을 판정해
판정 필드와 등급이 **종목별로 완전히 같은지** 본다(사용자 지시 "기존 KR 전체 ABC 등급 분포 전후 완전 일치").

데이터: 저장해 둔 KR 일봉 pickle({"data": {ticker: df}, "names": {...}}) — 운영 번들과 같은 경로(naver_kr.fetch 1900일 →
app._downcast). 실적은 조회하지 않는다 — 등급은 (판정 결과, 기업축)의 함수라, 기업축 세 경우(통과·미달·실적 없음)를
고정해 넣고 두 모듈의 grade()를 비교한다(실적은 두 모듈에 똑같이 들어가는 입력이므로 이것으로 충분하다).

실행: python3 scripts/measurements/2026-10-06_abc_display_only_check.py <data.pkl> [base_commit]
"""
import importlib.util
import json
import os
import pickle
import subprocess
import sys
import tempfile
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts", "measurements")]
import abc_screener as new  # noqa: E402
import harness  # noqa: E402


def load_old(commit):
    src = subprocess.run(["git", "show", f"{commit}:abc_screener.py"], capture_output=True, text=True, cwd=ROOT, check=True).stdout
    path = os.path.join(tempfile.mkdtemp(), "abc_old.py")
    open(path, "w", encoding="utf-8").write(src)
    spec = importlib.util.spec_from_file_location("abc_old", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


JUDGE_KEYS = ("verdict", "reason", "c_stage", "gate_pct", "stage_pct", "close", "ma_gate", "ma_stage", "ma_inverted",
              "b_turnover_eok", "a", "b", "gate_break")
FIN = {"pass": (4, 4, 2), "fail": (1, 4, 0), "unknown": (None, 0, None)}   # (rev_yoy_pos, rev_yoy_of, eps_pos_q)


def main():
    data = pickle.load(open(sys.argv[1], "rb"))["data"]
    old = load_old(sys.argv[2] if len(sys.argv) > 2 else "HEAD")
    diffs, grades_old, grades_new, labels = [], Counter(), Counter(), Counter()
    br = Counter()
    for t, df in data.items():
        ro, rn = old.analyze_abc(df), new.analyze_abc(df)
        for k in JUDGE_KEYS:
            vo, vn = ro.get(k), rn.get(k)
            if k == "breakout":
                continue
            if vo != vn:
                diffs.append((t, k, vo, vn))
        bo, bn = (ro.get("breakout") or {}), (rn.get("breakout") or {})
        if {k: bo.get(k) for k in ("bars_ago", "vol_bar_ago", "vol_mult")} != {k: bn.get(k) for k in ("bars_ago", "vol_bar_ago", "vol_mult")}:
            diffs.append((t, "breakout", bo, bn))
        for case, (rp, rof, eps) in FIN.items():
            co = old.company_axis(ro["b_turnover_eok"], rp, eps, False, rev_yoy_of=rof)
            cn = new.company_axis(rn["b_turnover_eok"], rp, eps, False, rev_yoy_of=rof)
            go, gn = old.grade(ro, co), new.grade(rn, cn)
            grades_old[(case, go)] += 1
            grades_new[(case, gn)] += 1
            if go != gn:
                diffs.append((t, f"grade[{case}]", go, gn))
        if rn["verdict"] == "ABC":
            labels[rn["b_quality"]["label"]] += 1
            br[(rn.get("breakout") or {}).get("label")] += 1
    out = {"run_stamp": harness.run_stamp(), "n": len(data), "diffs": len(diffs), "diff_samples": diffs[:20],
           "grades_old": {f"{c}:{g}": n for (c, g), n in sorted(grades_old.items(), key=str)},
           "grades_new": {f"{c}:{g}": n for (c, g), n in sorted(grades_new.items(), key=str)},
           "b_quality_labels": dict(labels), "breakout_labels": {str(k): v for k, v in br.items()}}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    if diffs:
        sys.exit(1)   # 표시 전용이 아니다 — 실패로 끝낸다(CLAUDE.md "발견한 문제는 실패로")


if __name__ == "__main__":
    main()

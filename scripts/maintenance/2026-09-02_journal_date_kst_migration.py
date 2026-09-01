"""
저널 date/watch_start_date UTC→KST 마이그레이션 (2026-09-02, 사용자 지시).

배경: `app.py` `/api/watch/quick`(⚡감시 원클릭 등록 — 스캐너 카드/재점화/
대장전환/"오늘의 결정" 전부 공유)가 v5.146 이전까지
`datetime.now()`를 타임존 없이 호출해, Railway 서버(UTC)의 날짜를 그대로
`date`/`watch_start_date`에 저장했다(app.py:9895, v5.146에서
`datetime.now(KST)`로 수정 완료 — 이 스크립트는 그 수정 *이전에* 이미
저장된 기존 레코드를 바로잡는 별도 작업). KST 00:00~08:59 사이에
⚡감시로 등록된 레코드는 날짜가 하루 밀려 있다.

**복구 원리**: 레코드의 `id`(생성 시각 epoch ms — 클라이언트
`Date.now()`/서버 `int(time.time()*1000)`, 둘 다 이 버그의 영향을 받지
않는 순수 타임스탬프)로 정확한 KST 날짜를 역산해서 `date`/
`watch_start_date`를 덮어쓴다.

**2026-09-02 실사고로 발견한 치명적 결함과 수정**: 최초 버전은 `id`
기반 재계산값이 저장된 `date`와 다르기만 하면 전부 "버그"로 간주해
덮어썼다 — 실제 dry-run 결과 35건 중 27건이 며칠~18일씩 차이 나는
비정상 값이었다. 원인: `id`=레코드 **생성** 시각, `date`=**거래 발생일**
— 과거 거래를 나중에 수동 입력했거나 날짜를 수정한 레코드는 둘이
원래 다르다(예: RCUS는 08-25 생성, 실제 거래일 08-07 — 18일 차이).
**UTC→KST 버그는 항상 정확히 "+1일"(UTC가 KST보다 9시간 느려 자정
넘김을 못 따라가는 구조라 방향과 크기가 고정) 방향으로만 발생**하므로,
`새 날짜 == 기존 날짜 + 정확히 1일`인 경우만 이 버그로 간주하고
교정한다. 그 외(뒤로 이동·2일 이상 차이 등)는 수동입력/날짜수정 등
**다른 이유로 원래 다른 값**이라고 판단해 절대 안 건드리고 별도
목록으로만 보고한다. 표시 레이어만 바꾸는 방식(display-only 변환)은
주간/월간 R 통계·활동 캘린더처럼 `r.date`를 직접 읽는 다른 로직까지
못 고치므로, 저장값 자체를 정확히 복구하는 이 방식을 선택했다.

**안전장치(사용자 지시)**:
  a) 기본값은 **dry-run** — 실제 쓰기는 `--apply` 플래그가 있을 때만.
  b) dry-run 결과로 몇 건이 바뀌는지, 어떤 레코드가 어떻게 바뀌는지 출력.
  c) `--apply` 실행 시 저널 파일을 타임스탬프 붙여 먼저 백업.
  d) `id`가 없거나 앱 운영기간(2026-07-01 이후, 현재 이전) 밖으로 계산되는
     레코드는 건드리지 않고 경고만 — 조용히 틀린 값을 덮어쓰지 않는다.
  e) **(2026-09-02 추가) 교정 대상은 "새 날짜 = 기존 날짜 + 정확히 1일"
     인 경우로만 한정.** 그 외는 전부 스킵 + 별도 목록 보고(수동입력/
     날짜수정 의심 레코드로 분류, 절대 덮어쓰지 않음).
  f) **(2026-09-02 추가) `--apply` 직전 최종 방어선**: 실제로 쓰기
     직전에 `changes` 목록 전원을 다시 검사해 delta가 정확히 +1일이
     아닌 항목이 하나라도 있으면 — 필터링 로직 자체에 새 버그가 생긴
     경우를 대비해 — 파일을 전혀 건드리지 않고 즉시 중단(백업조차
     안 함, 아무 부작용 없이 에러 종료).

`app.py`를 그대로 import해서 `JOURNAL_PATH`/`KST`/`load_journal()`을
재사용한다(경로 해석 로직을 새로 구현하지 않음 — README 규칙3과 같은
원칙). Railway 환경(`/data` 마운트)에서 실행해야 실제 운영 데이터를
대상으로 한다 — 로컬에서 돌리면 로컬 폴백 경로(`journal_user.json`,
보통 존재하지 않음)를 보게 된다.

사용:
  python3 scripts/maintenance/2026-09-02_journal_date_kst_migration.py            # dry-run(기본)
  python3 scripts/maintenance/2026-09-02_journal_date_kst_migration.py --apply    # 실제 반영(백업 후)

**이 스크립트는 아직 실행되지 않았다** — dry-run 결과를 사용자에게
보고한 뒤 --apply 여부를 지시받는다.
"""
import sys
import os
import json
import shutil
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import app  # JOURNAL_PATH/KST/load_journal 재사용 — 경로 해석 재구현 안 함

KST = app.KST
APP_LAUNCH_FLOOR = datetime(2026, 7, 1, tzinfo=KST)   # 사용자 지시(d): 운영기간 하한
FIELDS_TO_FIX = ("date", "watch_start_date")
EXPECTED_DELTA_DAYS = 1   # UTC→KST 버그는 항상 정확히 +1일(사용자 지시 e)


def _parse_ymd(s: str):
    """YYYY-MM-DD 파싱, 실패 시 None(값 자체가 손상된 레코드 방어)."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def correct_kst_date(record_id, now: datetime) -> str | None:
    """id(epoch ms)로 정확한 KST 날짜 역산. id가 없거나, 숫자가 아니거나,
    운영기간(2026-07-01~현재) 밖으로 계산되면 None(건드리지 않음 신호)."""
    if not isinstance(record_id, (int, float)) or record_id <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(record_id / 1000, tz=KST)
    except (OverflowError, OSError, ValueError):
        return None
    if dt < APP_LAUNCH_FLOOR or dt > now:
        return None
    return dt.strftime("%Y-%m-%d")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="실제로 저널 파일을 덮어쓴다. 기본은 dry-run(변경 없음).")
    args = parser.parse_args()

    now = datetime.now(KST)
    journal = app.load_journal()
    print(f"[migration] 대상 파일: {app.JOURNAL_PATH}")
    print(f"[migration] 저널 레코드 총 {len(journal)}건, 기준 시각(KST): {now.strftime('%Y-%m-%d %H:%M:%S')}")

    changes = []              # (index, ticker, field, old, new, delta_days) — 전부 delta==+1
    skipped_no_id = []        # ticker
    skipped_out_of_range = [] # (ticker, id)
    skipped_wrong_delta = []  # (index, ticker, field, old, new, delta_days) — 수동입력/날짜수정 의심

    for i, rec in enumerate(journal):
        rid = rec.get("id")
        correct = correct_kst_date(rid, now)
        if correct is None:
            if rid is None:
                skipped_no_id.append(rec.get("ticker", "?"))
            else:
                skipped_out_of_range.append((rec.get("ticker", "?"), rid))
            continue
        for field in FIELDS_TO_FIX:
            old = rec.get(field)
            if not old or old == correct:
                continue
            old_d, new_d = _parse_ymd(old), _parse_ymd(correct)
            if old_d is None or new_d is None:
                skipped_wrong_delta.append((i, rec.get("ticker", "?"), field, old, correct, None))
                continue
            delta = (new_d - old_d).days
            if delta == EXPECTED_DELTA_DAYS:
                changes.append((i, rec.get("ticker", "?"), field, old, correct, delta))
            else:
                # 사용자 지시(2026-09-02): +1일이 아니면 UTC 버그가 아니라
                # 수동입력/날짜수정 등 다른 이유로 원래 다른 값 — 안 건드림.
                skipped_wrong_delta.append((i, rec.get("ticker", "?"), field, old, correct, delta))

    print(f"\n[migration] 교정 대상(+1일 정확히 일치): {len(changes)}건")
    for i, ticker, field, old, new, delta in changes:
        print(f"  [idx {i}] {ticker} · {field}: {old} → {new} (+{delta}일)")

    print(f"\n[migration] +1일이 아니라서 스킵(수동입력/날짜수정 의심 — 안 건드림): "
          f"{len(skipped_wrong_delta)}건")
    for i, ticker, field, old, new, delta in skipped_wrong_delta:
        delta_str = f"{delta:+d}일" if delta is not None else "파싱불가"
        print(f"  [idx {i}] {ticker} · {field}: {old} → (id기준 {new}, {delta_str}) — 스킵")

    print(f"\n[migration] id 없어서 스킵: {len(skipped_no_id)}건", skipped_no_id[:20],
          "..." if len(skipped_no_id) > 20 else "")
    print(f"[migration] id는 있으나 운영기간(2026-07-01~현재) 밖이라 스킵: "
          f"{len(skipped_out_of_range)}건", skipped_out_of_range[:20],
          "..." if len(skipped_out_of_range) > 20 else "")

    if not args.apply:
        print("\n[migration] === dry-run 모드 — 파일을 변경하지 않았습니다. ===")
        print("[migration] 반영하려면 --apply를 붙여 다시 실행하세요.")
        return

    if not changes:
        print("\n[migration] 변경할 게 없어 --apply라도 아무 파일도 안 건드립니다.")
        return

    # 사용자 지시(f): 실제 쓰기 직전 최종 방어선 — 필터링 로직에 새 버그가
    # 생겼을 경우를 대비해, changes 전원이 정말 +1일인지 다시 검증한다.
    # 하나라도 어긋나면 백업조차 하지 않고 즉시 중단.
    bad = [c for c in changes if c[5] != EXPECTED_DELTA_DAYS]
    if bad:
        print(f"\n[migration] !!! 안전장치 발동: 교정 목록에 +1일이 아닌 항목 "
              f"{len(bad)}건 발견 — 필터링 로직 버그 의심. 파일을 전혀 건드리지 "
              f"않고 중단합니다.")
        for i, ticker, field, old, new, delta in bad:
            print(f"    [idx {i}] {ticker} · {field}: {old} → {new} ({delta:+d}일)")
        sys.exit(1)

    backup_path = f"{app.JOURNAL_PATH}.bak.{now.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(app.JOURNAL_PATH, backup_path)
    print(f"\n[migration] 백업 완료: {backup_path}")

    for i, ticker, field, old, new, delta in changes:
        journal[i][field] = new

    tmp_path = app.JOURNAL_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=1)
    os.replace(tmp_path, app.JOURNAL_PATH)
    print(f"[migration] {len(changes)}건 반영 완료: {app.JOURNAL_PATH}")


if __name__ == "__main__":
    main()

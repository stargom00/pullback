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
`watch_start_date`를 덮어쓴다. 표시 레이어만 바꾸는 방식(display-only
변환)은 주간/월간 R 통계·활동 캘린더처럼 `r.date`를 직접 읽는 다른
로직까지 못 고치므로, 저장값 자체를 정확히 복구하는 이 방식을 선택했다
(id가 있어서 근사가 아니라 정확한 역산이 가능 — 사용자 승인 경위 참고).

**안전장치(사용자 지시)**:
  a) 기본값은 **dry-run** — 실제 쓰기는 `--apply` 플래그가 있을 때만.
  b) dry-run 결과로 몇 건이 바뀌는지, 어떤 레코드가 어떻게 바뀌는지 출력.
  c) `--apply` 실행 시 저널 파일을 타임스탬프 붙여 먼저 백업.
  d) `id`가 없거나 앱 운영기간(2026-07-01 이후, 현재 이전) 밖으로 계산되는
     레코드는 건드리지 않고 경고만 — 조용히 틀린 값을 덮어쓰지 않는다.

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

    changes = []              # (index, ticker, field, old, new)
    skipped_no_id = []        # ticker
    skipped_out_of_range = [] # (ticker, id)

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
            if old and old != correct:
                changes.append((i, rec.get("ticker", "?"), field, old, correct))

    print(f"\n[migration] 변경 대상: {len(changes)}건")
    for i, ticker, field, old, new in changes:
        print(f"  [idx {i}] {ticker} · {field}: {old} → {new}")

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

    backup_path = f"{app.JOURNAL_PATH}.bak.{now.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(app.JOURNAL_PATH, backup_path)
    print(f"\n[migration] 백업 완료: {backup_path}")

    for i, ticker, field, old, new in changes:
        journal[i][field] = new

    tmp_path = app.JOURNAL_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=1)
    os.replace(tmp_path, app.JOURNAL_PATH)
    print(f"[migration] {len(changes)}건 반영 완료: {app.JOURNAL_PATH}")


if __name__ == "__main__":
    main()

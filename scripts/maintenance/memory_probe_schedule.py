#!/usr/bin/env python3
"""예정된 메모리 측정을 **밀린 것까지** 채우고, 끝나면 스스로 정리한다.

launchd로 옮긴 이유(사용자 지시): cron은 절전 중 지나간 실행을 **건너뛰고
보충하지 않는다**. launchd의 StartCalendarInterval은 깨어난 뒤 실행해준다.

다만 launchd도 절전 중 밀린 여러 실행을 **한 번으로 합쳐서** 깨운다. 두 시각이
모두 절전에 걸리면 한 번만 불린다는 뜻이라, "지금이 몇 시인가"로 라벨을 고르면
한쪽이 영영 안 찍힌다. 그래서 시각이 아니라 **기록을 기준으로** 판단한다:
예정 시각이 지났는데 JSONL에 그 라벨이 없으면 지금 찍는다.

두 라벨이 모두 기록되면 plist를 삭제하고 서비스를 내린다
(사용자 지시: "09-22 실행 후 plist 자동 제거"). **삭제가 bootout보다 먼저**여야
한다 — bootout은 이 스크립트를 돌리는 job 자신을 죽이기 때문에, 순서를 바꾸면
서비스만 내려가고 파일이 남는다(리허설에서 실제로 겪었다).

등록(이 저장소의 `.plist.sample` 참고):
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
확인:
    launchctl print gui/$(id -u)/com.seulkicho.pullback.memoryprobe
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "memory_probe.py"
JSONL = HERE / "memory_probe.jsonl"
LABEL = "com.seulkicho.pullback.memoryprobe"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

# (라벨, 예정 시각) — **로컬 시계(NZST)** 기준. KST = 로컬 − 3시간.
SCHEDULE = [
    ("KR-EOD-통과", "2026-09-21 23:15"),   # KST 09-21 20:15 — KR EOD 1회 통과
    ("US-EOD-통과", "2026-09-22 10:00"),   # KST 09-22 07:00 — US EOD까지 통과
]


def _recorded() -> set:
    """이미 찍힌 라벨. 파일이 없거나 깨진 줄이 있어도 나머지는 살린다."""
    out = set()
    if not JSONL.exists():
        return out
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not d.get("error"):
            out.add(d.get("label"))
    return out


def _cleanup():
    """예정된 측정이 다 끝났다 — plist를 내리고 지운다."""
    print(f"[schedule] 두 측정 모두 완료 — {PLIST.name} 제거", flush=True)
    # **파일을 먼저 지운다.** bootout은 이 스크립트를 실행 중인 job 자신을
    # 종료시키므로, 먼저 호출하면 unlink에 도달하지 못한다 — 리허설에서
    # 실제로 그래서 plist가 남았다(서비스만 내려가고 파일은 그대로).
    try:
        PLIST.unlink()
        print("[schedule] plist 파일 삭제됨", flush=True)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[schedule] plist 삭제 실패(수동으로 지울 것): {e}", flush=True)
    # 그 다음에 서비스를 내린다. 여기서 이 프로세스가 죽어도 목적은 달성됐다.
    try:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                       capture_output=True, timeout=20)
    except Exception:
        pass


def main() -> int:
    now = datetime.now()
    done = _recorded()
    ran = 0
    for label, when in SCHEDULE:
        if label in done:
            continue
        due = datetime.strptime(when, "%Y-%m-%d %H:%M")
        if now < due:
            continue                     # 아직 시각 전 — 건너뛴다
        late = (now - due).total_seconds() / 60
        print(f"[schedule] {label} 실행 (예정 {when}, {late:.0f}분 경과)", flush=True)
        subprocess.run([sys.executable, str(PROBE), label], timeout=180)
        ran += 1
    done = _recorded()
    if all(lbl in done for lbl, _ in SCHEDULE):
        _cleanup()
    elif ran == 0:
        print(f"[schedule] 실행할 것 없음 (완료 {sorted(done)})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

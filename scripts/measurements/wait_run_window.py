"""측정 실행 창이 열릴 때까지 기다렸다가 종료(코드 0). 창 판정은 harness.run_window_ok 한 곳(사본 금지).

사용: python3 scripts/measurements/wait_run_window.py kr  && <측정 명령>
      시장 인자: kr / us / kr,us. 5분마다 확인하고, 기다리는 동안 남은 사유를 한 줄씩 출력한다.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import harness  # noqa: E402

markets = tuple((sys.argv[1] if len(sys.argv) > 1 else "kr").split(","))
while True:
    ok, why = harness.run_window_ok(markets)
    print(f"[wait] {time.strftime('%H:%M:%S')} {'열림' if ok else '대기'} — {why}", flush=True)
    if ok:
        sys.exit(0)
    time.sleep(300)

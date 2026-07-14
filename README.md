# 눌림목 스캐너 v4.57 — 시장 게이트 근본 수정

## 적용 파일
- `scanner.py` — 함수 교체/추가
- `app.py` — 함수 교체 + 엔드포인트 교체

## 1. scanner.py

`scanner_gate.py`의 내용으로 아래를 처리:

| 함수 | 작업 |
|---|---|
| `ftd_state(close, vol)` | **통째로 교체** |
| `dist_count(close, vol, ftd, ...)` | **신규 추가** |
| `gate_suggest(dist, ftd, above_ma60)` | **통째로 교체** (⚠️ 시그니처 변경: `dist_days: int` → `dist: dict`) |

세 함수는 인접 배치 권장. `import pandas as pd`는 이미 있음.

## 2. app.py

`app_gate.py` 참조:

| 대상 | 작업 |
|---|---|
| `INDEX_SPEC`, `_volume_valid()`, `_fetch_proxy_volume()` | **신규 추가** (`_index_regime` 위) |
| `_index_regime(code)` | **통째로 교체** |
| `_GATE_RANK`, `_GATE_R`, `_worst_gate()` | **신규 추가** |
| `@app.get("/api/market/gate")` | **통째로 교체** |
| `_indices_impl()`의 `gather` 블록 | S&P500 추가 (app_gate.py 하단 3번 주석 참조) |

## 3. 버전 문자열
```python
VERSION = "v4.57"
```

## 검증
```bash
python test_gate.py    # 6/6
python test_gate2.py   # 3/3
```
`test_gate2.py`는 `test_gate.py`를 import함 (기존 로직 재현용) — 같은 폴더에 둘 것.

## 수정한 버그
1. `gate_suggest`의 `dist_days >= 6` early return이 FTD 분기를 가로막아 **죽은 코드**로 만듦
2. 분산일이 순수 25일 롤링 — FTD 리셋도 5% 만료도 없어 **늘어나기만 하고 안 빠짐**
3. `in_correction`이 FTD 후에도 안 풀려 rally_day가 무한 증가
4. (테스트 중 발견) FTD 없이 회복한 시장이 영원히 조정 모드에 갇힘

## 데이터 한계 (선언)
- `^GSPC` Volume 미확인 → SPY 폴백. 실패 시 `dist_days: None` (0으로 위장 안 함)
- ETF 거래량은 지수 거래량의 **프록시**. 오닐 원본(NYSE 전체 거래량)과 다름
- KR 분산일 임계(-0.2%)는 미국장 기준값 그대로. **검증 안 됨** — 이번 수정 범위 밖

## 노출 %를 만들지 않은 이유
오닐/IBD는 노출을 %로 정의하지 않음. 기존 R 설정(`max_open_r`)에
🟢3R / 🟡1.5R / 🔴0 이 이미 있고, 그게 유일하게 근거 있는 규칙.
근거 없는 숫자를 새로 만들면 정밀해 보이지만 정보는 하나도 안 늘어남.

## 배포 후 확인
`/api/market/gate` 열어서:
- 나스닥 `dist_days`가 9 → 몇으로 떨어지는지
- `^GSPC`의 `vol_source`가 `index`인지 `SPY`인지 (Volume 유무 확인)
- `dist_raw` vs `dist_days` 차이 = 제거 규칙이 실제로 몇 개 걷어냈는지

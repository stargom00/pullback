"""
눌림목 스캐너 — 웹 서버
모드: pullback(눌림목) / turnaround(추세전환) / leader / super / breakout / surge
RS 모멘텀: 3개월 수익률 백분위 - 12개월 수익률 백분위 (시장별)
실행: uvicorn app:app --host 0.0.0.0 --port 8000

[변경 이력]
v5.127 [비용개선/문서정정] 사용자 지시 2건 — ① theme_map 일일 생성 한도를
        자동/수동 독립 카운트로 분리(v5.126에서 3건 공유 한도가 자동
        트리거 우선 소진 시 수동 생성을 막던 문제 수정). entry에
        "trigger":"auto"|"manual" 필드 추가, `theme_map.AUTO_DAILY_LIMIT=6`
        (money_flow 자동 트리거) / `MANUAL_DAILY_LIMIT=4`(POST 수동)로
        분리, `today_generation_count(trigger=...)`로 선택 카운트. 에러
        메시지도 "자동 생성 한도"/"수동 생성 한도"로 구분 표시. ②
        CLAUDE.md에 원칙 추가: "외부 유료 API 호출 엔드포인트는 쿨다운·
        진행중 잠금·일일 한도를 함께 구현할 것"(v5.126 비용 급증 사건
        참조). ③ 6배 표본 재검증(2026-08-31) 결과 반영 — "KR=돌파 계열
        우위"(z=4.88, 2026-08-29 사전 등록 채택) 결론이 90개 체크포인트
        재검증에서 재현 안 됨(z=1.38)이 확인돼 GUIDE.md 1페이지
        체크리스트·시장별 전략 지도 섹션의 관련 서술을 전부 정정/철회
        (US 쪽은 반대로 유의해짐, z=-0.16→-2.97 — 되돌림 계열 우위
        오히려 강화). `scripts/measurements/README.md`에 규칙9 신설
        ("채택 판정은 체크포인트 90개 이상으로 할 것, 20개 창은 예비
        관찰로만") + `docs/kr_us_strategy_map.md`에 규칙9 기준 재검증
        대기 목록(depth_atr 게이트·RS게이트E·다중히트 보너스·RSI<50 —
        전부 20개 창 채택, depth_atr/게이트E는 KR+US 혼합 채택 후 규칙8
        분해에서 양쪽 다 비유의였던 이력까지 있어 우선순위 높음) 작성.
v5.126 [비용개선] Anthropic API 비용 급증(8/26~8/31 6일 $48, 예상 20배)
        조사 후 절감 조치(사용자 지시) — 원인: money_flow_report.py
        (8/26 신설)·theme_map.py(8/29 신설)·macro_calendar.py(8/30 프롬프트
        확장) 세 기능 모두 이번 비용 급증 구간에 활발히 개발/테스트되던
        중이었고, 그중 `/api/moneyflow/{market}/run`(🔄 재실행 버튼)이
        **아무 제한도 없이** 클릭마다 Claude+웹서치(최대 5회) 풀프라이스
        호출을 그대로 내보내는 걸 확인 — 가장 유력한 단일 원인으로 판단
        (같은 날 버그수정 커밋 3건이 있었던 걸 보면 반복 재실행 테스트
        정황). 조치 4건: ① `/api/moneyflow/{market}/run`에 시장별 2분
        쿨다운+진행중 잠금 신설(기존엔 전혀 없었음). ②
        `/api/calendar/macro/run`에도 짧은(2분) 쿨다운 추가(기존
        `_macro_calendar_task_running` 동시실행 잠금은 있었으나 순차
        재클릭은 못 막았음, 24시간 재시도 스로틀은 실패 후 자동재시도용이라
        별개). ③ `/api/theme_map/{theme}`에 테마별 진행중 잠금 추가(완료
        전 같은 테마 중복 POST 방지 — 기존엔 일일 생성 한도 카운트가 완료
        후에만 올라가서 그 사이 중복 호출이 안 막혔음). ④ 세 모듈 다
        prompt caching 적용(`system` + `cache_control: ephemeral`) —
        고정 프롬프트(테마생성/매크로캘린더/돈의흐름 해석 지침, 각
        ~600~2000토큰 추정)를 반복 호출 시 캐시 히트로 입력비 최대 90%
        절감 가능(대시보드에 "Not enabled"로 떠 있던 것 해결). ⑤
        money_flow_report.py 입력 다이어트 — 스냅샷 JSON을 `indent=2`
        pretty-print에서 컴팩트(`separators=(",",":")`) 인코딩으로
        전환(순수 공백 제거, 로컬 시뮬레이션 실측 -32%) + 종목당 중복/
        과잉정밀도 필드 제거(volume은 turnover에 이미 반영, prev_rank는
        rank_change로 대체 가능 — 정보 손실 없음) + turnover/mcap_approx
        정수 반올림(합산 실측 -40%). [범위 밖] 웹서치 max_uses 상한은
        이미 3개 모듈 다 설정돼 있었음(theme_map=5, money_flow_report=5,
        macro_calendar=8) — 신설 아님, 하향 조정도 안 함(품질 저하 우려,
        진짜 원인은 호출 빈도였다는 게 이번 조사 결론). 서버사이드
        web_search 툴은 한 API 호출 안에서도 검색 왕복마다 누적 컨텍스트를
        다시 토크나이즈해 과금하므로(멀티턴 내부 루프), 338회 검색 자체가
        입력토큰 급증의 직접 원인이라기보다 "호출이 몇 번이나 됐는지"가
        진짜 변수라고 판단 — 정확한 호출 횟수는 Anthropic 대시보드(코드/
        로그로는 재구성 불가) 확인 필요.
v5.125 [기능추가] 🔁 전 리더 재점화 워치리스트(사용자 지시) —
        docs/kr_theme_leader_reignition.md 채택 결과(D0 리더 재점화율
        51.8% vs 대조군 39~42%, 확인진입 EV+0.755R)를 실시간 감시로
        전환. 신규 모듈 theme_reignition.py(app.py 미의존, theme_lifecycle.py
        와 같은 원칙) — find_watch_candidates()가 테마별 D0 사이클에서
        지금 D0+30~D0+180거래일 창 안의 리더를 찾고, check_confirm()이
        표준 돌파(최근 20일 고가+거래량 1.5배)로 확인진입 여부를 판정.
        [의도적으로 백테스트와 다른 점] 백테스트의 재점화 판정(+15%/+25%
        급등 사후 탐지)은 실시간 알림에 못 씀(급등이 끝난 뒤에야 판정) —
        표준 돌파 정의로 대체, 응축 여부는 배지로만 병기(게이트 아님 —
        응축 선행이 43.7%로 과반 미달). [저장/스케줄] reignition_watch.json
        (jongga_forward.json과 같은 패턴)에 상태(watching/confirmed/expired)
        저장, _warm_market()의 KR 장마감 후 분기에서 하루 1회
        _refresh_reignition_watch() 호출(부분봉 오염 우려로 장중 미실행).
        노이즈 가드: 동시 20개+ 창내 리더 시 RS 상위만 확인체크 대상(나머지는
        표시만, alert_suppressed). 확인진입 후 포워드 R은 목표 사전정의 없이
        손절이탈/60봉상한 시 시가평가로 확정(harness.race와는 다른 단순
        mark-to-market — 방향성 엣지 대조용). [엔드포인트] GET
        /api/reignition/watchlist(UI), /api/reignition/confirmed(얼마냐봇
        폴링용 — _BOT_READ_EXACT_PATHS 추가, 실제 텔레그램 발송은 stock-alert
        레포 쪽 구현 필요), /api/reignition/forward(포워드 통계). [UI]
        static/index.html 대장관찰 탭 하단에 "🔁 재점화 대기" 섹션(종가베팅
        포워드 성적 박스와 같은 패턴). GUIDE.md 3장에 "KR 사전포착 두 경로"
        (돌파임박=고점근처 응축용 vs 재점화감시=붕괴 후 전리더용) 표 추가.
v5.124 [기능개선] theme_map 후속 3건(사용자 지시) — ① 광의 테마(제약·바이오
        등 유니버스 후보 50개+) 상한을 8→25로 확대(theme_map.py 프롬프트
        규칙2). 하위테마 분할 대신 상한확대를 택함 — 이유: DAILY_GENERATION
        _LIMIT=3/day 예산상 광의 테마 하나를 4개로 쪼개면 그날 예산을 다
        써버림, 이번 계기인 JW신약 3중히트도 섹터 전체 단위 확산판정이
        목적이었지 하위테마 분리가 필요한 사례가 아니었음. 좁은 테마는
        Claude가 실재 후보만 반환(환각금지 규칙1)하므로 "50개+" 사전판정
        로직 불필요. MAX_TOKENS 4000→6000 동반 상향. ② "정적 rank=시총순"
        오해 정정 — 실제로는 theme_map.py 생성 프롬프트 기준 사업직결도
        순위(시총 아님). 프롬프트 규칙3에 명시 추가 + static/index.html
        테마 라이프사이클 서브뷰에 정적/거래대금/회전율 3종 서열을 나란히
        표시하는 표+툴팁 신설(2fe72b8가 API에만 노출하고 UI는 동시편집
        충돌로 미완이었던 부분 완료) — 회전율 열에 ★판정기준 표시. ③
        제약·바이오 재생성은 이 환경에 ANTHROPIC_API_KEY가 없어 직접
        실행 불가 — 사용자가 실제 키로 POST 재실행 후 GET /api/theme_lifecycle
        /제약·바이오 재호출하면 새 매핑이 코드 변경 없이 그대로 반영됨
        (analyze_theme은 theme_map.json을 매 호출 시 다시 읽음).
v5.123 [버그수정] theme_map API 두 가지 수정(사용자 지시, 실사용자 재현
        버그) — ① GET /api/theme_map, GET /api/theme_map/{theme}가
        API_READ_TOKEN 허용목록에 없어서 POST(생성)는 되는데 GET(조회)은
        세션 로그인 없이 401로 막혀 있었음 — 로컬 재현으로 확인(POST는
        정상 토큰이면 실제로 인증 통과함, 사용자가 겪은 POST 401은 토큰값
        불일치 등 코드 밖 원인으로 추정 — CLAUDE.md 참고). _TOKEN_READABLE_
        EXACT_PATHS/_TOKEN_READABLE_PATH_PREFIXES 신설, GET 두 경로 추가.
        ② POST /api/theme_map/{theme}가 Claude 생성 완료까지 동기 대기해서
        Railway 프록시 타임아웃(upstream error)에 걸림 — 비동기 job으로
        전환: 즉시 202+job_id, 백그라운드 생성(asyncio.create_task, 기존
        _run_money_flow_bg류와 같은 패턴), GET /api/theme_map/jobs/{job_id}
        로 상태 폴링. job은 인메모리(다른 캐시들과 동일하게 재배포 시
        초기화, 영속화 불필요). 매크로 캘린더 수동재생성(POST /api/
        calendar/macro/run)도 같은 동기대기 문제가 있어 함께 전환 — 단
        이쪽은 종목별이 아닌 단일 리소스라 별도 job_id 없이 기존
        _macro_calendar_task_running 플래그 + 캐시 파일 자체를 진행상태로
        재사용(GET /api/calendar/macro/status 신설). static/index.html의
        runMacroCalendarNow()도 POST 완료=생성완료 가정을 버리고 /status
        폴링으로 갱신(theme_map POST는 프론트에서 호출하는 곳이 원래
        없어서 UI 쪽 변경 불필요, GET 두 곳만 이미 사용 중).
v5.122 [기능개선] 테마 라이프사이클 D0를 사이클 단위로 탐지(사용자 지시) —
        이탈(이탈) 판정 이후 새 점화 조건(z>=2 & 대장주+5%↑) 충족 시 새
        D0로 리셋, 서열그룹도 새 D0 기준으로 재확정. theme_lifecycle.py에
        find_cycles() 신설(_ignition_at 헬퍼로 find_d0와 로직 공유),
        analyze_theme()가 최근 사이클 기준으로 상단 필드 채우고 전체
        사이클 이력은 새 "cycles" 필드로 반환. static/index.html에 사이클
        이력 표 추가. 실데이터 검증(제약·바이오): 창 내 2개 사이클 확인 —
        ① D0 2026-06-17(알테오젠, z=2.28) → 이탈 2026-08-13,
        ② D0 2026-08-26(한미약품, z=3.0) → 진행중(오늘 08-31 기준 "후기"
        단계, 대장주 5일수익률 -11.2% vs 테마평균 -1.53%로 대장주 둔화).
        JW신약(067290.KQ)은 D0(8/26) 시점 서열7위(rank4+)였다가 확산목표
        (+5%)를 사이클 최초로(2거래일만에) 달성, 오늘 거래대금 서열은
        2위로 상승 — 오늘의 +14% 급등은 새 점화가 아니라 이미 진행 중이던
        8월 사이클의 "후기 국면 후발주자 따라잡기"로 해석됨.
v5.121 [신규] 돈의흐름 탭에 "테마 라이프사이클 분석" 서브뷰 추가(사용자
        지시). theme_map.json 종목리스트 기반(money_flow.py의 top100
        sector_of 집계와 별개 계산)으로 최근 60거래일 재구성: 거래대금
        점유율·breadth·서열별(rank1~3 vs 4+) 수익률·확산 lag·집중도.
        점화(z≥2&대장주만↑&집중도높음)/확산(breadth≥50%&2·3등도달&집중도
        하락)/후기(4+신고가>1~3등&대장주둔화)/이탈(점유율3일연속하락&
        breadth<40%) 4단계를 명시 임계값으로 판정, 판정마다 근거 수치를
        항상 동반(라벨만 던지지 않음). 자금이동 매트릭스(테마간 최근
        20거래일 점유율변화 상관)도 추가. 신규 모듈 theme_lifecycle.py
        (app.py 미의존, money_flow.py/theme_map.py와 같은 원칙). 신규
        API: GET /api/theme_lifecycle/{테마}, GET /api/theme_lifecycle_rotation.
        신규 테마 매핑 "제약·바이오"는 이 샌드박스에 ANTHROPIC_API_KEY가
        없어 theme_map.generate_theme_map()을 실제로 호출 못 해, 알려진
        KR 제약·바이오 8종목(JW신약 포함)을 수동 큐레이션해 로컬
        theme_map.json에 넣고 검증함 — theme_map.json은 .gitignore
        대상이라 이 커밋에 포함되지 않음. 배포 후 POST /api/theme_map/
        제약·바이오로 실제 Claude 생성분으로 교체 필요(수동 큐레이션은
        source:"manual"로 구분됨, 검증 참고 fallback).
v5.120 [문서/주석] 역방향 감사(2026-08-31)에서 찾은 stale 경고 2건 정리:
        CLAUDE.md의 rs_rank=80 고정근사치 설명을 "콜드캐시일 때만 폴백,
        웜이면 실제 rs_ranks 사용(v5.61)"으로 갱신, GUIDE.md 저모멘텀
        돌파 배지 설명의 "US 미검증"을 "US 검증 완료·정반대 방향 확인
        (z=2.39)"으로 28번째 줄 체크리스트와 일치시킴. 로직 변경 없음.
v5.119 [문서/주석] vol_reference()의 v5.57 UD게이트 경고(⚠️ 봇 쪽 수정
        필요, 이 레포에서는 손 안 댐)가 stock-alert v2.15에서 이미
        해결된 뒤에도 안 지워져 있던 것 정리 — "미뤄진 구현" 전수감사
        (2026-08-31)에서 발견. 실제 위험 없는 stale 경고였음, 로직
        변경 없음.
v5.118 [문서/주석] 거래량 확증 편향 수정 3단계 완료 — 시간비례 외삽
        자체의 장초반 과대추정 보정은 stock-alert/main.py에 구현(KR
        한정, VOLUME_PROJECTION_BIAS/_bias_correction_factor, v2.23).
        여기(app.py)는 vol_reference() 주석만 갱신 — "시간 보정은
        봇에서"로 미뤄져 있던 표현이 실제 구현 위치를 안 가리키고
        있었음(양쪽 다 서로 미루던 부분), 이제 정확한 함수/파일 위치를
        명시. 조사 전체 기록: docs/volume_confirm_bias_investigation.md.
v5.117 [버그수정] 거래량 확증(얼마냐봇) 편향 수정 1단계 — 분모 오염 제거
        (사용자 지시). /api/vol/{ticker}의 avg_volume_50/20이 장중 호출
        시 '오늘'의 진행 중인 부분봉을 50/20일 평균에 그대로 섞어 넣고
        있었음(KR: naver_kr.fetch가 오늘 거래일을 실시간으로 채워 반환,
        US: yfinance 일봉도 장중 갱신됨). 부분봉이 섞이면 평균이 낮아져
        봇의 '예상 거래량비(%)'가 시간비례 외삽 편향 위에 한 번 더
        부풀려짐. 마지막 행이 오늘 날짜이고 해당 시장이 장중이면
        (_is_market_open_now) 평균 계산에서 제외. 실측(005930.KS,
        08-31 10:12 KST): avg50 29,302,219(오염) → 29,865,350(수정,
        +1.9%), 실제 엔드포인트 종단 호출로 확인. 종가베팅(analyze_jongga)
        은 이 엔드포인트를 쓰지 않고 자체 vol.iloc[-21:-1] 창(원래부터
        당일 제외)이라 이번 수정과 무관 — 영향 없음 확인. 거래량 확증
        편향 수정 3단계 중 1단계, 다음 단계는 표본 확대 재측정.
v5.116 [기능추가/버그수정] 일지 진입 기록 실거래 기준 자유입력(사용자 지시).
        (1) 일지 모달에 수량/투입금액 입력칸 신설 — 카드 계산값을 기본값
        으로 채우되 자유 편집, 저장 시 우선순위는 수량직접입력 > 투입금액
        ÷진입가 > R설정 제안치. 수정 폼(✏️)에도 수량 입력칸 추가해 사후
        수정 가능(빈칸이면 기존값 유지, 강제로 안 지움). (2) 버그수정:
        saveJournal()이 돌파/돌파임박/박스돌파 모드면 카테고리를
        '추세추종(실제 매매)'로 선택해도 무조건 status='pending'으로
        강등하던 문제 — 실제 체결가가 카드 피벗보다 높은(슬리피지) 정상
        매매까지 대기로 잘못 빠짐. 카테고리 기반 판정으로 통일(saveEdit()
        과 동일 규칙) — 감시(quick watch)의 피벗교차 자동판정(v5.106,
        /api/watch/quick + updateTracking pending→entered)은 별개 경로라
        무변경. (3) R 진행률은 이미 r.entry/r.stop(사용자가 저장한 실제
        값) 기준으로 계산되고 있었음(구조상 카드 가정값을 따로 저장 안
        해서 폴백 로직 불필요) — 이번엔 그 입력 경로 자체의 버그만 수정.
        (4) 포지션 탭 정합: 일지 진입가 옆에 토스 실제 평단가 참고 표시
        (tossAvgFor, 읽기전용 — r.entry는 절대 안 덮어씀, 포지션 탭을
        연 적 없으면 표시 안 함). jsdom 20건 검증(entry>pivot에도 진입
        유지, R 계산, 수정 영속성, 대기/관찰 카테고리 무변화, updateTracking
        무변경 확인 등) 전부 통과.
v5.115 [수정] 다중 히트 배지 툴팁에 표본크기 경고 자동 병기(사용자 지시,
        docs/kr_breakout_family_multi_hit_ev.md 후속 결정). n<50인 조합
        (3중 n=33, 돌파+추세전환 n=8, 박스돌파+추세전환 n=12)에 "표본
        작음: 참고용" 문구 추가 — 최소 n 하한 게이트는 걸지 않음(정보성
        표시일 뿐이라 판단). n 값은 MULTI_HIT_SAMPLE_N 한 곳에서만
        관리해 경고 임계값이 개별 문구와 따로 안 놀게 함. 배지/가점
        로직 자체는 변경 없음.
v5.114 [기능추가] KR 돌파 계열(돌파/박스돌파/추세전환) 동시 히트 배지 +
        정렬 가점(사용자 지시, docs/kr_breakout_family_multi_hit_ev.md
        사전등록 채택 결과 구현). 자카드 진단(돌파↔박스돌파 0.485=중복,
        추세전환↔나머지 0.02~0.03=독립)에 따라 "개수"가 아니라 "독립성"
        기준 — 추세전환이 낀 조합만 우대: 2조합 🔱강력(EV 0.5~0.83R),
        3조합 🔱🔱(EV 1.152R), 돌파+박스돌파만이면 ⚑중복신호(가점 없음,
        EV 0.462R이지만 정보 제한적). run_scan()에 _kr_breakout_family_
        hit_map()으로 당일 3탭 동시 히트 계산(측정 스크립트 collect_family
        로직 재사용) → 정렬 키에 score+multi_hit_sort_bonus(강력+8,
        3중+15, 중복+0) 반영. 접힌 카드에도 🔱만 노출(⚑는 펼쳐야 보임).
        GUIDE.md "🇰🇷 국장" 체크리스트에 한 줄 추가.
v5.113 [기능개선] 검색 진단 화면을 tab-hit 카드 수준으로 강화(사용자 지시).
        기존엔 /api/lookup이 지표 6개짜리 독립 소형 카드(renderLookupCard)를
        따로 그리고 액션 버튼이 아예 없어 "조건 미달 종목을 감시 걸기"가
        불가능했음. static/index.html: card()에 mode='search' 분기
        (searchResultCard) 신설 — 실제 tab-hit 카드와 같은 sparkSVG/
        fmtVolume/fmtTurnover/fmtRR 등을 그대로 재사용하고, entrySignal()
        화이트리스트에 'search'가 없어 실제 시그널 배지가 섞일 위험 없음.
        헤더에 "🔍 검색 결과 — 현재 시그널 없음" 배지로 실제 히트와 구분.
        app.py: /api/lookup에 scanner.horizontal_levels(가장 가까운 상단
        저항=참고 피벗)·_rr_block(손절=CONFIG["risk_hard_atr_mult"], 새
        배수 발명 안 함)로 "돌파 시 참고 수치"(가정 진입/손절/리스크%/
        전고 기준 손익비) 계산 — 실제 게이트가 쓰는 것과 같은 함수
        재사용, 매수 신호 아님을 라벨에 명시. /api/debug의 탈락_핵심사유에
        "(이후 조건은 미검사)" 항상 명시 + breakout/boxbreak/imminent가
        min_bars/rs_min에서 걸린 경우 고점대비%(off_high_pct) 값을
        [참고,미검사]로 병기(예: "고점대비 -29.1% (요구 -25% 이내)") —
        그 뒤 게이트는 이전 게이트가 좁혀놓은 상태에 의존해 순서 밖에서
        계산하면 값이 왜곡될 수 있어 이 한 항목만 보수적으로 추가.
        ⚡감시/☆즐겨찾기/+일지 버튼도 추가 — quickWatch/openJournal이
        lastHits(현재 탭 히트 목록)에서만 종목을 찾던 구조라 검색 결과
        티커는 못 찾고 조용히 no-op했음(신규 _searchHit 전역 폴백으로
        수정). 로컬 함수 호출(005930.KS/000660.KS 실조회)과 jsdom
        렌더 테스트(카드 필드 14건 + renderLookupCard/openJournal/
        quickWatch 동작 3건, 전부 pass)로 검증.
v5.112 [버그수정] 검색창/경보등록 한글 종목명 조회 회귀(사용자 보고: "삼성전자"
        → 유니버스에 없다, "005930"은 정상). git log 전수 확인 결과 특정
        커밋의 회귀가 아니라 /api/lookup이 애초에 이름→티커 변환을 한 번도
        하지 않았음(항상 ticker.upper()만 하고 유니버스 키(티커)와 직접
        대조) — 대형주가 우연히 현재 탭 결과에 카드로 있을 때만 클라이언트
        측 부분일치 필터로 동작해온 것처럼 보였을 뿐. universe.py에
        resolve_name_to_ticker() 신설(단일 지점) — ①입력이 이미 티커
        ②숫자코드+KR접미사 ③이름 완전일치 ④접두일치 ⑤부분일치 순, 각
        단계에서 후보 2개+면 그 단계에서 후보 목록 반환("두산에너"→
        두산에너빌리티 1건은 접두일치 단일매치라 후보 없이 바로 확정,
        "삼성"처럼 17건 걸리면 후보 목록). /api/lookup·POST /api/alerts
        (경보 등록 — 여기도 raw 문자열을 그대로 대문자화해 파일에 쓰던
        동일 버그) 둘 다 이 함수 하나로 통일. 즐겨찾기/감시등록(watch/
        quick)/일지는 전부 카드·버튼에서 이미 resolve된 ticker를 받는
        구조라 전수 점검 결과 이 버그의 영향 없음(감시 등록만 자유 텍스트
        입력이라 실제로 뚫려 있었음). 조용한 실패 금지 원칙 적용 — 실패
        사유를 "이름을 못 찾았어요(코드로 시도해보세요)" vs "유니버스에
        없어요"로 구분해 반환하고, 프론트도 서버 메시지를 그대로 표시하도록
        수정(기존엔 서버 에러 내용과 무관하게 항상 같은 하드코딩 문구를
        띄우고 있었음 — 이것도 조용한 실패였음). 로컬 함수 호출로 검증:
        삼성전자→005930.KS, 두산에너빌리티/두산에너(부분)→034020.KS,
        005930→005930.KS, NVDA→NVDA 전부 정상, 존재하지 않는 이름은
        name_not_found로 정확히 구분, 999999.KS는 not_in_universe로 구분.
v5.111 [기능개선] 캘린더 홈 "숨기기/표시" 균형 재조정(사용자 지시) —
        데이터 없는 날(주말 등) 화면이 너무 빈약해 보이던 문제. ① 섹션
        골격 7개 전부 항상 표시로 전환 — 액션큐/테마×스캐너/포워드성적은
        데이터 없어도 섹션 자체는 유지하고 안에 회색 안내 한 줄만
        표시(D-day 경고만 예외 — 없으면 완전 숨김 유지, 경고는 있을 때만
        의미). ② 포지션 요약: 한 줄 텍스트 → 미니카드 칩(종목별
        [티커 +x.xR | 손절까지 -x.x%], 근접 시 빨간 테두리)으로 승격 —
        get_positions()에 이미 있던 dist_to_stop_pct를 positions_summary
        items에 추가로 노출(그동안 near_stop 불리언만 있어서 프론트가
        실제 % 숫자를 못 그렸음). ③ 상단에 시장 컨텍스트 배너 신설 —
        오늘 KR·US 둘 다 휴장(주말 포함)이면 "🛌 오늘 KR·US 휴장 — 다음
        개장 {날짜}"(is_trading_day 재사용, 둘 중 하나라도 열리는 가장
        가까운 날 탐색). ④ 매크로 캘린더 생성 프롬프트(macro_calendar.py)
        확장 — 기존 5개 카테고리(FOMC/CPI/PPI/고용보고서/GDP/금통위)에
        연준 인사 연설·미국채 주요 입찰·미국 옵션만기(쿼드러플위칭,
        분기말월 세번째 금요일)·KR 선물옵션 만기(매월 둘째 목요일)·빅테크
        실적(AAPL/MSFT/GOOGL 등 보유 무관 초대형주) 추가, MAX_TOKENS
        4000→6000·web_search max_uses 5→8 상향(카테고리 확장분 반영).
        POST /api/calendar/macro/run을 API_READ_TOKEN 쓰기 허용목록에
        추가(v5.109 테마매핑과 같은 패턴 — 세션 로그인 없이 새 프롬프트
        결과 확인용). 로컬 검증: market_closed 배너(실제 일요일 기준
        정상 감지, next_open=다음 월요일), positions_summary의
        dist_to_stop_pct 필드 노출, 토큰 인증 경로(무토큰 401 → 토큰
        통과) 전부 확인.
v5.110 [기능추가] 캘린더 홈 5개 섹션 확장(사용자 지시). 전부 기존 API/
        캐시 재사용, 새 계산 최소화 원칙 — GET /api/calendar 하나에 통합
        + 독립 호출(게이트·포지션·종가베팅후보) asyncio.gather 병렬화,
        earnings 조회도 티커별 gather 병렬화(로컬 검증: 응답시간 캐시
        워밍 후 약 1.4초). 배치 순서: ① 🔴D-day 경고(보유종목 실적
        D-3 이내, get_positions()+기존 earnings 로직 교집합) ② 💼포지션
        요약 한 줄(get_positions() 재사용, 손절선 -3%↑ 🔴강조 —
        dist_to_stop_pct≤3, 클릭 시 포지션 탭 이동) ③ 📋오늘의 액션 큐
        (대기 피벗 -1%↓근접·종가베팅후보 발생·대장관찰 눌림목전환, 항목
        없으면 "오늘 대기 항목 없음" 한 줄) ④ 게이트+돈의흐름(기존
        유지) ⑤ 🔥강세테마×스캐너 교집합(KR 돈의흐름 스냅샷의
        stage="확산(본격)" 또는 streak_days≥2 테마 × theme_map.json ×
        오늘 KR 돌파/박스돌파/추세전환 스캔 캐시 _cache["kr:{mode}"] 교집합
        — 캐시 미스면 새 스캔 안 돌리고 그냥 스킵, 없으면 섹션 자체 비표시)
        ⑥ D+14 일정(기존 유지) ⑦ 종가베팅 포워드 성적(_jongga_forward_stats()
        재사용, 30건 미만이면 "표본 축적 중" 문구).
        대장관찰 전환판정은 POST /api/watch/leader-check의 로직을
        _leader_conversion_check() 공용 함수로 추출해 캘린더와 같이 씀
        (중복 제거). [버그수정] 구현 중 발견: /api/ma·dist·vol·
        pullback-signal·캘린더 신규 코드 총 5곳이 _data_cache 키를
        "data:KR"/"data:US"(대문자)로 조회하고 있었는데 실제 저장 키는
        _fetch_market_data의 f"data:{market}"이 그대로 소문자(kr/us)라
        캐시 히트가 한 번도 안 나고 매번 "data:all" 폴백 또는 개별
        fetch로 새던 사전 존재 버그 — 5곳 전부 소문자로 수정(로컬 재현:
        market=kr 단일 스캔 후 대문자 키로는 캐시 미스, 소문자로 고치니
        정상 히트 확인). 로컬 curl 전체 시나리오(대기 피벗 근접·포지션
        근접손절·손절미설정·강세테마 매칭 로직 단위테스트) 검증 완료 —
        단 실제 프로덕션 데이터(테마 확산 상태·종가베팅 당일 후보)로는
        미검증, 브라우저 렌더링도 Chrome 확장 미연결로 미검증(v5.108과
        동일한 한계).
v5.109 [기능개선] API_READ_TOKEN으로 테마 매핑 수동생성(POST
        /api/theme_map/{theme}) 허용(사용자 지시). 배경: v5.105 로그인
        게이트 도입 후 이 쓰기 엔드포인트도 세션 쿠키가 필요해져서, 스크립트/
        curl로 테마 매핑을 트리거하려면 로그인 쿠키가 있어야 했음(비번을
        채팅에 공유하지 않는 게 사용자 방침이라 곤란) — API_READ_TOKEN
        헤더로도 통과하게 별도 허용목록(_is_token_writable_path) 신설.
        기존 얼마냐봇 GET 허용목록(_is_bot_read_path, GET 전용)과는 분리된
        함수 — 저널/포지션/손절 등 계정 데이터를 바꾸는 나머지 쓰기 API는
        토큰으로 절대 안 열림(세션 쿠키 필수 유지), 이 엔드포인트 하나만
        예외. 토큰 유출 시 피해도 theme_map.py 자체 비용가드
        (DAILY_GENERATION_LIMIT=3/일)로 제한됨. 로컬에서 401(토큰 없음/틀림)
        →통과(정상 토큰) 경로와, 무관 쓰기 API(POST /api/journal)는 토큰
        으로도 여전히 401인 것까지 확인.
v5.108 [버그수정+기능추가] 초기로드 탭↔시장 연동 버그 + 📅캘린더 탭 신설
        (사용자 지시). ① [버그수정] v5.92의 탭↔시장 자동연동(TAB_MARKET_LABEL)이
        [data-mode] 클릭 핸들러에만 있고 부트스트랩(첫 로드) 경로엔 없었음
        — 기본 탭이 🇺🇸/🇰🇷 라벨 탭인데 market 기본값은 'all'이라 첫 로드에
        다른 시장 종목이 섞여 나오던 버그. 클릭 핸들러의 뷰전환 로직을
        applyTabViewState() 함수로 빼서 부트스트랩도 같은 경로를 타게 통일.
        ② [기능추가] 📅캘린더 탭 — 로그인 후 기본 화면(탭 줄 맨 앞, mode
        기본값도 'calendar'로 변경). GET /api/calendar 한 번으로: 게이트
        신호등 3개(KR/US/종합, market_gate() 재사용) + 최신 돈의흐름 오늘
        한 문장(KR/US, money_flow_report.extract_summary 재사용) + D+14
        매크로 일정 + 휴장일(is_trading_day 재사용, 평일만) + 보유·즐겨찾기
        종목 다음 실적일(yfinance calendar, KR/US 둘 다 실측 확인 — AAPL/
        005930.KS로 정상 조회됨, 24시간 캐시). 매크로 일정은 새 모듈
        macro_calendar.py가 money_flow_report.py와 같은 원칙(Claude API +
        web_search 툴, 실패해도 예외 없이 (events,error) 반환)으로 생성 —
        4주 US(FOMC/CPI/PPI/고용보고서/GDP)·KR(금통위 등) 일정을 응답 맨 끝
        JSON 블록으로 받아 파싱, 형식 안 맞는 항목은 필터링. 캐시
        (macro_calendar.json, /data 볼륨)는 7일 이상 오래됐을 때만 재생성
        (스케줄러 4분 루프에 훅, 실패는 24시간 재시도 스로틀), 실패 시 이전
        events는 그대로 유지 + last_error만 갱신 — "성공한 마지막 결과"와
        "이번 시도 상태"를 분리해서 장애 중에도 화면이 비지 않게 함. 수동
        재생성 POST /api/calendar/macro/run(🔄 버튼)도 제공. 화면에 "일정은
        참고용이며 변경될 수 있어요" 한 줄 고정 노출. 로컬 검증: /api/calendar
        정상 응답(게이트·돈의흐름·휴장일 실측 확인), D+14 윈도우 필터링(3건
        중 윈도우 밖 1건 정상 제외), 즐겨찾기 실적일 조회(AAPL D-61 정상),
        macro_calendar.py JSON 추출/검증 로직 단위 테스트(잘못된 날짜·국가
        코드 항목 정상 필터링), API 실패 시 폴백 경로(캐시 유지+에러 기록)
        실측 확인 — 단, 실제 Claude 생성 성공 경로는 로컬 API 키 문제로
        미검증(프로덕션 배포 후 확인 필요). 브라우저 렌더링은 Chrome 확장
        미연결로 미검증 — curl 백엔드 검증 + JS 문법 검사만 완료.
v5.107 [기능개선] Railway 도메인 이전 반영(사용자 지시) —
        pullback-production → pullback2-production.up.railway.app. moneyflow
        리포트 URL(app.py), sync_toss.py의 DEFAULT_SERVER_URL, CLAUDE.md·
        docs/toss_position_sync_setup.md 문서 전부 교체. stock-alert
        레포(SCANNER_URL 기본값, v2.22)도 같이 교체 — 얼마냐봇이 폴링하는
        모든 API 호출이 새 도메인을 향하게 함. 단, sync_toss.py는 .env의
        PULLBACK_SERVER_URL이, stock-alert는 Railway SCANNER_URL 환경변수가
        설정돼 있으면 이 기본값보다 우선한다 — 로컬 .env(PULLBACK_SERVER_URL)는
        미설정 확인, Railway SCANNER_URL 설정 여부는 대시보드 확인 필요
        (설정돼 있다면 거기도 같이 갱신해야 실제로 반영됨).
v5.106 [버그수정] 감시 등록 즉시 '진입'으로 오전환되는 버그(사용자 지시,
        재현: 8/30 컴투스 등 5건 — 등록가=피벗 34,000, 현재가 36,000, 등록
        직후 +1.6R 표시). 원인: 서버(/api/watch/quick)와 얼마냐봇은 이
        전환에 관여 안 함 — 전환은 static/index.html의 updateTracking()이
        "현재가 ≥ 진입가" 상태(레벨)만 보고 매 체크마다 판정하는 구조였고,
        등록 시점 가격을 저장/비교하지 않아 등록 직후 첫 체크에서 바로
        걸림(교차 감지가 아니라 레벨 체크였던 게 근본원인). 수정 3단계:
        ① reg_price(등록 시점 가격) 필드 신설 — /api/watch/quick이 받아서
        저장, updateTracking() pending 분기가 "reg_price가 이미 entry
        이상이면 자동전환 안 함"으로 교차 판정하도록 변경(reg_price 없는
        구버전 레코드는 기존 레벨체크로 폴백, 하위호환). ② 등록 시점 가드:
        quickWatch()가 등록 전에 현재가≥피벗이면 3지선다 모달(①현재가
        기준 진입 기록 ②지정가 입력해 대기 ③취소) 표시, 서버도 같은 조건을
        force_status 없이는 409로 거부(이중 방어 — 프론트 우회/레이스에도
        안전). ③ 오염 기록 정정: viewRow에 ↩대기로(revertToPending) 버튼
        신설 — entered→pending 수동 되돌리기(부분익절 있으면 차단), 기존
        delJournal(삭제)과 함께 8/30 5건 수동 정리 가능. 수동 '+ 일지에
        추가' 모달(saveJournal)도 reg_price를 같이 저장해 같은 안전장치
        적용(대기 카테고리로 저장 시).
v5.105 [기능추가] 앱 비공개 전환 — 로그인 게이트(사용자 지시). 포지션
        탭(계좌 수량·평단·손익)이 그대로 공개 노출되던 상태를 막음.
        APP_PASSWORD 환경변수 설정 시에만 켜짐(미설정이면 기존처럼 전체
        공개, 온오프는 이 변수 하나). 90일 HMAC 서명 쿠키(pb_session,
        httponly+secure+samesite=lax) — 서명 키가 비번에서 파생돼 비번을
        바꾸면 이전 쿠키가 전부 자동 무효화됨. /login(GET·POST, 비밀번호
        입력 하나짜리 미니멀 폼) 신설, python-multipart 의존성 추가 없이
        urllib.parse.parse_qs로 form body 직접 파싱. 예외: ①
        /api/positions/sync·sync_error는 기존 SYNC_TOKEN 자체 보호 그대로
        유지(세션 게이트 안 거침, sync_toss.py는 쿠키를 못 들고 있음) ②
        얼마냐봇이 폴링하는 GET 경로(watch/positions·watch/pending·
        opening-surge·jongga/candidates·positions·market/gate·journal·
        dist/{t}·ma/{t}·pullback-signal/{t}·vol/{t}·moneyflow/{m}/summary,
        stock-alert/main.py SCANNER_URL 호출부 전수 확인 기준)는 새
        API_READ_TOKEN 헤더(X-Api-Read-Token)로도 통과 — stock-alert
        레포도 이 헤더를 보내도록 같이 수정(v2.21). 나머지 전부(스캔·
        저널·디버그 등)는 세션 쿠키 필수, API는 401 JSON·페이지는 /login
        리다이렉트.
v5.104 [기능추가] sync_toss.py IP 미허용 실패 상태 노출(사용자 지시).
        토스 API가 403(IP 미허용)으로 실패하면 sync_toss.py가 현재 공인
        IP를 포함해 POST /api/positions/sync_error(SYNC_TOKEN 인증)로
        상태만 서버에 남김 — sync_error.json에 저장, since/last_seen 구분
        (같은 ip+type이면 since 유지, last_seen만 갱신). GET /api/positions
        응답에 sync_error 필드로 노출. 성공적으로 동기화되면(POST
        /api/positions/sync) sync_error.json을 자동 삭제 — 문제 해소 시
        조용해짐. 알림 발송(텔레그램)은 이 레포 책임이 아니고 얼마냐봇이
        sync_error 필드를 폴링해서 자체 dedup 후 처리하는 구조로 결정
        (텔레그램 봇 토큰을 이 레포에 두지 않기 위해 사용자가 방향 전환).
v5.103 [기능추가] 포지션 보드 1단계 — 토스 잔고 × 스캐너 결합(사용자 지시).
        아키텍처: Railway가 토스 Open API를 직접 못 부름(허용 IP 방식,
        Railway 아웃바운드 IP 비고정) → 맥 로컬 sync_toss.py(launchd,
        30분 간격, toss_client.py 조회전용 재사용)가 수량·평단만 추려
        POST /api/positions/sync(X-Sync-Token 헤더, SYNC_TOKEN 불일치 시
        401)로 전송, positions.json에 스냅샷 저장. GET /api/positions는
        저장된 수량·평단에 서버가 그 순간 새로 조회한 가격을 결합해
        평가액·손익·R진행률·ATR×1.5 손절제안·RS·현재 히트중인 탭·가격고정
        의심을 계산 — 가격은 항상 실시간, 수량·평단만 동기화 지연 허용.
        24시간 이상 스냅샷 오래되면 stale 플래그. 손절가 입력은
        positions_meta.json(수량·평단 동기화가 절대 못 건드리는 별도
        파일)에 저장 + 같은 티커의 열린 저널 기록 stop도 같이 갱신.
        💼 포지션 탭(마감정리 옆) 신설. 계좌번호 등 식별정보는 sync_toss.py가
        애초에 추출하지 않음. Railway SYNC_TOKEN 환경변수·launchd 등록은
        사용자 확인 후 별도 진행(docs/toss_position_sync_setup.md).
v5.102 [버그수정] 감시/관찰/일지 버튼 계열 전면 감사(사용자 지시). 근본
        원인: 저널 저장이 /api/journal 전체배열 덮어쓰기인데, 프론트가
        setJournal()과 _saveJournalToServer()를 따로 호출하는 곳이 9곳
        있었고, quickWatch/watchLeaderConversion의 loadJournalFromServer()가
        내부에서 updateTracking()을 미대기(fire-and-forget)로 돌려 동시에
        여러 저장 요청이 경쟁 — 늦게 도착한 쪽이 먼저 도착한 걸 그대로
        덮어써(lost-update) 방금 등록한 레코드가 저장 파일에서 통째로
        사라지는 사고 재현 확인(👁 관찰 등록 직후 자동 추적 사이클과
        경쟁시켜 재현 → 관찰 레코드 소실, 수정 후 재현 안 됨 확인).
        _saveJournalToServer 직접 호출 9곳을 모두 setJournal()로 통일하고,
        setJournal()을 프라미스 체인으로 직렬화 + 실제 전송 시점에
        journalCache를 다시 읽도록 변경 — 유실이 구조적으로 불가능해짐.
        사용자가 의심한 "analyze_boxbreak() pivot 필드 부재"(대장후보 죽은
        버튼과 같은 이유) 가설은 실측 스캔 5건 전수 확인으로 기각 — pivot/
        stop 모두 정상 존재, card() 렌더링도 정상. ⚡감시 버튼의 "무반응"
        체감은 이 저장 경쟁 사고의 다른 얼굴로 판단(등록 직후 자동추적이
        경쟁해 조용히 유실). 👁 관찰이 "대기 아닌 진입"으로 보인 것도
        같은 경쟁 사고 — 생성 시점 코드(app.py /api/watch/quick, status
        'watch' 정상 부여) 자체는 문제 없었음.
v5.101 [UI개선] 탭 순서를 시장별 측정 EV 순으로 재배열(사용자 지시,
        순수 표시 레이어 — scanner.py 게이트/EV 로직 미변경).
        🇺🇸: 슈퍼대장(0.346R)→돌파임박(0.232R)→눌림목(0.206R).
        🇰🇷: 박스돌파(0.431R)→돌파(0.368R)→추세전환(0.326R)→종가베팅
        (오버나이트 갭 전략이라 R계열과 비교 불가 — 계열 맨 뒤 고정,
        위치 자체는 원래도 맨 뒤라 변경 없음). 나머지 그룹(대장관찰·
        섹터·돈의흐름/인버스·붕괴/마감정리·내일지/⋯실험)은 현행 유지.
        GUIDE.md "시장별 전략 지도" 표 + 체크리스트 두 곳(🇰🇷/🇺🇸 권장
        탭 나열) 순서를 동일하게 맞추고 "탭 순서 = 측정 EV 순" 명시
        추가.
v5.100 [기능추가] 테마-관련주 매핑 인프라(사용자 지시) — 테마로테이션
        탭의 전제, 측정 3번(대조군용 재료). UI는 아직 없음(측정 통과 후
        별도 지시로 탭 설계 때 함께).
        신규 `theme_map.py`(app.py 미의존 — money_flow.py/money_flow_
        report.py와 같은 원칙): `generate_theme_map(theme, kr_universe)`
        가 Claude(`claude-sonnet-4-6`, money_flow_report.py와 동일 모델
        — 이미 이 앱에서 검증된 조합)에 web_search 툴을 켜서 테마 관련
        KR 상장사 명단을 JSON으로 받는다(프롬프트: 실재 상장사만·사업
        직결도 순 rank·대장주=재료와 가장 직접 연결된 종목·각 종목
        구체적 한 줄 근거). 환각 방지: 응답 티커를 `universe.get_universe
        ("kr")`과 6자리 코드 기준으로 대조 — 불일치 종목은 결과에서
        빼고 `removed` 필드+로그로 남김(테스트: 가짜 anthropic 모듈
        주입해 실제 티커 1개+존재하지 않는 티커 1개 섞은 응답으로 직접
        검증, 환각만 정확히 걸러짐 확인). 저장:
        `_resolve_theme_map_dir()`(JOURNAL_DIR→/data→앱폴더, money_flow.py
        의 `_resolve_money_flow_dir`와 동일 우선순위를 독립 재현)의
        theme_map.json — `{테마명: {generated_at, stocks, source,
        removed}}`. 30일 경과 시 `is_stale()`이 재생성 대상으로 판정.
        트리거: `_run_money_flow(market="kr", ...)`가 스냅샷 계산 후
        `themes` 중 `stage=="확산(본격)"` 또는 `streak_days>=2`인 테마를
        추려 `theme_map.maybe_auto_generate()` 호출 — 매핑 없거나(or
        30일 경과) 테마만, 하루 신규 생성 최대 3건(비용 가드, 테스트로
        한도 초과 시 API 미호출까지 직접 검증) 넘으면 스킵. US 돈의흐름
        잡은 대상 아님(테마 매핑은 KR 전용).
        API: `GET /api/theme_map`(목록, 경량 뷰) · `GET /api/theme_map/
        {테마명}`(매핑 조회 — 저장된 정적 rank와 별도로 각 종목의 당일
        거래대금 순위 `turnover_rank_today`를 동적으로 계산해 병기) ·
        `POST /api/theme_map/{테마명}`(수동 생성 — 자동 생성과 같은
        일일 한도 공유, 한도 초과 시 429).
v5.99 [기능추가] 개장일(거래일) 판정 가드(사용자 지시) — 스케줄러가
        주말/공휴일 구분 없이 매일 돌아서, `_market_session_key()`가
        KST 요일/시각만으로 "장마감" 판정을 내리면 실제 휴장일에도
        daykey가 생성돼 금요일 데이터가 그날(토요일/평일공휴일) 날짜로
        리포트·스캔캐시에 저장되는 문제가 있었음. `is_trading_day(market,
        date)` 신설 — 주말+정적 공휴일 목록(2026-08-29 WebSearch/WebFetch
        로 KRX 3개 소스 교차확인(+BigGo뉴스로 6/3 지방선거·7/17 제헌절
        특별휴장 재확인), NYSE는 nyse.com 공식+stockmarkethours.org 교차
        확인, 전부 datetime.weekday()로 대체공휴일 논리 재검증)로 판정.
        의존성 검토: `holidays`/`exchange_calendars` 미설치 상태에서 신규
        추가는 requirements.txt 버전 미고정(v5.93 Railway 장애 조사에서
        이미 확인된 리스크) 문제와 겹쳐 위험 대비 이득이 낮다고 판단해
        보류, pykrx는 KRX_ID/KRX_PW 로그인 필요(v4.38.9에서 이미 포기)라
        미채택 — 정적 리스트로 결정(docs/trading_calendar.md에 갱신
        절차·출처·2027 KRX 미확정 상태 명시, KRX는 2026만/NYSE는
        2026~2027 확인됨을 KRX_CONFIRMED_YEARS/NYSE_CONFIRMED_YEARS로
        구분해 범위 밖 연도는 로그로 알림). 적용: (1) `_warm_market()`의
        "장마감 후" 분기 전체(스캔캐시 워밍+돈의흐름 생성+종가베팅
        EOD/갭확정)를 is_trading_day() 가드로 감싸 비개장일엔 통째로
        스킵(기존 캐시 유지, 재웜 안 함). (2) `_maybe_run_jongga_snapshot()`
        의 기존 주말 전용 체크를 is_trading_day()로 교체(공휴일도 커버).
        (3) `/api/moneyflow/{market}`·`/api/moneyflow/{market}/summary`·
        `/api/jongga/candidates`·`/api/jongga/forward` 응답에 trading_day
        필드 추가 — 봇이 이중 확인할 수 있게. 검증: is_trading_day() 13개
        케이스(주말/공휴일/평일/확인범위밖연도) 직접 실행 확인(2026-08-29
        실제로 토요일임을 datetime으로 재확인해 판정 정확성 교차검증).
v5.98 [기능추가] 🇰🇷 종가베팅 포워드 트래킹(사용자 지시) — 후보의 실제
        성과를 자동 누적해 백테스트(+1.22%, n=276, z=4.28)와 실전 결과를
        계속 대조. 저장: `_resolve_persistent_path("jongga_forward.json")`
        (Railway 볼륨, favorites/alerts와 동일 경로 우선순위). 레코드는
        {날짜:{티커:{...}}} — snapshot_price/close_price/next_open_price
        3단계로 채워지고, 스냅샷가↔확정종가가 다를 수 있어(14:40~15:30
        변동) 두 기준(gap_snapshot_pct/gap_close_pct)을 분리 계산.
        (1) `_record_jongga_snapshot()`: 14:40~15:00 스케줄 스냅샷
        (`_maybe_run_jongga_snapshot`)에서만 호출 — 라이브 온디맨드 스캔은
        기록 안 함(중복/조기기록 방지). (2) `_record_jongga_eod()`:
        `_warm_market()`의 '장마감 후' 분기(daykey 확정 시점)에서 오늘자
        후보의 확정 종가 채움. (3) `_resolve_jongga_gaps()`: `_warm_market()`
        장마감후·장중 두 분기 다에서 호출 — 과거(오늘 이전) 미확정
        레코드 중 다음 거래일 데이터(Open)가 이미 들어온 것을 찾아 갭
        확정(date_str < today 가드로 당일 자기 자신 오확정 방지, 왕복
        비용 0.3% 차감은 백테스트와 동일 가정). (4) `/api/jongga/forward`
        신설 — 스냅샷기준/종가기준 각각 n·평균갭·갭업률 + 백테스트
        참조값 + 최근 30건. (5) static/index.html: 종가베팅 탭 하단에
        `jonggaForwardFooterHtml()` — 누적 30건 미만이면 "표본 축적 중"
        표시, 이상이면 두 기준 나란히 표시. `/api/scan`과 별도 엔드포인트라
        탭 진입 시 `loadJonggaForwardStats()`로 독립 fetch.
        검증: _record_jongga_snapshot→_record_jongga_eod→_resolve_jongga_gaps
        →_jongga_forward_stats() 전체 라이프사이클을 합성 pandas 데이터로
        직접 실행해 종가/갭 계산값 정확성 확인(예: (72500/71000-1-0.003)
        *100=1.81% 등 수동 검산 일치).
v5.97 [기능추가] 🇰🇷 종가베팅 탭 신설(사용자 지시) — 사전 등록 백테스트
        채택 조건 그대로 구현(docs/kr_jongga_betting_backtest.md, 조합A:
        n=276, 비용차감후 평균 +1.22%, base 대비 z=4.28). T일 종가매수
        →T+1일 시가매도, KR 전용.
        [스캔] scanner.py: JONGGA_CONFIG + analyze_jongga() 신설 —
        base(거래대금 상위100)·candle(+3%↑짧은윗꼬리)·volume(20일평균
        2배+)·position(20일선위·52주고점-15%이내) 임계값 백테스트와
        완전 동일. 상한가(전일比+30%) 근접 종목은 매수 불가라 제외(백
        테스트엔 없던 실전 제약, 신규 추가). 가격고정(M&A) 정보용 필드
        부착은 기존 9개 analyze_*()와 동일(_price_frozen_block).
        [교차] app.py `_run_scan_jongga()` 신설 — turnover_rank(거래대금
        순위)는 RS랭크와 같은 이유로 cross-sectional이라 analyze_jongga()
        밖에서 KR 전종목 대상으로 미리 계산해 넘김. `/api/scan?mode=
        jongga`로 노출(allowed mode 목록에 추가).
        [타이밍] naver_kr.fetch_history()가 장중 당일 봉을 실시간에
        가깝게 이미 포함한다는 게 기존 docstring에 확인돼 있어(별도
        "전일 확정 폴백" 불필요) `_jongga_session_state()`가 평일
        14:40 전/14:40~15:30/그 외 3단계로 안내 문구만 결정(스캔 로직
        자체는 시간 무관, 매 요청 시점 최신 봉 사용). `_scheduler_loop()`
        (4분 주기)에 `_maybe_run_jongga_snapshot()` 추가 — 평일 14:40
        ~15:00 사이 1회만 스냅샷을 잡아 `_cache["kr:jongga"]`에 저장.
        [알림] `/api/jongga/candidates` 신설 — 얼마냐봇(외부 레포,
        `/api/ma` 폴링 방식과 동일)이 폴링해 자체적으로 텔레그램 메시지를
        만들어 보내는 용도. **이 레포엔 텔레그램 발송 코드 자체가 없어
        (얼마냐봇은 별도 레포) "14:50 자동 발송"은 이 커밋만으론 미완성**
        — 데이터 엔드포인트 + 메시지 포맷 힌트(message_format_hint)까지만
        준비, 실제 발송은 봇 레포 쪽 작업 필요(사용자에게 별도 보고).
        [UI] static/index.html: 🇰🇷 3탭 옆에 "🇰🇷종가베팅" 탭 추가.
        jonggaCard()/collapsedRowHtmlJongga() 완전 별도 렌더러 신설 —
        entrySignal/riskPctMiniHtml 등 R계열 헬퍼를 아예 호출 안 함(카드에
        진입판정 신호등·손절폭·2R 손익비 없음, 사용자 지시 6번). 조건 5개
        체크리스트 + 거래대금순위 + 백테스트 근거수치(backtest_note) +
        매도규칙(sell_rule) 표시. 탭 상단 고정 안전배너(jonggaSafetyBanner)
        + 시간대별 세션 안내(jonggaSessionBanner, lastScanMeta로 전달)
        추가. 클릭 시 시장필터 자동 KR 전환(단 z=4.88 전략지도 TAB_MARKET_
        LABEL과는 별개 취급 — 마켓불일치 배너 문구가 안 맞을 것이므로).
        [GUIDE] 6.5장 "종가베팅" 신설 — 조건·타이밍·매도규칙·근거수치·
        미포함(theme) 사유 기록.
        검증: card({mode:'jongga',...}) 렌더 결과에 entry-sig 클래스·
        리스크 필드 없음을 node eval로 직접 확인, analyze_jongga() 상한가
        제외/전조건통과 케이스 합성 데이터로 검증, test_scanner.py/
        test_trace_parity.py 재실행.
v5.96 [측정] RSI<50 저모멘텀 필터 US 확장 검증(사전 등록, 사용자 지시) —
        기각 확정. KR과 동일 설계(이분+시간반분 재현)로 US 돌파+박스
        돌파+추세전환에 적용한 결과 두 절반 모두 KR과 **정반대 방향**
        (이전절반 RSI<50 EV 0.729R vs RSI≥50 0.249R z=2.39 유의, 최근
        절반도 방향 동일·비유의) — 사전 기준(두 절반 모두 -0.15R 이상)
        미달로 "KR 전용 필터" 확정. 코드 변경 없음(isLowMomentumBreakout
        의 market==='KR' 조건이 이미 US를 배제하고 있었음 — 이번 측정은
        그 설계가 사후적으로 옳았음을 확인). GUIDE.md 체크리스트의
        "US 검증 중" 문구를 결과로 갱신. 근거:
        docs/kr_breakout_rsi_investigation.md "US 확장 검증" 절,
        scripts/measurements/2026-08-29_us_breakout_rsi_under50_time_split.py.
v5.95 [문서] GUIDE.md 맨 앞에 "실전 체크리스트 (1페이지)" 신설(사용자
        지시) — 측정 검증된 규칙만 시장별로 요약, 항목마다 근거 docs
        링크. 공통(ATR×1.5 손절/가격고정 자동제외/게이트색 진입판단
        제외) · KR(돌파 계열 채택 z=4.88/RSI<50 회피/RSI70+ 무죄) ·
        US(되돌림 계열/슈퍼대장 포지션 절반+ATR×2/RSI<50은 검증 중).
        코드 변경 없음 — GUIDE.md 문서만.
v5.94 [기능추가] KR 돌파 계열 저모멘텀(RSI<50) 경고 배지 구현(사용자
        지시) — 사전 등록 시간분할 독립 재현 완료 근거
        (docs/kr_breakout_rsi_investigation.md, 이전 절반 z=-3.15/최근
        절반 z=-2.97, 둘 다 -0.15R 기준 초과). static/index.html만
        수정(scanner.py/app.py 무변경 — hit["rsi"]는 analyze_breakout/
        analyze_boxbreak/analyze_turnaround이 이미 내부에서 계산해
        내려주는 기존 필드를 그대로 씀). isLowMomentumBreakout(s) 헬퍼
        신설(mode가 breakout/boxbreak/turnaround AND market==='KR'
        AND rsi<50 — US는 미검증이라 market 조건으로 배제). (1) 펼친
        카드에 "⚠️ 저모멘텀 돌파 — 손절률 66~78% (검증됨)" 배지
        (climax-tag caution 재사용). (2) 접힌 카드엔 collapsedBadgeIconsHtml
        에 ⚠️ 아이콘만 추가. (3) entrySignal() 진입 판정 신호등에 warn
        1건 반영 — RS 70~89 warn 분기와 동일 패턴(checks.push + warn++),
        게이트 아님 — 카드 제외 없음, list 필터링 미적용. (4) GUIDE.md
        6장(돌파·박스돌파)에 한 줄 추가.
v5.93 [문구수정] 게이트 조건부 EV 실측(2026-08-29, docs/pullback_ev_kr_us_
        regime_investigation.md 7절 — US z=-3.15 역방향, 게이트가 개별
        신호 EV를 예측 못 함 확인) 반영, 처방형 문구 제거(사용자 지시).
        (1) static/index.html gateOf(): "🔴 신규 진입 자제"/"🟢 진입
        환경 양호"/"🟡 선별 진입" → "🔴 지수 약세"/"🟢 지수 안정"/
        "🟡 지수 혼조"(시장 상태 서술로만, 3단계 판정 로직·분산일·FTD
        표시는 불변). (2) 게이트 펼침 상세(idx-detail)에 안내 한 줄
        추가: "ℹ️ 게이트 상태는 개별 신호의 EV를 예측하지 못함(2026-
        08-29 측정) — 진입 판단은 카드의 신호 품질(손절폭·구조)로."
        (3) app.py `_index_regime()`의 individual 지수 카드 툴팁
        regime_txt도 동일 취지로 "비우호(신규진입 자제)"→"비우호" 등
        괄호 처방구 제거(gate_suggest()의 gate_why 접미사는 R노출
        자동제안 기능이 별도로 쓰는 값이라 미변경). (4) GUIDE.md 1장
        "시장 게이트" 절에 동일 실측 캐비어트 추가 — R노출 한도 표
        (오픈리스크 3R/1.5R/0)는 포트폴리오 노출관리 규칙으로 그대로
        유효함을 명시하되 "🔴=이 카드는 나쁘다"로 해석하지 않도록.
        scanner.py의 ftd_state/dist_count/gate_suggest 판정 로직 자체는
        미변경 — 순수 표시 문구 교체.
v5.92 [UI개편] 탭 메뉴 전면 개편 + 앱 리네이밍(사용자 지시). "눌림목
        스캐너"→"돌파·눌림 스캐너"(부제 "국장은 돌파 · 미장은 눌림 —
        시장별 검증 전략") — index.html title/h1, GUIDE.md, README.md,
        /guide 페이지 title만 변경, 레포명/URL/API 경로는 그대로.
        탭에 시장 라벨 부착(내부 mode 키 불변, 표시명만): 🇺🇸눌림목/
        🇺🇸슈퍼대장/🇺🇸돌파임박, 🇰🇷돌파/🇰🇷박스돌파/🇰🇷추세전환.
        🇰🇷/🇺🇸 탭 클릭 시 시장 필터 자동 연동(setMarket) — 수동으로
        반대 시장 선택은 허용하되 그러면 카드 목록 상단에 얇은 안내
        ("이 계열은 {시장}에서 검증되지 않음 — 전략 지도 참고",
        marketMismatchBanner, market==='all'일 땐 표시 안 함). 탭 순서
        재편: [🇺🇸 3개]→[🇰🇷 3개]→[대장관찰·섹터·💰돈의흐름]→
        [인버스·붕괴]→[마감정리·내일지]→[⋯ 실험] 접기 버튼(패턴/
        Stage2/IBD9/강한피벗/실적우수/급등 6개, expTabsGroup을
        display:contents로 토글해 부모 .tabs의 flex 레이아웃 유지 —
        상태 저장 안 함, 새로고침마다 항상 접힘). 대장후보→대장관찰
        표시명 변경(MODE_TAB_LABEL·진단패널 modeNames·전환관찰 버튼
        툴팁 3곳) — 추세전환은 이름 유지(사용자 결정). 하위호환 확인:
        즐겨찾기(ticker 기준, mode 무관)·저널(저장된 mode 키 불변,
        라벨만 렌더 시점에 바뀜)·URL 파라미터(이 앱은 mode/market을
        URL에 안 실음, 확인 완료)·얼마냐봇(/api/ma/{ticker} 미변경)
        전부 영향 없음. scanner.py 게이트/EV 로직 미변경(순수 표시
        레이어), test_scanner.py/test_trace_parity.py 재실행 확인.
v5.91 [UI개선] 시장별 전략 지도(docs/kr_us_strategy_map.md, 사전 등록 합산
        검정 z=4.88) UI 반영(사용자 지시). KR에서는 돌파 계열(돌파·박스
        돌파·추세전환)이 되돌림 계열(눌림목·돌파임박)보다 EV가 유의하게
        높음(격차 +0.283R, z=4.88) — US는 계열 간 유의차 없음. (1)
        static/index.html "한국"/"미국" 시장 필터 선택 시 해당 계열
        탭에 ★ 표시(KR→돌파/박스돌파/추세전환, US→눌림목/슈퍼대장/
        돌파임박) — 탭 접근은 막지 않음, 표시만(updateModeRecommendations).
        (2) KR 히트 카드 중 눌림목·돌파임박(되돌림 계열) 진입 판정 박스에
        "ℹ️ KR 되돌림 계열 EV 0.089R — 돌파 계열(0.372R) 우선 권장 (전략
        지도)" 한 줄 추가(strategyMapNoteHtml, 진입 차단 아님·정보용) —
        US 카드는 미변경. (3) GUIDE.md 상단에 "시장별 전략 지도" 요약
        표 신설(근거 z=4.88, docs 링크). scanner.py/app.py 게이트·EV
        계산 로직은 미변경(순수 표시 레이어).
v5.90 [기능개선] 가격고정(M&A 의심) 필터 전 탭 공통화(사용자 지시) — CRNX/
        APGE(둘 다 실제 M&A 발표, 웹 검증 완료) 두 종목이 대장후보·급등
        탭에 잡히던 문제. 원인은 탐지 로직 결함이 아니라 커버리지 공백
        — 기존 merger_warning()(v4.80)이 6개 analyze_*()에만 있었고
        대장후보/급등/추세전환/돌파 4개엔 아예 없었음(직접 호출로 확인:
        CRNX/APGE가 leader/surge에서 실제 HIT). scanner.py에
        price_frozen_check() 단일 공통 유틸 신설(발표충격갭 +15%+ 동시
        ATR% 20봉 기준 0.5% 미만 — 실데이터 캘리브레이션: CRNX/APGE
        ATR% 0.09~0.12% vs 정상 주도주 10종목 ATR% 2.78~5.74%, 22배+
        마진 확인, docs/price_frozen_calibration.md) — 10개
        analyze_*() 전부가 내부에서 호출해 결과에 price_frozen 필드로
        항상 부착. v4.80처럼 완전 제외(하드 게이트)하지 않고 정보용으로만
        남김 — 표시 여부는 static/index.html이 판단해 카드가 뜨는 전
        탭에서 기본 숨김 + 탭 하단 "N개 숨김 — 펼치기" 한 줄로 전환(⚠️
        가격고정 배지 부착해서 표시, 완전 은폐 아님). RS 랭킹 계산
        유니버스는 그대로 유지(영향 없음). harness.py
        passes_liquidity_filter()가 hit.price_frozen을 같이 체크하도록
        확장해 15개+ 측정 스크립트를 하나도 안 고쳐도 v4.80과 동일하게
        EV 측정에서 계속 제외됨. 검증: test_scanner.py 0 FAIL,
        test_trace_parity.py 381 passed(_trace_*↔analyze_* 완전 일치).
v5.89 [UI개선] 💰 돈의 흐름을 헤더의 작은 링크 아이콘에서 정식 탭으로 승격
        (사용자 지시 — 아이콘이 너무 작아 존재감 없었음). static/index.html
        #modeTabs에 "💰 돈의흐름" 탭 추가(마감정리 옆) — 클릭하면 페이지
        이동 없이 기존 /moneyflow 페이지의 fmtTop/loadDate/runNow 뷰
        로직을 그대로 이식해 탭 컨텐츠 영역에 렌더링. KR/US는 별도
        버튼 없이 기존 시장 필터(전체/한국/미국)와 연동(전체 선택 시
        자동으로 한국 취급). 날짜 선택·수동 재실행 버튼, "관찰용
        정보 — 진입 신호 아님" 배너 전부 탭 안에 유지. 오늘 자 새
        리포트가 있는데 아직 안 연 상태면 탭 옆에 작은 점 표시
        (localStorage 기준, 열면 사라짐). 헤더의 💰 아이콘은 제거 —
        /moneyflow 직접 URL(GET /moneyflow)은 얼마냐봇 텔레그램 링크가
        그리로 가고 있어 그대로 유지, API(/api/moneyflow/*)도 무수정.
v5.88 [문서] US 눌림목(무필터 기본 탭) EV 시간분할 재현 확인(사용자
        지시) — GUIDE.md "US 종목 위주로 신뢰" 권고에 캐비어트 반영
        (UI 미반영, v5.84와 같은 패턴). US 단독 눌림목 EV +0.206R
        (n=1,333, 오늘 재현)을 측정기간 절반으로 나누면 이전 절반
        (off160~250) +0.334R vs 최근 절반(off60~150) +0.029R로 사실상
        무신호 — 3절 슈퍼대장 KR 사례와 반대로 "최근에 약해진 패턴".
        사전 판정 기준(둘 다 +0.1R 이상 → 신뢰 근거 vs 한쪽이라도 미달
        → 캐비어트)에서 후반부 미달로 캐비어트 확정.
        매집봉 필터 재현 확인 2건 완료: ① offset 20/40/60 개별 재측정
        (n_without=1로 검정력 없어 절차적 기각) ② 원측정 코호트(20
        체크포인트 풀링) 재수집 후 시간순 전반부/후반부 반분 재측정
        (전반부 gap=-0.034R 역방향, 후반부 gap=+0.055R로 0.1R 미달) —
        ②가 검정력 있는 최종 판정: 기각 확정, 🕯️ 뱃지 도입 안 함.
        docs/maejip_candle_filter_kr.md·docs/pullback_ev_kr_us_regime_investigation.md
        갱신. scanner.py/app.py 로직·static 무수정.
v5.87 [신규] 얼마냐봇 연동용 GET /api/moneyflow/{market}/summary 엔드포인트
        (사용자 지시). 최신 리포트의 날짜/강한 테마 3개/약한 테마 3개/최종
        한 문장을 JSON으로 반환 — 봇이 마크다운 본문을 정규식으로 긁지
        않고 모델이 직접 낸 구조화 데이터를 그대로 쓰게 함.
        [발견] MAX_TOKENS=8000이던 2단계(Claude) 응답이 KR/US 리포트 둘
        다 9번 섹션("핵심 뉴스") 근처에서 항상 잘려 10번(최종 한 문장)·
        11번(데이터 검증)이 한 번도 생성된 적이 없었음(실측 확인) — 봇이
        의존할 "최종 한 문장"이 애초에 안 나오는 구조였음. 16000으로 상향.
        docs/money_flow_prompt.md에 "12. 기계 판독용 요약" 섹션 추가 —
        리포트 맨 끝에 strong_themes/weak_themes/final_sentence를 담은
        JSON 코드블록을 모델이 직접 내도록 지시. money_flow_report.py에
        extract_summary() 추가(정규식으로 마지막 ```json 블록 파싱).
v5.86 [수정] money_flow.py 1단계 저장 크래시 버그 수정. `_attach_kr_mcap`의
        `micro_cap` 판정(`mcap < MICRO_CAP_EOK * 1e8`)이 investor_flow.py
        pandas 연산 결과인 `numpy.float64`끼리 비교돼 `numpy.bool_`을
        반환 — 파이썬 `bool`의 서브클래스가 아니라 `json.dump`가
        "Object of type bool is not JSON serializable"로 매 실행 크래시
        (v5.85 배포 이후 KR 리포트가 한 번도 저장된 적 없었음). `bool(...)`
        로 감싸 수정. 수동 실행(KR)으로 스냅샷 저장·2단계 markdown 생성
        확인.
v5.85 [신규] 💰 돈의 흐름 데일리 리포트 (사용자 지시) — "투자하는범" 3단계
        방법론(거래대금→가격→테마)의 자동화. **진입 신호가 아니라 정보
        레이어** — scanner.py 매매 신호와 완전히 분리(scanner.py 무수정).
        [1단계: money_flow.py 신규 모듈] KR/US 각각 거래대금 상위 100
        추출(기존 `_fetch_market_data` 데이터 재사용, 새 스크래핑 없음),
        등락률·전일대비 순위변화, 섹터/테마 분류(미분류 처리), 테마별
        집계(종목수/breadth/평균등락률/거래대금점유율+전일대비변화),
        ±10% 급등락 표시, KR 초소형주 플래그(시총 1000억 기준,
        investor_flow.py 재사용 — US는 시총 소스 없어 None+한계 명시).
        [테마 지속성(streak)] 거래대금 점유율 상위 10위 이내 연속 등장
        일수 — 아무 테마나 매일 top100에 최소 1종목은 있어 무조건
        "등장"으로 치면 무의미해지므로 순위 컷으로 "눈에 띄게 강한
        테마"만 추적, 컷 밖으로 밀리면 리셋.
        [테마 내부 서열판+확산 단계] 테마 내 top100 편입 종목을 거래대금
        순 대장주/2등주/3등주로 서열화 + 4단계 규칙 판정: 초기(대장주만)
        /확산(본격, 대장주+후발주 동반상승)/말기 경계(후발주만)/비확산.
        일자별 JSON 저장(영구볼륨 우선).
        [2단계: money_flow_report.py 신규 모듈] docs/money_flow_prompt.md
        (사용자 제공 원문 그대로 저장 + streak·확산단계를 자금이동 판단
        핵심 근거로 쓰라는 지시 추가) 프롬프트로 Claude API(claude-sonnet-
        4-6, web_search 툴 활성화) 호출해 마크다운 리포트 생성. 키
        없음/패키지 미설치/API 실패는 예외 없이 (None, 에러메시지) 반환
        — 호출부가 1단계 결과만으로 항상 응답 가능(사용자 지시).
        [3단계: /moneyflow 페이지] KR/US 탭 + 날짜 선택 + 수동 재실행
        버튼, 상단 "관찰용 정보 — 진입 신호 아님" 배너. AI 해석 있으면
        marked.js 렌더, 없으면(생성 실패) 1단계 JSON을 표로 폴백 렌더.
        헤더에 💰 링크 추가(📖 가이드 링크 옆).
        [스케줄러 연동] 기존 `_warm_market`(장마감 후 1회 워밍)에 독립
        추적(`_moneyflow_warmed`)으로 연결 — 스캔 워밍과 서로 실패 전파
        안 됨. Claude 호출이 오래 걸릴 수 있어 `create_task`로 던져
        4분 주기 스케줄러 루프를 안 막음. API: GET/POST /api/moneyflow/
        {market}(/run).
        requirements.txt: beautifulsoup4(전전 버전 누락 보완), anthropic
        추가. ANTHROPIC_API_KEY는 .env/Railway 환경변수로 사용자가 직접
        설정 필요(본 배포에 값 없음 — 2단계는 설정 전까지 항상 폴백).
        검증: TestClient로 3개 엔드포인트 실제 호출(POST run/GET
        with·without date/invalid market 400) + money_flow.py 합성
        데이터로 streak 연속성·확산단계 4가지 케이스 단위 검증.
v5.84 [문서] 슈퍼대장 EV — KR/US 혼합 착시 캐비어트 반영(사용자 지시,
        2026-08-26 KR/US 분해 조사 후속). 눌림목 슈퍼대장 소속 EV
        0.266은 KR+US 혼합 수치로 추정 — KR 단독 재현은 -0.214R(역방향,
        n=103), US 단독은 +0.346R로 확인(z≈3.36, 매우 유의). 기간
        분해(최근/이전 절반)에서 최근 악화 신호 없음 — 표본 전체에
        걸친 지속 패턴.
        GUIDE.md "👑 슈퍼대장 활용법"(v5.83) 우선순위1에 "⚠️ KR 종목
        주의" 문단 추가: KR 종목엔 이 필터를 진입 근거로 쓰지 말 것
        명시.
        docs/all_tabs_common_yardstick_investigation.md에 "눌림목/
        슈퍼대장 EV — KR/US 혼합 착시 재논쟁 방지 노트" 추가(추세전환
        재논쟁 방지 노트와 같은 형식) — 혼합 단일 수치로 결론 내지
        말 것을 명시.
        scripts/measurements/README.md 규칙8 추가: KR+US 혼합 코호트는
        시장별 분해를 반드시 병기.
        UI(필터·뱃지)는 아직 미반영 — 표본·견고성(시드/기간)을 더 본
        후 결정. scanner.py 무수정, static/index.html은 verBadge만 변경.
v5.83 [문서] 👑 슈퍼대장 활용법 — 실측 기반 3단계 우선순위 문서화(사용자 지시).
        GUIDE.md 3장(대장후보·슈퍼대장)에 "👑 슈퍼대장 활용법" 절 추가:
        ①눌림목 [👑]필터 주력(EV 0.266 vs 무필터 0.172, 손절폭도 자연히
        좁음) ②워치리스트 공급원(대장후보 74%가 중앙값 6봉 내 눌림목
        전환 — 👁관찰 등록 후 대기) ③탭 직접진입은 조건부(ATR×2 손절
        EV 0.641로 수치상 최고지만 손절폭 median 10.3%가 통상 규율과
        충돌 — 쓰려면 포지션 절반). buy_zone 대기(안1, EV 1.22)는
        생존편향(60봉 내 27.9% 미도달, 하이브리드 EV 0.26)으로 폐기
        확정, 재논쟁 불필요 명시. 근거: docs/all_tabs_common_yardstick_
        investigation.md, docs/leader_to_pullback_watch.md.
        슈퍼대장 탭 상단 설명(DESC.super)에도 한 줄 요약 추가: "활용:
        ①눌림목 [👑]필터(주력) ②👁관찰 등록 후 눌림목 전환 대기 ③직접
        진입 시 포지션 절반 (ATR×2 손절, 가이드 참고)" — 매매 중 가이드
        전체를 안 열어도 바로 우선순위를 상기할 수 있게.
        scanner.py 무수정.
v5.82 [버그수정] 카드 접기/펼치기 도입(v5.80) 후 끊어졌던 트레이딩뷰 차트
        연결 복구 — 종목명 클릭으로 열도록 재설계.
        [원인] v5.80에서 접힌 카드(card-collapsed)가 기본 노출 상태가
        됐는데, 트레이딩뷰 링크(tv-link)는 펼쳤을 때만 보이는
        card-expanded 안 티커(.tick) 줄에만 있었음 — 접힌 상태에서는
        차트로 갈 방법이 아예 없었음(종목명은 평범한 span).
        [수정] collapsedRowHtml()/collapsedRowHtmlInverse()의 종목명
        span과 card()/breakdownCard()/인버스 카드 expanded의 .name
        안 종목명을 전부 <a href="tvUrl(s)">로 교체 — 접힌 카드·펼친
        카드 양쪽 다 종목명 클릭 시 새 탭으로 트레이딩뷰 차트 열림.
        새 .name-link 클래스(color:inherit·밑줄 없음 기본, hover 시만
        #7fb8e0 색+밑줄)로 평소엔 기존 텍스트와 시각적으로 동일하게
        유지. 행 펼침/접힘 클릭 가드(bindCardInteractions)에 .name-link
        를 명시 추가(a 태그라 이미 걸러지지만 의도를 코드에 남김) —
        종목명 클릭이 행 토글로 전파 안 됨. 기존 티커 줄의 📈 차트
        링크(.tv-link)는 손대지 않아 그대로 작동.
        검증: Node로 card()/breakdownCard() KR·US 출력에서 종목명
        링크 href가 tvUrl()과 일치, name-link 2회(접힘+펼침) 존재,
        기존 tv-link 여전히 존재 확인. html.parser 태그 균형(에러 0),
        중복 id 없음, pytest 383개 통과. scanner.py 무수정.
v5.81 [측정+UI] Stage2 "검증실패" 뱃지 정정 — 8/17 캠페인 마무리.
        [배경] 2026-08-25_stage2_liquidity_matched_control.py(seed=42
        단발)가 완전무작위 대조군 대비 하락위험 열위(+4.9pp)를
        유동성매칭 대조군(+0.7pp)으로 재측정 시 사실상 무차이로 축소—
        단발 시드라 우연인지 확인 필요했음.
        [5시드 견고성 확인] 2026-08-25_stage2_liquidity_matched_control_
        multiseed.py(seed 42/1/7/123/2026, 유동성컷→RS→템플릿 판정은
        체크포인트당 1회만 계산해 캐싱하고 대조군 추출만 시드별 반복).
        결과: 상승우위 5/5 시드 전부 양수(평균 +9.02pp, 범위
        7.52~11.76pp) — 견고. 하락위험차이는 부호가 시드마다 흔들림
        (평균 -0.85pp, 범위 -3.92~+3.59pp) — 견고하지 않음(사실상 무차이).
        [뱃지 변경] "검증실패"(danger 빨강) → "참고"(exp-tag 기본 보라).
        Stage2 카드에 신규 경고 문구 추가: "대조군 대비 약한 우위(+9pp),
        진입 신호로는 미검증" — 상승우위는 확인됐지만 하락위험차이가
        불안정하다는 점, 후보 발굴 참고용이라는 점 명시.
        [방법론 규칙 추가] scripts/measurements/README.md 규칙6: 대조군은
        반드시 대상 탭과 동일 유동성 컷 통과 종목에서 시점매칭 추출
        (완전무작위 금지) — 이번 추세전환(EV 0.362→0.211 과대)·Stage2
        (하락열위 +4.9pp→+0.7pp 착시) 두 사례가 근거.
        [문서] all_tabs_common_yardstick_investigation.md에 "추세전환
        '가장 큼' 재논쟁 방지" 노트 추가(원 0.362는 부풀려진 코호트,
        실측 0.211로 중위권). pullback_stop_width_and_entry_timing.md에
        5시드 결과·뱃지 변경 근거·시드 재현성 캐비어트(동일 seed=42라도
        별도 프로세스 실행 간 결과가 다를 수 있음 — KR 유니버스 fetch가
        ThreadPoolExecutor+as_completed라 종목 순서가 실행마다 달라져
        random.sample 표집이 seed만으론 재현 안 됨, 하네스 확장 과제로
        기록) 절 추가.
        scanner.py 무수정 — 측정 스크립트+UI만 변경.
v5.80 [UI] 접힌 카드 개선 2건(사용자 지시).
        (1) 같은 행 동시 펼침 — 카드 개별 펼침을 없애고, 클릭하면 그
        카드가 속한 그리드 행(현재 열수만큼) 전체가 같이 펼쳐지고 다시
        클릭하면 행 전체가 접힘. 행 판별은 인덱스 기반(rowStart =
        floor(index/열수)) — 열수는 getComputedStyle(gridEl).
        gridTemplateColumns를 클릭 시점에 실시간으로 파싱해서 구함(JS에
        breakpoint 값을 하드코딩 안 해서 반응형 CSS가 바뀌어도 안 깨짐).
        추가로 펼친 채 창 크기가 바뀌어 열수가 달라지면(행 구성 자체가
        깨짐) resize 이벤트(디바운스 150ms)로 전체 카드를 강제로 접음
        (collapseAllCardsIfColumnCountChanged) — 어정쩡한 상태로 안 남게.
        (2) 접힌 카드 1층 종목명 오른쪽에 가격+등락률 추가(cc-price 기본
        색·보통굵기 + cc-chg 11px 파스텔 초록/빨강 — RS·점수보다 시선을
        덜 끌게, risk%와 같은 채도 낮춤 철학). 종목명은 flex:1(무제한
        확장) 대신 max-width:84px로 바꿔서 가격이 이름 바로 옆에 붙게 함.
        숫자 정렬(고정폭+tabular-nums) 원칙은 기존 RS/리스크%/점수 컬럼에
        그대로 유지, 가격/등락률도 tabular-nums만 적용(고정폭은 안 씀 —
        통화·자릿수가 종목마다 달라 표처럼 맞출 대상이 아님). 인버스
        카드는 원래 가격 표시가 없던 설계라 이번 변경 대상에서 제외.
        검증: Node로 행 그룹핑(3열 8개 카드, 인덱스4 클릭→3,4,5행 펼침),
        같은 행 재클릭 시 전체 접힘, 마지막 부분행(6,7 두 개뿐) 클릭 시
        범위 안 벗어남, 열수 변경 시 전체 강제 접힘 4가지 시나리오 전부
        모의 DOM으로 실측 확인. 가격/등락률은 KR("13,840원"/+1.23% 초록)·
        US("$87.98"/-0.55% 빨강) 양쪽 다 card() 직접 실행해 출력 확인.
        html.parser 태그 균형(에러 0), pytest 383개 통과.
v5.79 [UI+버그수정] 접힌 카드 2층의 🟢🔴가 진입판정(entrySignal)인지
        리스크%인지 구분 안 되던 문제 수정(사용자 제보 — 한국알콜: 접힌
        카드 🔴인데 펼치면 🟡, 리스크 4.26%는 ATR 4.2%×1.5=6.3% 이내라
        초록이어야 함). 원인 ①: entrySigMini(구 entryIconMini)가 3단계
        신호등(🟢/🟡/🔴)을 2단계(🟢/🔴)로 압축해서 warn(🟡)이 🔴로 잘못
        보임 — 압축 없이 펼친 카드와 동일한 3단계로 복원. 원인 ②: 이
        아이콘이 "리스크" 필드 안에 같이 묶여 있어서 리스크 탓처럼 보임 —
        cc-entry를 리스크 필드 밖으로 완전히 분리(독립 항목). 리스크%
        색상 로직 자체(ATR×1.5 파스텔 초록/주황)는 검증 결과 버그 없음
        (Node로 risk_pct=4.26/atr_pct=4.2 직접 계산 — 정상적으로 초록
        반환, threshold=6.3 정상 적용).
        ⚠️ 검증 중 이 사고와 무관한 별도 버그 2건 추가 발견 — 사용자 승인
        받아 같이 수정(내일부터 🟡 판정이 늘어나 보이면 이 커밋이 원인):
        (a) entrySignal()의 RS 체크에서 danger 분기(RS<70)엔 `if(rsActive)
        danger++`가 있는데 대응하는 warn 분기(70≤RS<90, "주도주는 아님")엔
        `warn++`가 아예 빠져 있어서, checks 배열엔 warn으로 찍히면서도
        최종 판정엔 반영이 안 됐음(RS 70~89 종목이 전부 🟢으로 새어나감)
        — RS가 활성 항목인 pullback/imminent/turnaround/boxbreak/pattern
        탭 전체에 영향. warn 분기에 `if (rsActive) warn++` 추가.
        (b) 같은 함수의 리스크 체크가 `s.market === 'kr'`(소문자)로 비교해
        실제 값 'KR'(대문자)과 항상 불일치 — 한국 종목이 12% 기준 대신
        미국 기준(8%)으로 판정되고 있었음. 'KR'(대문자)로 수정 — 리스크
        8~12% 사이였던 한국 종목들이 앞으로 ok로 바뀔 수 있음.
        (2) 카드 최소폭 230px로 추가 축소(3열 유지), breakpoint도 800px로
        재계산.
        검증: Node로 RS=75 단독 재현(수정 전 checks=warn/level=ok 버그
        재현 → 수정 후 level=warn/🟡 확인), KR risk_pct=10%가 12% 기준
        적용돼 ok로 바뀌는 것 확인, US risk_pct=10%는 8% 기준 그대로 warn
        유지(회귀 없음) 확인. html.parser 태그 균형(에러 0), pytest 383개
        통과.
v5.78 [UI] v5.77 접힌 카드가 한 줄에 다 욱여넣어 위계 없이 산만하다는
        피드백 → 2층 구조로 재설계. 1층(cc-row1)은 종목명(15px bold,
        눈의 앵커)·☆✕·시장뱃지·🔥🚀👑·▸로 정체성 줄, 2층(cc-row2)은
        스파크라인(80×24, 기존 2배)·"RS"/"리스크" 라벨(9.5px muted)+값·
        점수(우측끝)로 수치 줄. 라벨을 붙여 숫자 해석 부담 감소. 숫자
        컬럼은 여전히 width 고정+tabular-nums로 세로 정렬 유지(v5.77
        방침 이어감). 카드 최소폭 330→260px 축소(2층이라 가로 공간 덜
        필요, 열은 여전히 최대 3 — 2열 전환 breakpoint도 1050→900px로
        같이 낮춤, 폭 남으면 카드가 넓어짐), grid gap 14→19px·mobile
        gap도 통일해서 12→17px, 카드 패딩 9~12px→12~16px(mobile
        7~12px→10~14px)로 여유. 펼침/접힘 토글·버튼 전파 가드(bindCard
        Interactions)는 구조 변경과 무관하게 그대로 동작(closest() 가드가
        DOM 깊이에 안 걸림). 인버스 카드는 스파크 데이터가 원래 없어
        2층에서 강도/5일등락/레버리지만. 검증: Node로 card()를 직접
        실행해 cc-row1/cc-row2/cc-name-lg/cc-field×2/라벨 문구 확인,
        html.parser 태그 균형(에러 0), pytest 383개 통과.
v5.77 [UI] v5.76 접힌 카드 뷰 시각 문제 2건 수정.
        (1) 펼침 시 빈 공간: .grid에 align-items:start 추가 — 그리드 행이
        가장 큰 아이템(펼친 카드) 높이로 트랙 사이징되는 건 CSS Grid 특성상
        불가피하지만, align-items:start로 짧은 형제 카드가 그 트랙 높이까지
        늘어나 빈 테두리 박스를 그리는 것만 막음(카드 자체 높이는 그대로,
        옆에 여백만 생김 — 요청한 "빈 공간만 없으면 됨" 기준 충족).
        (2) 가독성 3건: (a) 데스크톱 그리드 auto-fill(무제한 열) →
        repeat(3, minmax(330px,1fr)) 고정 3열, 1050px 이하는 2열(화면
        넓으면 카드가 넓어짐, 열이 늘지 않음) (b) 카드 기본 테두리를
        var(--line)(남색 톤) 대신 무채색 진회색(#33363d, 배경보다 살짝
        밝음)으로, 강조 테두리는 fired·fav·👑슈퍼대장(신규 .super-hl)에만
        (c) 접힌 카드 컬럼을 max-width→width 고정으로 바꿔 표처럼 정렬
        (종목명+국가는 .cc-namewrap로 묶어 폭 고정, RS·리스크%·점수는
        tabular-nums+고정폭 우측정렬) (d) 리스크% 초록/주황을 파스텔톤
        (#8fd6ac/#f0c479)으로 채도 낮춤 — "색상은 유지"라 톤만 조정, 로직
        불변. 검증: Node로 super_hl 샘플 데이터를 card()에 직접 실행해
        super-hl 클래스·cc-namewrap 구조·파스텔 그린 색상 확인, Python
        html.parser 태그 균형(에러 0)+중복 id(0건), pytest 383개 통과.
v5.76 [UI] 상단 영역(카드 시작 전까지) 전면 압축 — "폰에서 스크롤 없이 첫
        카드" 목표. ⚠️ 게이트 신호등 판정 로직(loadIndices의 gateOf: 분산일·
        레짐→lv/문구)은 한 글자도 안 건드림, 표시 위치만 재배치.
        (1) 게이트 배너 3개(문장형)+지수 카드 6개 → "코스피🟢 코스닥🟡
        나스닥🔴" 요약 한 줄(idx-summary). 탭하면 기존 배너 문장+지수 카드
        전부(닛케이·비트코인 포함, 이 둘은 요약엔 안 나오고 펼침에만 있음)
        idx-detail로 펼쳐짐, 다시 탭하면 접힘 — idxExpanded 변수만으로
        관리, 저장 안 함(단 5분마다 도는 loadIndices 재호출 시 사용자가
        펼쳐둔 상태는 유지되게 변수를 재사용, 새로고침해야 초기화).
        (2) 손절폭 칩 4개(상시노출) → "손절폭 ▾"/"손절폭(N) ▾" 드롭다운
        하나로 흡수(.dropdown-wrap, position:absolute 플로팅 패널). 필터
        로직(riskTierFilter Set·RISK_TIERS)은 그대로, 바깥 클릭하면 닫힘.
        (3) 스캔 통계 줄(종목수/기준시각/스캔시간/경보)을 검색창 아래로
        이동 + 폰트·패딩 축소해 보조 텍스트 한 줄로. 경보 클릭→관리 패널
        동작 불변.
        검증: Python html.parser 전체 파일 태그 균형(에러 0)·중복 id
        검사(0건), Node에 loadIndices()·renderRiskTierFilter()를 목(mock)
        DOM+fetch로 직접 실행해 요약 문구(🟢/🟡/🟢/🔴 매핑)·닛케이·비트코인
        위치·접힘↔펼침 토글 왕복·드롭다운 열림/닫힘·칩 4개 모두 확인.
        ⚠️ 이번 턴은 Chrome 확장이 연결 안 돼 있어 실제 모바일 뷰포트
        스크린샷으로는 최종 확인 못함(로컬 uvicorn까지는 띄웠으나 브라우저
        자동화 불가) — 대신 v5.75(섹터 칩)+이번 압축분 높이를 각 요소
        CSS로 역산: 헤더 하단부터 카드 시작까지가 기존 대비 대략 200px+
        절감 추정(게이트 영역 ~140→~30, 손절폭 줄 ~45→0(기본 접힘),
        상태줄 ~45→~24). 배포 후 실기기 확인 권장.
v5.75 [UI] 섹터 칩 세 줄 → 한 줄 압축. 기본은 종목 수 상위 6개 칩만
        (백엔드 Counter.most_common()이 이미 count 내림차순이라 그대로
        slice), 줄 끝 "+N개 ▾"로 전체 펼침/"접기 ▴"로 되접기 — 펼침 상태는
        변수로만 관리하고 저장 안 해서 새로고침하면 항상 접힘부터 시작.
        접힌 상태에서 상위 6개 밖의 섹터가 이미 필터로 선택돼 있으면
        예외로 같이 보여줘서 "선택은 돼 있는데 꺼줄 방법이 없는" 상황을
        막음. 각 칩 배경에 종목 수 비례 채움 바 추가(최다 섹터=100% 기준
        상대 %, 인라인 linear-gradient) — 선택 표시는 배경 대신 테두리
        색(amber)으로 옮김(인라인 배경이 우선순위가 더 높아 기존 방식대로
        배경을 못 바꿔서). 컨테이너는 flex-wrap:nowrap+overflow-x:auto로
        바꿔 데스크톱/모바일 공통으로 가로 스크롤(줄바꿈 안 함). Node로
        renderSectors()를 직접 실행해 접힘(6+토글)/펼침(전체+토글)/
        상위밖 필터 예외 3가지 시나리오 전부 실측 확인.
v5.74 [UI] 스캐너 카드 접기/펼치기 — 기본 접힘, 클릭하면 펼침(다시 클릭하면
        접힘). 목적: 한 화면에서 많은 종목을 빠르게 훑고 관심 가는 것만
        펼쳐보기. 접힌 카드는 한 줄 요약(☆·✕·종목명·시장·미니스파크·RS·
        진입신호등(🟢/🔴, warn/danger는 🔴로 압축)·리스크%(ATR×1.5 기준
        초록/주황, ATR 무효 시 고정 US8%/KR12% 폴백)·점수·🔥/🚀/👑 아이콘)만
        표시. ☆/✕/차트링크/일지버튼 등 클릭은 closest() 가드로 펼침 토글에
        안 걸리게 분리. 펼침 상태는 클래스로만 관리하고 어디에도 저장 안
        해서 재스캔·새로고침마다 항상 접힘부터 시작. card()/breakdownCard()/
        인버스 인라인 카드 3곳 전부 적용 — 인버스는 RS/risk_pct/score/
        entrySignal 개념 자체가 없어 강도(inv_score)·과열여부·5일 등락·
        레버리지로 대응 매핑. 기존 card-top 이하 전체 마크업은 그대로 두고
        `<div class="card-expanded">`로만 감싸는 방식으로 구현해 회귀
        위험 최소화 — Python html.parser로 전체 파일 태그 균형 확인(에러 0),
        Node에 카드 렌더 함수를 직접 실행해 imminent/breakdown 샘플 데이터로
        collapsed+expanded 마크업 정상 생성 확인.
v5.73 [배포] /guide, /guide.md에 no-cache 헤더 추가 — 사용자가 v5.72 배포
        후 돌파임박 탭에 숨김 X버튼이 안 보인다고 제보. 실제로는 코드
        문제가 아니었음(프로덕션 raw HTML을 curl로 받아 로컬과 diff =
        완전 동일, card() 함수를 Node로 직접 실행해 imminent 데이터로도
        hide-btn이 정상 생성됨을 확인) — 원인은 "/"가 이미 no-cache
        헤더(v5.13)를 달고 있는데도 브라우저가 강하게 캐싱해 Cmd+Shift+R로도
        안 풀리고 개발자도구 "캐시 비우기"로만 해결되던 현상. "/"·/sw.js는
        이미 _NO_CACHE_HEADERS 적용 중이었지만 /guide·/guide.md는 헤더가
        아예 없어 브라우저 기본 휴리스틱 캐싱에 노출돼 있었음 — 동일
        헤더로 통일. API 응답(/api/*)은 기존 미들웨어가 이미 전부 커버.
v5.72 [UI+API] 스캐너 카드에 종목 숨김 기능 추가 — 카드 X 버튼(☆ 옆)으로
        즉시 숨기면 모든 탭에서 표시만 안 됨(스캔/게이트/저널/하네스 로직은
        무영향, 순수 표시 필터). 90일 자동 만료, 필터 칩 줄의 "🙈 숨김 N"
        버튼에서 남은 일수 확인 + 즉시 복구 가능. 서버 저장은 favorites와
        동일 패턴(_resolve_persistent_path, /data 볼륨) — hidden_user.txt에
        티커/숨긴시각/이름 기록. API: POST/DELETE /api/hidden/{ticker},
        GET /api/hidden(남은 일수 포함, 조회 시 만료분 자동 정리).
v5.71 [CONFIG+게이트] 눌림목 RS 게이트를 E=A(12개월 RS≥80) OR B(3개월
        RS≥80) OR C(RS≥50 且 20거래일 전 대비 랭크+25 이상)로 확장, 눌림폭
        게이트를 고정%(1.5~15%)에서 ATR 상대배수 depth_atr∈[0.5,3.0]로
        교체. 계기: MSTR/BMNR/CRCL/PLTR 같은 주도주가 눌림목 탭에 전혀 안
        잡히는 문제 진단(scripts/measurements/reject_tracer.py) — 4종목
        전부 rs_min=80 미달이 사유(RS 계산불가 아니라 실제로 낮음, 3종목은
        12개월/3개월/모멘텀 세 관점 다 약함 — 구조적으로 이 시스템 대상이
        아님을 확인). 후속 측정(2026-08-23_reject_tracer_rs_variants.py +
        _ev_and_gate_e.py, harness 2R 레이스)에서 E\\A 증분 EV 0.235R(n=869)
        · depth_atr 증분 EV 0.194R(n=480)가 현행 A 단독 EV 0.108R보다
        우수해 채택. 눌림목 탭에만 적용(다른 탭 미변경). 통과 경로를
        `rs_path`("12M"|"3M"|"momentum") 필드로 노출, UI에 🔥단기주도/
        🚀랭크급등 배지 추가(12M은 기존 표시 유지, 별도 필터 없이 목록에
        섞어 표시). rs_3m/rs_delta는 app.py `_compute_rs_ranks` 헬퍼로
        전체 유니버스 기준 계산(RS 계산 자체를 재사용해 물리적으로 한 곳,
        20거래일 전 트렁케이션 벤치마크 상수는 균일 차감이라 순위 불변이라
        오늘 값 재사용 — harness.py의 US 벤치마크 생략과 같은 근거).
        `_trace_pullback`도 동기화(CLAUDE.md 리터럴 사본 원칙).
        근거·수치 전체: docs/rs_gate_e_and_depth_atr_v5.71.md.
v5.70 [측정+CONFIG] 돌파·박스돌파 RS 역방향 심층 조사 — v5.69에서 나온
        부가 발견("돌파·박스돌파는 75-80이 오히려 최고치, 90+에서 비단조")이
        신호등에서 돌파 RS를 뺀 이유(ok군 0.232R < warn군 0.482R)와 같은
        방향이라는 지적을 받고 후속 측정. 새 스크립트
        `scripts/measurements/2026-08-14_breakout_boxbreak_rs_reversal_deep_dive.py`
        (하네스 재사용, 53초 완료). (1) rs_min을 60까지 실측(외삽 아님) —
        75 밑으로는 개선 없음(70-75 돌파 0.035R로 급락, 60-65/65-70 일부
        음수) — 75가 실제로 꺾이는 지점. (2) 임계값별 누적 비교 — 75로
        낮추면 EV와 히트 수가 둘 다 오름(돌파 0.212→0.248R + 19.1→25.0건/일,
        박스돌파 0.161→0.196R + 15.9→21.2건/일), 70 이하는 다시 하락 — 75가
        자연스러운 하한. (3) extended 가설 확인 — RS 구간별 200일선 이격
        (ext200_pct) median이 16.6%(60-65)→72.0%(95-99)로 강한 단조 증가,
        RS 높은 돌파 히트일수록 이미 많이 오른 상태라 EV가 나빠지는
        것으로 보임(RS 자체보다 확장도가 진짜 원인일 가능성).
        **반영**: BREAKOUT_CONFIG/BOXBREAK_CONFIG의 rs_min 80→75
        (IMMINENT_CONFIG는 80 유지 — 이 탭만 75-80이 최저치). 세 탭이
        공유하던 임계값을 처음으로 분리. ext200을 직접 게이트로 쓰는 안은
        범위 밖이라 보류(다음 라운드 후보). 근거·전체 표:
        docs/pullback_stop_width_and_entry_timing.md 6절.
v5.69 [측정] rs_min 85→80(v5.60, breakout/boxbreak/imminent) 재측정 —
        v5.68에서 매긴 재측정 우선순위 3위. 새 스크립트
        `scripts/measurements/2026-08-14_rs_min_bucket_ev_breakout_boxbreak_imminent.py`
        (v5.68의 공통 하네스 재사용, 오늘 이미 받아둔 유니버스 데이터로
        100초 만에 완료) — CONFIG rs_min을 측정 전용 사본에서만 70으로
        낮춰 75-80/80-85 구간이 함수 내부 게이트에 안 걸리게 한 뒤, 실제
        rs_rank로 5구간(75-80/80-85/85-90/90-95/95-99) 사후 분류해 2R
        레이스 EV 비교. 결과: v5.60 결정의 핵심 근거("80-85가 85-90보다
        EV 같거나 높음")가 **3개 탭 전부 재현**(돌파 0.309R>0.186R,
        박스돌파 0.171R>0.121R, 돌파임박 0.262R>0.236R, 전 구간 n≥30) —
        rs_min=80 유지. 부가 발견: 돌파·박스돌파는 75-80이 오히려 전
        구간 최고치인 반면 돌파임박은 75-80이 최저치라, 세 탭이 공유하는
        단일 rs_min=80이 돌파임박엔 딱 맞고 다른 둘엔 다소 보수적일 수
        있음(탭별 분리는 다음 라운드 후보로 남김, 지금 당장 문제는 아님).
        scanner.py의 v5.60 주석 3곳에 재현 결과 추가. 상세:
        docs/pullback_stop_width_and_entry_timing.md 5절.
v5.68 [측정인프라+기능] v5.67의 진짜 원인("Script A 원본이 저장소에 안
        남아있어 대조 불가")이 이 조사 하나만의 문제가 아니라, 오늘 기준
        최소 5개 결정(패턴탭 검증실패 라벨/신호등 U/D 제거/tightening·
        vol_dry 조정/rs_min 85→80/슈퍼대장 진입좌표)이 같은 이유로 재현
        불가 상태라는 지적을 받고 재발 방지 조치.
        (1) `scripts/measurements/` 신설 — 결정 근거가 되는 측정은 스크립트를
        커밋하는 관례 도입(README.md에 규칙 + 기존 7개 docs 전수 감사표,
        오늘 것 빼고 전부 재현 불가로 확인). (2) `scripts/measurements/harness.py`
        신설 — 유니버스 fetch/체크포인트 RS재계산/저유동성 필터/2R레이스를
        공용화해 "스크립트마다 제각각 구현하다 기준선이 갈리는" 유형의
        사고를 구조적으로 차단. v5.67 스크립트를 이 하네스로 리팩터해
        재실행 → 리팩터 전과 완전 동일(부동소수점까지 일치) 확인.
        (3) 5개 결정에 재측정 우선순위 부여(신호등 U/D·패턴탭 라벨 1·2순위 —
        실거래 알림 영향 크고 사용자도 최우선 지목; rs_min 85→80은 3순위 —
        오늘 뒤집힌 손절폭 타이어 비교와 같은 "인접구간 이진비교" 구조라
        재현성 의심이 제일 구체적; 슈퍼대장 진입좌표 4순위 — 다중조건
        단조패턴이라 상대적으로 튼튼; tightening/vol_dry 5순위 — 효과
        크기 작거나 이미 절충 반영). 근거·표: docs/pullback_stop_width_and_entry_timing.md
        "Script A 기반 결정 재측정 우선순위" 절. 각 영향 문서 상단에도
        재현 불가 노트 추가.
        (4) ③ 관찰 트리거를 눌림목으로 확장(구현 완료, 지난 라운드엔 측정만
        하고 보류했던 것) — `analyze()`(눌림목)에 `signal_high` 필드 추가,
        `static/index.html` 저장 로직의 `mode==='imminent'` 단일 체크를
        `TRIGGER_WATCH_MODES` Set(`imminent`,`pullback`)으로 교체. 확인
        판정/추적 로직은 이미 trigger_price 유무로만 판정하는 모드 무관
        구조라 그대로 재사용 — 새 UI 없이 저장 조건만 확장.
v5.67 [측정] v5.66 눌림목 재측정치의 기준선 불일치 조사. 재측정한 눌림목
        전체 EV(0.286R)가 기존 all_tabs_common_yardstick_investigation.md의
        Script A 기록(0.172R, n=1787 vs 재측정 n=1648)과 66% 차이가 나서
        "필터 방향 결론보다 이게 급하다"는 지적을 받고 원인을 특정하려
        시도. 확인 결과: 체크포인트/레이스 스펙은 방법론 문서 원문과 항목별
        대조 일치, RS 계산은 app.py `_fetch_market_data_inner`와 나란히
        재현해 3512종목 전원 랭크 일치, 미국 벤치마크 생략 가정(상수이동→
        순위불변)은 실제 ^IXIC로 검증해 2036종목 전원 일치, 레이스 로직은
        4종목 수동 트레이스로 확인, CONFIG 드리프트(v5.57~v5.65)는
        눌림목엔 ma20_slope_floor 완화 하나뿐인데 이건 히트가 늘어나는
        방향이라 격차(내 쪽이 더 적음)와 반대라 설명이 안 됨 — 전부
        정상이었음. 실제로 찾은 진짜 gap은 하나: `run_scan()`의 저유동성
        하드 필터(KR 3억원/US $2M, v4.52)가 `analyze()` 밖(app.py)에 있어서
        측정 스크립트가 `analyze()`를 직접 호출하며 놓치고 있었음 — 추가
        했지만 영향은 미미(눌림목 n 1648→1623, EV 0.286→0.291), 격차의
        원인은 아니었음. Script A 원본 스크립트가 저장소에 안 남아있어(1회성
        측정 스크립트 비커밋 관례) 최종적으로 원인 특정은 실패. 대신
        "재현 가능성"(방법론 대조·RS 검증·수동 트레이스·프로덕션 필터
        일치)을 근거로 재측정치를 채택하고, all_tabs_common_yardstick_investigation.md
        상단에 Script A 표가 재현 안 됨을 명시하는 노트 추가. 눌림목
        stopWidthWarnBanner/배지 문구를 최종 재측정치(n=1623)로 갱신.
        상세: docs/pullback_stop_width_and_entry_timing.md "기준선 불일치
        조사" 절.
v5.66 [UI+측정] 눌림목 손절폭 배지 재정의 + 확인 후 진입 측정. 사용자가
        제시한 전제("손절≤5%만 남기면 EV 0.172→0.145로 하락")를 전체 유니버스
        재측정(off=60~250, 20지점, n=1648)으로 검증 시도했으나 재현 안 됨 —
        오히려 타이트한 쪽이 근소 우세(0.295 vs 0.240). 5개 핵심 탭(눌림목/
        추세전환/돌파/박스돌파/돌파임박)을 같은 방식으로 다 재보니 방향이
        탭마다 다름(돌파·박스돌파는 타이트 우세, 추세전환은 열세, 돌파임박은
        무차이) — "손절 좁음=나쁜 신호"라는 단일 결론은 성립 안 하고, 오히려
        "방향 자체가 불안정하다"가 결론. static/index.html 배지 툴팁·눌림목
        전용 인라인 안내줄(#stopWidthWarnBanner)을 이 결론(포지션 사이징용,
        품질 신호 아님)으로 갱신, 필터/정렬 자체는 유지(narrow가 나쁘다는
        근거 없어 제거 안 함). 슈퍼대장 필터(#superOnlyToggle, 실측 EV 0.266)
        금색 톤으로 시각 강조 — 기본 on은 보류(89→16건으로 줄면 되돌리기
        비대칭적으로 불편, 시각 강조로 충분하다고 판단).
        눌림목 안A(즉시진입)/안C'(1~3봉 내 거래량1.5배+고가돌파 확인 후
        진입)/안D'(같은 시점·무조건 진입, 대조군) 2R 레이스 측정도 진행 —
        돌파임박(docs/imminent_stop_entry_investigation.md)과 같은 패턴 재현:
        안C'(n=266, EV 0.898R)가 안D'(EV 0.256R)를 크게 앞서 조건 자체의
        순수 효과가 확인되고, 미진입분을 안A로 채운 하이브리드(EV 0.232R)는
        순수 안A(EV 0.286R)보다 낮아 "확인 전엔 진입 안 하는 규율"의 가치도
        재확인. 근거·전체 수치: docs/pullback_stop_width_and_entry_timing.md.
v5.65 [테스트] test_trace_parity.py 셋업별 커버리지 미달 시 hard FAIL —
        v5.64에서 0건 발견 후 print 경고("⚠️")만 달아놨는데, "CI가 초록불
        이면 아무도 로그를 안 읽는다"는 지적을 받고 정식 assert로 전환.
        MIN_COVERAGE=3 상수 신설, `test_min_coverage_per_setup` 테스트
        추가 — 셋업별로 stop/risk_pct를 실제로 비교한 건수가 미달이면
        FAIL, 메시지에 어느 셋업이 몇 건 부족한지 그대로 출력. 재현 검증:
        MIN_COVERAGE를 일부러 100으로 올려서 5개 셋업 전부(부족 86~94건)
        정확히 FAIL하는 것 확인 후 원복. `_compute_coverage()`로 계산
        로직을 이 테스트와 `_summary()`가 공유하게 리팩터(따로 계산하다
        갈리는 일 방지). docs/rs_definition_and_slope_investigation.md 8절.
v5.64 [테스트] test_trace_parity.py 커버리지 점검 — v5.63 신설 직후 셋업별
        분포를 실제로 뽑아보니 pullback 6건/imminent 10건만 있었고
        turnaround/breakout/boxbreak는 0건(둘 다 통과하는 조합 자체가 초기
        29종목 표본에 하나도 없었음 — stop/risk_pct 비교가 이 3개 셋업에선
        한 번도 실행된 적이 없었다는 뜻, 테스트 파일은 "통과"했지만 사실상
        미검증 상태). 전체 유니버스(KR+US, kr_bundle/us_data 캐시)를 스캔해
        각 셋업을 실제로 통과하는 종목을 찾아 9개 추가(turnaround: 021240.KS/
        078130.KQ/082640.KS, breakout: 007070.KS/028670.KS/041960.KQ,
        boxbreak: 078340.KQ/145020.KQ/282330.KS) — 픽스처 29→38종목. 결과:
        5개 셋업 전부 6건 이상(목표 2~3건 상회). `_summary()`가 이제 셋업별
        커버리지를 출력하고 0건인 셋업을 ⚠️로 표시 — 픽스처를 바꿀 때마다
        `python3 test_trace_parity.py`로 커버리지 회귀를 바로 확인 가능.
        전 조합(380개) 재확인: 통과/탈락 일치 380, 값불일치 0.
        docs/rs_definition_and_slope_investigation.md 8절.
v5.63 [테스트] _trace_*(app.py 진단 재현) vs analyze_*(scanner.py 실제 스캔)
        차등(differential) 테스트 신규 — v5.62의 AST 상수감사가 "리터럴이
        CONFIG 밖에 있는지"만 보고 값 자체의 일치는 못 본다는 한계를 다른
        방식으로 보완. 실제 KR 14 + US 15종목(test_fixtures/sample_tickers.pkl,
        2026-08-07 종가 300봉, 체크인된 고정 스냅샷 — CI 네트워크 의존 없음)에
        rs_rank 82(일반 분기)/95(주도주 분기) 두 값으로 5개 셋업 전부(눌림목/
        추세전환/돌파/박스돌파/돌파임박) 돌려 (1) 통과·탈락 일치 (2) 둘 다
        통과 시 stop·risk_pct 완전 일치를 assert. 상수 이름/위치를 아예 안 보고
        실행 결과만 비교해서 AST 한계를 안 받음. [설계 교훈] 처음엔 rs_rank=95
        하나만 썼다가, v5.60 slope_floor 버그(주도주만 0.98 적용하던 옛 코드)를
        일부러 재현해 넣어도 이 테스트가 통과해버리는 걸 발견 — 95는 항상
        is_leader=True라 옛 코드의 "if is_leader" 분기만 타서 새 코드(무조건
        0.98)와 우연히 같은 값이 나왔음. rs_rank 82(비주도주)를 추가하니
        재현 버그를 정확히 잡음(290조합 중 2건 PASS/FAIL 불일치, 정확히
        rs82 지점) — 조건부 로직을 다루는 차등 테스트는 조건의 양쪽 분기를
        다 밟는 입력이 필요하다는 게 이번 발견. [부수] _trace_* 5개 함수의
        return dict에 stop 필드 추가(기존엔 risk_pct/pivot만 노출, 비교
        가능하게 stop도 노출 — _trace_turnaround는 아예 stop을 계산 안 하고
        있어서 통과 시점에 계산 추가, 게이트에는 원래도 영향 없었음).
        [CI] test_trace_const_audit.py가 파일만 있고 워크플로에 안 물려있어
        아무도 안 보는 상태였음(내일 오전 8시 예정 스케줄 실행에도 안 걸렸을
        것) — .github/workflows/test.yml에 이 파일과 test_trace_parity.py
        둘 다 추가. [구조 질문] _trace_*가 app.py에 분리된 이유: analyze_*가
        게이트에서 막히면 bare None만 반환해(핫패스 성능 트레이드오프,
        v5.39 당시 의도적 설계) "어디서 막혔는지" 정보가 안 남는다 — 구조적
        제약은 아니고, analyze_*에 optional trace 파라미터를 추가하면(기본
        None, 핫패스 무변화) 사본 자체를 없앨 수 있음. 실거래 스캔 함수
        5개의 게이트 시퀀스를 직접 건드리는 리팩터라 이번엔 안 함 — 필요해
        지면 검토. docs/rs_definition_and_slope_investigation.md 8절.
v5.62 [버그수정] app.py _trace_*(진단 재현) 함수의 scanner.py 상수 복사
        전수감사 — v5.61 slope_floor 동기화 누락 사고가 다른 곳에도
        있는지 확인해달라는 요청. 5개 _trace_* 전부 점검: CONFIG 값은
        전부 cfg[...] 참조라 문제 없음(v5.60 rs_min 85→80 등은 자동
        반영됨). CONFIG 밖 판정 로직 2건 발견·수정 —
        (1) _gate_risk_pct가 _risk_hard_ok의 risk% 계산식을 리터럴로
        재구현하고 있었음(기존엔 "반드시 같은 로직으로 유지" 주석만
        존재) → scanner.py에 _risk_pct_at_gate() 공용 헬퍼로 뽑아내
        _risk_hard_ok와 _gate_risk_pct 둘 다 이걸 호출하게 변경 —
        계산식이 물리적으로 한 곳에만 있어 이제 드리프트 불가능.
        (2) ma20_slope_floor(0.98)가 scanner.analyze() 본문의 지역
        변수였던 걸 CONFIG로 승격 — _trace_pullback도 cfg[...]로
        직접 읽게. 나머지(손절 배수 0.97/0.98/0.15/0.99 등)는
        scanner.py 자신도 CONFIG 밖 지역 리터럴이라 구조상 cfg[...]
        참조로 못 바꿈 — 4곳에 "scanner.py와 동기화 필요" 주석 남김.
        [린터] test_trace_const_audit.py 신규 — _trace_* 함수에서
        "X = 리터럴 if 조건 else 리터럴" 모양(정확히 이번 사고 패턴)을
        FAIL로 자동 감지, 그 외 float 리터럴은 INFO 체크리스트로 나열
        (완전자동 대조는 어느 리터럴이 scanner.py 어느 함수와 짝인지
        코드만으론 판별 불가해 구조적으로 무리 — CLAUDE.md에 수동
        확인 원칙 추가로 보완). docs/rs_definition_and_slope_
        investigation.md 6절 갱신.
v5.61 [버그수정] /api/debug의 RS 근사치가 화면에 안 보이던 문제 — v5.60
        삼성화재 조사에서 드러남(라이브 디버그가 rs=80 고정 근사치를 썼는데
        정식 percentile은 75라, "5점차 억울한 탈락"이라는 잘못된 결론으로
        이어짐). box_info 사고(DELL 486 vs 실제 447.88, v5.39에서 라벨링
        완료)와 같은 클래스. [반영] debug_ticker()가 leader-check(v5.54)와
        같은 패턴으로 _fetch_market_data("all") 캐시를 재사용 — 캐시가
        따뜻하면(보통 이미 그럼) 딕셔너리 조회만으로 정식 RS/RS모멘텀을
        가져와 5개 _trace_*·leader/super/surge 판정 전부에 사용, 콜드면
        (블로킹 없이 즉시 폴백) 기존처럼 80/5로 폴백하되 payload에
        rs_percentile_is_approx로 명시하고 탈락사유 전체에 근사 캐치올
        접미사. static/index.html 진단 헤더에 정식/근사 색상 배지 추가.
        전수 확인 결과 RS 외 다른 근사 필드는 없음(box_info/수평저항은
        이미 라벨링 완료, 나머지 트레이스 함수는 scanner.py 함수를 직접
        import해 써서 드리프트 위험 없음) — 단 이 과정에서 app.py의
        _trace_pullback이 v5.60 slope_floor 변경(전 종목 0.98)과 별개로
        자체 사본을 갖고 있어 동기화가 안 됐던 버그를 추가로 발견해 같이
        수정. 콜드/워밍 캐시 비교 검증: 콜드에선 근사치 80이 우연히
        돌파임박 rs_min(80)을 턱걸이 통과시켜 "돌파임박 통과"로 오판정
        했었음 — 워밍(정식 75)에선 8개 모드 전부 정확히 탈락으로 정정됨.
        docs/rs_definition_and_slope_investigation.md 6절.
v5.60 [반영] 삼성화재 조사(docs/rs_definition_and_slope_investigation.md)
        결론 2건. (1) 눌림목 ma20_slope의 slope_floor(0.98) 완화를
        전 종목으로 확대(기존 주도주RS90+ 한정) — 실측(offset 60~250
        step10, n=1963)에서 1.0→0.98 완화는 통과율만 오르고 EV는
        그대로(+0.078→+0.079R, 일반군만 봐도 +0.068→+0.067R). 조건
        자체는 정방향 유효(통과 EV+0.081R vs 미통과-0.036R, 오늘 나온
        역방향 지표들과 다른 계열). (2) 돌파/박스돌파/돌파임박
        rs_min 85→80(RS 계산방식 자체는 안 바꿈, 게이트만) — 3탭 전부
        80-85 EV가 85-90과 같거나 높고(돌파임박 n=19232:
        +0.114R/+0.080R vs +0.046R/+0.076R), 돌파의 절대RS 85-90은
        오히려 -0.034R — "85"라는 특정 문턱에 EV 근거가 없었음.
        눌림목(rs_min=80)과 통일. [반영 후 확인] 오늘자(2026-08-07
        종가) 실제 percentile RS로 재스캔 — 돌파 7→12건, 박스돌파
        10→15건, 돌파임박 100→132건, 눌림목 50→58건(신규진입분은
        전부 80-85/floor완화 구간 EV로, 기존 히트와 대등). 단
        삼성화재 본인은 오늘자 실제 우리RS가 75로 확인돼(라이브
        디버그 페이지의 "RS 80" 표시는 종목 단독조회 시 쓰는 고정
        근사치 — CLAUDE.md에 이미 문서화된 한계, 실제 값 아님) 이번
        변경으로도 4개 탭 전부 못 들어옴 — RS 자체가 부족한 것.
        [보류] RS 산식(지수대비 초과성과→절대RS) 교체는 반영 안 함 —
        rs_min이 거의 전탭 1차게이트고 백분위가 score에 곱셈으로도
        들어가 바꾸면 오늘 EV 기준선이 전부 무효화됨. 탭마다 승자가
        갈려("우리 방식이 낫다는 근거 없음" ≠ "절대RS가 낫다") 재측정
        없이 결론 낼 근거도 부족. 대신 카드 RS 라벨에 툴팁 추가
        ("지수 대비 초과성과 기준 — MarketSmith 등과 다를 수 있음").
        [미채택] "고점후수렴" 신규탭 — 조작화 기준(고점대비-8~-15%,
        MA20평탄,거래량감소,4주+횡보) 실측 결과 패턴충족군 EV
        +0.198R이 시점매칭 대조군 +0.215R보다 낮음 — 오늘 검증실패한
        패턴 4종과 같은 결론이라 보류.
v5.59 [버그수정] distribution_check()의 U/D 분산 신호 제거 — v5.57/58
        U/D 조사의 마지막 항목. 보유종목 알림(`/api/dist/{ticker}`, 봇이
        진입 종목마다 하루 1회 체크해 danger 시 실제 알림 발송) 맥락에서
        측정: close>ma50>ma200(보유 근사) 모집단에서 U/D<1.0("분산
        경고") 종목의 20일내 -10%하락 비율(29.4%)이 시점매칭 대조군
        (31.7%)보다 오히려 낮음 — 오늘 다른 U/D 사용처(눌림목/돌파/
        돌파임박 진입 스크리닝, 강한피벗 게이트)와 같은 역방향. level
        순서도 danger(39.7%)>none(37.8%)>caution(31.2%)로 단조롭지
        않고, danger의 소폭 상승은 U/D 외 신호(최대급락일·소진성거래량)
        가 섞인 결과라 U/D 기여로 보기 어려움. [반영] "U/D 악화" 신호가
        signals/level 판정에 기여하던 걸 제거, `detail.ud`는 참고값으로
        계속 노출(이전엔 다른 신호가 하나도 없으면 detail 자체가 안
        채워져 U/D만 신호였던 케이스는 값도 같이 사라졌던 것도 같이
        수정 — signals 유무와 무관하게 항상 채움). 고점대량반전/
        최대급락일/이평이탈/소진성거래량(climax) 4개 신호는 오늘 측정
        대상이 아니라 그대로 유지. docs/ud_volume_ratio_investigation.md
        갱신 — U/D 조사 시리즈(v5.57/58/59) 종합 마무리.
v5.58 [UI] trend_grade() A/B/C/D 등급 배지 참고용 격하 — v5.57 U/D
        조사의 후속. [측정] 8조건 통과율: 눌림목·돌파는 8개 중 7개,
        돌파임박은 6개가 90%+ — 각 탭 자체 게이트(RS/우상향추세)가 이미
        걸러놓은 걸 다시 재는 구조라 대부분 0비트. 유일하게 변별력 있는
        "20일선 위"(눌림목 74.7%, 돌파임박 57.0%)가 하필 눌림목·돌파임박
        에서 정반대 방향(눌림목 역방향, 돌파임박 강한 정방향). [등급별
        EV] 눌림목 A=0.140/B=0.196/C=0.202/D=0.385로 완전 역순(A최저,
        D최고), 돌파임박은 A는 1위 맞지만 B(0.134)가 C·D보다 나쁨 — 세
        탭 다 A>B>C>D 순서가 안 나옴. [옵션 검증] "U/D 강등만 빼면
        풀리는지"(옵션a) 직접 검증 — 등급을 U/D 포함/배제 두 버전으로
        계산해 비교했더니 세 탭 다 역순/무질서가 그대로 남음(눌림목 C가
        여전히 A·B보다 높음, 돌파 B가 여전히 A보다 높음, 돌파임박 B가
        여전히 최악) — U/D는 문제의 일부일 뿐 주범이 아니었음. 근본
        원인은 "20일선 위"가 A등급 필수조건(8/8)에 들어있는데 눌림목
        에서 역방향이라 A 모집단이 구조적으로 불리한 쪽에 쏠리는 것.
        [결론] 역방향 조건만 골라 빼는 안(b)도 남는 게 "20일선 위" 하나
        뿐이라 등급이라 부르기 어려워 기각 — **등급 배지를 판정에서
        완전히 내림**(옵션c). Trend Template 8조건은 원래 "종목 발굴"용
        인데 이미 각 탭 게이트를 통과한 후보에 다시 적용하는 구조라
        신호등 리스크 항목이 0비트였던 것과 같은 문제. [반영] `.gbadge`
        4등급 색상을 전부 중립 회색으로 통일(우열 암시 색상 제거),
        "A급(참고)" 식으로 텍스트·툴팁 변경. `grade` 필드가 필터/게이트로
        쓰인 적 없음(순수 표시용 passthrough)을 scanner.py 전수 확인 후
        진행 — 스캔 결과 자체(어떤 종목이 뜨는지)엔 영향 없음, 표시만
        변경. [얼마냐봇] `/api/pullback-signal` 엔드포인트 docstring에
        봇 쪽 수정 필요 사항 기록 — U/D≥1.5 하드 게이트가 방향 신뢰
        불가로 확인됐으니 게이트 조건에서 빼야 함(이 레포 밖이라 값
        자체는 안 건드림). docs/ud_volume_ratio_investigation.md 갱신.
v5.57 [버그수정] U/D Volume Ratio(매집/분산) 방향성 재검증 — 코텍(052330)
        U/D가 우리 계산(0.92)과 MarketSmith 주봉 차트(2.0)에서 2배
        차이난다는 리포트로 시작. [원인 규명] up_down_volume()/
        ud_volume_ratio() 계산 자체(50일, 종가 vs 전일종가, 상승/하락일
        거래량 합)는 IBD/MarketSmith 표준과 정확히 일치 — 다만 MarketSmith
        는 "현재 보고 있는 차트 단위로 50개 봉"이라 주봉 차트에선 50주
        기준이었음(우리는 항상 50일 고정). 코텍을 주봉 50주로 재현하니
        정확히 2.0 일치. 전체 유니버스 확인 결과 이 격차(2배 이상)가
        19.7%에서 발생하는 흔한 현상. [성과 상관관계] 일봉50일(현행)/
        주봉50주(MarketSmith)/일봉250일 세 방식 다 눌림목·돌파·돌파임박
        2R 레이스 EV와 일관된 정방향 관계가 없음 — 오히려 현행(일봉50일)
        은 3개 탭 전부 역방향(U/D<1 그룹이 EV 더 높음: 눌림목 0.33R vs
        0.14R, 돌파임박 0.33R vs 0.23R). 주봉으로 바꿔도 안 고쳐짐(눌림목
        여전히 역방향, 돌파임박은 거의 0비트). [반영] ①카드 배지 색상
        (초록/주황)을 방향성 암시 없는 중립색으로 통일. ②추세전환의
        "📉 매집미확증" 배지·경고 배너 제거, `ud_weak` 필드·score의 U/D
        가감(±12점, 배점 총합 125→115 재정규화) 전부 제거 — 배지만 빼고
        점수는 남겨두면 몰래 계속 반영되는 상태라 같이 뺌. ③강한피벗
        (실험) 탭의 STRONG_PIVOT_MIN_UD(U/D<1.0 하드 제외) 게이트 제거 —
        실측 결과 게이트가 걸러내던 그룹(ud<1.0)이 EV 0.361로 셋 중
        최고(게이트 있음 0.224, 없음 전체 0.252)였음. strength_score의
        U/D 기반 accum_score_component(최대 20점)도 같은 이유로 제거.
        [함수 정리] up_down_volume()과 ud_volume_ratio()가 완전히 같은
        계산(창 길이만 window/days로 이름 다름)이라 중복 확인 — 호출부
        4곳(scanner.py 3곳+app.py 1곳)을 up_down_volume()으로 통일,
        ud_volume_ratio() 삭제. ud_volume_detail()(상위1·3일 제외 분해)은
        용도가 달라 유지. [전수 감사] U/D가 쓰이는 모든 곳을 표시/점수/
        게이트 3분류로 확인(docs/all_tabs_common_yardstick_investigation.md).
        아직 미반영: trend_grade() 8조건 중 5개+U/D강등 로직이 탭마다
        방향이 다르거나 반대라 A/B/C/D 등급 자체가 성과를 못 가름(눌림목은
        A등급 EV 0.140 vs D등급 0.385로 완전히 역순) — 재설계 필요,
        방향 확인 후 진행 예정. distribution_check()(보유종목 분산 경보,
        봇이 실제 알림 발송)의 U/D 신호는 오늘 측정한 "진입 전 스크리닝"과
        다른 질문(보유 중 종목 모니터링)이라 별도 측정 필요. 얼마냐봇의
        "눌림 지지 진입" 알림(/api/pullback-signal)이 U/D≥1.5를 하드
        게이트로 쓰고 있음을 확인 — 봇 쪽 코드라 이 레포에서 수정 불가,
        사용자에게 별도 전달 필요.
v5.56 [UI] 탭별 필터/정렬 버튼 유효성 전수조사 — v5.55로 슈퍼대장에
        risk_pct가 생기면서 다른 필터(주도주만/적격만/매집순/손절좁은순)도
        탭마다 실제로 유효한지 확인. 가설은 "필드는 있는데 통과율 100%
        (0비트)"였으나 실제로는 정반대 — **필드 자체가 없어서 통과율 0%**.
        [발견] 주도주만(rs·거래량·rr·risk_pct 4개 다 필요): 대장후보(rr·
        risk_pct 없음)/슈퍼대장(거래량 필드 자체가 없음)/급등(rr·risk_pct
        없음)은 클릭하면 항상 0건. 적격만(bear_ok, badge_fields() 호출하는
        4곳에만 존재): 추세전환/대장후보/슈퍼대장/박스돌파/급등 5개 탭
        항상 0건. 손절좁은순: risk_pct 없는 탭은 전부 999 동점이라 정렬
        무의미. 매집순: 코드 자체가 mode==='pattern' 게이트라 다른 탭은
        원래도 no-op(문제 아님). [반영] 버튼 표시 여부와 필터 적용 조건을
        같은 Set으로 통일(RISK_TIER_MODES 패턴 재사용) —
        STRICT_MODE_VALID/BEAR_OK_VALID/ACCUM_SORT_VALID 신설, 손절좁은순은
        기존 RISK_TIER_MODES 그대로 재사용. 필드 없는 탭에서 버튼을
        display:none으로 숨김. hideFilterButtonsIfNotApplicable()을
        load() 진입 시점 + 탭 클릭 핸들러(journal은 load()를 안 타서
        별도 호출) 양쪽에서 호출해 인버스/섹터/마감정리/일지로 전환할 때도
        이전 탭 버튼이 안 남게 함. [참고] rr≥2 서브조건은 _rr_block 쓰는
        모든 탭에서 사실상 0비트(2R 최소보장 폴백 때문)지만 전체 필터가
        0건까지는 아니라 문서에만 기록, 미반영.
v5.55 [기능] 슈퍼대장 명단 활용법 재설계 — 안1(buy_zone 대기) 탈락 이후
        "명단으로만 두기 전에" 3가지 추가 측정(docs/all_tabs_common_
        yardstick_investigation.md v5.55 섹션). [①눌림목 필터] 눌림목
        히트를 슈퍼대장 소속/비소속으로 나누면 EV 0.151→0.266로 개선
        (돌파/박스돌파/돌파임박은 도움 안 되거나 나빠 미적용). 슈퍼대장은
        신호등 RS≥90의 완전한 부분집합(소속 100%가 RS≥90)이지만 RS90단독
        (EV 0.190)보다 한 단계 더 엄격해 별도 필터 가치 있음. RS≥95 근사
        판정은 실제 analyze_super()보다 24.2% 더 잡는데 EV가 낮아(0.244
        vs 0.266) 근사 대신 실제 판정 채택 — 비용 차이 없음(이미 fetch된
        데이터에 연산 한 번 추가). run_scan()이 mode=='pullback'일 때
        각 히트에 is_super 필드 부착, 카드에 "👑 슈퍼대장만" 토글(눌림목
        전용, 기존 필터 줄에 배치, 신호등과 별개 필터임을 툴팁에 명시).
        [②즉시 진입] 안1 실패 원인이 "이 종목을 사서"가 아니라 "기다려서"
        였는지 검증 — 진입=신호일 종가(무조건, 생존편향 함정 없음), 손절
        4안(20일선-2%/50일선-2%/ATR×2/significant_support) 2R 레이스
        전부 안1(EV 0.26)을 크게 웃돎(EV 0.52~0.72). EV 1위는 50일선-2%
        (0.718)지만 손절폭 median 17.2%로 실행이 어려워, ATR×2(EV 0.641,
        median 10.3% — 절반)를 채택. analyze_super()에 is_kr 파라미터
        추가, _rr_block으로 진입(현재가)/손절(ATR×2)/리스크%/손익비 계산,
        카드에 metrics + 전용 경고 배너("되돌림 기다리지 않고 즉시 진입
        ... buy_zone 대기는 검증 실패") 추가. RISK_TIER_MODES에 super
        추가해 기존 🔵⚪🟠🔴 배지·필터 재사용. buy_zone 근접/status "✓"
        표기가 진입신호로 오인될 수 있어("매수 확인✓"/"담을곳 근접" 배지
        제거, status는 회색조 참고 칩으로, 담을곳엔 "참고, 진입기준 아님"
        명시) — Script D(near_buy_zone 무차이)와 ③(아래) 둘 다 이 표시가
        근거 없었음을 뒷받침. [③status 하방] ATR×2 기준 status(7종)별
        2R EV — "20일선 지지✓"(이름상 확인됨)가 손절률 최고(51.0%)·EV
        최저(0.448), "조정 깊음"(이름상 나쁜 신호)이 손절률 최저·EV
        2위(0.855)로 라벨과 실제 성과가 반대. 다만 최악 status 제외해도
        전체 EV 개선이 +2%뿐이라(신고가/눌림진행/20일선테스트가 81% 차지)
        별도 필터는 구현 안 하고 보류 — ②의 badge 정리로 충분하다고 판단.
v5.54 [기능] 대장후보→눌림목 전환 관찰. Script D(전 탭 공통잣대 조사)에서
        확인한 대장후보 히트의 74%가 60봉 내 눌림목 전환(median 6봉)을
        기존 감시(⚡)/관찰(👁) 인프라로 알려줌. [카드] 대장후보 카드의
        ⚡감시 버튼(대장후보엔 pivot이 없어 원래 항상 "피벗없음"으로
        실패하던 죽은 버튼)을 "👁 눌림목 전환 관찰"로 교체 — 클릭 시
        손절 없음·봇 알림 없음으로 관찰 등록, 등록 시점 RS·20일선
        위치·현재가 스냅샷 저장. [백엔드] `/api/watch/quick`에
        `category` 파라미터 추가(`관찰`이면 pivot 불필요, status='watch').
        신규 `/api/watch/leader-check`: 관찰 티커만 받아 `analyze()`로
        눌림목 게이트 판정 — 전용 fetch 없이 기존 시장 데이터 캐시
        (`_fetch_market_data`, 다른 탭 로드로 이미 채워짐)를 재사용해
        캐시가 따뜻하면 사실상 즉시 응답, 콜드면 pending 반환 후 다음
        폴링에 재시도(비용 실측: docs/leader_to_pullback_watch.md).
        [만료] 26영업일(p90) — 트리거 관찰의 3영업일과 다르게 설정(대장후보는
        RS95+ 리스트 소속이 핵심인 느린 신호). 만료돼도 스냅샷 값은
        유지, 회색조 처리, 기존 일괄정리 버튼이 관찰 종류별로 다른
        만료일수를 쓰도록 일반화(watchExpiryDays/isWatchResolved).
        [필드명] trigger_date→watch_start_date로
        개명(관찰 종류를 불문한 공통 "관찰 시작일" 개념인데 트리거 관찰
        전용 이름이 남아있던 문제 — 오늘 여러 번 잡은 것과 같은 유형).
        읽는 쪽은 watchStartDate(r)가 옛 trigger_date로 폴백해 기존
        레코드 호환.
v5.53 [UI] entrySignal() 거래량 항목도 돌파·박스돌파에서 참고용으로
        격하 — ok율 100.0%로 확인(v5.52의 리스크 90%+보다 더 심한
        0비트). 원인도 리스크와 동일: scanner.py BREAKOUT_CONFIG/
        BOXBREAK_CONFIG의 vol_mult:1.5 게이트가 이미 같은 조건
        (vol_mult>=1.5)을 확인하고 통과시켜서, 히트 시점엔 신호등이
        같은 걸 또 확인하는 구조라 항상 ok. [반영] 돌파: 거래량 제외 —
        RS는 이미 v5.51에서 역방향으로 제외돼 있던 상태라 남는 게
        AVWAP뿐(단일 판정, 눌림목과 같은 체제). 박스돌파: 거래량 제외,
        RS는 뺀 적 없어 RS+AVWAP 2항목. [정정] 최초 지시에 "돌파는
        RS+AVWAP 2항목"이라는 표현이 있었으나 이는 RS가 이미 역방향으로
        제외된 걸 놓친 것 — 확인 후 AVWAP 단일로 정정, RS는 되돌리지
        않음. [화면] 참고용으로 격하된 항목의 사유를 "참고·중복"(게이트가
        이미 같은 조건 확인, 0비트)과 "참고·역방향"(그 항목 ok가 오히려
        결과 나쁨)으로 구분 표시(ENTRY_SIGNAL_EXCLUDE_REASON) — 이유가
        다른데 같은 회색조면 헷갈린다는 지적 반영. 돌파는 전용 안내문
        추가("거래량은 게이트에서 이미 확인, RS는 역방향 — 남은 건
        AVWAP뿐"). [검증] 박스돌파 🟢은 4항목 시절 40건(EV 0.725)→
        리스크 격하 41건(EV 0.683)→거래량 격하도 41건(EV 0.683)
        그대로 — 거래량이 이미 100% ok였다는 건 판정에 한 번도
        기여한 적 없었다는 뜻이라 활성 항목에서 빼도 재분류가
        일어나지 않음(0을 더하고 빼는 것과 동일). 돌파도 같은 이유로
        🟢 102건·EV 0.353 무변화. docs/all_tabs_common_yardstick_
        investigation.md에 5탭×4항목 최종 정리표(판정/참고 및 사유)
        수록 — 다음에 항목 추가/재배치할 때 참고.
v5.52 [UI] entrySignal() 리스크 항목 재점검 — 항목 수를 줄이면서 판정
        기준이 헐거워졌는지(🟢 과반 여부) 확인 요청에 따른 후속.
        [측정] v5.51 직후 5개 탭 🟢 비율: 눌림목 58.0%(과반), 돌파임박
        37.7%, 추세전환 9.6%, 돌파 38.6%, 박스돌파 16.6% — 과반은 눌림목
        뿐. 눌림목 🔴는 0.1%(2건)로 사실상 소멸 확인. [기각] "활성 항목
        절반 이상 warn=🔴"로 danger 문턱을 항목수에 비례시키는 비율규칙을
        검토했으나, 🟢의 정의(전부 ok)가 기존 규칙과 동일해 🟢 비율을
        전혀 못 바꿈(재현 스크립트로 확인: 눌림목 🟢 58.0%→58.0% 무변화,
        예전 🟡가 통째로 🔴로 재분류될 뿐) — 폐기. [원인] 리스크 항목의
        탭별 통과율 전수조사 결과 눌림목 99.4%·돌파임박 97.1%·돌파
        99.2%·박스돌파 98.3%로 전부 0비트 체크(어제 손익비 100% 통과와
        같은 유형) — 손절폭이 근접 피벗 기준이라 애초에 좁게 형성돼
        12%/8% 한도를 거의 항상 만족. 추세전환만 71.9%로 실제 변별력
        있어 유지. [반영] 위 4개 탭 리스크를 참고용으로 격하
        (ENTRY_SIGNAL_ITEMS: 눌림목→RS 단일, 돌파임박→RS+AVWAP, 돌파→
        거래량+AVWAP, 박스돌파→거래량+RS+AVWAP). 눌림목은 이미 거래량·
        AVWAP도 빠진 상태라 RS 단일 판정이 됨 — "신호등"이 과장되지
        않도록 체크리스트 전용 문구 추가(entrySignalNoteHtml(): "판정에
        쓰는 항목 RS 1개... 🟢은 RS 90 이상을 뜻할 뿐 진입 적합 판정이
        아님" + 구조적 설명 "RS 80 게이트를 이미 통과한 집단이라 절반
        이상이 RS 90+인 게 정상"). [보류] 눌림목 RS 컷 인상(90→95, 🟢
        58%→23.4%)은 구간별 EV가 비단조적(RS[93,95)에서 급락)이라 노이즈
        가능성 판단, 보류. 돌파/박스돌파의 거래량 항목도 ok율 100.0%로
        리스크보다 더 심한 0비트임을 발견했으나 이번엔 명시적 지시 범위
        밖이라 미반영 — 다음 판단 대상.
v5.51 [UI] entrySignal() 신호등 4항목(거래량/RS/리스크/AVWAP)을 탭별 판정
        항목으로 분리. docs/all_tabs_common_yardstick_investigation.md
        후속 측정(항목별 EV 분해, Script A 원본 히트 재활용)에서 일부
        탭의 특정 항목이 역방향(그 항목이 ok일 때 오히려 EV가 낮음)으로
        나옴 — 패턴 4종/Stage2 탭 전체가 역선택이었던 것과 같은 종류의
        반전이 핵심 탭 "안의 항목 하나" 단위에서도 발견됨. [반영] 눌림목:
        거래량·AVWAP 제외, RS·리스크만 판정(EV 0.087→0.189, n356→1037).
        돌파임박: 거래량 제외, RS·리스크·AVWAP만 판정(0.173→0.285,
        n567→1216). 추세전환: 거래량 제외(n71→170, EV는 0.521→0.453으로
        하락하지만 표본 71건이 작아 노이즈 가능성 있다고 판단, 제외 사유는
        같은 거래량 역방향). 돌파: RS 제외(n62→102, 같은 이유로 노이즈
        가능성 감안). 박스돌파: 역방향 항목 없어 4항목 그대로. 패턴
        4종/Stage2: 이미 탭 전체 검증실패 취급이라 항목 튜닝 안 함.
        [화면] 제외된 항목도 카드에서 정보로는 계속 표시(회색조+"참고"
        태그), 판정(🟢🟡🔴)에서만 제외. 체크리스트 상단에 "이 탭에서
        검증된 N개 항목만 판정에 반영 중" 안내 추가(탭마다 항목 수가
        다른 이유를 알 수 있게). [검증] 반영 전/후 재현 스크립트로 5개
        탭 🟢🟡🔴 분포 확인 — 눌림목 🟢 356→1037(×2.91), 돌파임박
        567→1216(×2.14), 추세전환 71→170(×2.39), 돌파 62→102(×1.65),
        박스돌파 무변화. 새로 🟢 된 종목은 전부 예전에 거래량(또는
        돌파 탭은 RS) warn 단독 사유로 🟡/🔴였던 케이스.
v5.50 [UI] 전 탭 공통 잣대 측정(docs/all_tabs_common_yardstick_investigation.md,
        Script A~F) 결과 반영. 패턴 4종(급등매집/컵앤핸들/더블바닥/치솟은깃발)과
        Stage2가 시점매칭 대조군 대비 우위가 없거나(급등매집: EV 0.154=0.154
        동일) 역선택(컵앤핸들 0.075<0.110, 더블바닥 0.114<0.147, 치솟은깃발
        0.030≪0.145 약 5배 격차, Stage2는 유동성필터 후에도 상승우위
        +2.3%p뿐인데 하락위험은 오히려 4.2%p 더 나쁨)임이 실측 확인돼,
        지우지 않고 "보이되 오해하지 않게" 처리. [1] #modeTabs에서 두 탭을
        tab-exp 클러스터 끝(섹터 앞)으로 재배치, 배지를 "실험"→"검증실패"
        (.exp-tag.danger, 빨간색)로 변경. [2] 카드에 서브패턴별로 다른 경고
        문구 배너 추가(치솟은깃발처럼 격차가 큰 탭을 공통 문구로 뭉뚱그리면
        정보 손실이라 patternWarningText()로 분리) + Stage2 전용 경고 배너.
        [참고] 같은 라운드에서 슈퍼대장 안1(진입=buy_zone 즉시체결 가정, EV
        1.22)도 buy_zone 실제 터치율을 재검증(Script F)했더니 60봉 내
        72.1%만 터치하고 미터치 종목이 오히려 median +75.4%로 더 크게
        가는 생존편향(안C와 동일 함정) 확인 — EV가 0.26~0.361로 재추정돼
        카드 반영은 보류, 재설계 필요(이번 커밋에는 미포함).
v5.49 [버그수정] favorites_user.txt/alerts_user.txt를 journal_user.json과
        같은 영구 볼륨(/data) 우선 경로로 저장 — 지금까지는 앱 코드 폴더에만
        저장돼 Railway 재배포할 때마다 사라질 수 있었음(journal은 이미
        v4.48.1부터 /data 우선이었는데 이 두 파일만 그 로직이 없었음).
        `_resolve_persistent_path()`로 일반화(환경변수 JOURNAL_DIR → /data →
        앱 폴더 순 우선순위, journal의 `_resolve_journal_path()`와 동일 원리)
        + 앱 폴더에 기존 파일 있으면 새 경로로 1회 자동 마이그레이션(둘 다
        없어질 걱정 없이 그대로 이전). 경보종목(투경/투주/정지/단기과열)은
        수동으로 매핑해둔 목록이라 날아가면 복구가 번거로워 우선순위 높음.
v5.48 [문서] /guide(GUIDE.md) v4.49.2→v5.47 기준으로 전면 갱신. v4.49 이후
        쌓인 변경(손절폭 절대값 4단계 배지, 패턴 서브탭, 급등매집 개명,
        일지 관찰 트리거 🟢확인됨, 진입신호등 4항목으로 축소, boxbreak
        extended_max 등)이 가이드에 전혀 반영 안 돼 있던 걸 정리.
        [핵심 추가] 0.5장 "이렇게 쓰세요" 4줄 빠른참조 신설. 4장(돌파임박)에
        안C 조사(docs/imminent_stop_entry_investigation.md) 실측 근거를
        넣어 "왜 즉시매수 대신 관찰 등록 후 확인해야 하는지"를 숫자로 설명
        (EV 0.20R→확인 후 0.80R). 6장의 "게이트는 피벗 기준" 문구는 v5.41로
        이미 틀린 서술이 돼 있어 수정(카드=게이트 기준으로 통일됨). 7장에
        급등매집 "관찰용, 진입용 아님" 명시(ABC 문서 패턴 조사에서 역선택
        방향 나온 사례 인용). 9장에 신호등 4항목(거래량/RS/리스크/AVWAP,
        손익비 항목 제거 반영) 및 관찰 종료 액션 3종 설명 추가. 부록에
        🔵⚪🟠🔴/🟢확인됨 배지 추가.
v5.47 [UI] 일지 관찰(watch) 종료 처리 — 무한정 쌓이던 문제 대응.
        [1] 관찰중 행에 종료 액션 3종: ▶진입함(실제 매매로 전환, category를
        관찰에서 빼고 status='entered'+편집모드로 진입가/손절가 입력)
        / ✖무산(셋업 붕괴, status='missed') / ⏹종료(승패 판단 없이 그냥
        종료, status='missed'지만 closed_reason으로 구분). 종료된 관찰은
        재관찰(🔓) 버튼으로 되돌릴 수 있음(기존 종료→진입중 reopenRow와
        같은 패턴).
        [2] 확인 기간(3영업일, v5.44) 지난 트리거 관찰 일괄정리 버튼 —
        이미 🟢확인된 건은 자동 제외(isTriggerConfirmed 재사용), 확인 안
        된 것만 대상.
        [3] 관찰 뷰에서 종료된 관찰은 삭제 대신 "종료된 관찰 (N건)"으로
        접혀 하단에(기본 접힘, 클릭해서 펼침) — 기존 종료 탭의 월별 접기
        (renderClosedMonths)와 같은 UX 패턴.
        [기반 확인] 기존 종료 처리(entered→closed, pending→missed 등)가
        전부 status/tracking 필드 갱신 → setJournal→서버저장→렌더 패턴임을
        확인 후 관찰에도 동일 패턴 적용 — 새 인프라 없이 기존 상태기계
        재사용.
v5.46 [UI] 패턴 탭 서브탭(전체/컵앤핸들/치솟은깃발/더블바닥/급등매집)을
        섹터 칩(.schip, 둥근 pill·amber)과 시각적으로 완전히 분리 — 전용
        스타일(.psub, 사각 모서리·보라 액센트)과 전용 위치(필터 줄 바로
        아래, 섹터 칩보다 위)로. 이전엔 섹터 칩과 완전히 같은 클래스라
        패턴 탭에서 두 종류의 칩이 구분 없이 섞여 보였음 — 섹터 칩은
        브라우징(여러 개 중 아무거나 골라볼 수 있음)이고 서브탭은 상호배타적
        선택이라 성격이 달라 구분 필요. v5.45의 손절폭 필터(#riskTierBar)와
        같은 줄 그룹(필터 영역)에 놓이도록 레이아웃도 같이 정리.
v5.45 [UI] 손절폭 절대값 4단계 배지(v5.42)를 필터로도 선택 가능하게(다중선택).
        risk_pct 필드가 있는 모드(눌림목/추세전환/돌파/박스돌파/돌파임박/패턴/
        강한피벗)에서만 노출 — 그 외(대장후보/슈퍼대장/Stage2/IBD9/실적우수/
        급등/인버스/붕괴/섹터/마감정리/일지)는 확인해보니 risk_pct 필드 자체가
        없어서(각 analyze_* 함수가 `_rr_block`을 안 씀) 필터가 무의미해 숨김.
        기존 필터 줄(전체/한국/미국/🎯주도주만/↕손절좁은순/💎적격만/🧲매집순,
        7개)에 4개를 더 넣으면 줄바꿈이 지저분해져 별도 줄로 분리 —
        `#riskTierBar`를 필터 줄 바로 아래, 섹터 칩보다 위에 배치(risk_pct
        모드가 아니면 숨김). 배지 표시 로직(riskTierBadge)과 필터 로직이 같은
        경계값을 쓰도록 `RISK_TIERS`/`riskTierOf()`로 단일 소스화(드리프트
        방지). 필터 칩은 다중선택이라 섹터 칩(둥근 pill, 단일 브라우징)과
        구분되게 사각 모서리로.
v5.44 [신규] 일지 관찰(watch) 항목에 돌파임박 "확인 후 진입" 트리거 배지.
        [배경] 안C 조사(docs/imminent_stop_entry_investigation.md 3.6)
        결론 — 돌파임박 신호의 실제 가치는 손절/진입가 공식이 아니라
        "거래량 동반 확인 전엔 진입하지 않는다"는 규율 자체(미진입 80.6%를
        안A로 채워넣으면 EV가 오히려 0.199R→0.163R로 하락). 화면이 그 규율을
        실행할 수 있게 트리거 정보를 관찰 등록 시점에 스냅샷.
        [데이터] analyze_imminent에 signal_high(오늘 고가) 필드 추가.
        일지 '관찰' 카테고리로 저장 시(돌파임박 한정) trigger_price(=등록일
        고가), trigger_volume(=avg_volume×1.5), trigger_date(=등록일),
        trigger_pivot_dist_pct(=등록 시점 피벗까지 거리) 스냅샷 — 라벨은
        "신호일 고가"가 아니라 "등록일 고가"로 표시(측정 방법론은 신호
        최초감지일 기준이라 다름을 명시).
        [판정] 등록 다음 영업일부터 판정(당일 비교는 트리거가 자기 자신이라
        항상 자명하게 충족되는 문제 방지). 3영업일(측정 N=3과 동일) 경과 시
        "확인 기간 종료" — 자동 삭제·기록 수정 없음(기존 원칙 유지), 숫자는
        만료 후에도 계속 표시.
        [백엔드] /api/prices가 highs/volumes도 반환하도록 확장(기존
        prices/closed에 필드 추가, 기존 소비자 영향 없음) — 오늘 고가·거래량이
        트리거 조건을 충족했는지 비교용. 이미 fetch하는 df에서 뽑는 거라
        추가 네트워크 비용 없음.
        [부수 수정] 가격 추적 대상 판정을 `_isPriceTrackable()` 헬퍼로 통일
        하면서, 관찰(watch)이 진입/대기 손절·목표 추적 로직을 잘못 태울 수
        있던 경로를 명시적으로 분리(entry/stop 없는 관찰 항목이 있으면
        NaN 손절폭으로 흘러들어갈 뻔한 지점 차단).
v5.43 [개선] 눌림목/돌파임박 score 계산에서 tightening(VCP 캔들수축) 가점
        완전 제거(눌림목 5점/돌파임박 20점), 돌파임박 vol_dry(거래량수축)
        20점 절벽을 항상 연속식(vol_ratio 기반)으로 통일.
        [근거] 돌파임박 히트 대상 전체 유니버스 실측(과거 체크포인트
        3600여건, 60봉 레이스 시뮬레이션):
          - tightening True/False간 도달률·손절률 차이 1.5%p — 오차범위,
            실증 근거 없음.
          - vol_dry(수축) True일 때 이후 10봉 내 실제 거래량동반돌파 비율
            39.6%, False일 때 46.7% — 오히려 역방향(수축이 폭증 돌파를
            예고 못 함). tightening보다 더 명확한 반증.
        둘 다 "VCP(변동성 압축 후 확장)"라는 같은 전제를 코드화한 것인데
        실증이 정반대 방향이거나 무의미했음.
        [처리 방식 차이] tightening은 순수 이진값이라 가점 완전 삭제.
        vol_dry는 연속값(vol_ratio) 정보가 있어 절벽(20 vs 연속식)만 없애고
        정보 자체는 유지 — top30 랭킹 영향 비교(제거/반전/항목삭제 3안 측정)
        결과 이 방식이 가장 덜 흔듦(상위30 이탈 3건, 반전은 14건/항목삭제는
        12건).
        [검증] 재현 점수 vs 실제 score 오차 평균 0.02~0.03점(신뢰 가능한
        재현) 확인 후 변형 비교. 반전(부호를 뒤집어 데이터 방향에 맞추는 안)은
        돌파임박 top30의 60%를 흔들어(18/30 이탈, 최대 34계단) 채택 안 함 —
        데이터가 가리키는 방향이라도 과적합성 랭킹 요동은 위험하다고 판단.
        `tightening`/`vol_dry` 필드 자체는 유지(카드 배지, 강한피벗 풀
        strength_score에서 계속 사용) — 이번 변경은 눌림목/돌파임박 자체
        score 계산에서의 가중치 반영만 제거.
        [측정 전체 기록] docs/imminent_stop_entry_investigation.md — 안A/B/C/D
        손절·진입 정의 비교, 생존편향 확인(안C 진입분 EV 0.796R이 전체 신호의
        19.4%에 집중, 하이브리드 대체 시 오히려 0.163R로 하락 — "확인 후
        진입" 규율의 근거), R기준 재계산 등 전체 과정.
v5.42 [UI] 손절폭 절대값 4단계 배지(static/index.html) 추가 — 예전
        stop_wide(🚫손절폭넓음, ATR×1.5 상대기준) 배지 자리를 대체.
        [배경] v5.40에서 _risk_hard_ok가 stop_wide와 같은 ATR×1.5를 게이트로
        쓰게 되면서, pullback/breakout/boxbreak/imminent 4탭에서 stop_wide는
        통과한 결과에 정의상 절대 안 뜨는 죽은 배지가 됐음. "ATR 대비
        타이트해서 게이트 통과"와 "절대 손절폭이 실전에서 쓸만한가(+1R
        도달 가능성)"는 다른 질문이라 후자를 새 배지로 답함.
        [기준] risk_pct 기준 4단계 — 🔵 타이트 ≤5% · ⚪ 보통 5~8% ·
        🟠 넓음 8~12% · 🔴 매우넓음 12%+. 실제 %값도 라벨에 같이 표시.
        게이트 유무와 무관하게 risk_pct만 있으면 전 모드(turnaround/pattern
        포함) 적용 — entrySignal 체크리스트(고정 8%US/12%KR 이진판정)와는
        시각적으로 분리된 별도 배지 슬롯이라 "8%"가 두 군데 나와도 안 헷갈림.
        [측정] 적용 전 실측(오늘자, 4개 게이트 탭 히트 전체): 눌림목
        🔵69.4%(50/72, "3~5% 자리" 목표와 실측 일치) · 돌파 🔵27.3%/🟠18.2%
        · 박스돌파 🔵22.2%/🟠22.2% · 돌파임박 🔵41.3%/🟠13.5%/🔴3.2%(v5.40
        ATR완화로 새로 들어온 9건이 이 🟠🔴 구간에 몰림 — 배지로 정확히
        드러남). 정렬("↕ 손절좁은순")과 배지가 같은 risk_pct 필드를 써서
        항상 일치함을 코드로 확인(별도 계산 경로 없음).
v5.41 [버그수정] analyze_breakout/analyze_boxbreak의 _risk_hard_ok 호출을
        피벗 기준→현재가(close) 기준으로 통일 + boxbreak에 extended_max(12%)
        신설. pullback은 변경 안 함(Case13 회귀 방지 근거 documented, 유지).
        [배경] 게이트(피벗 기준)와 카드 표시 risk_pct(entry=close 기준)가
        달라 최대 25.2%p까지 벌어지는 사례 발견(전체 유니버스 측정).
        박스돌파는 stop=pivot×0.97로 피벗에 고정되는데, 코드 자체 주석이
        "이미 돌파한 상태 → 실제 진입은 현재가. 리스크/손익비 모두 현재가
        기준으로 통일"이라고 명시해놓고 정작 하드게이트만 pivot을 넘겨
        그 의도를 뒤집고 있었음. 051160.KQ 사례: 박스상단(피벗) 대비 이미
        +35.5% 연장, 카드엔 리스크 29.0%로 정확히 뜨는데 게이트는 피벗
        기준 3.79%로 계산해 손쉽게 통과시킴 — 추격 진입이 하드게이트를
        무력화하는 구멍.
        [측정→결정] boxbreak 현재 히트 24건의 ext 분포(중앙값 7.9%, 최대
        35.5%) 확인 후 breakout과 같은 extended_max=0.12로 채택(연장 12%+
        가 대부분은 아니었음, 5/24건).
        [검증] 동일 캐시로 patch 전/후 실제 프로덕션 함수 재실행 diff:
        pullback 변화 0건(의도대로 무변경), breakout 17→11(-6, 전부
        close기준 전환으로 인한 조정), boxbreak 24→9(-15, close기준
        전환+extended_max 결합효과 — 051160.KQ 포함 고연장 종목 전부 제외
        확인). imminent는 이번 패치 대상 아니라 126건 그대로.
        [부수 발견] 오늘 데이터 기준으론 boxbreak에서 extended_max가 단독
        으로 추가 배제한 종목은 0건(연장 12%+ 종목은 close기준 전환만으로
        도 이미 다 걸러짐, ATR 완화를 적용해도 한도 미달). 다만 논리적으론
        고ATR 종목이 ATR 완화로 리스크 상한을 넉넉히 받는 동시에 심하게
        연장된 경우 close기준 단독으론 못 거를 수 있어 이중 안전장치로서
        의미는 있음(오늘 표본에 그 조합이 없었을 뿐).
        [테스트 수정] test_scanner.py Case22가 +6% 연장을 썼는데, close
        기준 전환으로 실제 리스크가 8.94%(고정 8% 초과)가 돼 탈락 판정으로
        바뀜 — 의도된 동작(연장분이 진짜 리스크에 반영됨). 표본을 +4%
        연장(risk 7.19%)으로 조정해 "정상 탐지" 케이스 의미를 유지.
        [디버그 패널] _trace_pullback이 게이트기준(피벗) risk%와 카드기준
        (현재가) risk%를 둘 다 표시하도록 확장(게이트기준_실제피벗 payload
        + 프론트 🎯 섹션) — 두 값이 다를 때만 두 줄로, 같으면(이제 돌파/
        박스돌파는 항상 같음) 한 줄만 표시. "34건 조용히 탈락" 문제(카드
        숫자만 보면 통과처럼 보이는데 실제 게이트는 탈락) 대응.
v5.40 [개선] _risk_hard_ok(pullback/breakout/boxbreak/imminent 4탭 공통 리스크
        하드게이트)를 고정 US8%/KR12%에서 loosen-only ATR 완화로 변경.
        한도 = max(고정 US8%/KR12%, min(ATR%×1.5, 15% 절대상한)). badge_fields의
        stop_wide가 이미 쓰는 ATR×1.5를 그대로 재사용(새 배수 발명 안 함).
        [배경] stop_wide는 v4.67에 ATR×1.5로 바뀌었는데 _risk_hard_ok는 고정
        %로 남아있던 불일치 — 고ATR 미국주(DELL 등)가 배지엔 안 걸리는데
        하드게이트에서만 탈락하는 모순이 있었음.
        [측정→결정] 전체 유니버스(KR1503+US2109) 측정 결과: pure ATR×N
        치환은 저ATR 종목을 새로 탈락시켜 저ATR 손해 발생(×1.5에서 오히려
        전체 히트 감소) — loosen-only(max)만 순수 증가. N=1.5 채택(±2.0은
        검토 결과 보류 — 임의로 결과에 기준을 맞추는 것이라 판단, badge와
        동일 배수 재사용 원칙 우선).
        [검증] 동일 캐시 데이터로 patch 전/후 실제 프로덕션 함수(analyze/
        analyze_breakout/analyze_boxbreak/analyze_imminent) 재실행 후 히트
        종목 집합 diff — 4탭 전부 탈락(lost) 0건 확인(loosen-only이므로
        수학적으로 보장되지만 실측으로 재확인). 신규 진입: pullback +2,
        imminent +9, breakout/boxbreak 오늘 시세 기준 0(경계 종목 없음).
        15% 캡이 2건(006340.KS, 144960.KQ — ATR×1.5가 15%를 넘어 캡에
        걸림)에서 실제로 작동 확인, 둘 다 캡 적용 후에도 통과.
        [부수 발견] _risk_hard_ok의 판정 기준(피벗→손절 pivot 기준)과
        카드에 표시되는 risk_pct(entry=close 기준, pullback/breakout/
        boxbreak 한정 — imminent는 entry=None이라 pivot 기준으로 일치)가
        다를 수 있음이 이번 검증 중 재확인됨(기존 설계, 이번 패치와 무관).
        DELL 눌림목 카드엔 6.55%로 뜨지만 게이트 판정은 9.02%(피벗 기준)로
        했음 — 표시값만 보고 게이트 통과 이유를 추측하면 오판 가능.
        [설정] CONFIG에 risk_hard_atr_mult=1.5, risk_hard_atr_cap=15.0 추가.
v5.39 [버그수정] /api/debug '탈락_핵심사유'가 실제 스캔이 안 쓰는 box_info
        (터치필터 없는 단순 20/40/60봉 최고가)로 사유를 지목하던 것 수정.
        DELL에서 핵심사유가 "저항 486"이라 떴지만 돌파임박 게이트가 실제로
        쓰는 select_pivot 값은 447.88(리테스트저항)이었음 — 통과한 조건을
        탈락 사유로 잘못 표시해 "거래량 부족이 공통 원인"이라는 오판을
        유발한 사고 확인 후 재작성.
        [구조] analyze/analyze_turnaround/analyze_breakout/analyze_boxbreak/
        analyze_imminent 5개 함수의 실제 게이트 순서를 그대로 재현하는
        _trace_* 함수(app.py)를 새로 작성 — select_pivot/_rr_block/
        _risk_hard_ok/late_stage_info/_merger_block 등 scanner.py의 실제
        헬퍼를 그대로 호출해 로직 드리프트 방지. 각 게이트를 통과/탈락
        여부와 함께 순서대로 기록해 어디서 처음 걸렸는지 정확히 노출.
        [신규 필드] 게이트기준_실제피벗(select_pivot 결과 — 진짜 판정
        기준), 게이트추적(전체 게이트 단계별 기록). box_info/수평저항은
        "_주의" 필드로 참고용(게이트 기준 아님)임을 명시.
        [부수 수정] modes 판정에서 analyze_breakout만 is_kr 전달이
        빠져있던 버그 발견·수정 (KR 종목 리스크 하드게이트 한도가
        8%[US]로 잘못 적용될 뻔한 경우였음 — trace 함수 통일 과정에서
        확인). analyze_breakout에 base_high<=0 스킵 게이트 누락도 보강.
        [프론트] static/index.html 진단 패널에 "🎯 실제 게이트 판정 피벗"
        섹션 추가, 수평저항 섹션은 참고용 라벨로 톤다운.
        [검증] test_scanner.py 29케이스 전부 통과, min_bars 감사 통과.
        DELL 재현: pivot 447.88(리테스트저항)/risk_pct 9.06%로 이전 수동
        진단과 정확히 일치 확인.
v5.38 [UI] 패턴 탭 진입신호등(entrySignal, static/index.html)에서 손익비
        (rr>=2) 항목 제거. `rr_info()`가 목표를 "최소 2R 보장"으로 항상
        끌어올리는 구조라 rr은 절대 2 밑으로 안 내려감 — 실측(4패턴
        227건 전수) rr>=2 통과율 100%, 정보 없는(0비트) 체크였음.
        [중간 시도, 되돌림] rr 대신 target_basis(전고/측정이동=차트
        근거 있음 vs 2R=폴백)로 바꿔봤으나, target_basis가 '2R'로
        떨어지는 이유 자체가 "리스크%가 넓어서(패턴 탭 중앙값 21%)
        2R 바닥이 다른 목표보다 높게 걸림"이라(109/109 전건 확인)
        기존 리스크 항목과 사실상 같은 사실을 중복 계상하는 문제가
        있었음(리스크 넓음 하나가 리스크warn+목표근거warn 둘로
        갈라져 경고2개→자동 🔴). 4패턴 전부 🟡가 🔴로 밀리는 부작용만
        확인되고 판정 정보는 안 늘어나서 제거로 정리.
        [검증] 제거 전후 4패턴 등급분포 사실상 동일(오차는 스크립트
        실행 시점차의 자연 시세변동) — 정보 없는 항목이었다는 실측
        재확인. 급등매집 과거 1년 🟢비율도 0.58%로 동일(22건 그대로).
v5.37 [버그수정] analyze_pattern(패턴 탭)에 is_kr 파라미터가 아예 없어서
        _rr_block()에 is_kr=False가 하드코딩돼 있던 것 수정. 조사 결과
        analyze_pattern은 도입 커밋(2e33d2e, v4440, 2026-07-02)부터 이
        상태였고, _rr_block 호출 6곳(analyze/turnaround/breakout/
        boxbreak/imminent/pattern) 중 이 함수만 자기 is_kr을 못 넘기고
        있었음(나머지 5곳은 정상). 영향은 제한적이었음을 실측 확인—
        _risk_hard_ok()를 애초에 안 불러서 하드게이트로 종목이 사라지는
        문제는 없었고, is_kr이 바꾸는 건 risk_warn 임계치(8→12%) 하나뿐
        인데 프론트 진입신호등(entrySignal)은 risk_warn을 안 읽고
        s.market으로 직접 재계산해서 신호등 등급엔 영향 없음 — 유일한
        가시적 효과는 KR 히트 중 risk_pct 8~12% 구간(오늘 16건)의
        `data.warn_count`(⚠️경보 배지) 과다계상뿐이었음.
        [추가] is_kr이 없다 보니 badge_fields()(베이스품질/손절폭ATR판정/
        약세장적격/UD신뢰도 — analyze·analyze_breakout·analyze_imminent가
        쓰는 공통 헬퍼)도 통째로 안 불려서 패턴 탭 카드에만 이 배지들이
        안 뜨고 있었음. badge_fields() 자체의 docstring이 "기존엔
        analyze에만 있어 돌파임박 탭에 배지가 안 떴음"이라고 과거 같은
        종류의 누락을 문서화해뒀는데도 patttern 탭 추가 시(v4440) 또
        반복된 것 — 의도적 배제였다는 근거(주석 등)가 없어 누락으로
        판단, 추가함. 프론트(static/index.html)는 base_badge/stop_wide/
        bear_ok 렌더링에 mode 제외 조건이 없어(코드 확인) 프론트 수정
        없이 자동으로 뜸.
        [실측] 수정 전후 패턴 탭 히트 건수·등급분포 거의 동일(badge_fields
        는 필터가 아니라 필드 추가라 정상) — 급등매집 0/6/57→0/6/57 등급
        분포 불변. `vol_high`(ATR 변동성 체크)는 badge_fields에 없는
        analyze() 전용 필드라 이번 수정으로도 여전히 안 채워짐(0건,
        신호등 6번째 체크는 패턴 탭에서 계속 스킵 — 별도 결정 필요시
        후속 작업).
v5.36 [UI] 패턴 탭에 서브탭 추가(static/index.html) — 전체/☕컵앤핸들/
        🚩치솟은깃발/🔻🔻더블바닥/🎆급등매집. 스캔은 그대로 1회(analyze_pattern
        이 이미 4개 서브디텍터 중 quality 최고 하나만 `pattern` 필드로
        반환) — 서브탭은 클라이언트에서 `h.pattern` 값으로 거르기만 함,
        백엔드 변경 없음. 각 서브탭 라벨에 현재 시장·검색·주도주만 등
        다른 필터를 전부 반영한 개수 표시(0개는 회색으로 흐리게, 클릭은
        그대로 됨 — "조건 통과 종목 없음" 안내). 기존 필터(한국/미국/
        주도주만/손절좁은순/적격만/매집순)와 자유 조합 가능. 서브필터
        상태는 다른 탭으로 이동해도 유지되지만(기존 시장필터 관례와
        동일), mode!=='pattern'일 때는 서브탭 바 숨김 + 필터 자체가
        안 걸리게 이중으로 게이팅(`renderPatternSubTabs` 내부 체크 +
        `renderCards`의 필터 적용 조건 둘 다 mode 확인). 패턴별 기본
        정렬(예: 컵앤핸들 피벗근접순)은 범위 밖 — v1 제외.
v5.35 [개명] 패턴 탭의 "ABC상한가" → "급등매집"(scanner.py 문자열/주석,
        app.py:3693 부근 디버그 엔드포인트, static/index.html:978, 내부
        함수명 `_pat_abc`→`_pat_surge_accum`). ABC(문서 방식) 신규 탭 조사
        중 이름이 겹쳐서 붙인 개명 — 그 탭 자체는 선별력 검증에서 음성
        결과가 나와 보류(조사 기록: docs/abc_doc_style_tab_investigation.md).
        accum_score/qa_score 등 매집 채점 함수는 이름에 "abc"가 안 들어가
        있어 이번 개명과 무관, 그대로 재사용.
v5.34 [진단] /api/debug/{ticker}에 rs_raw_score/rs_quarters_used 추가.
        price_ago 재정규화(v5.32)로 상장 200~252봉 종목이 3분기짜리
        점수를 받는데, 지금까지는 이걸 눈으로 확인할 방법이 없었음(전체
        유니버스 스캔에서는 최종 백분위만 남고 원점수·분기수는 버려짐).
        진단 엔드포인트는 종목 1개만 처리해서 비용 무시할 만함(핫패스인
        전체 스캔 루프에는 추가 안 함). 카드 표시는 보류 — API로 충분.
        [테스트] test_scanner.py Case7/Case10이 v5.32 이전부터(각각
        2026-06-28/06-19, TURN_CONFIG.rs_min 30→70·CONFIG.rs_min 50→80로
        올라간 시점) 깨져 있던 걸 확인 — 시나리오 자체가 틀린 게 아니라
        테스트가 하드코딩한 rs_rank=55가 그새 올라간 게이트를 못 넘어서
        RS 필터에서 즉시 탈락하고 있었음(로직 버그 아님). rs_rank를
        각 탭의 현재 rs_min보다 확실히 위(72/82)로 올려 수정 — 29개
        케이스 전부 통과.
v5.33 [도구] min_bars 감사 린터(test_min_bars_audit.py) 추가 — v5.32에서
        사람이 코드를 한 줄씩 읽어서 찾은 "게이트는 통과하는데 내부
        rolling/iloc이 더 많은 봉을 요구하는" 결함 클래스를 AST 정적분석
        으로 재발 방지. scanner.py를 파싱해 각 analyze_*/헬퍼 함수의
        min_bars 게이트(cfg 기본값 또는 `if len(x)<N: return`류)를
        추출하고, 내부 rolling(N)/스칼라 iloc(N)이 게이트를 넘는지,
        자기클램프(rolling(min(N,len)))나 인덱스클램프(price_ago류)
        안티패턴이 있는지 검사. 헬퍼 호출은 인터프로시저로 전파(trend_grade
        같은 헬퍼가 자기 게이트로 스스로 방어하면 호출부 게이트가 낮아도
        안전 — 그렇지 않으면 헬퍼의 내부 요구치를 호출부 게이트와 대조).
        [검증] v5.32 패치 전 scanner.py(git 36c92fc)에 대해 실행 → 이번에
        고친 4건(rs_raw_score/rs_score_stage2의 INDEX_CLAMP, analyze_inverse
        의 SELF_CLAMP_ROLLING 2건, boxbreak/pattern→trend_grade의
        OWN_GATE_INSUFFICIENT+HELPER_EXCEEDS_GATE) 전부 FAIL로 재현 확인.
        패치 후 현재 코드는 0 FAIL(WARN 2건은 analyze_surge/inverse의
        `math.isnan(m200) or ...` 폴백 — 실제 보호돼 있음을 이미 확인한
        패턴이라 non-blocking으로 유지). pytest로 실행 가능
        (`test_no_min_bars_gaps`).
        [부수 정리] analyze_inverse의 vol50 `rolling(min(50,len(v)))`(원래도
        무해했음 — min_bars=60이 항상 커버) 및 rs_score_stage2의 price_ago
        `-min(days,len(c)-1)-1`(자기 게이트 253이 항상 커버) — 둘 다 실제
        결함은 아니었지만 린터가 안티패턴 모양 자체로 잡아서(게이트가 우연히
        충분한 것과 코드가 안전한 건 다른 문제) 같이 정리, 클린 기준선
        확보.
v5.32 [버그수정] "min_bars는 통과하는데 내부가 더 요구" 클래스 결함 전수 감사
        (KR 485봉/US 501봉 기준 표로 정리) 후 확인된 4건 수정. fetch
        730일(KR)/2y(US) 확대(v5.28)의 부작용이 아니라 전부 원래 있던
        결함 — 확대로 US rs_score_stage2가 살아나면서(251→501봉) 같은
        클래스의 다른 결함들도 같이 드러남.
        [trend_grade] len(c)<200이면 등급 자체를 미부여("?")로 변경.
        예전엔 ma200/ma200_prev가 NaN인데도 "200일선 위·200일선 상승·
        60>200일선" 3개 조건이 NaN 비교(예외 안 남, 그냥 False)로
        무조건 실패 처리돼, boxbreak(min_bars=140)/pattern(130) 탭의
        신규 상장주가 130~199봉 KR+US 전종목(33건) 예외 없이 D급으로
        나오고 있었음(실측 확인). 200일선 조건은 상장 200일 미만이면
        "어려운 조건"이 아니라 정의 자체가 안 되는 질문이라, 점수를
        깎는 대신 판정 불가로 처리(옵션: 5조건 재정규화안도 검토했으나
        200일선이 빠진 자리를 다른 조건으로 채우면 "몇 조건 중 몇 개"가
        불명확해져 폐기). static/index.html이 이미 grade==='?'일 때
        배지를 렌더링하지 않아 UI 변경 불필요.
        [analyze_inverse] ma200 = c.rolling(min(200, len(c))).mean()
        자기 클램프 제거. 클램프가 항상 유한값을 만들어 뒤에 있던
        math.isnan(m200) 폴백(데이터 부족 시 200일선 비교를 건너뛰려던
        의도)이 완전히 죽어 있었음. 현재 인버스 유니버스 15종목(US
        10+KR 5) 전부 484봉 이상이라 실피해는 0건이지만, 짧은 이력의
        신규 인버스 상품이 유니버스에 추가되는 순간 재현되고 그때는
        아무도 원인을 못 찾을 상황이라 지금 수정. 수정 전후 15종목
        판정 동일함을 재확인.
        [rs_raw_score] price_ago의 idx = -min(days, len(c)-1)-1 클램프
        제거. 상장 200~252봉 종목(KR 11/US 17, 각 시장의 0.7~0.8%)에서
        "252거래일 전 가격"을 상장 초기 첫 봉 가격으로 조용히 대체하고
        있었음 — 최대 ±0.14점(원점수 스케일)까지 왜곡, 실측 사례
        WYFI(현재 +0.11 상위권 → 재정규화 후 -0.03 하위권 반전) 등.
        이제 부족한 분기는 제외하고 남은 가중치로 재정규화(accum_score
        와 동일 패턴) — 남은 분기가 1개 이하면 추세강도 지표로 의미가
        없어 None. outer gate 200 특성상 현재는 q4(252일)만 빠질 수
        있지만 향후 게이트 완화 대비 일반화. 진단용 rs_quarters_used()
        헬퍼 추가(핫패스 미사용, 종목별 "몇 분기짜리 RS인지" 조회용).
        [STAGE2_CONFIG] min_bars 260→262. 기존 260은 "200일선+52주+
        1개월 기울기 여유"라는 주석뿐 근거 있는 유도값이 아니었음 —
        실제 내부 최대 요구치는 lo52/hi52의 무가드 c.iloc[-252:]가
        만드는 252, 마진 8은 rs_score_stage2가 253 요구인데 US
        period="1y"(251봉)로 100% 죽었던 것과 같은 구조라 재발
        가능했음. 252 + 10봉 버퍼(다른 탭들의 "200요구+210게이트"
        관례와 동일 폭)로 근거를 주석에 명시하며 262로 조정.
        [검증] 4건 전부 기존 test_scanner.py 통과(사전에 존재하던
        Case7/10 실패는 무관, git stash로 미수정 버전에서도 동일 실패
        확인) + 각 수정이 실측 대상 종목에서 기대한 값을 내는지 재확인
        (trend_grade 33/33 '?', 인버스 15종목 판정불변, rs_raw_score
        28/28 사전계산과 일치).
v5.26 [버그수정] A-B-C 상한가(scanner.py _pat_abc)의 A구간(수렴 판정)이
        a_start = max(a_end-55, 0)로 55봉 고정이었던 문제. 실데이터
        검증(453340/192440/084370 등) 결과 종목별 실제 "수렴이 깨지는
        지점"이 5봉~199봉까지 전혀 달라서, 55라는 값 하나로 뭉뚱그리면
        a_range(고저폭) 게이트가 부정확해지고(잠깐 요동친 종목이 탈락,
        훨씬 길게 수렴 중인 종목이 통과) base_len(="형성 N일째" 배지)도
        사실상 상수 55로 고정돼 의미가 없었음.
        [수정] a_end에서 한 봉씩 뒤로 확장하며 누적 고저폭이 25%를 넘기
        직전까지 늘리는 적응형 방식으로 교체(while 루프, O(1) 증분
        running_hi/lo로 전체 O(n)). 무한 확장 방지로 250봉 상한, 최소
        25봉 하한은 기존 유지.
        [회귀] 22종목 표본 재검증 — 판정이 바뀐 건 3건, 전부 A구간이
        길어지며 a_hi가 올라가 first_wave(peak가 A상단+15%↑) 조건이
        깨져 통과→탈락. 직접 주봉 확인 결과 셋 다 9~10개월 박스권+
        기간수익률 마이너스로 "상승 1파"가 없었던 게 맞아 올바른 판정
        변화로 확인(first_wave 로직 자체는 무변경). 전체 KR
        유니버스(1502종목) 재스캔 — 예외 없이 완료, ABC상한가 히트
        11→14건.
        [매집 스코어 영향 확인] v5.19 accumulation_score/v5.24
        quiet_accumulation_score가 A구간(a_start~a_end)을 입력으로 쓰는
        걸 확인하고, 적응형으로 바뀌면서 a_start가 데이터 시작(0)까지
        밀리는 케이스가 늘어나는지 전체 유니버스 기준으로 신/구 버전
        직접 비교. 결과: a_start==0 히트 0→0건, 그중
        quiet_accumulation_score "데이터부족" 실격도 0→0건 — 증가 없음
        확인(3건 이상이면 별도 커밋으로 오프바이원 수정 예정이었으나
        불필요 판정). scanner.py 주석으로만 이 엣지케이스(a_start==0일
        때 quiet_accumulation_score가 항상 실격되는 기존 오프바이원)를
        남겨둠 — 이번 수정으로 새로 생긴 버그 아님.
v5.25 [버그수정] 조용한 매집 스코어(v5.24)의 55~74점 등급 라벨
        "🔷매집흔적"이 기존 accum_score(v5.19) 배지 "🧲 매집흔적 N"과
        텍스트가 겹쳐, 같은 ABC상한가 카드에 "매집흔적"이 두 번(다른
        숫자로) 뜨는 혼동 — v5.24 배포 직후 사용자가 스크린샷으로 직접
        확인해 리포트. "🔷매집조짐"으로 교체(scanner.py
        _quiet_accum_grade만 수정, 임계값·점수 계산 로직은 무변경).
v5.24 [기능추가] U/D 신뢰도 필터 + 조용한 매집 스코어 — 사용자 리포트
        (한울반도체 320000): 일봉 U/D 1.86이 건강해 보였지만 실제로는
        이틀(06/22~23) 거래량이 상승거래량의 68%를 차지한 이벤트성
        급등(그 주 고점 대비 종가 완전 반납)이라 매집이 아니라 순환매
        였음 — 지표 결함이었지 데이터 오류가 아니었음.
        [Task 1] scanner.py에 ud_volume_detail(c,v,window=50) 신규 —
        top1_share/top3_share/hhi/n50/ud_ex_top1/ud_ex_top3/ud_reliable
        반환. ud_reliable=(top1_share<0.40) AND (top3_share<0.65), 재현
        검증 결과 임계값은 그대로 유지(내리지 않음). badge_fields()에
        전부 부착 — analyze/analyze_imminent 등 badge_fields를 쓰는 모든
        탭에 자동 전파. 카드 U/D 표시를 "1.86" → "1.86 (상위3일 제외
        0.59)"로 변경(ud_ex_top3 기준, ud_ex_top1은 진단용 dict 보관만),
        top1_share≥40%면 "⚠️상위1일 N%", 아니면 top3_share≥65%면
        "⚠️상위3일 N%" 뱃지(고정 문구 대신 실측 비중 숫자 표시).
        [거래량 위축/증가 판정 교정] 단일 봉 값을 직접 비교에 쓰면
        미완성 봉·휴장 직후 -80%대 허위 신호가 남 — _vol_ma_ratio(5일
        이동평균÷장기평균) 원칙으로 통일, Task 2 구성요소 2·5에 적용.
        [Task 2] scanner.py에 quiet_accumulation_score(df,window=60)
        신규 — 실격조건 4개(단일일 급등≥15%/최대거래량≥8배/구조훼손/
        신규: 상위3일거래량비중≥35%로 이틀·사흘짜리 사건 커버) 통과 시
        6개 구성요소(자금흐름CLV·하락일거래량위축·거래량분산도(Task1
        재사용)·변동성수축·후반부거래량증가·가격안정성) 가중합 0~100점.
        데이터부족(상장60일미만)은 score=None+사유로 실격(score=0)과
        구분. A-B-C 상한가 디텍터의 A구간에 부착(_pat_abc, 기존
        accum_score와 별개 지표, 원본 무변경) + 눌림목 탭(analyze)에도
        부착 — 둘 다 개별 try/except로 격리해 계산 실패가 tab 전체를
        죽이지 않게 함. UI: 🔵강한매집/🔷매집흔적/⚪중립/⛔없음 등급
        뱃지, 실격 시 사유 뱃지(⛔이벤트성 등), 승률/확률 문구 없음.
        검증: 320000 실측(top3_share 0.685>0.65로 ud_reliable=False
        확정, 실격조건 4개 전부 독립 발화), 하락일 없는 종목·상장
        60일미만·상승일 1개뿐인 합성 엣지케이스 전부 예외 없음 확인,
        실제 ABC 히트 3종목(004650/052330/340570)에서 qa_score 정상
        계산 확인, card() 4개 시나리오(패턴/눌림목/강한피벗/실격) Node
        렌더링 크래시·undefined 없음 확인. test_scanner.py 회귀 없음
        (기존 실패 2건은 이 작업 이전부터의 베이스라인, scanner.py에
        영향받지 않는 케이스).
        [알려진 사소한 이슈] Task 2의 55~74점 등급 라벨 "🔷매집흔적"이
        기존 v5.19 accum_score 배지 문구("🧲 매집흔적 N")와 텍스트가
        겹침 — ABC 카드에서 두 지표가 동시에 뜨면 "매집흔적"이 두 번
        보일 수 있음(사용자 스펙에 명시된 라벨 그대로 구현, 필요시 추후
        문구 조정 검토).
v5.23 [기능개선] 📊섹터 탭 표에 뜨는 상위 종목(코스피/코스닥/미국 각 셀의
        상위 5개)이 그냥 텍스트라 클릭이 안 됐음 — 사용자 요청으로
        트레이딩뷰 차트 링크 연결(다른 탭 카드/마감정리와 동일한 tvUrl()
        패턴). /api/sectors가 이미 top 항목마다 ticker를 주고 있어서
        (KR은 .KS/.KQ 접미사 포함) 백엔드 변경 없이 static/index.html만
        수정 — tvUrl()이 접미사로 KR/US를 자동 판별. Node로 렌더링 결과를
        직접 실행해 KR(KRX: 접두어)·US(심볼만) 링크가 각각 올바르게
        생성됨을 확인.
v5.22 [기능개선] strong_pivot 탭을 "초기 국면 + 매집 우위 + 강한 조건 겹침"만
        남도록 3단 필터로 강화 — analyze_imminent/analyze_stage2/
        analyze_ibd9_* 원본은 무수정, _run_scan_strong_pivot만 수정.
        1층(하드컷): late_level≠none(후기 스테이지) 또는 ext200_pct가
        STRONG_PIVOT_MAX_EXT200(30) 초과면 제외 — 이미 크게 오른 뒤의
        피벗은 "초기 국면"이 아니므로.
        2층(매집 필수화): 품질풀(Stage2/IBD9) OR게이트 통과 + U/D Volume
        Ratio가 STRONG_PIVOT_MIN_UD(1.0) 미만이면 제외 — 구조가 좋아도
        지금 매집 중이 아니면 배제.
        3층(강도 스코어): pool_count(20점/개, 최대 40) + 매집강도(최대
        20점, ud_vol을 1.0~3.0 구간에서 선형 환산) + 두드림(최대 15점) +
        거래량수축/변동폭축소(각 10점) + RS(최대 15점)로 strength_score
        산출 → 내림차순 정렬. pool_count>=2(Stage2+IBD9 동시 통과)면 그
        자체로 최상위 신호로 보고 강도 컷 면제, 아니면
        STRONG_PIVOT_MIN_STRENGTH(40) 미만이면 제외.
        임계 상수 3개 전부 파일 상단에 분리 — 배포 후 diag(imminent_pass/
        dropped_late/dropped_ext200/gate_pass/dropped_accum/dropped_weak/
        final_hits)를 보고 단계별로 완화 가능.
        실제 실행 검증(660종목: KR60+US600): imminent_pass 28→dropped_late
        7→dropped_ext200 15→gate_pass 1→dropped_accum 0→dropped_weak 0→
        final_hits 1(SNOW). strength_score 66.0을 가중치 수식으로 손계산해
        정확히 일치함을 확인(20+7.1+6+10+10+12.9=65.98≈66.0).
        [카드 버그 추가수정] strong_pivot 카드의 4번째 지표를 RSI에서
        ud_vol(U/D 거래량)로 교체 — 이 탭의 핵심 신호(매집 우위)를 카드에서
        바로 보이게. Node로 card() 추출해 크래시 없음 + 'undefined' 없음
        확인.
v5.21 [버그수정] strong_pivot 카드에 "눌림 깊이/지지선"이 undefined로 뜨는
        문제 — 사용자 리포트.
        [원인] static/index.html의 card() 메인 지표(metrics) 블록은
        s.mode별로 분기하는 별도의 삼항연쇄인데(배지 블록과는 다른 체인),
        v5.20에서 strong_pivot을 이 체인에 추가하지 않아 마지막 default
        분기(눌림목 전용: pullback_pct/support_ma/ma_dist_pct)로 떨어짐.
        analyze_imminent는 이 필드들을 아예 반환하지 않아 전부 undefined로
        찍힌 것 — || 0 같은 방어값으로 가리지 않고 근본 원인(잘못된 분기)을
        고쳤다.
        [해결] s.mode === 'strong_pivot' 전용 분기 추가 — 눌림깊이/지지선
        대신 analyze_imminent가 실제로 주는 필드(피벗까지=pivot_dist_pct,
        두드림=touch_count, 피벗=pivot)로 표시. Node로 card() 추출해 실제
        렌더링 결과에 'undefined' 문자열이 없음을 확인, 기존 pullback 모드
        카드는 회귀 없이 그대로 눌림깊이/지지선 표시됨도 함께 확인.
v5.20 [기능추가] 실험 탭 strong_pivot("강한피벗") 신규 — analyze_imminent(피벗
        형성 중, 원본 미수정) ∩ (Stage2 통과 OR IBD9 통과). Stage2=한국전용,
        IBD9=미국전용이라 게이트가 시장별로 자연히 갈리고, OR 게이트라 0개
        방지. 기존 analyze_imminent/analyze_stage2/analyze_ibd9_*/
        _run_scan_stage2/_run_scan_ibd9 원본 함수는 전혀 수정하지 않고 각
        파이프라인의 hits ticker 집합만 재사용(_run_scan_strong_pivot이
        내부에서 _run_scan_stage2/_run_scan_ibd9를 그대로 호출, 실패해도
        try/except로 빈 집합 처리해 이 탭 하나가 죽어도 나머지엔 영향 없음).
        run_scan()에 분기 한 줄, /api/scan 허용 mode 목록에 한 항목만 추가
        — 순수 additive(scanner.py 무변경, app.py는 새 함수+분기 2줄 뿐).
        UI: 탭 목록에 "강한피벗"(실험 태그) 추가, 카드에 통과한 풀(📐Stage2 /
        🇺🇸IBD9)을 배지로 표시.
        배포 전 실제 실행 검증: (1) 실 데이터 660종목(KR60+US600) 스캔 →
        imminent_pass 28, gate_pass 4, HPE/FTNT/SNOW/GH 4건 실검출, 정렬
        순서(pool_count desc→triggered desc→score desc) 확인. (2) 프론트
        card() 함수를 Node로 추출해 1풀/2풀 배지 모두 크래시 없이 렌더링
        확인. (3) test_scanner.py 기존 케이스 회귀 없음 확인(scanner.py를
        아예 건드리지 않아 origin/main과 diff 0 — 기존 실패 2건은 이 작업
        이전부터 있던 베이스라인이며 무관함).
v5.19 [기능추가] A-B-C 상한가 패턴에 매집 스코어(accumulation_score) 추가.
        기존 ABC 감지기(_pat_abc)가 찾아낸 A구간(횡보 베이스)의 일봉 OHLCV만
        가지고 "조용한 물량 수집" 흔적을 0~100점으로 채점 — 이미 걸린 ABC
        후보들 사이의 순위 정렬용 보조 지표. 진입 신호도 상한가 확률도 아님
        (승률 문구 없음, 백테스트 없음).
        6개 지표(거래량비/ATR수축비/종가강도/U·D거래량/거래량증가비/변동폭비)
        를 정규화해 가중합, 데이터 부족(A<15봉, 기준윈도우<40봉, 거래량 결측
        3봉+)이면 0점이 아니라 score=None+reason으로 구분. ud_vol만 하락봉이
        없어 계산 불가할 때는 그 가중치(0.10)를 제외하고 나머지로 재정규화
        (신규 test_udvol_renorm.py로 수기 계산과 정확히 일치함을 확인).
        analyze_pattern()이 하위 감지기 dict를 통째로 안 넘기고 필드를
        수동으로 골라 담는 구조라, ABC가 이겼을 때만 accum_* 필드를 넘기도록
        명시적으로 연결(안 하면 조용히 누락되는 함정이었음).
        실제 KR 종목 스캔(1504종목 중 364개 확인)에서 5건 실검출로 점수가
        vol_ratio/ud_vol 동시 충족 종목(42점)과 무충족 종목(18점)을 구조적
        으로 구분함을 확인. scanner.py는 순수 additive(190줄 추가/0줄 삭제,
        기존 ABC 게이트 조건 무변경).
        UI(static/index.html): 🧲매집흔적 배지(65점↑ 강조, 미만은 흐리게),
        ⚠️매집판정불가 배지(None+사유), 툴팁에 6개 원값+A구간 날짜(승률
        문구 없음), "🧲 매집순" 정렬 토글(None은 맨 뒤) 추가.
        /api/debug/{ticker}에 "매집채점" 섹션 추가 — ABC 아니면 "ABC패턴
        미검출 — 매집채점 대상 아님"으로 명시.
v5.18 [기능개선] 카드에 섹터가 대부분 안 붙는 문제 — 사용자 리포트(SK하이닉스
        같은 극소수만 붙고 대부분 "기타"라 안 보임).
        [원인] sectors.py의 SECTOR_MAP은 "AI 데이터센터 밸류체인" 테마 위주로
        손으로 큐레이션한 좁은 목록(반도체/데이터센터/클라우드 등)이라, 그
        밖의 종목은 전부 "기타"로 빠짐 — 프론트가 "기타"는 표시 자체를
        생략해서 섹터가 안 보이는 것처럼 느껴짐. 미국은 이미 us_sectors_auto
        (외부 데이터셋, 2072종목)로 보완돼 있었는데 한국은 이 보완이 아예
        없었음.
        [해결] kr_sectors_auto.py 신규 — 네이버 금융 "업종별시세"(79개 GICS
        스타일 업종 분류)를 전부 스크레이핑하고, 시가총액 순위 페이지와
        교차 매칭해 .KS/.KQ 접미사를 확정한 뒤 2685종목 매핑을 생성(us_
        sectors_auto.py와 동일한 "1회 생성 정적 파일" 패턴). _sector_of()가
        sectors.py 정밀 매핑 → 없으면 kr_sectors_auto/us_sectors_auto 순으로
        폴백하도록 수정. 실사용 예시로 검증(브이엠 089970.KQ, 타이거일렉
        219130.KQ가 기존엔 "기타"였는데 이제 "반도체와반도체장비"로 정확히
        붙음 — app.py를 실제로 import해서 _sector_of() 직접 호출로 확인).
v5.17 [기능개선] 💰실적우수 탭 — RS70+ 전체를 확인 대상으로 확대(사용자
        요청: "조건에 맞는 건 다 검색되면 좋겠다, RS70+ 이상"). 이전엔
        RS70+ 중에서도 상위 20개만 확인해서(요청 하나 안에서 기다리던 시절의
        속도 걱정 때문에 v5.07~v5.09에서 넣은 제한) 3400여 종목 중 7개만
        나왔는데, 이는 "실적이 좋은 종목이 드물다"가 아니라 "RS70+ 종목의
        대부분을 애초에 확인조차 안 했다"는 뜻이었음.
        v5.14부터 실적 조회가 완전히 백그라운드로 빠져 사용자 응답 속도엔
        영향이 없으므로: EARNINGS_TAB_MAX_CHECK 20→5000(사실상 무제한),
        EARNINGS_TAB_DEADLINE_SEC 45초→30분(백그라운드 파이프라인 전체
        예산), 완료 결과 캐시 10분→6시간(스캔 자체가 오래 걸리는데 짧게
        버리면 끝나자마자 또 처음부터 도는 낭비 방지), 격리 스레드풀
        (_earnings_executor) 4→6워커로 확대(격리돼 있어 다른 엔드포인트엔
        영향 없음). 종목 수가 많아진 만큼 첫 백그라운드 스캔은 수십 분
        걸릴 수 있음 — 이후엔 6시간 캐시로 즉시 응답.
v5.16 [버그수정] v5.15 배포 후에도 동일 — 진짜 크래시 지점을 마침내 찾아
        Node로 재현·검증 완료.
        [진짜 원인] card()가 모든 모드에서 무조건 sparkSVG(s.spark,
        s.spark_ma20)를 호출하는데(스파크라인 차트), _run_scan_earnings_inner
        가 만드는 hit 딕셔너리엔 이 필드 자체가 없었음(다른 모든 analyze_*
        함수는 spark/spark_ma20을 채워 반환하는데 이 파이프라인만 app.py에서
        직접 딕셔너리를 만들면서 빠뜨림). sparkSVG 내부의
        `closes.concat(...)`이 undefined.concat()으로 100% 크래시 —
        annual_eps의 null 값(v5.15에서 고침)보다 먼저 실행되는 코드라, v5.15
        수정은 맞는 수정이었지만 이 크래시에 가려 효과가 안 보였음.
        [검증] Node로 card() 함수를 실제 index.html에서 그대로 추출해
        (a) spark 필드 없는 실제 응답 모양으로 호출 → 정확히 동일한 에러
        재현 확인, (b) spark 필드를 채운 수정 버전으로 호출 → 정상 렌더링
        확인. 추측이 아니라 실행해서 확인 후 배포.
        [해결] _run_scan_earnings_inner의 hit 딕셔너리에 spark/spark_ma20
        추가(다른 analyze_* 함수들과 동일한 방식, c.iloc[-60:] 기반).
v5.15 [버그수정] 💰실적우수 탭이 계속 "재시도" 화면에 걸려있던 진짜 원인 —
        사용자가 Chrome 개발자도구 Network 탭 응답 원문을 직접 캡처해줘서
        확정.
        [진짜 원인] 서버는 v5.05부터 계속 200 OK로 정상 데이터(후보 7개
        포함)를 잘 주고 있었음! 문제는 프론트: 카드에 "연간 EPS 추이"를
        그릴 때 `annual_eps` 배열의 값을 전부 `.toLocaleString()`으로
        포맷했는데, 실제 응답엔 추정치가 아직 없는 연도가 `null`로 옵니다
        (예: 제주반도체 [486, 567, 1147, null]). null.toLocaleString()은
        자바스크립트 에러를 던지고, 이 에러가 renderCards() 안에서 터지면서
        load()의 try 블록 전체가 실패 처리돼 catch로 떨어짐 — 그래서 서버는
        완벽하게 성공했는데 화면엔 v5.11 시절 "재시도" 메시지가 계속 뜨는
        모순이 생겼음. v5.09~v5.14에서 계속 백엔드(타임아웃/격리풀/전체
        마감시각/pending 아키텍처)만 고치고 있었던 게 전부 헛다리였던 이유 —
        진짜 문제는 백엔드가 아니라 프론트 렌더링 코드 한 줄이었음.
        [해결] annual_eps 배열 렌더링 시 null 값은 '-'로 표시하도록 방어
        코드 추가(`v == null ? '-' : v.toLocaleString()`).
        [교훈] 사용자가 크게 도와준 부분 — 브라우저 개발자도구로 실제 HTTP
        응답 원문을 직접 확인한 게 결정적이었음. 서버 로그만 봐서는 이
        버그를 못 잡았을 것(서버 입장에선 완전히 정상 처리였으므로).
v5.14 [버그수정] 사용자 재확인: "다른 탭이랑 로딩하는 게 다르다. 왜 자꾸
        재시도를 하냐, 재시도 하지 말라고. 다른 탭 참고해." — 정확한 지적.
        [원인] 다른 스캔 모드는 가격 번들만 준비되면 나머지(필터링·정렬)가
        전부 CPU 연산이라 요청 하나 안에서 즉시 끝남. 실적우수만 유일하게
        그 뒤에 "네트워크 조회"(RS70+ 상위 20종목의 실적 확인, 최악 45초)를
        같은 요청 안에서 추가로 더 기다리고 있었음 — 이게 다른 탭과 로딩
        경험이 다르게 "느리고 계속 재시도하는" 것처럼 보인 진짜 이유.
        v5.09의 45초 "마감시각"은 이 대기 자체를 없앤 게 아니라 상한선만
        둔 것이라 근본 해결이 아니었음.
        [해결] _fetch_market_data의 콜드스타트 처리(v5.12)와 완전히 같은
        패턴 적용: _run_scan_earnings을 얇은 래퍼로 바꿔, 실적 조회(무거운
        부분)는 _run_scan_earnings_bg()로 백그라운드에 위임하고 이 함수
        자체는 즉시 반환한다 — 완료된 결과가 있으면(10분 캐시) 그대로,
        없으면 매번 즉시 pending 응답. 이제 실적우수도 다른 탭처럼 모든
        요청이 항상 순간적으로 끝나고, 실제 계산은 사용자가 보지 않는
        백그라운드에서 진행되다가 완료되면 다음 폴링(v5.12, 5초 간격)에
        자연스럽게 반영된다.
v5.13 [버그수정] v5.12 배포 후 "강제 새로고침(Cmd+Shift+R)을 해도" 여전히
        v5.11의 옛날 재시도 문구("자동 재시도 X/24")가 뜨는 것 확인 — 새
        JS 자체가 브라우저에 전혀 로드되고 있지 않다는 뜻.
        [원인] PWA 서비스워커(sw.js)가 API가 아닌 요청(메인 HTML 문서 포함)
        은 그냥 fetch(e.request)만 하고 있었음(cache 옵션 없음). 서비스워커의
        fetch 핸들러를 거치는 요청은 브라우저의 "하드 리프레시로 캐시 우회"
        지시가 서비스워커 내부 fetch 호출에는 안 이어질 수 있어서, 사용자가
        아무리 강하게 새로고침해도 서비스워커가 계속 예전 HTML/JS를 내려주는
        PWA의 잘 알려진 함정에 걸려 있었음. 게다가 서버의 "/" 라우트도
        FileResponse 기본값이라 Cache-Control 헤더 자체가 없어 브라우저가
        자체 판단으로 캐싱할 여지도 있었음(v4.92에서 /api/*엔 이미 막아뒀지만
        메인 HTML 자체는 안 막았던 사각지대).
        [해결] sw.js: 메인 문서(navigate 요청 · '/' · /api/*) 전부 명시적
        cache:'no-store'로 강제. app.py: "/"와 "/sw.js" 라우트에 명시적
        no-cache 헤더 추가(서버 쪽 이중 안전장치, /api/* no-cache 미들웨어와
        같은 철학). 이미 등록된 서비스워커가 새 sw.js를 감지하려면 페이지를
        한두 번 더 새로고침해야 할 수 있음(브라우저가 다음 탐색 시 sw.js
        바이트 차이를 비교해 갱신 — skipWaiting/clients.claim은 이미 있어
        감지만 되면 즉시 적용됨).
v5.12 [버그수정] v5.11 이후에도 동일 — 사용자가 핵심을 짚어줌: "횟수가
        중요한 게 아니라 오래 기다려야 한다, 계속 새로고침하면 안 된다."
        [진짜 원인] v5.05~v5.11 내내 프론트 재시도 "횟수/간격"만 늘렸는데,
        정작 서버 쪽 콜드 스타트 분기(_fetch_market_data)가 요청 하나를
        수분~8분+ 동안 그대로 블로킹하고 있던 게 근본 문제였음. 이러면
        재시도를 몇 번을 하든 매번 새 요청이 또 몇 분짜리로 걸리고, 그 사이
        브라우저/Railway 엣지 등 중간 어디서든 한 번만 끊겨도 실패로 보임 —
        "재시도 횟수"가 아니라 "각 요청 자체가 너무 오래 걸린다"는 게 진짜
        문제였다는 걸 사용자 피드백으로 알아챔.
        [해결] 아키텍처를 바꿈: 콜드 스타트여도 이 요청은 더 이상 기다리지
        않는다. 데이터 수집은 백그라운드 태스크로 걸어두고 즉시
        pending:true로 응답 → 프론트는 이 응답을 실패가 아닌 "준비 중"으로
        인식해 5초 간격으로 가볍게(즉시 응답) 폴링한다. 어떤 단일 HTTP
        요청도 이제 수 초 이상 걸리지 않아 중간에 끊길 일이 없음.
        /api/scan(run_scan 경유)·/api/sectors·/api/eod 전부 이 패턴 적용.
        pending 응답은 결과 캐시에 저장하지 않음(안 그러면 실제 데이터가
        준비된 뒤에도 계속 pending만 보이게 됨).
v5.11 [버그수정] v5.10(재시도 3분)도 여전히 부족 — 사용자 확인: 실적우수뿐
        아니라 돌파임박/눌림목 같은 일반 탭도 평일 첫 스캔은 5분 넘게 걸릴
        때가 있음(주말엔 저장된 자료를 그대로 불러와 빠름). 즉 이건 실적
        우수 파이프라인의 버그가 아니라 전 탭 공통의 콜드 스캔 소요시간
        문제였음 — 다른 탭 재확인을 요청해서 얻은 결론.
        [해결] static/index.html load() 재시도 창을 3분(20초×9회)→8분(20초×
        24회)으로 확대. 실제 최대 소요시간(5분+)보다 넉넉한 여유를 둠.
v5.10 [버그수정] v5.09 이후에도 "상황 스크린샷과 똑같이" 로딩 실패 — 사용자가
        인터넷 문제/강제새로고침 문제인지 질문. 프론트 재시도 로직 자체의
        한계였음을 재확인.
        [진짜 원인] 유니버스 3,500+ 종목 콜드 스캔은 실제로 2~4분(스크린샷
        로그 기준 KR 175초+US 41초 ≈ 3.6분) 걸리는데, 프론트는 **15초 뒤
        딱 1번만** 재시도하고 그마저 실패하면 영구적으로 "스캔에 실패했습니다"
        를 띄우며 포기했음. 서버는 백그라운드로 계속 스캔 중이라 몇 분 뒤엔
        캐시가 채워져 성공하는데, 그 전에 영구 실패 메시지를 띄운 게 근본
        원인 — v5.07~v5.09에서 백엔드(타임아웃/격리풀/전체 마감시각)만
        고치고 있었지 이 프론트 재시도 시간이 애초에 콜드 스캔 소요시간보다
        훨씬 짧다는 걸 놓치고 있었음. "실적우수만" 유독 자주 겪은 이유:
        v5.05~v5.09 배포마다 Railway가 재시작해 캐시가 지워졌고, 사용자가
        매번 이 탭부터 먼저 재확인했기 때문(다른 탭은 그 사이 이미 한 번
        캐시가 데워진 뒤 열어본 것) — 탭 자체의 차이가 아니라 테스트 순서
        때문이었을 가능성이 큼.
        [해결] static/index.html load(): 재시도 간격 15초→20초, 횟수 1회→
        최대 9회(총 3분)로 확대 + 진행 상황("N초째, 자동 재시도 X/9") 표시.
        모든 탭의 콜드 스캔 UX가 같이 개선됨(실적우수 전용 수정 아님).
v5.09 [버그수정] v5.08 이후에도 💰실적우수 탭 로딩 실패 — 사용자 재확인,
        스크린샷으로 "06:44 캐시로 7개 후보 성공" 상태바가 남아있는데 재시도는
        계속 실패하는 것 확인.
        [원인] v5.08은 종목당 12초 타임아웃만 뒀는데, 후보가 20개면 배치
        (4개씩) 5번 × 최대 12초 = 최악 60초. 여기에 유니버스 데이터 자체가
        아직 캐시에 없는 콜드 상태(코드상 KR 175초+US 41초 소요, 스크린샷의
        타이밍 로그로 확인)까지 겹치면 총 응답시간이 200초를 훌쩍 넘어가
        브라우저/Railway 쪽에서 먼저 끊길 수 있었음 — "종목당" 타임아웃만으론
        전체 파이프라인 시간을 못 막는다는 게 이번에 새로 확인한 지점.
        [해결] 실적 조회 단계 전체에 45초 "마감시각"을 둠(EARNINGS_TAB_
        DEADLINE_SEC) — 넘으면 남은 배치는 건너뛰고 그때까지 찾은 결과만
        으로 즉시 응답. 응답에 partial 플래그 추가, 프론트는 "⏳ 일부만 확인"
        표시(재시도하면 종목별 6시간 캐시가 쌓여 있어 더 빨라짐). 이제
        최악의 경우에도 (유니버스 콜드캐시 시간) + 45초를 넘지 않음 —
        유니버스 자체가 콜드인 경우(다른 탭도 첫 스캔 2~4분 걸리는 것과
        동일한 현상)는 이 탭만의 문제가 아니라 앱 공통 동작이라 별도 탭을
        먼저 열어 캐시를 데운 뒤 실적우수 탭을 열면 우회 가능.
v5.08 [버그수정] v5.07 이후에도 💰실적우수 탭 로딩 계속 실패 — 사용자 재확인
        후 더 깊이 분석.
        [진짜 원인] v5.07의 asyncio.wait_for(timeout=12)는 "코루틴이 기다리는
        걸 포기"하게만 할 뿐 실제로 돌고 있는 스레드를 죽이지 못함(파이썬은
        스레드 강제종료 불가). yfinance의 income_stmt/quarterly_income_stmt는
        내부적으로 요청마다 timeout=30이 여러 번(크럼/쿠키/데이터) 걸릴 수
        있어 최악의 경우 스레드 하나가 60~90초를 붙잡을 수 있는데, 이걸
        앱 전체가 공유하는 _executor(max_workers=8)에서 그대로 돌렸음.
        배치마다 새 실적 조회를 또 던지니 느린/멈춘 요청이 쌓이면서 워커가
        고갈되면 이 탭은 물론 스캔·펀더멘털·분산체크 등 _executor를 쓰는
        다른 모든 엔드포인트까지 줄줄이 막힐 수 있는 구조였음 — v5.07은
        증상(응답 지연)만 가렸지 스레드 점유 자체는 못 막았던 게 근본 문제.
        [해결] 실적 조회 전용 격리 스레드풀(_earnings_executor, max_workers
        =4) 신규 — 최악의 경우에도 피해 범위를 실적 조회로만 한정하고 다른
        엔드포인트는 안전. /api/earnings, /api/debug의 실적성장 섹션,
        _attach_earnings_badges(배지 부착 — v5.07엔 타임아웃이 없었음, 이제
        추가), _run_scan_earnings 전부 이 풀로 통일. 배치 크기(BATCH)를
        4로 맞춰 풀 용량과 일치시키고, 조회 대상 40→20으로 축소해 전체
        최악 대기시간을 v5.07 수준(~60초)으로 유지.
v5.07 [버그수정] 💰실적우수 탭 로딩 계속 실패(다른 탭은 정상) — 사용자 리포트.
        [원인] yfinance income_stmt/quarterly_income_stmt는 크럼(crumb)/
        쿠키 인증이 필요한 엔드포인트라 Railway 서버 IP에서 야후 응답이
        느려지거나 막힐 수 있는데, 80종목을 배치(8개씩) 순차 대기하는
        구조에 종목당 타임아웃이 없었음 — 하나라도 오래 걸리면 응답 전체가
        안 끝나거나(체감상 "로딩 실패") asyncio.gather가 예외 하나로 전체
        스캔을 중단시킬 수 있었음. 로컬 20종목 테스트(정상 티커+ETF+지수+
        상장폐지 종목 포함)에선 크래시가 재현 안 됐지만, 프로덕션 네트워크
        변동성까지 가정한 방어가 빠져 있던 게 근본 문제.
        [해결] 종목당 12초 하드 타임아웃(asyncio.wait_for, 초과/실패 시
        '판정불가'로 넘어가고 스캔 계속) + asyncio.gather(return_exceptions
        =True)로 예외 전파 차단 + 조회 대상 80→40개 축소(최악 대기시간
        단축). 이 세 가지는 이미 IBD9/Stage2가 쓰던 "저비용 먼저 + 개수
        제한" 패턴에 "종목당 타임아웃"을 추가로 얹은 것.
v5.06 [신규] 💰실적우수 전용 탭 — earnings.py Phase 1 판정만으로 스캔(한국+
        미국 통합). 기존 배지(v5.05)는 그대로 두고, 별도 탭에서 "실적 조건만"
        통과한 종목을 직접 찾아줌.
        [배경] 사용자가 Stage2/IBD9엔 왜 배지가 안 뜨냐고 물어서 설명하는
        과정에서, 두 탭 다 필터 자체에 실적 조건이 없다(추세/유동성/RS만)는
        걸 확인 — 실적 좋은 종목을 보려면 전용 탭이 필요하다고 판단.
        [구현] _run_scan_earnings(): 유니버스 전체에 실적 조회를 걸면 절대
        안 끝나서, RS 백분위(이미 계산돼 있어 추가비용 0)로 먼저 거른 뒤
        상위 80개만 실적 조회(배치 동시 실행 + 6시간 캐시, IBD9/Stage2와
        같은 "저비용 먼저" 패턴). RS70+ 사전 필터라 이 문턱 아래 종목은
        실적이 아무리 좋아도 이 탭엔 안 뜸 — 특정 종목이 궁금하면
        /api/debug/{ticker}로 직접 확인 권장. static/index.html: 💰실적우수
        탭 + 전용 카드(RS/분기EPS YoY/매출YoY/가속여부/연간EPS 추이) 추가.
v5.05 [신규] 실적(EPS) 필터 Phase 1 — 💰실적우수 배지 + /api/earnings/{ticker}.
        [Phase 0 데이터 정찰 결과] 미국(yfinance) 20/20 샘플 성공(100%),
        한국(네이버 파이낸스 "주요재무정보" HTML) 19/20 성공(95%, 실패 1건은
        상장폐지·합병 종목이라 정상 — 스크레이핑 결함 아님). 이 결과로 미국+
        한국 둘 다 구현하기로 결정. 상세: earnings.py 모듈 docstring 참조.
        [판정 기준] 1) 3년 연간 EPS 연속 증가(실제치만) 2) 최근분기 EPS
        YoY≥25%(미너비니) 3) 매출 YoY 동반 증가 4) (선택 표시) 증가율 가속.
        데이터 부족/없음은 제외가 아니라 판정불가(verdict=unknown)로 반환.
        [구현] earnings.py 신규: 미국은 yfinance income_stmt/quarterly_
        income_stmt, 한국은 finance.naver.com/item/main.naver의 "주요재무
        정보" 표를 <thead> colspan(연간/분기 열 개수)으로 정확히 헤더 매칭
        (개수 추측 없음 — Phase 0에서 8~10개로 들쭉날쭉해 보인 건 초기 나이브
        정규식의 착시였고 실제론 header colspan으로 정확히 셀 수 있음을 확인).
        ⚠️ 이 네이버 페이지는 UTF-8(naver_kr.py의 다른 페이지들이 쓰는
        EUC-KR과 다름 — 잘못 지정하면 조용히 전부 실패).
        app.py: /api/earnings/{ticker}(6시간 캐시) 신규. run_scan()/Stage2/
        IBD9 세 파이프라인 전부 최종 hits에 배지 부착(유니버스 전체 아님,
        run_scan()은 상위 30개만 — v4.86에서 고친 스캔 속도 재악화 방지).
        /api/debug/{ticker}에 "실적성장" 섹션 추가. static/index.html에
        "💰 실적우수" 배지(카드 상단, 모드 무관 공통 위치) 추가.
v5.04 [신규] /api/pullback-signal/{ticker} — 얼마냐봇 "눌림 지지 진입" 알림용
        신규 엔드포인트. RS 백분위(사이트 전역 rs_ranks 재사용) · U/D Volume
        Ratio(매집/분산, up_down_volume 재사용) · 주봉 10EMA 거리(신규
        weekly_ema10()) · 21일 ATR%(atr() 재사용) · 월봉 50% 되돌림(신규
        monthly_retrace_50(), confluence 가산 참고용) 한 번에 반환.
        [배경] 봇에 "🚀 피벗 돌파" 외에 "📉 눌림 지지 접근" 알림을 새로 추가
        하려는데, RS 백분위와 주봉 EMA·월봉 되돌림은 스캐너에만 있는 전체
        유니버스 캐시가 필요해서(벤치마크 대비 초과성과, 리샘플링) 봇 단독
        구현 불가 — 이 엔드포인트로 노출.
        scanner.py: weekly_ema10(), monthly_retrace_50() 신규(일봉→주봉/월봉
        리샘플링, DatetimeIndex 기반). 게이트 임계값(RS≥90/U·D≥1.5/±2%)은
        봇 쪽에서 판정 — 운영 중 튜닝하기 쉽게 스캐너는 원시값만 제공.
v5.03 [신규] 🇺🇸IBD9 탭 — IBD/MarketSmith식 9조건 스크린(사용자 제공 스펙,
        미국 전용). 1.A/D Rating A/B 2.가격$5+ 3.50일평균거래량50만주+
        4.50일평균거래대금$500만+ 5.3개월수익률30%+ 6.21일ATR4%+ 7.베타1+
        8.펀드보유수20개+ 9.시총$2억+.
        [데이터 한계 — 명확히 선언] 8번(펀드 보유 수)은 yfinance 무료
        데이터로 IBD 원본과 동일한 정확한 개수를 못 구해서
        heldPercentInstitutions(기관 보유 비율)로 대체 — 참고용, 필터링엔
        안 씀. 1번(A/D Rating)도 IBD 고유 알고리즘(13주 가중)이 아니라
        기존 up_down_volume(U/D Volume Ratio, 50일)을 등급(A~E)으로 변환한
        근사치. 카드/설명에 전부 "근사치" 명시.
        [구현] 적용 순서: 가격데이터만으로 되는 저비용 5개(조건2~6, 캐시된
        일봉으로 즉시 계산) 먼저 → 통과한 종목 중 3개월수익률 상위 60개만
        yfinance .info(베타·시총·기관보유비율)로 고비용 3개(조건1·7·9) 확인
        — .info는 종목당 네트워크 왕복이라 무제한 조회하면 레이트리밋 위험.
        scanner.py: analyze_ibd9_cheap()/analyze_ibd9_full() 신규.
        app.py: 전용 파이프라인 _run_scan_ibd9() + /api/scan?mode=ibd9.
        static/index.html: 🇺🇸IBD9 탭 + 전용 카드(3개월수익률/ATR/베타/시총/
        거래량·거래대금/A·D등급 배지) 추가.
v5.02 [신규] 📐Stage2 탭 — 미너비니 Stage2 트렌드 템플릿 스캐너(사용자 스펙).
        적용 순서: 유동성 → RS백분위 → Stage2템플릿 → 거래량수축/MA수렴(필터)
        → A/B 티어링. 한국 전용(유동성컷이 원화 거래대금 기준이라).
        1) 유동성컷: 일평균 거래대금(20일) >= 20억원(KR).
        2) RS백분위: RS_score = 3M*0.4+6M*0.3+12M*0.3(로그수익률+클리핑,
           벤치마크 차감 없음 — 스펙에 언급 없어 절대수익률로 구현), 유동성
           생존자 안에서만 percentile 계산, >=70만 통과. 기존 rs_raw_score
           (1M+3M+6M+9M+12M, IBD가중, 지수대비 초과성과, 다른 탭 전용)와는
           완전히 별개 함수(scanner.rs_score_stage2) — 다른 탭 영향 없음.
        3) Stage2템플릿: 현재가>50MA>150MA>200MA, 200MA 최근 20거래일(≈1개월)
           상승, 현재가>=52주저점*1.30, 현재가>=52주고점*0.75(-25%이내).
        4) 거래량수축(최근5일평균<50일평균) + MA(5/10/20)수렴(스프레드<=종가
           3%) — 스펙에 "필터"로 명시돼 있어 가점이 아니라 통과 필수 조건.
        5) 티어링: A=52주고점 -10%이내, B=-10~-25%. 0개인 날 있는 게 정상
           (스펙에 명시된 기대치) — 필터 완화하지 말 것.
        구현: scanner.py에 rs_score_stage2()/analyze_stage2()/STAGE2_CONFIG
        신규. app.py에 전용 파이프라인 _run_scan_stage2() 추가 — 유동성
        생존자 안에서만 RS 백분위를 다시 매겨야 해서(적용 순서상 필수) 기존
        run_scan()의 범용 fn 디스패치와 분리. /api/scan?mode=stage2로 호출,
        응답 모양은 기존 모드와 동일해 프론트 공용 로직 재사용.
        static/index.html: 📐Stage2 탭 + 전용 카드(RS/50·150·200MA/52주위치/
        A·B티어 배지/거래대금) 추가.
v5.01 [버그수정] 거래량 없는 스파이크가 며칠 뒤 갑자기 정식 피벗으로 둔갑.
        [사례] 티쓰리: 거래량 없이 찍은 고가 때문에, 그 봉이 EXCLUDE(오늘·
               어제) 구간을 벗어나자마자 베이스천장 피벗이 2925→2950으로
               튐(사용자 리포트).
        [원인] scanner.py select_pivot()의 "베이스천장"(단기 피벗) = 직전
               2봉 뺀 5봉의 고가(High) raw 최댓값. "전고"(significant_
               resistance)는 최소 2회 터치를 요구해 노이즈를 걸러내는데,
               베이스천장은 그 필터가 없어서 거래량 없는 1회성 꼬리도 그대로
               저항으로 인정됐음.
        [해결] select_pivot()에 v(거래량) 인자 추가. 그날 거래량이 최근 20일
               평균의 50% 미만인 봉은 베이스천장 후보 고가에서 제외. analyze/
               analyze_turnaround/analyze_imminent 3곳 모두 v 전달하도록 수정.
v5.00 [신규] /api/opening-surge — 장 시작 10분 돈 유입(거래량 급증) 스캔용
        엔드포인트. 얼마냐봇이 09:10 KST에 1회 호출해 텔레그램 알림.
        유니버스 전 종목의 오늘 누적 거래량을 시간보정한 평소(50일 평균)
        예상 거래량과 비교해 급증(기본 3배↑ + 최소 거래대금 5억) 종목만 반환.
        종목별 API 왕복 대신 이미 캐시된 유니버스 일봉을 한 번에 훑음(수백~
        수천 종목을 09:00~09:10 사이에 개별 조회하면 시간 안에 못 끝남).
        wait_for_fresh=True로 강제 — 이 시각에 스테일 캐시를 쓰면 거래량이
        사실상 0으로 잡혀 무의미함.
v4.99 [버그수정] 마감정리/섹터요약 탭이 장중에 계속 숫자가 바뀌던 문제.
        [원인] 두 탭 다 "전일/마감" 요약이 취지인데(섹터요약은 docstring에도
               "전일" 명시), 실제 구현은 그때그때 마지막 봉(iloc[-1])을 다시
               계산해서 장중엔 그 순간의 미확정 등락률·거래대금 순위가 10분
               TTL마다 계속 반영됐음. 사용자가 "장이 열리면 전날 자료 기준으로
               그대로 유지해야 할 것 같은데 장중에 계속 로딩한다"고 지적.
        [해결] _market_session_key("kr")로 오늘 KR장이 아직 마감 전인지 판정.
               마감 전이면 마지막으로 "마감 확정" 상태에서 계산해둔 스냅샷을
               그대로 반환(재계산 안 함) — /api/eod, /api/sectors 둘 다 적용.
               장 마감(평일 15:40 KST) 후 딱 1번만 새로 계산해서 스냅샷 갱신.
        [주의] 배포 직후 등 스냅샷이 아예 없는 상태에서 장중에 첫 요청이 오면
               부득이 그 순간 값을 1회성으로 보여줌(그 다음 마감 때 정상 스냅샷으로 교체).
v4.98 [신규] 진입 종목 급락 알림(-5% 이상, 60초 폴링 기준).
        [배경] "짧은 시간 안에 -5% 이상 급락하면 알림"이 필요하다는 요청.
               오늘 등락률(며칠에 걸친 하락 포함)과 달리 폴링 주기 사이의
               변화만 봐야 "급락"의 의미가 있음.
        [구현] static/index.html: updateTracking()이 진입(entered) 종목의
               직전 체크가(last_price)와 새 가격을 비교해 -5% 이상 빠지면
               브라우저 Notification + 인앱 배너로 알림(종목당 10분 쿨다운).
               60초 자동 폴링(startFlashDropPolling)을 새로 추가해 탭이
               열려있는 동안 계속 감시 — 기존엔 수동 새로고침/앱 로드 시에만
               가격을 갱신해서 급변을 놓쳤음.
               일지 탭에 "🔔 급락 알림 켜기/ON" 표시 추가(알림 권한 상태 확인용).
        [주의] 텔레그램(얼마냐봇) 연동은 이번엔 범위 밖 — 웹앱이 열려있어야
               작동. 브라우저 알림 권한을 허용해야 실제로 뜸.
v4.97 [신규] 📋 마감정리 탭에 섹터 로테이션(상승률 상위/거래대금 상위 섹터) 추가.
        [배경] 한국 시장은 순환매(테마 로테이션)가 특히 심해서, 마감 후
               개별 종목보다 "오늘 어느 섹터가 주도했는지"를 먼저 봐야 함.
        [구현] /api/eod에 두 축 추가:
               1) sector_rise — 유니버스 전 종목 등락률을 섹터로 묶어 평균
                  (생존자 2종목↑), 내림차순 정렬. 각 섹터 상위 3종목 포함.
               2) sector_value — 네이버 거래대금 순위 상위 120종목(기존 40→120
                  확대, top_value 표시용 데이터도 겸용)이 어느 섹터에 몰렸는지
                  종목 수로 집계. 실제 거래대금 금액은 스크레이핑 소스에 없어
                  순위 등장 빈도를 프록시로 사용(/api/sectors 주도업종 집계와
                  같은 철학).
        static/index.html: 마감정리 탭에 "📈 상승률 상위 섹터" / "🔥 거래대금
        상위 섹터" 2열 카드 추가.
v4.96 [버그수정] 관심종목 수동 등록 시 한국 종목 현재가 조회 실패(마키나락스 477850 사례).
        [문제] saveManualAdd()가 "시장" 드롭다운(한국/미국)과 무관하게 종목코드
               입력값을 그대로("477850") 써서 /api/prices에 넘김. is_kr()은
               ticker가 .KS/.KQ로 끝나는지만 보므로 접미사 없는 코드는 무조건
               미국(yfinance) 경로로 새서 한국 종목(특히 신규상장주)은 가격을
               못 가져옴 — 저장되는 ticker 필드 자체에도 접미사가 안 붙어
               이후 추적(updateTracking 등)까지 계속 실패.
        [해결] 시장=한국(KR)이고 코드가 접미사 없는 5~6자리 숫자면 .KQ/.KS
               둘 다 /api/prices로 조회해 실제로 값이 온 접미사로 ticker
               변수 자체를 교정(코드가 있으면 관심 등록가 자동입력이든, 수동
               입력이든 저장되는 ticker에 항상 반영).
v4.95 [버그수정] 목표가(2R) 도달 시 자동 전량종료되던 문제 — 부분익절과 충돌.
        [문제] updateTracking()이 현재가가 target(대부분 2R 기본값)에 닿으면
               자동으로 '목표도달'로 전량 종료·result_r 확정해버렸음. 근데
               실제 매매 방식은 "2R/30%에서 절반만 익절하고 나머지는 추세
               따라 홀딩"이라, 부분익절(🔪)로 기록하려고 들어간 사이에
               포지션이 먼저 자동으로 통째 종료돼버리는 사고 발생(WTTR 사례).
               TIGO는 target이 2R이 아닌 다른 값(측정이동 등)이라 아직 안
               닿아서 우연히 안 잡혔던 것 — 종목마다 다르게 보이는 원인.
        [해결] 목표도달 자동 전량종료 제거 — 이제 target_reached 플래그만
               남기고 "🎯 목표가 도달" 알림만 표시, 실제 종료는 사용자가
               🔪부분/⏹종료로 직접 판단. 손절 자동종료는 그대로 유지(리스크
               관리 게이트라 다름).
        [재오픈] 종료(closed)된 행에 🔓재오픈 버튼 신설 — 실수로 자동/수동
               종료된 종목을 종료 기록만 초기화하고 진입중으로 되돌릴 수 있게
               (부분익절 이력은 보존). 예전엔 상태가 텍스트 표시만 돼서 되돌릴
               방법이 아예 없어, 이걸 고치려고 편집(연필)으로 들어가도 노트란에
               사정을 글로 적을 수밖에 없어 불편했던 문제도 같이 해소.
v4.94 [신규] 📋 마감정리 탭에 코스피/코스닥 상승·하락·보합 종목 수 표시.
        /api/eod가 KR 데이터 순회하는 김에 같이 집계(추가 호출 없음). 캐시된
        일봉 등락 부호로 코스피/코스닥 각각 상승/하락/보합 종목 수를 세어
        상한가/거래대금 상위 박스 위에 별도 요약 박스로 표시.
v4.93 [버그수정] 진짜 최종 근본원인 — 디스크 캐시(rs4)가 v4.87~89 버그를
        영구히 물고 있었음. v4.92까지도 안 고쳐진 이유.
        [원인] 하필 이 버그를 고치던 시점에 한국장이 마감(15:40 KST)됐음.
        _fetch_market_data_inner는 장마감 후엔 디스크 캐시(/data, 영구 볼륨)를
        읽고 "다음 거래일까지 재호출 0"으로 그대로 반환함. 마감 직후 저장된
        오늘자 rs4 캐시 파일이 v4.87~v4.89 버그(등락률 0%로 오염된 오늘 봉)를
        그대로 담고 있었는데, 이후 v4.90~v4.92를 아무리 배포해도 디스크 캐시
        히트 경로는 naver_kr.fetch()를 다시 호출하지 않으니 그 오염된 파일을
        계속 그대로 서빙 — 서버 재배포로도 안 지워짐(영구 볼륨이라 재시작해도
        파일이 살아남음). "강력 새로고침해도 로딩이 금방 된다"는 사용자
        관찰이 정확히 이 증거였음(진짜 재수집이면 몇 분 걸려야 정상).
        [해결] 디스크 캐시 네임스페이스 rs4→rs5로 캐시버스트. 이 코드베이스가
        전에도 같은 이유(rs2→rs3, rs3→rs4)로 두 번 써온 패턴 — 오염된 캐시
        파일을 이름 자체를 바꿔 강제로 무시하고 새로 빌드하게 함.
v4.92 [버그수정] 브라우저가 /api/* 응답을 캐싱해서, v4.87~v4.91 서버 수정을
        아무리 배포해도 화면이 안 바뀌는 것처럼 보이던 문제(사용자가 "버전은
        올랐는데 삼기도 그대로, 등락률도 0% 그대로"라고 반복 제보).
        [원인] FastAPI 기본값은 Cache-Control 헤더를 아예 안 붙임 → 브라우저가
        자체 휴리스틱으로 /api/scan 같은 GET 응답을 캐싱해버릴 수 있음. 그러면
        서버 로직이 아무리 바뀌어도 브라우저가 네트워크를 다시 안 타서 예전
        응답을 그대로 재사용 — 서버 쪽만 계속 고치고 있었으니 못 잡을 만했음.
        [해결] 모든 /api/* 응답에 Cache-Control: no-store 미들웨어로 강제.
        서비스워커(sw.js)도 /api/ fetch에 cache:'no-store' 명시(이중 방어),
        프론트 메인 스캔 fetch에도 동일 옵션 추가.
v4.91 [신규] 국장 시총 1000억원 미만 종목 스캔 제외 (예: 시총 718억짜리
        삼기가 돌파임박에 뜨던 문제).
        naver_kr.fetch_high_marketcap_allowed(): sise_market_sum 페이지가
        시총 내림차순 정렬임을 이용해, 문턱(1000억) 아래로 내려가는 순간
        그 시장 스크레이핑을 중단 — 코스피 ~25페이지, 코스닥 ~14페이지선에서
        끝남(전체 종목을 다 긁을 필요 없음, 실측 확인). 결과는 '허용목록'
        (시총 충족 티커 집합)으로 캐시하고, 하루 1회 스케줄러가 백그라운드로
        갱신. _fetch_market_data_inner에서 유니버스 구성 시 국장 종목 중
        허용목록에 없는 것만 제외 — 허용목록이 아직 준비 안 됐으면(서버
        재시작 직후 등) 필터 없이 통과(fail-open).
v4.90 [버그수정] 한국 종목 등락률 +0% — v4.89로도 안 고쳐졌던 진짜 근본원인.
        [재조사] v4.89는 fetch_live_price()의 값이 '오늘 실시간'이라고 믿고
               언제 오버레이할지만 게이팅했는데, 실측(curl)해보니 전제 자체가
               틀렸음: fetch_live_price()(m.stock.naver.com integration API)가
               최종적으로 참조하는 dealTrendInfos[0]과 totalInfos.lastClosePrice는
               이름 그대로 '전일 종가' — 어떤 필드를 폴백해도 하루 지연된 값만
               나옴. 반면 fetch_history()가 쓰는 siseJson 엔드포인트는 오늘
               날짜 행을 이미 실시간에 가깝게 채워서 줌 — 삼성전자우(005935)로
               직접 대조: siseJson 오늘 행의 시가/고가/저가/거래량이
               integration API의 당일 실시간 값과 정확히 일치, 반면 종가만
               '어제' 값을 물고 있었음.
        [해결] naver_kr.fetch()에서 fetch_live_price 기반 오버레이 로직 자체를
               제거 — siseJson이 이미 정답을 주므로 fetch_history() 그대로
               반환. app.py의 _one_price(/api/prices, 일지 추적가)도 동일하게
               fetch_live_price 대신 fetch_history 직접 사용으로 변경.
               fetch_live_price()는 이제 아무 데서도 실가격 판단에 안 쓰임
               (디버그 엔드포인트의 참고용 출력에만 남음).
v4.89 [버그수정] 한국 종목 오늘 등락률이 +0%로 뜨던 문제 — v4.87의 부작용.
        [원인] v4.87에서 fetch_live_price()가 dealTrendInfos[0](최신 '거래일'
               종가 — 장중 실시간이 아니라 그날 종가에 가까운 값, 개장 전엔
               어제 값 그대로)를 폴백으로 반환하게 고쳤음. 그런데
               naver_kr.fetch()는 "live가 있으면 무조건 오늘 날짜로 새 봉을
               만든다" 구조였어서, 개장 전(예: 09시 전)에 어제 종가가 그대로
               live로 돌아와도 그걸 '오늘 새 봉'으로 추가해버림 → 오늘 봉
               Close와 어제 봉 Close가 완전히 같아져 등락률이 항상 0%로 계산.
        [해결] 오늘 봉이 아직 없을 때는 지금이 실제로 한국장 장중일 때만
               새 봉을 만들도록 게이트(_kr_market_open_now, KST 09:00~15:30
               평일). 장외면 live는 마지막 종가 반복일 뿐이니 무시하고 과거
               일봉 그대로 사용 — 등락률이 "마지막 완결 거래일 vs 그 전날"로
               정상 계산됨.
v4.88 [수정] 메인 화면 상단 범례(⚠️경보/리스크, ⭕점수, 🤝M&A의심) 줄 삭제.
        v4.70에서 👑주도주·🔥트리거는 이미 뺐는데 나머지 세 개가 남아있었음 —
        요청에 따라 마저 제거. 배지 자체(카드에 뜨는 아이콘)는 그대로 유지,
        설명 줄만 삭제.
v4.87 [버그수정] naver_kr.fetch_live_price()가 실제 API 응답 구조와 안 맞아
        사실상 항상 실패하던 문제 — 파급 범위가 예상보다 훨씬 컸음.
        [문제] totalInfos에서 code가 'closePrice'/'nowVal'인 항목을 찾는
               로직이었는데, 실측해보니(마키나락스 477850, 사용자가 관찰
               등록한 종목의 가격이 안 바뀐다고 제보) 실제 응답의 totalInfos엔
               그런 code가 없음(lastClosePrice=전일종가, openPrice/high/low
               뿐). 그래서 이 함수가 사실상 항상 None → 호출부가 매번 일봉
               마지막 종가로 조용히 폴백하고 있었음.
        [파급 범위] 이 함수는 (1) 일지 추적가(/api/prices) 뿐 아니라
               (2) naver_kr.fetch() — 스캐너가 한국 종목 일봉에 장중 현재가를
               덮어쓰는 바로 그 함수에도 쓰임. 즉 장중 내내 한국 종목 전체가
               실시간가 보정 없이 직전 일봉 그대로 스캔되고 있었을 가능성이
               높음 — v4.73/v4.86의 캐시 신선도 수정과는 별개의, 더 근본적인
               "애초에 라이브 가격을 못 받아오던" 문제.
        [해결] dealTrendInfos[0](최신 거래일 항목)의 closePrice를 최종 폴백으로
               추가 — 실제로 마키나락스 22,600원을 정확히 돌려주는 것 확인.
v4.86 [버그수정] "첫 스캔 대부분 실패, 로딩돼도 10분" 근본원인 수정.
        [문제] _fetch_market_data의 모든 호출(스캔/섹터/마감정리/스케줄러 전부)이
               시장별 공유 락(_market_fetch_locks)을 무조건 기다리는 구조였음.
               v4.73에서 종목별 실제 fetch 시각을 추적하도록 고친 뒤로 실제
               재수집이 예전보다 훨씬 자주(종목당 30분마다) 일어나게 됐는데, 그
               재수집 한 번이 네이버 레이트리밋 등으로 오래 걸리면(수분~10분+)
               그동안 락을 쥐고 있어 캐시가 이미 신선한 다른 요청까지 전부 그
               뒤에서 대기하게 됨 — 스케줄러 주석에도 "장중엔 콜드 스캔이
               사용자 요청 때 실행되면 브라우저 타임아웃으로 스캔 실패가 뜸"
               이라고 정확히 예견돼 있었으나, v4.73 이후 이 경로가 훨씬 자주
               걸리게 되면서 드물던 증상이 "대부분"으로 악화된 것.
        [해결] _fetch_market_data에 wait_for_fresh 인자 추가. 기본값 False(사용자
               요청 전부)는 캐시가 있으면 신선도와 무관하게 즉시 반환하고, 오래
               됐으면 백그라운드 태스크로 갱신만 걸어둔 뒤 요청은 기다리지 않는다
               (stale-while-revalidate). 캐시가 아예 없을 때(콜드 스타트)만
               실제로 기다림 — 서버 재시작 직후 딱 한 번만 발생. 스케줄러의
               워밍 호출만 wait_for_fresh=True로 실제 완료를 기다린다(워밍의
               목적 자체가 미리 실제로 받아두기라서).
v4.85 [신규] 일지 — 부분익절 기록 + 보유일 자동계산 + 근거/복기란 확대.
        [배경] 기본 매매가 "2R/30%에서 절반 익절 후 나머지는 추세 따라 홀딩"인데
               일지는 진입/전량청산 2단계뿐이라 부분익절을 남길 데이터 필드
               자체가 없었음. 보유일도 매번 손으로 세서 입력해야 했고, 근거·
               복기 textarea가 70px로 너무 작아 자세히 쓰기 불편했음.
        [부분익절] 진입중 행에 🔪부분 버튼 신설 — 청산비율(%)·청산가만 입력하면
               R은 진입가/손절로 자동계산, r.partials 배열에 순서대로 저장.
               포지션은 계속 entered로 남아 잔량만 계속 추적. 최종 전량 청산 시
               지금까지의 부분청산 + 마지막 청산분을 비중가중 블렌드해 결과R
               계산 (예: 1차 50%@+2.1R, 잔여 50%@+3.4R → 2.75R). 부분익절
               이력은 목록에 상시 표시, CSV에도 컬럼 추가.
        [보유일] 수정 폼 열 때 등록일 기준 경과일을 자동 계산해 기본값으로 채움
               (필요하면 여전히 수정 가능).
        [근거/복기] textarea 높이 70px→130px, 폰트 12px→13px로 확대.
        [목록 가독성] 상태배지(꽉 찬 배경)와 빠른 액션 버튼(⏹종료 등)이 둘 다
               진한 배경이라 '지금 상태'와 '누르는 버튼'이 헷갈렸음 — 액션
               버튼을 점선 테두리+투명배경으로 시각적으로 분리.
v4.84 [수정] 📋 마감정리 탭 레이아웃 — 상한가/거래대금 상위를 세로 스택 대신
        2열 나란히 배치, 표 글자 크기를 앱 전반 규격(11~12px대)에 맞춰 축소
        (기본 브라우저 표 폰트라 유독 크게 보이던 문제).
v4.83 [신규] 📋 마감정리 탭 — 코스피/코스닥 상한가 종목 + 거래대금 상위.
        신규 /api/eod: 상한가는 캐시된 KR 일봉 전체를 훑어 당일 등락률이
        ±30% 가격제한폭에 근접(29.5%+)한 종목을 찾음(추가 네트워크 호출 없음).
        거래대금 상위는 네이버 거래대금 상위 페이지의 등장 순서(=실제 순위)를
        그대로 쓰고, 표시용 종가/등락률만 캐시된 일봉에서 조회. 10분 캐시.
v4.82 [수정] 검색한 종목이 현재 탭에 안 잡혔을 때, '왜 안 잡혔는지' 상세 진단이
        자동으로 뜨게 함 (그동안은 Enter/진단 버튼을 따로 눌러야 나왔음).
        기본 현재가 카드를 띄우는 triggerLookup()에 runDiag() 자동 호출을
        얹었고, 반대로 검색어가 현재 탭에서 찾아지면 이전 검색의 진단 결과가
        카드 밑에 그대로 남아있지 않게 정리하는 로직도 추가.
v4.81 [수정] '종목명 찾기'와 '왜 안 잡혔지?' 진단 검색란을 하나로 통일.
        [문제] 입력란이 두 개 따로 있어서(카드 필터용 / 진단용) 뭘 어디에
               넣어야 하는지 헷갈린다는 피드백.
        [해결] 검색란 하나로 합침 — 타이핑하면 기존처럼 카드 실시간 필터링,
               Enter나 진단 버튼을 누르면 같은 검색어로 상세 진단(/api/debug)도
               같이 뜸. runDiag()가 이제 이 단일 입력란값을 읽음.
v4.80 [수정] M&A의심 종목 스캔 결과에서 제외 + 감시 등록 직후 일지 미반영 버그.
        [M&A의심] 지금까지는 배지(🤝 M&A의심)로 표시만 하고 카드 자체는 그대로
               노출했음. pullback/breakout/boxbreak/imminent/breakdown/pattern
               6개 모드의 analyze_*()에서 _merger_block 판정 후 merger=True면
               스캔 결과에서 아예 제외하도록 변경 — 배지 대신 완전 배제.
        [감시→일지 미반영] ⚡ 감시 버튼 → POST /api/watch/quick은 서버 일지
               파일에만 append하고, 클라이언트의 journalCache는 안 건드렸음.
               그래서 등록 직후 '내 일지' 탭으로 가면 방금 등록한 종목이 안
               보이고, 새로고침해야만 나타났음(캐시가 등록 이전 시점 그대로라).
               quickWatch() 성공 시 loadJournalFromServer()로 캐시를 즉시
               재동기화하도록 수정.
v4.79 [신규] 모바일 대응 전면 재정비 + PWA(홈 화면 설치) 지원.
        [모바일 레이아웃] PC 레이아웃을 그대로 축소만 하던 걸 실제로 재배치:
               모드 탭이 줄바꿈으로 잘려서 다루기 힘들던 것 → 한 줄 가로 스크롤
               (스와이프)로 전환. 카드 metrics 4열이 폰 폭에서 숫자가 뭉개지던
               것 → 2열로. 일지 표는 가로 스크롤 중에도 종목명(3번째 열)이
               고정되게. 전반적으로 폰트·여백을 터치 기준으로 재조정.
        [PWA] manifest.json + 아이콘(192/512, iOS용 180) + 서비스워커(sw.js)
               추가 — 앱스토어 등록 없이 폰 홈 화면에 "추가"하면 브라우저 주소창
               없이 앱처럼 전체화면으로 실행됨. 서비스워커는 스코프가 사이트
               전체(/)가 되도록 반드시 루트(/sw.js)에서 서빙 — /static/sw.js로
               등록하면 스코프가 /static/으로 좁아져 설치 조건을 못 채움.
               오프라인 캐싱은 의도적으로 안 함(항상 최신 데이터 필요).
v4.78 [버그수정] 인버스 탭 "강도 점수(0~100)"가 항상 '–'로 뜨던 문제(근본원인).
        [문제] UI 안내문·정렬 코드(app.py의 inv_score 정렬 키)는 이미 있었는데,
               정작 scanner.analyze_inverse()가 inv_score 필드 자체를 계산해서
               반환한 적이 없었음 — 프론트가 항상 undefined를 받아 '–' 표시.
        [해결] scanner.inverse_score() 신설: 정배열/20일선/기울기 구조 신호(최대
               55점) + 5일 모멘텀(최대 25점, 20%↑에서 만점) + 거래량 확인(최대
               20점, 평균 3배↑에서 만점) 합산, 과열(RSI)이면 되돌림 위험으로
               -15점 감점. analyze_inverse()가 이 값을 inv_score로 반환.
               곱버스(2x/3x) 파생 카드는 레버리지 반영된 5일 등락으로 재계산.
v4.77 [수정] 일지 진입중/대기/관찰을 큰 탭으로 승격.
        [문제] 상태 필터(진입중/대기/관찰)가 이미 있었지만 전체/종료/추세/단타
               필터와 똑같은 작은 pill 버튼으로 섞여있어 눈에 안 띄고, 기본
               화면은 전부 한 표에 일렬로 섞여 나와 보기 불편했음.
        [해결] 진입중/대기/관찰 3개만 크고 굵은 탭 스타일(.jrnl-tab)로 분리,
               나머지(전체/종료/추세/단타)는 구분선 뒤에 기존 작은 버튼으로
               유지. 필터링 로직 자체는 기존 journalFilter 그대로 재사용.
v4.76 [신규] 일지 진입/추적 종목의 R 표시에 2R 도달가 같이 표시.
        [배경] "0.8R" 같은 배수만 봐선 실제로 얼마까지 가야 2R인지 감이 안
               잡힘. 진입가·손절가로 2R 가격(entry + 2×(entry-stop))을
               계산해 "D+n" 옆에 "2R 12,340" 식으로 상시 노출.
v4.75 [신규] 상단 지수 바(코스피/코스닥/S&P500/나스닥/닛케이/비트코인) 이름 클릭 시
        트레이딩뷰 차트로 이동. /api/indices가 한글 라벨만 주기 때문에 프론트에
        라벨→트레이딩뷰 심볼 매핑(TV_INDEX_SYMBOL)을 추가 (KRX:KOSPI,
        KRX:KOSDAQ, NASDAQ:IXIC, SP:SPX, TVC:NI225, COINBASE:BTCUSD).
v4.74 [버그수정] 일지 날짜 스탬프(등록일·활동달력)가 KST 자정~09시엔 하루 전으로
        찍히던 문제.
        [문제] 프론트가 '오늘' 날짜를 전부 new Date().toISOString().slice(0,10)로
               만들었는데, toISOString()은 항상 UTC 기준. 한국은 UTC+9라 KST
               00:00~09:00 사이엔 UTC로는 아직 전날이라, 이 시간대에 등록한
               종목은 하루 전 날짜로 저장됨 (마키나락스를 7/20 새벽에 관찰
               등록했는데 활동 달력엔 7/19로 표시된 사례).
        [해결] 브라우저 로컬시간이 아니라 항상 KST로 shift한 날짜 문자열을
               만드는 kstStr() 헬퍼 추가, 일지 등록일/최종확인일/종료일/주간·
               월간 R 집계 기준일/CSV 파일명 등 날짜 스탬프 13곳 전부 교체.
               경과일 계산(일수 차분)에 쓰던 Date 객체 자체는 그대로 둬서
               영향 없음 — 문자열 포맷팅 지점만 수정.
v4.73 [버그수정] 장중 스캐너 가격이 첫 fetch 이후로 사실상 고정되던 문제(근본원인).
        [문제] _fetch_market_data_inner의 증분재사용 로직이 "직전 캐시가
               REUSE_TTL(30분) 이내인가"를 번들 전체의 재구성 시각(ts)으로
               판단했음. 그런데 ts는 실제 fetch가 하나도 없는 순수 재사용
               사이클에서도 매번 now()로 갱신됨. 백그라운드 워밍(4~8분 주기)이
               이 사이클을 계속 돌리는데 그 주기가 REUSE_TTL(30분)보다 짧아서,
               재사용 조건이 사실상 영원히 참이 됨 → 장 시작 후 첫 fetch
               이후로는 종목별 실제 가격이 하루 종일 다시 안 받아짐(카드
               현재가·등락률·RS 전부 고정). v4.43.2가 고쳤다고 기록된
               "기가비스 6/26 종가 고정" 버그가 "며칠"에서 "장중 내내"로
               형태만 바뀌어 재발한 셈.
        [해결] 종목별 실제 fetch 시각을 별도 딕셔너리(data_ts)로 추적, 번들
               재구성 여부와 무관하게 종목 단위로 REUSE_TTL 경과 시 반드시
               재요청되게 함. 일지 추적가(/api/prices)는 원래도 매번 실시간
               조회라 이 버그의 영향을 받지 않았음 — 스캐너 카드 쪽만 해당.
v4.72 [기능개선] 일지 '수동 추가' → 관찰(watch) 상태에서 피벗가 필수 입력 제거.
        [문제] 스캐너 유니버스에 없는 신규 상장주(예: 마키나락스 477850, 2026-05
               코스닥 상장이라 유니버스 갱신 주기에 아직 안 들어감)를 "관찰"로만
               추적하고 싶어도, 관찰 상태에서도 피벗가가 필수값이라 억지로 숫자를
               지어내야 했음. 게다가 손절가 없이 저장하면 프론트의 활성추적
               필터(entry && stop)에 안 걸려 현재가 갱신도 전혀 안 됐음.
        [해결] 관찰 상태는 피벗가를 선택으로 바꾸고, 비워두면 /api/prices로 현재가를
               가져와 자동 채움. 활성추적 필터를 entry && (stop || status==='watch')로
               완화해 손절 없는 관찰 종목도 현재가가 갱신되게 함. 일지 목록의 결과
               칸도 관찰 상태면 R 계산 대신 현재가만 표시.
v4.71 [버그수정] ⚡ 감시 버튼이 카드 재렌더링 시 다시 활성화되던 문제.
        [문제] 감시 버튼 클릭 → /api/watch/quick으로 서버 일지에 pending 등록.
               하지만 버튼 자체의 disabled 상태는 그 DOM 엘리먼트에만 남아,
               카드 목록이 다시 렌더링(탭 전환·다시 스캔 등)되면 새 버튼이
               항상 초기 상태(활성, "⚡ 감시")로 그려짐 → 중복 클릭 유발.
        [해결] 프론트에 isPendingWatch(ticker) 헬퍼 추가 — 카드 렌더 시
               journalCache에 해당 티커의 pending 항목이 있으면 버튼을
               처음부터 disabled + "✓ 감시중"으로 그림.
v4.70 [수정] 카드/상단 배지 시각적 소음 축소.
        [배지 축소] 등급(A/B/C/D급), 베이스 품질(베이스 짧음 등), 후기 스테이지,
               손절폭 넓음 등 climax-tag/gbadge 계열 배지의 볼드체를 없애고
               폰트를 더 작게(9px, normal weight) 축소 — 정보는 유지하되 카드
               제목보다 튀지 않게.
        [상단 배너 축소] 코스피/코스닥/나스닥 신규진입자제 게이트 배너도 동일하게
               볼드 제거 + 축소(13px→11px).
        [범례 삭제] 👑 주도주, 🔥 트리거(오늘 출발) 범례 및 카드 제목 앞 아이콘
               삭제 — 정보 과잉으로 판단. 일지 자동기록용 신호 스냅샷(journal
               signal 필드)은 유지, 화면 표시만 제거.
v4.69 [수정] 검색 UI 정리 — '종목명/코드 찾기'와 '왜 안 잡혔지?' 진단 검색란을
        한 줄에 반반으로 배치, 화면 상단(종목명 찾기 자리)으로 이동. 이전엔
        진단 검색란이 콘텐츠 하단에 따로 떨어져 있어 화면이 산만했음. 진단
        결과(diagResult)는 기존 위치(콘텐츠 아래)에 그대로 표시.
v4.68 [신규] 일지 자동 손절을 종가 기준으로 확정 + 카드에 ATR% 상시 표시.
        [문제] 자동 손절 판정이 앱을 연 순간의 장중 실시간가로 즉시 이뤄짐.
               장중 손절가를 스쳤다가 종가에 반등하면, 그 순간에 앱을 안 보고
               있었을 경우 다음에 열었을 땐 이미 가격이 회복돼 있어 px<=stop이
               거짓이 됨 → 영원히 '진입중'으로 남고 일지에 손절 기록이 안 됨.
        [해결] /api/prices가 티커별 장마감 여부(closed)도 함께 반환. 프론트는
               장중엔 손절가 터치를 배지로만 표시(⚠️ 장중 손절터치), 실제 종료
               판정(exit_reason=손절도달)은 장마감 후 가격으로만 확정. 대기
               종목의 셋업붕괴 판정도 동일하게 종가 기준으로 통일.
        [ATR 표시] badge_fields가 내부적으로만 쓰던 atr_pct를 _rr_block에도
               추가해 눌림목/추세전환/돌파/박스돌파/돌파임박/패턴 전 탭에 노출,
               카드에 '리스크' 옆 'ATR' 지표로 상시 표시. 손절폭 넓음 배지의
               판정 근거(ATR×1.5)를 숫자로 직접 확인 가능.
v4.67 [수정] 손절폭 판정을 고정%에서 ATR 배수로 — 미국주 배제 문제 해결.
        [문제] 고정 5%(US)/7%(KR)가 종목 변동성을 무시. 미국주는 ATR이 커서
               (ANAB 7.7%) 정상 손절폭이 5%를 넘어 💎적격에서 통째 탈락,
               국내만 남던 반복 문제의 뿌리.
        [해결] 손절폭 > ATR×1.5면 넓음. 변동성 대비 판정. ANAB 6.8%/ATR7.7%
               =0.88배 통과. 저변동주는 오히려 더 빡세짐(ATR2%→한계3%).
               stop_wide·bear_ok 공통. ATR 불량시 고정% 폴백.
v4.66 [수정] 약세장적격(bear_ok) 손절폭 기준을 손절폭배지(stop_wide)와 통일.
        [버그] bear_ok는 risk_warn(8%/12%), stop_wide는 5%/7%를 써서 손절폭
               7.2%짜리가 🚫손절폭넓음 + 💎약세장적격을 동시에 다는 모순
               (ANAB D급이 적격으로 둔갑). [해결] bear_ok도 5%/7% 기준으로.
               이제 🚫 뜬 종목은 💎이 절대 안 뜸.
v4.65 [개선] 용어 통일 + 💎 옥석 필터 + 리테스트 독립화 (봇 v2.8.1).
        ① [⚡관찰]→[⚡감시] 개명 — 일지 '관찰' 카테고리(이평접근 알림)와
           이름 충돌 해소. 감시=피벗돌파, 관찰=이평접근으로 역할 분리.
        ② [💎 적격만] 필터 버튼 — 약세장(🟡🔴) 돌파임박 125개에서
           bear_ok(탄탄베이스+RS90+손절폭적정) 충족만 표시. 옥석 가리기.
        ③ 봇 리테스트를 pending 목록과 독립화 — 대기→진입 자동전환으로
           pending에서 빠진 종목의 리테스트를 놓치던 결함 수정.
v4.64 [신규] 발견→관찰→진입 파이프라인 (봇 v2.8과 세트).
        ① 원클릭 관찰: 카드 [⚡관찰] 버튼 → POST /api/watch/quick →
           피벗·손절 자동으로 일지 대기 등록 → 봇 즉시 감시. (등록 마찰 제거 —
           "스캐너 발견했는데 등록 안 해서 알림 못 받고 놓침" 패턴 차단)
        ② 수평저항 위쪽 제외 +2%→+0.5% (나이스정통: 눈앞 저항 30,300이
           제외구간에 들어가 안 잡히던 버그)
v4.63 [신규] 수평 저항/지지 매물대 감지 — 진짜 피벗 자동 판정.
        [문제] 스캐너 박스 상단이 '최근 N일 최고가'라 스파이크(윗꼬리 한번)를
               피벗으로 잡음. 여러번 눌린 수평 매물대가 진짜 저항인데 못 봄.
               (빅솔론: 스캐너는 8970을 피벗으로 봤지만 실제 저항은 8300대)
        [해결] horizontal_levels() — 가격을 1% 빈으로 나눠 고가·저가 터치횟수
               집계. 3회+ 닿은 구간만 매물대(스파이크는 1~2회라 자동 제외).
               인접 빈 병합, 현재가 ±2% 제외. /api/debug와 진단패널에 표시.
v4.62 [신규] '왜 안 잡혔나' 진단 패널 — 카드 상단에 종목코드 입력→진단.
        /api/debug에 탈락_핵심사유 자동 판정 추가(정배열깨짐/박스미돌파/거래량부족
        등을 지표로 자동 해석). UI에서 모드별 통과/탈락 칩 + 사유 + 핵심지표 표시.
        기존엔 /api/debug/{ticker} URL 직접 쳐야 했음.
v4.61 [수정] 손절폭 배지 2건 + 베이스 배지 전탭 확대.
        [버그1] stop_wide를 analyze 로컬 risk_pct(피벗기준)로 판정 → 카드
                하단 '리스크%'(rrb값)와 달라 11.32%인데 ✅로 뜸. rrb.risk_pct로 통일.
        [버그2] 베이스 배지가 analyze(눌림목)에만 있어 돌파임박 탭엔 안 뜸.
                badge_fields() 공통 헬퍼로 analyze/imminent/breakout 전부 적용.
        [개선] 손절폭 배지는 넓은 것만 🚫 표시(좁은 ✅ 제거 — 숫자중복·노이즈).
v4.60 [신규] 상시 손절폭 배지 (US 5% / KR 7%) — 전 탭 항상 표시.
        [배경] 반복 실패 근본원인 = 돌파임박 추격 진입으로 손절폭 6~10%.
               +1R(=손절폭) 목표가 멀어 도달 전 손절. 한 번도 +1R 못 감.
        [해결] stop_wide = risk_pct > (KR 7% / US 5%). 🚫손절폭N% (넓음) /
               ✅손절폭N% (적정) 배지. 게이트 무관 항상. 돌파 탭에서 특히
               "지지 근처(눌림목)에서 사라" 각인.
v4.59 [신규] 약세장 진입 적격 배지 — pressure/correction 국면 옥석 가리기.
        [배경] pressure에서 손절폭 넓은 종목 2개 동시 진입해 둘 다 손절.
        [해결] bear_ok = 탄탄한베이스 + RS90+ + 손절폭적정, 3조건 AND.
               게이트 🟡🔴일 때만 카드에 💎약세장적격/△부적격 배지 표시.
v4.58 [신규] 베이스 품질 배지 + 데이터 불연속 감지.
        [문제] MEC(며칠짜리 얕은 눌림)·NVRI(스핀오프로 두 회사 데이터가 한
               티커에 섞임)가 돌파임박에 떠서 진입 유도. 베이스 품질을 안 봄.
        [해결] scanner.base_quality() 신설 — 길이(주)·깊이·VCP수축·거래량건조 +
               ±25%+ 일간 갭(스핀오프/합병/재상장/분할) 감지. 점수 가감 +
               카드 배지(🧱탄탄/📏짧음·깊이/⚠️불연속). 오닐 5주 기준.
v4.57 [수정] 시장 게이트 근본 버그 3개 + 4개 지수 확장.
        [문제] gate_suggest의 dist_days>=6 early return이 FTD 분기를 죽은
               코드로 만듦. 분산일이 리셋·만료 없이 쌓이기만 함. FTD 후에도
               in_correction이 안 풀려 나스닥이 한 달간 correction에 갇힘.
        [해결] dist_count() 분리(FTD리셋+5%만료), gate_suggest가 FTD를 먼저
               평가, ftd_state에 조정종료 조건 추가. 게이트를 KOSPI/KOSDAQ/
               S&P500/나스닥 4개로 확장. 거래량 검증+ETF폴백. 봇 v2.7 알림
               헤더에 게이트 삽입.
v4.55.1 [개선] A-B-C 수렴중 매수 타점 안내 — 피벗 추격 방지.
        [문제] 수렴중 카드가 피벗(B상단)을 매수 기준처럼 보여줘, 거기서 사면
               이미 +15% 위라 손절폭 22%로 과대(코텍 실전 사례).
        [해결] 수렴중일 때 매수타점(B하단~+30%)·손절(B하단-3%)·목표(B상단)를
               별도 계산. B하단 지지 반등 진입 시 손익비 13R(손절폭 3%)로 개선.
        카드에 '🎯 매수타점 N~N' 뱃지 추가. 툴팁에 진입 방법 안내.

v4.55.0 [개선] A-B-C 상한가 패턴 v3 — 동적 구간 탐지로 재설계.
        [문제] v1 고정 구간 분할이 종목마다 다른 A-B-C 길이를 못 맞춰 실패.
               오늘 재폭발하면 close가 최고가라 상승1파 고점을 잘못 잡음.
        [해결] 최근 급등구간(C)을 먼저 분리 → 그 이전에서 상승1파 고점 탐지
               → peak 전후로 A/B 동적 분할. A는 peak-10봉 앞에서 끝나 순수 수렴만.
        [스테이지] 첫폭발(A→첫상한가) / 수렴중(B다지기,대기) /
               폭발초입(B후재폭발 1~2번째,최고품질) / 폭발진행(3번째+,추격주의).
               B가 peak대비 -30%↑ 깊으면 컵앤핸들로 제외(마녀공장 케이스).
        [검증] 광주·금호·삼화·디벨로먼트·마녀공장 실데이터 근사 5종 통과.
               카드에 스테이지 뱃지(💥첫폭발/⏳수렴중/🔥폭발초입/⚠️폭발진행).

v4.54.0 [신규] A-B-C 상한가 패턴 감지 (한국 시장 특유) — 패턴 탭에 추가.
        구조: A(1차 장기수렴, 변동폭≤25%) → 상승1파(A상단+10%↑, 장기선 위)
              → B(2차 수렴, A보다 높고 변동폭≤20%) → C(B상단 돌파+거래량 300%↑).
        스테이지: 폭발(C 돌파 당일, 거래량 3배+) / 수렴완성(B상단 근처 대기).
        엄격 모드(정석 A-B-C만). 금호타이어·삼화콘덴서 실패턴으로 검증.
        🎆 뱃지. 수렴중 대기 + 돌파순간 둘 다 포착.
v4.53.6 [신규] 하향 목표가 도달 알림 (봇 v2.6) — 눌림목 대기 완성.
        진입 목표가가 현재가보다 낮게 설정된 대기 종목(RCUS $30인데 목표
        $26)이 목표까지 눌려 내려오면 🎯 알림. 피벗 돌파(상향)와 반대 방향.
        상향/하향은 목표가 위치로 자동 구분. pending API에 target_below 추가.
        [수정] 일지 수정 폼에 '대기' 옵션 추가(기존 3개→4개) + 카테고리
               변경 시 status 자동 동기화(대기→pending, 관찰→watch).
v4.53.5 [수정] 관찰 종목 R 오알림 — 관찰인데 진입가 넣으면 봇이 진입으로
               착각해 +2R 알림 발송(RCUS 사례: 관찰+진입가26 → 현재가30에서
               +2.13R 오알림).
        [원인] /api/watch/positions가 (status or 'entered') 폴백이라 status
               없는/관찰 종목을 진입으로 오인.
        [수정] category=관찰이면 status·진입가 무관하게 R 감시 제외. status
               있으면 entered만. status 없는 구 레코드는 하위호환 유지.
v4.53.4 [개선] 일지 추가 모달에 '대기' 옵션 신설 — 눌림목 등 비돌파 종목도
               피벗 감시받게. 기존엔 돌파 계열만 자동 pending이라, 눌림목
               종목은 관찰(watch)로만 저장돼 봇 알림을 못 받았음.
               대기=pending(봇 피벗 감시), 관찰=watch(알림 없음, 통계 제외)로 분리.
v4.53.3 [수정] 급등일 눌림목 오분류 — 우뚝 솟은 양봉이 눌림목에 뜨던 버그.
        [원인] breakout_day 예외가 '얕게 눌렸다 출발하는' 종목을 눌림목에
               남기려 넣은 건데, VLO처럼 이미 오른 상태에서 또 급등하면
               '얕은 눌림'이 아니라 '연장(추격)'인데도 눌림목에 남음.
        [수정] breakout_day 시 AVWAP이 extended(+8%)/overheated면 눌림목 제외.
               건강한 눌림(0~8%)은 유지. VLO(+10.7%) 제외 확인, 정상 눌림 통과.
v4.53.2 [수정] tvUrl 함수 중복 정의 통합 — 일지 종목 클릭 시 트레이딩뷰 연결
               안 되던 버그. 카드(객체)·일지(ticker+market) 양쪽 시그니처 모두
               처리하도록 단일 함수로 통합.
v4.53.1 [개선] 시장 게이트 배너를 코스피/코스닥/나스닥 3개 개별 박스로 분리.
               각 지수 분산일·레짐·FTD로 개별 판정 (🔴/🟡/🟢). 한국 종목은
               코스피/코스닥, 미국 종목은 나스닥 게이트로 진입 판단.
v4.53.0 [신규] /api/ma/{ticker} — 종목 10/20/50일선 값 + 이탈 여부. 봇 이평 알림용.
        봇 v2.5: ① 보유 종목 이평 이탈(종가 기준, 15:20) → 트레일링 손절 신호
                 ② 관찰 종목 이평 접근(장중 2분, ±1%) → 눌림목 진입 준비 알림.
                 이탈은 종가라 정확, 접근은 참고용(반등 확인은 사용자).
v4.52.5 [근본수정] 스캔 반반 실패 해결 — 장중 프리로드 부재가 원인.
        [원인] 스케줄러의 _warm_market이 daykey(장 마감 후에만 생성) 없으면
               return → 장중엔 프리로드를 안 함. 캐시 10분 TTL 만료 후 열면
               콜드 스캔(3~4분) 실행 → 브라우저 타임아웃 → '스캔 실패'.
               로그상 컨테이너는 안 죽음(OOM 아님) 확인 후 이 원인 특정.
        [수정] ① 장중에도 8분마다 프리로드 (스케줄러 4분 주기) → 사용자는
               항상 캐시 히트. ② 프론트: 스캔 실패 시 15초 후 1회 자동 재시도
               (워밍 대기). 프리로드(근본)+재시도(안전망) 이중 방어.
v4.52.4 [개선] 시장 배너 분산일을 코스피/나스닥 각각 표시 (기존 최댓값 1개).
               게이트 판정은 국내(코스피) 기준 유지, 나스닥은 참고용.
v4.52.3 [개선] 수동 추가 시 상태 선택 (대기/진입/관찰). 기존엔 대기 고정이라
               이미 매수한 종목을 기록 못 함. 진입 선택 시 손절 필수(R·손절
               알림), 관찰은 통계 제외. 진입가/피벗가 라벨 자동 전환.
v4.52.2 [신규] 종목 티커 클릭 → 트레이딩뷰 차트 새 탭 (한국 KRX:코드, 미국 티커).
               카드의 티커 옆 📈, 모든 탭 카드에 적용.
v4.52.1 [신규] 돌파임박 피벗 리테스트 인식 — 먼 스파이크 고점 오인 수정.
        [원인] select_pivot이 significant_resistance(가장 높은 저항)를 써서
               더블유게임즈 6월 스파이크(76,500)를 피벗으로 잡음 → 실제
               리테스트 중인 가까운 저항(67,500)을 놓쳐 돌파임박 탈락.
        [수정] significant_resistance_near() 신설 — 현재가에서 가장 가까운
               유효 저항 + 지지→저항 역전(polarity flip) 인식. 돌파임박
               (analyze_imminent)만 use_near=True로 적용. 다른 탭은 기존 유지.
               더블유게임즈 시나리오로 76,500→67,500 검증 완료.
v4.52.0 [신규] 📅 활동 달력 (내일지) — 날짜별 분석·등록 종목명 표시,
               월 이동, 셀 마우스오버로 전체 목록. 지난 숙제 한눈에.
        [신규] 저유동성 하드 필터 — 평균 거래대금 KR 3억/일, US $2M/일 미만
               스캔 결과 제외 (급등 탭 예외). 시총 대신 거래대금 기준:
               "호가 얇아 매매 불가"의 직접 지표. 수렴만 하는 초소형주 제거.
v4.51.1 [수정] 분산 경고 오탐 제거 — 네이처셀 +8.9% 양봉에 매도신호 오발생.
        [원인] 이평이탈 판정이 단순 close<ma50이라, 이미 오래전 하락해 바닥에서
               반등 중인 종목(50일선 아래)도 매일 danger로 오탐. 오른 날에도 알림.
        [수정] '어제 이평 위 → 오늘 이평 아래'로 새로 깨는 하락일만 신호 인정.
               반등 양봉은 제외. 진짜 하향돌파는 정상 감지 확인.
v4.51.0 [신규] 보유 종목 분산 경고 — 진입 후 매도 신호 감지.
        scanner.distribution_check(): 고점대량반전·최대급락일·U/D악화·
        이평이탈(21/50일선)·소진성거래량 조합으로 level(none/caution/danger)
        판정. /api/dist/{ticker}. 봇이 진입 종목마다 하루 1회 체크해 danger 시
        ⚠️ 알림. BHE 패턴(신고가 대량반전) 정확히 danger로 검증.
v4.50.4 [근본수정] MQ +316% 유령 등락 원인 규명 및 제거.
        [원인] 야후가 종목의 최근 거래일들을 통째로 결측 처리하면, 유효한
               옛날 가격(MQ의 2022년 4.18달러)이 iloc[-2](전일종가) 자리로
               밀려와 17.41/4.18 = +316% 발생. 결측이 부분적이면 dropna(how=all)로
               안 걸러짐. 실제 재현으로 정확히 316.5% 확인.
        [수정] ① _downcast에 Close NaN 행 제거 (전 데이터 경로 공통)
               ② 섹터 집계에서 전일종가 날짜갭 >7일이면 제외 (옛날값 밀림 차단)
               ③ 기존 물리범위 필터(±31%/±100%)는 최후 안전망으로 유지
v4.50.3 [수정] 섹터 유령 등락 차단 — 야후 분할/조정 불일치로 전일종가가
               엉뚱하게 들어와 +316% 같은 유령 등락 발생(MQ 사례).
               개별 종목 하루 등락이 물리 범위(KR ±31% / US +100%~-60%)를
               벗어나면 섹터·주도업종 집계에서 제외.
v4.50.2 [개선] 섹터 탭 가독성 — 미국 종목은 티커 표시(이름 잘림 해결),
               상위 종목 2→5개, 줄바꿈 허용. 주도업종 랭킹 툴팁도 미국 티커.
v4.50.1 [신규] /api/vol/{ticker} — 종목 50/20일 평균 거래량 참조.
        봇이 돌파 시 네이버 실시간 누적 거래량 ÷ 시간경과율 ÷ 평균 →
        예상 거래량비 계산. 돌파 알림에 🟢확증/🟡애매/🔴부족 표시.
v4.50.0 [신규] FTD 자동화 — 시장 국면 판단을 시스템 안으로 (기능 동결 전 마지막).
        [상태 머신] scanner.ftd_state(): 조정 판정(저점 이전 고점比 -6%+),
               반등 시도 일수 카운트(저점 이탈 시 자동 리셋), FTD(4일차+ &
               +1.25% & 거래량 증가). 구식 "당일 깃발" 방식 폐기. 시나리오 6종 검증.
        [게이트 제안] gate_suggest(): 분산일+FTD 상태 → 🟢/🟡/🔴 자동 제안.
               /api/market/gate (KOSPI 기준). 리스크바에 제안 표시 + 불일치 시
               [적용] 원클릭. R설정 모달에 근거 문구.
        [봇 연동 v2.2] 30분마다 제안 변경 감시 → 📢 알림 (FTD 발생 시 "시험
               매수 0.5R" 지침 포함). 일요일 09:00 주간 리포트(주간R·승패·
               충동·진입중) — 주말 루틴 자동화.
        ※ 이후 기능 동결. 다음 과제는 코드가 아니라 표본 20건.
v4.49.3 [신규] /api/watch/positions — 진입중 포지션 노출. 얼마냐봇이 2분마다
               감시해 R 마일스톤 알림: 💰+2R(절반 익절+본전 이동)·+3R/+4R…·
               🛑손절 도달. 피벗 감시엔 ⚡-1% 접근 예고 추가 (봇 쪽 구현).
v4.49.2 [신규] 💧/🚱 유동성 뱃지 — "시총"이 아니라 "내가 나올 수 있느냐" 기준.
        [판정] 1R 포지션 금액(R설정 역산) ÷ 50일 평균 거래대금(avg_turnover 신설,
               당일 거래대금은 눌림 후보에서 과소평가되므로 평균 사용).
               5% 미만 정상 / 5~15% 💧 주의(사이즈 절반·분할 청산) /
               15%+ 🚱 부적합(관찰만). 계좌가 커지면 기준 자동 강화 (상대 기준).
        [가이드] 뱃지 사전에 반영.
v4.49.1 [신규] 📖 활용 가이드 내장 — GUIDE.md를 /guide에서 다크테마로 렌더
               (marked.js CDN, 백엔드 의존성 없음). 헤더 버전 뱃지 옆 📖 버튼.
               12개 챕터: 게이트/섹터/탭별 매매법/R시스템/루틴/절대규칙10.
v4.49.0 [신규] 앤트킹 스크린 차용 3종 — 상대RS의 맹점 보완.
        [절대 모멘텀] 대장후보/슈퍼대장에 3개월 +30% 게이트 (슈퍼대장은 베이스
               고려 15%). 폭락장의 "덜 빠져서 RS 높은" 가짜 주도주 차단.
               카드에 mom_3m_pct 필드. 탭이 비면 "주도주 없음"이라는 팩트.
        [주도업종 랭킹] 섹터 탭 상단: RS85+ & 3개월+30% & 200일선 위 생존자를
               업종별 카운트 (KR/US). 하루 등락 평균 대신 로테이션 감지용.
        [U/D 등급 반영] U/D ≤0.8(분산) → 등급 한 단계 강등 + 사유 표시,
               ≥1.5 매집 표기. A/D Rating 근사 — 차트 8/8이어도 분산이면 A 아님.
v4.48.3 [신규] 등급·성숙도 배지 — "수많은 종목 중 진짜"를 한눈에.
        [A/B/C 등급] 미너비니 트렌드 템플릿 8조건 채점(이 앱 이평 체계로 대응):
               200일선 위/상승 · 60일선 위 · 60>200 · 20일선 위 · 52주저점+30% ·
               52주고점-25%이내 · RS70+. A=8/8+RS87(주도주) B=7+ C=5~6 D=이하.
               눌림목·돌파·박스돌파·돌파임박·패턴 전 카드에 색상 칩 + 미충족
               조건 툴팁. 
        [패턴 성숙도] 치솟은깃발: 깃발 15봉(3주)+거래량 고갈 미달 시 ⏳미완성
               배지 + "N봉 더 필요" 표기 (오닐 정석 — 형성 중 추격은 실패 모드).
               컵앤핸들: 손잡이 5봉+거래량 고갈. 더블바닥: 감지 시 완성형.
               요건 충족 시 ✅요건충족 배지.
v4.48.2 [개선] 탭 상태줄 라벨 수정 — 모든 탭이 '눌림목 후보'로 표기되던 것을
               현재 탭 이름(돌파임박/패턴/...)으로. 
        [일지] 📈 R 누적 곡선 추가 — 종료 매매의 누적 R을 시간순 SVG로 표시
               (에쿼티 커브의 R 버전). 승/패 점 색상, 최대낙폭(R), 관찰 제외.
[변경 이력 계속]
v4.48.1 [긴급수정] 스캔 반복 실패 해결 — 유니버스 확대 후속.
        [싱글플라이트 락] 콜드 스캔(2~4분) 중 재시도·타 탭 요청이 전체 페치를
               중복 실행 → 메모리 2~3배 → OOM → 컨테이너 재시작 루프가 원인.
               시장별 asyncio.Lock으로 페치는 1회만, 후속 요청은 캐시 공유.
        [KR 균형 수집] top_n=1500 트림이 코스피 1250+코스닥 250으로 왜곡되던
               버그 수정 → 시장당 절반(750/750) 수집 + 교차 트림. 캐시 키 v6.
        [메모리 절감] 일봉 DF float64→float32 다운캐스트 (300MB+ → 절반).
               스캔 판정 동일성 검증 완료.
        [지터 축소] 0.05~0.15 → 0.02~0.08초 (락 도입으로 중복 부하 소멸).
v4.48.0 [신규] 무적 스캐너 1차 — BHE 사후분석 기반 품질 게이트 (감사 결과 반영).
        [감사 발견] ① climax_warning 미연결(죽은 코드) ② 베이스 카운트가 추세전환
               탭에만 연결 ③ risk_warn이 표시만 하고 제외 안 함(BHE 10.3% 통과 원인)
               ④ 200일선 이격도 미측정(BHE 진입 시점 +70% 과확장 미탐지).
        [리스크 하드 게이트] 피벗→현실화손절 거리가 US 8%/KR 12% 초과 → 제외.
               피벗 기준 판정(당일 급등 돌파 종가 기준 아님 — 정상 셋업 보호).
               눌림목·돌파·박스돌파·돌파임박 4개 경로 적용.
        [후기 스테이지 게이트] late_stage_info(): 200일선 이격(60%+ 경고/100%+ 제외),
               베이스 카운트(4차+ 경고), 클라이맥스(기존 함수 연결) 종합.
               카드에 🏔후기/🛑 배지 + 이격% 툴팁.
        [일지] 리스크바에 투입금액/추정현금/투입비중 표시.
        [진입폼] 피벗 대비 추격 % 표시 — +2% 초과 시 추격 금지 경고.
        [유니버스 확대] KR 800→1500 (거래대금 상위 = 유동성 있는 전 종목,
               로테이션을 베이스 단계부터 포착). US 1292→2072 (성장섹터 한정 해제,
               전 섹터 — 산업재 URI/PWR/CAT/GE 등 편입. 필터: $10+/시총 $500M+/
               거래량 30만+). 섹터 매핑 2072개 재생성 (기존 정밀 매핑 유지).
        [안정화] naver_kr 일봉 fetch에 재시도 2회 + 백오프(429는 길게) + 지터 —
               유니버스 확대 시 일시 실패가 종목 누락으로 이어지지 않게.
               ※ 콜드 스캔(하루 첫 스캔)은 2~3분으로 늘어남. 이후는 캐시로 즉시.
v4.47.0 [신규] R 기반 리스크 시스템 — 감정매매를 구조로 차단.
        [철학] 모든 결정은 매수 전에. 사이즈는 공식이, 진입 허용은 게이트가 정한다.
        [설정] /api/rsettings (영구 볼륨): 총자산·1R%(기본 0.5)·오픈 상한(3R)·
               주간(-3R)/월간(-6R) 한도·시장 게이트·분산일·환율. ⚙️ 버튼(일지 탭).
        [사이즈] 진입 폼에 실시간 계산줄: 주식수 = 1R금액 ÷ (진입-손절).
               US는 환율 환산. 확신은 진입 여부에만 — 수량엔 반영 안 됨.
        [대시보드] 일지 상단 리스크바: 오픈 리스크 게이지(nR/상한)·주간R·월간R·
               연속손실·게이트 상태.
        [서킷브레이커] ① 오픈 합계 > 상한 → 신규 진입 차단(관찰 저장은 허용)
               ② 주간 -3R → 그 주 잠금 ③ 월간 -6R → 그 달 잠금
               ④ 연속 3패 → 다음 진입 R 절반 자동 축소(승리 시 복구).
        [게이트] 🟢확인된 상승(3R)/🟡조정 압박(1.5R)/🔴조정(신규 0) — 수동 설정.
        [진입 잠금] 일지 추가(즉시 진입 계열)와 대기→▶진입 전환 모두 게이트 통과 필요.
               잠금 시 '관찰(대기)' 저장으로 우회 제안. 규칙은 기억이 아니라 구조로.
        [스캐너 수정] 눌림 깊이를 장중 고가 기준으로 측정 (종가 기준은 3~6%p 과소평가 —
               디앤디 -24% 조정이 -18%로 계산돼 눌림목 오탐되던 버그).
               시장별 상한: KR 15% / US 12%. 초과 시 눌림 아닌 새 베이스로 간주.
v4.46.1 [안전장치] 일지 이력 보존 강화 — 사용자 우려(과거 기록 덮어씀) 대응.
        [사실] 추가는 항상 새 id의 새 기록 — 덮어쓰기 코드는 원래 없음.
        [개선] ① 추적 중 종목 재추가: 차단 → 확인 후 별개 새 기록으로 허용
               (매매용+관찰용 병행 가능, 기존 기록 불변)
               ② 종료된 과거 기록 ✏️수정 시 경고 확인창(이력 오염 방지).
v4.46.0 [신규+개선] 일지 관찰 항목 구분 + 가독성 재구성.
        [관찰] 기록 목적 3종: 📈추세추종/⚡단타(실매매) vs 👁관찰(매매 안 함).
               추가 모달에서 선택, 관찰은 status=pending 고정 + 파란 👁뱃지 +
               좌측 파란 보더. 매매 통계(승률/평균R)에서 자동 제외, 별도
               관찰 카드로 카운트. 가격 추적은 유지(신호가 어떻게 풀리는지 관찰).
        [가독성] ① 정렬: 진입중→대기→관찰→종료→무산 그룹順, 그룹 내 최신순
               (섞여 보이던 원인 해결) ② 필터 칩 확장: 진입중/대기/종료/추세/
               단타/관찰 ③ 좌측 3px 상태 컬러 보더(초록=진입중, 호박=대기,
               파랑=관찰, 회색=종료) ④ 종목명 13.5px 볼드 ⑤ 줄무늬 배경
               ⑥ 근거/복기 2줄 클램프(호버 시 전체 툴팁) ⑦ 종료행 살짝 딤.
v4.45.0 [신규] 매도 사유 게이트 + 충동 매도 추적 (TradingCodex 승인게이트 개념의 1인용 축소).
        [배경] 사용자 문제: 수익이던 포지션이 본전 오면 손절가 전인데 손이
               먼저 나감(충동 매도). 결정과 실행 사이에 강제 단계를 삽입.
        [동작] 일지에서 청산 저장 시 종료 사유 필수 선택:
               🔴손절도달 / 📉트레일링·규칙익절 / 🎯목표도달 / ⚠️규칙외(충동).
               미선택 시 저장 차단. "충동"도 죄책감 없이 고르게 안내 —
               기록이 쌓이면 충동의 비용이 데이터로 보임.
        [표시] 충동 종료는 결과R 아래 빨간 ⚠️충동 뱃지. 추세추종 통계 카드에
               충동 N건 카운트(빨강). 자동 종료(손절/목표 도달)도 exit_reason
               기록. 기존 회고(e_review) 필드는 포스트모템 메모로 그대로 활용.
v4.44.3 [신규] ↕ 손절좁은순 정렬 토글 — 리스크%(손절폭) 오름차순 정렬.
        [이유] 약세장에선 타이트한 진입 자리가 드물어 "그나마 가장 좁은
               손절"부터 보는 게 실용적. 점수=셋업의 질, 손절폭=타이밍의 질.
        [동작] 토글 켜면 리스크% 오름차순이 최우선(시장 우선 정렬 무시).
               즐겨찾기 고정은 유지. 🎯주도주만(리스크 8%↓ 포함)과 병용 가능.
v4.44.2 [개선] 섹터 표 셀 안에 대표 종목 2개(+등락) 직접 표시.
        기존엔 마우스 오버 툴팁에만 있었음. 툴팁(상위 3)은 유지.
v4.44.1 [버그수정] 패턴 탭이 눌림목 결과를 보여주던 문제.
        [원인] /api/scan 모드 화이트리스트에 "pattern" 누락 → "pullback" 폴백.
               run_scan 디스패치에만 추가하고 엔드포인트 검증을 빠뜨림.
        [추가] 패턴 카드에 형성 기간(일수+주 환산) 명시. 섹터 탭을 코스피/
               코스닥/미국 3열 표로 재구성(셀에 마우스 올리면 상위 종목).
v4.44.0 [신규 2건] 📊 섹터 요약 탭 + 🧩 패턴 탐지 탭(실험).
        [섹터] /api/sectors: 유니버스 전 종목의 마지막 봉 등락률을 섹터로 집계.
               코스피/코스닥/미국 3패널, 섹터별 평균등락·종목수·상승비율·상위3.
               신규 파일 us_sectors_auto.py — 미국 자동 섹터 매핑(rreichel3
               industry 기반)으로 커버리지 21%→97%. sectors.py 정밀 매핑 우선,
               자동은 빈 곳만 보완. 파일 없어도 서버 정상(try/except).
        [패턴] scanner.analyze_pattern: 컵앤핸들(12~35% U바닥+3~20봉 얕은 손잡이),
               치솟은깃발(45봉내 +90% 후 3~20봉 ≤25% 깃발), 더블바닥(±4% 두 바닥
               +중간반등 10%+). 패턴이 거의 완성돼 피벗 근처(-6%~+1.5%, 깃발은
               -18%)인 종목만 표시. 돌파임박(위치 신호)과 달리 몇 주~몇 달의
               '형태'를 인식. 합성 데이터 검증: 3패턴 탐지 ✓, 랜덤워크 오탐 5%,
               단순 상승추세 오탐 0%.
v4.43.2 [치명버그수정] 가격이 며칠 전에 얼어붙던 문제 — 증분 캐시 신선도 검사.
        [증상] 기가비스 168,300원 표시(6/26 종가), 실제는 189,200원(7/1 종가).
               한국·미국 전 종목 가격이 서버 기동 시점 데이터에서 안 바뀜.
        [원인] 증분 다운로드가 직전 캐시의 나이를 확인하지 않고 무조건 재사용.
               "캐시에 있으면 재사용, 없으면 받기"라 서버가 살아있는 동안 기존
               종목은 영원히 갱신 안 됨("재사용 2309개"의 정체). 마감 후엔 이
               냉동 데이터가 당일 daykey로 디스크 저장돼 오염이 고착됨.
        [해결] (1) 재사용 조건에 신선도 추가: 직전 캐시가 REUSE_TTL(기본 30분,
               env 조정 가능) 이내일 때만 재사용, 넘으면 전체 재수집.
               (2) 디스크 캐시 네임스페이스 rs3→rs4 버스트: /data에 남은 오염
               파일 무시하고 새로 빌드. 옛 rs3 파일은 저장 시 자동 청소.
        [영향] 하루 첫 스캔·30분 경과 후 스캔은 전체 수집(KR ~100-200초).
               대신 가격은 항상 30분 이내 최신. 마감 후는 디스크 캐시로 즉시.
v4.38.0 [대형] 미국 유니버스 239 → 1424개 대폭 확장 (트레이딩 가능하게).
        [이유] 239개는 셋업 발굴에 너무 좁음. SLS(셀라스) 같은 중소형 모멘텀주가
               베이스 다질 때도 안 잡혔음 — 아예 스캔 대상이 아니었기 때문.
        [방법] 새 파일 us_universe_auto.py: 나스닥+NYSE 성장섹터(Tech/Health/
               Telecom/소비재/에너지) 시총 10억달러+ 자동 선별 1292개.
               기존 359개와 머지 → 미국 1424개. SLS 포함 확인.
        [잡주 제외] 시총 1B+ 기준 + 거래대금 동적 필터(scanner)로 저거래 자동 제외.
               워런트/유닛/SPAC/우선주/ETF 이름 필터링.
        [성능] 미국은 yf.download 배치(100개씩 15배치)라 요청 수 안 늘어남.
        [한국] 기존대로 거래대금 상위 600개 동적(pykrx).
v4.37.27 [개선] 인버스 탭 — 부정확한 '오늘 등락' 제거, 신뢰 가능한 지표로 교체.
        오늘 등락은 데이터 시점 문제로 계속 어긋나 → 아예 제거하고,
        시점 영향 적은 3가지 지표로 대체:
        1. 강도 점수 0~100 (20일선·기울기·5일·연속상승·거래량·200일선 종합)
        2. 기초지수 5일 등락 (코스닥/나스닥 등 — 정상 데이터에서 직접 계산.
           인버스가 왜 오르는지 직관적: "코스닥 -7% → 인버스 유리")
        3. 연속 상승일 (인버스가 며칠째 오르는지)
        정렬도 강도점수 기준으로. "정확한 실시간 가격은 증권앱" 안내 추가.
v4.37.26 [개선] 미국 인버스 '오늘 등락' 시점 어긋남 + 지수 신호등 복원.
        [SQQQ 반대 원인] 미국 ETF는 yfinance가 시간외(데이마켓)를 안 줘서
               '오늘 등락'이 전일 정규장 종가 기준 → 토스 실시간과 어긋남.
               곱버스 역산도 이 1x 값을 쓰므로 같이 어긋남. 실시간 데이터
               없이는 완전 해결 불가.
        [처리] 미국·곱버스 '오늘 등락'에 * 표시 + "시점 차이로 어긋날 수 있음,
               방향은 5일 수익률·강도로 판단" 안내. 5일 추세는 신뢰 가능.
        [복원] 지수 바 신호등(🟢🟡🔴) 다시 추가 — 단 이름 뒤에 작게 붙여
               가격 하락으로 오인되지 않게. "시장 분위기(분산일 기반)" 툴팁.
v4.37.25 [버그수정] 인버스가 +6% 오르는데 "부적합"으로 뜨던 문제.
        [원인] 인버스 강도 판정이 장기 정배열(20>60>200)을 요구 → 막 반등한
               인버스는 장기 추세가 아직 역배열이라 "부적합(weak)"으로 거름.
               근데 인버스가 장기 정배열 되려면 지수가 몇 달째 하락해야 함(이미 늦음).
        [수정] 인버스는 단기 모멘텀 중심으로 판정:
               strong = 20일선 위 + 20일선 상승 + 5일 +2%↑ (본격 하락장)
               building = 20일선 위 또는 3일 +1%↑ (반등 시작)
               weak = 그 외 (지수 견조)
               → 코스피 폭락에 인버스 +6%면 이제 strong으로 정상 판정.
v4.37.24 [개선] 곱버스 다시 추가 — 1x에서 등락 역산(네이버 거꾸로 데이터 우회).
        국장 지수 숏 매매 상품(곱버스)이 필요하다는 요청. 곱버스 일봉이
        네이버에서 거꾸로 오는 문제는, 같은 기초지수 1x ETF(데이터 정상)에서
        등락을 N배로 역산해 해결. 곱버스 현재가는 '1x 역산'으로 표시.
        - 코스닥 곱버스(291630) ← 코스닥 1x(251340)
        - 코스피 곱버스(252670/252710) ← 코스피 1x(114800)
        - 미국 SQQQ/SPXU/QID/SDS ← PSQ/SH
        [UI] 지수 바 앞 레짐 동그라미(🔴) 제거 — 하락으로 오인돼 혼란.
v4.37.23 [정리] 인버스 탭에서 곱버스(2x/3x) 전부 제거 → 1x 인버스만 8개.
        [원인 확정] /api/debugraw로 확인: 네이버가 코스닥150 곱버스(291630)
                   일봉을 거꾸로 줌(코스닥 하락인데 곱버스도 하락). 소스 문제라
                   코드로 수정 불가. 곱버스는 어차피 변동성 극심+가치침식으로
                   비권장 상품 → 제거가 정답.
        [구성] US 1x: PSQ/SH/DOG/RWM/VIXY. KR 1x: KODEX인버스/코스닥인버스/구.
               모두 데이터 정상. 국면 확인용으로 충분.
        [역할] 인버스 탭 = ETF로 시장 하락 국면 확인. 개별 종목 숏은 🩸붕괴 탭.
v4.37.22 [진단] 곱버스(291630) 데이터 거꾸로 문제 — raw 데이터 점검 엔드포인트.
        /api/debugraw/{ticker} 추가: 일봉 마지막5 + 장중현재가 + 병합결과를
        그대로 노출. 곱버스가 1x와 반대 방향 표시되는 원인(장중현재가 오류 vs
        일봉 파싱 오류) 진단용. 예: /api/debugraw/291630.KS
v4.37.21 [버그수정] 🩸붕괴 탭에서 숏 후보 67개인데 0개 표시되는 문제.
        [원인] '🎯 주도주만'(RS90+) 필터가 켜진 상태였는데, 이 필터는
               롱 전용(강한 종목). 숏 종목은 RS 약세(≤50)라 전부 걸러짐.
        [수정] 붕괴 탭에선 주도주 필터를 적용하지 않음(롱 전용 필터).
               빈 결과 메시지도 붕괴 탭에선 주도주 안내 대신 정상 안내.
v4.37.20 [버그수정] 🩸붕괴 숏 손익비 0.1R 문제.
        [증상] 숏 셋업 손익비가 0.1R로 나옴 — 손절(위)은 먼데 목표(아래)가
               지지선이라 코앞 → 아무도 못 들어가는 셋업.
        [원인] 목표를 '지지선/60일저점'으로 잡아, 지지선 근처서 잡힌 종목은
               목표가 코앞. 손절은 20일선이라 너무 멀어 위험 과대.
        [수정] 목표(아래) = 측정 하락폭(박스높이를 지지선 아래로 투영) →
               의미 있는 하락 목표. 손절(위) = 가까운 스윙 고가(타이트).
               손익비 1.5R 미만 셋업은 아예 제외(min_rr) → 0.1R 안 뜸.
v4.37.19 [신규] 🩸붕괴 탭 — Stage 4 숏 셋업 감지 (돌파임박/눌림목의 거울상).
        개별 종목 숏 매매 점수+근거 시스템. 미너비니/와인스타인 4단계 중
        Stage 4(하락/캐피출레이션) 종목을 0~100 점수로 포착.
        [조건] 역배열(20<60<200) + 200일선 하락 + 가격 이평선 아래 +
               지지선 이탈/직전 + 하락 거래량(기관매도) + RS 약세(≤50).
        [점수] 기본40 + 하락거래량15 + 분산일10 + 저점낮아짐10 +
               RS약세10 + 지지이탈10 + 이평반등실패5.
        [매매계획] 숏 진입(현재가)/손절(위쪽 저항=20일선or최근고가)/
                  목표(아래 더깊은저점 또는 -2R). 손익비 표시.
        [경고] 숏 손실무한·숏스퀴즈·급반등 위험 명시. RSI 과매도 시
               반등 위험 경고. 한국 종목은 개인 공매도 제약 경고.
               미너비니式 본래 현금 우선 — 단, 시장 약세 국면엔 숏도 활용.
v4.37.18 [버그수정] 인버스 곱버스 데이터 거꾸로 표시 문제.
        [증상] 코스닥 -5% 하락일에 1배 인버스는 +5%(정상)인데 코스닥150
               곱버스(291630)는 -11%로 거꾸로 표시. 같은 기초지수인데 방향 반대.
        [원인] 네이버에서 일부 ETF(곱버스) 데이터가 며칠 지연 들어옴 →
               옛 데이터로 분석되어 방향이 거꾸로 보임.
        [수정] analyze_inverse에 데이터 신선도 검증 추가. 마지막 봉이
               4일(max_data_age) 넘게 오래되면 분석 제외 → 지연 데이터 차단.
v4.37.17 [추가] 상단 지수란에 비트코인(BTC-USD)·닛케이(^N225) 추가.
        순서: 코스피·코스닥·나스닥·닛케이·비트코인. _fetch_yf_index로 일반화.
        배너(분산일/FTD) 판정은 기존대로 국내 지수만 사용 → 영향 없음.
v4.37.16 [신규] 🔻인버스 탭 — 지수 하락 베팅 감지 (국면 확인 + 매매 신호).
        지수 계속 하락 중 → 하락에 베팅하는 인버스 ETF 포착.
        - inverse_universe.py: 미국 10개(SQQQ/SH/SOXS/VIXY 등) +
          한국 6개(KODEX 인버스/곱버스 등).
        - analyze_inverse(): 일반 종목의 거울상. 인버스 정배열+상승 =
          지수 약세 = 하락장. strength(strong/building/weak)로 강도 표현.
        - /api/inverse: 인버스만 fetch·분석. strong 3개+면 "🔴 하락장 확인".
        - 인버스 과열(RSI 80+) 경고: 지수 과대낙폭=반등 시 인버스 급락 위험.
        [용도] (1) 국면 확인 — 인버스 강세 = 시장 나쁨 재확인
               (2) 매매 신호 — 단기·소액만. 곱버스 변동성/가치침식 경고.
               ※ 미너비니式 본래 하락장엔 현금 우선, 인버스는 보조.
        [기타] 일지 '수동 추가' 버튼 색 밝게(비활성처럼 보이던 것 수정).
v4.37.15 [신규] 수동 종목 추가 (스캐너에 없는 종목 직접 담기).
        비자처럼 스캐너에 안 뜬 종목도 관찰 리스트에 담고 싶다는 요청.
        - 일지 탭에 '➕ 수동 추가' 버튼 + 모달(종목코드·이름·시장·피벗·손절·메모).
        - 항상 대기(pending) 상태로 저장, manual:true 표시.
        - 일지에 '수동' 초록 배지로 구분 표시.
        - 피벗 입력 필수 → 기존 피벗 알림 시스템 그대로 재활용:
          봇이 /api/watch/pending으로 감시, 피벗 돌파 시 텔레그램 알림.
        - 자동 무산(14일 경과 / 손절 이탈)도 동일 적용. 봇 수정 불필요.
v4.37.14 [안전장치] 데이터 오류(손절≥진입) 항목 자동 감지·격리.
        [배경] 과거 버그 버전(v4.37.4 이전)으로 저장된 6-23 한국 종목들이
               손절가가 진입가보다 높게 박제됨(리스크 일률 12). 현재 코드는
               정상이나(손절에서 risk 역산), 깨진 일지 항목이 통계를 오염.
        [처리] isBrokenRow(손절≥진입) 판정 추가:
               - 승률·평균R 통계에서 제외 (journalStats, signalValidation)
               - 일지에 ⚠️데이터오류 빨간 배지 + 행 흐리게 표시
               - /api/watch/pending에서도 제외 (봇 감시 안 함)
        깨진 항목은 삭제(✕) 권장. 정상 항목(손절<진입)은 영향 없음.
v4.37.13 [신규] 대기종목 자동 무산 (2조건).
        그동안 대기는 수동 무산만 가능 → 돌파 안 와도 영원히 대기로 남아
        일지 지저분 + 봇이 죽은 종목 계속 감시. 자동 무산 2조건 추가:
          (1) 대기 WATCH_DAYS(14일) 경과 → '대기만료' 무산
              (돌파 셋업은 베이스 완성 후 빨리 터져야 유효, 식으면 무산)
          (2) 현재가가 손절가 아래로 빠짐 → '셋업붕괴' 무산
              (베이스/지지 무너지면 셋업 깨진 것)
        무산 사유(closed_reason)를 일지에 표시. 가격 갱신 시 자동 판정되며,
        자동 무산되면 /api/watch/pending에서도 빠져 봇 감시 대상에서 제외.
v4.37.12 [버그수정] 피벗 알림: 기존 대기종목이 /api/watch/pending에 안 뜨던 문제.
        v4.37.11 전에 담은 대기종목은 pivot 필드가 없어 전부 누락됐음.
        → pivot 없으면 entry(진입가=베이스천장)로 대체. 기존 대기종목도
        바로 감시 대상이 됨(MACOM/IREN 등). 앞으로 담는 건 pivot 직접 저장.
v4.37.11 [신규] 대기종목 피벗 돌파 알림 (텔레그램 봇 연동).
        일지 ⏳대기 종목이 피벗을 돌파하는 순간 텔레그램으로 알림받기 위함.
        - 일지에 pivot(피벗가) 저장 추가.
        - /api/watch/pending: status=pending + 피벗 있는 종목을
          {ticker,name,market,pivot,entry,stop,tab}로 노출.
        - 텔레그램 봇(stock-alert)이 이 API를 1분마다 읽어 현재가가 피벗
          이상이면 '🚀 피벗 돌파' 알림 (봇 레포 별도 배포 필요).
        역할 분리: 스캐너=분석/대기목록, 봇=가격감시/알림.
v4.37.10 [확장] ATR 손절 버퍼를 손절 쓰는 전 탭으로 확대 (탭별 배수 차등).
        v4.37.9는 눌림목만 적용했었음. 이제 모든 손절 탭에 적용:
          - 눌림목·추세전환: ATR×0.3 (현재가 근처 진입 → 변동성 여유)
          - 돌파·박스돌파·돌파임박: ATR×0.15 (피벗 돌파 진입 →
            타이트 유지가 정석. 피벗 깨지면 빠른 손절)
        공통 헬퍼 apply_atr_buffer()로 통일. _rr_block이 stop_struct(버퍼전
        구조손절)·atr_buf(버퍼값)를 함께 반환 → 전 탭에서 추적 가능.
        ※ leader/super/surge는 손절을 다루지 않아 대상 외.
v4.37.9 [개선] 손절에 ATR 버퍼 추가 — 종목 변동성 반영(노이즈 손절 방지).
        [배경] 구조 손절(지지선)을 정확히 밑에 두면, 지지선 살짝 깨고 반등하는
               노이즈에 털림. 종목마다 변동성이 다르니 그만큼 여유가 필요.
        [방식] 손절 = 구조 손절(지지선/저점) - ATR×버퍼배수(기본 0.3).
               ※ 예전 ATR손절(현재가-ATR×2.5)과 다름! 손절은 여전히 구조에
                 고정되어 현재가 따라 안 움직이고, ATR만큼만 아래로 버퍼.
               변동성 큰 종목은 버퍼 크게, 작은 종목은 작게 자동 반영.
        [설정] CONFIG["atr_stop_buffer"]=0.3 (추적하며 0.3~0.5 조정 가능, 0=끔).
        [추적] 반환에 stop_struct(버퍼전 구조손절), atr_buf(버퍼값) 추가.
        (티에스이: 구조손절 226,789 → 버퍼후 217,657, ATR 12%의 0.3배=9,132 여유)
v4.37.8 [신규] 시장 타이밍(오닐 M factor) — 분산일 카운트로 진입 자제 판정.
        [배경] 장 나쁠 때 진입하면 좋은 종목도 시장 동반 하락에 털림.
               반복 손절의 주요 원인 중 하나(요 며칠 한국장 -10% 폭락).
        [추가] _index_regime을 오닐식으로 강화: 분산일(전일대비 -0.2%↓ +
               거래량 증가 = 기관 매도일) 최근 25거래일 카운트.
               - 분산일 5개+ 또는 60일선 아래 → 🔴 비우호(신규진입 자제)
               - 분산일 3~4개 → 🟡 주의(선별 진입)
               - FTD(하락후 반등 +1.5% & 거래량증가) → 바닥 신호 표시
        [표시] 상단 시장 배너에 분산일 개수·FTD 표시.
               "🔴 시장 비우호 — 신규 진입 자제 · 분산일 N개"
        → 빠지는 장에서 진입을 막아 반복 손절 방지.
v4.37.7 [신규] 변동성(ATR%) 경고 — 반복 손절 방지 (미너비니: 손절폭은 변동성에 맞춰라).
        [배경] 고변동 종목(티에스이 ATR 12%)을 타이트한 손절로 진입하면
               하루 정상 변동(노이즈)에 털리고, 추세는 맞아서 손절 후 급등이
               반복됨. 변동성을 모르고 진입한 게 반복 손절의 핵심 원인.
        [추가] analyze에 atr_pct(ATR/현재가%), vol_high(ATR 7%+),
               atr_tight(손절폭 < ATR×1.5) 계산.
        [표시] 고변동 종목에 경고:
               - 손절이 변동성 대비 타이트하면 🔴 'ATR n% — 손절 대비 큼
                 (노이즈 손절 위험)'
               - 그 외 고변동은 🟡 'ATR n% — 비중 축소 권장'
        → 진입 전에 "이 종목은 하루 n% 흔들린다"를 알려 노이즈 손절을 예방.
v4.37.6 [버그수정] 눌림목 손절이 spike 꼬리 저점으로 잡혀 비현실적으로 멀던 문제.
        [증상] 티에스이: 20일선 229,080이 합리적 손절인데 손절 198,500(리스크
               20.4%)로 표시. 6월 장중 급락 꼬리(198,500)가 손절로 잡힘.
        [원인] (1) 지지 이평이 현재가 위면(MA10이 살짝 위) 후보에서 탈락 →
               멀리 있는 pullback_low로 떨어짐. (2) pullback_low가 단순 최저가라
               일시적 장중 급락 꼬리(spike low)를 그대로 손절로 씀.
        [해결] (1) 현재가 '아래'의 이평 중 가장 가까운 것을 손절 기준으로
               (MA10이 위면 자동으로 MA20 사용). (2) 단순 최저가 대신
               significant_support(2번+ 지지받은 의미있는 저점)로 spike 꼬리 제외.
               (3) 화면 지지선 표시를 실제 손절 기준과 일치(stop_ma_name).
        (티에스이: 손절 198,500→226,789(MA20), 리스크 20.4%→9.1%)
        ※ 미너비니/오닐 통일 작업의 일부 — 손절은 의미있는 지지 기준.
v4.37.5 [개선] AVWAP을 미너비니/오닐 정석으로 — 강세주 과열 오진 해결.
        [증상] 기가비스: 10일선 위 2.77% 건강한 눌림인데 AVWAP +26.8% '과열'.
        [원인] 앵커가 '최근 60봉 중 거래량 최대 봉'이라, 강세주는 옛날 폭등일이
               앵커로 잡혀 AVWAP이 바닥에 깔리고 현재가와 과도하게 벌어짐.
        [해결] 미너비니 extension 판단은 단기(10/20일선) 기준이므로:
               - 앵커 = 최근 25봉(5주) 중 '최저가 봉'(최근 베이스/눌림의 시작)
               - = 최근 매수자들의 평균가 기준 → 강세주 건강한 눌림이 정상 판정
               - zone 임계 하향: 과열 20→15%, 연장 10→8% (단기 이격 기준)
        (기가비스류 검증: 이격 26.8%→1.4%, zone 과열→healthy)
        ※ 시스템 기준을 미너비니/오닐로 통일하는 작업의 일부.
v4.37.4 [핵심수정] 손절이 현재가 따라 움직이던 문제 — 구조 기반 고정으로.
        [증상] 티에스이: 지지선 MA10(3%)인데 손절은 215,600(12%)으로 따로 놀고,
               현재가 245,000→255,000 변하면 손절도 따라 움직임. 235,000에
               진입했는데 손절이 자꾸 바뀜.
        [원인] rr_info의 '손절 현실화'가 ATR손절(현재가-ATR×2.5)과 12%상한
               (현재가×0.88)으로 손절을 덮어씀. 둘 다 현재가 기반이라 손절이
               가격에 연동돼 흔들리고, 실제 지지선과 무관한 값이 나옴.
        [해결] ATR손절·12%상한 제거. 손절은 호출부(탭별)에서 계산한 구조 기반
               값(지지선/눌림저점/베이스하단)을 그대로 사용 → 가격 구조에 고정.
               현재가 변해도 손절 불변. 화면 지지선=손절가 일치. 리스크%는
               상한 없이 현재가 기준으로 정직하게(지지 가까우면 작게).
        [탭별 진입기준] 눌림/추세전환/돌파/박스돌파=현재가, 돌파임박=피벗.
        (티에스이: 손절 215,600→237,600, 리스크 12%→3%로 정상화)
v4.37.3 [버그수정] 눌림목 리스크%가 현재가 아닌 피벗 기준으로 계산되던 문제.
        [증상] PSK(피에스케이): 현재가 165,500·손절 164,120(0.8% 차이)인데
               리스크 12%로 표시 → '손절폭 넓음' 경고 → 좋은 자리가 '진입자제'로.
        [원인] analyze_pullback의 _rr_block 호출에 entry=None이라 리스크/손절이
               피벗 기준으로 계산됨. 게다가 손절폭 12% 상한이 피벗 기준으로 걸려
               손절가 자체가 인위적으로 당겨짐(eff=pivot×0.88).
        [해결] 눌림목은 현재가 근처 진입이므로 entry=close(현재가) 전달.
               → 리스크/손절/손익비 모두 현재가 기준으로 정확 계산.
               PSK는 리스크 12%→0.8%로 정정, 진짜 손익비 좋은 자리로 표시됨.
        [영향] 눌림 종목 전반의 리스크%가 현재가 기준으로 낮아져 '진입양호'가
               늘어남(원래 눌림은 현재가 진입이라 리스크 작은 게 정상).
v4.37.2 [버그수정] 대장후보(leader)·슈퍼대장(super) 탭에 '일지에 추가' 버튼
        누락. 버튼 렌더 조건에서 두 모드가 빠져있어 RS강한 종목(CORZ/RIOT 등)을
        추적관찰 담을 수 없었음. 조건을 s.ticker 있으면 전 모드 표시로 단순화.
        leader/super는 pivot/stop 없어 진입가=현재가 기본, 손절은 사용자 입력
        (null 체크로 안전). 추적관찰이면 일지에서 '대기'로 전환해 쓰면 됨.
v4.37.1 [버그수정] 저가주 폭등 종목의 RS 과대평가(예: BMNR RS99인데 실제 추락중).
        [원인] rs_raw_score의 분기수익률이 단순비율(p0/p3-1)이라, 1년전 저가
               ($1)→폭등($35)→현재하락($15) 종목이 분기수익률 수백%로 점수폭발.
               현재 추세가 하락이어도 과거 폭등이 RS를 지배 → 지수 빼도 RS99.
        [해결] 분기수익률을 로그수익률 ln(p0/p3) + ±0.7클립으로 변경.
               극단폭등을 압축해 저가주 왜곡 차단. 정상/강한 추세주 순위는 보존.
               (BMNR류 raw 1.601→0.027, 지수보다 낮아져 RS 하위권으로 정상화)
        디스크캐시 네임스페이스 rs2→rs3로 옛 캐시 무시.
v4.37.0 [핵심개선] RS를 '지수 대비 상대강도'로 전환.
        [문제] 기존 RS는 universe 내 절대수익률 백분위라, 종목을 늘리면
               (v4.36) 같은 종목 RS가 출렁이고, '시장 대비 강함' 변별력이 약했음.
               (강한 종목끼리 모인 풀 안에서 순위 → 편향)
        [해결] 각 종목 raw score에서 해당 지수(미국=^IXIC, 한국=코스피/코스닥)의
               raw score를 빼 '지수 대비 초과성과'를 만들고 그걸 백분위로.
               → universe 편향 완화, '지수를 이긴 정도' 순위로 의미 명확화.
               지수 일봉은 RS 계산 직전 1회 fetch(_benchmark_rs_scores).
        [캐시] 디스크 캐시 네임스페이스 datacache_→datacache_rs3_ 로 분리해
               옛 절대RS 캐시는 자동 무시(배포 후 첫 스캔에서 새 RS로 재빌드).
        [주의] RS 분포가 바뀌므로 같은 종목의 RS 숫자가 이전과 다를 수 있음.
               필터 임계값(80/90 등)은 유지 — 분포 보고 다음에 재조정 가능.
v4.36.1 [버그수정] 신규 확장 종목(v4.36.0)의 섹터 미표시.
        us_universe_ext.py 225종목이 sectors.py 매핑에 없어 전부 '기타'로
        떠 섹터가 안 보였음. sectors_ext.py 추가로 225개 100% 매핑.
        신규 섹터: 광통신/양자컴퓨팅/원자력/우주항공/코인채굴/핀테크.
        (VECO=반도체-장비, IONQ=양자컴퓨팅, ALAB=반도체-설계 등)
v4.36.0 [대규모] universe 확장 + 배치 fetch + 마감후 자동 스캔.
        목적: 추세전환 초입(중소형 성장주)을 놓치던 한계 해소. universe가
              대형주 위주라 VECO 같은 RS98 종목도 안 잡히던 문제.
        (1) 미국 fetch를 yf.Ticker 개별 → yf.download 배치(100개씩)로 전환.
            요청수 1/100로 축소 → 야후 차단 위험 제거, 종목 확대 가능.
            한국은 네이버 개별 유지(배치 API 없음), 동시성 6.
        (2) universe 확장: 미국 239→359(us_universe_ext.py, S&P500+나스닥100+
            반도체장비/광통신/SaaS/양자/원자력 등 중소형 성장주, VECO 포함).
            한국은 pykrx로 거래대금 상위 KR_TOP_N(기본600) 동적 구성
            (하루1회 KRX조회, 파일캐시, 실패시 정적 폴백). requirements에 pykrx.
        (3) 마감후 자동 스캔 스케줄러: 한국15:40·미국06:00(KST) 마감 직후
            백그라운드로 데이터+주요모드 미리 빌드→디스크캐시. 접속 전 준비완료.
            5분 폴링, 거래일당 시장별 1회만 워밍(_warmed 중복방지).
            동적 universe도 이때 갱신. 장중엔 기존 캐시 재사용(외부호출 0).
        [주의] pykrx는 KRX 접속 필요 — Railway 네트워크 허용 확인.
               배포 후 첫 마감 워밍까지 동적KR은 정적 폴백으로 동작.
v4.35.3 [기능] 일지 진입상태(status) 추가 — 추적관찰 시 R통계 오염 방지.
        [문제] 돌파 안 온 추적관찰 종목도 손절가 닿으면 자동 -1R 손절로
               기록돼 승률/R 통계가 망가짐(실제론 안 샀는데).
        [해결] 4가지 상태: 대기(pending)/진입(entered)/무산(missed)/종료(closed).
          · 돌파 계열 일지 추가 시 기본 '대기' → 손익통계 제외, 손절가 닿아도
            손절 처리 안 함. 진입가(피벗) 도달하면 자동으로 '진입' 전환(진입일 갱신).
          · 눌림목/추세전환 등은 기존처럼 '진입'으로 시작.
          · 일지 테이블에 상태 뱃지(⏳대기/▶진입/✖무산/✔종료) + 대기 항목엔
            [▶진입][✖무산] 빠른 전환 버튼. 무산은 통계 완전 제외(흐리게 표시).
          · 통계 카드: 진입N·대기N·종료N 구분 표시. CSV에 상태/모드 컬럼 추가.
          · 기존 일지(status 없음)는 '진입'으로 간주(하위호환).
v4.35.2 [개선] 일지 진입가 기본값을 모드별로 분기.
        [문제] 돌파 매매는 피벗 돌파 시 진입인데, 일지 추가 시 entry 기본값이
               현재가로 채워져 추적관찰 시 R값이 잘못 잡힘(현재가 기준 계산).
        [해결] 돌파/돌파임박/박스돌파 모드는 entry 기본값 = 피벗(돌파가),
               눌림목/추세전환 등은 현재가 유지. 진입가 라벨에 힌트 표시
               ('피벗 돌파가' / '현재가 근처'). 실매매 종목은 사용자가 직접 수정.
v4.35.1 (1) [버그수정] 일지 탭 라벨 오류. 일지 추가 시 탭이름 매핑에
        imminent(돌파임박)·leader(대장후보)·super(슈퍼대장)가 빠져서
        해당 탭에서 추가한 종목이 전부 '눌림목'으로 잘못 저장됨.
        → MODE_TAB_LABEL 매핑 한 곳으로 통일(modeTab/modeCategory 헬퍼),
          전 모드 커버. 저장 레코드에 mode_raw 원본도 함께 남김(추후 디버깅).
        주의: 이미 잘못 저장된 기존 일지 항목은 수동 수정 필요.
        (2) [버그수정] /api/debug 한글 깨짐(모바일). ensure_ascii=False +
        charset=utf-8 명시로 정배열/거래량배수 등 한글 정상 표시.
v4.35.0 (1) 장 마감 후 디스크 캐시 — 로딩 속도 개선.
        [문제] 마감 후에도 일봉이 안 바뀌는데 10분 TTL이라 야후/네이버를
               계속 재호출 → 로딩 점점 느려짐.
        [해결] _market_session_key(): 한국장(평일 15:40↑)·미국장(KST 06:00↑)
               둘 다 마감하면 '거래일 키' 반환. 그 키로 데이터를 /data 디스크에
               pickle 저장하고, 다음 거래일 전까지 TTL 무시하고 재사용.
               → 마감 후 첫 스캔 1회만 fetch, 이후 외부호출 0(즉시 로딩).
               서버 재시작/재배포돼도 볼륨에서 복원. 모드결과 캐시도 동일 적용.
               장중엔 기존 10분 TTL 유지(실시간성).
        (2) 추세전환(turnaround) = '1→2단계 첫 돌파'로 강화.
        [추가] 200일선 바닥 상향전환 게이트(20봉 전보다 높아야 통과) +
               돌파일 거래량 폭증(최근5일 최대 ≥50일평균×1.5 → 🚀 가산).
               카드에 📈장기선↑ / 돌파일 거래량 배수 / 🚀 배지. 기가비스式
               1단계 졸업 신호 포착.
v4.21.0 밸류에이션 배지(ROE·PER밴드) — 참고용/온디맨드. fundamentals.py +
        /api/fundamentals/{ticker}. 카드 '밸류 보기' 클릭 시만 호출.
        진입조건엔 미반영. 미국주 yfinance(PER밴드), 한국주 네이버(PER/PBR/ROE).
v4.20.0 (1) R 손익비를 실제 진입가 기준으로 수정 (2) 상단 지수 바 추가.
        [문제1] 돌파/박스돌파 카드의 손익비 R이 '피벗 진입' 가정이라,
                이미 피벗 위로 연장된 자리에서 사면 1R(=pivot-stop)이 실제보다
                작아 손익비가 부풀려짐. 리스크%는 현재가 기준인데 R은 피벗 기준
                → 한 카드 안에서 분모가 두 개.
        [해결1] rr_info(entry=) 인자 추가. 이미 돌파한 돌파/박스돌파는
                entry=close(현재가)로 통일. 눌림목/돌파임박은 피벗 진입이 맞아
                기존대로 pivot 폴백 유지. 박스돌파 risk_pct도 현재가 기준으로.
                [검증] MS 예: 옛 1R=$6.67(3%)→ 새 1R=$9.33(4.2%), 실전 2R 정직.
        [추가2] /api/indices: 코스피·코스닥(네이버 fetch_index) + 나스닥(yf ^IXIC).
                상단 지수 바, 60초 캐시/갱신. 한국식 색(상승 빨강/하락 파랑).
v4.19.0 U/D Volume(매집/분산 비율) 추가 — 오닐 지표.
        [추가] up_down_volume(): 최근 50일 상승일거래량÷하락일거래량.
               1.0↑ 매집(기관 매수), 1.0↓ 분산(기관 매도).
               돌파/박스돌파/돌파임박 카드 RSI 옆에 "U/D X.X" 표시.
               1.0 이상 초록, 미만 주황. 거래량 폭발해도 분산이면 주의.
v4.18.1 돌파임박 손절을 '의미있는 지지(폭락바닥 제외)'로.
        [문제] 한올바이오파마 손절 38,400 = 리스크 30.94%(비현실적).
               38,400은 5/20 폭락 바닥 한 번. 손절을 단순 최저가(min)로
               잡아서 폭락 꼬리 하나에 손절이 끌려감.
               (20일선 -2%는 현재가 위라 후보에서 빠지고 폭락바닥만 남음)
        [해결] significant_support(): 저가 중 ±2% 안에 2번 이상 지지받은
               가격만 '진짜 지지'로 인정. 폭락 바닥 하나는 제외.
               손절 우선순위: 의미있는 지지 → 20일선 -2% → (폴백) 단순 저점.
        [검증] 폭락바닥(38,400) 무시 → 박스지지(49,000)로 손절. 리스크 30%→~12%.
v4.18.0 박스돌파도 '의미있는 저항(꼬리 제외)'으로 박스 상단 계산.
        [문제] 박스돌파 함수는 박스 상단을 단순 max(고가)로 잡아서,
               동진쎄미켐 5/26 꼬리(72,200)를 박스 천장으로 오인.
               → 박스폭 32~42%로 부풀려져 탈락 + 돌파여부 false.
        [해결] v4.17.0의 significant_resistance(2번+ 닿은 저항)를
               박스돌파에도 적용. 꼬리 제외하고 진짜 박스천장 인식.
        [검증] 꼬리(72,200) 제외 → 박스상단 65,000, 박스폭 42%→12%로 정상화.
        [참고] 포스코스틸리온(058430)은 유니버스 미등록이라 별도. 추가 필요시 요청.
v4.17.2 진단 API에 박스돌파(boxbreak) + 박스/거래량 지표 추가.
        modes에 boxbreak 통과/탈락 표시. indicators에 거래량배수(vs50일),
        120일선 이격, 박스20/40/60 폭·상단·돌파여부 추가.
        → 박스돌파/돌파임박 탈락 이유를 정확히 진단 가능.
v4.17.1 진단 API가 접미사(.KS/.KQ) 없이도 자동 매칭.
        [문제] /api/debug/005290 → "데이터 없음". 실제 코드는 005290.KQ인데
               접미사 없이 조회해서 네이버가 못 찾음. (데이터 문제 아님)
        [수정] 숫자코드만 입력하면 유니버스에서 .KS/.KQ 자동으로 찾아 붙임.
               이제 /api/debug/005290 → 005290.KQ(동진쎄미켐) 자동 매칭.
v4.17.0 피벗을 '여러 번 닿은 의미있는 저항'으로 (오버슈팅 꼬리 제외). 전모드 적용.
        [문제] 피벗 = 단순 최고가 → 긴 꼬리 하나(오버슈팅)를 천장으로 오인.
               동진쎄미켐: 진짜 천장 64~65k인데 5/26 꼬리 72,200을 피벗으로
               잡아 현재가가 -11%로 멀어져 돌파임박에서 탈락.
        [해결] significant_resistance(): 구간 고가 중 ±2% 안에 2번 이상 닿은
               가격만 '진짜 저항'으로 인정. 1번만 튀어나온 꼬리는 천장에서 제외.
               그런 저항이 없으면(진짜 신고가 추세) 단순 최고가로 폴백.
               → 전고(중기) 피벗에 적용. 모든 모드가 더 정확한 천장 사용.
        [검증] 꼬리(72,200) 무시하고 박스천장(65,000) 잡음 확인.
               신고가 추세 폴백 정상. 전 모드 스모크테스트 통과.
v4.16.1 박스돌파 가짜 돌파 수정 (모더나 오탐).
        [문제] 박스 상단을 '종가'로 잡아서, 진짜 천장(고가)보다 낮게 인식.
               모더나(고가천장 56, 현재 55.4 = 아직 아래)가 "돌파"로 오탐.
        [수정] ① 박스 고점/저점을 고가(high)/저가(low) 기준으로 → 진짜 천장 인식
               ② 돌파 판정 엄격화: 박스 상단 +0.5% 이상 확실히 넘어야 인정
                  (천장 코앞·살짝 닿음은 박스돌파 아님 → 돌파임박 영역)
        [검증] 천장 아래 종목 제외 / 진짜 돌파만 통과 확인.
v4.16.0 돌파임박에 '박스 상단 두드림 횟수' 추가.
        [신규] 동진쎄미켐처럼 박스 상단(피벗)을 여러 번 두드리는 종목 구분.
               최근 20봉 중 고가가 피벗 ±2% 안에 닿은 횟수(연속은 1회로 묶음).
               미너비니/오닐: 저항을 여러 번 두드리면 매물벽 약해져 돌파확률↑.
               - 2회 이상이면 "👊 N번 두드림" 배지 + 점수 가점(최대 +10)
               - 같은 돌파임박 중 더 유망한(여러번 두드린) 종목이 위로.
v4.15.1 박스돌파 카드 표시 버그 수정 (undefined 떡칠).
        [문제] 박스돌파 결과가 눌림목 카드 양식으로 그려져서 undefined 표시.
               (프론트 카드 렌더링에 boxbreak 분기가 없어서 else로 빠짐)
        [수정] 카드 지표/배지/거래량줄/스파크/일지라벨에 boxbreak 분기 추가.
               박스돌파 전용 표시: 연장도/거래량/박스폭/피벗(박스상단 N일)/
               박스돌파 N일 배지/장중돌파 미확정 배지.
v4.15.0 📦 박스돌파(boxbreak) 모드 신규 추가.
        [신규] 국장에서 자주 나오는 '박스 탈출 / 삼각수렴 돌파' 패턴 전용 탭.
               - 20/40/60봉 박스를 모두 검사, 하나라도 돌파면 포착
               - 거래량 1.5배+ 동반 필수 (박스돌파의 핵심, 가짜 거름)
               - 120일선(장기선) 위 — "장기선 위 박스탈출은 크게 간다"
               - RS 70+ (강한 종목만)
               - 급등 포함(가온전선 +29%도 돌파면 OK)
               - 장중 돌파도 표시(미확정 배지)
               - 박스 좁고 길수록 + 거래량 클수록 점수 높음
               예: 예스티(박스탈출 거래량폭발), 가온전선(하락추세 상단돌파)
v4.14.0 피벗을 '고정된 베이스 저항'으로 변경 (신고가 쫓아다니던 문제 해결).
        [문제] 피벗=최근 N봉 고가 라서, 주가가 신고가 만들 때마다 피벗이
               따라 올라감. "57,000인 줄 알았는데 57,700이네?" 혼란.
               기준선 역할을 못 함.
        [해결] 피벗 계산 시 직전 2봉(오늘·어제 신고가 갱신 봉)을 제외하고,
               그 이전 베이스(횡보 구간)의 고점을 피벗으로. → 주가가 신고가를
               만들어도 피벗(과거 천장)이 고정됨. 진짜 저항선 역할.
        [검증] 오늘 55,000이든 59,000(신고가)이든 피벗 동일(56,000) 확인.
v4.13.1 RS 계산을 IBD/MarketSmith 정식 공식으로 교체 (트레이딩뷰 근접).
        [변경] 기존: 1/3/6/9/12개월 임의 가중(합 1.2, 비정규화)
               신규: IBD 정식 = 12개월을 3개월씩 4분기로 나눠
                     0.4×Q1(최근) + 0.2×Q2 + 0.2×Q3 + 0.2×Q4 (합 1.0)
                     = 최근 분기 2배 가중. 트레이딩뷰 RS Rating과 같은 공식.
        [한계] 모집단이 우리 유니버스(493개)라 트레이딩뷰(전체시장) 대비
               숫자는 다를 수 있음. 공식은 동일, 상대 순위 의미도 동일.
v4.13.0 돌파임박 필터를 v4.9.1 수준으로 되돌림 (폭넓게 보여주기 복원).
        [배경] 손절 정교화하려 필터를 자꾸 조여 한국 0개까지 갔음. 사용자가
               "돌파 직전 종목을 폭넓게 보여주던 게 더 좋았다. 손절은 직접 거른다"고
               하여 필터를 원복. (방법 2: 필터만 되돌리고 좋은 추가는 유지)
        [되돌림] RS 70→50, 베이스폭 필터 제거, 급등 제외 제거,
                 손절은 단순 방식(최근저점/20일선-2% 중 현재가 아래 최고)으로.
        [유지] 클라이맥스 과열경고(🛑/🔆), 손익비 R, 종목검색, 데이터캐시,
               검색창 여백 등 v4.10~4.12의 좋은 추가는 그대로.
v4.12.1 돌파임박 RS 하한 50→70 (주도주만) + 베이스폭 15→12% (손절 타이트).
        [문제] RS 50~69 약한 종목(GS,기아,KT&G 등)이 떠서 주도주가 아님.
               손절도 13~14%로 여전히 깊었음.
        [수정] RS 하한 70 (미너비니 주도주 기준). 약한 종목 원천 제외.
               베이스폭 12%로 조여 타이트한 VCP만. RS로 거른 강한 종목 중
               좁은 베이스만 남아 손절 자연히 5~8%로.
        [효과] 종목 수↓ 질↑. RS 70+ & 타이트 베이스 = 진짜 미너비니 셋업.
v4.12.0 베이스폭 필터 완화(한국종목 전멸 수정) + 클라이맥스 매도경고 추가.
        [수정] v4.11.0 베이스폭 12%/15봉 필터가 한국 종목을 전부 걸러버림
               (한국 변동성이 커서). → 15%/10봉으로 완화. SK하이닉스류
               급등 종목은 여전히 제외되되, 잔잔한 한국 종목은 통과.
        [신규] 미너비니식 클라이맥스(과열/매도) 경고 — 모든 모드 카드에 표시.
               급등은 매수 아닌 '경계' 신호라는 미너비니 철학 반영.
               감지: 포물선급등(10봉+30%)/이평과열(20일선+25%)/최대급락일/
                     소진성거래량(60봉최대+음봉)/RSI과열(80+).
               🛑과열(danger: 최대급락·소진거래량) / 🔆과열(caution: 그 외)
v4.11.1 진단 API 강화 — 모드별 통과/탈락 + 지표 스냅샷.
        - /api/debug/{ticker} 가 7개 모드 각각 통과/탈락을 보여줌
        - 정배열 여부, 200일선 이격, 최근5봉 상승률, 베이스폭 등 지표 표시
        - "왜 이 종목이 이 탭에 없지?" 질문에 정확히 답하는 도구
v4.11.0 돌파임박 = '잔잔한 베이스 + 천장 코앞'으로 명확화 (길 B).
        [문제] 손절 공식을 아무리 바꿔도 깊게 나옴(SK하이닉스 22%, 현대 48%).
               근본 원인: 손절이 아니라 필터였음. '급등으로 천장 도달'한 종목이
               잔뜩 잡혀서, 어떤 손절을 써도 멀 수밖에 없었음.
        [해결] 돌파임박 필터에 '잔잔한 베이스' 조건 추가:
               · 베이스 폭(최근 15봉 고저폭) ≤ 12% — 넓으면 급등/난조로 제외
               · 최근 5봉 상승률 ≤ 10% — 이미 급등한 종목 제외
        [결과] 신세계/현대백화점/SK하이닉스처럼 급등 중인 종목은 자동 제외.
               남는 종목은 천장 아래 조용히 횡보 → 손절 자연히 5~8% 타이트.
        [눌림목과 차이] 눌림목=천장에서 먼 깊은 조정 / 돌파임박=천장 코앞 얕은 횡보
v4.10.3 돌파임박 손절을 ATR → 베이스 저점 기반으로 변경 (돌파매매 정석).
        [진단 결과] /api/debug로 확인: SK하이닉스 ATR 7.87%는 버그가 아니라
                    실제로 최근 2주 변동성이 극심했음(6/8 -7.7%, 6/9 +15.9%).
                    중앙값으로도 안 줄어든 이유 = 이상치가 아니라 전반적 고변동성.
        [변경] 손절 = 최근 15봉 베이스(횡보 구간) 저점.
               종목 변동성(ATR)이 아니라 '이 돌파 셋업이 무너지는 지점'.
               · 잔잔한 베이스 후 천장 근접 = 손절 타이트(5%) = 좋은 셋업
               · 급등으로 천장 도달(SK하이닉스) = 손절 깊음(22%) ⚠️ = 부적합
        [효과] 손절% 자체가 셋업 품질 지표가 됨. 급등 도달 종목이 자동 걸러짐.
v4.10.2 손절 원인 진단용 API 추가 + 검색창 여백 수정.
        - v4.10.1 중앙값 적용 후에도 한국 종목 손절 15~22% 지속 → ATR 계산이
          아니라 네이버 High/Low 데이터 자체를 의심. 미국(yfinance)은 정상.
        - 진단 API: /api/debug/{ticker} → 최근 14일 OHLC 원본 + TR 분해 확인
          (예: /api/debug/000660.KS 를 브라우저에서 열어 데이터 보고)
        - 검색창 좌우 여백을 카드 그리드와 동일하게(24px/모바일16px) 맞춤
v4.10.1 ATR 손절 버그 수정 — 평균→중앙값 (이상치 강건).
        [문제] 돌파임박 종목 손절이 죄다 10~31%로 비정상. 삼성SDS 31.7%.
               원인: ATR을 '평균(mean)'으로 계산 → 최근 급등/급락 며칠의
               거대한 변동폭이 14일 평균을 통째로 끌어올림. 돌파임박 종목은
               본질적으로 최근 크게 움직인 종목이라 전부 부풀려졌음.
        [해결] ATR을 '중앙값(median)'으로 변경 → 급등 며칠을 무시.
               잔잔한 종목 손절 ~3%, 보통 ~5%, 변동성큰 ~8%로 정상화.
               임의 통일이 아니라 종목별 진짜 변동성대로 차등 적용.
v4.10.0 돌파임박 손절을 ATR 기반(변동성 반영)으로 변경 + 피벗 기준 통일.
        [문제] 손절을 현재가 기준 지지선(20일선/옛저점)으로 잡아, 피벗 진입
               가정과 따로 놀았음. 게다가 모든 종목 동일 기준이라 변동성 무시.
        [변경]
        - 손절 = 피벗 - (ATR×2). 종목 변동성에 따라 손절폭 자동 조절
          · 잔잔한 종목 → 타이트, 변동성 큰 종목 → 넓게
        - 리스크%를 피벗 기준으로 통일 → 라벨 '(피벗진입)'이 실제와 일치
        - 손절 깊으면(>8%) ⚠️ 경고 → 변동성 과한 종목 걸러짐(예: HL만도)
        - scanner: atr() 함수 추가
v4.9.1  종목 검색 기능 추가 (프론트만).
        - 스캔된 카드 중에서 종목명/코드로 실시간 필터링
        - 서버 부하 없음 (이미 받은 목록에서 거름). 일지 탭에선 숨김
v4.9.0  손익비(R) 표시 + 리스크 '피벗진입 가정' 명시.
        - 리스크%가 '피벗에서 진입' 가정임을 라벨로 명시 (현재가 아님)
        - 손익비 R 추가: (목표-피벗)/(피벗-손절). 목표=전고 우선, 없으면 2R
        - 2R 이상 초록, 1.5 미만 빨강 경고. 진입 가치 한눈에
        - scanner: rr_info() 헬퍼, 4개 모드에 target/rr/target_basis
v4.8.0  데이터/필터 분리로 데이터 소스 호출 대폭 감소 (차단 방지).
        - 종목 일봉은 시장 단위로 1번만 받아 캐시(_data_cache), 모드 전환 시 재호출 안 함
        - 모드 7개 다 눌러도 네이버 호출은 시장당 1번 (기존 대비 1/7)
        - 동시 호출 6개로 제한(세마포어) + executor 워커 12->8 축소
v4.7.1  탭 순서 변경: 돌파임박을 첫 번째(기본)로, 눌림목 두 번째.
v4.7.0  즐겨찾기(★) 기능 + 탭 순서 변경.
        - 카드 별 버튼으로 즐겨찾기 토글, 즐겨찾기는 카드 최상단 고정
        - 서버 저장(favorites_user.txt), 기기 바뀌어도 유지
        - 탭 순서: 눌림목 → 돌파임박 → 돌파 → ...
v4.6.1  거래량 + 거래대금 표시 추가 (눌림목/돌파/돌파임박/추세전환 카드).
        거래대금 = 종가 × 거래량 근사. 한국 억/조원, 미국 $M/$B 단위.
        - scanner: volume_info() 헬퍼, 4개 모드 dict에 volume/turnover
        - index.html: fmtVolume/fmtTurnover + 카드 거래량 줄
v4.6.0  '돌파 임박(imminent)' 모드 신설.
        천장(피벗) 바로 아래 -5%~0%까지 올라왔지만 아직 안 뚫은 종목 포착.
        돌파 전날 미리 잡으려는 용도. 거래량 수축은 가점(필수 아님).
        점수 = 피벗근접 35 + 거래량수축 20 + VCP 20 + RS 15 + 200일선위 10.
        - scanner: analyze_imminent() + IMMINENT_CONFIG
        - app: imminent 모드 등록 + is_kr 전달
        - index.html: 🎯돌파임박 탭/카드/배지/설명
v4.5.1  '장중 돌파 ⚠️ 미확정' 배지 추가 (눌림목/추세전환 모드).
        한국 종목이 장중에 하락 추세선을 넘었지만 종가 확정 전인 상태를
        노란 배지로 경고. 가짜 돌파(위꼬리 후 마감 하락) 주의 유도.
        - scanner: is_kr_market_open(), select_pivot에 tl_break_intraday 반환
        - app: pullback/turnaround 모드에 is_kr 전달
v4.5.0  한국 종목(.KS/.KQ) 데이터 소스를 yfinance → 네이버(naver_kr)로 전환.
        yfinance 일봉의 한국 장중 지연(전일 종가 고정) 문제 해결.
        과거 일봉 + 장중 현재가 보정. 미국 종목은 yfinance 유지.
v4.4.2  (이전 버전)
"""
import asyncio
import hashlib
import hmac
import math
import os
import time
import uuid
import json as _json
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

import yfinance as yf
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from scanner import analyze, analyze_turnaround, analyze_leader, analyze_super, analyze_breakout, analyze_surge, analyze_imminent, analyze_boxbreak, analyze_inverse, analyze_breakdown, analyze_pattern, analyze_stage2, rs_score_stage2, analyze_ibd9_cheap, analyze_ibd9_full, analyze_jongga, rs_raw_score, to_rs_rank, climax_warning, inverse_score, price_frozen_check
from inverse_universe import inverse_universe
from sectors import get_sector
try:
    from us_sectors_auto import US_SECTORS_AUTO   # v4.44.0: 미국 자동 매핑 보완(커버리지 21%→97%)
except Exception as _e:
    print(f"[sectors] us_sectors_auto 미탑재 -> 자동보완 비활성: {_e}", flush=True)
    US_SECTORS_AUTO = {}
try:
    from kr_sectors_auto import KR_SECTORS_AUTO   # v5.18: 한국 자동 매핑 보완(네이버 업종별시세, 2685종목)
except Exception as _e:
    print(f"[sectors] kr_sectors_auto 미탑재 -> 자동보완 비활성: {_e}", flush=True)
    KR_SECTORS_AUTO = {}


def _sector_of(t: str) -> str:
    # v5.18 [기능개선] sectors.py의 SECTOR_MAP은 "AI 데이터센터 밸류체인"
    # 테마 위주로 손으로 큐레이션한 좁은 목록이라, 그 밖의 종목은 전부
    # "기타"로 빠지고 카드에 섹터가 아예 안 붙었음(사용자 리포트: SK하이닉스
    # 같은 극소수만 붙고 대부분 안 붙음). 미국은 이미 us_sectors_auto로
    # 보완돼 있었는데 한국은 보완이 없었던 게 원인 — us_sectors_auto.py와
    # 같은 패턴으로 kr_sectors_auto.py(네이버 업종별시세 79개 분류를
    # 시가총액 순위 페이지와 교차 매칭해 생성) 추가.
    s = get_sector(t)
    if s != "기타":
        return s
    if t.endswith((".KS", ".KQ")):
        return KR_SECTORS_AUTO.get(t.upper(), "기타")
    return US_SECTORS_AUTO.get(t.upper(), "기타")
from universe import get_universe, load_alerts
import scanner as scanner_mod
import naver_kr
import fundamentals as fundamentals_mod
import earnings as earnings_mod
import money_flow
import money_flow_report
import theme_map
import theme_lifecycle
import theme_reignition
import macro_calendar

app = FastAPI(title="눌림목 스캐너")


@app.middleware("http")
async def _no_cache_api(request, call_next):
    """v4.92 [버그수정] /api/* 응답에 캐시 방지 헤더가 전혀 없었음 — FastAPI
    기본값은 Cache-Control을 아예 안 붙이는데, 그러면 브라우저가 자체
    휴리스틱으로 GET 응답(특히 /api/scan)을 캐싱해버릴 수 있음. 그러면
    서버 로직을 아무리 고쳐도(v4.87~v4.91) 브라우저가 네트워크를 다시 안
    타서 화면이 안 바뀌는 것처럼 보임 — 실제로 사용자가 여러 버전을 거쳐도
    똑같은 숫자(등락률 0%, 시총 미달 종목)가 계속 보이던 문제의 유력한 원인.
    /api/ 전체에 무조건 no-store를 강제해 브라우저/중간 프록시 캐싱을 차단."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# v5.105: 비공개 전환 — 계좌 정보(포지션 탭)가 그대로 노출되던 상태를 막는다
# (사용자 지시). APP_PASSWORD 미설정이면 이 게이트 자체가 꺼져 기존처럼
# 전체 공개 — 온오프는 이 환경변수 하나로 결정(로컬 개발 시 방해 안 됨).
APP_PASSWORD = os.environ.get("APP_PASSWORD")
API_READ_TOKEN = os.environ.get("API_READ_TOKEN")
_SESSION_COOKIE = "pb_session"
_SESSION_MAX_AGE = 90 * 24 * 3600   # 90일

# sync_toss.py 전용 — 자체 SYNC_TOKEN으로 이미 보호되고(_verify_sync_token),
# 브라우저 쿠키를 못 들고 있는 로컬 스크립트라 세션/API_READ_TOKEN 게이트를
# 아예 안 거친다.
_SYNC_TOKEN_GATED_PATHS = {"/api/positions/sync", "/api/positions/sync_error"}

# 얼마냐봇(stock-alert 레포)이 폴링하는 읽기 전용 API — API_READ_TOKEN
# 헤더(X-Api-Read-Token)로 세션 쿠키 없이 통과. stock-alert/main.py의
# SCANNER_URL 호출부 전수 확인(2026-08-30 기준) 결과와 정확히 일치시킴 —
# 여기 없는 경로(예: POST /api/moneyflow/{market}/run)는 봇이 안 쓰므로
# 의도적으로 제외, 세션 쿠키 없이는 여전히 막힘.
_BOT_READ_EXACT_PATHS = {
    "/api/watch/positions", "/api/watch/pending", "/api/opening-surge",
    "/api/jongga/candidates", "/api/positions", "/api/market/gate", "/api/journal",
    # v5.125: 얼마냐봇이 재점화 알림을 보내려면 stock-alert(별도 레포) 쪽에도
    # 이 경로를 폴링하는 코드가 추가돼야 함 — 여기서는 데이터만 열어둠.
    "/api/reignition/confirmed",
}
_BOT_READ_PATH_PREFIXES = ("/api/dist/", "/api/ma/", "/api/pullback-signal/", "/api/vol/")


def _is_bot_read_path(method: str, path: str) -> bool:
    if method != "GET":
        return False
    if path in _BOT_READ_EXACT_PATHS:
        return True
    if path.startswith(_BOT_READ_PATH_PREFIXES):
        return True
    if path.startswith("/api/moneyflow/") and path.endswith("/summary"):
        return True
    return False


# v5.109/v5.111(사용자 지시): API_READ_TOKEN으로 통과 가능한 쓰기 경로 —
# 테마 매핑 수동 생성(POST /api/theme_map/{theme})과 매크로 캘린더 수동
# 재생성(POST /api/calendar/macro/run) 둘뿐. 둘 다 "스크립트/curl로
# 트리거해서 Claude 생성 결과를 바로 확인"하는 용도라 세션 로그인 없이도
# 되게 해달라는 요청 — 저널/포지션/손절 등 계정 데이터를 바꾸는 나머지
# 쓰기 API는 절대 여기 안 넣는다(세션 쿠키 필수 유지). 토큰 유출 시 피해도
# 각자 비용 가드로 제한됨(theme_map.py: DAILY_GENERATION_LIMIT=3/일,
# 매크로 캘린더: 성공 시 7일간 재생성 스킵 + 실패 시 24시간 재시도 스로틀).
# v5.123[버그수정]: GET /api/theme_map, GET /api/theme_map/{theme}가 이
# 목록에 없어서 curl로 생성 결과를 바로 조회할 수 없던 문제 — POST(생성)만
# 되고 GET(조회)은 세션 로그인 없이 막혀 있었음. 같은 용도(curl로 트리거·
# 확인)이므로 GET도 함께 허용.
_TOKEN_WRITABLE_EXACT_PATHS = {"/api/calendar/macro/run"}
_TOKEN_READABLE_EXACT_PATHS = {"/api/theme_map", "/api/calendar/macro/status"}
_TOKEN_READABLE_PATH_PREFIXES = ("/api/theme_map/",)


def _is_token_writable_path(method: str, path: str) -> bool:
    if method != "POST":
        return False
    return path.startswith("/api/theme_map/") or path in _TOKEN_WRITABLE_EXACT_PATHS


def _is_token_readable_path(method: str, path: str) -> bool:
    if method != "GET":
        return False
    return path in _TOKEN_READABLE_EXACT_PATHS or path.startswith(_TOKEN_READABLE_PATH_PREFIXES)


def _session_secret() -> bytes:
    # 서명 키를 비밀번호에서 파생 — 비번을 바꾸면(Railway 재배포) 이전에
    # 발급된 쿠키가 자동으로 전부 무효화되는 부수 효과(의도적, 별도 무효화
    # 로직 불필요).
    return hashlib.sha256((APP_PASSWORD or "").encode()).digest()


def _make_session_cookie() -> str:
    expiry = int(time.time()) + _SESSION_MAX_AGE
    payload = str(expiry)
    sig = hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_session_cookie(value) -> bool:
    if not value or "." not in value:
        return False
    payload, _, sig = value.partition(".")
    expected = hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(payload) > time.time()
    except ValueError:
        return False


_LOGIN_PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>눌림목 스캐너</title>
<style>
body{background:#0f1115;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
form{background:#1a1d24;padding:32px;border-radius:12px;width:260px;box-shadow:0 4px 20px rgba(0,0,0,.3)}
h1{font-size:16px;margin:0 0 20px;text-align:center;color:#9ca3af;font-weight:500}
input{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;border:1px solid #333;
background:#0f1115;color:#e5e7eb;font-size:15px}
button{width:100%;margin-top:12px;padding:12px;border:0;border-radius:8px;background:#3b82f6;
color:#fff;font-size:15px;cursor:pointer}
.err{color:#f87171;font-size:13px;text-align:center;margin:0 0 12px}
</style></head><body>
<form method="post" action="/login">
<h1>🔒 눌림목 스캐너</h1>
__ERROR__
<input type="password" name="password" placeholder="비밀번호" autofocus required>
<button type="submit">입장</button>
</form>
</body></html>"""


@app.get("/login")
async def login_page():
    if not APP_PASSWORD:
        return RedirectResponse("/", status_code=302)
    return Response(_LOGIN_PAGE_HTML.replace("__ERROR__", ""), media_type="text/html")


@app.post("/login")
async def login_submit(request: Request):
    if not APP_PASSWORD:
        return RedirectResponse("/", status_code=302)
    body = (await request.body()).decode("utf-8", errors="replace")
    pw = (parse_qs(body).get("password") or [""])[0]
    if not hmac.compare_digest(pw, APP_PASSWORD):
        html = _LOGIN_PAGE_HTML.replace("__ERROR__", '<p class="err">비밀번호가 틀렸습니다</p>')
        return Response(html, media_type="text/html", status_code=401)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(_SESSION_COOKIE, _make_session_cookie(), max_age=_SESSION_MAX_AGE,
                     httponly=True, secure=True, samesite="lax")
    return resp


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    """APP_PASSWORD 미설정이면 통과(게이트 꺼짐). 설정되면: /login과
    SYNC_TOKEN 자체보호 경로는 항상 통과, 유효한 세션 쿠키가 있으면 통과,
    얼마냐봇 폴링 경로 + 테마 매핑 수동생성(v5.109, 유일한 쓰기 예외) +
    테마 매핑/작업상태 조회(v5.123)는 API_READ_TOKEN 헤더로도 통과.
    나머지는 API면 401 JSON, 페이지면 /login으로 리다이렉트."""
    if not APP_PASSWORD:
        return await call_next(request)

    path = request.url.path
    if path == "/login" or path in _SYNC_TOKEN_GATED_PATHS:
        return await call_next(request)

    if _verify_session_cookie(request.cookies.get(_SESSION_COOKIE)):
        return await call_next(request)

    token_ok = bool(API_READ_TOKEN) and hmac.compare_digest(
        request.headers.get("X-Api-Read-Token", ""), API_READ_TOKEN)
    if token_ok and (_is_bot_read_path(request.method, path) or _is_token_writable_path(request.method, path)
                      or _is_token_readable_path(request.method, path)):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": "로그인 필요"}, status_code=401)
    return RedirectResponse("/login", status_code=302)


VERSION = "v5.127"
CACHE_TTL = 600              # 모드별 결과 캐시 (10분)
DATA_TTL = 600              # 시장별 원본 데이터 캐시 (10분) — 모드 전환 시 재호출 안 함
REUSE_TTL = int(os.environ.get("REUSE_TTL", "1800"))  # 증분 재사용 허용 시간(30분) — 이보다 오래된 캐시는 전체 재수집
MAX_CONCURRENT_FETCH = 6    # 데이터 소스 동시 호출 제한 (차단 방지)
US_BATCH_SIZE = 100         # 미국 종목 yf.download 배치 크기 (요청 수 1/N로 축소)
KR_MAX_CONCURRENT = int(os.environ.get("KR_MAX_CONCURRENT", "10"))  # 한국 네이버 동시 호출 (16→10 원복)
_cache: dict[str, dict] = {}
_data_cache: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=8)  # v4.39.x 원복(동시성 과다가 오히려 느려짐)
# v5.08: 실적(earnings) 조회 전용 격리 풀. yfinance income_stmt/quarterly_
# income_stmt는 내부적으로 timeout=30(요청당)이 여러 번 걸릴 수 있어 최악의
# 경우 종목 하나가 스레드를 60~90초 붙잡을 수 있음. asyncio.wait_for로
# "기다리는 걸 포기"해도 스레드 자체는 안 죽어서(파이썬 스레드는 강제 종료
# 불가) 계속 실행되며 풀 슬롯을 점유 — 공유 _executor(8개뿐)를 같이 쓰면
# 이게 쌓여 다른 모든 엔드포인트(스캔·펀더멘털·분산체크 등)까지 막힐 수
# 있음. 격리해서 최악의 경우에도 피해 범위를 실적 조회로만 한정.
# v5.17: RS70+ 전체를 훑게 되면서 종목 수가 확 늘어(수백~천여 개) 4워커로는
# 너무 오래 걸림 — 격리 풀이라 다른 엔드포인트에 영향 없이 늘려도 안전.
_earnings_executor = ThreadPoolExecutor(max_workers=6)


def _downcast(df):
    """가격 정규화 + float32 다운캐스트 (v4.48.1 / v4.50.4).
    ① Close가 NaN인 행 제거 — 모든 데이터 경로(야후 개별·배치·네이버)에서
       전일종가 자리에 결측이 끼어 유령 등락(+316% 등)이 나오는 걸 원천 차단.
    ② float32 다운캐스트 — 유니버스 3,570개 float64는 300MB+라 절반으로.
    """
    try:
        if "Close" in df.columns:
            df = df[df["Close"].notna()]
    except Exception:
        pass
    try:
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col in df.columns:
                df[col] = df[col].astype("float32")
    except Exception:
        pass
    return df


def _fetch(ticker: str):
    # 한국 종목(.KS/.KQ)은 네이버, 그 외는 yfinance
    if naver_kr.is_kr(ticker):
        try:
            df = naver_kr.fetch(ticker)
            if df is None or df.empty:
                return None
            return _downcast(df)
        except Exception:
            return None
    try:
        # v5.28: KR을 400일(≈269봉)→730일(≈485봉)로 늘리면서 미국도 같이
        # 2y로 맞춤 — 한쪽만 늘리면 시장 간 lookback 기준(52주 고저·RS
        # 12개월 등 공용 지표)이 어긋나게 됨.
        df = yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        return _downcast(df)
    except Exception:
        return None


def _fetch_us_batch(tickers: list[str]) -> dict:
    """미국 종목을 yf.download로 한 번에 받아 {ticker: df}로 분해.
    종목당 1요청 → 배치당 1요청으로 줄여 야후 부하/차단을 크게 낮춤.
    개별 history()와 동일하게 auto_adjust=True, 2년치 일봉(v5.28, KR과 동일
    lookback 기준으로 맞춤)."""
    out: dict = {}
    if not tickers:
        return out
    try:
        raw = yf.download(
            tickers, period="2y", interval="1d",
            auto_adjust=True, group_by="ticker",
            threads=True, progress=False,
        )
    except Exception:
        return out
    if raw is None or len(raw) == 0:
        return out

    # 단일 종목이면 컬럼이 평면(Open/High/...), 복수면 멀티인덱스(ticker, field)
    single = len(tickers) == 1
    for t in tickers:
        try:
            if single:
                df = raw.copy()
            else:
                if t not in raw.columns.get_level_values(0):
                    continue
                df = raw[t].copy()
            df = df.dropna(how="all")
            if df is None or df.empty or "Close" not in df.columns:
                continue
            # 전부 NaN인 종목(상장폐지/데이터없음) 제외
            if df["Close"].dropna().empty:
                continue
            # ── 근본 수정 (v4.50.4): Close가 NaN인 행 제거 ──
            # 야후 배치는 종목마다 거래일이 달라(거래정지·상장일 차이) 특정 종목의
            # 중간에 NaN 행이 생김. dropna(how="all")은 '전 컬럼 NaN'만 지우므로
            # Close만 NaN인 행이 남고, 그 행이 iloc[-2](전일종가) 자리에 오면
            # 유령 등락 발생(MQ +316%). Close 기준으로 결측행을 확실히 제거.
            df = df[df["Close"].notna()]
            if len(df) < 2:
                continue
            out[t] = _downcast(df)
        except Exception:
            continue
    return out


def _ret_pct(close, days):
    c = close.dropna()
    if len(c) < days + 1:
        return None
    past = float(c.iloc[-days - 1])
    return float(c.iloc[-1]) / past - 1 if past > 0 else None


def _compute_rs_ranks(data: dict, b_kospi: float, b_kosdaq: float, b_us: float):
    """종목 dict(df)로부터 RS 백분위(12개월, 지수 초과성과) + 3개월 단순수익률
    백분위를 계산. v5.71: rs_delta(20거래일 전 대비 rs_rank 변화) 산출을 위해
    이 계산을 그대로 다시 돌릴 수 있게 분리(원래 _fetch_market_data_inner
    본문에 인라인이었던 걸 승격) — data에 20봉 잘린 df를 넣으면 그 시점
    기준 RS가 재현된다. 벤치마크 상수(b_kospi/b_kosdaq/b_us)는 같은 시장
    안에서 전 종목에 동일하게 적용돼 백분위 순위엔 영향이 없으므로(균일한
    상수 차감은 상대순위 불변) 트렁케이션 시점과 무관하게 오늘 값을 그대로
    재사용해도 결과가 같다(scripts/measurements/harness.py의 US 벤치마크
    생략과 같은 근거)."""
    kr, us = {}, {}
    kr3, us3 = {}, {}
    for t, df in data.items():
        is_kr = t.endswith((".KS", ".KQ"))
        raw = rs_raw_score(df["Close"])
        if raw is not None:
            bench_score = (b_kospi if t.endswith(".KS") else b_kosdaq) if is_kr else b_us
            (kr if is_kr else us)[t] = raw - bench_score
        (kr3 if is_kr else us3)[t] = _ret_pct(df["Close"], 63)
    rs_ranks = {**to_rs_rank(kr), **to_rs_rank(us)}
    rank3 = {**to_rs_rank(kr3), **to_rs_rank(us3)}
    return rs_ranks, rank3


# ── 장 마감 후 디스크 캐시 ──────────────────────────────────
# 한국장·미국장 둘 다 마감하면 그날 일봉은 더 안 바뀜.
# 그날 데이터를 디스크(/data)에 저장하고, 다음 거래일 장 시작 전까지
# TTL 무시하고 재사용 → 마감 후 로딩이 즉시(야후/네이버 재호출 0).
KST = timezone(timedelta(hours=9))


def _market_session_key(market: str) -> str | None:
    """둘 다 마감했으면 '확정된 거래일 키'(YYYY-MM-DD)를 반환, 아니면 None.
    None이면 장중/애매한 시간 → 기존 10분 메모리 TTL로 동작.
    - 한국장 마감: 평일 KST 15:40 이후
    - 미국장 마감: KST 06:00 이후(서머타임 포함 안전)~ 한국장 시작(09:00) 전 종일
    market=all 은 둘 다 마감해야 확정. kr/us 단독은 해당 장만 따짐.
    """
    now = datetime.now(KST)
    wd = now.weekday()  # 월0 ~ 일6
    hm = now.hour * 60 + now.minute

    # 주말은 항상 '마감 확정'(데이터 안 바뀜) — 직전 거래일 날짜로 키 고정
    weekend = wd >= 5

    kr_closed = weekend or (wd <= 4 and hm >= 15 * 60 + 40)
    # 미국장 데이터는 KST 새벽에 확정. 06:00~다음 한국장 데이터 갱신 전까지 안정.
    us_closed = weekend or (hm >= 6 * 60)

    def needed():
        if market == "kr":
            return kr_closed
        if market == "us":
            return us_closed
        return kr_closed and us_closed

    if not needed():
        return None
    return now.strftime("%Y-%m-%d")


def _is_market_open_now(is_kr: bool) -> bool:
    """지금 그 시장이 '장중'인지 (일지 자동 손절 판정을 종가로만 하기 위한 게이트, v4.68).
    - 한국장: 평일 09:00~15:30 KST
    - 미국장: DST 계산 없이 넉넉히 22:00~06:30 KST를 '장중'으로 봄(실제 22:30~05:00/06:00
      보다 넓게 잡아 장중을 장마감으로 오판하는 일이 없게 안전 마진을 둠).
    주말은 항상 장마감."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    if is_kr:
        return 9 * 60 <= hm < 15 * 60 + 30
    return hm >= 22 * 60 or hm < 6 * 60 + 30


# ── 개장일 판정 (v5.99, 사용자 지시) ──────────────────────────────────
# 배경: 스케줄러가 주말/공휴일 구분 없이 매일 4분마다 돌아서, `_market_
# session_key()`가 "장 마감 후" 판정을 KST 요일/시각만으로 내리면(공휴일
# 무시) 실제로 장이 안 열린 날에도 "오늘자 daykey"가 생성돼 그 날짜로
# 리포트/스캔캐시가 저장되는 문제가 있었다(예: 금요일 데이터 그대로인데
# 토요일/평일공휴일 날짜로 라벨링). is_trading_day()는 주말+공휴일을
# 둘 다 걸러 이 문제를 막는다.
#
# 라이브러리 검토 결과 — 정적 리스트로 결정한 이유:
#   - `holidays`/`exchange_calendars` 둘 다 미설치. requirements.txt가
#     버전 고정이 전혀 없어(CLAUDE.md, v5.93 Railway 장애 조사에서 이미
#     확인된 리스크) 신규 의존성 추가 자체가 예측 못한 배포 실패 위험을
#     늘린다 — 이번 기능은 "날짜 가드"라 실패해도 치명적이지 않은데
#     반해, 의존성 설치 실패는 앱 전체 기동을 막을 수 있어 득실이 안 맞음.
#   - `pykrx`는 이미 의존성이지만 KRX 실시간 조회는 KRX_ID/KRX_PW 로그인이
#     필요해 이 프로젝트가 이미 v4.38.9에서 포기한 경로(universe.py 주석
#     참고) — 개장일 조회도 마찬가지로 막힐 게 뻔해 시도하지 않음.
#   - 결론: 2026~2027 정적 리스트로 시작. **매년 갱신 필요** —
#     `docs/trading_calendar.md`에 갱신 절차·출처 명시.
# 2026-08-29 WebSearch/WebFetch로 직접 조사(KRX: kstockguide.com·
# calendarlabs.com·market-holiday.com 교차 확인 + biggo.com/finance 뉴스로
# 6/3·7/17 특별휴장 재확인. NYSE: nyse.com 공식 페이지 + stockmarkethours.org
# 교차 확인, ICE 보도자료는 추출이 불완전해 참고만 함). 전부 datetime.weekday()
# 로 요일 재계산해 논리 일관성 검증 완료(대체공휴일 규칙과 정합).
# 상세 출처·갱신 절차는 docs/trading_calendar.md.
KRX_HOLIDAYS_2026 = {
    "2026-01-01",  # 신정
    "2026-02-16",  # 설날 연휴(전날)
    "2026-02-17",  # 설날
    "2026-02-18",  # 설날 연휴(다음날)
    "2026-03-02",  # 삼일절 대체공휴일(3/1 일요일)
    "2026-05-01",  # 근로자의 날
    "2026-05-05",  # 어린이날
    "2026-05-25",  # 부처님오신날 대체공휴일(5/24 일요일)
    "2026-06-03",  # 전국동시지방선거(임시공휴일)
    "2026-07-17",  # 제헌절(2026년 한시적 공휴일 복원 — 상시 휴장일 아님, 매년 재확인 필요)
    "2026-08-17",  # 광복절 대체공휴일(8/15 토요일)
    "2026-09-24",  # 추석 연휴(전날)
    "2026-09-25",  # 추석
    "2026-10-05",  # 개천절 대체공휴일(10/3 토요일)
    "2026-10-09",  # 한글날
    "2026-12-25",  # 크리스마스
    "2026-12-31",  # 연말 휴장일(매년 정확한 날짜 KRX 공지로 재확인 권장)
}
# 2027 KRX: 조사 시점(2026-08-29) 기준 KRX 공식 캘린더 미발표(공휴일 사이트도
# "2027년 상세 휴장일 안내는 아직 검색 결과에서 찾을 수 없음" — docs/trading_
# calendar.md 참고). 부정확한 추정치를 넣는 대신 비워두고 연도 자체를 "확인된
# 연도" 밖으로 둬서(KRX_CONFIRMED_YEARS) is_trading_day가 로그로 알려주게 함 —
# 2026년 12월경 KRX가 2027년 캘린더를 공표하면 그때 채운다.
KRX_HOLIDAYS_2027: set[str] = set()
KRX_CONFIRMED_YEARS = (2026,)

NYSE_HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King, Jr. Day
    "2026-02-16",  # Washington's Birthday
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day(observed, 7/4가 토요일)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving Day
    "2026-12-25",  # Christmas Day
}
NYSE_HOLIDAYS_2027 = {
    "2027-01-01",  # New Year's Day
    "2027-01-18",  # Martin Luther King, Jr. Day
    "2027-02-15",  # Washington's Birthday
    "2027-03-26",  # Good Friday
    "2027-05-31",  # Memorial Day
    "2027-06-18",  # Juneteenth(observed)
    "2027-07-05",  # Independence Day(observed, 7/4가 일요일)
    "2027-09-06",  # Labor Day
    "2027-11-25",  # Thanksgiving Day
    "2027-12-24",  # Christmas Day(observed, 12/25가 토요일)
}
NYSE_CONFIRMED_YEARS = (2026, 2027)   # NYSE는 official 발표가 이미 2027까지 나와 있음


def is_trading_day(market: str, date: "datetime | str | None" = None) -> bool:
    """market: 'kr' 또는 'us'. date: KST 달력 날짜(YYYY-MM-DD 문자열 또는
    datetime, 생략 시 오늘). 주말(토/일) + 정적 공휴일 목록 둘 다 걸러
    "실제로 그 시장이 열렸을 날짜인가"를 판정 — 리포트/스캔 캐시에 잘못된
    daykey가 찍히는 걸 막는 게 목적.

    v5.99 단순화: US도 KST 달력일 그대로 쓴다(실제 ET 환산 안 함) —
    `_market_session_key()` 등 이 앱의 기존 US 날짜 처리와 동일한
    근사치라 새 불일치를 만들지 않는다(완벽한 타임존 환산은 별도 개선
    대상, 지금 범위 아님).

    KR은 2026만, US는 2026~2027 확인됨(KRX가 2027 캘린더를 아직 미발표라
    KR만 범위가 좁음) — 확인 범위 밖 연도는 주말만 걸러지고 공휴일 체크는
    사실상 무력화된다. 조용히 새는 대신 로그를 남긴다(매년/공표시 갱신 필요,
    docs/trading_calendar.md)."""
    if date is None:
        date = datetime.now(KST)
    if isinstance(date, str):
        date_str = date
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return True   # 파싱 실패 — 과도한 차단보다 fail-open
    else:
        dt = date
        date_str = dt.strftime("%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    if market == "kr":
        holiday_set, confirmed_years = (KRX_HOLIDAYS_2026 | KRX_HOLIDAYS_2027), KRX_CONFIRMED_YEARS
    else:
        holiday_set, confirmed_years = (NYSE_HOLIDAYS_2026 | NYSE_HOLIDAYS_2027), NYSE_CONFIRMED_YEARS
    if dt.year not in confirmed_years:
        print(f"[is_trading_day] {market} {date_str} — 정적 휴장일 목록 확인 범위"
              f"({confirmed_years}) 밖, 주말만 체크됨(목록 갱신 필요, docs/trading_calendar.md)")
    return date_str not in holiday_set


def _disk_cache_dir() -> str:
    for d in (os.environ.get("JOURNAL_DIR"), "/data", os.path.dirname(__file__)):
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except OSError:
            continue
    return os.path.dirname(__file__)


def _universe_sig(market: str) -> str:
    # 유니버스 개수를 캐시 키에 넣어, 종목 수가 바뀌면 캐시가 자동 무효화되게 한다.
    # (예: 미국 239→1424 확장 시 옛 캐시를 안 읽고 새로 빌드)
    try:
        from universe import get_universe
        return f"u{len(get_universe(market))}"
    except Exception:
        return "u0"


# v4.93: rs4→rs5 캐시버스트. 오늘(장마감 후) 저장된 rs4 파일이 v4.87~v4.89의
# 버그(등락률 0%로 오염된 오늘 봉)를 그대로 물고 있어서, 그 뒤로 v4.90~v4.92를
# 아무리 배포해도 디스크 캐시 히트 경로가 naver_kr.fetch()를 아예 다시
# 안 불러 계속 오염된 값을 서빙하고 있었음(다음 거래일까지 캐시라 서버
# 재배포로도 안 없어짐 — /data가 영구 볼륨이라 재시작해도 파일이 남음).
# v5.28: rs5→rs6. 같은 이유로 다시 필요 — fetch 기간을 400일→730일(KR),
# 1y→2y(US)로 늘렸는데 daykey(오늘 날짜)만으로는 이 파라미터 변경을
# 못 알아채서, 배포 직전(옛 코드로) 오늘자 캐시가 이미 저장돼 있으면
# 재배포 후에도 짧은 기간짜리 옛 캐시를 그대로 서빙하게 됨.
# v5.31: _save_disk_cache의 정리 로직이 rs3/rs4는 무조건 삭제하면서 rs5는
# "오늘 날짜면 보존"(rs5가 그 시점의 '현재' 네임스페이스였을 때 로직)으로
# 남아있던 걸 발견 — rs6로 넘어온 뒤엔 rs5도 완전히 은퇴한 네임스페이스라
# 똑같이 무조건 삭제해야 맞다(동작엔 영향 없음 — load는 항상 이 상수를 통해
# 만든 경로만 읽으니 rs5 잔재가 다시 서빙될 일은 없고, 그냥 안 지워진
# 파일이 볼륨에 남는 하우스키핑 문제였음). rs3/rs4/rs5를 일일이 나열하는
# 대신 "현재 네임스페이스(_CACHE_NS)가 아니면 전부 삭제"로 일반화해서
# 다음 마이그레이션(rs7 등) 때 이 리스트를 또 손보지 않아도 되게 함.
_CACHE_NS = "rs6"   # 현재 디스크캐시 네임스페이스 — 스키마/기간 등이 바뀌어 캐시버스트가
                    # 필요하면 이 값만 올린다. _save_disk_cache가 자동으로 이전 네임스페이스를 정리한다.


def _disk_cache_path(market: str, daykey: str) -> str:
    # u{N} = 유니버스 크기 시그니처(위 _universe_sig 참고).
    return os.path.join(_disk_cache_dir(), f"datacache_{_CACHE_NS}_{market}_{_universe_sig(market)}_{daykey}.pkl")


def _load_disk_cache(market: str, daykey: str):
    import pickle
    path = _disk_cache_path(market, daykey)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _save_disk_cache(market: str, daykey: str, bundle: dict):
    import pickle
    path = _disk_cache_path(market, daykey)
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(bundle, f)
        os.replace(tmp, path)
        # 오래된 캐시 정리 — 은퇴한 네임스페이스(_CACHE_NS와 다름)는 날짜
        # 상관없이 전부 삭제, 현재 네임스페이스는 오늘(daykey) 아닌 것만
        # 삭제. 남기는 건 딱 하나(현재 네임스페이스 + 오늘)뿐이라 다음
        # 마이그레이션 때도 이 로직을 안 건드려도 된다(v5.31).
        d = _disk_cache_dir()
        for fn in os.listdir(d):
            if not (fn.startswith("datacache_") and fn.endswith(".pkl")):
                continue
            parts = fn.split("_")
            if len(parts) < 3:
                continue
            fn_ns, fn_market = parts[1], parts[2]
            if fn_market != market:
                continue
            keep = fn_ns == _CACHE_NS and fn.endswith(f"_{daykey}.pkl")
            if not keep:
                try:
                    os.remove(os.path.join(d, fn))
                except OSError:
                    pass
    except Exception:
        pass
# ────────────────────────────────────────────────────────────


# RS 벤치마크 지수 일봉 종가 (상대강도 계산용)
# 미국=나스닥종합(^IXIC), 한국=코스피/코스닥. 종목 RS에서 지수 RS를 빼
# "지수 대비 초과성과"를 만들어 universe 편향을 제거한다.
def _benchmark_close(market_kind: str):
    """market_kind: 'us'|'kospi'|'kosdaq' → 지수 일봉 Close 시리즈(약 1년) 반환."""
    try:
        if market_kind == "us":
            df = yf.Ticker("^IXIC").history(period="1y", interval="1d", auto_adjust=False)
            return df["Close"].dropna() if df is not None and not df.empty else None
        # 한국 지수: 네이버
        code = "KOSPI" if market_kind == "kospi" else "KOSDAQ"
        hist = naver_kr.fetch_index_history(code, days=400)
        if hist is None:
            return None
        # fetch_index_history 반환형이 DataFrame(Close 포함)이라고 가정, 아니면 Series
        try:
            return hist["Close"].dropna()
        except Exception:
            import pandas as pd
            return pd.Series(hist).dropna()
    except Exception:
        return None


def _benchmark_rs_scores() -> dict:
    """벤치마크 지수들의 rs_raw_score를 미리 계산해 dict로.
    {'us': float, 'kospi': float, 'kosdaq': float} (실패한 건 0.0)."""
    out = {}
    for kind in ("us", "kospi", "kosdaq"):
        c = _benchmark_close(kind)
        s = rs_raw_score(c) if c is not None else None
        out[kind] = s if s is not None else 0.0
    return out


# 시장별 싱글플라이트 락 (v4.48.1): 콜드 스캔(2~4분) 중 재시도/다른 탭 요청이
# 전체 유니버스 페치를 중복 실행 → 메모리 2~3배 → Railway OOM → 컨테이너 재시작
# → "스캔 반복 실패" 루프의 원인. 락 안에서 캐시를 재확인하므로 뒤늦게 진입한
# 요청은 페치 없이 방금 채워진 캐시를 그대로 받는다.
_market_fetch_locks: dict = {}
_market_refreshing: dict = {}   # v4.86: cache_key -> True인 동안 백그라운드 갱신 진행중


async def _fetch_market_data(market: str, wait_for_fresh: bool = False) -> dict | None:
    """시장 단위로 종목 일봉 + RS 계산. 모드와 무관하므로 시장별로 캐시해 재사용.
    여기서만 네이버/야후를 호출한다 (모드 전환 시 재호출 안 함).

    v4.86 [버그수정] "첫 스캔 대부분 실패, 로딩돼도 10분" 증상의 근본원인.
        [문제] 지금까지 모든 호출이 시장별 공유 락(_market_fetch_locks)을 무조건
               기다렸음. v4.73에서 종목별 실제 fetch 시각을 추적하도록 고친 뒤로
               실제 재수집이 예전보다 훨씬 자주(종목당 30분마다) 일어나게 됐는데,
               그 재수집 한 번이 네이버 레이트리밋 등으로 오래 걸리면(수분~10분+)
               그동안 락을 쥐고 있어 다른 모든 요청(캐시가 이미 신선해도!)이
               전부 그 뒤에서 대기하게 됨.
        [해결] wait_for_fresh=False(기본값, 사용자 요청 전부)면: 캐시가 있으면
               신선도와 무관하게 즉시 반환하고, 오래됐으면 백그라운드 태스크로
               갱신만 걸어둔 뒤 이번 요청은 기다리지 않는다(stale-while-
               revalidate). 캐시가 아예 없을 때(콜드 스타트)만 실제로 기다린다.
               wait_for_fresh=True(스케줄러 워밍 전용)는 기존처럼 락을 잡고
               진짜로 최신 데이터가 될 때까지 기다린다 — 워밍의 목적 자체가
               '미리 실제로 받아두기'라 여기서까지 스킵하면 캐시가 영영 안 됨."""
    cache_key = f"data:{market}"
    if wait_for_fresh:
        _lock = _market_fetch_locks.setdefault(cache_key, asyncio.Lock())
        async with _lock:
            return await _fetch_market_data_inner(market, cache_key)

    daykey = _market_session_key(market)
    mem = _data_cache.get(cache_key)
    if daykey and mem and mem.get("daykey") == daykey:
        return mem
    if not mem and daykey:
        disk = _load_disk_cache(market, daykey)
        if disk:
            _data_cache[cache_key] = disk
            return disk
    if mem:
        fresh = (not daykey) and (time.time() - mem.get("ts", 0) < DATA_TTL)
        if not fresh and not _market_refreshing.get(cache_key):
            _market_refreshing[cache_key] = True
            asyncio.create_task(_refresh_market_data_bg(market, cache_key))
        return mem
    # 캐시가 아예 없음(콜드 스타트).
    # v5.12 [버그수정] "재시도 시간을 아무리 늘려도 여전히 실패" — 사용자가
    # "횟수가 중요한 게 아니라 오래 기다려야 한다, 계속 새로고침하면 안 된다"
    # 고 정확히 짚어줌. 원인: 이 분기가 지금까지 "이번 요청 하나가" 콜드
    # 스캔 전체(수분~8분+)를 끝날 때까지 그대로 물고 있었음 — 그동안 클라
    # 이언트 fetch() 하나가 몇 분씩 연결을 붙들고 있어야 하는데, 중간의
    # 어떤 구간(브라우저·Railway 엣지 등)이든 하나만 그 사이 끊어버리면
    # 실패로 보임. 프론트의 재시도 "횟수"를 늘려도, 매번 새 요청이 또 몇
    # 분짜리로 다시 걸리는 구조라 근본 해결이 안 됐던 것.
    # [해결] 이 요청 자체는 더 이상 기다리지 않는다. 백그라운드 태스크만
    # 걸어두고 즉시 None을 반환 → 호출부(run_scan 등)가 "준비 중" 응답을
    # 내려보내고, 프론트는 가벼운 폴링(각 요청이 즉시 끝남)으로 기다린다.
    if not _market_refreshing.get(cache_key):
        _market_refreshing[cache_key] = True
        asyncio.create_task(_refresh_market_data_bg(market, cache_key))
    return None


async def _refresh_market_data_bg(market: str, cache_key: str):
    """캐시는 있지만 오래됐을 때 백그라운드에서 실제로 갱신. 사용자 요청은 안 기다림."""
    _lock = _market_fetch_locks.setdefault(cache_key, asyncio.Lock())
    try:
        async with _lock:
            await _fetch_market_data_inner(market, cache_key)
    except Exception as e:
        print(f"[bg-refresh] {market} failed: {e}")
    finally:
        _market_refreshing[cache_key] = False


async def _fetch_market_data_inner(market: str, cache_key: str) -> dict:

    # 1) 장 마감 후면 디스크 캐시 우선 — 다음 거래일까지 재호출 0
    daykey = _market_session_key(market)
    if daykey:
        mem = _data_cache.get(cache_key)
        if mem and mem.get("daykey") == daykey:
            return mem  # 메모리에 이미 그날치 있음
        disk = _load_disk_cache(market, daykey)
        if disk:
            _data_cache[cache_key] = disk  # 메모리로 승격
            return disk

    # 2) 장중/애매한 시간 → 기존 10분 메모리 TTL
    cached = _data_cache.get(cache_key)
    if cached and not daykey and time.time() - cached["ts"] < DATA_TTL:
        return cached

    universe = get_universe(market)
    # v4.91: 시총 1000억원 미만 국장 종목 제외 (예: 시총 700억짜리가 돌파임박에
    # 뜨는 문제). 허용목록이 아직 준비 안 됐으면(서버 갓 재시작 등) 필터 없이
    # 통과 — fail-open, 백그라운드 채워지면 다음 스캔부터 적용됨.
    _mcap_allowed = _get_mcap_allowed()
    if _mcap_allowed:
        universe = {t: n for t, n in universe.items()
                    if not naver_kr.is_kr(t) or t in _mcap_allowed}
    loop = asyncio.get_event_loop()
    tickers = list(universe.keys())

    # ── 증분 다운로드(C안): 직전 캐시에 있는 종목 df는 재사용, 새로 편입된 종목만 받는다.
    # [v4.43.2] 신선도 검사 추가: 직전 캐시가 REUSE_TTL(기본 30분) 이내일 때만 재사용.
    # 이전엔 나이 확인 없이 무조건 재사용해 서버가 살아있는 동안 가격이 며칠 전에
    # 얼어붙는 버그가 있었음(기가비스 6/26 종가 고정 사례).
    # [v4.73 수정] v4.43.2의 신선도 검사는 "번들이 마지막으로 재구성된 시각"(ts)을
    # 봤는데, ts는 재사용만 하고 실제 fetch가 하나도 없어도 매 사이클 now()로
    # 갱신됨. 백그라운드 워밍이 4~8분마다 도는데 REUSE_TTL은 30분이라, 재사용
    # 조건이 사실상 영원히 참이 되어 장이 열린 뒤 첫 fetch 이후로는 종목별
    # 실제 가격이 하루 종일 다시 안 받아지는 문제가 있었음(기가비스 버그가
    # "며칠"에서 "장중 내내"로 형태만 바뀌어 재발). 종목별로 실제 fetch 시각을
    # 따로 추적(data_ts)해 번들 재구성 여부와 무관하게 30분마다 실제 재요청되게 함.
    prev = _data_cache.get(cache_key)
    prev_data = prev.get("data", {}) if isinstance(prev, dict) else {}
    prev_data_ts = prev.get("data_ts", {}) if isinstance(prev, dict) else {}
    now_ts = time.time()
    reused: dict = {}
    data_ts: dict = {}
    fetch_targets = []
    for t in tickers:
        fetched_at = prev_data_ts.get(t, 0)
        if t in prev_data and prev_data[t] is not None and now_ts - fetched_at < REUSE_TTL:
            reused[t] = prev_data[t]      # 최근 REUSE_TTL 이내에 실제로 받은 데이터 재사용
            data_ts[t] = fetched_at
        else:
            fetch_targets.append(t)        # 없거나, 실제 fetch가 오래돼 다시 받아야 함

    kr_tickers = [t for t in fetch_targets if naver_kr.is_kr(t)]
    us_tickers = [t for t in fetch_targets if not naver_kr.is_kr(t)]

    data: dict = dict(reused)   # 재사용분으로 시작, 아래에서 새 종목만 채움

    # ── 한국: 네이버 개별 호출 (배치 API 없음), 동시성 제한 ──
    _t_kr = time.time()
    if kr_tickers:
        sem = asyncio.Semaphore(KR_MAX_CONCURRENT)

        async def fetch_kr(t):
            async with sem:
                return await loop.run_in_executor(_executor, _fetch, t)

        kr_dfs = await asyncio.gather(*[fetch_kr(t) for t in kr_tickers])
        for t, df in zip(kr_tickers, kr_dfs):
            if df is not None:
                data[t] = df
                data_ts[t] = time.time()
    _dur_kr = time.time() - _t_kr

    # ── 미국: yf.download 배치 (100개씩) → 요청 수 1/100로 축소 ──
    _t_us = time.time()
    if us_tickers:
        batches = [us_tickers[i:i + US_BATCH_SIZE]
                   for i in range(0, len(us_tickers), US_BATCH_SIZE)]

        async def fetch_us_batch(batch):
            return await loop.run_in_executor(_executor, _fetch_us_batch, batch)

        # 배치는 동시에 너무 많이 띄우지 않게 2개씩 (각 배치가 내부 threads=True)
        for i in range(0, len(batches), 2):
            chunk = batches[i:i + 2]
            results = await asyncio.gather(*[fetch_us_batch(b) for b in chunk])
            for r in results:
                data.update(r)
                for t in r:
                    data_ts[t] = time.time()
    _dur_us = time.time() - _t_us

    # ── RS 등급: "지수 대비 초과성과" 기반 ──
    _t_rs = time.time()
    # 각 종목 raw score에서 해당 시장 지수의 raw score를 빼서 universe 편향 제거.
    # (지수를 이긴 정도 → 백분위). 지수 fetch는 블로킹이라 executor에서.
    bench = await loop.run_in_executor(_executor, _benchmark_rs_scores)
    b_us = bench.get("us", 0.0)
    b_kospi = bench.get("kospi", 0.0)
    b_kosdaq = bench.get("kosdaq", 0.0)

    rs_ranks, rank3 = _compute_rs_ranks(data, b_kospi, b_kosdaq, b_us)
    kr12, us12 = {}, {}
    for t, df in data.items():
        is_kr = t.endswith((".KS", ".KQ"))
        (kr12 if is_kr else us12)[t] = _ret_pct(df["Close"], 252)
    rank12 = {**to_rs_rank(kr12), **to_rs_rank(us12)}
    rs_moms = {t: rank3[t] - rank12[t] for t in data if t in rank3 and t in rank12}
    rs3_ranks = rank3   # v5.71: rs_3m 필드로 그대로 노출 (게이트 변형 E — 3개월 RS)

    # ── rs_delta: 20거래일 전 대비 RS 랭크 변화 (v5.71, 게이트 변형 E) ──
    # scripts/measurements/2026-08-23_reject_tracer_rs_variants.py에서 검증된
    # 정의 그대로 이식 — 같은 트렁케이션(끝에서 20봉 제거)으로 RS를 재계산해
    # 오늘 랭크와 비교. data는 이미 메모리에 있어 네트워크 재호출 없음.
    RS_DELTA_LOOKBACK = 20
    data_20ago = {t: df.iloc[:-RS_DELTA_LOOKBACK] for t, df in data.items()
                  if len(df) > RS_DELTA_LOOKBACK}
    rs_ranks_20ago, _ = _compute_rs_ranks(data_20ago, b_kospi, b_kosdaq, b_us)
    rs_deltas = {t: rs_ranks[t] - rs_ranks_20ago[t] for t in rs_ranks if t in rs_ranks_20ago}
    _dur_rs = time.time() - _t_rs

    # ── 속도 진단 로그 — 어느 단계가 느린지 Railway 로그로 확인 ──
    _timing = {
        "market": market,
        "n_total": len(tickers),
        "n_reused": len(reused),
        "n_fetched_kr": len(kr_tickers),
        "n_fetched_us": len(us_tickers),
        "kr_sec": round(_dur_kr, 1),
        "us_sec": round(_dur_us, 1),
        "rs_sec": round(_dur_rs, 1),
    }
    print(f"[TIMING] {_timing}", flush=True)

    bundle = {
        "universe": universe,
        "data": data,
        "data_ts": data_ts,
        "rs_ranks": rs_ranks,
        "rs_moms": rs_moms,
        "rs3_ranks": rs3_ranks,   # v5.71: 3개월 RS 백분위 (게이트 변형 E)
        "rs_deltas": rs_deltas,   # v5.71: 20거래일 전 대비 RS 랭크 변화
        "ts": time.time(),
        "daykey": daykey,
        "timing": _timing,
    }
    _data_cache[cache_key] = bundle
    # 장 마감 후 fetch였다면 디스크에 저장 → 다음 거래일까지 재사용
    if daykey:
        _save_disk_cache(market, daykey, bundle)
    return bundle


# ── Stage 2 트렌드 템플릿 스캐너 (v5.02, 사용자 스펙) ──
# 적용 순서: 유동성 → RS백분위 → Stage2템플릿 → 거래량수축/MA수렴 → 티어링.
# 일반 run_scan()의 fn 디스패치(모든 모드가 유니버스 전체 RS로 판정)와 달리,
# 이 모드는 "유동성 생존자 안에서만" RS 백분위를 다시 매겨야 해서(사용자
# 스펙의 적용 순서) 전용 파이프라인으로 분리. 결과 dict 모양은 run_scan()의
# 반환 형태와 맞춰서 프론트 load()가 그대로 재사용되게 한다.
STAGE2_LIQUIDITY_MIN_EOK = 20   # 일평균 거래대금(20일) 20억원 이상만 (한국 전용)
STAGE2_RS_PCTILE_MIN = 70


# ── 🇰🇷 종가베팅 (v5.97) ──────────────────────────────────────────
# T일 종가매수→T+1일 시가매도. 근거: docs/kr_jongga_betting_backtest.md
# "후속 — 사전 등록 재설계" 절, 조합 A 채택(n=276, 비용차감후 평균
# +1.22%, base 대비 z=4.28). 조건 자체는 scanner.analyze_jongga()에
# 그대로 있음 — 여기(app.py)는 KR 전종목 거래대금 순위(cross-sectional,
# RS랭크와 같은 이유로 analyze_jongga() 밖에서 계산)만 준비해서 넘긴다.
JONGGA_BACKTEST_NOTE = ("이 조건 과거 평균 익일갭 +1.22% (n=276, 비용차감후, "
                          "왕복0.3% 가정) — z=4.28 (docs/kr_jongga_betting_backtest.md)")
JONGGA_SELL_RULE = "익일 시초가~9:05 전량 매도"


def _jongga_session_state(now_kst: datetime) -> dict:
    """종가베팅 탭 상단 안내용 시간대 상태 — 실제 스캔 로직과 무관, 순수
    안내 문구용(사용자 지시 4번). 평일 09:00~14:40=대기, 14:40~15:30(동시
    호가 포함)=활성, 그 외=장종료. 주말은 항상 장종료로 취급."""
    wd = now_kst.weekday()
    hm = now_kst.hour * 60 + now_kst.minute
    if wd >= 5:
        return {"state": "after", "label": "오늘 장 종료 — 내일 후보는 14:40에"}
    if hm < 14 * 60 + 40:
        return {"state": "before", "label": "오늘 후보는 14:40~15:00 사이에 갱신됩니다"}
    if hm < 15 * 60 + 30:
        return {"state": "active", "label": "⏰ 오늘 15:20 동시호가 전 진입용"}
    return {"state": "after", "label": "오늘 장 종료 — 내일 후보는 14:40에"}


async def _run_scan_jongga(bundle: dict) -> dict:
    universe = bundle["universe"]
    data = bundle["data"]
    kr_data = {t: df for t, df in data.items() if naver_kr.is_kr(t)}

    diag = {"kr_universe": sum(1 for t in universe if naver_kr.is_kr(t)),
            "kr_fetched": len(kr_data), "kr_hits": 0}

    # ── 거래대금(종가×거래량) 순위 — RS랭크와 동일하게 cross-sectional이라
    # analyze_jongga() 밖에서 미리 계산(scripts/measurements/2026-08-29_kr_
    # jongga_betting_backtest_extended.py의 turnover_rank_at()과 동일 정의) ──
    turnovers = {}
    for t, df in kr_data.items():
        c, v = df.get("Close"), df.get("Volume")
        if c is None or v is None or len(c) < 1 or len(v) < 1:
            continue
        try:
            turnovers[t] = float(c.iloc[-1]) * float(v.iloc[-1])
        except Exception:
            continue
    ranked = sorted(turnovers.items(), key=lambda kv: kv[1], reverse=True)
    turnover_rank = {t: i + 1 for i, (t, _) in enumerate(ranked)}

    hits = []
    for t, df in kr_data.items():
        r = analyze_jongga(df, turnover_rank=turnover_rank.get(t))
        if r is None:
            continue
        hits.append({
            "ticker": t, "name": universe.get(t, t), "market": "KR",
            "sector": _sector_of(t), "backtest_note": JONGGA_BACKTEST_NOTE,
            "sell_rule": JONGGA_SELL_RULE,
            **r,
        })
        diag["kr_hits"] += 1
    hits.sort(key=lambda x: x.get("turnover_rank", 9999))

    now_kst = datetime.now(KST)
    session = _jongga_session_state(now_kst)

    return {
        "version": VERSION, "market": "kr", "mode": "jongga",
        "scanned": len(universe), "fetched": len(data), "diag": diag,
        "hits": hits, "warn_count": 0,
        "backtest_note": JONGGA_BACKTEST_NOTE, "sell_rule": JONGGA_SELL_RULE,
        "session_state": session["state"], "session_label": session["label"],
        "timing": bundle.get("timing"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(),
    }


# ── 🇰🇷 종가베팅 포워드 트래킹 (v5.98) ──────────────────────────────
# 후보의 실제 성과를 자동 누적 — 백테스트(+1.22%)와 실전 결과를 계속
# 대조하기 위함(사용자 지시). 저장 파일 경로(JONGGA_FORWARD_PATH)는
# 아래쪽 _resolve_persistent_path() 정의 직후에 잡는다(모듈 하단 참고,
# ALERTS_USER_PATH 등과 같은 위치) — 이 함수들은 호출 시점에만
# JONGGA_FORWARD_PATH를 참조하므로 정의 순서는 무관.
#
# 데이터 구조: {"YYYY-MM-DD": {티커: {레코드}}}. 레코드 필드:
#   snapshot_price/snapshot_ts — 14:40~15:00 스냅샷 시점 가격
#   close_price/eod_recorded  — 당일 장마감 확정 종가(15:40+ 채움)
#   next_open_price/next_open_date/resolved — 익일 시가 확정(장 열리면 채움)
#   gap_snapshot_pct — (익일시가/스냅샷가 - 1 - 비용0.3%)*100  ("스냅샷 기준" 성과)
#   gap_close_pct    — (익일시가/확정종가 - 1 - 비용0.3%)*100  ("종가 기준" 성과)
# 스냅샷가↔확정종가가 다를 수 있어(14:40~15:30 변동) 둘 다 남기고 분리
# 계산한다 — 실전 진입가는 그 사이 어딘가라 어느 한쪽만 쓰면 왜곡된다.
JONGGA_FORWARD_COST = 0.003  # 왕복 수수료+슬리피지 0.3% — 백테스트와 동일 가정


def _load_jongga_forward() -> dict:
    if os.path.exists(JONGGA_FORWARD_PATH):
        try:
            with open(JONGGA_FORWARD_PATH, encoding="utf-8") as f:
                data = _json.load(f)
                return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}
    return {}


def _save_jongga_forward(data: dict):
    try:
        tmp = JONGGA_FORWARD_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, JONGGA_FORWARD_PATH)
    except OSError as e:
        print(f"[jongga-forward] 저장 실패: {e}")


def _record_jongga_snapshot(date_str: str, hits: list):
    """14:40~15:00 스냅샷 확정 시(스케줄러 1회 실행분에서만 호출 —
    사용자가 탭을 열 때마다 도는 라이브 스캔에서는 호출 안 함, 중복/
    조기기록 방지) 오늘자 후보를 전부 기록."""
    fwd = _load_jongga_forward()
    day_rec = fwd.setdefault(date_str, {})
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    changed = False
    for h in hits:
        t = h.get("ticker")
        if not t or t in day_rec:
            continue
        day_rec[t] = {
            "ticker": t, "name": h.get("name", t),
            "snapshot_price": h.get("close"), "snapshot_ts": now_str,
            "turnover_rank": h.get("turnover_rank"), "change_pct_snapshot": h.get("change_pct"),
            "vol_mult": h.get("vol_mult"), "off_high_pct": h.get("off_high_pct"), "wick_pct": h.get("wick_pct"),
            "close_price": None, "eod_recorded": False,
            "next_open_price": None, "next_open_date": None, "resolved": False,
            "gap_snapshot_pct": None, "gap_close_pct": None,
        }
        changed = True
    if changed:
        _save_jongga_forward(fwd)


def _record_jongga_eod(date_str: str, kr_data: dict):
    """장 마감 후(daykey 확정 시점, _warm_market의 '장마감 후' 분기)
    오늘자 후보들의 확정 종가를 채운다."""
    fwd = _load_jongga_forward()
    day_rec = fwd.get(date_str)
    if not day_rec:
        return
    changed = False
    for t, rec in day_rec.items():
        if rec.get("eod_recorded"):
            continue
        df = kr_data.get(t)
        if df is None or df.empty:
            continue
        try:
            rec["close_price"] = round(float(df["Close"].iloc[-1]), 2)
            rec["eod_recorded"] = True
            changed = True
        except Exception:
            continue
    if changed:
        _save_jongga_forward(fwd)


def _resolve_jongga_gaps(kr_data: dict):
    """장중 워밍(_warm_market 장중 분기, 8분 주기) 때마다 호출 — 아직
    안 풀린 과거 날짜 레코드 중 '다음 거래일' 데이터가 이미 들어온
    것이 있으면 시가를 확정하고 갭을 계산한다. 오늘 자기 자신의 스냅샷
    레코드는 date_str < today 조건으로 원천 배제(당일 데이터로 당일
    후보를 잘못 확정하는 것 방지)."""
    fwd = _load_jongga_forward()
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    changed = False
    for date_str, day_rec in fwd.items():
        if date_str >= today_str:
            continue
        for t, rec in day_rec.items():
            if rec.get("resolved"):
                continue
            df = kr_data.get(t)
            if df is None or df.empty:
                continue
            try:
                last_date = str(df.index[-1].date())
            except Exception:
                continue
            if last_date <= date_str:
                continue   # 아직 다음 거래일 데이터가 안 들어옴
            try:
                open_t1 = float(df["Open"].iloc[-1])
            except Exception:
                continue
            rec["next_open_price"] = round(open_t1, 2)
            rec["next_open_date"] = last_date
            snap = rec.get("snapshot_price")
            close_ = rec.get("close_price")
            if snap:
                rec["gap_snapshot_pct"] = round((open_t1 / snap - 1 - JONGGA_FORWARD_COST) * 100, 2)
            if close_:
                rec["gap_close_pct"] = round((open_t1 / close_ - 1 - JONGGA_FORWARD_COST) * 100, 2)
            rec["resolved"] = True
            changed = True
    if changed:
        _save_jongga_forward(fwd)


def _jongga_forward_stats() -> dict:
    """/api/jongga/forward용 누적 통계. 스냅샷기준/종가기준 각각 계산."""
    fwd = _load_jongga_forward()
    resolved = []
    for date_str, day_rec in fwd.items():
        for t, rec in day_rec.items():
            if rec.get("resolved"):
                resolved.append({**rec, "date": date_str})
    resolved.sort(key=lambda r: (r["date"], r.get("ticker", "")), reverse=True)

    def _agg(field):
        vals = [r[field] for r in resolved if r.get(field) is not None]
        n = len(vals)
        if n == 0:
            return {"n": 0, "mean_gap_pct": None, "up_rate": None}
        return {
            "n": n,
            "mean_gap_pct": round(sum(vals) / n, 3),
            "up_rate": round(sum(1 for v in vals if v > 0) / n * 100, 1),
        }

    return {
        "total_resolved": len(resolved),
        "snapshot_basis": _agg("gap_snapshot_pct"),
        "close_basis": _agg("gap_close_pct"),
        "backtest_reference": {"mean_gap_pct": 1.22, "n": 276, "z": 4.28,
                                "source": "docs/kr_jongga_betting_backtest.md"},
        "recent": resolved[:30],
    }


# ── 🔁 전(前) 테마 리더 재점화 워치리스트 (v5.125, 사용자 지시) ──────────
# docs/kr_theme_leader_reignition.md 채택 결과(D0 리더 재점화 51.8%,
# 대조군 대비 z>=1.96 두 건 모두 유의, 확인진입 EV=+0.755R)를 실시간
# 감시로 옮긴다. theme_reignition.py가 순수 계산(창 판정·피벗·확인·응축
# 배지)을, 여기(app.py)는 저장/스케줄링/노출만 담당 — money_flow.py/
# theme_lifecycle.py와 같은 책임 분리 원칙. 저장 파일 경로는 아래쪽
# _resolve_persistent_path() 정의 직후에 잡는다(JONGGA_FORWARD_PATH와
# 같은 위치, 정의 순서 무관 — 호출 시점에만 참조).
#
# 데이터 구조: {"테마|티커|D0날짜": {레코드}}. 레코드 상태 전이:
#   watching(창 안, 확인진입 대기) → confirmed(표준 돌파 확인, 포워드
#   추적 시작) 또는 expired(창 만료/테마 매핑 소실, 자동 해제).
# 포워드 추적은 harness.race()(측정 스크립트 전용)를 프로덕션에 들여오지
# 않고 직접 구현 — 목표(target) 사전정의 없이 손절 이탈 또는 60봉 상한
# 시점의 시가평가 R로 확정한다(백테스트 confirm_entry_race의 target/stop
# 경주와는 다른, 더 단순한 mark-to-market 방식 — 방향성 엣지 대조용으로는
# 충분하지만 완전히 같은 방법론은 아님, 비교 시 참고).


def _load_reignition_watch() -> dict:
    if os.path.exists(REIGNITION_WATCH_PATH):
        try:
            with open(REIGNITION_WATCH_PATH, encoding="utf-8") as f:
                data = _json.load(f)
                return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}
    return {}


def _save_reignition_watch(data: dict):
    try:
        tmp = REIGNITION_WATCH_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, REIGNITION_WATCH_PATH)
    except OSError as e:
        print(f"[reignition] 저장 실패: {e}")


def _reignition_compute_candidates(bundle: dict) -> list:
    """theme_map 전 테마 순회 + RS 상위 노이즈가드 캡까지 포함한 오늘자
    후보 목록. 테마별 compute_theme_series+find_cycles라 무거운 편 —
    executor에서 호출할 것(_refresh_reignition_watch가 그렇게 함)."""
    entries = {name: theme_map.get(name) for name in theme_map.list_all().keys()}
    entries = {name: e for name, e in entries.items() if e and e.get("stocks")}
    if not entries:
        return []
    market_turnover = theme_lifecycle.market_daily_turnover(bundle["data"])
    candidates = theme_reignition.find_watch_candidates(entries, bundle["data"], market_turnover)
    rs_ranks = bundle.get("rs_ranks", {})
    candidates.sort(key=lambda c: -(rs_ranks.get(c["ticker"]) or 0))
    for i, c in enumerate(candidates):
        # 노이즈 가드(사용자 지시: 20개+ 동시 창내 리더 시 RS 상위 우선) —
        # 상위 MAX_ACTIVE_WATCHES만 확인진입 체크·봇알림 대상. 나머지도
        # 계속 표시는 되지만(창 안에 있다는 사실 자체는 유효 정보) 확인
        # 체크에서는 제외해 봇 알림 폭주를 막는다.
        c["alert_suppressed"] = i >= theme_reignition.MAX_ACTIVE_WATCHES
    return candidates


async def _refresh_reignition_watch(bundle: dict):
    """일 1회(_warm_market의 KR 장마감 후 분기에서 호출) — 창 진입/이탈
    갱신, 확인진입 체크, 포워드 R 갱신까지 한 번에 처리."""
    loop = asyncio.get_event_loop()
    candidates = await loop.run_in_executor(_executor, _reignition_compute_candidates, bundle)
    store = _load_reignition_watch()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    data = bundle["data"]
    current_keys = set()

    for c in candidates:
        key = f"{c['theme']}|{c['ticker']}|{c['d0_date']}"
        current_keys.add(key)
        rec = store.get(key)
        if rec is None:
            rec = {"theme": c["theme"], "ticker": c["ticker"], "name": c["name"],
                   "d0_date": c["d0_date"], "first_seen": today, "status": "watching",
                   "compression": None, "confirm": None, "forward": None}
            store[key] = rec
        rec["days_since_d0"] = c["days_since_d0"]
        rec["window_days_left"] = c["window_days_left"]
        rec["alert_suppressed"] = c["alert_suppressed"]
        rec["last_checked"] = today
        if rec["status"] == "watching" and not c["alert_suppressed"]:
            df = data.get(c["ticker"])
            if df is not None and len(df):
                try:
                    res = theme_reignition.check_confirm(df)
                except Exception as e:
                    res = None
                    print(f"[reignition] {c['ticker']} 확인진입 체크 실패: {e}")
                if res:
                    rec["compression"] = res["compression"]
                    if res["confirmed"]:
                        rec["status"] = "confirmed"
                        rec["confirm"] = {"date": today, "pivot": res["pivot"], "stop": res["stop"]}
                        rec["forward"] = {"opened_at": today, "entry": res["pivot"], "stop": res["stop"],
                                          "bars_held": 0, "r_progress": 0.0,
                                          "resolved": False, "resolved_r": None, "resolved_reason": None}

    # ── 창 만료(180거래일 초과) 또는 테마 매핑 자체가 사라짐 — 자동 해제 ──
    for key, rec in store.items():
        if rec.get("status") == "watching" and key not in current_keys:
            rec["status"] = "expired"
            rec["expired_at"] = today

    # ── 확인진입 이후 포워드 R 갱신(손절 이탈 또는 60봉 상한 시 확정) ──
    for key, rec in store.items():
        fwd = rec.get("forward")
        if not fwd or fwd.get("resolved"):
            continue
        df = data.get(rec["ticker"])
        if df is None or df.empty:
            continue
        try:
            close = float(df["Close"].iloc[-1])
        except Exception:
            continue
        entry, stop = fwd["entry"], fwd["stop"]
        if entry <= stop:
            continue
        r_now = round((close - entry) / (entry - stop), 2)
        fwd["bars_held"] += 1
        fwd["r_progress"] = r_now
        if close <= stop:
            fwd["resolved"], fwd["resolved_r"], fwd["resolved_reason"] = True, r_now, "stop"
        elif fwd["bars_held"] >= theme_reignition.FORWARD_MAX_BARS:
            fwd["resolved"], fwd["resolved_r"], fwd["resolved_reason"] = True, r_now, "time"

    _save_reignition_watch(store)


def _reignition_forward_stats() -> dict:
    """/api/reignition/forward용 누적 통계 — 백테스트 +0.755R과 실전 대조."""
    store = _load_reignition_watch()
    resolved = [rec for rec in store.values() if (rec.get("forward") or {}).get("resolved")]
    resolved.sort(key=lambda r: r["forward"]["opened_at"], reverse=True)
    n = len(resolved)
    rs_list = [r["forward"]["resolved_r"] for r in resolved]
    ev_r = round(sum(rs_list) / n, 3) if n else None
    win_rate = round(sum(1 for r in rs_list if r > 0) / n * 100, 1) if n else None
    return {
        "total_resolved": n, "ev_r": ev_r, "win_rate": win_rate,
        "backtest_reference": {"ev_r": 0.755, "n": 53,
                                "source": "docs/kr_theme_leader_reignition.md"},
        "recent": [{"theme": r["theme"], "ticker": r["ticker"], "name": r["name"],
                    "d0_date": r["d0_date"], **r["forward"]} for r in resolved[:30]],
    }


async def _run_scan_stage2(bundle: dict) -> dict:
    universe = bundle["universe"]
    data = bundle["data"]

    diag = {"kr_universe": 0, "us_universe": 0, "kr_fetched": 0, "us_fetched": 0,
            "kr_hits": 0, "us_hits": 0}
    for t in universe:
        if naver_kr.is_kr(t): diag["kr_universe"] += 1
        else: diag["us_universe"] += 1
    for t in data:
        if naver_kr.is_kr(t): diag["kr_fetched"] += 1

    # ── 1) 유동성 컷: 일평균 거래대금(20일) >= 20억원. 한국 전용(원화 기준). ──
    liquid: dict = {}
    liq_value: dict = {}
    for t, df in data.items():
        if not naver_kr.is_kr(t):
            continue
        c, v = df.get("Close"), df.get("Volume")
        if c is None or v is None or len(c) < 20 or len(v) < 20:
            continue
        try:
            avg_value = float((c.iloc[-20:] * v.iloc[-20:]).mean())
        except Exception:
            continue
        if avg_value >= STAGE2_LIQUIDITY_MIN_EOK * 1e8:
            liquid[t] = df
            liq_value[t] = avg_value
    diag["liquidity_dropped"] = diag["kr_fetched"] - len(liquid)

    # ── 2) RS 백분위: 유동성 생존자 안에서만 산출, >=70만 통과 ──
    raw_scores = {}
    for t, df in liquid.items():
        s = rs_score_stage2(df["Close"])
        if s is not None:
            raw_scores[t] = s
    pctiles = to_rs_rank(raw_scores)
    rs_survivors = {t: liquid[t] for t in liquid if pctiles.get(t, 0) >= STAGE2_RS_PCTILE_MIN}
    diag["rs_dropped"] = len(liquid) - len(rs_survivors)

    # ── 3) Stage2 템플릿 + 4) 거래량수축/MA수렴(필터) + 5) 티어링 ──
    hits = []
    for t, df in rs_survivors.items():
        r = analyze_stage2(df, rs_pctile=pctiles.get(t))
        if r is None:
            continue
        pf = price_frozen_check(df["Close"], df["High"], df["Low"], df["Volume"])  # v5.90
        hits.append({
            "ticker": t, "name": universe.get(t, t), "market": "KR",
            "sector": _sector_of(t), "alert": None,
            "climax": False, "climax_reasons": [], "climax_level": None,
            "avg_value_20_eok": round(liq_value[t] / 1e8, 1),
            "price_frozen": pf["price_frozen"], "price_frozen_reasons": pf["price_frozen_reasons"],
            **r,
        })
        diag["kr_hits"] += 1
    hits.sort(key=lambda x: (x["tier"], -x["score"]))
    await _attach_earnings_badges(hits)   # v5.05: 💰실적우수 배지

    from collections import Counter
    sec_count = Counter(h["sector"] for h in hits if h["sector"] != "기타")
    sector_summary = [{"sector": s, "count": n} for s, n in sec_count.most_common() if n >= 2]

    return {
        "version": VERSION, "market": "kr", "mode": "stage2",
        "scanned": len(universe), "fetched": len(data), "diag": diag,
        "hits": hits, "sector_summary": sector_summary, "warn_count": 0,
        "timing": bundle.get("timing"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(),
    }


# ── 강한피벗(strong_pivot) 실험 탭 (v5.20, v5.22 강화) ──
# 정의: analyze_imminent 통과(피벗 형성 중) ∩ (Stage2 통과 OR IBD9 통과).
# Stage2=KR전용, IBD9=US전용이라 자연히 시장별로 갈리고, 게이트는 OR(둘 중
# 하나만 통과해도 인정)이라 0개 방지 목적. analyze_imminent/analyze_stage2/
# analyze_ibd9_*와 _run_scan_stage2/_run_scan_ibd9 원본은 전혀 수정하지 않고
# 그 결과(hits의 ticker 집합)만 재사용한다 — 이 탭이 통째로 사라져도 기존
# imminent/stage2/ibd9 탭에는 아무 영향이 없다(순수 additive, 롤백 용이).
#
# v5.22: "초기 국면 + 매집 우위 + 강한 조건 겹침"만 남도록 3단 필터 추가.
# 1층(하드컷): 후기 스테이지/과확장 제외 — 이 탭의 목적은 "아직 초기인" 피벗만
#   보는 것이라, 이미 크게 오른 뒤의 베이스(late_level≠none)나 200일선과
#   너무 멀어진 종목(ext200_pct 초과)은 대상이 아님.
# 2층(매집 필수화, v5.57에서 제거): 원래 U/D Volume Ratio 1.0 미만 하드
#   제외였으나, 실측(docs/all_tabs_common_yardstick_investigation.md)
#   결과 U/D<1.0 그룹이 오히려 EV가 가장 높았음(게이트 있음 0.224 vs
#   없음 0.252 vs 새로 추가되는 ud<1.0 그룹만 0.361 — 최고). 게이트가
#   최우수 후보를 걸러내고 있었던 것이 확인돼 제거.
# 3층(강도 스코어): 남은 후보를 strength_score로 다시 랭킹하고, pool_count가
#   2(Stage2+IBD9 동시 통과)면 그 자체로 최상위 신호로 보고 강도 컷을 면제,
#   아니면 strength_score 임계를 넘는 것만 최종 통과. U/D 기반
#   accum_score_component(v5.57에서 제거)도 같은 이유 — 방향이 틀린 값을
#   점수에 남겨두면 하드 게이트만 없앤 채 계속 불리하게 반영됨.
STRONG_PIVOT_MAX_EXT200 = 30    # 200일선 이격 +30% 초과면 과확장으로 제외(1층)
STRONG_PIVOT_MIN_STRENGTH = 40  # strength_score 최소 컷 — pool_count>=2면 면제(3층)


async def _run_scan_strong_pivot(bundle: dict) -> dict:
    universe = bundle["universe"]
    data = bundle["data"]
    rs_ranks = bundle["rs_ranks"]
    rs_moms = bundle["rs_moms"]

    try:
        stage2_result = await _run_scan_stage2(bundle)
        stage2_set = {h["ticker"] for h in stage2_result.get("hits", [])}
    except Exception:
        stage2_set = set()
    try:
        ibd9_result = await _run_scan_ibd9(bundle)
        ibd9_set = {h["ticker"] for h in ibd9_result.get("hits", [])}
    except Exception:
        ibd9_set = set()

    diag = {"kr_universe": 0, "us_universe": 0, "kr_fetched": 0, "us_fetched": 0,
            "kr_hits": 0, "us_hits": 0, "imminent_pass": 0,
            "dropped_late": 0, "dropped_ext200": 0, "gate_pass": 0,
            "dropped_weak": 0, "final_hits": 0}
    for t in universe:
        if naver_kr.is_kr(t): diag["kr_universe"] += 1
        else: diag["us_universe"] += 1
    for t in data:
        if naver_kr.is_kr(t): diag["kr_fetched"] += 1
        else: diag["us_fetched"] += 1

    alerts = load_alerts()
    hits = []
    for t, df in data.items():
        is_kr = naver_kr.is_kr(t)
        result = analyze_imminent(df, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t), is_kr=is_kr)
        if result is None:
            continue
        diag["imminent_pass"] += 1

        # ── 1층 하드컷: 후기 스테이지 / 과확장 제외 ──
        if result.get("late_level") not in (None, "none"):
            diag["dropped_late"] += 1
            continue
        if (result.get("ext200_pct") or 0) > STRONG_PIVOT_MAX_EXT200:
            diag["dropped_ext200"] += 1
            continue

        # ── 품질풀 OR 게이트 (Stage2 / IBD9) ──
        pools = []
        if t in stage2_set:
            pools.append("Stage2")
        if t in ibd9_set:
            pools.append("IBD9")
        if not pools:
            continue
        diag["gate_pass"] += 1

        # ── 저유동성 하드 필터 — 기존 run_scan과 동일 기준 (KR 3억원/US $2M) ──
        avg_turn = result.get("avg_turnover") or 0
        if avg_turn > 0:
            floor_ = 3e8 if is_kr else 2e6
            if avg_turn < floor_:
                diag["liquidity_dropped"] = diag.get("liquidity_dropped", 0) + 1
                continue

        # ── 3층: 강도 스코어 (가중치 근거는 위 주석 참고) ──
        # pool_count 20점/개(최대 40) — 이 탭의 핵심 선별 기준이라 최우선.
        # 두드림(touch_count) 최대 15점(회당 3점, 5회 캡) — 매물벽 약화 신호.
        # 거래량수축/변동폭축소 각 10점 고정 — "조용히 준비 중"인 초기 국면의 직접 증거.
        # RS 최대 15점 — 이미 여러 게이트를 통과했으므로 추가 변별력은 작게만.
        # v5.57: U/D 기반 accum_score_component(최대 20점) 제거 — 방향이
        # 틀린 값이라 점수에서도 빼야 하드 게이트 제거의 취지가 산다.
        touch = result.get("touch_count") or 0
        rs = result.get("rs") or 0
        pool_score = len(pools) * 20
        touch_score = min(touch, 5) * 3
        vol_dry_score = 10 if result.get("vol_dry") else 0
        tightening_score = 10 if result.get("tightening") else 0
        rs_score = 15 * rs / 99
        strength_score = round(pool_score + touch_score
                               + vol_dry_score + tightening_score + rs_score, 1)
        strength_score = min(strength_score, 100.0)

        # ── 최소 컷: pool_count>=2(겹침 자체가 최상위 신호)면 면제, 아니면 강도 임계 필요 ──
        if len(pools) < 2 and strength_score < STRONG_PIVOT_MIN_STRENGTH:
            diag["dropped_weak"] += 1
            continue

        mkt = "KR" if is_kr else "US"
        alert_kind = alerts.get(t.upper())
        cw = climax_warning(df["Close"], df["High"], df["Low"], df["Volume"])
        # v5.90: price_frozen은 analyze_imminent()가 이미 result에 붙여줌 — 중복 계산 안 함.
        hits.append({
            "ticker": t, "name": universe.get(t, t), "market": mkt,
            "sector": _sector_of(t), "alert": alert_kind,
            "climax": cw["climax"], "climax_reasons": cw["reasons"],
            "climax_level": cw["level"],
            **result,
            "mode": "strong_pivot",
            "pools": pools,
            "pool_count": len(pools),
            "strength_score": strength_score,
        })
        if is_kr: diag["kr_hits"] += 1
        else: diag["us_hits"] += 1

    hits.sort(key=lambda x: x["strength_score"], reverse=True)
    diag["final_hits"] = len(hits)
    await _attach_earnings_badges(hits[:30])

    from collections import Counter
    sec_count = Counter(h["sector"] for h in hits if h["sector"] != "기타")
    sector_summary = [{"sector": s, "count": n} for s, n in sec_count.most_common() if n >= 2]

    warn_count = sum(1 for h in hits if h.get("alert") or h.get("risk_warn"))

    return {
        "version": VERSION, "market": "all", "mode": "strong_pivot",
        "scanned": len(universe), "fetched": len(data), "diag": diag,
        "hits": hits, "sector_summary": sector_summary, "warn_count": warn_count,
        "timing": bundle.get("timing"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(),
    }


# ── IBD 9조건 스크린 (v5.03, 사용자 제공 스펙 — 미국 전용) ──
# 적용 순서: 가격데이터만으로 되는 저비용 5개(2~6번) 먼저 → 통과한 소수만
# yfinance .info(베타·시총·기관보유비율)가 필요한 고비용 3개(1·7·9번) 확인.
# 저비용 단계에서 3개월수익률 30%+ 등으로 이미 크게 줄어들지만, 시장 급등
# 국면 등 예외적으로 생존자가 많을 때를 대비해 상위 IBD9_MAX_INFO_FETCH개로
# 캡 — .info 호출은 종목당 네트워크 왕복이라 무제한 허용하면 레이트리밋/
# 응답지연 위험.
IBD9_MAX_INFO_FETCH = 60


def _ibd9_fetch_info(ticker: str) -> dict:
    """블로킹 — executor에서 실행. yfinance .info에서 베타/시총/기관보유비율만
    추출. 실패하면 전부 None(그 종목은 고비용 단계에서 탈락)."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return {"beta": None, "market_cap": None, "held_pct_inst": None}
    return {
        "beta": info.get("beta"),
        "market_cap": info.get("marketCap"),
        "held_pct_inst": info.get("heldPercentInstitutions"),
    }


async def _run_scan_ibd9(bundle: dict) -> dict:
    universe = bundle["universe"]
    data = bundle["data"]

    diag = {"kr_universe": 0, "us_universe": 0, "kr_fetched": 0, "us_fetched": 0,
            "kr_hits": 0, "us_hits": 0}
    for t in universe:
        if naver_kr.is_kr(t): diag["kr_universe"] += 1
        else: diag["us_universe"] += 1
    for t in data:
        if not naver_kr.is_kr(t): diag["us_fetched"] += 1

    # ── 저비용 필터(가격 데이터만, 조건 2~6) — 미국 종목만 ──
    cheap_survivors = {}
    for t, df in data.items():
        if naver_kr.is_kr(t):
            continue
        r = analyze_ibd9_cheap(df)
        if r is not None:
            cheap_survivors[t] = r
    diag["cheap_dropped"] = diag["us_fetched"] - len(cheap_survivors)

    # 생존자가 많으면 3개월 수익률 상위 IBD9_MAX_INFO_FETCH개만 고비용 단계로.
    tickers = sorted(cheap_survivors, key=lambda t: -cheap_survivors[t]["ret_3m_pct"])
    tickers = tickers[:IBD9_MAX_INFO_FETCH]
    diag["info_fetch_capped"] = len(cheap_survivors) - len(tickers)

    # ── 고비용 필터(yfinance .info: 베타/시총/기관보유비율) — 배치 실행 ──
    loop = asyncio.get_event_loop()
    info_map = {}
    BATCH = 8
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        results = await asyncio.gather(
            *[loop.run_in_executor(_executor, _ibd9_fetch_info, t) for t in chunk]
        )
        for t, res in zip(chunk, results):
            info_map[t] = res

    hits = []
    for t in tickers:
        extra = info_map.get(t, {})
        r = analyze_ibd9_full(data[t], cheap_survivors[t], extra.get("beta"),
                              extra.get("market_cap"), extra.get("held_pct_inst"))
        if r is None:
            continue
        pf = price_frozen_check(data[t]["Close"], data[t]["High"], data[t]["Low"], data[t]["Volume"])  # v5.90
        hits.append({
            "ticker": t, "name": universe.get(t, t), "market": "US",
            "sector": _sector_of(t), "alert": None,
            "climax": False, "climax_reasons": [], "climax_level": None,
            "price_frozen": pf["price_frozen"], "price_frozen_reasons": pf["price_frozen_reasons"],
            **r,
        })
        diag["us_hits"] += 1
    hits.sort(key=lambda x: -x["score"])
    await _attach_earnings_badges(hits)   # v5.05: 💰실적우수 배지

    from collections import Counter
    sec_count = Counter(h["sector"] for h in hits if h["sector"] != "기타")
    sector_summary = [{"sector": s, "count": n} for s, n in sec_count.most_common() if n >= 2]

    return {
        "version": VERSION, "market": "us", "mode": "ibd9",
        "scanned": len(universe), "fetched": len(data), "diag": diag,
        "hits": hits, "sector_summary": sector_summary, "warn_count": 0,
        "timing": bundle.get("timing"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(),
    }


# ── 💰실적우수 전용 탭 (v5.06, v5.07 안정화) — earnings.py Phase 1 판정만으로
# 스캔. 배지(_attach_earnings_badges)는 그대로 두고, 이 탭은 유니버스
# 전체에서 "실적 조건만" 통과한 종목을 직접 찾아준다. 한국+미국 둘 다 대상.
# 적용 순서: RS 백분위(이미 계산돼 있어 추가비용 0) 사전 필터 → 상위 N개만
# 비용 발생하는 실적 조회(yfinance/네이버 HTML) — 유니버스 전체(수천 종목)에
# 실적 조회를 다 걸면 절대 안 끝남.
#
# v5.07 [버그수정] "실적우수 탭 로딩 계속 실패" (다른 탭은 정상).
#   [원인] yfinance income_stmt/quarterly_income_stmt는 최초 크럼(crumb)/
#          쿠키 인증이 필요한 엔드포인트라, Railway 서버 IP에서 야후가 느리게
#          응답하거나 일시적으로 막히면 종목 하나당 호출이 오래 걸릴 수 있음.
#          80종목을 배치(8개씩) 순차 대기하는 구조라, 그 중 하나라도 오래
#          걸리면(또는 asyncio.gather가 예외 하나에 전체를 중단시키면) 응답
#          전체가 안 끝나거나 500으로 죽어 "로딩 실패"로 보임 — 로컬 20종목
#          테스트에선 재현 안 됐지만(전부 성공/정상 실패), 프로덕션 환경의
#          네트워크 변동성까지 가정한 방어가 없었던 게 근본 문제.
#   [해결] (1) 종목당 12초 하드 타임아웃(asyncio.wait_for) — 느린 응답이
#              전체를 막지 않고 그 종목만 '판정불가'로 넘어감.
#          (2) asyncio.gather(return_exceptions=True) — 예외 하나가 전체
#              스캔을 중단시키지 않게.
#          (3) 조회 대상 80→40개로 축소 — 최악의 경우 총 대기시간 단축.
#
# v5.08 [버그수정] v5.07 이후에도 "실적우수 탭만 계속 로딩 실패" — 사용자
# 재확인. asyncio.wait_for는 코루틴이 기다리는 걸 포기하게만 할 뿐 실제
# 스레드를 죽이지 못해서(파이썬 스레드 강제종료 불가), 느린/멈춘 yfinance
# 요청이 공유 _executor(max_workers=8, 앱 전체가 씀)의 워커를 계속 붙잡고
# 있었을 가능성이 높음 — 배치마다 새 요청을 또 던지니 워커가 고갈되면서
# 이 탭뿐 아니라 결국 응답 자체가 안 옴. 실적 조회 전용 격리 풀
# (_earnings_executor, max_workers=4)로 분리해 최악의 경우에도 다른
# 엔드포인트가 막히지 않게 하고, BATCH를 그 풀 크기(4)에 맞춤 + 조회 대상을
# 40→20으로 더 줄여 전체 최악 대기시간을 이전 수준(약 60초)으로 유지.
# v5.17 [기능개선] 사용자 요청: "조건에 맞는 건 다 검색되면 좋겠다, RS70+
# 이상 전부." 이전엔 RS70+ 중에서도 상위 20개만 확인해서(요청 하나 안에서
# 기다리던 시절의 속도 걱정 때문) 3400여 종목 중 실적 조건까지 통과한 게
# 7개뿐이었음 — 이 중 실제로는 "RS70+ 인데 확인조차 안 된" 종목이 훨씬 많이
# 있었을 거라는 뜻. v5.14부터 실적 조회가 완전히 백그라운드로 빠져서 사용자
# 응답 속도엔 영향이 없으므로, 이제 RS70+ 전체를 확인 대상으로 늘림(사실상
# 무제한 — 유니버스 규모를 넉넉히 상회하는 값으로 캡만 걸어둠). 배경 스캔
# 자체는 종목 수에 비례해 오래 걸릴 수 있음(수백~천여 종목이면 수십 분).
EARNINGS_TAB_RS_MIN = 70
EARNINGS_TAB_MAX_CHECK = 5000
EARNINGS_TAB_PER_TICKER_TIMEOUT = 12  # 초
EARNINGS_TAB_DEADLINE_SEC = 1800  # 백그라운드 파이프라인 전체 예산(30분) — 넘으면 그때까지 결과로 응답


async def _get_earnings_safe(ticker: str) -> dict:
    """_get_earnings_cached를 타임아웃 + 예외 안전망으로 감싼 버전.
    실패/타임아웃이면 '판정불가'로 취급 — 이 종목 하나가 전체 스캔을 막지 않음."""
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_earnings_executor, _get_earnings_cached, ticker),
            timeout=EARNINGS_TAB_PER_TICKER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "verdict": "unknown", "reasons": ["조회 타임아웃"]}
    except Exception as e:
        return {"ok": False, "verdict": "unknown", "reasons": [f"조회 실패: {e}"]}


async def _run_scan_earnings_inner(bundle: dict) -> dict:
    import math
    universe = bundle["universe"]
    data = bundle["data"]
    rs_ranks = bundle.get("rs_ranks", {})

    diag = {"kr_universe": 0, "us_universe": 0, "kr_fetched": 0, "us_fetched": 0,
            "kr_hits": 0, "us_hits": 0}
    for t in universe:
        if naver_kr.is_kr(t): diag["kr_universe"] += 1
        else: diag["us_universe"] += 1
    for t in data:
        if naver_kr.is_kr(t): diag["kr_fetched"] += 1
        else: diag["us_fetched"] += 1

    # ── 저비용 사전 필터: RS 백분위(무료) 상위만, 그 중에서도 RS 상위 N개만 ──
    candidates = []
    for t, df in data.items():
        rs = rs_ranks.get(t)
        if rs is None or rs < EARNINGS_TAB_RS_MIN or df is None or len(df) < 2:
            continue
        candidates.append((t, rs))
    candidates.sort(key=lambda x: -x[1])
    candidates = candidates[:EARNINGS_TAB_MAX_CHECK]
    diag["rs_prefilter_dropped"] = (diag["kr_fetched"] + diag["us_fetched"]) - len(candidates)

    # ── 고비용 실적 조회 — 배치 동시 실행 + 종목당 타임아웃 + 6시간 캐시 ──
    # BATCH을 _earnings_executor의 max_workers(4)에 맞춤 — 안 맞추면 나머지가
    # 풀에서 대기만 하다 타임아웃되는 낭비가 생김.
    # v5.09: 종목당 타임아웃(12초)이 있어도 배치 개수가 많으면 총합이 커져
    # 결국 응답 자체가 안 오는 문제가 있었음(사용자 재확인: v5.08 이후에도
    # 실패) — 배치별 타임아웃 대신 파이프라인 전체에 '마감시각'을 둬서,
    # 넘으면 이후 배치는 건너뛰고 그때까지 찾은 것만으로 응답한다. 이러면
    # 총 응답시간이 항상 EARNINGS_TAB_DEADLINE_SEC 근처로 상한선이 생김.
    tickers = [t for t, _ in candidates]
    BATCH = 6   # v5.17: _earnings_executor의 max_workers(6)에 맞춤
    eg_map = {}
    deadline = time.time() + EARNINGS_TAB_DEADLINE_SEC
    timed_out = False
    for i in range(0, len(tickers), BATCH):
        if time.time() >= deadline:
            timed_out = True
            break
        chunk = tickers[i:i + BATCH]
        results = await asyncio.gather(
            *[_get_earnings_safe(t) for t in chunk], return_exceptions=True
        )
        for t, r in zip(chunk, results):
            eg_map[t] = r if isinstance(r, dict) else {"ok": False, "verdict": "unknown", "reasons": [str(r)]}
    diag["earnings_checked"] = len(eg_map)
    diag["earnings_timed_out"] = timed_out

    hits = []
    for t, rs in candidates:
        eg = eg_map.get(t) or {}
        if eg.get("verdict") != "pass":
            continue
        df = data[t]
        c = df["Close"]
        close = float(c.iloc[-1])
        prev = float(c.iloc[-2]) if len(c) >= 2 else close
        chg = (close / prev - 1) * 100 if prev else 0.0
        is_kr = naver_kr.is_kr(t)
        eps_yoy = eg.get("quarterly_eps_yoy_pct") or 0
        score = round(60 * (rs or 0) / 99 + 40 * min(eps_yoy, 200) / 200, 1)
        pf = price_frozen_check(df["Close"], df["High"], df["Low"], df["Volume"])  # v5.90
        hits.append({
            "ticker": t, "name": universe.get(t, t), "market": "KR" if is_kr else "US",
            "sector": _sector_of(t), "alert": None,
            "climax": False, "climax_reasons": [], "climax_level": None,
            "price_frozen": pf["price_frozen"], "price_frozen_reasons": pf["price_frozen_reasons"],
            "mode": "earnings",
            "close": round(close, 2), "change_pct": round(chg, 2),
            "score": score, "rs": rs,
            "earnings_verdict": "pass", "earnings_badge": True,
            "eps_yoy_pct": eg.get("quarterly_eps_yoy_pct"),
            "revenue_yoy_pct": eg.get("revenue_yoy_pct"),
            "annual_eps_growing": eg.get("annual_eps_growing"),
            "annual_eps": eg.get("annual_eps"),
            "accelerating": eg.get("accelerating"),
            # v5.16 [버그수정]: 프론트 card()가 모든 모드에서 무조건
            # sparkSVG(s.spark, s.spark_ma20)를 호출하는데, 이 모드만 그
            # 필드를 안 채워서 undefined.concat()으로 100% 크래시 —
            # 다른 analyze_* 함수들과 동일하게 채워준다.
            "spark": [round(float(x), 4) for x in c.iloc[-60:].tolist()],
            "spark_ma20": [
                None if math.isnan(x) else round(float(x), 4)
                for x in c.rolling(20).mean().iloc[-60:].tolist()
            ],
        })
        if is_kr: diag["kr_hits"] += 1
        else: diag["us_hits"] += 1
    hits.sort(key=lambda x: -x["score"])

    from collections import Counter
    sec_count = Counter(h["sector"] for h in hits if h["sector"] != "기타")
    sector_summary = [{"sector": s, "count": n} for s, n in sec_count.most_common() if n >= 2]

    return {
        "version": VERSION, "market": "all", "mode": "earnings",
        "scanned": len(universe), "fetched": len(data), "diag": diag,
        "hits": hits, "sector_summary": sector_summary, "warn_count": 0,
        "timing": bundle.get("timing"), "partial": timed_out,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(),
    }


def _pending_scan_result(market: str, mode: str) -> dict:
    """v5.12: 유니버스 데이터가 아직 콜드(백그라운드 수집 중)일 때 즉시
    반환하는 '준비 중' 응답. 프론트가 이 pending 플래그를 보고 가벼운
    폴링으로 재확인한다 — 이 응답 자체는 절대 결과 캐시에 저장하면 안 됨
    (그러면 실제 데이터가 준비된 뒤에도 계속 pending만 보이게 됨)."""
    return {
        "version": VERSION, "market": market, "mode": mode,
        "pending": True, "scanned": 0, "fetched": 0,
        "diag": {}, "hits": [], "sector_summary": [], "warn_count": 0,
        "timing": None, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }


# v5.14 [버그수정] 사용자 재확인: "다른 탭이랑 로딩하는 게 다르다. 재시도
# 하지 말라고. 다른 탭 참고해." — 정확한 지적. 다른 모드는 가격 번들만
# 준비되면 나머지(순회·정렬)가 전부 CPU 연산이라 요청 안에서 즉시 끝나는데,
# 실적우수만 유일하게 그 뒤에 "네트워크 조회"(RS백분위 20종목 실적 확인,
# 최대 45초)를 요청 하나 안에서 추가로 더 기다렸음 — 이게 다른 탭과
# 다르게 "느리고 재시도하는" 것처럼 보인 진짜 이유. _fetch_market_data의
# 콜드스타트 처리(v5.12)와 완전히 같은 패턴으로: 이 무거운 단계도 요청
# 밖으로(백그라운드) 빼서, 다른 탭처럼 매 요청이 항상 즉시 끝나게 만든다.
_earnings_scan_state: dict = {"in_progress": False, "result": None, "finished_at": 0}
# v5.17: RS70+ 전체를 훑게 되면서 스캔 자체가 수십 분 걸릴 수 있어, 완료된
# 결과를 너무 짧게(10분) 버리면 다 끝나자마자 또 처음부터 다시 도는 낭비가
# 생김 — 종목별 실적 캐시(_EARNINGS_TTL)와 맞춰 6시간으로.
EARNINGS_SCAN_TTL = 6 * 3600


async def _run_scan_earnings_bg(bundle: dict) -> None:
    try:
        result = await _run_scan_earnings_inner(bundle)
        _earnings_scan_state["result"] = result
        _earnings_scan_state["finished_at"] = time.time()
    except Exception as e:
        print(f"[earnings-scan bg] failed: {e}")
    finally:
        _earnings_scan_state["in_progress"] = False


async def _run_scan_earnings(bundle: dict) -> dict:
    """더 이상 이 함수를 호출한 요청이 실적 조회를 기다리지 않는다 — 완료된
    결과가 있으면(TTL 이내) 그걸 반환, 없으면 백그라운드로 계산을 걸어두고
    즉시 '준비 중'을 반환한다(다른 탭들과 동일하게 매 요청이 가벼움)."""
    cached = _earnings_scan_state.get("result")
    if cached is not None and time.time() - _earnings_scan_state.get("finished_at", 0) < EARNINGS_SCAN_TTL:
        return cached
    if not _earnings_scan_state.get("in_progress"):
        _earnings_scan_state["in_progress"] = True
        asyncio.create_task(_run_scan_earnings_bg(bundle))
    return _pending_scan_result("all", "earnings")


# v5.114 (사용자 지시, docs/kr_breakout_family_multi_hit_ev.md 사전등록 채택) —
# KR 돌파/박스돌파/추세전환 동시 히트 배지. 측정 결과: 자카드 진단상 돌파↔
# 박스돌파는 0.485(같은 조건의 중복 검출)인 반면 추세전환은 나머지 둘과
# 0.02~0.03(거의 독립) — 그래서 "히트 개수"가 아니라 "추세전환 포함 여부"로
# 우대한다(사용자 지시: 돌파+박스돌파 쌍은 가점 없음). sort_bonus 크기는
# scanner.py의 기존 보너스 스케일(ACCUM_SYNERGY_BONUS=10, "진짜 돌파 보너스"
# =10 등, score가 0~100대)과 같은 자리수로 맞춤 — 실측 EV가 단일 히트
# 0.252R 대비 추세전환 포함 2조합은 0.500~0.833R(약 2~3배), 3조합은
# 1.152R(약 4.5배)이라 3조합에 더 큰 가점을 준다.
MULTI_HIT_TAB_LABEL = {"breakout": "돌파", "boxbreak": "박스돌파", "turnaround": "추세전환"}
MULTI_HIT_SORT_BONUS = {"strong2": 8, "strong3": 15}   # score 스케일 가점, 중복(⚑)은 0

# v5.115(사용자 지시): 조합별 배지/가점에 최소 n 하한은 걸지 않되(정보성
# 표시+약한 정렬가점일 뿐 게이트가 아니므로), n<50인 조합은 툴팁에 표본
# 경고를 자동 병기한다. n 값은 여기 한 곳에만 두고 아래서 파생시켜서,
# 측정치가 갱신돼도 경고 조건이 따로 놀지 않게 한다.
MULTI_HIT_SAMPLE_N = {"triple": 33, "breakout+turnaround": 8,
                       "boxbreak+turnaround": 12, "breakout+boxbreak": 342}
MULTI_HIT_SAMPLE_WARN_THRESHOLD = 50


def _multi_hit_n_suffix(combo: str) -> str:
    """'n=33' 또는 표본이 작으면 'n=33, 표본 작음: 참고용'."""
    n = MULTI_HIT_SAMPLE_N[combo]
    suffix = f"n={n}"
    if n < MULTI_HIT_SAMPLE_WARN_THRESHOLD:
        suffix += ", 표본 작음: 참고용"
    return suffix


def _kr_breakout_family_hit_map(data: dict, rs_ranks: dict, rs_moms: dict) -> dict:
    """오늘자 KR 돌파/박스돌파/추세전환 동시 히트 맵 (ticker -> set(label)).
    docs/kr_breakout_family_multi_hit_ev.md 측정 스크립트(collect_family)와
    동일한 히트 판정(analyze_fn + 저유동성 3억원/일 필터)을 프로덕션에서
    재사용 — 히트 로직을 여기서 새로 만들지 않는다."""
    from scanner import BREAKOUT_CONFIG, BOXBREAK_CONFIG, TURN_CONFIG
    family = [
        ("breakout", analyze_breakout, BREAKOUT_CONFIG),
        ("boxbreak", analyze_boxbreak, BOXBREAK_CONFIG),
        ("turnaround", analyze_turnaround, TURN_CONFIG),
    ]
    hit_map: dict = {}
    for label, analyze_fn, cfg in family:
        for t, df in data.items():
            if not t.endswith((".KS", ".KQ")):
                continue
            if len(df) < cfg["min_bars"]:
                continue
            try:
                result = analyze_fn(df, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t), is_kr=True)
            except Exception:
                result = None
            if result is None:
                continue
            avg_turn = result.get("avg_turnover") or 0
            if avg_turn and avg_turn < 3e8:
                continue
            hit_map.setdefault(t, set()).add(label)
    return hit_map


def _multi_hit_badge_for(labels: set) -> dict | None:
    """labels: 오늘 이 티커가 히트한 KR 돌파계열 탭 집합(2개 이상일 때만
    호출). 배지/툴팁(실측 EV 인용)/정렬가점 반환. 1개 이하면 None."""
    if len(labels) < 2:
        return None
    if len(labels) == 3:
        return {
            "badge": "🔱🔱",
            "tooltip": (f"돌파+박스돌파+추세전환 3중 히트 — 실측 EV 1.152R"
                        f"({_multi_hit_n_suffix('triple')}), "
                        "손절률 24.2% (단일 히트 0.252R 대비) · "
                        "docs/kr_breakout_family_multi_hit_ev.md"),
            "sort_bonus": MULTI_HIT_SORT_BONUS["strong3"],
            "combo": "triple",
        }
    if "turnaround" in labels:
        other = next(l for l in labels if l != "turnaround")
        combo = f"{other}+turnaround"
        ev = "0.500R" if other == "breakout" else "0.833R"
        return {
            "badge": "🔱 강력",
            "tooltip": (f"{MULTI_HIT_TAB_LABEL[other]}+추세전환 동시 히트 — 실측 EV {ev}"
                        f"({_multi_hit_n_suffix(combo)}) "
                        "(단일 히트 0.252R 대비), 두 탭 히트집합 자카드 유사도 0.02~0.03"
                        "(독립 신호) · docs/kr_breakout_family_multi_hit_ev.md"),
            "sort_bonus": MULTI_HIT_SORT_BONUS["strong2"],
            "combo": combo,
        }
    return {
        "badge": "⚑ 중복신호",
        "tooltip": (f"돌파+박스돌파 동시 히트 — 실측 EV 0.462R"
                    f"({_multi_hit_n_suffix('breakout+boxbreak')})이지만 두 탭 "
                    "히트집합 자카드 유사도 0.485로 같은 조건의 중복 검출에 가까워 "
                    "추가 정보가 제한적(추세전환이 낀 조합의 0.5~1.15R보다 약함) · "
                    "docs/kr_breakout_family_multi_hit_ev.md"),
        "sort_bonus": 0,
        "combo": "breakout+boxbreak",
    }


async def run_scan(market: str, mode: str) -> dict:
    # 데이터는 시장 단위 캐시에서 (모드 바뀌어도 재호출 안 함)
    bundle = await _fetch_market_data(market)
    if bundle is None:
        return _pending_scan_result(market, mode)
    if mode == "earnings":
        return await _run_scan_earnings(bundle)
    if mode == "stage2":
        return await _run_scan_stage2(bundle)
    if mode == "ibd9":
        return await _run_scan_ibd9(bundle)
    if mode == "strong_pivot":
        return await _run_scan_strong_pivot(bundle)
    if mode == "jongga":
        return await _run_scan_jongga(bundle)
    universe = bundle["universe"]
    data = bundle["data"]
    rs_ranks = bundle["rs_ranks"]
    rs_moms = bundle["rs_moms"]
    rs3_ranks = bundle.get("rs3_ranks", {})   # v5.71 — 구 디스크캐시 호환 위해 .get
    rs_deltas = bundle.get("rs_deltas", {})
    _scan_timing = bundle.get("timing")
    # v5.114: KR 돌파계열 3탭에서만 동시 히트 맵 계산(다른 모드엔 해당 없음).
    _family_hit_map = _kr_breakout_family_hit_map(data, rs_ranks, rs_moms) if mode in ("breakout", "boxbreak", "turnaround") else {}

    fn = {"turnaround": analyze_turnaround, "leader": analyze_leader, "super": analyze_super, "breakout": analyze_breakout, "surge": analyze_surge, "imminent": analyze_imminent, "boxbreak": analyze_boxbreak, "breakdown": analyze_breakdown, "pattern": analyze_pattern}.get(mode, analyze)
    supports_intraday = mode in ("pullback", "turnaround", "imminent", "boxbreak", "breakout", "breakdown", "pattern", "super")  # is_kr 인자를 받는 모드
    alerts = load_alerts()
    hits = []
    # 진단용 시장별 카운터
    diag = {"kr_universe": 0, "us_universe": 0, "kr_fetched": 0, "us_fetched": 0,
            "kr_hits": 0, "us_hits": 0}
    for t in universe:
        if t.endswith((".KS", ".KQ")): diag["kr_universe"] += 1
        else: diag["us_universe"] += 1
    for t in data:
        if t.endswith((".KS", ".KQ")): diag["kr_fetched"] += 1
        else: diag["us_fetched"] += 1
    for t, df in data.items():
        is_kr = t.endswith((".KS", ".KQ"))
        kwargs = {"rs_rank": rs_ranks.get(t), "rs_mom": rs_moms.get(t)}
        if supports_intraday:
            kwargs["is_kr"] = is_kr
        if mode == "pullback":   # v5.71: 게이트 변형 E는 눌림목 탭에만 적용
            kwargs["rs_3m"] = rs3_ranks.get(t)
            kwargs["rs_delta"] = rs_deltas.get(t)
        result = fn(df, **kwargs)
        if result is None:
            continue
        mkt = "KR" if is_kr else "US"
        # ── 저유동성 하드 필터 (v4.52) ──
        # 호가가 얇아 진입/청산 자체가 힘든 종목 제외. 시총이 아니라
        # 평균 거래대금 기준 (매매 가능성의 직접 지표).
        # KR 3억원/일, US $2M/일 미만은 스캔 결과에서 탈락.
        # 급등 탭은 제외 (단타 탭은 당일 거래대금이 이미 조건).
        avg_turn = result.get("avg_turnover") or 0
        if mode != "surge" and avg_turn > 0:
            floor_ = 3e8 if is_kr else 2e6
            if avg_turn < floor_:
                diag["liquidity_dropped"] = diag.get("liquidity_dropped", 0) + 1
                continue
        alert_kind = alerts.get(t.upper())
        # 미너비니식 클라이맥스(과열/매도) 경고 — 모든 모드에 부착
        cw = climax_warning(df["Close"], df["High"], df["Low"], df["Volume"])
        # v5.90(사용자 지시) — 가격고정(M&A 의심)은 scanner.py의 10개 analyze_*()가
        # 전부 내부에서 이미 계산해 result에 price_frozen/price_frozen_reasons로
        # 붙여준다(정보용, 하드 게이트 아님) — 여기서 다시 계산 안 함(중복
        # 방지). 예전엔 analyze_*() 내부에서 완전 제외했지만(v4.80), 이제는
        # 표시 여부를 프론트(static/index.html)가 판단 — "기본 숨김 + N개
        # 펼치기" 방식으로 바꾸기 위해 하드 제외를 뺐다. RS 랭킹 계산
        # (rs_ranks/rs_moms)은 이 지점보다 앞서 별도로 끝나 있어 영향 없고,
        # harness 측정 파이프라인은 passes_liquidity_filter가 hit.price_frozen을
        # 그대로 읽어 예전과 동일하게 제외한다(harness.py 참고).
        # v5.55: 눌림목 전용 — 슈퍼대장(RS95+ + 모멘텀) 소속 여부. 측정 결과
        # (docs/all_tabs_common_yardstick_investigation.md 후속) 눌림목
        # 히트를 슈퍼대장 소속으로만 좁히면 EV 0.151→0.266로 개선, RS≥90
        # 신호등의 완전한 부분집합이라 신호등을 대체하는 게 아니라 그보다
        # 한 단계 더 엄격한 별도 필터. 근사(RS≥95만)보다 실제 analyze_super()
        # 호출이 EV 0.244→0.266로 더 정확해 그대로 사용(비용 차이 없음).
        if mode == "pullback":
            result["is_super"] = analyze_super(df, rs_rank=rs_ranks.get(t),
                                               rs_mom=rs_moms.get(t), is_kr=is_kr) is not None
        # v5.114: 이 티커가 오늘 다른 KR 돌파계열 탭에도 동시 히트했는지
        # (docs/kr_breakout_family_multi_hit_ev.md 사전등록 채택 결과 반영).
        _multi_labels = _family_hit_map.get(t) if _family_hit_map else None
        if _multi_labels and len(_multi_labels) >= 2:
            _badge = _multi_hit_badge_for(_multi_labels)
            if _badge:
                result["multi_hit_badge"] = _badge["badge"]
                result["multi_hit_tooltip"] = _badge["tooltip"]
                result["multi_hit_combo"] = _badge["combo"]
                result["multi_hit_sort_bonus"] = _badge["sort_bonus"]
        hits.append({"ticker": t, "name": universe[t], "market": mkt,
                     "sector": _sector_of(t), "alert": alert_kind,
                     "climax": cw["climax"], "climax_reasons": cw["reasons"],
                     "climax_level": cw["level"], **result})
        if is_kr: diag["kr_hits"] += 1
        else: diag["us_hits"] += 1

    hits.sort(key=lambda x: (x.get("triggered", False),
                              (x.get("setup_score") or x["score"]) + x.get("multi_hit_sort_bonus", 0)),
              reverse=True)
    # v5.05: 💰실적우수 배지 — 스캔 하나에 수십~백여 개 히트가 나올 수 있어
    # (IBD9/Stage2와 달리 이 경로는 히트 수가 안 작음) 상위 30개만 적용.
    # 안 그러면 v4.86에서 어렵게 고친 "첫 스캔 2~4분" 속도가 다시 느려짐.
    await _attach_earnings_badges(hits[:30])

    # 섹터 요약: 2개 이상 잡힌 섹터를 개수 내림차순 (기타 제외)
    from collections import Counter
    sec_count = Counter(h["sector"] for h in hits if h["sector"] != "기타")
    sector_summary = [
        {"sector": s, "count": n} for s, n in sec_count.most_common() if n >= 2
    ]

    warn_count = sum(1 for h in hits if h.get("alert") or h.get("risk_warn"))

    return {
        "version": VERSION,
        "market": market,
        "mode": mode,
        "scanned": len(universe),
        "fetched": len(data),
        "diag": diag,
        "hits": hits,
        "sector_summary": sector_summary,
        "warn_count": warn_count,
        "timing": _scan_timing,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }


@app.get("/api/scan")
async def scan(market: str = "all", mode: str = "imminent", refresh: bool = False):
    market = market if market in ("kr", "us", "all") else "all"
    mode = mode if mode in ("pullback", "turnaround", "leader", "super", "breakout", "surge", "imminent", "boxbreak", "breakdown", "pattern", "stage2", "ibd9", "earnings", "strong_pivot", "jongga") else "pullback"
    key = f"{market}:{mode}"
    favs = load_favorites()
    cached = _cache.get(key)
    if cached and not refresh:
        # 장 마감 후면 TTL 무시(데이터 안 바뀜), 장중이면 10분 TTL
        daykey = _market_session_key(market)
        fresh = (cached.get("daykey") == daykey) if daykey else (time.time() - cached["ts"] < CACHE_TTL)
        if fresh:
            return JSONResponse(_clean_nan({**cached, "favorites": favs, "cached": True}))
    result = await run_scan(market, mode)
    if result.get("pending"):
        # v5.12: 준비 중 응답은 캐시에 저장하면 안 됨 — 안 그러면 실제 데이터가
        # 준비된 뒤에도 다음 요청이 이 pending 스냅샷을 계속 돌려주게 됨.
        return JSONResponse(_clean_nan({**result, "favorites": favs, "cached": False}))
    result["daykey"] = _market_session_key(market)
    _cache[key] = result
    return JSONResponse(_clean_nan({**result, "favorites": favs, "cached": False}))


_inverse_cache: dict = {}


@app.get("/api/inverse")
async def inverse_scan(market: str = "all", refresh: bool = False):
    """인버스 ETF 스캔 — 지수 하락 베팅 종목.
    인버스가 강세(strong)면 = 시장 하락 국면 확인 + 매매 후보.
    일반 universe와 별개의 인버스 종목만 받아 analyze_inverse 실행."""
    market = market if market in ("kr", "us", "all") else "all"
    key = f"inv:{market}"
    cached = _inverse_cache.get(key)
    if cached and not refresh and time.time() - cached["ts"] < CACHE_TTL:
        return JSONResponse(_clean_nan({**cached, "cached": True}))

    inv = inverse_universe(market)
    us_tickers = [t for t, m in inv.items() if m["market"] == "US"]
    kr_tickers = [t for t, m in inv.items() if m["market"] == "KR"]

    data: dict = {}
    # 미국: 배치 fetch
    if us_tickers:
        try:
            data.update(_fetch_us_batch(us_tickers))
        except Exception:
            pass
    # 한국: 개별 fetch (네이버)
    for t in kr_tickers:
        try:
            df = _fetch(t)
            if df is not None and not df.empty:
                data[t] = df
        except Exception:
            continue

    # 1단계: derive_from 없는(기준) ETF 먼저 분석 → 결과 저장
    base_results: dict = {}
    for t, df in data.items():
        meta = inv.get(t, {})
        if meta.get("derive_from"):
            continue   # 파생(곱버스)은 2단계에서
        try:
            r = analyze_inverse(df, meta)
        except Exception:
            r = None
        if r is not None:
            base_results[t] = r

    hits = []
    # 기준 ETF 결과 추가
    for t, r in base_results.items():
        meta = inv.get(t, {})
        hits.append({"ticker": t, "market": meta.get("market", "US"), **r})

    # 2단계: 파생(곱버스 2x/3x)은 1x 기준에서 등락 역산 (네이버 거꾸로 데이터 우회)
    for t, meta in inv.items():
        src = meta.get("derive_from")
        if not src:
            continue
        base = base_results.get(src)
        if base is None:
            continue   # 1x 데이터 없으면 곱버스도 스킵
        lev = meta.get("leverage", 2)
        # 1x의 일간/5일 등락에 레버리지 배수 적용 (방향 동일, 폭만 N배)
        derived = dict(base)
        derived["name"] = meta["name"]
        derived["leverage"] = lev
        derived["underlying"] = meta.get("underlying", base.get("underlying", ""))
        derived["change_pct"] = round(base["change_pct"] * lev, 2)
        derived["ret5_pct"] = round(base["ret5_pct"] * lev, 1)
        derived["close"] = None       # 곱버스 실제가는 데이터 불신 → 표시 안 함
        derived["derived"] = True      # 1x에서 역산했음 표시
        derived["derived_from"] = src
        # 강도점수도 레버리지 반영한 5일 등락으로 재계산 (구조 신호는 1x와 동일 가정)
        derived["inv_score"] = inverse_score(
            base.get("aligned", False), base.get("above_ma20", False),
            base.get("ma20_slope_up", False), derived["ret5_pct"],
            base.get("vol_mult", 0.0), base.get("overheated", False))
        hits.append({"ticker": t, "market": meta.get("market", "US"), **derived})

    # ── 기초지수 5일 등락 (인버스가 왜 오르는지 직관적으로) ──
    # 인버스 데이터는 못 믿어도 기초지수(코스피/나스닥 등)는 정상 → 그걸 끌어다 씀.
    # underlying 이름 → 5일 등락% 캐시
    idx_5d: dict = {}
    def _index_5d_change(underlying: str) -> float | None:
        if underlying in idx_5d:
            return idx_5d[underlying]
        val = None
        try:
            if underlying in ("코스피200",):
                h = naver_kr.fetch_index_history("KOSPI", days=15)
            elif underlying in ("코스닥150",):
                h = naver_kr.fetch_index_history("KOSDAQ", days=15)
            else:
                sym = {"나스닥100": "^NDX", "S&P500": "^GSPC", "다우": "^DJI",
                       "러셀2000": "^RUT", "반도체": "^SOX", "VIX": "^VIX"}.get(underlying)
                h = None
                if sym:
                    try:
                        import yfinance as yf
                        h = yf.Ticker(sym).history(period="15d", interval="1d", auto_adjust=False)
                    except Exception:
                        h = None
            if h is not None and len(h) > 5:
                closes = h["Close"].dropna()
                if len(closes) > 5:
                    val = round((float(closes.iloc[-1]) / float(closes.iloc[-6]) - 1) * 100, 1)
        except Exception:
            val = None
        idx_5d[underlying] = val
        return val

    for hit in hits:
        hit["index_5d_pct"] = _index_5d_change(hit.get("underlying", ""))

    # 강도순 정렬: strong > building > weak, 같으면 강도점수 높은 순
    order = {"strong": 0, "building": 1, "weak": 2}
    hits.sort(key=lambda x: (order.get(x["strength"], 3), -x.get("inv_score", 0)))

    # 시장 국면 종합: strong 인버스가 많으면 하락장 확정
    strong_n = sum(1 for h in hits if h["strength"] == "strong")
    building_n = sum(1 for h in hits if h["strength"] == "building")
    if strong_n >= 3:
        regime = "bear"
        regime_txt = f"🔴 하락장 — 인버스 {strong_n}개 강세 (지수 약세 확인)"
    elif strong_n + building_n >= 3:
        regime = "weakening"
        regime_txt = f"🟡 약세 전환 조짐 — 인버스 {strong_n+building_n}개 상승 시도"
    else:
        regime = "ok"
        regime_txt = "🟢 지수 견조 — 인버스 부적합 (현금/롱 유지)"

    result = {
        "version": VERSION, "market": market,
        "hits": hits, "regime": regime, "regime_txt": regime_txt,
        "strong_count": strong_n,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }
    _inverse_cache[key] = result
    return JSONResponse(_clean_nan({**result, "cached": False}))


# ── 마감 후 자동 스캔 스케줄러 ──
# 한국 마감(15:40 KST)·미국 마감(06:00 KST) 직후, 해당 시장 데이터를 미리
# 받아 디스크 캐시를 채워둠. 사용자가 접속하기 전에 준비 완료 → 첫 로딩도 즉시.
# 동적 universe(거래대금 상위)도 이때 갱신됨.
_warmed: dict = {}  # {"kr:daykey": True} 중복 워밍 방지


_warm_intraday_ts: dict = {}   # {market: 마지막 장중 워밍 시각}


# ── 돈의 흐름 데일리 리포트 (v5.85, 사용자 지시) ──
# scanner.py의 매매 신호와 완전히 분리된 정보 레이어 — 진입 신호 아님.
# 1단계(money_flow.py, 거래대금 상위 100+테마 집계)와 2단계(money_flow_
# report.py, Claude API 해석)를 순차 실행. 스케줄러(장마감 후 1회)와
# 수동 재실행(POST /api/moneyflow/{market}/run) 공통 진입점.
_moneyflow_warmed: dict = {}  # {"kr:daykey": True} — 스캔 워밍과 별도 추적


async def _run_money_flow(market: str, daykey: str) -> dict:
    """1단계+2단계 실행 + 저장. 2단계(Claude) 실패는 예외를 안 던지고
    error 필드로만 남겨 호출부가 1단계 결과만으로도 항상 응답 가능하게
    한다(사용자 지시: "API 호출 실패 시 1단계 계산 결과만이라도 표시")."""
    bundle = await _fetch_market_data(market, wait_for_fresh=True)
    if not bundle:
        return {"error": f"{market} 시장 데이터 로드 실패", "snapshot": None, "markdown": None}
    methodology_note = (
        f"US 거래대금 상위 100은 미국 전체 시장이 아니라 이 앱의 US 유니버스"
        f"({len(bundle['universe'])}종목, 전 섹터 동적구성) 안에서의 순위입니다"
        " — 실제 미국 전체 시장 상위 100과 다를 수 있습니다(전체 시장 API 없음)."
    ) if market == "us" else ""
    loop = asyncio.get_event_loop()
    snapshot = await loop.run_in_executor(
        _executor, money_flow.run_daily, market, daykey,
        bundle["data"], bundle["universe"], _sector_of, methodology_note,
    )
    markdown, error = await loop.run_in_executor(_executor, money_flow_report.generate_report, snapshot)
    if markdown:
        money_flow.save_report_markdown(market, daykey, markdown)
        print(f"[moneyflow] {market} {daykey} 리포트 생성 완료")
    else:
        print(f"[moneyflow] {market} {daykey} 2단계 실패(1단계만 저장됨): {error}")
    # v5.100(사용자 지시 4번): KR 강세 테마(확산 단계 진입 또는 streak
    # 2일+) 감지 시, 매핑 없는(또는 30일 경과) 테마만 자동 생성. US는
    # theme_map이 "KR 관련주" 전용(사용자 지시 1번)이라 대상 아님.
    if market == "kr":
        try:
            candidate_themes = [
                name for name, info in (snapshot.get("themes") or {}).items()
                if info.get("stage") == "확산(본격)" or (info.get("streak_days") or 0) >= 2
            ]
            if candidate_themes:
                generated = await loop.run_in_executor(
                    _executor, theme_map.maybe_auto_generate, candidate_themes, bundle["universe"],
                )
                if generated:
                    print(f"[theme_map] {daykey} 자동 생성: {generated}")
        except Exception as e:
            print(f"[theme_map] 자동 생성 트리거 실패: {e}")
    return {"snapshot": snapshot, "markdown": markdown, "error": error}


async def _run_money_flow_bg(market: str, daykey: str):
    """스케줄러 전용 fire-and-forget 래퍼 — create_task로 던지므로 예외를
    여기서 잡아 로그만 남긴다(안 잡으면 조용히 삼켜지는 asyncio 기본 동작
    대신 원인 파악 가능하게)."""
    try:
        await _run_money_flow(market, daykey)
    except Exception as e:
        print(f"[moneyflow] scheduler run failed {market} {daykey}: {e}")


async def _warm_market(market: str):
    """해당 시장 데이터+주요 모드 결과를 미리 빌드(캐시 저장).
    v4.52.5: 장 마감 후뿐 아니라 '장중에도' 프리로드.
    장중엔 콜드 스캔(3~4분)이 사용자 요청 때 실행되면 브라우저 타임아웃으로
    '스캔 실패'가 뜸 → 스케줄러가 미리 데워두면 사용자는 항상 캐시 히트."""
    daykey = _market_session_key(market)
    if daykey:
        # v5.99: 비개장일(주말/공휴일)이면 여기서 전부 스킵 — daykey는
        # 이미 확정돼 있지만(_market_session_key가 요일/시각만 봄) 실제로
        # 그 시장이 안 열렸으면 재웜/리포트생성을 안 한다. 기존 캐시(직전
        # 진짜 거래일 데이터)는 그대로 남아있어 사용자에게 문제 없음 —
        # "데이터 안 바뀜"이라는 원래 의도(아래 주석)를 코드로 실제 구현.
        if not is_trading_day(market, daykey):
            return
        # ── 장 마감 후: 하루 1회만 데우면 됨 (데이터 고정) ──
        wkey = f"{market}:{daykey}"
        if _warmed.get(wkey):
            return
        try:
            bundle = await _fetch_market_data(market, wait_for_fresh=True)
            for mode in ("imminent", "pullback", "turnaround", "breakout"):
                res = await run_scan(market, mode)
                res["daykey"] = daykey
                _cache[f"{market}:{mode}"] = res
            _warmed[wkey] = True
            print(f"[scheduler] warmed {market} for {daykey}")
            # v5.98: 종가베팅 포워드 트래킹 — 장마감 확정 시점에 오늘자
            # 후보들의 확정 종가 기록 + 어제 이전 미확정 레코드 갱신 시도.
            if market == "kr" and bundle:
                try:
                    _record_jongga_eod(daykey, bundle["data"])
                    _resolve_jongga_gaps(bundle["data"])
                except Exception as e2:
                    print(f"[jongga-forward] EOD 처리 실패: {e2}")
                # v5.125: 전 리더 재점화 워치리스트 — 장마감 확정 시점에
                # 창 진입/이탈·확인진입·포워드 R을 하루 1회 갱신(장중 실시간
                # 부분봉으로 돌파/거래량을 판정하면 v5.117류 오염 위험이라
                # EOD 확정 데이터에서만 판정, 사용자 지시 없이 실시간화 안 함).
                try:
                    await _refresh_reignition_watch(bundle)
                except Exception as e2:
                    print(f"[reignition] EOD 갱신 실패: {e2}")
        except Exception as e:
            print(f"[scheduler] warm {market} failed: {e}")
        # 돈의 흐름은 스캔 워밍과 독립 추적 — 스캔 워밍 실패와 무관하게 시도,
        # 서로의 실패가 전파되지 않음. Claude API 호출이 오래(10~30초+) 걸릴
        # 수 있어 create_task로 던져 4분 주기 스케줄러 루프를 막지 않는다.
        mfkey = f"{market}:{daykey}"
        if not _moneyflow_warmed.get(mfkey):
            _moneyflow_warmed[mfkey] = True
            asyncio.create_task(_run_money_flow_bg(market, daykey))
        return
    # ── 장중: 캐시가 8분 이상 묵었으면 미리 갱신 (사용자 요청 전에) ──
    # DATA_TTL(10분)보다 짧은 주기로 데워, 사용자가 열 때 항상 신선한 캐시 확보.
    now = time.time()
    last = _warm_intraday_ts.get(market, 0)
    if now - last < 480:      # 8분
        return
    try:
        bundle = await _fetch_market_data(market, wait_for_fresh=True)
        for mode in ("imminent", "pullback", "turnaround", "breakout"):
            res = await run_scan(market, mode)
            res["daykey"] = None
            res["ts"] = time.time()
            _cache[f"{market}:{mode}"] = res
        _warm_intraday_ts[market] = now
        print(f"[scheduler] intraday-warmed {market}")
        # v5.98: 익일 장 시작 후 첫(이후 매) 장중 워밍마다 — 과거 미확정
        # 종가베팅 레코드에 오늘 시가가 들어왔는지 확인해 갭 확정.
        if market == "kr" and bundle:
            try:
                _resolve_jongga_gaps(bundle["data"])
            except Exception as e2:
                print(f"[jongga-forward] 장중 갭 확정 실패: {e2}")
    except Exception as e:
        print(f"[scheduler] intraday warm {market} failed: {e}")


_MCAP_MIN_EOK = 1000  # 시총 1000억원 미만 국장 종목은 스캔 제외 (v4.91)
_mcap_allowed_cache: dict = {}   # {"date": "YYYY-MM-DD", "tickers": set(...)}
_mcap_fetch_in_progress = False


def _get_mcap_allowed() -> set:
    """오늘자로 준비된 시총 허용목록. 아직 없으면 빈 set(필터 없이 통과 — fail-open)."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if _mcap_allowed_cache.get("date") == today:
        return _mcap_allowed_cache.get("tickers", set())
    return set()


async def _ensure_mcap_allowed():
    """시총 허용목록을 하루 1회 백그라운드로 채움 (블로킹 스크레이핑이라 executor에서)."""
    global _mcap_fetch_in_progress
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if _mcap_allowed_cache.get("date") == today or _mcap_fetch_in_progress:
        return
    _mcap_fetch_in_progress = True
    try:
        loop = asyncio.get_event_loop()
        allowed = await loop.run_in_executor(
            _executor, naver_kr.fetch_high_marketcap_allowed, _MCAP_MIN_EOK
        )
        if allowed:
            _mcap_allowed_cache["date"] = today
            _mcap_allowed_cache["tickers"] = allowed
            print(f"[mcap] {today} 시총 {_MCAP_MIN_EOK}억↑ 허용목록 {len(allowed)}종목")
    except Exception as e:
        print(f"[mcap] fetch failed: {e}")
    finally:
        _mcap_fetch_in_progress = False


_jongga_snapshot_date: str | None = None   # 오늘 자 종가베팅 스냅샷 실행 여부(날짜 키)


async def _maybe_run_jongga_snapshot():
    """종가베팅 장중 스냅샷 — 평일 KST 14:40~15:00 사이 1회만 실행(사용자
    지시 3번). 4분 주기 스케줄러 루프에 얹혀서 그 창 안에 최소 4~5회
    깨어나므로 4분 해상도로도 창을 놓치지 않는다. 결과는 일반 /api/scan
    캐시(_cache["kr:jongga"])에 그대로 저장 — 프론트가 열든 안 열든 이
    시각의 스냅샷이 확보된다. 텔레그램 발송(사용자 지시 7번)은 이 레포에
    봇 코드가 없어(얼마냐봇은 외부 레포, /api/ma/{ticker}만 폴링 —
    CLAUDE.md) 여기서는 못 하고, 로그 + /api/jongga/candidates 엔드포인트
    (봇이 폴링해서 자체적으로 보내야 함)로 대체."""
    global _jongga_snapshot_date
    now = datetime.now(KST)
    if not is_trading_day("kr", now):   # v5.99: 주말+공휴일(기존엔 주말만)
        return
    hm = now.hour * 60 + now.minute
    if not (14 * 60 + 40 <= hm < 15 * 60):
        return
    today = now.strftime("%Y-%m-%d")
    if _jongga_snapshot_date == today:
        return
    try:
        bundle = await _fetch_market_data("kr", wait_for_fresh=True)
        if not bundle:
            return
        result = await _run_scan_jongga(bundle)
        result["daykey"] = _market_session_key("kr")
        _cache["kr:jongga"] = result
        _jongga_snapshot_date = today
        print(f"[jongga] {today} 장중 스냅샷 완료 — 후보 {len(result['hits'])}개")
        try:
            _record_jongga_snapshot(today, result["hits"])  # v5.98 포워드 트래킹 기록
        except Exception as e2:
            print(f"[jongga-forward] 스냅샷 기록 실패: {e2}")
    except Exception as e:
        print(f"[jongga] 장중 스냅샷 실패: {e}")


async def _scheduler_loop():
    """4분마다 깨어나 각 시장을 워밍.
    - 장 마감 후: 하루 1회 (데이터 고정)
    - 장중: 8분 이상 묵은 캐시를 미리 갱신 → 사용자는 항상 캐시 히트,
            콜드 스캔으로 인한 '스캔 실패'가 사라짐.
    - 🇰🇷 종가베팅: 14:40~15:00 사이 1회 스냅샷(v5.97, 아래 참고)."""
    await asyncio.sleep(20)  # 부팅 직후 잠깐 대기
    while True:
        try:
            asyncio.create_task(_ensure_mcap_allowed())  # 하루 1회, 워밍과 별개로 진행
            for market in ("kr", "us"):
                await _warm_market(market)
            await _maybe_run_jongga_snapshot()
            _maybe_refresh_macro_calendar()   # v5.108: 캘린더 탭 매크로 일정, 주 1회
        except Exception as e:
            print(f"[scheduler] loop error: {e}")
        await asyncio.sleep(240)  # 4분


@app.on_event("startup")
async def _start_scheduler():
    asyncio.create_task(_scheduler_loop())


def _resolve_persistent_path(filename: str) -> str:
    """지정 파일명을 영구 볼륨(/data) 우선으로 저장할 경로 결정 + 앱 폴더에
    옛 파일이 있으면 1회 마이그레이션 (v5.49). journal(_resolve_journal_path,
    아래쪽에 정의)과 같은 이유 — Railway는 재배포 시 앱 코드 폴더를 새로
    체크아웃해서 거기 저장된 파일은 날아가지만 /data(마운트된 영구 볼륨)는
    유지된다. favorites_user.txt/alerts_user.txt가 이 로직 없이 앱 폴더에만
    저장돼 재배포마다 사라질 수 있던 버그 수정 — 경보종목은 수동으로 매핑해둔
    거라 날아가면 복구가 번거로움.
    우선순위: 1) 환경변수 JOURNAL_DIR(journal과 같은 볼륨 재사용)
              2) /data  3) 앱 폴더(로컬 개발 폴백, 배포 시엔 휘발)
    """
    candidates = []
    env_dir = os.environ.get("JOURNAL_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates.append("/data")
    candidates.append(os.path.dirname(__file__))
    resolved = None
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.remove(test)
            resolved = os.path.join(d, filename)
            break
        except OSError:
            continue
    if resolved is None:
        resolved = os.path.join(os.path.dirname(__file__), filename)
    # 마이그레이션: 새 경로에 파일이 아직 없고, 앱 폴더(구 경로)에 기존 파일이
    # 있으면 1회 복사 — 지금까지 앱 폴더에 쌓인 즐겨찾기/경보종목을 보존.
    old_path = os.path.join(os.path.dirname(__file__), filename)
    if resolved != old_path and not os.path.exists(resolved) and os.path.exists(old_path):
        try:
            import shutil
            shutil.copy2(old_path, resolved)
        except OSError:
            pass
    return resolved


ALERTS_USER_PATH = _resolve_persistent_path("alerts_user.txt")
JONGGA_FORWARD_PATH = _resolve_persistent_path("jongga_forward.json")  # v5.98 포워드 트래킹
REIGNITION_WATCH_PATH = _resolve_persistent_path("reignition_watch.json")  # v5.125 재점화 워치리스트
# v5.103: 포지션 보드 — 토스 잔고 동기화(수량·평단, sync_toss.py가 30분마다
# 덮어씀)와 사용자가 UI에서 입력하는 손절가(positions_meta.json, sync가
# 절대 안 건드림)를 별도 파일로 분리. 같은 파일에 같이 두면 다음 동기화가
# 손절가까지 통째로 지워버림 — journal의 전체배열 덮어쓰기 저장과 같은
# 함정([버그수정] v5.102 저널 저장 경쟁 사고)이라 애초에 파일을 나눠 피한다.
POSITIONS_PATH = _resolve_persistent_path("positions.json")
POSITIONS_META_PATH = _resolve_persistent_path("positions_meta.json")
# v5.104: sync_toss.py가 토스 API IP 미허용(403)으로 실패하면 여기에 기록.
# 발송(텔레그램 알림)은 이 레포 책임이 아니다 — 얼마냐봇이 /api/positions의
# sync_error 필드를 폴링해서 자체적으로 처리한다(dedup 포함).
SYNC_ERROR_PATH = _resolve_persistent_path("sync_error.json")


@app.get("/api/alerts")
async def get_alerts():
    return JSONResponse(load_alerts())


@app.post("/api/alerts")
async def add_alert(request: Request):
    """대시보드에서 경보 종목 추가/삭제. {ticker, kind} 또는 {ticker, remove:true}"""
    body = await request.json()
    raw = (body.get("ticker") or "").strip()
    if not raw:
        return JSONResponse({"ok": False, "error": "ticker 필요"}, status_code=400)
    kind = body.get("kind", "경보")
    remove = body.get("remove", False)

    if remove:
        # 삭제는 항상 목록에 이미 저장된 정확한 티커로만 호출됨(클라이언트가
        # dataset에서 그대로 넘김) — 이름 재해석 불필요, 오히려 위험(엉뚱한
        # 후보로 잘못 삭제될 수 있음).
        ticker = raw.upper()
    else:
        # v5.112: 여기도 검색창과 동일한 단일 지점으로 이름/코드를 해석 —
        # 안 그러면 "삼성전자"가 그대로 대문자화된 문자열로 저장돼 어떤
        # 스캔에도 안 걸리는 조용한 실패가 됨(사용자 보고 사례).
        from universe import resolve_name_to_ticker
        _uni_for_alert = get_universe(None)
        res = resolve_name_to_ticker(raw, _uni_for_alert)
        if res["candidates"]:
            return JSONResponse({"ok": False, "candidates": res["candidates"], "query": raw})
        if not res["ticker"]:
            msg = ("이름을 못 찾았어요 — 코드로 시도해보세요."
                   if res["reason"] == "name_not_found" else "유니버스에 없어요.")
            return JSONResponse({"ok": False, "error": msg, "reason": res["reason"]})
        ticker = res["ticker"]

    # alerts_user.txt 읽기 → 수정 → 쓰기
    entries = {}
    if os.path.exists(ALERTS_USER_PATH):
        with open(ALERTS_USER_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=1)
                entries[parts[0].upper()] = parts[1] if len(parts) > 1 else "경보"
    if remove:
        entries.pop(ticker, None)
    else:
        entries[ticker] = kind
    with open(ALERTS_USER_PATH, "w", encoding="utf-8") as f:
        f.write("# 대시보드에서 추가한 경보 종목 (자동 생성)\n")
        for tk, kd in sorted(entries.items()):
            f.write(f"{tk} {kd}\n")
    # 캐시 무효화 (다음 스캔에 반영)
    _cache.clear()
    return JSONResponse({"ok": True, "alerts": entries})


# ── 즐겨찾기 (서버 저장, alerts와 동일 패턴) ──
FAVORITES_PATH = _resolve_persistent_path("favorites_user.txt")


def load_favorites() -> list[str]:
    """즐겨찾기 티커 목록 (대문자)."""
    favs = []
    if os.path.exists(FAVORITES_PATH):
        with open(FAVORITES_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                favs.append(line.split()[0].upper())
    return favs


@app.get("/api/favorites")
async def get_favorites():
    return JSONResponse(load_favorites())


@app.post("/api/favorites")
async def toggle_favorite(request: Request):
    """즐겨찾기 추가/삭제. {ticker, remove?:bool}. remove 없으면 토글."""
    body = await request.json()
    ticker = (body.get("ticker") or "").upper().strip()
    if not ticker:
        return JSONResponse({"ok": False, "error": "ticker 필요"}, status_code=400)
    favs = load_favorites()
    remove = body.get("remove")
    if remove is None:
        remove = ticker in favs  # 없으면 토글
    if remove:
        favs = [f for f in favs if f != ticker]
    elif ticker not in favs:
        favs.append(ticker)
    with open(FAVORITES_PATH, "w", encoding="utf-8") as f:
        f.write("# 대시보드 즐겨찾기 (자동 생성)\n")
        for tk in favs:
            f.write(f"{tk}\n")
    return JSONResponse({"ok": True, "favorites": favs})


# ── 종목 숨김 (스캐너 카드 표시 필터, alerts/favorites와 동일 패턴) ──
# v5.72: 순수 표시 필터 — 스캔/게이트/저널/하네스 로직은 이 스토어를 전혀
# 참조하지 않는다(프론트엔드가 /api/hidden 목록으로 카드를 걸러낼 뿐).
HIDDEN_PATH = _resolve_persistent_path("hidden_user.txt")
HIDDEN_EXPIRE_DAYS = 90


def load_hidden() -> dict:
    """숨긴 종목 {TICKER: {"hidden_at": iso, "name": str}}. 90일 지난 항목은
    여기서 걸러내고(만료된 레코드는 조회 시 정리) 남은 게 있으면 파일도
    다시 씀."""
    entries = {}
    if os.path.exists(HIDDEN_PATH):
        with open(HIDDEN_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=2)
                if len(parts) < 2:
                    continue
                ticker = parts[0].upper()
                entries[ticker] = {
                    "hidden_at": parts[1],
                    "name": parts[2] if len(parts) > 2 else ticker,
                }
    now = datetime.now(timezone.utc)
    alive = {}
    expired = False
    for ticker, info in entries.items():
        try:
            hidden_dt = datetime.fromisoformat(info["hidden_at"])
        except ValueError:
            expired = True  # 손상된 레코드도 정리 대상
            continue
        if (now - hidden_dt).days >= HIDDEN_EXPIRE_DAYS:
            expired = True
            continue
        alive[ticker] = info
    if expired:
        _save_hidden(alive)
    return alive


def _save_hidden(entries: dict):
    with open(HIDDEN_PATH, "w", encoding="utf-8") as f:
        f.write("# 숨긴 종목 (대시보드에서 자동 생성) — 90일 지나면 자동 만료\n")
        for ticker, info in sorted(entries.items()):
            f.write(f"{ticker} {info['hidden_at']} {info.get('name', ticker)}\n")


@app.get("/api/hidden")
async def get_hidden():
    """숨긴 종목 목록. 만료(90일 경과) 항목은 load_hidden()이 조회 시점에
    걸러내고 파일에서도 지운다."""
    entries = load_hidden()
    now = datetime.now(timezone.utc)
    items = []
    for ticker, info in entries.items():
        hidden_dt = datetime.fromisoformat(info["hidden_at"])
        days_left = max(0, HIDDEN_EXPIRE_DAYS - (now - hidden_dt).days)
        items.append({
            "ticker": ticker,
            "name": info.get("name", ticker),
            "hidden_at": info["hidden_at"],
            "days_left": days_left,
        })
    items.sort(key=lambda x: x["hidden_at"], reverse=True)
    return JSONResponse(items)


@app.post("/api/hidden/{ticker}")
async def hide_ticker(ticker: str, request: Request):
    ticker = ticker.upper().strip()
    if not ticker:
        return JSONResponse({"ok": False, "error": "ticker 필요"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = (body or {}).get("name") or ticker
    entries = load_hidden()
    entries[ticker] = {"hidden_at": datetime.now(timezone.utc).isoformat(), "name": name}
    _save_hidden(entries)
    return JSONResponse({"ok": True})


@app.delete("/api/hidden/{ticker}")
async def unhide_ticker(ticker: str):
    ticker = ticker.upper().strip()
    entries = load_hidden()
    entries.pop(ticker, None)
    _save_hidden(entries)
    return JSONResponse({"ok": True})


@app.get("/api/debugraw/{ticker}")
async def debug_raw(ticker: str):
    """한국 종목 raw 데이터 점검: 일봉 마지막 5개 + 장중 현재가 + 병합 결과.
    곱버스 등 데이터 거꾸로 문제 진단용. 예: /api/debugraw/291630.KS"""
    import naver_kr
    out = {"ticker": ticker}
    try:
        hist = naver_kr.fetch_history(ticker)
        if hist is not None and not hist.empty:
            tail = hist.tail(5)
            out["history_last5"] = [
                {"date": str(idx.date()), "close": float(row["Close"]),
                 "open": float(row["Open"]), "volume": float(row["Volume"])}
                for idx, row in tail.iterrows()
            ]
        else:
            out["history_last5"] = None
    except Exception as e:
        out["history_error"] = str(e)
    try:
        out["live_price"] = naver_kr.fetch_live_price(ticker)
    except Exception as e:
        out["live_error"] = str(e)
    try:
        merged = naver_kr.fetch(ticker)
        if merged is not None and not merged.empty:
            out["merged_last_close"] = float(merged.iloc[-1]["Close"])
            out["merged_last_date"] = str(merged.index[-1].date())
    except Exception as e:
        out["merged_error"] = str(e)
    return JSONResponse(out)


_sectors_cache: dict = {}   # {"ts":, "closed_daykey":, "data": {...}} — v4.99


@app.get("/api/sectors")
async def api_sectors():
    """전일(마지막 봉) 섹터별 강세 요약 — 코스피/코스닥/미국 3패널.
    유니버스 전 종목의 마지막 봉 등락률을 섹터로 묶어 평균·상승비율·상위 종목 집계.
    v4.44.0. 섹터 매핑된 종목 기준(기타 제외).

    v4.99 [버그수정] 이름대로 "전일" 요약이어야 하는데, 실제론 마지막 봉을 그때
    그때 다시 계산해서 장중엔 그날 진행 중인(미확정) 등락률이 계속 반영됐음
    (사용자 피드백: 장 열리면 전날 자료 그대로 유지해야 하는데 장중에 계속
    로딩된다). /api/eod와 같은 방식으로 KR장이 아직 마감 전(_market_session_key
    가 None)이면 마지막 마감 확정 스냅샷을 그대로 반환 — 재계산 안 함."""
    daykey = _market_session_key("kr")   # None이면 오늘 KR장 아직 마감 전
    if daykey is None and _sectors_cache.get("data"):
        return JSONResponse(_sectors_cache["data"])
    if daykey and _sectors_cache.get("closed_daykey") == daykey:
        return JSONResponse(_sectors_cache["data"])

    bundle = await _fetch_market_data("all")
    if bundle is None:
        # v5.12: 콜드 스타트라 백그라운드로 수집 중 — 잠시 후 재요청하면 됨.
        return {"version": VERSION, "pending": True, "asof": "", "panels": {}}
    data = bundle["data"]
    universe = bundle["universe"]
    rs_ranks = bundle.get("rs_ranks", {})
    panels: dict[str, dict[str, list]] = {"KOSPI": {}, "KOSDAQ": {}, "US": {}}
    # ── 주도업종 집계 (v4.49, 생존자 카운트 방식) ──
    # 하루 등락 평균은 노이즈 — "RS 85+ AND 3개월 +30% AND 200일선 위" 생존자가
    # 어느 업종에 몰렸는지가 로테이션의 진짜 신호. (앤트킹 스크린 방식 차용)
    leading: dict[str, dict[str, list]] = {"KR": {}, "US": {}}
    for t, df in data.items():
        if df is None or len(df) < 2:
            continue
        try:
            c = df["Close"]
            # v4.50.4: 전일종가가 '실제 최근 거래일'인지 확인.
            # 야후가 최근 데이터를 통째로 결측 처리하면 옛날 유효값(예: MQ 2022년
            # 4.18)이 iloc[-2]로 밀려와 +316% 유령 등락 발생. 인덱스 날짜로 검증.
            last, prev = float(c.iloc[-1]), float(c.iloc[-2])
            try:
                gap_days = (c.index[-1] - c.index[-2]).days
                if gap_days > 7:      # 전일종가가 7일 넘게 과거 → 데이터 공백
                    continue
            except Exception:
                pass
        except Exception:
            continue
        if prev <= 0:
            continue
        chg = (last / prev - 1) * 100
        # 안전망 (v4.50.3, 근본원인은 v4.50.4 _downcast에서 해결).
        # Close 결측행 제거로 유령 등락은 원천 차단됐지만, 야후가 조정 전/후
        # 가격을 섞어 보내는 등 예상 못한 데이터 오염이 재발할 수 있으므로
        # 물리적으로 불가능한 등락(KR ±31% / US +100%~-60%)은 집계에서 배제.
        is_kr_t = t.endswith((".KS", ".KQ"))
        hi_lim = 31.0 if is_kr_t else 100.0
        lo_lim = -31.0 if is_kr_t else -60.0
        if chg > hi_lim or chg < lo_lim:
            continue
        sec = _sector_of(t)
        if sec == "기타":
            continue
        panel = "KOSPI" if t.endswith(".KS") else ("KOSDAQ" if t.endswith(".KQ") else "US")
        panels[panel].setdefault(sec, []).append((t, universe.get(t, t), chg))
        # 주도주 판정 (KR/US 통합 패널)
        try:
            rs = rs_ranks.get(t)
            if rs is not None and rs >= 85 and len(c) >= 200:
                base63 = float(c.iloc[-64]) if len(c) >= 64 else 0.0
                m3 = last / base63 - 1.0 if base63 > 0 else 0.0
                ma200 = float(c.rolling(200).mean().iloc[-1])
                if m3 >= 0.30 and last > ma200:
                    mkt = "KR" if t.endswith((".KS", ".KQ")) else "US"
                    leading[mkt].setdefault(sec, []).append(
                        (t, universe.get(t, t), int(rs), round(m3 * 100, 1)))
        except Exception:
            pass
    # 주도업종 랭킹: 생존자 수 내림차순, 업종당 상위 3종목(RS순)
    leading_out = {}
    for mkt, groups in leading.items():
        rows = []
        for sec, items in groups.items():
            items.sort(key=lambda x: x[2], reverse=True)
            rows.append({
                "sector": sec, "n": len(items),
                "top": [{"ticker": tk, "name": nm, "rs": rs, "mom3": m3}
                        for tk, nm, rs, m3 in items[:3]],
            })
        rows.sort(key=lambda r: r["n"], reverse=True)
        leading_out[mkt] = rows[:8]

    out = {"leading": leading_out}
    for pname, groups in panels.items():
        min_n = 2 if pname != "US" else 3   # 국내는 섹터당 종목 적어 완화
        rows = []
        for sec, items in groups.items():
            if len(items) < min_n:
                continue
            chgs = [x[2] for x in items]
            avg = sum(chgs) / len(chgs)
            up = sum(1 for x in chgs if x > 0) * 100 // len(chgs)
            top = sorted(items, key=lambda x: x[2], reverse=True)[:5]
            rows.append({
                "sector": sec, "n": len(items),
                "avg_chg": round(avg, 2), "up_pct": up,
                "top": [{"ticker": tk, "name": nm, "chg": round(cg, 1)}
                        for tk, nm, cg in top],
            })
        rows.sort(key=lambda r: r["avg_chg"], reverse=True)
        out[pname] = rows
    ts = bundle.get("ts")
    asof = datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M") if ts else ""
    result = {"version": VERSION, "asof": asof, "panels": out}
    _sectors_cache["data"] = result
    if daykey:
        # 마감 확정된 결과만 스냅샷으로 표시 — 다음 장중엔 이 스냅샷을 그대로 재사용.
        _sectors_cache["closed_daykey"] = daykey
    return result


@app.get("/api/lookup/{ticker}")
async def lookup_ticker(ticker: str):
    """검색 전용: 종목이 어느 탭 조건에 안 맞아도 핵심 지표를 반환.
    프론트 검색에서 현재 탭 결과에 없을 때 이걸로 종목 데이터를 조회.
    예: /api/lookup/BIIB"""
    from universe import resolve_name_to_ticker
    query = ticker.strip()
    _uni = get_universe(None)
    res = resolve_name_to_ticker(query, _uni)
    if res["candidates"]:
        return JSONResponse({"candidates": res["candidates"], "query": query})
    if not res["ticker"]:
        # v5.112: 실패 사유를 구분 — 조용히 "없다"로만 뭉개지 않는다
        msg = ("이름을 못 찾았어요 — 코드로 시도해보세요."
               if res["reason"] == "name_not_found" else "유니버스에 없어요.")
        return JSONResponse({"error": msg, "reason": res["reason"], "query": query})
    ticker = res["ticker"]
    name = _uni.get(ticker, ticker)
    df = await asyncio.get_event_loop().run_in_executor(_executor, _fetch, ticker)
    if df is None or df.empty or len(df) < 60:
        return JSONResponse({"error": "데이터 없음 또는 부족", "ticker": ticker, "name": name})

    is_kr = ticker.endswith((".KS", ".KQ"))
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    close = float(c.iloc[-1])
    prev = float(c.iloc[-2]) if len(c) >= 2 else close
    change_pct = round((close / prev - 1) * 100, 2) if prev > 0 else 0.0

    ma20 = float(c.rolling(20).mean().iloc[-1])
    ma60 = float(c.rolling(60).mean().iloc[-1])
    ma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    ma200_dist = round((close / ma200 - 1) * 100, 1) if ma200 else None

    # 정배열 여부
    aligned = bool((close > ma20 > ma60) and (ma200 is None or ma60 > ma200))
    # 거래량
    vol_today = float(v.iloc[-1])
    vol_avg50 = float(v.iloc[-51:-1].mean()) if len(v) >= 51 else float(v.mean())
    vol_mult = round(vol_today / vol_avg50, 2) if vol_avg50 > 0 else None
    turnover = round(close * vol_today)
    # RSI(14)
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        rsi = 100.0
    else:
        rsi = float((100 - 100 / (1 + gain / loss)).iloc[-1])
    # U/D Volume (50일)
    from scanner import up_down_volume
    ud = up_down_volume(c, v, 50)

    # 52주 고저 대비 위치
    hi52 = float(c.iloc[-252:].max()) if len(c) >= 252 else float(c.max())
    lo52 = float(c.iloc[-252:].min()) if len(c) >= 252 else float(c.min())
    from_high = round((close / hi52 - 1) * 100, 1) if hi52 > 0 else None
    from_low = round((close / lo52 - 1) * 100, 1) if lo52 > 0 else None

    payload = {
        "ticker": ticker, "name": name,
        "market": "KR" if is_kr else "US",
        "close": close, "change_pct": change_pct,
        "ma20": round(ma20), "ma60": round(ma60),
        "ma200": round(ma200) if ma200 else None,
        "ma200_dist": ma200_dist,
        "aligned": aligned,
        "vol_mult": vol_mult, "turnover": turnover,
        "rsi": round(rsi, 1), "ud": ud,
        "from_high": from_high, "from_low": from_low,
    }

    # v5.113: 검색 결과를 실제 탭 히트와 같은 카드 렌더러(static/index.html
    # card())로 그릴 수 있게 카드가 요구하는 필드를 채워 넣는다 — 예전엔
    # renderLookupCard()가 이 지표 6개만 보여주고 진입 참고 수치·액션 버튼이
    # 아예 없었음(사용자 지시). "돌파 시 참고 수치"는 실거래 게이트가 아니라
    # 참고용 가정 계산이므로 mode="search"로 명시하고, 실제 게이트가 쓰는
    # 것과 동일한 함수(horizontal_levels/_rr_block/CONFIG의 ATR×1.5)를 그대로
    # 재사용해 손절 배수를 새로 만들지 않는다.
    from scanner import horizontal_levels, atr as _atr, _rr_block, CONFIG as _SCANNER_CONFIG, volume_info
    payload["mode"] = "search"
    payload["is_search_result"] = True
    payload["ud_vol"] = ud
    try:
        payload["atr_pct"] = round(_atr(h, lo, c) / close * 100, 2) if close > 0 else None
    except Exception:
        payload["atr_pct"] = None
    try:
        vi = volume_info(close, v)
        payload["volume"] = vi.get("volume")
        payload["vol_vs_avg"] = vi.get("vol_vs_avg")
    except Exception:
        payload["volume"] = vol_today
    try:
        payload["spark"] = [round(float(x), 4) for x in c.iloc[-60:].tolist()]
        payload["spark_ma20"] = [
            None if math.isnan(x) else round(float(x), 4)
            for x in c.rolling(20).mean().iloc[-60:].tolist()
        ]
    except Exception:
        payload["spark"], payload["spark_ma20"] = [], []
    # v5.61과 동일한 근사 RS 폴백 — 유니버스 캐시가 따뜻하면 정식 percentile.
    try:
        _bundle = await _fetch_market_data("all")
        _real_rs = _bundle["rs_ranks"].get(ticker) if _bundle else None
        payload["rs"] = _real_rs if _real_rs is not None else 80
        payload["rs_is_approx"] = _real_rs is None
    except Exception:
        payload["rs"] = 80
        payload["rs_is_approx"] = True
    # "이 종목이 뜨려면 얼마가 돼야 하는가" — 가장 가까운 상단 저항을 가정
    # 피벗으로 삼아 돌파 시 진입/손절/리스크/손익비를 계산(참고용, 게이트 아님).
    try:
        hl = horizontal_levels(h, lo, c)
        payload["수평저항"] = {
            "추천피벗": hl["pivot"], "피벗_터치횟수": hl["pivot_touches"],
            "저항": [{"price": r["price"], "touches": r["touches"], "dist_pct": r["dist_pct"]} for r in hl["resistances"][:4]],
            "지지": [{"price": s["price"], "touches": s["touches"], "dist_pct": s["dist_pct"]} for s in hl["supports"][:3]],
        }
        pivot = hl["pivot"]
        if pivot:
            atr_val = _atr(h, lo, c)
            raw_stop = pivot - atr_val * _SCANNER_CONFIG["risk_hard_atr_mult"]
            rrb = _rr_block(pivot, raw_stop, h, lo, c, entry=pivot, is_kr=is_kr)
            payload["pivot"] = round(pivot, 2)
            payload["pivot_type"] = "참고(수평저항)"
            payload["entry_basis"] = "돌파 시(가정)"
            payload["stop"] = rrb["stop"]
            payload["risk_pct"] = rrb["risk_pct"]
            payload["rr"] = rrb["rr"]
            payload["target"] = rrb["target"]
            payload["target_basis"] = rrb["target_basis"]
            payload["hypothetical_entry"] = True
    except Exception as _e:
        payload["수평저항"] = {"error": str(_e)}
    return JSONResponse(payload)


# v5.39: /api/debug의 "탈락_핵심사유"가 실제 스캔이 안 쓰는 값(box_info=단순
# 최고가)으로 사유를 추측하고 있었음 — 실제로는 통과한 게이트(select_pivot의
# 진짜 피벗)를 탈락 사유로 잘못 지목한 사례(DELL 486 vs 실제 447.88) 확인 후
# 각 탭의 실제 게이트를 순서대로 그대로 재현해 "어디서 True/False로 return
# None 됐는지"를 정확히 추적하는 함수로 교체. scanner.py 원본 함수의 게이트
# 순서·조건이 바뀌면 이 트레이서도 같이 업데이트해야 함(단일 소스가 아니라
# 재현이라 드리프트 위험 — analyze/analyze_turnaround/analyze_breakout/
# analyze_boxbreak/analyze_imminent 본체와 대조해서 유지보수할 것).
#
# [v5.63] 이 사본이 존재하는 이유: analyze_*가 게이트 탈락 시 None만 반환해서
# 진단 정보가 안 남기 때문(v5.39 당시 핫패스 성능 선택 — 구조적 제약은
# 아님). analyze_*에 trace: list | None = None 선택적 파라미터를 추가하면
# (기본 None → 실제 스캔 동작 무변화) 이 사본을 없앨 수 있지만, 실거래
# 함수 5개의 게이트 본문을 건드려야 해서 리스크가 크다 — test_trace_
# parity.py(app.py의 _trace_*와 scanner.py의 analyze_*를 실제 종목으로
# 돌려 stop/risk_pct 값 불일치를 잡는 차등 테스트)가 있는 한 지금은 보류.
# 다음 중 하나라도 해당되면 그때는 제거를 실행할 것:
#   ① test_trace_parity.py가 못 잡는 종류의 불일치가 한 번이라도 실제로 발생
#   ② 여기에 새 진단 항목을 추가해야 해서 사본에 또 손대야 하는 시점
# 실행 방법: analyze_*에 trace 파라미터 추가 → 게이트 탈락 지점마다 사유를
# trace에 append → 이 파일의 _trace_* 함수들 삭제.
# (CLAUDE.md "_trace_* 사본 보류 이유와 제거 트리거" 항목과 이 내용을
# 동기화해서 유지할 것 — docs/rs_definition_and_slope_investigation.md 8절)
def _gate_step(steps, name, ok, detail=None):
    steps.append({"gate": name, "ok": bool(ok), "detail": detail})
    return ok


def _gate_risk_pct(rrb, pivot):
    """게이트 실제 판정에 쓰인 risk% 표시용.
    v5.61: 리터럴 재구현 대신 scanner._risk_pct_at_gate를 그대로 호출 —
    _risk_hard_ok와 계산식이 물리적으로 한 곳에만 있어 드리프트 불가능
    (기존엔 이 파일에 수식을 복사해두고 "반드시 같은 로직으로 유지" 주석만
    있었음, docs/rs_definition_and_slope_investigation.md 6절).
    v5.41: pullback/imminent만 pivot을 넘겨 게이트=피벗 기준이고(각각
    Case13 회귀 방지/아직 미돌파라 근거 문서화됨), breakout/boxbreak는
    pivot 없이 호출해 게이트=카드(현재가) 기준으로 일치시킴(v5.41 수정)."""
    from scanner import _risk_pct_at_gate
    try:
        stop_eff = float(rrb.get("stop", 0.0))
        if pivot and pivot > 0 and stop_eff > 0:
            return round(_risk_pct_at_gate(rrb, pivot), 2)
    except Exception:
        pass
    return rrb.get("risk_pct")


def _trace_pullback(df, is_kr, rs_rank, rs_3m=None, rs_delta=None):
    """v5.71: rs_3m/rs_delta는 옵션 — /api/debug는 단일 종목만 보고 있어
    유니버스 전체 백분위(rs_3m)나 20거래일 전 랭크(rs_delta)를 낼 수 없다
    (rs_rank 자체도 이미 근사치인 것과 같은 한계, CLAUDE.md '알려진 설계 갭'
    참고). None으로 두면 3M/momentum 경로는 자동으로 평가 안 되고 12M 경로만
    본다 — scanner.analyze()와 동일한 None 처리 규약."""
    from scanner import (CONFIG as cfg, rsi as _rsi, select_pivot, significant_support,
                         apply_atr_buffer, _rr_block, _risk_hard_ok, late_stage_info,
                         _price_frozen_block, anchored_vwap, atr as _atr)
    steps = []
    n0 = len(df) if df is not None else 0
    if not _gate_step(steps, "min_bars", df is not None and n0 >= cfg["min_bars"], f"{n0}봉 (요구 {cfg['min_bars']})"):
        return {"passed": False, "fail_at": "min_bars", "steps": steps}
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if not _gate_step(steps, "min_bars_dropna", len(df) >= cfg["min_bars"], f"{len(df)}봉"):
        return {"passed": False, "fail_at": "min_bars_dropna", "steps": steps}
    # v5.71: RS 게이트 E = A(12개월) OR B(3개월) OR C(RS50+ 且 20일랭크+25)
    path_12m = rs_rank is not None and rs_rank >= cfg["rs_min"]
    path_3m = rs_3m is not None and rs_3m >= cfg["rs_min"]
    path_mom = (rs_rank is not None and rs_rank >= cfg["rs_momentum_floor"]
                and rs_delta is not None and rs_delta >= cfg["rs_delta_min"])
    rs_ok = path_12m or path_3m or path_mom
    if not _gate_step(steps, "rs_min", rs_ok,
                       f"12M rs={rs_rank}(요구{cfg['rs_min']}+) · 3M rs_3m={rs_3m}(요구{cfg['rs_min']}+) · "
                       f"모멘텀 rs={rs_rank}&rs_delta={rs_delta}(요구 rs{cfg['rs_momentum_floor']}+ 且 delta{cfg['rs_delta_min']}+)"):
        return {"passed": False, "fail_at": "rs_min", "steps": steps}
    rs_path = "12M" if path_12m else ("3M" if path_3m else "momentum")
    is_leader = rs_rank is not None and rs_rank >= cfg["leader_rs"]
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma10 = c.rolling(cfg["ma_short"]).mean(); ma20 = c.rolling(cfg["ma_mid"]).mean()
    ma60 = c.rolling(cfg["ma_long"]).mean(); ma200 = c.rolling(cfg["ma_trend"]).mean()
    r = _rsi(c)
    close = float(c.iloc[-1])
    m10, m20, m60, m200 = float(ma10.iloc[-1]), float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma200.iloc[-1])
    cur_rsi = float(r.iloc[-1])
    if not _gate_step(steps, "지표_계산가능", not any(math.isnan(x) for x in (m10, m20, m60, m200, cur_rsi)), "이평/RSI 중 NaN"):
        return {"passed": False, "fail_at": "nan", "steps": steps}
    # v5.61: 리터럴 사본 대신 scanner.py CONFIG["ma20_slope_floor"]에서 직접
    # 읽음 — v5.60 때 이 값이 app.py에 별도 사본으로 있다가 동기화가 안 됐던
    # 사고 재발 방지(전수 감사, docs/rs_definition_and_slope_investigation.md
    # 6절). 앞으로 scanner.py에서 이 값을 바꾸면 진단 화면도 자동으로 따라옴.
    slope_floor = cfg["ma20_slope_floor"]
    ma20_slope = m20 > float(ma20.iloc[-11]) * slope_floor
    in_uptrend = (close > m60) and (close > m200) and (m20 > m60) and ma20_slope
    if not _gate_step(steps, "우상향추세", in_uptrend,
                       f"close{'>' if close>m60 else '<='}MA60 · close{'>' if close>m200 else '<='}MA200 · MA20{'>' if m20>m60 else '<='}MA60 · 기울기{'OK' if ma20_slope else '꺾임'}"):
        return {"passed": False, "fail_at": "우상향추세", "steps": steps}
    prev_close = float(c.iloc[-2])
    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
    breakout_day = change_pct >= 4.0
    if breakout_day:
        high60_ref = float(h.iloc[-61:-1].max())
        pullback = (high60_ref - prev_close) / high60_ref
        _av = anchored_vwap(h, lo, c, v)
        if not _gate_step(steps, "돌파일_AVWAP연장가드", _av.get("zone") not in ("extended", "overheated"), f"avwap zone={_av.get('zone')}"):
            return {"passed": False, "fail_at": "avwap_extended", "steps": steps}
    else:
        last60_h = h.iloc[-60:]
        pullback = (float(last60_h.max()) - close) / float(last60_h.max())
    # v5.71: 눌림폭 게이트를 고정%에서 depth_atr(눌림폭%÷ATR%)로 교체
    atr_val = _atr(h, lo, c, 14)
    atr_pct_raw = atr_val / close * 100 if close > 0 else 0.0
    depth_atr = (pullback * 100 / atr_pct_raw) if atr_pct_raw > 0 else None
    depth_ok = depth_atr is not None and cfg["depth_atr_min"] <= depth_atr <= cfg["depth_atr_max"]
    if not _gate_step(steps, "눌림폭", depth_ok,
                       f"depth_atr={f'{depth_atr:.2f}' if depth_atr is not None else 'None'} "
                       f"(허용 {cfg['depth_atr_min']}~{cfg['depth_atr_max']}) · 눌림폭 {pullback*100:.1f}% · ATR% {atr_pct_raw:.1f}"):
        return {"passed": False, "fail_at": "눌림폭", "steps": steps}
    dist10, dist20, dist60 = (close - m10) / m10, (close - m20) / m20, (close - m60) / m60
    prox = cfg["ma_proximity"]
    prox_allow = prox + max(0.0, change_pct / 100) if change_pct >= 4.0 else prox
    near_ma = min(abs(dist10), abs(dist20), abs(dist60))
    if not _gate_step(steps, "이평선지지", near_ma <= prox_allow, f"근접 {near_ma*100:.1f}% (허용 {prox_allow*100:.1f}%)"):
        return {"passed": False, "fail_at": "이평선지지", "steps": steps}
    rsi_eval = float(r.iloc[-2]) if breakout_day else cur_rsi
    rsi_max = cfg["leader_rsi_max"] if is_leader else cfg["rsi_max"]
    if not _gate_step(steps, "RSI중립권", cfg["rsi_min"] <= rsi_eval <= rsi_max, f"RSI {rsi_eval:.1f} (허용 {cfg['rsi_min']}~{rsi_max})"):
        return {"passed": False, "fail_at": "RSI", "steps": steps}
    pw = cfg["pivot_window"]
    pivot, pivot_type, _, _ = select_pivot(h, lo, c, close, pw, is_kr=is_kr, v=v)
    # v5.61 감사: 0.99/이하 stop 계산 리터럴은 scanner.analyze()의 손절 계산과
    # 동일한 값을 이 함수 안에 그대로 복사한 것 — CONFIG에 없는(scanner.py
    # 자신도 지역 리터럴로 쓰는) 값이라 cfg[...] 참조로 못 바꿈. scanner.py
    # 쪽 이 리터럴이 바뀌면 여기도 같이 고칠 것(전수감사,
    # docs/rs_definition_and_slope_investigation.md 6절 — ma20_slope_floor는
    # CONFIG로 승격해 해결했지만 이건 구조상 어려움).
    ma_below = [x for x in (m10, m20, m60) if x and x < close]
    ma_stop = max(ma_below) * 0.99 if ma_below else None
    sig_low = significant_support(lo, pw, min_touches=2, band=0.02, exclude=1)
    cand = [x for x in (ma_stop, sig_low) if x is not None and x < close]
    stop = max(cand) if cand else float(lo.iloc[-pw:].min())
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, cfg.get("atr_stop_buffer", 0.0))
    rrb = _rr_block(pivot, stop, h, lo, c, base_low=float(lo.iloc[-cfg["recent_high_window"]:].min()),
                    entry=close, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    hard_ok = _risk_hard_ok(rrb, is_kr, pivot=pivot)
    gate_risk = _gate_risk_pct(rrb, pivot)
    card_risk = rrb.get("risk_pct")
    _limit_note = f"한도 {'12' if is_kr else '8'}%(고정) or ATR%×1.5(≤15%캡)"
    if not _gate_step(steps, "리스크하드게이트", hard_ok,
                       f"피벗({pivot_type}) {round(pivot,2)} · 게이트기준(피벗) risk {gate_risk}% vs 카드표시(현재가) risk {card_risk}% · {_limit_note}"):
        return {"passed": False, "fail_at": "risk_hard", "steps": steps, "pivot": round(pivot, 2), "pivot_type": pivot_type,
                "risk_pct": card_risk, "gate_risk_pct": gate_risk, "stop": round(stop, 2)}
    ls = late_stage_info(c, lo, h, v, is_kr)
    if not _gate_step(steps, "후기스테이지", ls.get("late_level") != "danger", f"level={ls.get('late_level')} flags={ls.get('late_flags')}"):
        return {"passed": False, "fail_at": "late_stage_danger", "steps": steps, "pivot": round(pivot, 2), "stop": round(stop, 2)}
    # v5.90: 가격고정(M&A 의심)은 더 이상 게이트가 아니라 표시 레이어 정보 —
    # 항상 통과 처리하되 디버그 패널에는 판정값을 그대로 보여준다.
    pf = _price_frozen_block(c, h, lo, v)
    _gate_step(steps, "가격고정(정보용, 비차단)", True,
               f"price_frozen={pf.get('price_frozen')} {pf.get('price_frozen_reasons')}")
    return {"passed": True, "fail_at": None, "steps": steps, "pivot": round(pivot, 2), "pivot_type": pivot_type,
            "risk_pct": card_risk, "gate_risk_pct": gate_risk, "stop": round(stop, 2), "rs_path": rs_path}


def _trace_turnaround(df, is_kr, rs_rank, rs_mom):
    from scanner import (TURN_CONFIG as cfg, rsi as _rsi, select_pivot, count_bases_since_bottom,
                         apply_atr_buffer, _rr_block)
    steps = []
    n0 = len(df) if df is not None else 0
    if not _gate_step(steps, "min_bars", df is not None and n0 >= cfg["min_bars"], f"{n0}봉"):
        return {"passed": False, "fail_at": "min_bars", "steps": steps}
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if not _gate_step(steps, "min_bars_dropna", len(df) >= cfg["min_bars"], f"{len(df)}봉"):
        return {"passed": False, "fail_at": "min_bars_dropna", "steps": steps}
    if not _gate_step(steps, "rs_min", rs_rank is not None and rs_rank >= cfg["rs_min"], f"rs={rs_rank} (요구 {cfg['rs_min']}+)"):
        return {"passed": False, "fail_at": "rs_min", "steps": steps}
    if not _gate_step(steps, "RS모멘텀", rs_mom is None or rs_mom >= 0, f"rs_mom={rs_mom}"):
        return {"passed": False, "fail_at": "rs_mom", "steps": steps}
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean(); ma200 = c.rolling(200).mean()
    r = _rsi(c)
    close = float(c.iloc[-1])
    m20, m60, m200 = float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma200.iloc[-1])
    cur_rsi = float(r.iloc[-1])
    if not _gate_step(steps, "지표_계산가능", not any(math.isnan(x) for x in (m20, m60, m200, cur_rsi)), None):
        return {"passed": False, "fail_at": "nan", "steps": steps}
    aligned = (ma20 > ma60) & (ma60 > ma200) & (c > ma200)
    if not _gate_step(steps, "정배열", bool(aligned.iloc[-1]), f"MA20{'>' if m20>m60 else '<='}MA60, MA60{'>' if m60>m200 else '<='}MA200, close{'>' if close>m200 else '<='}MA200"):
        return {"passed": False, "fail_at": "정배열", "steps": steps}
    align_days = 0
    for val in reversed(aligned.tolist()):
        if val:
            align_days += 1
        else:
            break
    if not _gate_step(steps, "전환신선도", align_days <= cfg["align_window"], f"{align_days}봉째 정배열 (허용 {cfg['align_window']}봉 이내)"):
        return {"passed": False, "fail_at": "align_window", "steps": steps}
    ma200_dist = (close - m200) / m200
    if not _gate_step(steps, "200일선근접도", ma200_dist <= cfg["max_ma200_dist"], f"+{ma200_dist*100:.1f}% (허용 {cfg['max_ma200_dist']*100:.0f}% 이내)"):
        return {"passed": False, "fail_at": "ma200_dist", "steps": steps}
    lb = cfg["ma200_slope_lookback"]
    ma200_rising = False
    if len(ma200.dropna()) > lb:
        m200_prev = float(ma200.iloc[-1 - lb])
        if not math.isnan(m200_prev) and m200_prev > 0:
            ma200_rising = (m200 - m200_prev) / m200_prev > cfg["ma200_rising_min"]
    if not _gate_step(steps, "200일선기울기", ma200_rising, "200일선이 아직 안 들림(1단계 미졸업)"):
        return {"passed": False, "fail_at": "ma200_rising", "steps": steps}
    base_info = count_bases_since_bottom(c, lo, h, low_lookback=cfg["low_lookback"],
                                         recent_bottom_max=cfg["recent_bottom_max"], correction_min=cfg["correction_min"])
    if cfg.get("first_base_only", True) and not _gate_step(steps, "1차베이스여부", base_info["is_first_base"], f"조정 {base_info['corrections']}회, 바닥{base_info['bottom_ago']}봉전"):
        return {"passed": False, "fail_at": "first_base_only", "steps": steps}
    pivot, pivot_type, _, _ = select_pivot(h, lo, c, close, 20, is_kr=is_kr, v=v)
    # v5.63: 손절/risk_pct 계산 — analyze_turnaround엔 이걸 막는 게이트가
    # 없어서(하드 리스크게이트 자체가 없음, CLAUDE.md 참고) trace 흐름에
    # 영향은 없지만, test_trace_parity.py가 stop/risk_pct를 비교하려면
    # 필요해서 통과 시점에 같이 계산(scanner.analyze_turnaround와 동일 리터럴
    # 0.98/0.3 — 이것도 CONFIG 밖 지역 리터럴이라 cfg[...] 참조 불가, 전수감사
    # 대상, docs/rs_definition_and_slope_investigation.md 6/7절).
    stop = m60 * 0.98
    _cand = [x for x in (stop, float(lo.iloc[-10:].min())) if x < close]
    stop = max(_cand) if _cand else float(lo.iloc[-10:].min())
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, 0.3)
    rrb = _rr_block(pivot, stop, h, lo, c, base_low=float(lo.iloc[-30:].min()),
                    entry=close, warn_pct=15.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    return {"passed": True, "fail_at": None, "steps": steps, "pivot": round(pivot, 2), "pivot_type": pivot_type,
            "stop": rrb.get("stop"), "risk_pct": rrb.get("risk_pct")}


def _trace_breakout(df, is_kr, rs_rank):
    from scanner import (BREAKOUT_CONFIG as cfg, rsi as _rsi, off_high_pct, apply_atr_buffer,
                         _rr_block, _risk_hard_ok, late_stage_info, _price_frozen_block)
    steps = []
    n0 = len(df) if df is not None else 0
    if not _gate_step(steps, "min_bars", df is not None and n0 >= cfg["min_bars"], f"{n0}봉"):
        return {"passed": False, "fail_at": "min_bars", "steps": steps}
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if not _gate_step(steps, "min_bars_dropna", len(df) >= cfg["min_bars"], f"{len(df)}봉"):
        return {"passed": False, "fail_at": "min_bars_dropna", "steps": steps}
    if not _gate_step(steps, "rs_min", rs_rank is not None and rs_rank >= cfg["rs_min"], f"rs={rs_rank} (요구 {cfg['rs_min']}+)"):
        return {"passed": False, "fail_at": "rs_min", "steps": steps}
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma200 = c.rolling(200).mean()
    close = float(c.iloc[-1])
    m200 = float(ma200.iloc[-1])
    if not _gate_step(steps, "지표_계산가능", not math.isnan(m200), None):
        return {"passed": False, "fail_at": "nan", "steps": steps}
    if not _gate_step(steps, "200일선위", close >= m200, f"close {close} vs MA200 {round(m200,2)}"):
        return {"passed": False, "fail_at": "close_below_ma200", "steps": steps}
    ohp = off_high_pct(c)
    if not _gate_step(steps, "고점대비낙폭", ohp >= -cfg["max_off_high"], f"{ohp:.1f}% (허용 -{cfg['max_off_high']}% 이내)"):
        return {"passed": False, "fail_at": "off_high_pct", "steps": steps}
    base = c.iloc[-(cfg["base_min_len"] + 1):-1]
    if not _gate_step(steps, "베이스길이", len(base) >= cfg["base_min_len"], f"{len(base)}봉"):
        return {"passed": False, "fail_at": "base_min_len", "steps": steps}
    base_high, base_low = float(base.max()), float(base.min())
    if not _gate_step(steps, "베이스상단유효", base_high > 0, f"base_high={base_high}"):
        return {"passed": False, "fail_at": "base_high_invalid", "steps": steps}
    base_range = (base_high - base_low) / base_high
    if not _gate_step(steps, "베이스폭", base_range <= cfg["base_max_range"], f"{base_range*100:.1f}% (허용 {cfg['base_max_range']*100:.0f}% 이내), 베이스상단 {round(base_high,2)}"):
        return {"passed": False, "fail_at": "base_max_range", "steps": steps}
    pivot = base_high
    if not _gate_step(steps, "돌파여부", close > pivot, f"close {close} vs 피벗(베이스상단) {round(pivot,2)}"):
        return {"passed": False, "fail_at": "not_broken_yet", "steps": steps, "pivot": round(pivot, 2)}
    ext = (close - pivot) / pivot
    if not _gate_step(steps, "연장도", ext <= cfg["extended_max"], f"+{ext*100:.1f}% (허용 {cfg['extended_max']*100:.0f}% 이내)"):
        return {"passed": False, "fail_at": "extended", "steps": steps, "pivot": round(pivot, 2)}
    vol_avg = float(v.iloc[-51:-1].mean())
    vol_mult = float(v.iloc[-1]) / vol_avg if vol_avg > 0 else 0.0
    if not _gate_step(steps, "거래량동반", vol_mult >= cfg["vol_mult"], f"{vol_mult:.2f}배 (요구 {cfg['vol_mult']}배+)"):
        return {"passed": False, "fail_at": "vol_mult", "steps": steps, "pivot": round(pivot, 2)}
    # v5.61 감사: 0.97/0.15는 scanner.analyze_breakout()의 손절 계산 리터럴
    # 복사 — scanner.py 자신도 CONFIG 밖 지역 리터럴이라 cfg[...] 참조로 못
    # 바꿈. scanner.py 쪽이 바뀌면 여기도 같이 고칠 것(전수감사,
    # docs/rs_definition_and_slope_investigation.md 6절).
    stop = round(pivot * 0.97, 2)
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, 0.15)
    rrb = _rr_block(pivot, stop, h, lo, c, base_low=base_low, entry=close, warn_pct=8.0,
                    is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    # v5.41: pivot 인자 제거 — scanner.py의 analyze_breakout이 이제 pivot 없이
    # 호출(이미 돌파한 상태라 실제 진입은 현재가라는 코드 주석과 일치시킴).
    # extended_max가 이미 있어 게이트/카드 값 차이는 애초에 작았음.
    hard_ok = _risk_hard_ok(rrb, is_kr)
    if not _gate_step(steps, "리스크하드게이트", hard_ok, f"risk_pct {rrb.get('risk_pct')}% (현재가 기준=게이트 기준과 동일) (한도 {'12' if is_kr else '8'}%(고정) or ATR%×1.5(≤15%캡))"):
        return {"passed": False, "fail_at": "risk_hard", "steps": steps, "pivot": round(pivot, 2),
                "risk_pct": rrb.get("risk_pct"), "stop": rrb.get("stop")}
    ls = late_stage_info(c, lo, h, v, is_kr)
    if not _gate_step(steps, "후기스테이지", ls.get("late_level") != "danger", f"level={ls.get('late_level')}"):
        return {"passed": False, "fail_at": "late_stage_danger", "steps": steps, "pivot": round(pivot, 2), "stop": rrb.get("stop")}
    pf = _price_frozen_block(c, h, lo, v)
    _gate_step(steps, "가격고정(정보용, 비차단)", True,
               f"price_frozen={pf.get('price_frozen')} {pf.get('price_frozen_reasons')}")
    return {"passed": True, "fail_at": None, "steps": steps, "pivot": round(pivot, 2),
            "risk_pct": rrb.get("risk_pct"), "stop": rrb.get("stop")}


def _trace_boxbreak(df, is_kr, rs_rank):
    from scanner import (BOXBREAK_CONFIG as cfg, off_high_pct, significant_resistance,
                         apply_atr_buffer, _rr_block, _risk_hard_ok, late_stage_info, _price_frozen_block)
    steps = []
    n0 = len(df) if df is not None else 0
    if not _gate_step(steps, "min_bars", df is not None and n0 >= cfg["min_bars"], f"{n0}봉"):
        return {"passed": False, "fail_at": "min_bars", "steps": steps}
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if not _gate_step(steps, "min_bars_dropna", len(df) >= cfg["min_bars"], f"{len(df)}봉"):
        return {"passed": False, "fail_at": "min_bars_dropna", "steps": steps}
    if not _gate_step(steps, "rs_min", rs_rank is not None and rs_rank >= cfg["rs_min"], f"rs={rs_rank} (요구 {cfg['rs_min']}+)"):
        return {"passed": False, "fail_at": "rs_min", "steps": steps}
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    close = float(c.iloc[-1])
    ma_long = c.rolling(cfg["ma_long"]).mean()
    m_long = float(ma_long.iloc[-1])
    if not _gate_step(steps, "지표_계산가능", not math.isnan(m_long), None):
        return {"passed": False, "fail_at": "nan", "steps": steps}
    ohp = off_high_pct(c)
    if not _gate_step(steps, "고점대비낙폭", ohp >= -cfg["max_off_high"], f"{ohp:.1f}% (허용 -{cfg['max_off_high']}% 이내)"):
        return {"passed": False, "fail_at": "off_high_pct", "steps": steps}
    if not _gate_step(steps, "장기선위", close >= m_long, f"close {close} vs MA{cfg['ma_long']} {round(m_long,2)}"):
        return {"passed": False, "fail_at": "close_below_ma_long", "steps": steps}
    vol_avg = float(v.iloc[-51:-1].mean())
    vol_mult = float(v.iloc[-1]) / vol_avg if vol_avg > 0 else 0.0
    if not _gate_step(steps, "거래량동반", vol_mult >= cfg["vol_mult"], f"{vol_mult:.2f}배 (요구 {cfg['vol_mult']}배+)"):
        return {"passed": False, "fail_at": "vol_mult", "steps": steps}
    best = None
    box_detail = {}
    for win in cfg["box_windows"]:
        if len(c) < win + 2:
            continue
        box_h, box_l = h.iloc[-(win + 1):-1], lo.iloc[-(win + 1):-1]
        sig_high = significant_resistance(h, win, min_touches=2, band=0.02, exclude=1)
        box_high = float(sig_high) if sig_high is not None else float(box_h.max())
        box_low = float(box_l.min())
        if box_high <= 0:
            continue
        box_range = (box_high - box_low) / box_high
        box_detail[f"{win}봉박스"] = {"상단": round(box_high, 2), "폭%": round(box_range * 100, 1),
                                     "돌파여부": close > box_high * 1.005}
        if box_range > cfg["box_max_range"] or close <= box_high * 1.005:
            continue
        ext = (close - box_high) / box_high
        box_detail[f"{win}봉박스"]["연장%"] = round(ext * 100, 1)
        if ext > cfg["extended_max"]:
            box_detail[f"{win}봉박스"]["제외사유"] = f"연장 {ext*100:.1f}% > 허용 {cfg['extended_max']*100:.0f}%"
            continue   # v5.41: 너무 연장됨 → 이 박스는 후보 제외 (breakout과 동일 기준 신설)
        tightness = 1 - min(box_range / cfg["box_max_range"], 1.0)
        quality = tightness * 0.5 + min(win / 60, 1.0) * 0.3 + min(vol_mult / 3, 1.0) * 0.2
        cand = {"win": win, "box_high": box_high, "box_low": box_low, "ext": ext, "quality": quality}
        if best is None or cand["quality"] > best["quality"]:
            best = cand
    if not _gate_step(steps, "박스돌파(연장12%이내)", best is not None, box_detail):
        return {"passed": False, "fail_at": "no_box_broken", "steps": steps, "박스상세": box_detail}
    pivot = best["box_high"]
    # v5.61 감사: 0.97/0.15는 scanner.analyze_boxbreak()의 손절 계산 리터럴
    # 복사 — breakout과 같은 사유로 cfg[...] 참조 불가(전수감사,
    # docs/rs_definition_and_slope_investigation.md 6절).
    stop = round(pivot * 0.97, 2)
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, 0.15)
    rrb = _rr_block(pivot, stop, h, lo, c, base_low=best["box_low"], entry=close, warn_pct=8.0,
                    is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    # v5.41: pivot 인자 제거 — analyze_boxbreak과 동일(게이트=카드 기준 통일).
    hard_ok = _risk_hard_ok(rrb, is_kr)
    if not _gate_step(steps, "리스크하드게이트", hard_ok, f"risk_pct {rrb.get('risk_pct')}% (현재가 기준=게이트 기준과 동일) (한도 {'12' if is_kr else '8'}%(고정) or ATR%×1.5(≤15%캡))"):
        return {"passed": False, "fail_at": "risk_hard", "steps": steps, "pivot": round(pivot, 2),
                "risk_pct": rrb.get("risk_pct"), "stop": rrb.get("stop"), "박스상세": box_detail}
    ls = late_stage_info(c, lo, h, v, is_kr)
    if not _gate_step(steps, "후기스테이지", ls.get("late_level") != "danger", f"level={ls.get('late_level')}"):
        return {"passed": False, "fail_at": "late_stage_danger", "steps": steps, "pivot": round(pivot, 2),
                "stop": rrb.get("stop"), "박스상세": box_detail}
    pf = _price_frozen_block(c, h, lo, v)
    _gate_step(steps, "가격고정(정보용, 비차단)", True,
               f"price_frozen={pf.get('price_frozen')} {pf.get('price_frozen_reasons')}")
    return {"passed": True, "fail_at": None, "steps": steps, "pivot": round(pivot, 2),
            "risk_pct": rrb.get("risk_pct"), "stop": rrb.get("stop"), "박스상세": box_detail}


def _trace_imminent(df, is_kr, rs_rank):
    from scanner import (IMMINENT_CONFIG as cfg, rsi as _rsi, off_high_pct, select_pivot,
                         significant_support, apply_atr_buffer, _rr_block, _risk_hard_ok,
                         late_stage_info, _price_frozen_block)
    steps = []
    n0 = len(df) if df is not None else 0
    if not _gate_step(steps, "min_bars", df is not None and n0 >= cfg["min_bars"], f"{n0}봉 (요구 {cfg['min_bars']})"):
        return {"passed": False, "fail_at": "min_bars", "steps": steps}
    df = df.dropna(subset=["Close", "Volume"]).copy()
    if not _gate_step(steps, "min_bars_dropna", len(df) >= cfg["min_bars"], f"{len(df)}봉"):
        return {"passed": False, "fail_at": "min_bars_dropna", "steps": steps}
    if not _gate_step(steps, "rs_min", rs_rank is not None and rs_rank >= cfg["rs_min"], f"rs={rs_rank} (요구 {cfg['rs_min']}+)"):
        return {"passed": False, "fail_at": "rs_min", "steps": steps}
    c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean(); ma200 = c.rolling(200).mean()
    r = _rsi(c)
    close = float(c.iloc[-1])
    m20, m60, m200 = float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma200.iloc[-1])
    cur_rsi = float(r.iloc[-1])
    if not _gate_step(steps, "지표_계산가능", not any(math.isnan(x) for x in (m20, m60, m200, cur_rsi)), None):
        return {"passed": False, "fail_at": "nan", "steps": steps}
    if not _gate_step(steps, "200일선위", close >= m200, f"close {close} vs MA200 {round(m200,2)}"):
        return {"passed": False, "fail_at": "close_below_ma200", "steps": steps}
    if not _gate_step(steps, "정배열초입", m20 > m60, f"MA20 {round(m20,2)} vs MA60 {round(m60,2)}"):
        return {"passed": False, "fail_at": "ma20_below_ma60", "steps": steps}
    ohp = off_high_pct(c)
    if not _gate_step(steps, "고점대비낙폭", ohp >= -cfg["max_off_high"], f"{ohp:.1f}% (허용 -{cfg['max_off_high']}% 이내)"):
        return {"passed": False, "fail_at": "off_high_pct", "steps": steps}
    pivot, pivot_type, _, _ = select_pivot(h, lo, c, close, cfg["pivot_window"], is_kr=is_kr, use_near=True, v=v)
    near = (close - pivot) / pivot if pivot > 0 else -1.0
    if not _gate_step(steps, "피벗근접", cfg["near_min"] <= near <= cfg["near_max"],
                       f"실제 판정 피벗({pivot_type}) {round(pivot,2)}, near {near*100:.2f}% (허용 {cfg['near_min']*100:.0f}%~{cfg['near_max']*100:.0f}%)"):
        return {"passed": False, "fail_at": "near_range", "steps": steps, "pivot": round(pivot, 2), "pivot_type": pivot_type, "near_pct": round(near * 100, 2)}
    # v5.61 감사: 0.98/0.15는 scanner.analyze_imminent()의 손절 계산 리터럴
    # 복사 — 위와 같은 사유로 cfg[...] 참조 불가(전수감사,
    # docs/rs_definition_and_slope_investigation.md 6절).
    sig_sup = significant_support(lo, cfg["pivot_window"], min_touches=2, band=0.02, exclude=1)
    cand = []
    if sig_sup is not None and sig_sup < close:
        cand.append(sig_sup)
    if m20 * 0.98 < close:
        cand.append(m20 * 0.98)
    stop = max(cand) if cand else float(lo.iloc[-cfg["pivot_window"]:].min())
    stop, stop_struct, atr_buf = apply_atr_buffer(stop, h, lo, c, 0.15)
    rrb = _rr_block(pivot, stop, h, lo, c, base_low=float(lo.iloc[-cfg["pivot_window"]:].min()),
                    entry=None, warn_pct=8.0, is_kr=is_kr, stop_struct=stop_struct, atr_buf=atr_buf)
    hard_ok = _risk_hard_ok(rrb, is_kr, pivot=pivot)
    if not _gate_step(steps, "리스크하드게이트", hard_ok, f"risk_pct {rrb.get('risk_pct')}% (피벗 기준=카드 표시와 동일, entry=None) (한도 {'12' if is_kr else '8'}%(고정) or ATR%×1.5(≤15%캡))"):
        return {"passed": False, "fail_at": "risk_hard", "steps": steps, "pivot": round(pivot, 2), "pivot_type": pivot_type,
                "near_pct": round(near * 100, 2), "risk_pct": rrb.get("risk_pct"), "stop": rrb.get("stop")}
    ls = late_stage_info(c, lo, h, v, is_kr)
    if not _gate_step(steps, "후기스테이지", ls.get("late_level") != "danger", f"level={ls.get('late_level')}"):
        return {"passed": False, "fail_at": "late_stage_danger", "steps": steps, "pivot": round(pivot, 2),
                "near_pct": round(near * 100, 2), "stop": rrb.get("stop")}
    pf = _price_frozen_block(c, h, lo, v)
    _gate_step(steps, "가격고정(정보용, 비차단)", True,
               f"price_frozen={pf.get('price_frozen')} {pf.get('price_frozen_reasons')}")
    return {"passed": True, "fail_at": None, "steps": steps, "pivot": round(pivot, 2), "pivot_type": pivot_type,
            "near_pct": round(near * 100, 2), "risk_pct": rrb.get("risk_pct"), "stop": rrb.get("stop")}


@app.get("/api/debug/{ticker}")
async def debug_ticker(ticker: str):
    """진단용: 종목의 최근 OHLC 원본 + ATR 분해 + 각 모드 통과/탈락 여부.
    예: /api/debug/347850.KQ  (배포 후 브라우저에서 열기)"""
    import pandas as _pd
    from scanner import (analyze, analyze_turnaround, analyze_imminent,
                         analyze_breakout, analyze_leader, analyze_super, analyze_surge,
                         analyze_boxbreak, rs_raw_score, rs_quarters_used)
    # 접미사(.KS/.KQ) 없이 숫자코드만 입력해도 유니버스에서 자동 매칭
    _uni = get_universe(None)
    if ticker not in _uni:
        for suf in (".KS", ".KQ"):
            if (ticker + suf) in _uni:
                ticker = ticker + suf
                break
    df = _fetch(ticker)
    if df is None or df.empty:
        return JSONResponse({"error": "데이터 없음", "ticker": ticker})
    is_kr = ticker.endswith((".KS", ".KQ"))

    # v5.61 [버그수정] RS는 유니버스 전체 상대순위라 종목 하나만으로는 정식
    # 백분위를 못 구해서, 예전엔 무조건 rs_rank=80/rs_mom=5 고정 근사치를
    # 썼음 — 화면에 근사 표시가 없어 "정식 판정"처럼 보였고, 삼성화재
    # 진단에서 근사치 80을 정식 RS 75로 오인해 "5점차 억울한 탈락"이라는
    # 잘못된 결론으로 이어진 사고가 있었음(docs/rs_definition_and_slope_
    # investigation.md). leader-check(v5.54)가 이미 같은 문제를 같은
    # 방법으로 풀어놨음 — _fetch_market_data 캐시(다른 탭 로드로 보통 이미
    # 따뜻함)를 재사용해 정식 rs_ranks/rs_moms를 dict 조회만으로 가져온다.
    # 캐시가 콜드면(_fetch_market_data가 새 fetch를 걸지 않고 즉시 None
    # 반환 — 이 요청을 블로킹하지 않음) 근사치로 폴백하고 근사임을 명시.
    _bundle = await _fetch_market_data("all")
    _real_rs = _bundle["rs_ranks"].get(ticker) if _bundle else None
    _real_rs_mom = _bundle["rs_moms"].get(ticker) if _bundle else None
    rs_is_approx = _real_rs is None
    rs_used = _real_rs if _real_rs is not None else 80
    rs_mom_used = _real_rs_mom if _real_rs_mom is not None else 5
    # v5.71: rs_3m/rs_delta는 위 rs_used와 달리 근사 폴백을 두지 않는다 —
    # 없으면(캐시 콜드/집계 안 됨) 그냥 None으로 둬서 _trace_pullback이
    # 3M/momentum 경로를 평가 생략하게 한다(12M 경로 근사는 기존과 동일하게
    # 유지되므로 판정 자체가 막히진 않음). 가짜 근사값을 만들면 "3M도
    # 통과"라는 오해를 줄 수 있어(rs_used=80 폴백처럼 rs_min 경계값을 그대로
    # 쓰면 3M/모멘텀 경로가 항상 참으로 보임) 일부러 비워둠.
    rs3_used = _bundle.get("rs3_ranks", {}).get(ticker) if _bundle else None
    rs_delta_used = _bundle.get("rs_deltas", {}).get(ticker) if _bundle else None

    h, lo, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = _pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    close = float(c.iloc[-1])

    # 각 모드 통과 여부 (RS는 위에서 정한 rs_used/rs_mom_used — 정식이면 그대로,
    # 근사면 폴백값 80/5. is_kr은 항상 명시적으로 넘김: v5.39 버그 재발 방지
    # 주석 유지.)
    # pullback/turnaround/breakout/boxbreak/imminent는 실제 게이트 순서를
    # 그대로 재현하는 _trace_* 함수로 판정 — modes와 게이트추적/탈락_핵심사유가
    # 서로 다른 계산에서 나와 어긋나는 일이 없도록 단일 소스로 통일.
    modes = {}
    traces = {}
    try:
        traces["pullback"] = _trace_pullback(df, is_kr, rs_used, rs3_used, rs_delta_used)
    except Exception as e:
        traces["pullback"] = {"passed": False, "fail_at": "에러", "steps": [{"gate": "에러", "ok": False, "detail": str(e)}]}
    try:
        traces["turnaround"] = _trace_turnaround(df, is_kr, rs_used, rs_mom_used)
    except Exception as e:
        traces["turnaround"] = {"passed": False, "fail_at": "에러", "steps": [{"gate": "에러", "ok": False, "detail": str(e)}]}
    try:
        traces["breakout"] = _trace_breakout(df, is_kr, rs_used)
    except Exception as e:
        traces["breakout"] = {"passed": False, "fail_at": "에러", "steps": [{"gate": "에러", "ok": False, "detail": str(e)}]}
    try:
        traces["boxbreak"] = _trace_boxbreak(df, is_kr, rs_used)
    except Exception as e:
        traces["boxbreak"] = {"passed": False, "fail_at": "에러", "steps": [{"gate": "에러", "ok": False, "detail": str(e)}]}
    try:
        traces["imminent"] = _trace_imminent(df, is_kr, rs_used)
    except Exception as e:
        traces["imminent"] = {"passed": False, "fail_at": "에러", "steps": [{"gate": "에러", "ok": False, "detail": str(e)}]}
    for name, t in traces.items():
        modes[name] = "통과" if t.get("passed") else ("에러" if t.get("fail_at") == "에러" else "탈락")
    for name, fn in [("leader", analyze_leader), ("super", analyze_super), ("surge", analyze_surge)]:
        try:
            res = fn(df, rs_rank=rs_used, rs_mom=rs_mom_used)
            modes[name] = "통과" if res is not None else "탈락"
        except Exception as e:
            modes[name] = f"에러: {e}"

    # 지표 스냅샷 (왜 탈락했는지 직접 보기 위한 값들)
    ma20 = float(c.rolling(20).mean().iloc[-1])
    ma60 = float(c.rolling(60).mean().iloc[-1])
    ma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    ret5 = (close / float(c.iloc[-6]) - 1) * 100 if len(c) >= 6 else None
    base_hi = float(h.iloc[-15:].max()); base_lo = float(lo.iloc[-15:].min())
    base_width = (base_hi - base_lo) / close * 100
    # 박스돌파 진단용 지표
    v = df["Volume"]
    vol_today = float(v.iloc[-1])
    vol_avg50 = float(v.iloc[-51:-1].mean()) if len(v) >= 51 else float(v.mean())
    vol_mult = round(vol_today / vol_avg50, 2) if vol_avg50 > 0 else None
    ma120 = float(c.rolling(120).mean().iloc[-1]) if len(c) >= 120 else None
    box_info = {}
    for win in (20, 40, 60):
        if len(h) >= win + 1:
            bh = float(h.iloc[-(win + 1):-1].max())
            bl = float(lo.iloc[-(win + 1):-1].min())
            box_info[f"박스{win}_폭%"] = round((bh - bl) / bh * 100, 1) if bh > 0 else None
            box_info[f"박스{win}_상단"] = round(bh)
            box_info[f"박스{win}_돌파여부"] = close > bh * 1.005

    # v5.34: RS 원점수 진단 — "몇 분기짜리인지"는 종목 하나만으로 계산 가능.
    # price_ago 재정규화(v5.32) 후 상장 200~252봉 종목이 3분기 점수인지
    # 눈으로 바로 확인하려고 추가. (v5.61: 백분위 자체는 위 rs_used로 확보됨)
    rs_raw = rs_raw_score(c)
    rs_q = rs_quarters_used(c)

    payload = {
        "ticker": ticker,
        "market": "KR" if is_kr else "US",
        "close": round(close),
        "modes": modes,
        "rs_percentile": rs_used,
        "rs_percentile_is_approx": rs_is_approx,
        "rs_percentile_note": (
            "⚠️ 근사치 — 유니버스 캐시가 콜드라 고정값 80 사용(정식 백분위 아님). "
            "스캐너 탭을 한 번 로드해 캐시를 데운 뒤 재조회하면 정식값이 반영됨."
            if rs_is_approx else
            "정식 유니버스 percentile RS (지수 대비 초과성과 기준)"
        ),
        "rs_raw_score": round(rs_raw, 4) if rs_raw is not None else None,
        "rs_quarters_used": rs_q,
        "indicators": {
            "ma20": round(ma20), "ma60": round(ma60),
            "ma200": round(ma200) if ma200 else None,
            "ma120": round(ma120) if ma120 else None,
            "close_vs_ma200_pct": round((close - ma200) / ma200 * 100, 1) if ma200 else None,
            "close_vs_ma120_pct": round((close - ma120) / ma120 * 100, 1) if ma120 else None,
            "정배열(20>60>200)": (ma200 is not None and ma20 > ma60 > ma200 and close > ma200),
            "ret5_pct": round(ret5, 1) if ret5 is not None else None,
            "base_width_15_pct": round(base_width, 1),
            "거래량배수(vs50일)": vol_mult,
            "박스돌파_기준": "거래량 1.5배+ & 120일선 위 & RS70+ & 박스폭30%이내 & 상단+0.5%돌파",
            **box_info,
        },
        "atr_median_pct": round(float(tr.iloc[-14:].median()) / close * 100, 2),
    }
    # v4.63: 수평 저항/지지 매물대 (터치 횟수 기반 — 스파이크 제외)
    try:
        from scanner import horizontal_levels
        _hl = horizontal_levels(h, lo, c)
        payload["수평저항"] = {
            "_주의": "참고용 터치-빈도 분석 — 실제 게이트가 쓰는 피벗과 다를 수 있음. 게이트기준_실제피벗 참조.",
            "추천피벗": _hl["pivot"],
            "피벗_터치횟수": _hl["pivot_touches"],
            "저항": [f"{r['price']} ({r['touches']}회, {r['dist_pct']:+}%)" for r in _hl["resistances"][:4]],
            "지지": [f"{s['price']} ({s['touches']}회, {s['dist_pct']:+}%)" for s in _hl["supports"][:3]],
        }
    except Exception as _e:
        payload["수평저항"] = {"error": str(_e)}
    # v5.39: 탈락 사유 재작성. 기존엔 box_info(단순 20/40/60봉 최고가, 터치
    # 필터 없음)로 만든 '가장 가까운 저항' 하나를 모든 모드에 공통 사유로
    # 붙였음 — 실제 게이트가 사용하는 select_pivot 값과 달라(DELL 486 vs
    # 실제 447.88) 통과한 게이트를 탈락 사유로 잘못 지목하는 사고가 났음.
    # 지금은 각 모드가 실제로 도는 게이트 순서를 _trace_*로 그대로 재현해
    # '어디서 True/False로 return None 됐는지'를 직접 갖고 온다.
    _mode_labels = {"pullback": "눌림목", "turnaround": "추세전환", "breakout": "돌파",
                    "boxbreak": "박스돌파", "imminent": "돌파임박"}
    reasons = []
    gate_trace_payload = {}
    real_pivots = {}
    for name, label in _mode_labels.items():
        t = traces.get(name, {})
        steps = t.get("steps", [])
        gate_trace_payload[label] = steps
        if t.get("pivot") is not None:
            entry = {
                "피벗": t["pivot"], "종류": t.get("pivot_type"),
                "near_pct": t.get("near_pct"), "risk_pct(카드기준)": t.get("risk_pct"),
                "게이트결과": "통과" if t.get("passed") else f"'{t.get('fail_at')}'에서 탈락",
            }
            # v5.41: 눌림목만 게이트(피벗기준)≠카드(현재가기준) risk_pct일 수
            # 있음 — Case13 회귀 방지 근거로 게이트만 피벗 기준 유지(CLAUDE.md
            # 참조). 돌파/박스돌파는 v5.41에서 카드 기준으로 통일해 항상 일치.
            if "gate_risk_pct" in t and t["gate_risk_pct"] != t.get("risk_pct"):
                entry["risk_pct(게이트기준,피벗)"] = t["gate_risk_pct"]
                entry["_주의"] = "게이트 판정과 카드 표시 risk_pct가 다름 — 게이트가 실제 통과/탈락을 결정한 값"
            real_pivots[label] = entry
        if t.get("passed"):
            continue
        if t.get("fail_at") == "에러":
            reasons.append(f"{label}: 진단 중 에러 — {steps[-1].get('detail') if steps else ''}")
            continue
        if not steps:
            reasons.append(f"{label}: 탈락(사유 추적 실패)")
            continue
        last = steps[-1]
        # v5.113(사용자 지시): 게이트는 순차 평가라 첫 탈락에서 멈춘다는 걸
        # 항상 명시. breakout/boxbreak/imminent 세 탭은 min_bars/rs_min에서
        # 걸리면 그 다음 게이트(고점대비낙폭)가 아예 평가되지 않는데, 이건
        # 저비용으로 재계산 가능해서 "미검사" 값이라도 참고용으로 병기한다
        # (다른 게이트들은 이전 게이트가 좁혀놓은 상태에 의존해 순서 밖에서
        # 계산하면 값이 왜곡될 수 있어 보류 — 근본원칙: 재현 불가능한 값을
        # 만들어 붙이느니 안 붙인다).
        extra = ""
        if name in ("breakout", "boxbreak", "imminent") and last["gate"] in ("min_bars", "min_bars_dropna", "rs_min"):
            try:
                from scanner import (off_high_pct as _ohp_fn, BREAKOUT_CONFIG as _bc,
                                     BOXBREAK_CONFIG as _xc, IMMINENT_CONFIG as _ic)
                _cfg_map = {"breakout": _bc, "boxbreak": _xc, "imminent": _ic}
                _cfg = _cfg_map[name]
                _ohp = _ohp_fn(c)
                extra = f" · [참고, 미검사] 고점대비 {_ohp:.1f}% (요구 -{_cfg['max_off_high']}% 이내)"
            except Exception:
                extra = ""
        reasons.append(f"{label}: [{last['gate']}] 탈락 — {last.get('detail')} (이후 조건은 미검사){extra}")
    if not reasons:
        reasons.append("5개 핵심 탭 모두 주요 게이트 통과 — RS/세부 조건에서 미세 탈락 가능성. modes와 게이트추적 대조 필요")
    # v5.61: RS가 근사치면 rs_min 게이트뿐 아니라 is_leader 분기(눌림목
    # pullback_min/rsi_max, leader_rs=90 경계 등) 전체가 실제와 다를 수
    # 있어 이 종목의 모든 탈락 사유에 캐치올로 붙인다 — rs_min 게이트 문구
    # 하나만 고치면 "RS가 진짜 원인인 다른 게이트"를 놓치기 때문.
    if rs_is_approx:
        reasons = [f"{r} [⚠️ RS 근사치({rs_used}) 기준 — 정식 백분위 아님]" for r in reasons]
    payload["탈락_핵심사유"] = reasons
    payload["게이트기준_실제피벗"] = real_pivots
    payload["게이트추적"] = gate_trace_payload
    # box_info/수평저항은 참고 정보일 뿐 위 게이트 판정에는 쓰이지 않음 — 실제
    # 판정 피벗은 게이트기준_실제피벗을 볼 것.
    payload["indicators"]["_주의"] = "박스20/40/60_상단은 터치 횟수 필터 없는 단순 최고가 — 실제 게이트 기준 아님. 게이트기준_실제피벗 참조."
    # v5.05: 실적(EPS/매출) 성장 진단 — Phase 1. 데이터 없으면 판정불가로 표시.
    try:
        _eg = await asyncio.get_event_loop().run_in_executor(_earnings_executor, _get_earnings_cached, ticker)
        payload["실적성장"] = {
            "판정": {"pass": "💰실적우수", "fail": "미충족", "unknown": "판정불가"}.get(_eg.get("verdict"), "판정불가"),
            "연간EPS_3년연속증가": _eg.get("annual_eps_growing"),
            "분기EPS_YoY%": _eg.get("quarterly_eps_yoy_pct"),
            "매출_YoY%": _eg.get("revenue_yoy_pct"),
            "증가율가속": _eg.get("accelerating"),
            "연간EPS": _eg.get("annual_eps"),
            "미충족사유": _eg.get("reasons"),
        }
    except Exception as _e:
        payload["실적성장"] = {"error": str(_e)}
    # v5.19: A-B-C 매집 스코어 진단 — 급등매집이 아니면 채점 대상 아님을 명시
    try:
        from scanner import analyze_pattern
        _pat = analyze_pattern(df, is_kr=is_kr)
        if _pat is None or _pat.get("pattern") != "급등매집":
            _found = _pat.get("pattern") if _pat else None
            payload["매집채점"] = {
                "상태": "급등매집 패턴 미검출 — 매집채점 대상 아님" + (f" (검출된 패턴: {_found})" if _found else ""),
            }
        else:
            payload["매집채점"] = {
                "A구간": f"{_pat.get('a_start_date')} ~ {_pat.get('a_end_date')}",
                "매집점수": _pat.get("accum_score"),
                "판정불가사유": _pat.get("accum_reason"),
                "시너지가점": _pat.get("accum_synergy"),
                "raw_parts": _pat.get("accum_parts"),
            }
    except Exception as _e:
        payload["매집채점"] = {"error": str(_e)}
    # ensure_ascii=False + charset 명시 → 모바일에서 한글 안 깨짐
    return Response(
        content=_json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
    )


_indices_cache: dict = {}
_INDICES_TTL = 300  # 지수+레짐 캐시 5분 (레짐 계산이 무거워 길게)
_fund_cache: dict = {}
_FUND_TTL = 3600    # 펀더멘털 캐시 1시간
_earnings_cache: dict = {}
_EARNINGS_TTL = 6 * 3600   # 실적 성장 캐시 6시간 — 실적은 분기 단위로만 바뀜 (v5.05)


def _get_earnings_cached(ticker: str) -> dict:
    """블로킹 — executor에서 실행. earnings.get_earnings_growth()를 6시간 캐시."""
    now = time.time()
    c = _earnings_cache.get(ticker)
    if c and now - c["ts"] < _EARNINGS_TTL:
        return c["data"]
    data = earnings_mod.get_earnings_growth(ticker)
    _earnings_cache[ticker] = {"ts": now, "data": data}
    return data


async def _attach_earnings_badges(hits: list) -> None:
    """실적 성장 배지(💰실적우수) — 스캔 결과 hits에 in-place로 필드 부착
    (v5.05, Phase 1). 유니버스 전체가 아니라 이미 필터를 통과해 최종 목록에
    남은 종목에만 적용(개수가 적어 부담 적음) + 6시간 캐시 + 배치 동시 실행
    이라 반복 스캔은 거의 즉시, 첫 스캔만 종목당 네트워크 왕복 비용."""
    if not hits:
        return
    tickers = [h["ticker"] for h in hits]
    BATCH = 6   # v5.17: _earnings_executor의 max_workers(6)에 맞춤
    results = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        chunk_results = await asyncio.gather(
            *[_get_earnings_safe(t) for t in chunk], return_exceptions=True
        )
        for t, r in zip(chunk, chunk_results):
            results[t] = r if isinstance(r, dict) else {}
    for h in hits:
        r = results.get(h["ticker"]) or {}
        h["earnings_verdict"] = r.get("verdict")       # pass|fail|unknown
        h["earnings_badge"] = (r.get("verdict") == "pass")
        h["eps_yoy_pct"] = r.get("quarterly_eps_yoy_pct")
        h["revenue_yoy_pct"] = r.get("revenue_yoy_pct")
        h["annual_eps_growing"] = r.get("annual_eps_growing")


def _fetch_nasdaq() -> dict | None:
    """나스닥 종합(^IXIC) 현재값 + 등락. yfinance."""
    return _fetch_yf_index("^IXIC", "나스닥")


def _fetch_yf_index(symbol: str, label: str, decimals: int = 2) -> dict | None:
    """yfinance 심볼의 현재값 + 등락 (지수·코인 공용).
    주말/휴장 대비: 10일치를 받아 마지막 거래일 종가를 잡고,
    데이터가 1개뿐이어도 등락 없이 종가만이라도 표시한다."""
    try:
        df = yf.Ticker(symbol).history(period="10d", interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return None
        closes = df["Close"].dropna()
        if closes.empty:
            return None
        last = float(closes.iloc[-1])
        if len(closes) >= 2:
            prev = float(closes.iloc[-2])
            chg = last - prev
            pct = (last / prev - 1) * 100 if prev > 0 else 0.0
        else:
            chg, pct = 0.0, 0.0  # 데이터 1개뿐이면 등락 0으로 표시(종가만이라도)
        return {"name": label, "value": round(last, decimals),
                "change": round(chg, decimals), "change_pct": round(pct, 2)}
    except Exception:
        return None


# ── v4.57: 지수별 거래량 소스. 지수 Volume이 무효면 대표 ETF로 폴백 ──
INDEX_SPEC = {
    "KOSPI":  {"label": "코스피",  "vol_proxy": None},
    "KOSDAQ": {"label": "코스닥",  "vol_proxy": None},
    "^IXIC":  {"label": "나스닥",  "vol_proxy": "QQQ"},
    "^GSPC":  {"label": "S&P500", "vol_proxy": "SPY"},
}


def _volume_valid(vol) -> bool:
    """지수 거래량이 분산일 판정에 쓸 만한가. 전부 0/NaN이거나 결측 30%↑면 무효."""
    try:
        if vol is None or len(vol) < 30:
            return False
        tail = vol.iloc[-30:]
        if float(tail.fillna(0).sum()) <= 0:
            return False
        if int(tail.isna().sum()) > 9:
            return False
        return True
    except Exception:
        return False


def _fetch_proxy_volume(ticker: str, n: int):
    """대표 ETF(SPY/QQQ) 거래량. 지수 Volume 무효 시 폴백."""
    try:
        df = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=False)
        if df is None or df.empty or "Volume" not in df:
            return None
        v = df["Volume"].dropna()
        return v if len(v) >= n else None
    except Exception:
        return None


def _index_regime(code: str) -> dict | None:
    """지수 일봉으로 시장 레짐 판정 (오닐/미너비니 M factor). v4.57 전면 개편.
    code: 'KOSPI'|'KOSDAQ'|'^IXIC'|'^GSPC'.

    [v4.57 근본수정]
      ① 분산일 카운트를 scanner.dist_count()로 분리 — FTD 리셋 + 5% 만료 적용
         (기존 순수 25일 롤링은 늘어나기만 하고 안 빠져 한 달간 correction에 갇힘)
      ② gate_suggest가 FTD를 분산일보다 먼저 평가 (기존 early return이 FTD 분기를
         죽은 코드로 만듦)
      ③ 지수 Volume 유효성 검증 + ETF 폴백 + 실패 시 dist_days=None(판정 불가).
         0으로 위장하면 "분산일 없음=건강"이라는 반대 신호가 됨.
    """
    try:
        spec = INDEX_SPEC.get(code, {})
        close, vol = None, None
        if code in ("^IXIC", "^GSPC"):
            df = yf.Ticker(code).history(period="6mo", interval="1d", auto_adjust=False)
            if df is None or df.empty:
                return None
            close = df["Close"].dropna()
            vol = df["Volume"] if "Volume" in df.columns else None
        else:
            hist = naver_kr.fetch_index_history(code, days=160)
            if hist is None or hist.empty:
                return None
            close = hist["Close"]
            vol = hist["Volume"] if "Volume" in hist.columns else None
        if close is None or len(close) < 60:
            return None

        # ── 거래량 소스 결정 (검증 → 폴백 → 포기) ──
        vol_source = "none"
        if _volume_valid(vol):
            vol_source = "index"
            vol = vol.reindex(close.index)
        else:
            proxy = spec.get("vol_proxy")
            if proxy:
                pv = _fetch_proxy_volume(proxy, len(close))
                if pv is not None and _volume_valid(pv):
                    aligned = pv.reindex(close.index)
                    if _volume_valid(aligned):
                        vol = aligned
                        vol_source = proxy
                    else:
                        vol = None
                else:
                    vol = None
            else:
                vol = None

        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        cur = float(close.iloc[-1])
        m20 = float(ma20.iloc[-1])
        m60 = float(ma60.iloc[-1])
        m20_prev = float(ma20.iloc[-6])
        rising20 = m20 > m20_prev
        above60 = cur > m60

        # ── FTD 상태 머신 ──
        if vol is not None:
            fs = scanner_mod.ftd_state(close, vol)
        else:
            fs = {"in_correction": False, "rally_day": 0, "ftd": False,
                  "ftd_days_ago": None, "ftd_idx_back": None, "rally_low": None,
                  "peak_before": None, "drawdown_pct": 0.0, "recovered": False}

        # ── 분산일 (FTD 리셋 + 5% 만료 적용) ──
        dc = scanner_mod.dist_count(close, vol, fs)
        ftd = bool(fs.get("ftd"))
        gate_sug, gate_why = scanner_mod.gate_suggest(dc, fs, above60)

        # ── 배너 표시용 레짐 (게이트 3색과 정합) ──
        # v5.93(사용자 지시): 게이트 조건부 EV 실측(2026-08-29,
        # docs/pullback_ev_kr_us_regime_investigation.md 7절 — US z=-3.15
        # 역방향)에서 게이트가 개별 신호 EV를 예측 못 함을 확인 — "신규진입
        # 자제"/"선별 진입"/"진입 환경 양호" 같은 처방형 문구 제거, 시장
        # 상태 서술로만 남김. gate_sug/gate_suggest() 판정 로직 자체는 불변
        # (R 노출 설정 자동 제안 등 다른 기능이 여전히 이 값을 씀).
        d = dc.get("days")
        if gate_sug == "correction":
            regime, txt = "bad", "비우호"
        elif gate_sug == "pressure":
            regime, txt = "neutral", "주의"
        else:
            regime, txt = "good", "우호"
        txt += f" · {gate_why}"

        return {"regime": regime, "regime_txt": txt,
                "above_ma20": cur > m20, "above_ma60": above60,
                "ma20_rising": rising20,
                "dist_days": d,
                "dist_raw": dc.get("raw"),
                "dist_expired": dc.get("expired"),
                "dist_pre_ftd": dc.get("pre_ftd"),
                "vol_source": vol_source,
                "ftd": ftd,
                "ftd_days_ago": fs.get("ftd_days_ago"),
                "rally_day": fs.get("rally_day", 0),
                "in_correction": fs.get("in_correction", False),
                "recovered": fs.get("recovered", False),
                "drawdown_pct": fs.get("drawdown_pct", 0.0),
                "gate_suggest": gate_sug, "gate_why": gate_why}
    except Exception:
        return None


# 게이트 강도 순서. 여러 지수 중 '가장 나쁜' 쪽 채택용.
_GATE_RANK = {"confirmed": 0, "pressure": 1, "correction": 2}
# 게이트별 신규 진입 오픈 리스크 배수 (max_open_r 대비). v4.47 R설정에 이미 정의된 규칙.
_GATE_R = {"confirmed": 1.0, "pressure": 0.5, "correction": 0.0}


def _worst_gate(gates) -> str:
    valid = [g for g in gates if g]
    if not valid:
        return "correction"
    return max(valid, key=lambda g: _GATE_RANK.get(g, 2))


@app.get("/api/market/gate")
async def market_gate():
    """시장 게이트 자동 제안 (v4.57) — 4개 지수(KOSPI/KOSDAQ/^GSPC/^IXIC).
    기존엔 KOSPI 하나만 봐서 미국 종목 알림에 쓸 게이트가 없었다."""
    loop = asyncio.get_event_loop()
    codes = ["KOSPI", "KOSDAQ", "^GSPC", "^IXIC"]
    regs = await asyncio.gather(*[
        loop.run_in_executor(_executor, _index_regime, c) for c in codes
    ], return_exceptions=True)

    out_idx = {}
    for code, reg in zip(codes, regs):
        if isinstance(reg, BaseException) or not reg:
            out_idx[code] = None
            continue
        out_idx[code] = {
            "label": INDEX_SPEC[code]["label"],
            "gate": reg.get("gate_suggest"), "why": reg.get("gate_why"),
            "dist_days": reg.get("dist_days"), "dist_raw": reg.get("dist_raw"),
            "dist_expired": reg.get("dist_expired"), "dist_pre_ftd": reg.get("dist_pre_ftd"),
            "vol_source": reg.get("vol_source"),
            "ftd": reg.get("ftd"), "ftd_days_ago": reg.get("ftd_days_ago"),
            "rally_day": reg.get("rally_day"), "in_correction": reg.get("in_correction"),
            "recovered": reg.get("recovered"), "drawdown_pct": reg.get("drawdown_pct"),
            "above_ma60": reg.get("above_ma60"),
        }

    if not any(out_idx.values()):
        return JSONResponse({"ok": False, "error": "전 지수 조회 실패"}, status_code=503)

    def g(code):
        v = out_idx.get(code)
        return v.get("gate") if v else None

    gate_kr = _worst_gate([g("KOSPI"), g("KOSDAQ")])
    gate_us = _worst_gate([g("^GSPC"), g("^IXIC")])
    suggest = _worst_gate([gate_kr, gate_us])

    why = ""
    for code in codes:
        v = out_idx.get(code)
        if v and v.get("gate") == suggest:
            why = f"{v['label']}: {v['why']}"
            break

    cur = dict(RSETTINGS_DEFAULT)
    if os.path.exists(RSETTINGS_PATH):
        try:
            with open(RSETTINGS_PATH, encoding="utf-8") as f:
                saved = _json.load(f)
                if isinstance(saved, dict):
                    cur.update(saved)
        except (ValueError, OSError):
            pass

    base_r = float(cur.get("max_open_r", 3.0))
    ftd_any = any(v.get("ftd") for v in out_idx.values() if v)

    return JSONResponse(_clean_nan({
        "ok": True,
        "indices": out_idx,
        "gate_kr": gate_kr, "gate_us": gate_us,
        "max_open_r_kr": round(base_r * _GATE_R.get(gate_kr, 0.0), 2),
        "max_open_r_us": round(base_r * _GATE_R.get(gate_us, 0.0), 2),
        # 기존 봇 호환 필드
        "suggest": suggest, "why": why,
        "current": cur.get("gate"), "ftd": ftd_any,
    }))


@app.get("/api/krstatus")
async def kr_status():
    """KR 동적 유니버스 로딩 진단. pykrx 설치/KRX 접근 실패 원인 확인용.
    예: /api/krstatus → {pykrx_installed, dynamic_count, last_error, ...}"""
    from universe import kr_dynamic_status
    return JSONResponse(kr_dynamic_status())


def _clean_nan(obj):
    """JSON 직렬화 전 NaN/Inf를 None으로 치환 (휴장일 등 빈 데이터 대비).
    재귀적으로 dict/list 내부까지 정리한다."""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_nan(v) for v in obj]
    return obj


_eod_cache: dict = {}
_EOD_TTL = 600  # 10분 캐시


@app.get("/api/eod")
async def eod_summary():
    """코스피/코스닥 마감 정리 — 상한가 종목 + 거래대금 상위 (v4.83) + 섹터
    로테이션(v4.97).
    - 상한가: 국내 가격제한폭(±30%)에 근접(29.5%+)한 당일 등락률. 캐시된 KR
      일봉 전체를 훑어 계산(추가 네트워크 호출 없음).
    - 거래대금 상위: 네이버 거래대금 상위 페이지의 등장 순서를 그대로 순위로
      사용(=실제 거래대금 순위). 표시용 종가/등락률만 캐시된 일봉에서 조회.
    - 섹터 로테이션: 상승률 상위 섹터(유니버스 평균 등락%) + 거래대금 상위
      섹터(거래대금 상위 120종목의 섹터별 등장 빈도).

    v4.99 [버그수정] 장중에 숫자가 계속 바뀌던 문제. "마감정리"라는 탭 이름과
    달리, 예전엔 10분 TTL만 보고 계속 재계산해서 장중엔 그 순간의 미확정
    등락률·거래대금 순위가 계속 반영됐음(사용자 피드백: "장이 열리면 전날
    자료 기준으로 그대로 유지해야 할 것 같은데 장중에 계속 로딩함"). 이제는
    _market_session_key("kr")가 None(=오늘 KR장이 아직 마감 전)이면 마지막으로
    "마감 확정" 상태에서 계산해둔 스냅샷을 그대로 반환 — 재계산 자체를 안 함.
    장 마감(평일 15:40 KST) 후 딱 1번만 새로 계산해서 스냅샷을 갱신한다."""
    daykey = _market_session_key("kr")   # None이면 오늘 KR장 아직 마감 전
    if daykey is None and _eod_cache.get("data"):
        # 스냅샷은 마감 확정 상태에서 계산된 것 그대로 반환 — date/market_closed도
        # 그 마감일 기준을 유지(진짜 "장중"이 아니라 "직전 마감"이 맞는 표현).
        return JSONResponse(_eod_cache["data"])
    now = time.time()
    if (daykey and _eod_cache.get("closed_daykey") == daykey
            and now - _eod_cache.get("ts", 0) < _EOD_TTL):
        return JSONResponse(_eod_cache["data"])

    bundle = await _fetch_market_data("kr")
    if bundle is None:
        # v5.12: 콜드 스타트라 백그라운드로 수집 중 — 잠시 후 재요청하면 됨.
        return JSONResponse({"pending": True, "date": "", "market_closed": False,
                             "breadth": {}, "limit_up": [], "top_value": [],
                             "sector_rise": [], "sector_value": []})
    data = bundle.get("data", {})
    universe = bundle.get("universe", {})

    limit_up = []
    # v4.94: 코스피/코스닥 상승·하락 종목 수 (등락 폭 무관, 부호만) — 같은 순회에서 집계.
    breadth = {"KOSPI": {"up": 0, "down": 0, "flat": 0}, "KOSDAQ": {"up": 0, "down": 0, "flat": 0}}
    for t, df in data.items():
        if df is None or len(df) < 2:
            continue
        try:
            c = df["Close"]
            close, prev = float(c.iloc[-1]), float(c.iloc[-2])
            if prev <= 0:
                continue
            chg = (close / prev - 1) * 100
            mkt_key = "KOSPI" if t.endswith(".KS") else ("KOSDAQ" if t.endswith(".KQ") else None)
            if mkt_key:
                bucket = "up" if chg > 0 else ("down" if chg < 0 else "flat")
                breadth[mkt_key][bucket] += 1
            if chg >= 29.5:
                limit_up.append({"ticker": t, "name": universe.get(t, t), "market": "KR",
                                 "close": round(close, 2), "change_pct": round(chg, 2)})
        except Exception:
            continue
    limit_up.sort(key=lambda x: -x["change_pct"])

    top_value = []
    tv = {}
    try:
        loop = asyncio.get_event_loop()
        # v4.97: 섹터별 거래대금 집중도 집계에 쓸 표본을 늘리려 40→120.
        tv = await loop.run_in_executor(_executor, naver_kr.fetch_top_value, 120)
        for t, name in tv.items():
            df = data.get(t)
            if df is None or len(df) < 2:
                continue
            c = df["Close"]
            close, prev = float(c.iloc[-1]), float(c.iloc[-2])
            chg = (close / prev - 1) * 100 if prev > 0 else 0.0
            top_value.append({"ticker": t, "name": name, "market": "KR",
                              "close": round(close, 2), "change_pct": round(chg, 2)})
    except Exception:
        pass

    # v4.97 [신규] 섹터 로테이션 — 한국 시장은 순환매가 심해 "오늘 어느 섹터가
    # 뜨는지"가 개별 종목보다 먼저 봐야 할 신호. 두 축으로 집계:
    #   1) 상승률 상위 섹터: 유니버스 전 종목 등락률을 섹터로 묶어 평균(생존자 ≥2).
    #   2) 거래대금 상위 섹터: 네이버 거래대금 순위 상위 120종목이 어느 섹터에
    #      몰렸는지 종목 수로 집계(실제 거래대금 금액은 스크레이핑 소스에 없어
    #      순위 등장 빈도를 프록시로 사용 — /api/sectors의 주도업종 집계와
    #      같은 "생존자 카운트" 철학).
    from collections import defaultdict as _dd
    sector_chg = _dd(list)
    for t, df in data.items():
        if not naver_kr.is_kr(t) or df is None or len(df) < 2:
            continue
        try:
            c = df["Close"]
            close, prev = float(c.iloc[-1]), float(c.iloc[-2])
            if prev <= 0:
                continue
            chg = (close / prev - 1) * 100
        except Exception:
            continue
        if chg > 31 or chg < -31:
            continue
        sec = _sector_of(t)
        if sec == "기타":
            continue
        sector_chg[sec].append((t, universe.get(t, t), chg))
    sector_rise = []
    for sec, items in sector_chg.items():
        if len(items) < 2:
            continue
        avg = sum(x[2] for x in items) / len(items)
        top = sorted(items, key=lambda x: -x[2])[:3]
        sector_rise.append({
            "sector": sec, "n": len(items), "avg_chg": round(avg, 2),
            "top": [{"ticker": tk, "name": nm, "chg": round(cg, 1)} for tk, nm, cg in top],
        })
    sector_rise.sort(key=lambda r: -r["avg_chg"])

    sector_value_count: dict = _dd(int)
    sector_value_top: dict = _dd(list)
    for t, name in tv.items():
        sec = _sector_of(t)
        if sec == "기타":
            continue
        sector_value_count[sec] += 1
        if len(sector_value_top[sec]) < 3:
            sector_value_top[sec].append({"ticker": t, "name": name})
    sector_value = [
        {"sector": sec, "n": cnt, "top": sector_value_top[sec]}
        for sec, cnt in sector_value_count.items()
    ]
    sector_value.sort(key=lambda r: -r["n"])

    result = {
        "date": daykey or datetime.now(KST).strftime("%Y-%m-%d"),
        "market_closed": bool(daykey),
        "breadth": breadth,
        "limit_up": limit_up,
        "top_value": top_value,
        "sector_rise": sector_rise[:8],
        "sector_value": sector_value[:8],
    }
    result = _clean_nan(result)
    _eod_cache["ts"] = now
    _eod_cache["data"] = result
    if daykey:
        # 마감 확정된 결과만 "스냅샷"으로 표시 — 다음 장중엔 이 스냅샷을 그대로 재사용.
        _eod_cache["closed_daykey"] = daykey
    return JSONResponse(result)


@app.get("/api/indices")
async def indices():
    """상단 지수 바: 코스피/코스닥/나스닥/닛케이/비트코인. 60초 캐시."""
    try:
        return await _indices_impl()
    except Exception as e:
        import traceback
        return JSONResponse({"error": f"{type(e).__name__}: {e}",
                             "trace": traceback.format_exc()[-2000:]}, status_code=200)


async def _indices_impl():
    now = time.time()
    if _indices_cache and now - _indices_cache.get("ts", 0) < _INDICES_TTL:
        return JSONResponse(_indices_cache["data"])

    loop = asyncio.get_event_loop()
    # return_exceptions=True: 8개 중 하나가 예외를 던져도 나머지는 살린다.
    # (네이버/야후 중 하나만 실패해도 지수 바 전체가 500으로 죽던 버그 수정)
    results = await asyncio.gather(
        loop.run_in_executor(_executor, _fetch_nasdaq),
        loop.run_in_executor(_executor, naver_kr.fetch_index, "KOSPI"),
        loop.run_in_executor(_executor, naver_kr.fetch_index, "KOSDAQ"),
        loop.run_in_executor(_executor, _index_regime, "KOSPI"),
        loop.run_in_executor(_executor, _index_regime, "KOSDAQ"),
        loop.run_in_executor(_executor, _index_regime, "^IXIC"),
        loop.run_in_executor(_executor, _fetch_yf_index, "^N225", "닛케이"),
        loop.run_in_executor(_executor, _fetch_yf_index, "BTC-USD", "비트코인"),
        # v4.57: S&P500 추가 (게이트 4개 지수 확장 — 배너에도 표시)
        loop.run_in_executor(_executor, _fetch_yf_index, "^GSPC", "S&P500"),
        loop.run_in_executor(_executor, _index_regime, "^GSPC"),
        return_exceptions=True,
    )
    # 예외로 돌아온 결과는 None으로 정규화 (개별 실패가 전체를 죽이지 않게)
    nasdaq, kospi, kosdaq, r_kospi, r_kosdaq, r_nasdaq, nikkei, btc, sp500, r_sp500 = [
        (None if isinstance(x, BaseException) else x) for x in results
    ]
    # 레짐 정보 병합 (둘 다 dict일 때만)
    if isinstance(kospi, dict) and isinstance(r_kospi, dict): kospi.update(r_kospi)
    if isinstance(kosdaq, dict) and isinstance(r_kosdaq, dict): kosdaq.update(r_kosdaq)
    if isinstance(nasdaq, dict) and isinstance(r_nasdaq, dict): nasdaq.update(r_nasdaq)
    if isinstance(sp500, dict) and isinstance(r_sp500, dict): sp500.update(r_sp500)
    # 순서: 코스피, 코스닥, S&P500, 나스닥, 닛케이, 비트코인
    data = {"indices": [x for x in (kospi, kosdaq, sp500, nasdaq, nikkei, btc) if isinstance(x, dict)]}
    data = _clean_nan(data)   # NaN/Inf 제거 (휴장일 빈 데이터로 인한 JSON 직렬화 실패 방지)
    _indices_cache["ts"] = now
    _indices_cache["data"] = data
    return JSONResponse(data)


@app.get("/api/fundamentals/{ticker}")
async def fundamentals(ticker: str):
    """카드 '밸류 보기' 클릭 시 온디맨드. 종목 1개 펀더멘털. 캐시 1시간."""
    now = time.time()
    cached = _fund_cache.get(ticker)
    if cached and now - cached["ts"] < _FUND_TTL:
        return JSONResponse(cached["data"])
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(_executor, fundamentals_mod.get_fundamentals, ticker)
    result = data or {"error": "데이터 없음"}
    _fund_cache[ticker] = {"ts": now, "data": result}
    return JSONResponse(result)


@app.get("/api/earnings/{ticker}")
async def earnings_growth(ticker: str):
    """실적(EPS/매출) 성장 판정 (v5.05, Phase 1) — 미너비니 CAN SLIM 기준.
    3년 연간 EPS 연속증가 + 최근분기 EPS YoY 25%+ + 매출 YoY 동반증가(+선택
    가속 여부). 데이터 없으면 제외가 아니라 판정불가(verdict=unknown)로
    반환. 6시간 캐시(실적은 분기 단위로만 바뀜)."""
    ticker = ticker.upper().strip()
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(_earnings_executor, _get_earnings_cached, ticker)
    return JSONResponse(data)


# ── 매매 일지 (서버 저장, 기기 간 동기화) ──
def _resolve_journal_path() -> str:
    """일지 저장 경로. 배포(railway up) 때 안 지워지도록 영구 볼륨 우선.
    우선순위: 1) 환경변수 JOURNAL_DIR  2) /data (Railway 볼륨 마운트 기본)
              3) 코드 폴더 (로컬 개발 폴백 — 배포 시엔 휘발됨)
    """
    candidates = []
    env_dir = os.environ.get("JOURNAL_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates.append("/data")                       # Railway 영구 볼륨 표준 마운트
    candidates.append(os.path.dirname(__file__))     # 폴백(로컬)
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            # 쓰기 가능 여부 확인
            test = os.path.join(d, ".write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.remove(test)
            return os.path.join(d, "journal_user.json")
        except OSError:
            continue
    # 최후 폴백
    return os.path.join(os.path.dirname(__file__), "journal_user.json")


JOURNAL_PATH = _resolve_journal_path()


def load_journal() -> list:
    """매매 일지 전체 (객체 배열)."""
    if os.path.exists(JOURNAL_PATH):
        try:
            with open(JOURNAL_PATH, encoding="utf-8") as f:
                data = _json.load(f)
                return data if isinstance(data, list) else []
        except (ValueError, OSError):
            return []
    return []


@app.get("/api/journal")
async def get_journal():
    return JSONResponse(load_journal())


@app.get("/api/watch/pending")
async def watch_pending():
    """대기(pending) 상태 + 피벗가 있는 종목만 노출 — 텔레그램 봇이 읽어
    피벗 돌파를 감시/알림하기 위한 엔드포인트.
    반환: [{ticker, name, market, pivot, entry, stop, tab}, ...]"""
    out = []
    for r in load_journal():
        if r.get("status") != "pending":
            continue
        # pivot 우선, 없으면 entry(진입가=베이스천장)로 대체 (v4.37.11 이전 항목 호환)
        pivot = r.get("pivot") or r.get("entry")
        if not pivot:
            continue
        # 데이터 오류(손절≥진입) 항목은 봇 감시 제외
        try:
            e, s = float(r.get("entry") or 0), float(r.get("stop") or 0)
            if e and s and s >= e:
                continue
        except (TypeError, ValueError):
            pass
        out.append({
            "id": r.get("id"),
            "ticker": r.get("ticker"),
            "name": r.get("name"),
            "market": r.get("market"),
            "pivot": pivot,
            "entry": r.get("entry"),
            "stop": r.get("stop"),
            # 하향 목표가 (v4.53.6): 진입가가 있으면 봇이 '이 가격까지 내려오면'
            # 알림. 눌림목 대기(RCUS $30→$26)용. 봇에서 현재가와 비교.
            "target_below": r.get("entry"),
            "category": r.get("category") or r.get("cat") or "",
            "tab": r.get("tab", ""),
        })
    return JSONResponse({"pending": out, "count": len(out)})


@app.get("/api/ma/{ticker}")
async def moving_averages(ticker: str):
    """종목의 오늘 이평선 값 (v4.53) — 봇의 이평 알림용.
    캐시된 일봉에서 10/20/50일선 + 현재가 + 오늘 종가가 각 이평 위/아래인지.
    이평은 매일 바뀌므로 봇이 매번 최신값을 받아 현재가와 비교."""
    ticker = ticker.upper().strip()
    df = None
    for key in ("data:all", "data:kr", "data:us"):   # v5.110[버그수정]: 실제 캐시 키는 소문자(_fetch_market_data의 f"data:{market}")
        bundle = _data_cache.get(key)
        if bundle and ticker in bundle.get("data", {}):
            df = bundle["data"][ticker]
            break
    if df is None:
        try:
            df = await asyncio.get_event_loop().run_in_executor(_executor, _fetch, ticker)
        except Exception:
            df = None
    if df is None or len(df) < 50:
        return JSONResponse({"ok": False}, status_code=404)
    try:
        c = df["Close"]
        close = float(c.iloc[-1])
        prev = float(c.iloc[-2])
        out = {"ok": True, "ticker": ticker, "close": round(close, 2),
               "prev_close": round(prev, 2)}
        for w in (10, 20, 50):
            if len(c) >= w:
                ma = float(c.rolling(w).mean().iloc[-1])
                ma_prev = float(c.rolling(w).mean().iloc[-2])
                out[f"ma{w}"] = round(ma, 2)
                # 오늘 종가가 이평 위/아래 + 어제 대비 방금 이탈했는지
                out[f"below{w}"] = close < ma
                out[f"broke{w}"] = (close < ma and prev >= ma_prev)   # 오늘 하향 이탈
                out[f"dist{w}_pct"] = round((close / ma - 1) * 100, 2) if ma > 0 else None
        return JSONResponse(out)
    except Exception:
        return JSONResponse({"ok": False}, status_code=500)


@app.get("/api/jongga/candidates")
async def jongga_candidates():
    """🇰🇷 종가베팅 오늘의 후보 — 얼마냐봇(외부 텔레그램 봇, /api/ma
    폴링 방식과 동일)이 14:50경 폴링해서 자체적으로 메시지를 만들어
    보내는 용도(v5.97, 사용자 지시 7번). 이 레포엔 텔레그램 발송 코드가
    없어(얼마냐봇은 별도 레포) 데이터만 제공 — 문구 형식 제안:
    '🌆 오늘의 종가베팅 후보 N개: 종목명(+등락%, 거래대금 N위)...'
    후보 0개면 봇이 침묵하도록 candidates=[]로 응답(발송 여부는 봇 쪽 로직)."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    trading_day = is_trading_day("kr", today)   # v5.99: 봇이 이중 확인할 수 있게 노출
    cached = _cache.get("kr:jongga")
    if not cached:
        return JSONResponse({"ok": True, "date": None, "candidates": [], "count": 0,
                              "trading_day": trading_day})
    hits = cached.get("hits", [])
    candidates = [
        {"ticker": h["ticker"], "name": h.get("name", h["ticker"]),
         "change_pct": h.get("change_pct"), "turnover_rank": h.get("turnover_rank")}
        for h in hits
    ]
    return JSONResponse({
        "ok": True, "date": cached.get("daykey") or cached.get("generated_at"),
        "candidates": candidates, "count": len(candidates),
        "trading_day": trading_day,
        "message_format_hint": "🌆 오늘의 종가베팅 후보 {count}개: {name}(+{change_pct}%, 거래대금 {turnover_rank}위), ...",
    })


@app.get("/api/jongga/forward")
async def jongga_forward():
    """🇰🇷 종가베팅 포워드 트래킹 누적 통계(v5.98, 사용자 지시) — 백테스트
    (+1.22%, n=276, z=4.28)와 실전 결과를 계속 대조하기 위한 엔드포인트.
    스냅샷가 기준/확정종가 기준을 분리 계산(모듈 상단 _resolve_jongga_gaps
    docstring 참고 — 실전 진입가는 그 사이 어딘가라 어느 한쪽만 쓰면 왜곡)."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return JSONResponse(_clean_nan({**_jongga_forward_stats(), "trading_day": is_trading_day("kr", today)}))


def _kr_turnover_rank_map(kr_data: dict) -> dict:
    """전체 KR 티커의 당일 거래대금(종가×거래량) 순위 — _run_scan_jongga
    의 turnover_rank_at()과 같은 정의(신규 지표라 재구현 금지 원칙 대상
    아님 — 프로덕션에 이미 있는 함수를 다시 만드는 게 아니라, 애초에
    별도 함수 스코프라 공용화하지 않은 계산식을 여기서도 한 번 더 씀)."""
    turnovers = {}
    for t, df in kr_data.items():
        c, v = df.get("Close"), df.get("Volume")
        if c is None or v is None or len(c) < 1 or len(v) < 1:
            continue
        try:
            turnovers[t] = float(c.iloc[-1]) * float(v.iloc[-1])
        except Exception:
            continue
    ranked = sorted(turnovers.items(), key=lambda kv: kv[1], reverse=True)
    return {t: i + 1 for i, (t, _) in enumerate(ranked)}


@app.get("/api/theme_map")
async def theme_map_list():
    """저장된 테마 매핑 목록(경량 뷰, UI 아직 없음 — 사용자 지시 6번)."""
    return JSONResponse(theme_map.list_all())


@app.get("/api/theme_map/{theme_name}")
async def theme_map_get(theme_name: str):
    """테마 매핑 조회 + 각 종목 당일 거래대금 순위 병기(동적, 사용자
    지시 5번) — 생성 시점의 정적 rank(대장주 서열)와 별도 필드로 공존."""
    entry = theme_map.get(theme_name)
    if entry is None:
        return JSONResponse(
            {"error": f"'{theme_name}' 매핑 없음 — POST /api/theme_map/{theme_name}으로 생성"},
            status_code=404)
    bundle = await _fetch_market_data("kr")
    rank_map = _kr_turnover_rank_map(bundle["data"]) if bundle else {}
    stocks = [{**s, "turnover_rank_today": rank_map.get(s["ticker"])} for s in entry.get("stocks", [])]
    return JSONResponse(_clean_nan({**entry, "stocks": stocks, "theme": theme_name,
                                     "stale": theme_map.is_stale(entry)}))


# v5.123[버그수정]: 생성(Claude+web_search)이 길면 Railway 프록시가 응답을
# 못 기다리고 upstream error로 끊어버림(실사용자 재현) — 동기 응답을
# 비동기 작업(job)으로 전환. POST는 즉시 202+job_id만 반환하고 실제 생성은
# asyncio.create_task로 백그라운드에서 진행(이 파일의 기존 fire-and-forget
# 관례 — _run_money_flow_bg/_refresh_macro_calendar_bg와 동일 패턴 재사용),
# GET /api/theme_map/jobs/{job_id}로 상태를 폴링한다. job 상태는 다른
# 인메모리 캐시들과 동일하게 재배포 시 초기화됨(영속화 불필요 — 생성
# 자체가 몇 분 내 끝나는 일회성 작업).
_theme_map_jobs: dict = {}  # {job_id: {"status", "theme", "result", "error", "created_at"}}


async def _theme_map_generate_job(job_id: str, theme_name: str):
    job = _theme_map_jobs[job_id]
    job["status"] = "running"
    try:
        bundle = await _fetch_market_data("kr", wait_for_fresh=True)
        if not bundle:
            job["status"] = "error"
            job["error"] = "KR 시장 데이터 로드 실패"
            return
        loop = asyncio.get_event_loop()
        entry = await loop.run_in_executor(_executor, theme_map.generate_theme_map, theme_name, bundle["universe"])
        if entry.get("error"):
            job["status"] = "error"
            job["error"] = entry["error"]
            return
        entry["trigger"] = "manual"   # v5.127: 자동/수동 한도 분리 카운트용
        theme_map.save_theme_map(theme_name, entry)
        job["status"] = "done"
        job["result"] = _clean_nan({"theme": theme_name, **entry})
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        _theme_map_generating.discard(theme_name)


_theme_map_generating: set = set()   # v5.125: 진행 중인 테마명 — 같은 테마 중복 호출 방지


@app.post("/api/theme_map/{theme_name}")
async def theme_map_generate(theme_name: str):
    """수동 생성(사용자 지시 4번) — 자동 생성과 별도 일일 한도를 쓴다
    (v5.127, 사용자 지시: 자동 트리거가 그날 한도를 먼저 써버려 수동 생성이
    막히던 문제 수정 — theme_map.AUTO_DAILY_LIMIT/MANUAL_DAILY_LIMIT로
    분리, entry의 "trigger" 필드로 구분 카운트). v5.123부터 비동기 job —
    즉시 202+job_id 반환, 실제 생성은 백그라운드, 결과는
    GET /api/theme_map/jobs/{job_id}로 조회. v5.125(사용자 지시, API 비용
    급증 조사): 같은 테마에 대해 이미 진행 중인 job이 있으면 새 job을
    또 안 만듦 — daily_generation_count는 job이 완료(save_theme_map)돼야
    올라가서, 완료 전에 같은 테마로 재요청(더블클릭·재시도 스크립트)이
    들어오면 이 가드 없이는 Claude+웹서치 호출이 중복으로 나갔다."""
    if theme_name in _theme_map_generating:
        return JSONResponse({"error": f"'{theme_name}' 이미 생성 중 — 잠시 후 다시 시도"}, status_code=429)
    if theme_map.today_generation_count(trigger="manual") >= theme_map.MANUAL_DAILY_LIMIT:
        return JSONResponse(
            {"error": f"오늘 수동 생성 한도({theme_map.MANUAL_DAILY_LIMIT}건) 도달 — 내일 다시 시도"},
            status_code=429)
    job_id = uuid.uuid4().hex
    _theme_map_jobs[job_id] = {"status": "pending", "theme": theme_name, "result": None,
                                "error": None, "created_at": datetime.now(KST).isoformat()}
    _theme_map_generating.add(theme_name)
    asyncio.create_task(_theme_map_generate_job(job_id, theme_name))
    return JSONResponse(
        {"job_id": job_id, "status": "pending", "theme": theme_name,
         "poll": f"/api/theme_map/jobs/{job_id}"},
        status_code=202)


@app.get("/api/theme_map/jobs/{job_id}")
async def theme_map_job_status(job_id: str):
    job = _theme_map_jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": f"job '{job_id}' 없음(만료 또는 오타 — 재배포로 초기화됐을 수도 있음)"},
                             status_code=404)
    return JSONResponse(_clean_nan({"job_id": job_id, **job}))


@app.get("/api/theme_lifecycle/{theme_name}")
async def theme_lifecycle_get(theme_name: str):
    """테마 라이프사이클 분석 (v5.121, 사용자 지시) — theme_map.json 종목
    리스트 기반 최근 60거래일 재구성(거래대금 점유율·breadth·서열별 수익률·
    확산 lag·집중도) + 명시적 임계값 4단계(점화/확산/후기/이탈) 판정,
    판정 근거 수치 동반(theme_lifecycle.py 참고). money_flow.py의 top100
    한정 sector_of 기반 테마 집계와는 별개 계산(서로 다른 테마 개념)."""
    entry = theme_map.get(theme_name)
    if entry is None or not entry.get("stocks"):
        return JSONResponse(
            {"error": f"'{theme_name}' 매핑 없음 — POST /api/theme_map/{theme_name}으로 생성"},
            status_code=404)
    bundle = await _fetch_market_data("kr")
    if not bundle:
        return JSONResponse({"error": "KR 시장 데이터 로드 중 — 잠시 후 재시도"}, status_code=503)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        _executor, theme_lifecycle.analyze_theme, theme_name, entry["stocks"], bundle["data"])
    if result is None:
        return JSONResponse({"error": "구성종목 데이터 부족(2개 미만 확보) — 표본부족",
                              "theme": theme_name}, status_code=200)
    result.pop("_theme_data", None)
    return JSONResponse(_clean_nan(result))


@app.get("/api/theme_lifecycle_rotation")
async def theme_lifecycle_rotation():
    """전체 theme_map.json 테마 간 자금 이동 매트릭스 — 최근 20거래일
    일별 거래대금 점유율 변화(delta) 상관계수. "테마A 하락일=테마B 상승일"
    같은 로테이션 서사를 숫자로(theme_lifecycle.rotation_matrix())."""
    names = list(theme_map.list_all().keys())
    if not names:
        return JSONResponse({"error": "저장된 테마 매핑이 없습니다"}, status_code=404)
    bundle = await _fetch_market_data("kr")
    if not bundle:
        return JSONResponse({"error": "KR 시장 데이터 로드 중 — 잠시 후 재시도"}, status_code=503)
    loop = asyncio.get_event_loop()

    def _compute():
        market_turnover = theme_lifecycle.market_daily_turnover(bundle["data"])
        series_map = {}
        for name in names:
            e = theme_map.get(name)
            if not e or not e.get("stocks"):
                continue
            td = theme_lifecycle.compute_theme_series(e["stocks"], bundle["data"], market_turnover)
            if td is not None:
                series_map[name] = td
        return theme_lifecycle.rotation_matrix(series_map)

    matrix = await loop.run_in_executor(_executor, _compute)
    return JSONResponse(_clean_nan({"themes": list(matrix.keys()), "matrix": matrix}))


def _reignition_watchlist_view() -> list:
    """대장관찰 탭 '🔁 재점화 대기' 섹션 + 봇 조회 공용 뷰. status=watching/
    confirmed만 노출(expired는 UI에서 볼 필요 없는 소멸 이력). confirmed를
    앞으로, 그다음 window_days_left 오름차순(창 만료 임박 = 급함)."""
    store = _load_reignition_watch()
    items = [{"theme": r["theme"], "ticker": r["ticker"], "name": r["name"],
              "d0_date": r["d0_date"], "days_since_d0": r.get("days_since_d0"),
              "window_days_left": r.get("window_days_left"), "status": r["status"],
              "alert_suppressed": r.get("alert_suppressed", False),
              "compression": r.get("compression"), "confirm": r.get("confirm"),
              "forward": r.get("forward")}
             for r in store.values() if r.get("status") in ("watching", "confirmed")]
    items.sort(key=lambda x: (x["status"] != "confirmed", x.get("window_days_left") if x.get("window_days_left") is not None else 999))
    return items


@app.get("/api/reignition/watchlist")
async def reignition_watchlist():
    """🔁 전 리더 재점화 대기 목록 (v5.125) — docs/kr_theme_leader_reignition.md
    채택 결과의 실시간 워치리스트. _refresh_reignition_watch()가 KR 장마감
    후 하루 1회 갱신한 저장분을 그대로 노출(이 엔드포인트 자체는 새 계산을
    하지 않음 — 대장관찰 탭 로드를 무겁게 만들지 않기 위함, 캘린더 탭과
    같은 원칙)."""
    items = _reignition_watchlist_view()
    return JSONResponse(_clean_nan({
        "items": items, "count": len(items),
        "n_suppressed": sum(1 for i in items if i["alert_suppressed"]),
        "window_start_days": theme_reignition.WATCH_WINDOW_START,
        "window_end_days": theme_reignition.WATCH_WINDOW_END,
    }))


@app.get("/api/reignition/confirmed")
async def reignition_confirmed():
    """🔁 재점화 확인진입 오늘자 신규분 — 얼마냐봇(외부 텔레그램 봇,
    /api/jongga/candidates와 동일 방식)이 폴링해서 자체적으로 메시지를
    만들어 보내는 용도. 이 레포엔 텔레그램 발송 코드가 없다(얼마냐봇은
    별도 레포) — 데이터만 제공. 문구 형식 제안:
    '🔁 전 리더 재점화 후보: {name} ({theme} D0 리더, D+{days_since_d0})'
    오늘 신규 확인분이 없으면 confirmed=[]로 응답(발송 여부는 봇 쪽 로직)."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    store = _load_reignition_watch()
    confirmed = [
        {"theme": r["theme"], "ticker": r["ticker"], "name": r["name"],
         "d0_date": r["d0_date"], "days_since_d0": r.get("days_since_d0"),
         "pivot": r["confirm"]["pivot"], "stop": r["confirm"]["stop"]}
        for r in store.values()
        if r.get("status") == "confirmed" and (r.get("confirm") or {}).get("date") == today
    ]
    return JSONResponse(_clean_nan({
        "ok": True, "date": today, "confirmed": confirmed, "count": len(confirmed),
        "trading_day": is_trading_day("kr", today),
        "message_format_hint": "🔁 전 리더 재점화 후보: {name} ({theme} D0 리더, D+{days_since_d0})",
    }))


@app.get("/api/reignition/forward")
async def reignition_forward():
    """🔁 재점화 포워드 트래킹 누적 통계(v5.125) — 백테스트(+0.755R, n=53,
    docs/kr_theme_leader_reignition.md)와 실전 결과를 계속 대조."""
    return JSONResponse(_clean_nan(_reignition_forward_stats()))


@app.get("/api/vol/{ticker}")
async def vol_reference(ticker: str):
    """종목의 평균 거래량 참조값 (v4.50.1) — 봇의 돌파 거래량 확증용.
    캐시된 일봉에서 50일 평균 거래량을 반환. 네이버 실시간 누적 거래량과
    나눠서 봇이 '예상 거래량비'를 계산한다. v5.117에서 이 함수의 부분봉
    오염(아래 참조)만 고쳤고, 시간비례 외삽 자체의 장초반 과대추정 편향은
    stock-alert/main.py의 VOLUME_PROJECTION_BIAS/_bias_correction_factor()에서
    KR 한정으로 보정한다(근거: docs/volume_confirm_bias_investigation.md).
    이 함수는 그 보정과 무관 — 완결된 일봉 평균만 정확히 돌려주면 된다."""
    ticker = ticker.upper().strip()
    # 캐시된 전 시장 데이터에서 탐색 (없으면 개별 fetch)
    df = None
    for key in ("data:all", "data:kr", "data:us"):   # v5.110[버그수정]: 실제 캐시 키는 소문자(_fetch_market_data의 f"data:{market}")
        bundle = _data_cache.get(key)
        if bundle and ticker in bundle.get("data", {}):
            df = bundle["data"][ticker]
            break
    if df is None:
        try:
            df = await asyncio.get_event_loop().run_in_executor(_executor, _fetch, ticker)
        except Exception:
            df = None
    if df is None or "Volume" not in df or len(df) < 5:
        return JSONResponse({"ok": False}, status_code=404)
    try:
        vol = df["Volume"]
        # v5.117[버그수정]: 장중 호출 시 df의 마지막 행이 '오늘'의 진행 중인
        # 부분봉일 수 있다(KR: naver_kr.fetch가 오늘 거래일을 실시간으로 채워
        # 반환함 — fetch() docstring 참조. US: yfinance 일봉도 장중엔 당일
        # 거래량이 계속 갱신됨). 이 부분봉이 50/20일 평균에 섞이면 완결된
        # 거래일보다 표본 하나가 적게 반영돼 평균이 낮아지고, 그 결과 봇의
        # '예상 거래량비(%)'가 한 번 더 부풀려진다(시간비례 외삽 편향과는
        # 별개의 추가 왜곡). 평균은 항상 완결된 일봉만으로 계산한다.
        is_kr = naver_kr.is_kr(ticker)
        if len(vol) and vol.index[-1].date() == datetime.now(KST).date() and _is_market_open_now(is_kr):
            vol = vol.iloc[:-1]
        avg50 = float(vol.iloc[-50:].mean())
        avg20 = float(vol.iloc[-20:].mean())
        return JSONResponse({"ok": True, "ticker": ticker,
                             "avg_volume_50": round(avg50),
                             "avg_volume_20": round(avg20)})
    except Exception:
        return JSONResponse({"ok": False}, status_code=500)


@app.get("/api/dist/{ticker}")
async def distribution_signal(ticker: str):
    """보유 종목 분산(매도) 신호 (v4.51) — 봇이 진입 종목마다 하루 1회 체크.
    캐시된 일봉으로 distribution_check 실행."""
    ticker = ticker.upper().strip()
    df = None
    for key in ("data:all", "data:kr", "data:us"):   # v5.110[버그수정]: 실제 캐시 키는 소문자(_fetch_market_data의 f"data:{market}")
        bundle = _data_cache.get(key)
        if bundle and ticker in bundle.get("data", {}):
            df = bundle["data"][ticker]
            break
    if df is None:
        try:
            df = await asyncio.get_event_loop().run_in_executor(_executor, _fetch, ticker)
        except Exception:
            df = None
    if df is None or len(df) < 55:
        return JSONResponse({"ok": False}, status_code=404)
    try:
        r = scanner_mod.distribution_check(df["Close"], df["High"], df["Low"], df["Volume"])
        return JSONResponse({"ok": True, "ticker": ticker, **r})
    except Exception:
        return JSONResponse({"ok": False}, status_code=500)


@app.get("/api/pullback-signal/{ticker}")
async def pullback_signal(ticker: str):
    """눌림 지지 진입 신호 (v5.04) — 얼마냐봇의 "눌림 지지 진입" 알림 전용.
    RS 백분위(지수 대비 초과성과, 사이트 전역 rs_ranks 재사용) · U/D Volume
    Ratio(매집/분산) · 주봉 10EMA 거리 · 21일→14일 ATR%(atr() 기본 period
    재사용, 기존 badge_fields와 동일 산식) · 월봉 50% 되돌림(참고용 confluence)
    을 한 번에 반환. 봇이 이 값들로 직접 게이트 판정(RS>=90, U/D>=1.5,
    주봉10EMA ±2%)한다 — 게이트 임계값 자체는 봇 쪽 로직(운영 중 튜닝 용이).

    v5.57 조사(docs/ud_volume_ratio_investigation.md): ud_ratio>=1.5 하드
    게이트는 실측상 방향이 역방향이거나(눌림목 U/D>=1.0군 EV 0.139 vs
    <1.0군 0.330) 신뢰 불가로 확인됨 — 강한피벗 탭의 같은 종류 게이트
    (STRONG_PIVOT_MIN_UD)는 이미 제거했음. stock-alert v2.15에서 해결됨
    (UD 하드게이트 제거, 현재 참고용) — ud_ratio 필드 값 자체는 참고용
    으로 계속 그대로 제공."""
    ticker = ticker.upper().strip()
    df = None
    rs_rank = None
    for key in ("data:all", "data:kr", "data:us"):   # v5.110[버그수정]: 실제 캐시 키는 소문자(_fetch_market_data의 f"data:{market}")
        bundle = _data_cache.get(key)
        if bundle and ticker in bundle.get("data", {}):
            df = bundle["data"][ticker]
            rs_rank = bundle.get("rs_ranks", {}).get(ticker)
            break
    if df is None:
        try:
            df = await asyncio.get_event_loop().run_in_executor(_executor, _fetch, ticker)
        except Exception:
            df = None
    if df is None or len(df) < 70:
        return JSONResponse({"ok": False}, status_code=404)
    try:
        c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
        close = float(c.iloc[-1])
        ema10w = scanner_mod.weekly_ema10(c)
        ud = scanner_mod.up_down_volume(c, v, 50)
        atr_val = scanner_mod.atr(h, lo, c, period=14)
        atr_pct = round(atr_val / close * 100, 2) if close > 0 else None
        retrace50 = scanner_mod.monthly_retrace_50(c)
        ema10w_dist_pct = round((close - ema10w) / ema10w * 100, 2) if ema10w else None
        confluence = bool(
            ema10w is not None and retrace50 is not None and close > 0
            and abs(ema10w - retrace50) / close <= 0.02
        )
        return JSONResponse({
            "ok": True, "ticker": ticker, "close": round(close, 2),
            "rs_rank": rs_rank,
            "ud_ratio": ud,
            "weekly_ema10": round(ema10w, 2) if ema10w is not None else None,
            "weekly_ema10_dist_pct": ema10w_dist_pct,
            "atr_pct": atr_pct,
            "monthly_retrace_50": round(retrace50, 2) if retrace50 is not None else None,
            "confluence": confluence,
        })
    except Exception:
        return JSONResponse({"ok": False}, status_code=500)


def _kr_session_elapsed_ratio(now: datetime) -> float:
    """장 시작 급증 스캔용 장중 경과 비율 — 봇의 _session_elapsed_ratio와 같은
    로직을 서버 쪽에 복제. 유니버스 전체를 한 번에 훑어야 해서(종목별 API
    왕복이면 너무 느림) 이 안에서 직접 계산한다. 장외는 1.0(스캔 의미 없음)."""
    open_min, close_min = 9 * 60, 15 * 60 + 30
    cur_min = now.hour * 60 + now.minute
    if cur_min < open_min or cur_min >= close_min:
        return 1.0
    return max(0.01, (cur_min - open_min) / (close_min - open_min))


_OPENING_SURGE_MIN_RATIO = 3.0     # 시간보정 평소 거래량 대비 이 배수 이상만 "급증"
_OPENING_SURGE_MIN_VALUE_EOK = 5   # 최소 거래대금(억원) — 초소형/저유동 잡음 제외


@app.get("/api/opening-surge")
async def opening_surge():
    """장 시작 직후 돈 유입(거래량 급증) 스캔 (v5.00) — 얼마냐봇이 장 시작
    10분 뒤(09:10 KST) 1회 호출. 유니버스 전 종목의 오늘 누적 거래량(네이버
    근실시간 '오늘' 봉)을, 시간보정한 평소(50일 평균) 예상 거래량과 비교해
    급증(기본 3배 이상 + 최소 거래대금 5억) 종목만 반환. 종목별 API 왕복 없이
    이미 캐시된 유니버스 일봉을 한 번에 훑는다 — get_stock_data를 수백~수천
    종목에 개별 호출하면 09:00~09:10 사이에 못 끝남.
    wait_for_fresh=True로 강제 — 이 시각에 스테일 캐시(장 열리기 전 데이터)를
    돌려주면 거래량이 사실상 0으로 잡혀 무의미하다."""
    now = datetime.now(KST)
    ratio_elapsed = _kr_session_elapsed_ratio(now)
    bundle = await _fetch_market_data("kr", wait_for_fresh=True)
    data = bundle.get("data", {})
    universe = bundle.get("universe", {})
    out = []
    for t, df in data.items():
        if df is None or len(df) < 55 or "Volume" not in df:
            continue
        try:
            vol = df["Volume"]
            vol_today = float(vol.iloc[-1])
            avg50 = float(vol.iloc[-51:-1].mean())   # 오늘 제외 과거 50일
            if avg50 <= 0:
                continue
            expected_by_now = avg50 * ratio_elapsed
            if expected_by_now <= 0:
                continue
            surge_ratio = vol_today / expected_by_now
            if surge_ratio < _OPENING_SURGE_MIN_RATIO:
                continue
            close = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else close
            chg = (close / prev - 1) * 100 if prev > 0 else 0.0
            value_eok = close * vol_today / 1e8
            if value_eok < _OPENING_SURGE_MIN_VALUE_EOK:
                continue
            out.append({
                "ticker": t, "name": universe.get(t, t),
                "close": round(close, 2), "change_pct": round(chg, 2),
                "surge_ratio": round(surge_ratio, 1),
                "value_eok": round(value_eok, 1),
            })
        except Exception:
            continue
    out.sort(key=lambda r: -r["surge_ratio"])
    return JSONResponse({
        "asof": now.strftime("%Y-%m-%d %H:%M"),
        "session_elapsed_pct": round(ratio_elapsed * 100, 1),
        "hits": out[:15],
    })


@app.get("/api/watch/positions")
async def watch_positions():
    """진입중(entered, 미종료) 포지션 노출 — 봇의 R 마일스톤/손절 알림용 (v4.49.3).
    반환: [{id, ticker, name, entry, stop, shares}, ...]
    봇이 2분마다 현재가로 R 진행률을 계산해 +2R(절반 익절)·손절 도달을 알림."""
    out = []
    for r in load_journal():
        # ── 진입 상태 판정 (v4.53.5) ──
        # 기존 (status or "entered")는 status 없는 관찰 종목을 진입으로 오인해
        # R 알림을 잘못 보냄(RCUS 사례: 관찰인데 진입가 넣어서 +2R 오알림).
        # 명시적으로 'entered'인 것만 R 감시. watch/pending/missed/closed 제외.
        # category가 '관찰'이면 status와 무관하게 제외 (이중 안전장치).
        status = r.get("status") or ""
        cat = r.get("category") or r.get("cat") or ""
        # 관찰은 status 무관하게 항상 제외 (진입가를 적어놔도 R 감시 안 함)
        if cat == "관찰":
            continue
        # status가 있으면 그걸로 판정 (entered만). status 없는 구 레코드는
        # 관찰이 아니고 진입가·손절이 있으면 진입으로 간주(하위호환).
        if status:
            if status != "entered":
                continue
        # status 없는 구 레코드: 아래 entry/stop 검증으로 걸러짐
        if r.get("result_r") not in ("", None):
            continue
        try:
            e = float(r.get("entry") or 0)
            s = float(r.get("stop") or 0)
        except (TypeError, ValueError):
            continue
        if not e or not s or s >= e:
            continue
        out.append({
            "id": r.get("id"),
            "ticker": r.get("ticker"),
            "name": r.get("name"),
            "entry": e,
            "stop": s,
            "shares": r.get("shares") or "",
        })
    return JSONResponse({"positions": out, "count": len(out)})


# ── R 기반 리스크 시스템 설정 (v4.47) ──────────────────────
# 총자산·R%·서킷브레이커·시장게이트를 저널과 같은 영구 볼륨에 저장.
# 프론트가 포지션 사이즈 계산과 진입 잠금에 사용.
RSETTINGS_PATH = os.path.join(os.path.dirname(JOURNAL_PATH), "r_settings.json")
RSETTINGS_DEFAULT = {
    "equity": 0,           # 총자산 (KRW)
    "r_pct": 0.5,          # 1R = 총자산의 %  (검증 전 0.5%, 검증 후 0.75~1%)
    "max_open_r": 3.0,     # 동시 오픈 리스크 상한 (R)
    "weekly_stop_r": 3.0,  # 주간 -nR 도달 시 신규 진입 중단
    "monthly_stop_r": 6.0, # 월간 -nR 도달 시 그 달 종료
    "gate": "confirmed",   # 시장 게이트: confirmed | pressure | correction
    "dist_days": 0,        # 분산일 카운트 (수동 입력, 게이트 판단 참고용)
    "usd_krw": 1400.0,     # 환율 (US 종목 사이즈 계산용, 수동 갱신)
}


@app.get("/guide.md")
async def guide_md():
    """활용 가이드 원문 (v5.47) — /guide 페이지가 fetch해서 렌더."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "GUIDE.md")
    if not os.path.exists(path):
        return Response("가이드 파일이 없습니다.", media_type="text/plain")
    return FileResponse(path, media_type="text/markdown; charset=utf-8", headers=_NO_CACHE_HEADERS)


@app.get("/guide")
async def guide_page():
    """활용 가이드 뷰어 — 스캐너 다크 테마, 클라이언트 렌더(marked.js)."""
    html = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>📖 돌파·눌림 스캐너 활용 가이드</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
:root{--bg:#0d1117;--surface:#161b22;--line:#30363d;--fg:#e6edf3;--muted:#8b949e;--green:#3fb950}
body{background:var(--bg);color:var(--fg);font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;
  margin:0;padding:24px 16px;line-height:1.75}
#doc{max-width:820px;margin:0 auto}
h1{font-size:24px;border-bottom:2px solid var(--line);padding-bottom:10px}
h2{font-size:19px;margin-top:36px;border-bottom:1px solid var(--line);padding-bottom:6px;color:var(--green)}
h3{font-size:15.5px;margin-top:24px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left}
th{background:var(--surface)}
code{background:var(--surface);padding:1px 5px;border-radius:4px;font-size:13px}
blockquote{border-left:3px solid var(--green);margin:0;padding:2px 14px;color:var(--muted)}
hr{border:none;border-top:1px solid var(--line);margin:28px 0}
strong{color:#ffd98a}
a.back{position:fixed;top:14px;right:14px;background:var(--surface);border:1px solid var(--line);
  color:var(--fg);text-decoration:none;padding:6px 12px;border-radius:8px;font-size:13px}
</style></head><body>
<a class="back" href="/">← 스캐너</a>
<div id="doc">불러오는 중…</div>
<script>
fetch('/guide.md').then(r => r.text()).then(md => {
  document.getElementById('doc').innerHTML = marked.parse(md);
}).catch(() => { document.getElementById('doc').textContent = '가이드를 불러오지 못했습니다.'; });
</script></body></html>"""
    return Response(html, media_type="text/html; charset=utf-8", headers=_NO_CACHE_HEADERS)


# ── 돈의 흐름 데일리 리포트 API (v5.85) ──
def _moneyflow_market_or_400(market: str) -> JSONResponse | None:
    if market not in ("kr", "us"):
        return JSONResponse({"error": "market은 kr 또는 us"}, status_code=400)
    return None


@app.get("/api/moneyflow/{market}")
async def get_money_flow(market: str, date: str | None = None):
    bad = _moneyflow_market_or_400(market)
    if bad:
        return bad
    available = money_flow.list_available_dates(market)
    daykey = date or (available[0] if available else None)
    if daykey is None:
        return JSONResponse({"market": market, "date": None, "available_dates": [],
                              "snapshot": None, "markdown": None,
                              "error": "아직 생성된 리포트가 없습니다. 재실행을 눌러보세요."})
    snapshot = money_flow.load_snapshot(market, daykey)
    markdown = money_flow.load_report_markdown(market, daykey)
    error = None if (snapshot is not None) else "해당 날짜의 데이터가 없습니다"
    if snapshot is not None and markdown is None:
        error = "AI 해석 리포트가 없습니다(생성 실패 또는 미실행) — 1단계 계산 결과만 표시합니다"
    return JSONResponse(_clean_nan({"market": market, "date": daykey, "available_dates": available,
                                     "snapshot": snapshot, "markdown": markdown, "error": error,
                                     "trading_day": is_trading_day(market, daykey)}))   # v5.99


@app.get("/api/moneyflow/{market}/summary")
async def get_money_flow_summary(market: str):
    """얼마냐봇 폴링 전용 요약 엔드포인트 (v5.87) — 최신 리포트의 날짜·
    강한/약한 테마 3개·최종 한 문장만 JSON으로 반환. money_flow_report의
    섹션 12 JSON 블록(docs/money_flow_prompt.md)을 파싱 — 마크다운 본문을
    정규식으로 긁지 않고 모델이 직접 낸 구조화 데이터를 그대로 씀."""
    bad = _moneyflow_market_or_400(market)
    if bad:
        return bad
    available = money_flow.list_available_dates(market)
    if not available:
        return JSONResponse({"market": market, "date": None, "error": "아직 생성된 리포트가 없습니다"})
    daykey = available[0]
    markdown = money_flow.load_report_markdown(market, daykey)
    if not markdown:
        return JSONResponse({"market": market, "date": daykey,
                              "error": "AI 해석 리포트가 없습니다(1단계 계산만 존재)"})
    summary = money_flow_report.extract_summary(markdown)
    if summary is None:
        return JSONResponse({"market": market, "date": daykey,
                              "error": "요약 JSON 파싱 실패(리포트가 중간에 잘렸거나 형식이 다름)"})
    return JSONResponse({
        "market": market, "date": daykey,
        "strong_themes": summary.get("strong_themes"),
        "weak_themes": summary.get("weak_themes"),
        "final_sentence": summary.get("final_sentence"),
        "url": "https://pullback2-production.up.railway.app/moneyflow",
        "error": None,
        "trading_day": is_trading_day(market, daykey),   # v5.99: 봇 이중 확인용
    })


_moneyflow_manual_running: dict = {}    # {"kr": True} — 시장별 진행 중 플래그
_moneyflow_manual_last_run: dict = {}   # {"kr": epoch_ts} — 마지막 수동 실행 완료 시각
_MONEYFLOW_MANUAL_COOLDOWN_SEC = 120    # v5.125(사용자 지시, API 비용 급증 조사)


@app.post("/api/moneyflow/{market}/run")
async def run_money_flow_now(market: str):
    """수동 재실행(🔄 버튼) — v5.125부터 쿨다운+진행중 잠금 추가. 원래
    아무 제한이 없어서 클릭 한 번마다 money_flow_report.generate_report()
    (Claude+웹서치 최대 5회, ~16000 max_tokens) 풀프라이스 호출이 그대로
    나갔다 — API 비용 급증(6일간 $48, 예상의 20배) 조사에서 확인된 유력한
    원인 중 하나(money_flow.py가 2026-08-26에 신설돼 비용 급증 시작일과
    겹침 — 그날 같은 세션에서 버그 수정 3건이 나온 걸 보면 반복 재실행
    테스트가 있었을 가능성이 높음). macro_calendar의 기존 가드(진행중
    플래그+재시도 스로틀)와 같은 패턴."""
    bad = _moneyflow_market_or_400(market)
    if bad:
        return bad
    if _moneyflow_manual_running.get(market):
        return JSONResponse({"error": "이미 진행 중 — 잠시 후 다시 시도"}, status_code=429)
    remaining = _MONEYFLOW_MANUAL_COOLDOWN_SEC - (time.time() - _moneyflow_manual_last_run.get(market, 0))
    if remaining > 0:
        return JSONResponse({"error": f"너무 잦은 재실행 — {int(remaining)}초 후 다시 시도 (비용 보호)"},
                             status_code=429)
    _moneyflow_manual_running[market] = True
    try:
        daykey = datetime.now(KST).strftime("%Y-%m-%d")
        try:
            result = await _run_money_flow(market, daykey)
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
        available = money_flow.list_available_dates(market)
        return JSONResponse(_clean_nan({"market": market, "date": daykey, "available_dates": available, **result}))
    finally:
        _moneyflow_manual_last_run[market] = time.time()
        _moneyflow_manual_running[market] = False


# ══════════════════ v5.108: 캘린더 탭 ══════════════════
# money_flow와 같은 원칙(생성은 별도 모듈, 캐시/트리거는 app.py)이되, 하루
# 지난다고 크게 안 바뀌는 데이터라 스케줄러 4분 주기가 아니라 주 1회만
# 재생성. 실패해도 이전에 성공한 events는 그대로 유지(캐시 파일에 성공한
# 마지막 events를 남겨두고, 시도 시각/에러만 별도 필드로 갱신).
MACRO_CALENDAR_PATH = _resolve_persistent_path("macro_calendar.json")
_MACRO_CALENDAR_STALE_DAYS = 7        # 이보다 오래되면 재생성 시도
_MACRO_CALENDAR_RETRY_HOURS = 24      # 실패했으면 이 시간 안엔 재시도 안 함(장애 시 폭주 방지)


def _load_macro_calendar() -> dict:
    if not os.path.exists(MACRO_CALENDAR_PATH):
        return {"events": [], "generated_at": None, "last_attempt_at": None, "last_error": None}
    try:
        with open(MACRO_CALENDAR_PATH, "r", encoding="utf-8") as f:
            data = _json.load(f)
            return data if isinstance(data, dict) else {"events": [], "generated_at": None,
                                                          "last_attempt_at": None, "last_error": None}
    except (OSError, ValueError):
        return {"events": [], "generated_at": None, "last_attempt_at": None, "last_error": None}


def _macro_calendar_stale(cache: dict) -> bool:
    gen = cache.get("generated_at")
    if not gen:
        return True
    try:
        age_days = (datetime.now(KST) - datetime.fromisoformat(gen)).total_seconds() / 86400
    except (ValueError, TypeError):
        return True
    return age_days >= _MACRO_CALENDAR_STALE_DAYS


def _macro_calendar_attempt_throttled(cache: dict) -> bool:
    attempt = cache.get("last_attempt_at")
    if not attempt:
        return False
    try:
        age_hours = (datetime.now(KST) - datetime.fromisoformat(attempt)).total_seconds() / 3600
    except (ValueError, TypeError):
        return False
    return age_hours < _MACRO_CALENDAR_RETRY_HOURS


_macro_calendar_task_running = False


async def _refresh_macro_calendar_bg():
    """실패해도 예외를 밖으로 던지지 않음(스케줄러 루프가 통째로 죽으면 안 됨) —
    money_flow_report 실패 처리와 같은 원칙."""
    global _macro_calendar_task_running
    if _macro_calendar_task_running:
        return
    _macro_calendar_task_running = True
    try:
        cache = _load_macro_calendar()
        today = datetime.now(KST).strftime("%Y-%m-%d")
        loop = asyncio.get_event_loop()
        try:
            events, error = await loop.run_in_executor(_executor, macro_calendar.generate_calendar, today)
        except Exception as e:
            events, error = None, f"{type(e).__name__}: {e}"
        now_iso = datetime.now(KST).isoformat()
        cache["last_attempt_at"] = now_iso
        if events is not None:
            cache["events"] = events
            cache["generated_at"] = now_iso
            cache["last_error"] = None
        else:
            cache["last_error"] = error
            print(f"[calendar] macro 갱신 실패(이전 캐시 유지): {error}")
        try:
            _save_json_atomic(MACRO_CALENDAR_PATH, cache)
        except OSError as e:
            print(f"[calendar] macro 캐시 저장 실패: {e}")
    finally:
        _macro_calendar_task_running = False


def _maybe_refresh_macro_calendar():
    cache = _load_macro_calendar()
    if _macro_calendar_stale(cache) and not _macro_calendar_attempt_throttled(cache):
        asyncio.create_task(_refresh_macro_calendar_bg())


_next_earnings_cache: dict = {}
_NEXT_EARNINGS_TTL = 24 * 3600   # 다음 실적일 캐시 24시간 — 날짜 자체는 하루 여러 번 안 바뀜


def _next_earnings_date(ticker: str):
    """블로킹 — executor에서 실행. yfinance calendar의 Earnings Date 중 오늘
    이후 가장 이른 날짜. KR(005930.KS)·US(AAPL) 둘 다 실측 확인함."""
    try:
        cal = yf.Ticker(ticker).calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if not dates:
            return None
        today = datetime.now(KST).date()
        future = sorted(d for d in dates if d and d >= today)
        return future[0].isoformat() if future else None
    except Exception:
        return None


def _next_earnings_date_cached(ticker: str):
    now = time.time()
    c = _next_earnings_cache.get(ticker)
    if c and now - c["ts"] < _NEXT_EARNINGS_TTL:
        return c["data"]
    data = _next_earnings_date(ticker)
    _next_earnings_cache[ticker] = {"ts": now, "data": data}
    return data


def _calendar_current_price(ticker: str):
    """블로킹 아님(딕셔너리 조회만) — /api/ma 등과 동일한 패턴(app.py 다른
    곳에 이미 4곳 있음)으로 이미 캐시된 일봉에서 현재가만 뽑는다. 캐시에
    없으면 None(여기서 새로 fetch는 안 함 — 캘린더 탭 로드를 무겁게 만들지
    않기 위해, 사용자 지시)."""
    for key in ("data:all", "data:kr", "data:us"):   # v5.110[버그수정]: 실제 캐시 키는 소문자(_fetch_market_data의 f"data:{market}")
        bundle = _data_cache.get(key)
        if bundle and ticker in bundle.get("data", {}):
            df = bundle["data"][ticker]
            if df is not None and len(df):
                try:
                    return float(df["Close"].iloc[-1])
                except (KeyError, IndexError, ValueError):
                    return None
    return None


@app.get("/api/calendar")
async def get_calendar():
    """캘린더 탭 — 로그인 후 기본 화면(v5.108). v5.110(사용자 지시)에서
    5개 섹션 확장: ① 보유종목 실적 D-3 경고 ② 포지션 요약 한 줄
    ③ 오늘의 액션 큐(대기 피벗 근접·종가베팅 후보·대장관찰 전환) ⑤ 강세
    테마×스캐너 교집합 ⑦ 종가베팅 포워드 성적. 전부 기존 API/캐시 재사용 —
    새 스캔·새 fetch를 이 엔드포인트가 직접 트리거하지 않는다(홈 화면이라
    로드가 무거우면 안 됨, 사용자 지시) — 캐시 미스면 그 섹션만 조용히
    비운다."""
    today_dt = datetime.now(KST)
    today = today_dt.strftime("%Y-%m-%d")

    # ── 서로 독립적인 기존 엔드포인트 3개(게이트·포지션·종가베팅후보)는
    # 병렬로 호출 — 순차 호출 대비 캘린더 전체 응답 시간을 줄인다(사용자 지시:
    # "API 호출 병렬화 또는 /api/calendar에 통합" 중 통합 + 병렬화 둘 다 적용).
    gate_task = market_gate()
    positions_task = get_positions()
    jongga_task = jongga_candidates()
    gate_resp, positions_resp, jongga_resp = await asyncio.gather(
        gate_task, positions_task, jongga_task, return_exceptions=True)

    gate = None
    if not isinstance(gate_resp, Exception):
        try:
            gate_body = _json.loads(gate_resp.body)
            if gate_body.get("ok"):
                gate = {"gate_kr": gate_body.get("gate_kr"), "gate_us": gate_body.get("gate_us"),
                        "suggest": gate_body.get("suggest"), "why": gate_body.get("why")}
        except Exception as e:
            print(f"[calendar] gate 파싱 실패: {e}")
    else:
        print(f"[calendar] gate 조회 실패: {gate_resp}")

    # ── ② 포지션 요약 한 줄 ──
    positions_summary = None
    position_tickers = set()
    if not isinstance(positions_resp, Exception):
        try:
            positions_body = _json.loads(positions_resp.body)
            plist = positions_body.get("positions") or []
            position_tickers = {p.get("ticker") for p in plist if p.get("ticker")}
            if plist:
                items = [{
                    "ticker": p.get("ticker"),
                    "r_progress": p.get("r_progress"),
                    # v5.111: 프론트 미니카드 칩("NVDA +2.16R | 손절까지 -4.9%")에
                    # 표시할 실제 % 값 — near_stop 불리언만으론 숫자를 못 그림.
                    "dist_to_stop_pct": p.get("dist_to_stop_pct"),
                    # 손절선까지 -3% 이내(또는 이미 이탈) 강조 — dist_to_stop_pct는
                    # (close-stop)/close*100라 작을수록/음수일수록 위험(사용자 지시).
                    "near_stop": p.get("dist_to_stop_pct") is not None and p["dist_to_stop_pct"] <= 3,
                } for p in plist]
                s = positions_body.get("summary") or {}
                positions_summary = {
                    "items": items,
                    "open_risk": s.get("open_risk"),
                    "missing_stop_count": s.get("positions_missing_stop", 0),
                }
        except Exception as e:
            print(f"[calendar] positions 파싱 실패: {e}")
    else:
        print(f"[calendar] positions 조회 실패: {positions_resp}")

    # ── ③b 오늘 종가베팅 후보 발생 여부(14:40 이후에만 실제로 참, jongga_candidates()
    # 자체가 시간 판정을 함 — 여기선 시간 체크 안 함) ──
    jongga_today = None
    if not isinstance(jongga_resp, Exception):
        try:
            jongga_body = _json.loads(jongga_resp.body)
            if jongga_body.get("ok") and jongga_body.get("count"):
                jongga_today = {"count": jongga_body["count"], "date": jongga_body.get("date")}
        except Exception as e:
            print(f"[calendar] jongga candidates 파싱 실패: {e}")
    else:
        print(f"[calendar] jongga candidates 조회 실패: {jongga_resp}")

    # ── 돈의흐름: 오늘 한 문장(KR/US) + KR은 테마 stage/streak도 같이 필요(⑤용) ──
    moneyflow = {}
    kr_snapshot = None
    for mkt in ("kr", "us"):
        available = money_flow.list_available_dates(mkt)
        entry = None
        if available:
            daykey = available[0]
            markdown = money_flow.load_report_markdown(mkt, daykey)
            summary = money_flow_report.extract_summary(markdown) if markdown else None
            if summary:
                entry = {"date": daykey, "final_sentence": summary.get("final_sentence")}
            if mkt == "kr":
                kr_snapshot = money_flow.load_snapshot(mkt, daykey)
        moneyflow[mkt] = entry

    # ── ① 보유종목 실적 D-3 경고 + 실적 발표(보유+즐겨찾기, 기존 로직 유지) ──
    favorite_tickers = {t for t in load_favorites() if t}
    all_earn_tickers = sorted(position_tickers | favorite_tickers)
    loop = asyncio.get_event_loop()
    earnings = []
    if all_earn_tickers:
        results = await asyncio.gather(*[
            loop.run_in_executor(_earnings_executor, _next_earnings_date_cached, tk)
            for tk in all_earn_tickers
        ], return_exceptions=True)
        for tk, d in zip(all_earn_tickers, results):
            if isinstance(d, Exception) or not d:
                continue
            d_minus = (datetime.strptime(d, "%Y-%m-%d").date() - today_dt.date()).days
            earnings.append({"ticker": tk, "date": d, "d_minus": d_minus})
    earnings.sort(key=lambda e: e["date"])
    dday_warnings = [e for e in earnings if e["ticker"] in position_tickers and e["d_minus"] <= 3]

    # ── ③a 대기(pending) 종목 중 피벗 -1% 이내 근접 — 이미 캐시된 가격만
    # 씀(새 fetch 없음), ③c 대장관찰 눌림목 전환 감지 ──
    journal = load_journal()
    near_pivot = []
    for r in journal:
        if (r.get("status") or "entered") != "pending":
            continue
        ticker = r.get("ticker")
        pivot = r.get("pivot") or r.get("entry")
        if not ticker or not pivot:
            continue
        try:
            pivot_f = float(pivot)
        except (TypeError, ValueError):
            continue
        if pivot_f <= 0:
            continue
        close = _calendar_current_price(ticker)
        if close is None:
            continue
        dist_pct = (pivot_f - close) / pivot_f * 100
        if 0 <= dist_pct <= 1:
            near_pivot.append({"ticker": ticker, "name": r.get("name") or ticker,
                                "pivot": pivot_f, "close": round(close, 2), "dist_pct": round(dist_pct, 2)})

    leader_watches = [r for r in journal if r.get("watch_kind") == "leader_conversion"
                       and (r.get("status") or "watch") == "watch" and r.get("ticker")]
    leader_converted = []
    if leader_watches:
        try:
            bundle_all = await _fetch_market_data("all")
            if bundle_all:
                conv_set = set(_leader_conversion_check([r["ticker"] for r in leader_watches], bundle_all))
                leader_converted = [{"ticker": r["ticker"], "name": r.get("name") or r["ticker"]}
                                     for r in leader_watches if r["ticker"] in conv_set]
        except Exception as e:
            print(f"[calendar] leader-conversion 체크 실패: {e}")

    # ── ⑤ 강세테마 × 스캐너 교집합 — KR 돈의흐름 확산(본격)/streak2+ 테마 소속
    # 종목 중 오늘 돌파계열 탭 히트. _cache(기존 /api/scan 캐시)를 그대로
    # 읽기만 함 — 미스면(해당 탭을 오늘 아무도 안 열었으면) 조용히 스킵,
    # 새 스캔은 절대 안 돌림(사용자 지시: 홈 로드 무겁게 하지 않기). ──
    theme_scanner_hits = []
    if kr_snapshot:
        themes_data = kr_snapshot.get("themes") or {}
        strong_themes = [name for name, info in themes_data.items()
                          if info.get("stage") == "확산(본격)" or (info.get("streak_days") or 0) >= 2]
        for theme_name in strong_themes:
            entry = theme_map.get(theme_name)
            if not entry:
                continue
            stock_names = {s["ticker"]: s.get("name") or s["ticker"] for s in (entry.get("stocks") or [])}
            if not stock_names:
                continue
            for mode, label in (("breakout", "돌파"), ("boxbreak", "박스돌파"), ("turnaround", "추세전환")):
                cached_scan = _cache.get(f"kr:{mode}")
                if not cached_scan or not cached_scan.get("hits"):
                    continue
                matched = [stock_names[h["ticker"]] for h in cached_scan["hits"] if h.get("ticker") in stock_names]
                if matched:
                    theme_scanner_hits.append({"theme": theme_name, "tab": label, "stocks": matched})

    # ── ⑦ 종가베팅 포워드 성적 — 기존 _jongga_forward_stats() 그대로 재사용 ──
    jongga_forward = None
    try:
        fwd = _jongga_forward_stats()
        jongga_forward = {
            "total_resolved": fwd.get("total_resolved", 0),
            "close_basis": fwd.get("close_basis"),
            "backtest_reference": fwd.get("backtest_reference"),
        }
    except Exception as e:
        print(f"[calendar] jongga forward 조회 실패: {e}")

    # 매크로 일정 — 캐시에서 오늘~D+14 윈도우만
    macro_cache = _load_macro_calendar()
    window_end = (today_dt + timedelta(days=14)).strftime("%Y-%m-%d")
    macro_events = sorted(
        (e for e in (macro_cache.get("events") or []) if e.get("date") and today <= e["date"] <= window_end),
        key=lambda e: e.get("date") or "",
    )

    # 휴장일 — 평일만(주말은 굳이 안 보여줌), 기존 is_trading_day 재사용
    holidays = []
    for i in range(15):
        d = today_dt + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        dkey = d.strftime("%Y-%m-%d")
        for mkt, label in (("kr", "KR"), ("us", "US")):
            if not is_trading_day(mkt, dkey):
                holidays.append({"date": dkey, "market": label, "label": "휴장"})

    # v5.111: KR·US 둘 다 오늘 휴장(주말 포함)이면 "왜 조용한지" 상단에 설명 —
    # 다음 개장일은 둘 중 하나라도 열리는 가장 가까운 날(사용자 지시).
    market_closed = None
    if not is_trading_day("kr", today) and not is_trading_day("us", today):
        next_open = None
        for i in range(1, 11):
            d = today_dt + timedelta(days=i)
            dkey = d.strftime("%Y-%m-%d")
            if is_trading_day("kr", dkey) or is_trading_day("us", dkey):
                next_open = dkey
                break
        market_closed = {"next_open": next_open}

    return JSONResponse(_clean_nan({
        "today": today,
        "dday_warnings": dday_warnings,
        "positions_summary": positions_summary,
        "action_queue": {"near_pivot": near_pivot, "jongga_today": jongga_today,
                          "leader_converted": leader_converted},
        "gate": gate,
        "moneyflow": moneyflow,
        "theme_scanner_hits": theme_scanner_hits,
        "macro_events": macro_events,
        "macro_note": "일정은 참고용이며 변경될 수 있어요.",
        "macro_generated_at": macro_cache.get("generated_at"),
        "macro_error": macro_cache.get("last_error") if not macro_cache.get("events") else None,
        "holidays": holidays,
        "earnings": earnings,
        "jongga_forward": jongga_forward,
        "market_closed": market_closed,
    }))


@app.post("/api/calendar/macro/run")
async def run_macro_calendar_now():
    """수동 재생성 트리거 — 최초 배포 직후 캐시가 비어있을 때, 또는 오래돼
    수동으로 새로고침하고 싶을 때. v5.123[버그수정]: 기존엔 Claude 생성이
    끝날 때까지 await해서 Railway 프록시 타임아웃(upstream error)에 걸릴
    수 있었음(theme_map POST와 같은 문제) — asyncio.create_task로 백그라운드
    전환, 즉시 202 반환. 매크로 캘린더는 테마 매핑과 달리 종목별이 아닌
    단일 리소스라 별도 job_id 체계 없이, 기존 _macro_calendar_task_running
    플래그 + 캐시 파일의 generated_at/last_attempt_at/last_error를 그대로
    진행상태로 재사용(새 저장소 안 만듦) — GET /api/calendar/macro/status로
    폴링. 이미 진행 중이면 _refresh_macro_calendar_bg 자체 가드가 중복 실행 방지.
    v5.125(사용자 지시, API 비용 급증 조사): 짧은 수동 재실행 쿨다운 추가 —
    기존 _macro_calendar_attempt_throttled()의 24시간 기준은 "실패 후
    자동재시도 폭주 방지"용이라 정상 완료 직후의 연타 클릭은 못 막았음
    (프롬프트 튜닝 중 반복 클릭 시나리오, macro_calendar.py가 이번 비용
    급증 구간(2026-08-30)에 실제로 프롬프트 확장 작업이 있었음). 별도의
    짧은(2분) 쿨다운을 여기서만 체크 — 자동 스케줄러의 24시간 로직은 그대로."""
    if _macro_calendar_task_running:
        return JSONResponse({"error": "이미 진행 중 — 잠시 후 다시 시도"}, status_code=429)
    last_attempt = (_load_macro_calendar() or {}).get("last_attempt_at")
    if last_attempt:
        try:
            age_sec = (datetime.now(KST) - datetime.fromisoformat(last_attempt)).total_seconds()
        except (ValueError, TypeError):
            age_sec = 999
        if age_sec < 120:
            return JSONResponse({"error": f"너무 잦은 재실행 — {int(120 - age_sec)}초 후 다시 시도 (비용 보호)"},
                                 status_code=429)
    asyncio.create_task(_refresh_macro_calendar_bg())
    return JSONResponse(_clean_nan({"status": "started", **_load_macro_calendar(),
                                     "poll": "/api/calendar/macro/status"}), status_code=202)


@app.get("/api/calendar/macro/status")
async def macro_calendar_status():
    """POST /api/calendar/macro/run이 던진 백그라운드 생성의 진행상태 —
    새 저장소 없이 기존 캐시 파일 + 실행중 플래그 재사용(위 설명 참고)."""
    return JSONResponse(_clean_nan({"generating": _macro_calendar_task_running, **_load_macro_calendar()}))


@app.get("/moneyflow")
async def money_flow_page():
    """돈의 흐름 데일리 리포트 뷰어 — KR/US 탭, 날짜 선택, 수동 재실행.
    AI 해석(markdown) 있으면 marked.js로 렌더, 없으면(API 실패 등)
    1단계 JSON을 표로 폴백 렌더(사용자 지시: 실패해도 계산 결과는 표시)."""
    html = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>💰 돈의 흐름 데일리 리포트</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
:root{--bg:#0d1117;--surface:#161b22;--line:#30363d;--fg:#e6edf3;--muted:#8b949e;--green:#3fb950;--amber:#f2b33d}
body{background:var(--bg);color:var(--fg);font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;
  margin:0;padding:24px 16px 60px;line-height:1.75}
#wrap{max-width:860px;margin:0 auto}
h1{font-size:22px;margin-bottom:4px}
h2{font-size:19px;margin-top:36px;border-bottom:1px solid var(--line);padding-bottom:6px;color:var(--green)}
h3{font-size:15.5px;margin-top:24px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left}
th{background:var(--surface)}
code{background:var(--surface);padding:1px 5px;border-radius:4px;font-size:13px}
blockquote{border-left:3px solid var(--green);margin:0;padding:2px 14px;color:var(--muted)}
hr{border:none;border-top:1px solid var(--line);margin:28px 0}
strong{color:#ffd98a}
a.back{position:fixed;top:14px;right:14px;background:var(--surface);border:1px solid var(--line);
  color:var(--fg);text-decoration:none;padding:6px 12px;border-radius:8px;font-size:13px}
.warn-banner{background:rgba(242,179,61,.1);border:1px solid rgba(242,179,61,.35);color:var(--amber);
  padding:10px 14px;border-radius:8px;font-size:13.5px;margin:14px 0}
.mf-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:14px 0}
.mf-tab{background:var(--surface);border:1px solid var(--line);color:var(--fg);padding:6px 14px;
  border-radius:8px;font-size:13.5px;cursor:pointer}
.mf-tab.active{border-color:var(--green);color:var(--green)}
select,button{background:var(--surface);border:1px solid var(--line);color:var(--fg);padding:6px 10px;
  border-radius:8px;font-size:13px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
.mf-note{color:var(--muted);font-size:12.5px;margin:6px 0}
.mf-err{color:#ff9b9b;font-size:13px;margin:10px 0}
</style></head><body>
<a class="back" href="/">← 스캐너</a>
<div id="wrap">
<h1>💰 돈의 흐름 데일리 리포트</h1>
<div class="warn-banner">⚠️ 관찰용 정보입니다 — 진입 신호가 아닙니다. 자금 흐름 파악용 참고 자료로만 활용하세요.</div>
<div class="mf-toolbar">
  <button class="mf-tab active" id="tabKr" onclick="setMarket('kr')">🇰🇷 한국</button>
  <button class="mf-tab" id="tabUs" onclick="setMarket('us')">🇺🇸 미국</button>
  <select id="dateSel" onchange="loadDate(this.value)"></select>
  <button id="runBtn" onclick="runNow()">🔄 재실행</button>
</div>
<div id="doc">불러오는 중…</div>
</div>
<script>
let market = 'kr';
function fmtTop(snapshot) {
  if (!snapshot || !snapshot.top) return '';
  const rows = snapshot.top.slice(0, 30).map(r =>
    `<tr><td>${r.rank}</td><td>${r.name || r.ticker}</td><td>${r.change_pct}%</td><td>${r.theme}</td>` +
    `<td>${(r.turnover/1e8).toFixed(1)}억</td><td>${r.rank_change != null ? (r.rank_change>0?'+':'')+r.rank_change : (r.is_new_entrant?'신규':'-')}</td></tr>`
  ).join('');
  const themeRows = Object.entries(snapshot.themes || {}).sort((a,b) => b[1].turnover_share_pct - a[1].turnover_share_pct).map(([name, t]) =>
    `<tr><td>${name}</td><td>${t.n}</td><td>${t.breadth_pct}%</td><td>${t.avg_change_pct}%</td>` +
    `<td>${t.turnover_share_pct}%${t.turnover_share_change_pct != null ? ` (${t.turnover_share_change_pct>0?'+':''}${t.turnover_share_change_pct}%p)` : ''}</td>` +
    `<td>${t.streak_days}일</td><td>${t.stage}</td></tr>`
  ).join('');
  return `<p class="mf-note">AI 해석 리포트가 없어 1단계 계산 결과(테마 집계 + 거래대금 상위 30)만 표로 표시합니다.</p>
  <h3>테마 집계</h3>
  <table><tr><th>테마</th><th>종목수</th><th>상승비율</th><th>평균등락</th><th>거래대금 점유율</th><th>연속등장</th><th>확산단계</th></tr>${themeRows}</table>
  <h3>거래대금 상위 30</h3>
  <table><tr><th>순위</th><th>종목</th><th>등락률</th><th>테마</th><th>거래대금</th><th>순위변화</th></tr>${rows}</table>`;
}
async function loadDate(date) {
  const doc = document.getElementById('doc');
  doc.innerHTML = '불러오는 중…';
  try {
    const url = date ? `/api/moneyflow/${market}?date=${encodeURIComponent(date)}` : `/api/moneyflow/${market}`;
    const res = await fetch(url);
    const d = await res.json();
    const sel = document.getElementById('dateSel');
    sel.innerHTML = (d.available_dates || []).map(dt => `<option value="${dt}" ${dt===d.date?'selected':''}>${dt}</option>`).join('');
    let html = '';
    if (d.markdown) {
      html = marked.parse(d.markdown);
    } else if (d.snapshot) {
      html = fmtTop(d.snapshot);
    } else {
      html = '<p class="mf-err">아직 리포트가 없습니다. 재실행 버튼을 눌러보세요.</p>';
    }
    if (d.error && d.snapshot) html += `<p class="mf-err">⚠️ ${d.error}</p>`;
    if (d.snapshot && d.snapshot.methodology_note) html += `<p class="mf-note">${d.snapshot.methodology_note}</p>`;
    doc.innerHTML = html;
  } catch (e) {
    doc.innerHTML = '<p class="mf-err">불러오기 실패</p>';
  }
}
function setMarket(m) {
  market = m;
  document.getElementById('tabKr').classList.toggle('active', m === 'kr');
  document.getElementById('tabUs').classList.toggle('active', m === 'us');
  loadDate(null);
}
async function runNow() {
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  btn.textContent = '실행 중… (최대 1분)';
  try {
    const res = await fetch(`/api/moneyflow/${market}/run`, {method: 'POST'});
    const d = await res.json();
    let html = d.markdown ? marked.parse(d.markdown) : (d.snapshot ? fmtTop(d.snapshot) : '<p class="mf-err">실행 실패</p>');
    if (d.error && d.snapshot) html += `<p class="mf-err">⚠️ ${d.error}</p>`;
    document.getElementById('doc').innerHTML = html;
    const sel = document.getElementById('dateSel');
    sel.innerHTML = (d.available_dates || []).map(dt => `<option value="${dt}" ${dt===d.date?'selected':''}>${dt}</option>`).join('');
  } catch (e) {
    document.getElementById('doc').innerHTML = '<p class="mf-err">실행 실패</p>';
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 재실행';
  }
}
loadDate(null);
</script></body></html>"""
    return Response(html, media_type="text/html; charset=utf-8", headers=_NO_CACHE_HEADERS)


@app.get("/api/rsettings")
async def get_rsettings():
    data = dict(RSETTINGS_DEFAULT)
    if os.path.exists(RSETTINGS_PATH):
        try:
            with open(RSETTINGS_PATH, encoding="utf-8") as f:
                saved = _json.load(f)
                if isinstance(saved, dict):
                    data.update(saved)
        except (ValueError, OSError):
            pass
    return JSONResponse(data)


@app.post("/api/rsettings")
async def save_rsettings(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "객체 필요"}, status_code=400)
    # 기존 저장값 위에 머지 — 부분 업데이트가 다른 필드를 기본값으로 지우지 않게
    data = dict(RSETTINGS_DEFAULT)
    if os.path.exists(RSETTINGS_PATH):
        try:
            with open(RSETTINGS_PATH, encoding="utf-8") as f:
                saved = _json.load(f)
                if isinstance(saved, dict):
                    data.update({k: saved[k] for k in RSETTINGS_DEFAULT if k in saved})
        except (ValueError, OSError):
            pass
    data.update({k: body[k] for k in RSETTINGS_DEFAULT if k in body})
    # 검증: 게이트 값 화이트리스트, 숫자 범위 상식선
    if data["gate"] not in ("confirmed", "pressure", "correction"):
        data["gate"] = "confirmed"
    try:
        tmp = RSETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, RSETTINGS_PATH)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **data})


@app.post("/api/watch/quick")
async def watch_quick(request: Request):
    """v4.64 원클릭 관찰 등록 — 스캐너 카드에서 클릭 한 번으로 봇 감시 시작.

    [배경] 스캐너 발견 ≠ 봇 감시. 기존엔 '+ 일지에 추가' 모달에서 수동 입력해야
    봇이 감시했고, 그 마찰 때문에 등록을 미루다 돌파를 놓침(Seulki 반복 패턴).
    이 API는 카드의 피벗·손절을 그대로 받아 서버에서 일지에 1건 append한다.
    (기존 POST /api/journal은 전체 덮어쓰기라 동시성 위험 → append 전용 신설)

    body: {ticker, name, market, pivot, stop, reg_price, force_status?, entry_override?}
    중복: 같은 ticker의 pending 항목이 이미 있으면 새로 안 만들고 exists 반환.

    v5.54: category='관찰' 분기 추가 — 대장후보→눌림목 전환 관찰(👁 버튼)이
    이 엔드포인트를 재사용. 이 경우 pivot 없이도 등록 허용(대장후보엔
    피벗 개념 자체가 없음), status='watch'(봇 알림 대상 아님, 손절 없음),
    watch_kind로 관찰 종류 구분(cleanupExpiredWatches 등에서 트리거 관찰과
    다른 만료 기간 적용). 기존 category='추세추종'(기본값) 경로는 무변경.

    v5.106 [버그수정] 등록 순간 이미 현재가가 피벗 위면 대기(pending)로
    만들어도 다음 updateTracking() 첫 체크에서 곧바로 '진입'으로 전환돼
    버리던 사고(8/30 컴투스 등 5건 — 등록가=피벗 34,000, 현재가 36,000,
    등록 직후 +1.6R로 표시). reg_price(등록 시점 가격)를 받아 저장해서
    프론트가 "상태(위/아래)"가 아니라 "등록 이후 실제 교차"로 판정할 수 있게
    한다(static/index.html updateTracking 참고). 여기서는 그 안전장치의
    서버측 강제: is_observe가 아니고 pivot이 있는데 reg_price가 이미 pivot
    이상이면 force_status 없이는 거부(409) — 프론트가 이 상태를 감지해
    등록 전에 3지선다(①현재가 기준 진입 ②지정가 대기 ③취소) 모달을 띄우고,
    ①을 고르면 force_status='entered'+entry_override로 재호출한다."""
    body = await request.json()
    ticker = (body.get("ticker") or "").strip()
    if not ticker:
        return JSONResponse({"ok": False, "error": "ticker 필요"}, status_code=400)
    category = body.get("category") or "추세추종"
    is_observe = category == "관찰"
    try:
        pivot = float(body.get("pivot") or 0) or None
        stop = float(body.get("stop") or 0) or None
    except (TypeError, ValueError):
        pivot, stop = None, None
    if not pivot and not is_observe:
        return JSONResponse({"ok": False, "error": "pivot 필요"}, status_code=400)

    try:
        reg_price = float(body.get("reg_price")) if body.get("reg_price") not in (None, "") else None
    except (TypeError, ValueError):
        reg_price = None
    force_status = body.get("force_status") or None   # None | "entered"
    try:
        entry_override = float(body.get("entry_override")) if body.get("entry_override") not in (None, "") else None
    except (TypeError, ValueError):
        entry_override = None

    if not is_observe and pivot and reg_price is not None and reg_price >= pivot and force_status != "entered":
        return JSONResponse({
            "ok": False, "error": "이미 피벗 위 — 대기로 등록하면 즉시 진입 처리됩니다",
            "code": "already_above_pivot", "reg_price": reg_price, "pivot": pivot,
        }, status_code=409)

    watch_kind = body.get("watch_kind") or None
    j = load_journal()
    if is_observe:
        for r in j:
            if (r.get("ticker") == ticker and r.get("status") == "watch"
                    and r.get("watch_kind") == watch_kind):
                return JSONResponse({"ok": True, "exists": True, "id": r.get("id"),
                                     "msg": "이미 관찰 중"})
    else:
        for r in j:
            if r.get("ticker") == ticker and r.get("status") == "pending":
                return JSONResponse({"ok": True, "exists": True, "id": r.get("id"),
                                     "msg": "이미 대기 감시 중"})
    entered_now = (not is_observe) and force_status == "entered"
    if entered_now:
        entry_val = entry_override if entry_override is not None else (reg_price if reg_price is not None else pivot)
    elif is_observe:
        entry_val = None
    else:
        entry_val = pivot   # 대기 항목만 entry=피벗 관례, 관찰은 없음

    import time as _t
    rec = {
        "id": int(_t.time() * 1000),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "ticker": ticker,
        "name": body.get("name") or ticker,
        "market": body.get("market") or ("KR" if ticker[:1].isdigit() else "US"),
        "status": "watch" if is_observe else ("entered" if entered_now else "pending"),
        "category": category,
        "cat": category,
        "tab": body.get("tab") or "돌파임박",
        "signal": "",
        "pivot": pivot,
        "entry": entry_val,
        "stop": stop,
        "pivot_type": "원클릭",
        "setup_score": body.get("score") or "",
        "reg_price": reg_price,   # v5.106: 등록 시점 가격 — pending 교차판정 기준
    }
    if entered_now:
        rec["tracking"] = bool(entry_val and stop)
    if is_observe:
        rec["watch_kind"] = watch_kind
        rec["watch_start_date"] = rec["date"]
        rec["rs"] = body.get("rs")
        rec["leader_snapshot_price"] = body.get("leader_snapshot_price")
        rec["leader_snapshot_ma20_dist_pct"] = body.get("leader_snapshot_ma20_dist_pct")
    j.append(rec)
    try:
        d = os.path.dirname(JOURNAL_PATH)
        if os.path.exists(JOURNAL_PATH):
            try:
                import shutil
                shutil.copy2(JOURNAL_PATH, JOURNAL_PATH + ".bak")
            except OSError:
                pass
        tmp = JOURNAL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(j, f, ensure_ascii=False, indent=1)
        os.replace(tmp, JOURNAL_PATH)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "id": rec["id"], "pivot": pivot, "stop": stop})


@app.post("/api/watch/leader-check")
async def watch_leader_check(request: Request):
    """v5.54 대장후보→눌림목 전환 관찰(watch_kind='leader_conversion') 판정.

    [배경] 프론트는 scanner.analyze()(눌림목 게이트)를 직접 못 돌린다(백엔드
    전용). 후보안 (a)일지 탭 열 때 전용 눌림목 스캔 (b)관찰 티커만 골라
    analyze() (c)봇 폴링용 API만 우선 신설 중 (b) 채택 — 실측 결과 시장
    데이터가 이미 캐시돼 있으면(_fetch_market_data, 다른 탭 로드로 하루 한 번
    이상은 채워짐) 이 엔드포인트는 새 네트워크 호출 없이 딕셔너리 조회만
    하므로 사실상 즉시 응답, 캐시가 아직 없으면(콜드 스타트) 그냥
    pending=True로 즉시 반환하고(이 요청이 직접 fetch를 걸지 않음 — 다른
    탭 로드가 캐시를 채울 때까지 기다림), 다음 폴링 주기(60초)에 재시도.
    전체 유니버스 재스캔과 달리 요청받은 티커만 골라 analyze() 호출이라
    연산 자체도 무시할 만한 수준(20개 기준 수십 ms, scanner.py 로직
    자체는 순수 pandas 연산이라 가벼움).

    body: {tickers: [...]}
    """
    body = await request.json()
    tickers = body.get("tickers") or []
    if not isinstance(tickers, list) or not tickers:
        return JSONResponse({"ok": True, "converted": [], "pending": False})

    bundle = await _fetch_market_data("all")
    if bundle is None:
        return JSONResponse({"ok": True, "converted": [], "pending": True})

    converted = _leader_conversion_check(tickers, bundle)
    return JSONResponse({"ok": True, "converted": converted, "pending": False,
                         "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")})


def _leader_conversion_check(tickers: list, bundle: dict) -> list:
    """대장후보→눌림목 전환 판정 공용 로직 — POST /api/watch/leader-check와
    v5.110 캘린더 탭 오늘의 액션 큐가 같이 쓴다(로직 중복 방지, v5.110에서
    추출). bundle: _fetch_market_data("all")의 반환값."""
    data = bundle["data"]
    rs_ranks = bundle["rs_ranks"]
    rs_moms = bundle["rs_moms"]
    rs3_ranks = bundle.get("rs3_ranks", {})
    rs_deltas = bundle.get("rs_deltas", {})
    converted = []
    for t in tickers:
        df = data.get(t)
        if df is None:
            continue
        is_kr = t.endswith((".KS", ".KQ"))
        try:
            result = analyze(df, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t), is_kr=is_kr,
                             rs_3m=rs3_ranks.get(t), rs_delta=rs_deltas.get(t))
        except Exception:
            continue
        if result is None:
            continue
        # run_scan과 동일한 저유동성 하드 필터 — 실제 눌림목 탭에 뜨는 기준과
        # 일치시켜야 "전환됨" 배지가 진짜 탭 히트와 어긋나지 않음.
        avg_turn = result.get("avg_turnover") or 0
        floor_ = 3e8 if is_kr else 2e6
        if avg_turn > 0 and avg_turn < floor_:
            continue
        converted.append(t)
    return converted


@app.post("/api/journal")
async def save_journal(request: Request):
    """일지 전체를 통째로 저장(덮어쓰기). body = 일지 객체 배열.
    원자적 쓰기(temp→rename) + 직전 백업으로 손상/유실 방지."""
    body = await request.json()
    if not isinstance(body, list):
        return JSONResponse({"ok": False, "error": "배열 필요"}, status_code=400)
    try:
        d = os.path.dirname(JOURNAL_PATH)
        # 직전 파일을 .bak으로 백업 (덮어쓰기 전)
        if os.path.exists(JOURNAL_PATH):
            try:
                import shutil
                shutil.copy2(JOURNAL_PATH, JOURNAL_PATH + ".bak")
            except OSError:
                pass
        # 원자적 쓰기: 임시파일에 쓰고 rename (중간에 죽어도 원본 보존)
        tmp = JOURNAL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(body, f, ensure_ascii=False, indent=1)
        os.replace(tmp, JOURNAL_PATH)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "count": len(body), "path": JOURNAL_PATH})


@app.post("/api/prices")
async def batch_prices(request: Request):
    """추적용 현재가 일괄 조회. body: {"tickers": ["AAPL", "005930.KS", ...]}
    반환: {"prices": {"AAPL": 123.4, ...}, "closed": {"AAPL": true, ...},
           "highs": {...}, "volumes": {...}} (실패 종목은 각 dict에서 생략).
    v4.68: closed = 해당 티커 시장이 지금 장중이 아닌지. 일지 자동 손절 판정을
    장중 순간가가 아니라 종가로만 하기 위해 프론트에서 이 플래그로 게이트한다.
    v5.44: highs/volumes 추가 — 일지 관찰(imminent) 트리거 확인 배지용(오늘
    고가·거래량이 등록 시점 트리거 조건을 충족했는지). 이미 fetch하는 df에서
    같이 뽑는 거라 추가 네트워크 비용 없음. 기존 소비자는 새 키를 그냥
    무시하면 되니 하위호환 문제없음."""
    body = await request.json()
    tickers = body.get("tickers", []) if isinstance(body, dict) else []
    if not tickers or not isinstance(tickers, list):
        return JSONResponse({"prices": {}})
    tickers = tickers[:50]   # 안전 상한

    def _one_price(tk: str):
        try:
            if naver_kr.is_kr(tk):
                # v4.90: fetch_live_price는 결국 하루 지연된 값(가장 최근 '완결'
                # 거래일 종가)만 주는 API라 여기 쓰면 오히려 stale — siseJson
                # 일봉(fetch_history)이 이미 오늘 행을 실시간에 가깝게 채워준다.
                df = naver_kr.fetch_history(tk, days=10)
                if df is not None and not df.empty:
                    row = df.iloc[-1]
                    return tk, float(df["Close"].iloc[-1]), float(row.get("High", row["Close"])), float(row.get("Volume", 0) or 0)
            else:
                info = yf.Ticker(tk).fast_info
                p = getattr(info, "last_price", None)
                df = yf.Ticker(tk).history(period="5d", interval="1d")
                hi, vol = None, None
                if df is not None and not df.empty:
                    row = df.iloc[-1]
                    hi = float(row.get("High", row["Close"]))
                    vol = float(row.get("Volume", 0) or 0)
                if p and p > 0:
                    return tk, float(p), (hi if hi is not None else float(p)), (vol or 0.0)
                if df is not None and not df.empty:
                    return tk, float(df["Close"].iloc[-1]), hi, vol
        except Exception:
            pass
        return tk, None, None, None

    loop = asyncio.get_event_loop()
    results = await asyncio.gather(*[
        loop.run_in_executor(_executor, _one_price, tk) for tk in tickers
    ])
    prices = {tk: p for tk, p, _, _ in results if p is not None}
    highs = {tk: h for tk, _, h, _ in results if h is not None}
    volumes = {tk: vol for tk, _, _, vol in results if vol is not None}
    closed = {tk: (not _is_market_open_now(naver_kr.is_kr(tk))) for tk in prices}
    return JSONResponse({"prices": prices, "closed": closed, "highs": highs, "volumes": volumes})


# ══════════════════ v5.103: 포지션 보드 (토스 잔고 × 스캐너 결합) ══════════
# 아키텍처: Railway는 토스 Open API를 직접 못 부른다(허용 IP 방식이라 서버 IP를
# 등록해야 하는데 Railway는 배포마다 아웃바운드 IP가 안 고정됨) — 그래서 맥
# 로컬에서 sync_toss.py(launchd, 30분 간격)가 TossClient(조회전용)로 잔고를
# 읽어 최소 정보(수량/평단/티커)만 이 서버로 POST한다. 서버는 그 스냅샷을
# 저장해두고, GET 요청마다 "가격만" 새로 조회해 결합한다 — 수량·평단은
# 동기화 지연을 허용하지만 가격은 항상 최신이어야 손익이 의미있기 때문.
POSITIONS_STALE_HOURS = 24


def _verify_sync_token(request: Request):
    """sync_toss.py만 쓰는 공유 시크릿 검증. 없거나 틀리면 401.
    SYNC_TOKEN 자체가 미설정이면(로컬 개발 등) 기능을 아예 막는다 —
    빈 문자열끼리 매치되는 사고 방지."""
    expected = os.environ.get("SYNC_TOKEN")
    got = request.headers.get("X-Sync-Token")
    if not expected or not got or got != expected:
        return False
    return True


def _load_positions_raw() -> dict:
    if not os.path.exists(POSITIONS_PATH):
        return {"positions": [], "synced_at": None}
    try:
        with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {"positions": [], "synced_at": None}


def _load_positions_meta() -> dict:
    """{ticker: {"stop": float, "updated_at": iso}} — sync가 절대 안 건드리는 파일."""
    if not os.path.exists(POSITIONS_META_PATH):
        return {}
    try:
        with open(POSITIONS_META_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {}


def _save_json_atomic(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _load_sync_error():
    if not os.path.exists(SYNC_ERROR_PATH):
        return None
    try:
        with open(SYNC_ERROR_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return None


@app.post("/api/positions/sync")
async def positions_sync(request: Request):
    """sync_toss.py 전용 수신 엔드포인트. body: {"positions": [{"ticker",
    "name", "market"("KR"/"US"), "quantity", "avg_price", "currency"}, ...]}.
    계좌번호 등 식별정보는 절대 받지 않는다(sync_toss.py가 애초에 안 보냄).
    수량·평단만 통째로 덮어쓴다 — 손절가는 별도 파일(positions_meta.json)이라
    영향 없음."""
    if not _verify_sync_token(request):
        return JSONResponse({"ok": False, "error": "인증 실패 (SYNC_TOKEN)"}, status_code=401)
    body = await request.json()
    items = body.get("positions", []) if isinstance(body, dict) else []
    if not isinstance(items, list):
        return JSONResponse({"ok": False, "error": "positions 배열 필요"}, status_code=400)

    uni = get_universe(None)
    out = []
    for it in items:
        ticker = str(it.get("ticker") or "").strip()
        market = (it.get("market") or "").upper()
        if not ticker:
            continue
        resolved, unresolved = ticker, False
        if market == "KR" and not ticker.endswith((".KS", ".KQ")):
            match = next((ticker + suf for suf in (".KS", ".KQ") if (ticker + suf) in uni), None)
            if match:
                resolved = match
            else:
                unresolved = True   # 유니버스 밖(소형주/ETF 등) — 그래도 저장은 함, 가격조회만 실패할 수 있음
        try:
            qty = float(it.get("quantity") or 0)
            avg_price = float(it.get("avg_price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        out.append({
            "ticker": resolved, "name": it.get("name") or resolved, "market": market,
            "quantity": qty, "avg_price": avg_price,
            "currency": it.get("currency") or ("KRW" if market == "KR" else "USD"),
            "unresolved": unresolved,
        })

    data = {"positions": out, "synced_at": datetime.now(KST).isoformat()}
    try:
        _save_json_atomic(POSITIONS_PATH, data)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    # 성공 = IP 문제가 해소됐다는 뜻이므로 이전에 기록된 sync_error를 지운다.
    if os.path.exists(SYNC_ERROR_PATH):
        try:
            os.remove(SYNC_ERROR_PATH)
        except OSError:
            pass
    return JSONResponse({"ok": True, "count": len(out)})


@app.post("/api/positions/sync_error")
async def positions_sync_error(request: Request):
    """sync_toss.py 전용. 토스 API가 IP 미허용(403)으로 실패했을 때 상태를
    남긴다. body: {"type", "ip", "code", "message"}. 발송(텔레그램 알림)은
    여기서 하지 않는다 — 얼마냐봇이 GET /api/positions의 sync_error 필드를
    폴링해서 자체적으로 처리(dedup 포함)한다. since는 같은 ip+type이 이미
    기록돼 있으면 유지하고, 아니면 지금 시각으로 새로 시작한다."""
    if not _verify_sync_token(request):
        return JSONResponse({"ok": False, "error": "인증 실패 (SYNC_TOKEN)"}, status_code=401)
    body = await request.json()
    err_type = str(body.get("type") or "").strip()
    ip = str(body.get("ip") or "").strip()
    if not err_type:
        return JSONResponse({"ok": False, "error": "type 필요"}, status_code=400)

    now = datetime.now(KST).isoformat()
    prev = _load_sync_error()
    since = now
    if prev and prev.get("type") == err_type and prev.get("ip") == ip:
        since = prev.get("since") or now

    data = {
        "type": err_type,
        "ip": ip or None,
        "code": str(body.get("code") or "") or None,
        "message": str(body.get("message") or "") or None,
        "since": since,
        "last_seen": now,
    }
    try:
        _save_json_atomic(SYNC_ERROR_PATH, data)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


@app.post("/api/positions/stop")
async def positions_set_stop(request: Request):
    """포지션 보드에서 손절가 입력 → positions_meta.json에 저장.
    같은 티커의 열린(status=entered, 미종료) 저널 기록이 있으면 그 stop도
    같이 갱신 — 사용자 지시("저널과 연동 저장")에 따라 R시스템 손절 알림이
    포지션 보드에서 고친 손절가를 그대로 따라가게 한다."""
    body = await request.json()
    ticker = str(body.get("ticker") or "").strip()
    if not ticker:
        return JSONResponse({"ok": False, "error": "ticker 필요"}, status_code=400)
    stop = body.get("stop")
    try:
        stop_val = float(stop) if stop not in (None, "") else None
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "stop 형식 오류"}, status_code=400)

    meta = _load_positions_meta()
    if stop_val is None:
        meta.pop(ticker, None)
    else:
        meta[ticker] = {"stop": stop_val, "updated_at": datetime.now(KST).isoformat()}
    _save_json_atomic(POSITIONS_META_PATH, meta)

    journal_synced = 0
    if stop_val is not None:
        j = load_journal()
        changed = False
        for r in j:
            if r.get("ticker") == ticker and (r.get("status") or "entered") == "entered" and r.get("result_r", "") == "":
                r["stop"] = stop_val
                changed = True
                journal_synced += 1
        if changed:
            _save_json_atomic(JOURNAL_PATH, j)
    return JSONResponse({"ok": True, "journal_synced": journal_synced})


_POSITION_MODE_CHECKS = [
    ("pullback", analyze), ("turnaround", analyze_turnaround),
    ("breakout", analyze_breakout), ("boxbreak", analyze_boxbreak),
    ("imminent", analyze_imminent), ("leader", analyze_leader), ("super", analyze_super),
]


@app.get("/api/positions")
async def get_positions():
    """저장된 수량·평단(동기화 지연 허용) + 서버가 지금 가진 최신가(항상 실시간)를
    결합해 평가액·손익·R진행률·손절거리를 계산. 스캐너 컨텍스트(RS·현재
    히트중인 탭·가격고정 의심)도 같이 붙인다."""
    raw = _load_positions_raw()
    positions, synced_at = raw.get("positions", []), raw.get("synced_at")
    meta = _load_positions_meta()
    sync_error = _load_sync_error()

    stale = False
    if synced_at:
        try:
            age_hours = (datetime.now(KST) - datetime.fromisoformat(synced_at)).total_seconds() / 3600
            stale = age_hours >= POSITIONS_STALE_HOURS
        except ValueError:
            pass

    if not positions:
        return JSONResponse({"synced_at": synced_at, "stale": stale, "positions": [], "summary": None, "sync_error": sync_error})

    bundle = await _fetch_market_data("all")
    rs_ranks = bundle["rs_ranks"] if bundle else {}
    rs_moms = bundle["rs_moms"] if bundle else {}

    def _one(p):
        ticker = p["ticker"]
        df = _fetch(ticker)
        if df is None or df.empty:
            return {**p, "price": None, "unresolved": True}
        is_kr = ticker.endswith((".KS", ".KQ"))
        h, lo, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
        close = float(c.iloc[-1])
        atr_val = scanner_mod.atr(h, lo, c)
        atr_pct = (atr_val / close * 100) if close > 0 else None
        rs_used = rs_ranks.get(ticker)
        rs_mom_used = rs_moms.get(ticker)
        rs_approx = rs_used is None
        if rs_approx:
            rs_used, rs_mom_used = 80, 5

        hit_tabs = []
        for name, fn in _POSITION_MODE_CHECKS:
            try:
                if fn(df, rs_rank=rs_used, rs_mom=rs_mom_used, is_kr=is_kr) is not None:
                    hit_tabs.append(name)
            except TypeError:
                # analyze_leader/analyze_super 시그니처가 is_kr을 안 받을 수 있음
                try:
                    if fn(df, rs_rank=rs_used, rs_mom=rs_mom_used) is not None:
                        hit_tabs.append(name)
                except Exception:
                    pass
            except Exception:
                pass

        try:
            pf = price_frozen_check(c, h, lo, v)
        except Exception:
            pf = {"price_frozen": False, "price_frozen_reasons": []}
        try:
            rsi_val = round(float(scanner_mod.rsi(c).iloc[-1]), 1)
        except Exception:
            rsi_val = None

        qty, avg_price = p["quantity"], p["avg_price"]
        market_value = qty * close
        cost_basis = qty * avg_price
        pnl = market_value - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else None

        m = meta.get(ticker)
        stop = m["stop"] if m else None
        stop_suggested = False
        if stop is None and atr_val:
            stop = round(close - atr_val * 1.5, 2)
            stop_suggested = True

        r_progress = None
        dist_to_stop_pct = None
        open_risk = None
        if stop is not None and stop > 0:
            dist_to_stop_pct = round((close - stop) / close * 100, 2)
            if avg_price > stop:
                r_progress = round((close - avg_price) / (avg_price - stop), 2)
                if not stop_suggested:   # 제안값(미확정)은 "설정된 리스크"로 합산하지 않음
                    open_risk = round(qty * (avg_price - stop), 2)

        return {
            **p, "price": round(close, 2), "market_value": round(market_value, 2),
            "cost_basis": round(cost_basis, 2), "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "stop": stop, "stop_suggested": stop_suggested,
            "atr_pct": round(atr_pct, 1) if atr_pct is not None else None,
            "r_progress": r_progress, "dist_to_stop_pct": dist_to_stop_pct,
            "open_risk": open_risk,
            "rs": rs_used, "rs_approx": rs_approx, "rsi": rsi_val,
            "hit_tabs": hit_tabs,
            "price_frozen": pf.get("price_frozen", False),
            "price_frozen_reasons": pf.get("price_frozen_reasons", []),
        }

    loop = asyncio.get_event_loop()
    results = await asyncio.gather(*[
        loop.run_in_executor(_executor, _one, p) for p in positions
    ])

    summary = {"market_value": {"KRW": 0.0, "USD": 0.0}, "cost_basis": {"KRW": 0.0, "USD": 0.0},
               "pnl": {"KRW": 0.0, "USD": 0.0}, "open_risk": {"KRW": 0.0, "USD": 0.0},
               "positions_missing_stop": 0}
    for r in results:
        cur = r.get("currency") or ("KRW" if r.get("market") == "KR" else "USD")
        if r.get("market_value") is not None:
            summary["market_value"][cur] = summary["market_value"].get(cur, 0.0) + r["market_value"]
            summary["cost_basis"][cur] = summary["cost_basis"].get(cur, 0.0) + r["cost_basis"]
            summary["pnl"][cur] = summary["pnl"].get(cur, 0.0) + r["pnl"]
        if r.get("open_risk") is not None:
            summary["open_risk"][cur] = summary["open_risk"].get(cur, 0.0) + r["open_risk"]
        # "미설정"은 손절을 아예 안 입력한 경우만 센다(stop_suggested=True).
        # 손절을 평단보다 위로 올려놔서(트레일링, 이익 확정) open_risk가
        # 정의상 없는 경우까지 "미설정"으로 잘못 세면 안 됨 — 사용자는 분명히
        # 입력했으므로.
        if r.get("stop_suggested"):
            summary["positions_missing_stop"] += 1

    return JSONResponse({"synced_at": synced_at, "stale": stale, "positions": results, "summary": summary, "sync_error": sync_error})


_NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}


@app.get("/")
async def index():
    # v5.13: FileResponse 기본값은 Cache-Control을 안 보내 브라우저가 자체
    # 판단으로 이 HTML(=앱의 JS 전부)을 캐싱할 수 있음 — 배포해도 하드
    # 리프레시로도 안 바뀌는 것처럼 보이던 문제의 한 축(서비스워커 fetch 캐시
    # 문제와 별개로 서버도 명시적으로 막아야 이중 안전).
    return FileResponse("static/index.html", headers=_NO_CACHE_HEADERS)


@app.get("/sw.js")
async def service_worker():
    """PWA 서비스워커는 반드시 루트(/)에서 서빙해야 스코프가 사이트 전체(/)가 된다
    (v4.79). /static/sw.js로 등록하면 스코프가 /static/으로 좁아져 앱 설치가
    제대로 안 됨 — /static 밑 정적파일과 별도로 루트 라우트를 둔다."""
    return FileResponse("static/sw.js", media_type="application/javascript", headers=_NO_CACHE_HEADERS)


app.mount("/static", StaticFiles(directory="static"), name="static")

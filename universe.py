"""
스캔 대상 유니버스 v2.2 — 약 480종목
- KR: 코스피 주요 + 코스닥 (반도체 소부장 중점 보강)
- US: S&P500 핵심 + 성장주
- watchlist.txt 파일이 있으면 추가 스캔 (한 줄에 하나: "티커 이름")
- 잘못되거나 상폐된 티커는 수집 실패 시 자동 제외됨
"""
import os

import sys

try:
    from us_universe_ext import US_UNIVERSE_EXT
except Exception as e:
    print(f"[universe] us_universe_ext import 실패 -> EXT 비활성: {e}", file=sys.stderr)
    US_UNIVERSE_EXT = {}

try:
    from us_universe_auto import US_UNIVERSE_AUTO
except Exception as e:
    print(f"[universe] us_universe_auto import 실패 -> AUTO 비활성: {e}", file=sys.stderr)
    US_UNIVERSE_AUTO = {}
try:
    from us_universe_sectorleaders import US_UNIVERSE_SECTOR
except Exception as e:
    print(f"[universe] us_universe_sectorleaders import 실패 -> SECTOR 비활성: {e}", file=sys.stderr)
    US_UNIVERSE_SECTOR = {}

print(f"[universe] 미국 확장 로드: EXT={len(US_UNIVERSE_EXT)} AUTO={len(US_UNIVERSE_AUTO)} SECTOR={len(US_UNIVERSE_SECTOR)}", file=sys.stderr)

# ── 한국 거래대금 상위 동적 구성 (pykrx) ──
# 매 거래일 1회 KRX에서 거래대금 상위 N개를 받아 파일 캐시.
# pykrx 미설치/조회 실패 시 정적 KR_UNIVERSE로 폴백.
_KR_DYNAMIC_CACHE: dict = {}
KR_TOP_N = int(os.environ.get("KR_TOP_N", "800"))  # 코스피+코스닥 거래대금 상위 (600→800, 중소형 성장주 포착 확대)
# 장중 거래대금 급증 종목 포착: 한국 장중(09:00~15:30 KST)엔 캐시를 INTRADAY_REFRESH_MIN분마다
# 갱신해 섹터 로테이션으로 새로 거래 터지는 종목을 유니버스에 빠르게 편입. 장 외엔 하루 1회.
INTRADAY_REFRESH_MIN = int(os.environ.get("KR_INTRADAY_REFRESH_MIN", "30"))

def _kr_cache_slot() -> str:
    """캐시 키에 붙일 시간 슬롯. 한국 장중이면 30분 단위 슬롯, 그 외엔 'eod'(하루 1회).
    예) 장중 10:25 → '20260629_1000' (10:00~10:29 슬롯), 장 외 → '20260629_eod'."""
    from datetime import datetime, timezone, timedelta
    kst = datetime.now(timezone(timedelta(hours=9)))
    daykey = kst.strftime("%Y%m%d")
    # 평일 09:00~15:30만 장중으로 간주
    minutes = kst.hour * 60 + kst.minute
    is_market = (kst.weekday() < 5) and (9 * 60 <= minutes <= 15 * 60 + 30)
    if not is_market:
        return f"{daykey}_eod"
    slot = (minutes // INTRADAY_REFRESH_MIN) * INTRADAY_REFRESH_MIN
    return f"{daykey}_{slot:04d}"


def load_kr_dynamic(top_n: int = KR_TOP_N) -> dict:
    """KRX 거래대금 상위 top_n 종목을 {티커.KS/.KQ: 이름}으로 반환.
    하루 1회만 실제 조회(파일 캐시), 실패 시 빈 dict."""
    import json
    # 장중엔 30분 슬롯, 장 외엔 하루 1회로 갱신되는 캐시 키
    slotkey = _kr_cache_slot()
    # 메모리 캐시 (슬롯이 같을 때만 재사용 → 장중 30분마다 자동 무효화)
    if _KR_DYNAMIC_CACHE.get("slotkey") == slotkey and _KR_DYNAMIC_CACHE.get("data"):
        return _KR_DYNAMIC_CACHE["data"]
    # 파일 캐시 (/data 우선)
    cache_dir = os.environ.get("JOURNAL_DIR") or ("/data" if os.path.isdir("/data") else os.path.dirname(__file__))
    # 캐시 키에 top_n + 시간슬롯 포함 — 슬롯이 바뀌면 새로 받음
    # v5 = ETF 필터 재강화 (MIDAS/WON/KoAct/TIME/액티브 등 차단, v4.39.8)
    cache_path = os.path.join(cache_dir, f"kr_universe_v5_{top_n}_{slotkey}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            _KR_DYNAMIC_CACHE.update({"slotkey": slotkey, "data": data})
            return data
        except Exception:
            pass
    # 실제 조회 — 네이버 거래대금 상위 (pykrx는 KRX 로그인 요구로 폐기, v4.38.9)
    try:
        import naver_kr
        out = naver_kr.fetch_top_value(top_n)
        if out:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False)
            except Exception:
                pass
            _KR_DYNAMIC_CACHE.update({"slotkey": slotkey, "data": out})
        return out
    except Exception as e:
        import sys, traceback
        _KR_DYNAMIC_CACHE["last_error"] = f"{type(e).__name__}: {e}"
        print(f"[universe] load_kr_dynamic 실패: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        return {}


def kr_dynamic_status() -> dict:
    """KR 동적 유니버스 로딩 상태 진단."""
    import sys
    info = {"KR_TOP_N": KR_TOP_N}
    # pykrx 설치 여부
    try:
        import pykrx
        info["pykrx_installed"] = True
        info["pykrx_version"] = getattr(pykrx, "__version__", "unknown")
    except Exception as e:
        info["pykrx_installed"] = False
        info["pykrx_error"] = f"{type(e).__name__}: {e}"
    # 네이버 소스별 직접 시도 (거래대금 vs 시가총액)
    try:
        import naver_kr
        # 거래대금만 (시총 병합 전 원시 카운트는 측정 어려우니 marketcap 단독 확인)
        mcap = naver_kr.fetch_top_marketcap()
        info["naver_marketcap_count"] = len(mcap)
        info["naver_mcap_first3"] = list(mcap.items())[:3]
        full = naver_kr.fetch_top_value(800)  # 병합 결과
        info["naver_merged_count"] = len(full)
    except Exception as e:
        info["naver_error"] = f"{type(e).__name__}: {e}"
    # 실제 로딩 시도 (캐시 포함)
    dyn = load_kr_dynamic()
    info["dynamic_count"] = len(dyn)
    info["last_error"] = _KR_DYNAMIC_CACHE.get("last_error")
    info["static_count"] = len(KR_UNIVERSE)
    return info


KR_UNIVERSE = {
    # ══ 코스피 대형 ══
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "373220.KS": "LG에너지솔루션",
    "207940.KS": "삼성바이오로직스", "005380.KS": "현대차", "000270.KS": "기아",
    "068270.KS": "셀트리온", "005490.KS": "POSCO홀딩스", "035420.KS": "NAVER",
    "035720.KS": "카카오", "051910.KS": "LG화학", "006400.KS": "삼성SDI",
    "028260.KS": "삼성물산", "034730.KS": "SK", "003550.KS": "LG",
    "402340.KS": "SK스퀘어", "267250.KS": "HD현대", "000150.KS": "두산",
    # ══ 반도체 (코스피) ══
    "042700.KS": "한미반도체", "000990.KS": "DB하이텍", "108320.KQ": "LX세미콘",
    "007660.KS": "이수페타시스", "353200.KS": "대덕전자", "195870.KS": "해성디에스",
    "014680.KS": "한솔케미칼", "093370.KS": "후성", "003160.KS": "디아이",
    "281820.KS": "케이씨텍", "009150.KS": "삼성전기", "011070.KS": "LG이노텍",
    # ══ 반도체 소부장 — 장비 (코스닥) ══
    "240810.KQ": "원익IPS", "036930.KQ": "주성엔지니어링", "084370.KQ": "유진테크",
    "095610.KQ": "테스", "319660.KQ": "피에스케이", "031980.KQ": "피에스케이홀딩스",
    "039030.KQ": "이오테크닉스", "403870.KQ": "HPSP", "036200.KQ": "유니셈",
    "083450.KQ": "GST", "039440.KQ": "에스티아이", "160980.KQ": "싸이맥스",
    "079370.KQ": "제우스", "092870.KQ": "엑시콘", "086390.KQ": "유니테스트",
    "253590.KQ": "네오셈", "168360.KQ": "펨트론", "137400.KQ": "피엔티",
    "222080.KQ": "씨아이에스", "348210.KQ": "넥스틴", "140860.KQ": "파크시스템스",
    "064290.KQ": "인텍플러스", "131970.KQ": "두산테스나", "232140.KQ": "와이씨",
    # ══ 반도체 소부장 — 소재/부품 (코스닥) ══
    "357780.KQ": "솔브레인", "036830.KQ": "솔브레인홀딩스", "005290.KQ": "동진쎄미켐",
    "074600.KQ": "원익QnC", "104830.KQ": "원익머트리얼즈", "064760.KQ": "티씨케이",
    "166090.KQ": "하나머티리얼즈", "183300.KQ": "코미코", "059090.KQ": "미코",
    "101490.KQ": "에스앤에스텍", "036810.KQ": "에프에스티", "092070.KQ": "디엔에프",
    "281740.KQ": "레이크머티리얼즈", "101160.KQ": "월덱스", "272110.KQ": "케이엔제이",
    "120110.KS": "코오롱인더", "011790.KS": "SKC", "336370.KS": "솔루스첨단소재",
    # ══ 반도체 소부장 — 테스트/패키징/기판 (코스닥) ══
    "058470.KQ": "리노공업", "095340.KQ": "ISC", "131290.KQ": "티에스이",
    "067310.KQ": "하나마이크론", "033640.KQ": "네패스", "222800.KQ": "심텍",
    "080220.KQ": "제주반도체", "420770.KQ": "기가비스", "252990.KQ": "샘씨엔에스",
    # ══ 반도체 설계/IP (코스닥) ══
    "399720.KQ": "가온칩스", "200710.KQ": "에이디테크놀로지", "394280.KQ": "오픈엣지테크놀로지",
    "432720.KQ": "퀄리타스반도체", "054450.KQ": "텔레칩스", "094360.KQ": "칩스앤미디어",
    "102120.KQ": "어보브반도체",
    # ══ 2차전지 ══
    "247540.KQ": "에코프로비엠", "086520.KQ": "에코프로", "066970.KQ": "엘앤에프",
    "393890.KQ": "더블유씨피", "121600.KQ": "나노신소재", "278280.KQ": "천보",
    "078600.KQ": "대주전자재료", "450080.KS": "에코프로머티", "005070.KS": "코스모신소재",
    "348370.KQ": "엔켐", "372170.KQ": "윤성에프앤씨", "003670.KS": "포스코퓨처엠",
    # ══ 방산/우주 ══
    "012450.KS": "한화에어로스페이스", "047810.KS": "한국항공우주", "079550.KS": "LIG넥스원",
    "272210.KS": "한화시스템", "064350.KS": "현대로템", "103140.KS": "풍산",
    "214430.KQ": "아이쓰리시스템",
    # ══ 조선/기자재 ══
    "042660.KS": "한화오션", "009540.KS": "HD한국조선해양", "329180.KS": "HD현대중공업",
    "010140.KS": "삼성중공업", "443060.KS": "HD현대마린솔루션", "082740.KS": "한화엔진",
    "077970.KS": "STX엔진", "075580.KS": "세진중공업", "033500.KQ": "동성화인텍",
    "014620.KQ": "성광벤드", "023160.KQ": "태광", "013030.KQ": "하이록코리아",
    "010120.KS": "LS일렉트릭",
    # ══ 전력기기/전선 ══
    "267260.KS": "HD현대일렉트릭", "298040.KS": "효성중공업", "006260.KS": "LS",
    "001440.KS": "대한전선", "103590.KS": "일진전기", "000500.KS": "가온전선",
    "229640.KS": "LS에코에너지", "033100.KQ": "제룡전기", "034020.KS": "두산에너빌리티",
    # ══ 로봇/AI ══
    "277810.KQ": "레인보우로보틱스", "454910.KS": "두산로보틱스", "348340.KQ": "뉴로메카",
    "108490.KQ": "로보티즈", "328130.KQ": "루닛", "338220.KQ": "뷰노",
    "304100.KQ": "솔트룩스", "402030.KQ": "코난테크놀로지",
    # ══ 바이오/제약 ══
    "196170.KQ": "알테오젠", "141080.KQ": "리가켐바이오", "028300.KQ": "HLB",
    "145020.KQ": "휴젤", "214150.KQ": "클래시스", "000250.KQ": "삼천당제약",
    "214450.KQ": "파마리서치", "237690.KQ": "에스티팜", "298380.KQ": "에이비엘바이오",
    "347850.KQ": "디앤디파마텍", "326030.KS": "SK바이오팜", "302440.KS": "SK바이오사이언스",
    "000100.KS": "유한양행", "128940.KS": "한미약품", "185750.KS": "종근당",
    "069620.KS": "대웅제약", "006280.KS": "녹십자", "009420.KS": "한올바이오파마",
    "195940.KQ": "HK이노엔", "086900.KQ": "메디톡스", "214370.KQ": "케어젠",
    "039200.KQ": "오스코텍", "087010.KQ": "펩트론", "206650.KQ": "유바이오로직스",
    "053030.KQ": "바이넥스", "068760.KQ": "셀트리온제약",
    # ══ 금융 ══
    "105560.KS": "KB금융", "055550.KS": "신한지주", "086790.KS": "하나금융지주",
    "316140.KS": "우리금융지주", "024110.KS": "기업은행", "032830.KS": "삼성생명",
    "000810.KS": "삼성화재", "088350.KS": "한화생명", "138040.KS": "메리츠금융지주",
    "005830.KS": "DB손해보험", "001450.KS": "현대해상", "006800.KS": "미래에셋증권",
    "071050.KS": "한국금융지주", "005940.KS": "NH투자증권", "016360.KS": "삼성증권",
    "039490.KS": "키움증권", "323410.KS": "카카오뱅크", "377300.KS": "카카오페이",
    # ══ 인터넷/게임/엔터 ══
    "036570.KS": "엔씨소프트", "251270.KS": "넷마블", "259960.KS": "크래프톤",
    "263750.KQ": "펄어비스", "293490.KQ": "카카오게임즈", "112040.KQ": "위메이드",
    "078340.KQ": "컴투스", "194480.KQ": "데브시스터즈", "462870.KS": "시프트업",
    "067160.KQ": "SOOP", "035760.KQ": "CJ ENM", "253450.KQ": "스튜디오드래곤",
    "352820.KS": "하이브", "041510.KQ": "에스엠", "035900.KQ": "JYP Ent.",
    "122870.KQ": "와이지엔터테인먼트", "376300.KQ": "디어유",
    "018260.KS": "삼성에스디에스", "030200.KS": "KT", "017670.KS": "SK텔레콤",
    "032640.KS": "LG유플러스",
    # ══ 자동차/부품 ══
    "012330.KS": "현대모비스", "011210.KS": "현대위아", "204320.KS": "HL만도",
    "161390.KS": "한국타이어앤테크놀로지", "086280.KS": "현대글로비스",
    # ══ 화학/정유/소재 ══
    "096770.KS": "SK이노베이션", "010950.KS": "S-Oil", "078930.KS": "GS",
    "011170.KS": "롯데케미칼", "011780.KS": "금호석유", "298020.KS": "효성티앤씨",
    "009830.KS": "한화솔루션", "000880.KS": "한화", "010130.KS": "고려아연",
    # ══ 산업재/건설/운송 ══
    "042670.KS": "HD현대인프라코어", "267270.KS": "HD현대건설기계", "241560.KS": "두산밥캣",
    "000720.KS": "현대건설", "006360.KS": "GS건설", "011200.KS": "HMM",
    "003490.KS": "대한항공", "017800.KS": "현대엘리베이터", "015760.KS": "한국전력",
    "036460.KS": "한국가스공사",
    # ══ 소비재/유통 ══
    "097950.KS": "CJ제일제당", "271560.KS": "오리온", "004370.KS": "농심",
    "003230.KS": "삼양식품", "007310.KS": "오뚜기", "005180.KS": "빙그레",
    "000080.KS": "하이트진로", "033780.KS": "KT&G", "090430.KS": "아모레퍼시픽",
    "051900.KS": "LG생활건강", "161890.KS": "한국콜마", "021240.KS": "코웨이",
    "139480.KS": "이마트", "023530.KS": "롯데쇼핑", "282330.KS": "BGF리테일",
    "007070.KS": "GS리테일", "004170.KS": "신세계", "069960.KS": "현대백화점",
    "383220.KS": "F&F", "081660.KS": "휠라홀딩스", "111770.KS": "영원무역",
    "105630.KS": "한세실업", "020000.KS": "한섬", "008770.KS": "호텔신라",
    "035250.KS": "강원랜드", "030000.KS": "제일기획", "066570.KS": "LG전자",
    "034220.KS": "LG디스플레이",
}

US_UNIVERSE = {
    # ══ 메가캡 ══
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "META": "Meta", "TSLA": "Tesla", "AVGO": "Broadcom",
    "BRK-B": "Berkshire", "LLY": "Eli Lilly", "JPM": "JPMorgan", "V": "Visa",
    "UNH": "UnitedHealth", "XOM": "Exxon", "MA": "Mastercard", "COST": "Costco",
    "HD": "Home Depot", "PG": "P&G", "NFLX": "Netflix", "JNJ": "J&J",
    "WMT": "Walmart", "ORCL": "Oracle", "IBM": "IBM", "ACN": "Accenture",
    # ══ 반도체 ══
    "AMD": "AMD", "TSM": "TSMC", "ASML": "ASML", "MU": "Micron",
    "QCOM": "Qualcomm", "ARM": "Arm", "MRVL": "Marvell", "LRCX": "Lam Research",
    "AMAT": "Applied Materials", "KLAC": "KLA", "INTC": "Intel", "TXN": "Texas Instruments",
    "ADI": "Analog Devices", "NXPI": "NXP", "MCHP": "Microchip", "ON": "onsemi",
    "MPWR": "Monolithic Power", "SWKS": "Skyworks", "ENTG": "Entegris", "TER": "Teradyne",
    "GFS": "GlobalFoundries", "COHR": "Coherent", "AMKR": "Amkor", "SMCI": "Supermicro",
    "VRT": "Vertiv", "ANET": "Arista", "DELL": "Dell", "WDC": "Western Digital",
    "STX": "Seagate", "NTAP": "NetApp", "PSTG": "Pure Storage", "KEYS": "Keysight",
    # ══ 소프트웨어/클라우드 ══
    "CRM": "Salesforce", "ADBE": "Adobe", "NOW": "ServiceNow", "INTU": "Intuit",
    "PLTR": "Palantir", "SNOW": "Snowflake", "CRWD": "CrowdStrike", "PANW": "Palo Alto",
    "ZS": "Zscaler", "DDOG": "Datadog", "NET": "Cloudflare", "MDB": "MongoDB",
    "FTNT": "Fortinet", "CYBR": "CyberArk", "S": "SentinelOne", "OKTA": "Okta",
    "WDAY": "Workday", "TEAM": "Atlassian", "HUBS": "HubSpot", "GTLB": "GitLab",
    "TWLO": "Twilio", "SHOP": "Shopify", "UBER": "Uber", "ABNB": "Airbnb",
    "APP": "AppLovin", "DUOL": "Duolingo", "AXON": "Axon", "SPOT": "Spotify",
    "RBLX": "Roblox", "EA": "EA", "TTWO": "Take-Two", "RDDT": "Reddit",
    "PINS": "Pinterest", "TTD": "Trade Desk", "ROKU": "Roku", "DKNG": "DraftKings",
    "MELI": "MercadoLibre", "SE": "Sea", "BABA": "Alibaba", "PDD": "PDD",
    # ══ 금융 ══
    "GS": "Goldman", "MS": "Morgan Stanley", "BAC": "BofA", "WFC": "Wells Fargo",
    "C": "Citi", "SCHW": "Schwab", "BLK": "BlackRock", "BX": "Blackstone",
    "KKR": "KKR", "APO": "Apollo", "AXP": "AmEx", "USB": "US Bancorp",
    "PNC": "PNC", "ICE": "ICE", "CME": "CME", "SPGI": "S&P Global",
    "MCO": "Moody's", "MSCI": "MSCI", "NDAQ": "Nasdaq",
    "COIN": "Coinbase", "HOOD": "Robinhood", "SOFI": "SoFi", "PYPL": "PayPal",
    # ══ 헬스케어 ══
    "NVO": "Novo Nordisk", "MRK": "Merck", "ABBV": "AbbVie", "VRTX": "Vertex",
    "REGN": "Regeneron", "ISRG": "Intuitive Surgical", "GILD": "Gilead",
    "AMGN": "Amgen", "PFE": "Pfizer", "BMY": "Bristol Myers", "BIIB": "Biogen",
    "MRNA": "Moderna", "AZN": "AstraZeneca", "NVS": "Novartis", "ZTS": "Zoetis",
    "TMO": "Thermo Fisher", "DHR": "Danaher", "A": "Agilent", "IDXX": "IDEXX",
    "MDT": "Medtronic", "SYK": "Stryker", "BSX": "Boston Scientific", "EW": "Edwards",
    "BDX": "Becton Dickinson", "DXCM": "Dexcom", "PODD": "Insulet",
    "HCA": "HCA", "CI": "Cigna", "ELV": "Elevance", "CVS": "CVS", "MCK": "McKesson",
    # ══ 에너지/유틸 ══
    "CVX": "Chevron", "COP": "ConocoPhillips", "EOG": "EOG", "SLB": "SLB",
    "OXY": "Occidental", "DVN": "Devon", "FANG": "Diamondback", "MPC": "Marathon",
    "PSX": "Phillips 66", "VLO": "Valero", "WMB": "Williams", "KMI": "Kinder Morgan",
    "LNG": "Cheniere", "FSLR": "First Solar", "CEG": "Constellation Energy",
    "VST": "Vistra", "NEE": "NextEra", "DUK": "Duke", "SO": "Southern", "AEP": "AEP",
    # ══ 산업재 ══
    "GE": "GE Aerospace", "CAT": "Caterpillar", "DE": "Deere", "RTX": "RTX",
    "LMT": "Lockheed", "BA": "Boeing", "NOC": "Northrop", "GD": "General Dynamics",
    "LHX": "L3Harris", "HWM": "Howmet", "TDG": "TransDigm", "HEI": "HEICO",
    "ETN": "Eaton", "EMR": "Emerson", "ROK": "Rockwell", "PH": "Parker Hannifin",
    "ITW": "ITW", "MMM": "3M", "HON": "Honeywell", "CMI": "Cummins",
    "PWR": "Quanta", "URI": "United Rentals", "FAST": "Fastenal", "GWW": "Grainger",
    "UNP": "Union Pacific", "CSX": "CSX", "NSC": "Norfolk Southern",
    "UPS": "UPS", "FDX": "FedEx", "PCAR": "PACCAR", "CARR": "Carrier",
    "TT": "Trane", "JCI": "Johnson Controls", "WM": "Waste Management", "RSG": "Republic Services",
    # ══ 소비재 ══
    "NKE": "Nike", "SBUX": "Starbucks", "MCD": "McDonald's", "DIS": "Disney",
    "CMG": "Chipotle", "LULU": "Lululemon", "TJX": "TJX", "BKNG": "Booking",
    "TGT": "Target", "LOW": "Lowe's", "ROST": "Ross", "ULTA": "Ulta",
    "DECK": "Deckers", "ONON": "On Holding", "EL": "Estee Lauder", "CL": "Colgate",
    "KO": "Coca-Cola", "PEP": "PepsiCo", "MDLZ": "Mondelez", "MO": "Altria",
    "PM": "Philip Morris", "GIS": "General Mills", "HSY": "Hershey", "STZ": "Constellation Brands",
    "YUM": "Yum Brands", "DPZ": "Domino's",
    # ══ 통신/리츠 ══
    "T": "AT&T", "VZ": "Verizon", "TMUS": "T-Mobile", "CMCSA": "Comcast",
    "PLD": "Prologis", "AMT": "American Tower", "EQIX": "Equinix", "DLR": "Digital Realty",
}


def load_watchlist() -> dict:
    """watchlist.txt에서 사용자 지정 티커 로드 (티커[공백]이름 형식 지원)"""
    extra = {}
    path = os.path.join(os.path.dirname(__file__), "watchlist.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=1)
                ticker = parts[0].upper()
                name = parts[1] if len(parts) > 1 else ticker
                extra[ticker] = name
    return extra




def load_alerts() -> dict:
    """alerts.txt에서 시장경보 종목 로드 → {티커: 유형}.
    대시보드에서 추가한 항목(alerts_user.txt)도 병합."""
    alerts = {}
    for fname in ("alerts.txt", "alerts_user.txt"):
        path = os.path.join(os.path.dirname(__file__), fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=1)
                ticker = parts[0].upper()
                kind = parts[1] if len(parts) > 1 else "경보"
                alerts[ticker] = kind
    return alerts


def get_universe(market: str) -> dict:
    wl = load_watchlist()
    # 미국: 정적 대형(US_UNIVERSE) + 확장(EXT) 머지
    us_full = {**US_UNIVERSE, **US_UNIVERSE_EXT, **US_UNIVERSE_AUTO, **US_UNIVERSE_SECTOR}
    # 한국: 거래대금 상위 동적(있으면) + 정적 베이스 (동적 실패 시 폴백)
    kr_dyn = load_kr_dynamic()
    kr_full = {**KR_UNIVERSE, **kr_dyn} if kr_dyn else dict(KR_UNIVERSE)

    if market == "kr":
        base = kr_full
        base.update({t: n for t, n in wl.items() if t.endswith((".KS", ".KQ"))})
    elif market == "us":
        base = us_full
        base.update({t: n for t, n in wl.items() if not t.endswith((".KS", ".KQ"))})
    else:
        base = {**kr_full, **us_full, **wl}
    return base

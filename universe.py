"""
스캔 대상 유니버스.
- KR: KOSPI/KOSDAQ 주요 종목 (yfinance용 .KS/.KQ 접미사)
- US: S&P500 핵심 + 성장주 위주
- watchlist.txt 파일이 있으면 거기 적힌 티커를 추가로 스캔
  (한 줄에 하나, 예: 005930.KS 또는 NVDA)
"""
import os

KR_UNIVERSE = {
    # ── KOSPI 대형/주도주 ──
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "373220.KS": "LG에너지솔루션",
    "207940.KS": "삼성바이오로직스", "005380.KS": "현대차", "000270.KS": "기아",
    "068270.KS": "셀트리온", "005490.KS": "POSCO홀딩스", "035420.KS": "NAVER",
    "035720.KS": "카카오", "051910.KS": "LG화학", "006400.KS": "삼성SDI",
    "012450.KS": "한화에어로스페이스", "042660.KS": "한화오션", "009540.KS": "HD한국조선해양",
    "329180.KS": "HD현대중공업", "010140.KS": "삼성중공업", "064350.KS": "현대로템",
    "047810.KS": "한국항공우주", "079550.KS": "LIG넥스원", "272210.KS": "한화시스템",
    "034020.KS": "두산에너빌리티", "267260.KS": "HD현대일렉트릭", "103140.KS": "풍산",
    "010120.KS": "LS일렉트릭", "006260.KS": "LS", "001440.KS": "대한전선",
    "298040.KS": "효성중공업", "105560.KS": "KB금융", "055550.KS": "신한지주",
    "086790.KS": "하나금융지주", "316140.KS": "우리금융지주", "024110.KS": "기업은행",
    "032830.KS": "삼성생명", "000810.KS": "삼성화재", "088350.KS": "한화생명",
    "015760.KS": "한국전력", "036460.KS": "한국가스공사", "009830.KS": "한화솔루션",
    "011200.KS": "HMM", "003490.KS": "대한항공", "086280.KS": "현대글로비스",
    "028260.KS": "삼성물산", "000720.KS": "현대건설", "006360.KS": "GS건설",
    "097950.KS": "CJ제일제당", "271560.KS": "오리온", "004370.KS": "농심",
    "090430.KS": "아모레퍼시픽", "051900.KS": "LG생활건강", "161890.KS": "한국콜마",
    "278530.KS": "코웨이", "008770.KS": "호텔신라", "035250.KS": "강원랜드",
    "352820.KS": "하이브", "041510.KQ": "에스엠", "035900.KQ": "JYP Ent.",
    "122870.KQ": "와이지엔터테인먼트", "377300.KS": "카카오페이", "323410.KS": "카카오뱅크",
    "030200.KS": "KT", "017670.KS": "SK텔레콤", "032640.KS": "LG유플러스",
    "009150.KS": "삼성전기", "011070.KS": "LG이노텍", "066570.KS": "LG전자",
    "402340.KS": "SK스퀘어", "034730.KS": "SK", "003550.KS": "LG",
    "018260.KS": "삼성에스디에스", "036570.KS": "엔씨소프트", "251270.KS": "넷마블",
    "259960.KS": "크래프톤", "263750.KQ": "펄어비스", "293490.KQ": "카카오게임즈",
    # ── KOSDAQ 주도주 ──
    "247540.KQ": "에코프로비엠", "086520.KQ": "에코프로", "066970.KQ": "엘앤에프",
    "028300.KQ": "HLB", "196170.KQ": "알테오젠", "141080.KQ": "리가켐바이오",
    "328130.KQ": "루닛", "145020.KQ": "휴젤", "214150.KQ": "클래시스",
    "039030.KQ": "이오테크닉스", "240810.KQ": "원익IPS", "058470.KQ": "리노공업",
    "403870.KQ": "HPSP", "112040.KQ": "위메이드", "095340.KQ": "ISC",
    "036930.KQ": "주성엔지니어링", "140860.KQ": "파크시스템스", "277810.KQ": "레인보우로보틱스",
    "108320.KQ": "LX세미콘", "098460.KQ": "고영", "025900.KQ": "동화기업",
    "393890.KQ": "더블유씨피", "121600.KQ": "나노신소재", "005290.KQ": "동진쎄미켐",
    "067310.KQ": "하나마이크론", "357780.KQ": "솔브레인", "000250.KQ": "삼천당제약",
    "214450.KQ": "파마리서치", "237690.KQ": "에스티팜", "298380.KQ": "에이비엘바이오",
    "347850.KQ": "디앤디파마텍",
}

US_UNIVERSE = {
    # ── 메가캡 / 지수 주도주 ──
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "META": "Meta", "TSLA": "Tesla", "AVGO": "Broadcom",
    "BRK-B": "Berkshire", "LLY": "Eli Lilly", "JPM": "JPMorgan", "V": "Visa",
    "UNH": "UnitedHealth", "XOM": "Exxon", "MA": "Mastercard", "COST": "Costco",
    "HD": "Home Depot", "PG": "P&G", "NFLX": "Netflix", "JNJ": "J&J",
    # ── 반도체 / AI ──
    "AMD": "AMD", "TSM": "TSMC", "ASML": "ASML", "MU": "Micron",
    "QCOM": "Qualcomm", "ARM": "Arm", "MRVL": "Marvell", "LRCX": "Lam Research",
    "AMAT": "Applied Materials", "KLAC": "KLA", "SMCI": "Supermicro",
    "VRT": "Vertiv", "ANET": "Arista", "DELL": "Dell", "TER": "Teradyne",
    # ── 소프트웨어 / 클라우드 ──
    "CRM": "Salesforce", "ORCL": "Oracle", "ADBE": "Adobe", "NOW": "ServiceNow",
    "PLTR": "Palantir", "SNOW": "Snowflake", "CRWD": "CrowdStrike",
    "PANW": "Palo Alto", "ZS": "Zscaler", "DDOG": "Datadog", "NET": "Cloudflare",
    "MDB": "MongoDB", "SHOP": "Shopify", "UBER": "Uber", "ABNB": "Airbnb",
    "APP": "AppLovin", "DUOL": "Duolingo", "AXON": "Axon", "SPOT": "Spotify",
    # ── 금융 / 핀테크 ──
    "GS": "Goldman", "MS": "Morgan Stanley", "BAC": "BofA", "WFC": "Wells Fargo",
    "COIN": "Coinbase", "HOOD": "Robinhood", "SOFI": "SoFi", "PYPL": "PayPal",
    # ── 헬스케어 / 바이오 ──
    "NVO": "Novo Nordisk", "MRK": "Merck", "ABBV": "AbbVie", "VRTX": "Vertex",
    "REGN": "Regeneron", "ISRG": "Intuitive Surgical", "GILD": "Gilead",
    # ── 산업 / 에너지 / 소비 ──
    "GE": "GE Aerospace", "CAT": "Caterpillar", "DE": "Deere", "RTX": "RTX",
    "LMT": "Lockheed", "BA": "Boeing", "ETN": "Eaton", "PWR": "Quanta",
    "CEG": "Constellation Energy", "VST": "Vistra", "NEE": "NextEra",
    "NKE": "Nike", "SBUX": "Starbucks", "MCD": "McDonald's", "DIS": "Disney",
    "CMG": "Chipotle", "LULU": "Lululemon", "TJX": "TJX", "BKNG": "Booking",
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


def get_universe(market: str) -> dict:
    wl = load_watchlist()
    if market == "kr":
        base = dict(KR_UNIVERSE)
        base.update({t: n for t, n in wl.items() if t.endswith((".KS", ".KQ"))})
    elif market == "us":
        base = dict(US_UNIVERSE)
        base.update({t: n for t, n in wl.items() if not t.endswith((".KS", ".KQ"))})
    else:  # all
        base = {**KR_UNIVERSE, **US_UNIVERSE, **wl}
    return base

"""
미국 종목 확장 풀 — S&P500 + 나스닥100 핵심 + 성장/반도체 중소형.
기존 US_UNIVERSE(대형 위주)에 더해 추세전환·돌파 초입을 놓치지 않도록
중소형 성장주(VECO 등)까지 포함. universe.py에서 머지된다.

외부 의존 0 (정적 리스트). 거래대금 동적 필터(universe.py)로 잡주는 자동 제외.
티커는 yfinance 형식(예: BRK-B).
"""

US_UNIVERSE_EXT = {
    # ── 반도체 / 장비 / 광통신 (중소형 포함 — 추세전환 핵심) ──
    "VECO": "Veeco", "AAOI": "Applied Optoelectronics", "AXTI": "AXT",
    "LSCC": "Lattice", "RMBS": "Rambus", "SITM": "SiTime", "POWI": "Power Integrations",
    "SLAB": "Silicon Labs", "ALGM": "Allegro Micro", "FORM": "FormFactor",
    "ONTO": "Onto Innovation", "ACLS": "Axcelis", "ICHR": "Ichor",
    "UCTT": "Ultra Clean", "CRDO": "Credo", "ALAB": "Astera Labs",
    "WOLF": "Wolfspeed", "QRVO": "Qorvo", "DIOD": "Diodes",
    "AMBA": "Ambarella", "LITE": "Lumentum", "INDI": "indie Semi",
    "NVTS": "Navitas", "MTSI": "MACOM", "SMTC": "Semtech",
    "PI": "Impinj", "SYNA": "Synaptics", "CEVA": "Ceva",

    # ── AI / 데이터센터 / 네트워킹 ──
    "ANET": "Arista", "DELL": "Dell", "HPE": "HP Enterprise", "SMCI": "Supermicro",
    "VRT": "Vertiv", "NTAP": "NetApp", "PSTG": "Pure Storage", "WDC": "Western Digital",
    "STX": "Seagate", "CIEN": "Ciena", "JNPR": "Juniper", "FFIV": "F5",
    "NBIS": "Nebius", "CRWV": "CoreWeave", "APLD": "Applied Digital",
    "IREN": "IREN", "CORZ": "Core Scientific",

    # ── 소프트웨어 / SaaS / 사이버보안 ──
    "CRM": "Salesforce", "NOW": "ServiceNow", "SNOW": "Snowflake", "PLTR": "Palantir",
    "CRWD": "CrowdStrike", "PANW": "Palo Alto", "FTNT": "Fortinet", "ZS": "Zscaler",
    "NET": "Cloudflare", "DDOG": "Datadog", "MDB": "MongoDB", "OKTA": "Okta",
    "TEAM": "Atlassian", "WDAY": "Workday", "ADBE": "Adobe", "INTU": "Intuit",
    "HUBS": "HubSpot", "TWLO": "Twilio", "S": "SentinelOne", "CYBR": "CyberArk",
    "GTLB": "GitLab", "ESTC": "Elastic", "CFLT": "Confluent", "FROG": "JFrog",
    "PATH": "UiPath", "AI": "C3.ai", "APP": "AppLovin", "RBLX": "Roblox",
    "U": "Unity", "DOCN": "DigitalOcean", "FSLY": "Fastly", "BILL": "Bill.com",
    "ASAN": "Asana", "MNDY": "Monday.com", "PCOR": "Procore", "BRZE": "Braze",
    "RXT": "Rackspace", "WYFI": "WiSA", "BADN": "Badger",

    # ── 양자컴퓨팅 / 신기술 ──
    "IONQ": "IonQ", "RGTI": "Rigetti", "QBTS": "D-Wave", "QUBT": "Quantum Computing",
    "ARQQ": "Arqit",

    # ── 핀테크 / 결제 / 코인 ──
    "COIN": "Coinbase", "HOOD": "Robinhood", "SQ": "Block", "PYPL": "PayPal",
    "SOFI": "SoFi", "AFRM": "Affirm", "UPST": "Upstart", "NU": "Nu Holdings",
    "MSTR": "MicroStrategy", "MARA": "Marathon", "RIOT": "Riot", "CLSK": "CleanSpark",
    "BMNR": "Bitmine", "GLXY": "Galaxy Digital",

    # ── 전기차 / 클린에너지 / 원자력 ──
    "RIVN": "Rivian", "LCID": "Lucid", "ENPH": "Enphase", "FSLR": "First Solar",
    "RUN": "Sunrun", "SEDG": "SolarEdge", "PLUG": "Plug Power", "NEE": "NextEra",
    "CEG": "Constellation", "VST": "Vistra", "SMR": "NuScale", "OKLO": "Oklo",
    "LEU": "Centrus", "CCJ": "Cameco", "BWXT": "BWX Tech", "GEV": "GE Vernova",
    "TLN": "Talen", "NNE": "Nano Nuclear",

    # ── 항공우주 / 방산 ──
    "RKLB": "Rocket Lab", "LUNR": "Intuitive Machines", "ASTS": "AST SpaceMobile",
    "ACHR": "Archer", "JOBY": "Joby", "AVAV": "AeroVironment", "KTOS": "Kratos",
    "LMT": "Lockheed", "RTX": "RTX", "NOC": "Northrop", "GD": "General Dynamics",
    "BA": "Boeing", "LHX": "L3Harris", "HWM": "Howmet", "AXON": "Axon",

    # ── 헬스케어 / 바이오 (대형 + 모멘텀) ──
    "LLY": "Eli Lilly", "NVO": "Novo Nordisk", "VRTX": "Vertex", "REGN": "Regeneron",
    "ISRG": "Intuitive Surgical", "MRNA": "Moderna", "HIMS": "Hims", "TEM": "Tempus",
    "GH": "Guardant", "EXAS": "Exact Sciences", "RXRX": "Recursion", "CRSP": "CRISPR",
    "NTLA": "Intellia", "BEAM": "Beam", "ALNY": "Alnylam", "ARWR": "Arrowhead",

    # ── 소비재 / 리테일 / 기타 모멘텀 ──
    "AMZN": "Amazon", "TSLA": "Tesla", "NFLX": "Netflix", "ABNB": "Airbnb",
    "UBER": "Uber", "DASH": "DoorDash", "SHOP": "Shopify", "MELI": "MercadoLibre",
    "SE": "Sea", "CART": "Maplebear", "CVNA": "Carvana", "DKNG": "DraftKings",
    "RDDT": "Reddit", "SPOT": "Spotify", "PINS": "Pinterest", "SNAP": "Snap",
    "TTD": "Trade Desk", "ROKU": "Roku", "CELH": "Celsius", "ELF": "e.l.f.",
    "DECK": "Deckers", "ONON": "On Holding", "BIRK": "Birkenstock", "CAVA": "Cava",
    "WING": "Wingstop", "DUOL": "Duolingo", "TOST": "Toast", "AS": "Amer Sports",

    # ── 산업재 / 소재 / 에너지 ──
    "CAT": "Caterpillar", "DE": "Deere", "GE": "GE Aerospace", "ETN": "Eaton",
    "PWR": "Quanta", "URI": "United Rentals", "PH": "Parker", "EMR": "Emerson",
    "FIX": "Comfort Systems", "POWL": "Powell", "ATKR": "Atkore",
    "FCX": "Freeport", "NUE": "Nucor", "STLD": "Steel Dynamics", "CLF": "Cleveland-Cliffs",
    "MP": "MP Materials", "ALB": "Albemarle", "X": "US Steel",

    # ── 금융 (대형) ──
    "JPM": "JPMorgan", "BAC": "Bank of America", "WFC": "Wells Fargo", "MS": "Morgan Stanley",
    "GS": "Goldman Sachs", "C": "Citigroup", "SCHW": "Schwab", "BLK": "BlackRock",
    "KKR": "KKR", "APO": "Apollo", "BX": "Blackstone", "AXP": "American Express",

    # ── 메가캡 (재확인) ──
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet",
    "META": "Meta", "AVGO": "Broadcom", "ORCL": "Oracle", "CRM2": "",
    "QCOM": "Qualcomm", "AMD": "AMD", "MU": "Micron", "TSM": "TSMC",
    "ARM": "Arm", "MRVL": "Marvell", "INTC": "Intel", "AMAT": "Applied Materials",
}

# 빈 이름/플레이스홀더 제거
US_UNIVERSE_EXT = {k: v for k, v in US_UNIVERSE_EXT.items() if v and not k.endswith("2")}

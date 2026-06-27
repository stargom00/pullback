"""
산업군 주도주 보강 (RS Top 15% 산업군 + 역발상 산업군 표 기준).
스캐너 유니버스에 빠져있던 섹터 리더들을 추가. universe.py가 머지.
출처: MarketSmith 산업군 분석 (전자/SW/의료/기계/항공/은행/부동산 등).
"""
US_UNIVERSE_SECTOR = {
    # 전자-기타 / 부품 / 과학계측
    "GLW": "Corning", "IDCC": "InterDigital", "ST": "Sensata Technologies",
    "DLB": "Dolby Laboratories", "ZBRA": "Zebra Technologies",
    "CGNX": "Cognex", "FN": "Fabrinet",
    # 컴퓨터 SW/HW/네트워킹
    "CALX": "Calix",
    # 의료 (관리형/생명공학/서비스/제약/간호)
    "FMS": "Fresenius Medical Care", "NHC": "National HealthCare",
    "RHHBY": "Roche Holding ADR", "DEO": "Diageo",
    # 기계-건설/광업/농업
    "AGCO": "AGCO Corp", "CNH": "CNH Industrial", "CMCO": "Columbus McKinnon",
    # 상업서비스-인력채용/리스
    "RCRUY": "Recruit Holdings ADR",
    # 가정용 가구/가전
    "NWL": "Newell Brands",
    # 교통-항공/철도
    "UAL": "United Airlines", "LTM": "LATAM Airlines",
    # 운송-트럭
    "ODFL": "Old Dominion Freight", "JBHT": "J.B. Hunt", "TFII": "TFI International",
    "SAIA": "Saia Inc",
    # 강철-특수합금
    "TS": "Tenaris ADR", "CRS": "Carpenter Technology", "CSTM": "Constellium",
    "WS": "Worthington Steel",
    # 은행-저축대출/지역/초대형
    "FLG": "Flagstar Financial", "AX": "Axos Financial", "TFSL": "TFS Financial",
    "WSFS": "WSFS Financial", "EWBC": "East West Bancorp", "PNFP": "Pinnacle Financial",
    "FHN": "First Horizon", "WBS": "Webster Financial", "FITB": "Fifth Third Bancorp",
    "TFC": "Truist Financial",
    # 물류/자동화
    "SERV": "Serve Robotics", "RR": "Richtech Robotics",
    # 건축-대형건설
    "FER": "Ferrovial", "EME": "EMCOR Group", "MTZ": "MasTec",
    # 석유가스-정제 (역발상)
    "SUN": "Sunoco",
    # 부동산-리츠 (역발상)
    "WELL": "Welltower", "PEB": "Pebblebrook Hotel", "RLJ": "RLJ Lodging",
    # 사무용 장비 (역발상)
    "PBI": "Pitney Bowes",
    # 소매-음료/주류 (역발상)
    "CCEP": "Coca-Cola Europacific", "KDP": "Keurig Dr Pepper", "MNST": "Monster Beverage",
    "BUD": "Anheuser-Busch InBev", "ABEV": "Ambev ADR",
    # 식품-도매 (역발상)
    "UNFI": "United Natural Foods",
    # 레저-제품 (역발상)
    "BC": "Brunswick Corp", "SMNNY": "Sumitomo Mitsui ADR",
    # 교통-철도 (역발상)
    "CP": "Canadian Pacific Kansas City",
    # 건축-도구 (역발상)
    "TTNDY": "Techtronic Industries ADR",
}

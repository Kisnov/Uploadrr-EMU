from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class CBR(Unit3d):
    tracker = "CBR"
    source_flag = "CapybaraBR"
    domain = "https://capybarabr.com"
    allowed_imghosts = []
    banned_groups = [
        "3LTON",
        "4yEo",
        "ADE",
        "AFG",
        "AROMA",
        "AniHLS",
        "AniURL",
        "AnimeRG",
        "BLUDV",
        "CHD",
        "CM8",
        "Comando",
        "CrEwSaDe",
        "DNL",
        "DeadFish",
        "ELiTE",
        "FGT",
        "FRDS",
        "FUM",
        "FaNGDiNG0",
        "Flights",
        "HAiKU",
        "HD2DVD",
        "HDS",
        "HDTime",
        "Hi10",
        "Hiro360",
        "ION10",
        "JIVE",
        "KiNGDOM",
        "LEGi0N",
        "LOAD",
        "Lapumia",
        "Leffe",
        "MeGusta",
        "NOIVTC",
        "NhaNc3",
        "OFT",
        "Oj",
        "PRODJi",
        "PiRaTeS",
        "PlaySD",
        "RAPiDCOWS",
        "RARBG",
        "RDN",
        "REsuRRecTioN",
        "RMTeam",
        "RetroPeeps",
        "SANTi",
        "SPASM",
        "SPDVD",
        "STUTTERSHIT",
        "SicFoI",
        "TGx",
        "TM",
        "TRiToN",
        "Telly",
        "UPiNSMOKE",
        "URANiME",
        "WAF",
        "XS",
        "YIFY",
        "ZKBL",
        "ZMNT",
        "ZmN",
        "aXXo",
        "d3g",
        "eSc",
        "iPlanet",
        "mHD",
        "mSD",
        "nHD",
        "nSD",
        "nikt0",
        "playXD",
        "x0r",
        "xRed",
    ]

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        keywords = meta.get("keywords", "")
        anime = meta.get("anime", False)
        keywords = list(map(str.strip, keywords.lower().split(",")))
        category_id = {"MOVIE": "1", "TV": "2", "Anime": "4"}.get(category_name, "0")
        if category_name == "MOVIE":
            if anime or "anime" in keywords:
                category_id = "4"
            else:
                category_id = "1"
        elif category_name == "TV":
            if anime or "anime" in keywords:
                category_id = "4"
            else:
                category_id = "2"
        return category_id

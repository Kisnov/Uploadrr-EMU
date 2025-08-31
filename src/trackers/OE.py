from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class OE(Unit3d):
    tracker = "OE"
    source_flag = "OE"
    domain = "https://onlyencodes.cc"
    allowed_imghosts = []
    banned_groups = {  # Updated 25-08-29
        "ALL_TYPES": [
            "0neshot",
            "3LT0N",
            "4K4U",
            "4yEo",
            "$andra",
            "[Oj]",
            "AFG",
            "AkihitoSubs",
            "Alcaide_Kira",
            "AniHLS",
            "Anime Time",
            "AnimeRG",
            "AniURL",
            "AOC",
            "AR",
            "AROMA",
            "ASW",
            "aXXo",
            "BakedFish",
            "BiTOR",
            "BRrip",
            "bonkai",
            "Cleo",
            "CM8",
            "C4K",
            "CrEwSaDe",
            "core",
            "d3g",
            "DDR",
            "DE3PM",
            "DeadFish",
            "DeeJayAhmed",
            "DNL",
            "ELiTE",
            "EMBER",
            "eSc",
            "EZTV",
            "FaNGDiNG0",
            "FGT",
            "fenix",
            "FUM",
            "FRDS",
            "FROZEN",
            "GalaxyTV",
            "GalaxyRG",
            "GalaxyRG265",
            "GERMini",
            "Grym",
            "GrymLegacy",
            "HAiKU",
            "HD2DVD",
            "HDTime",
            "Hi10",
            "HiQVE",
            "ION10",
            "iPlanet",
            "INFINITY",
            "JacobSwaggedUp",
            "JIVE",
            "Judas",
            "KiNGDOM",
            "LAMA",
            "Leffe",
            "LiGaS",
            "LOAD",
            "LycanHD",
            "MeGusta",
            "MezRips",
            "mHD",
            "Mr.Deadpool",
            "mSD",
            "NemDiggers",
            "neoHEVC",
            "NeXus",
            "NhaNc3",
            "nHD",
            "nikt0",
            "nSD",
            "NOIVTC",
            "pahe.in",
            "PlaySD",
            "playXD",
            "PRODJi",
            "ProRes",
            "project-gxs",
            "PSA",
            "QaS",
            "Ranger",
            "RAPiDCOWS",
            "RARBG",
            "Raze",
            "RCDiVX",
            "RDN",
            "Reaktor",
            "REsuRRecTioN",
            "RMTeam",
            "ROBOTS",
            "rubix",
            "SANTi",
            "SHUTTERSHIT",
            "SM737",
            "SpaceFish",
            "SPASM",
            "SSA",
            "TBS",
            "Telly",
            "Tenrai-Sensei",
            "TERMiNAL",
            "TGx",
            "TM",
            "topaz",
            "ToVaR",
            "TSP",
            "TSPxL",
            "UnKn0wn",
            "URANiME",
            "UTR",
            "VipapkSudios",
            "ViSION",
            "WAF",
            "Wardevil",
            "x0r",
            "xRed",
            "XS",
            "YAWNTiC",
            "YAWNiX",
            "YakuboEncodes",
            "YIFY",
            "YTS",
            "YuiSubs",
            "ZKBL",
            "ZmN",
            "ZMNT",
        ],
        "EVO": ["ENCODE", "HDTV", "WEBRIP", "REMUX"],
    }

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        category = meta["category"]
        tv_pack = meta.get("tv_pack", 0)
        sd = meta.get("sd", 0)
        type = meta["type"]
        type_id = {"DISC": "1", "REMUX": "2", "WEBDL": "4", "WEBRIP": "5", "HDTV": "6"}.get(type, "0")
        if category == "TV" and tv_pack == 1:
            type_id = "12"
        elif sd == 1:
            type_id = "13"
        else:
            video_codec = meta.get("video_codec")
            if type == "WEBRIP":
                if video_codec == "HEVC":
                    type_id = "10"
                if video_codec == "AV1":
                    type_id = "14"
                if video_codec == "AVC":
                    type_id = "15"
            if type == "ENCODE":
                if video_codec == "HEVC":
                    type_id = "10"
                if video_codec == "AV1":
                    type_id = "14"
                if video_codec == "AVC":
                    type_id = "15"
        return type_id

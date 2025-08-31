from typing import Any, Dict

from .UNIT3D import Unit3d


class YU(Unit3d):
    tracker = "YU"
    source_flag = "YuScene"
    domain = "https://yu-scene.net"
    banned_groups = [  # Updated 25-08-30 == https://yu-scene.net/wikis/2
        "B3LLUM",
        "D3US",
        "d3g",
        "FGTKiNGDOM",
        "Lama",
        "MeGusta",
        "MezRips",
        "mHD",
        "mRS",
        "msd",
        "NeXus",
        "NhaNc3",
        "nHD",
        "RARBG",
        "Radarr",
        "RCDiVX",
        "RDN",
        "SANTi",
        "VXT",
        "Will1869",
        "x0r",
        "XS",
        "YIFY",
        "YTS",
        "ZKBL",
        "ZmN",
        "ZMNT",
    ]

    # fmt: off
    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "MUSIC": "8"
        }.get(category_name, "0")
        return category_id
    # fmt: on
    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        media_type = meta["type"]
        is_disc = meta.get("is_disc")
        if is_disc == "DVD":
            media_type = "DVD"
        type_id = {
            "DISC": "17",
            "DVD": "7",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "FLAC": "16",
            "MP3": "9",
        }.get(media_type, "0")
        return type_id

import asyncio
import os
from typing import Any, Dict

import httpx

from src.mediainfo import MediaInfoHandler
from src.trackers.UNIT3D import Unit3d
from src.utils.bbcode import BBCODE
from src.utils.console import console


class ACM(Unit3d):
    tracker = "ACM"
    source_flag = "AsianCinema"
    domain = "https://eiga.moi/"
    banned_groups = [""]
    allowed_imghosts = []

    async def get_type(self, meta):
        if meta["is_disc"] == "BDMV":
            bdinfo = meta["bdinfo"]
            bd_sizes = [25, 50, 66, 100]
            for each in bd_sizes:
                if bdinfo["size"] < each:
                    bd_size = each
                    break
            if meta["uhd"] == "UHD" and bd_size != 25:
                type_string = f"UHD {bd_size}"
            else:
                type_string = f"BD {bd_size}"
        elif meta["is_disc"] == "DVD":
            if "DVD5" in meta["dvd_size"]:
                type_string = "DVD 5"
            elif "DVD9" in meta["dvd_size"]:
                type_string = "DVD 9"
        else:
            if meta["type"] == "REMUX":
                if meta["source"] == "BluRay":
                    type_string = "REMUX"
                if meta["uhd"] == "UHD":
                    type_string = "UHD REMUX"
            else:
                type_string = meta["type"]
        return type_string

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        type = await self.get_type(meta)
        type_id = {
            "UHD 100": "1",
            "UHD 66": "2",
            "UHD 50": "3",
            "UHD REMUX": "12",
            "BD 50": "4",
            "BD 25": "5",
            "DVD 5": "14",
            "REMUX": "7",
            "WEBDL": "9",
            "SDTV": "13",
            "DVD 9": "16",
            "HDTV": "17",
        }.get(type, "0")
        return type_id

    async def get_res_id(self, meta: Dict[str, Any]) -> str:
        resolution = meta["resolution"]
        resolution_id = {
            "2160p": "1",
            "1080p": "2",
            "1080i": "2",
            "720p": "3",
            "576p": "4",
            "576i": "4",
            "480p": "5",
            "480i": "5",
        }.get(resolution, "10")
        return resolution_id

    async def get_keywords(self, meta: Dict[str, Any]) -> str:
        keywords = meta["keywords"]
        if keywords != "":
            keywords_list = keywords.split(",")
            keywords_list = [keyword for keyword in keywords_list if " " not in keyword][:4]
            keywords = ", ".join(keywords_list)
        return keywords

    def get_subtitles(self, meta: Dict[str, Any]) -> list:
        sub_lang_map = {
            ("Arabic", "ara", "ar"): "Ara",
            ("Brazilian Portuguese", "Brazilian", "Portuguese-BR", "pt-br"): "Por-BR",
            ("Bulgarian", "bul", "bg"): "Bul",
            ("Chinese", "chi", "zh", "Chinese (Simplified)", "Chinese (Traditional)"): "Chi",
            ("Croatian", "hrv", "hr", "scr"): "Cro",
            ("Czech", "cze", "cz", "cs"): "Cze",
            ("Danish", "dan", "da"): "Dan",
            ("Dutch", "dut", "nl"): "Dut",
            ("English", "eng", "en", "English (CC)", "English - SDH"): "Eng",
            ("English - Forced", "English (Forced)", "en (Forced)"): "Eng",
            ("English Intertitles", "English (Intertitles)", "English - Intertitles", "en (Intertitles)"): "Eng",
            ("Estonian", "est", "et"): "Est",
            ("Finnish", "fin", "fi"): "Fin",
            ("French", "fre", "fr"): "Fre",
            ("German", "ger", "de"): "Ger",
            ("Greek", "gre", "el"): "Gre",
            ("Hebrew", "heb", "he"): "Heb",
            ("Hindihin", "hi"): "Hin",
            ("Hungarian", "hun", "hu"): "Hun",
            ("Icelandic", "ice", "is"): "Ice",
            ("Indonesian", "ind", "id"): "Ind",
            ("Italian", "ita", "it"): "Ita",
            ("Japanese", "jpn", "ja"): "Jpn",
            ("Korean", "kor", "ko"): "Kor",
            ("Latvian", "lav", "lv"): "Lav",
            ("Lithuanian", "lit", "lt"): "Lit",
            ("Norwegian", "nor", "no"): "Nor",
            ("Persian", "fa", "far"): "Per",
            ("Polish", "pol", "pl"): "Pol",
            ("Portuguese", "por", "pt"): "Por",
            ("Romanian", "rum", "ro"): "Rom",
            ("Russian", "rus", "ru"): "Rus",
            ("Serbian", "srp", "sr", "scc"): "Ser",
            ("Slovak", "slo", "sk"): "Slo",
            ("Slovenian", "slv", "sl"): "Slv",
            ("Spanish", "spa", "es"): "Spa",
            ("Swedish", "swe", "sv"): "Swe",
            ("Thai", "tha", "th"): "Tha",
            ("Turkish", "tur", "tr"): "Tur",
            ("Ukrainian", "ukr", "uk"): "Ukr",
            ("Vietnamese", "vie", "vi"): "Vie",
        }

        sub_langs = []
        if meta.get("is_disc", "") != "BDMV":
            mihandle = MediaInfoHandler()
            mi = mihandle._cache.get(meta["uuid"])
            for track in mi["media"]["track"]:
                if track["@type"] == "Text":
                    language = track.get("Language", "None")
                    for keys, value in sub_lang_map.items():
                        if language in keys:
                            sub_langs.append(value)
        else:
            for each in meta["bdinfo"]["subtitles"]:
                for keys, value in sub_lang_map.items():
                    if each in keys:
                        sub_langs.append(value)
        sub_langs = list(set(sub_langs))
        return sub_langs

    def get_subs_tag(self, subs: list) -> str:
        if subs:
            return f" - Subs ({' / '.join(subs)})"
        return ""

    async def upload(self, meta: Dict[str, Any]) -> bool:
        await self.edit_desc(meta)
        return await super().upload(meta)

    async def edit_desc(self, meta: Dict[str, Any]) -> None:
        base = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/DESCRIPTION.txt", "r", encoding="utf-8").read()
        with open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]DESCRIPTION.txt", "w", encoding="utf-8") as descfile:
            # Add This line for all web-dls
            if meta["type"] == "WEBDL" and meta.get("service_longname", "") != "":
                descfile.write(
                    f"[center][b][color=#ff00ff][size=18]This release is sourced from {meta['service_longname']} and is not transcoded, just remuxed from the direct {meta['service_longname']} stream[/size][/color][/b][/center]"
                )
            bbcode = BBCODE()
            if meta.get("discs", []) != []:
                discs = meta["discs"]
                if discs[0]["type"] == "DVD":
                    descfile.write(f"[spoiler=VOB MediaInfo][code]{discs[0]['vob_mi']}[/code][/spoiler]\n")
                    descfile.write("\n")
                if len(discs) >= 2:
                    for each in discs[1:]:
                        if each["type"] == "BDMV":
                            pass
                        if each["type"] == "DVD":
                            descfile.write(f"{each['name']}:\n")
                            descfile.write(
                                f"[spoiler={os.path.basename(each['vob'])}][code]{each['vob_mi']}[/code][/spoiler] [spoiler={os.path.basename(each['ifo'])}][code]{each['ifo_mi']}[/code][/spoiler]\n"
                            )
                            descfile.write("\n")
            desc = base
            desc = bbcode.convert_pre_to_code(desc)
            desc = bbcode.convert_hide_to_spoiler(desc)
            desc = bbcode.convert_comparison_to_collapse(desc, 1000)
            desc = desc.replace("[img]", "[img=300]")
            descfile.write(desc)
            images = meta["image_list"]
            if len(images) > 0:
                descfile.write("[center]")
                for each in range(len(images[: int(meta["screens"])])):
                    web_url = images[each]["web_url"]
                    img_url = images[each]["img_url"]
                    descfile.write(f"[url={web_url}][img=350]{img_url}[/img][/url]")
                descfile.write("[/center]")
            if self.signature is not None:
                descfile.write(self.signature)

    async def edit_name(self, meta: Dict[str, Any]) -> str:
        name = meta.get("name")
        aka = meta.get("aka")
        original_title = meta.get("original_title")
        audio = meta.get("audio")
        source = meta.get("source")
        is_disc = meta.get("is_disc")
        subs = self.get_subtitles(meta)
        resolution = meta.get("resolution")
        if aka != "":
            # ugly fix to remove the extra space in the title
            aka = aka + " "
            name = name.replace(aka, f" / {original_title} {chr(int('202A', 16))}")
        elif aka == "":
            if meta.get("title") != original_title:
                name = name.replace(meta["title"], f"{meta['title']} / {original_title} {chr(int('202A', 16))}")
        if "AAC" in audio:
            name = name.replace(audio.strip().replace("  ", " "), audio.replace("AAC ", "AAC"))
        name = name.replace("DD+ ", "DD+")
        name = name.replace("UHD BluRay REMUX", "Remux")
        name = name.replace("BluRay REMUX", "Remux")
        name = name.replace("H.265", "HEVC")
        if is_disc == "DVD":
            name = name.replace(f"{source} DVD5", f"{resolution} DVD {source}")
            name = name.replace(f"{source} DVD9", f"{resolution} DVD {source}")
            if audio == meta.get("channels"):
                name = name.replace(f"{audio}", f"MPEG {audio}")

        name = name + self.get_subs_tag(subs)
        return name

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dupes: Dict[str, Any] = {}
        console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        params = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
            "tmdbId": meta.get("tmdb", 0),
            "categories[]": await self.get_cat_id(meta),
            "types[]": await self.get_type_id(meta),
            # ACM doesn’t always have resolution info
            # "resolutions[]": await self.get_res_id(meta),
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            try:
                response = await client.get(self.search_url, params=params)
                if response.status_code == 200:
                    response_json = response.json()
                    for each in response_json.get("data", []):
                        name = each["attributes"]["name"]
                        size = each["attributes"]["size"]
                        dupes[name] = size
                else:
                    console.print(f"[red]Failed to search: HTTP {response.status_code}[/red]")
            except Exception as e:
                console.print(
                    f"[bold red]Unable to search for existing torrents on site. Either the site is down or your API key is incorrect. Error: {e}[/bold red]"
                )
                await asyncio.sleep(5)

        return dupes

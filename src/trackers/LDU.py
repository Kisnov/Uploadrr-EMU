import asyncio
import re
from typing import Any, Dict

import httpx

from src.mediainfo import MediaInfoHandler
from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class LDU(Unit3d):
    tracker = "LDU"
    source_flag = "LDU"
    domain = "https://theldu.to"
    banned_groups = [""]
    allowed_imghosts = []

    async def get_cat_id(self, meta):
        category_name = meta["category"]
        anime = meta.get("anime", False)

        if not meta.get("xxx"):
            genres = list(map(str.strip, meta.get("genres", "").lower().split(",")))
            keywords = list(map(str.strip, meta.get("keywords", "").lower().split(",")))

        edition = meta.get("edition", "")
        adult = meta.get("adult", False)
        is_silent = meta.get("is_silent", False)
        original_language = meta.get("original_language")
        hc = meta.get("hardcoded_subs", False)

        tag = self.get_language_tag(meta) or ""
        tags = [t[1:] for t in tag.split("]") if t]

        # fmt: off
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "Anime": "8",
            "FANRES": "12",
            "SPORTS": "35",
            "MUSIC": "3",
            "XXX": "6"
        }.get(category_name, "0")
        # fmt: on
        if category_name == "SPORTS":
            return "35"
        if category_name == "MUSIC":
            return "3"

        if adult and (anime or "hentai" in keywords or "animation" in keywords):
            return "10"  # Adult anime/hentai
        if adult:
            return "6"  # Adult content

        if category_name == "MOVIE":
            if (meta["is_disc"] != "DVD" and "???" in tags and original_language != "en") or hc:
                return "27"
            if "ENG" not in "".join(tags):
                return "22"
            if "3D" in edition:
                return "21"
            if any(x in edition.upper() for x in ["FANRES", "FANEDIT", "FANFIX", "RESTORATION"]):
                return "12"
            if anime or "anime" in keywords:
                return "8"
            if "silent film" in keywords or is_silent:
                return "18"
            if "holiday" in keywords:
                return "24"
            if "musical" in keywords:
                return "25"
            if "documentary" in genres:
                return "17"
            if "stand-up" in keywords or "stand-up comedy" in keywords:
                return "20"
            if "short film" in keywords:
                return "19"
            return "1"  # Default for MOVIE

        elif category_name == "TV":
            if (meta["is_disc"] != "DVD" and "???" in tags and original_language != "en") or hc:
                return "31"
            if "ENG" not in "".join(tags):
                return "29"
            if anime or "anime" in keywords:
                return "9"
            return "2"  # Default for TV

        elif category_name == "XXX":
            if ("pack" or "megapack") in meta["uuid"]:
                return "15"
            if not meta.get("tmdb"):
                return "30"
            if meta.get("tmdb") and "ENG" not in "".join(tags):
                return "45"
        return category_id

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        type = meta["type"]
        edition = meta.get("edition", "")
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "UPSCALE": "27",
            "AAC": "10",
            "FLAC": "7",
            "MP3": "11",
            "ALAC": "8",
            "AC3": "9",
            "WAVE": "14",
            "OGG": "14",
            "MPEG4": "14",
        }.get(type, "0")
        if type == "ENCODE":
            if "upscale" in edition.lower() or " ai" in edition.lower():
                type_id = "27"
        return type_id

    def get_language_tag(self, meta):
        soundmix = meta.get("imdb_info", {}).get("soundmix", [])
        is_silent = "silent" in soundmix if soundmix else False

        mihandle = MediaInfoHandler()
        mi = mihandle._cache.get(meta["uuid"])

        def validate_language_code(lang):
            if not lang or len(lang) != 3:
                return None
            if lang == "---":
                return None
            return lang.upper()

        audio_lang = []
        sub_lang = []

        if mi:  # TODO test reliability for BDMV (whatbout TV!?)
            for x in mi["media"]["track"]:
                if x["@type"] == "Audio":
                    commentary_found = "Title" in x and ("comment" in x["Title"].lower() or "review" in x["Title"].lower())
                    if not commentary_found:
                        lang = x.get("Language_String3")
                        if lang and lang.upper() != "???":
                            audio_lang.append(lang.upper())

            # Subs
            for x in mi["media"]["track"]:
                if x["@type"] == "Text":
                    lang = x.get("Language_String3")
                    if lang and lang.upper() != "???":
                        sub_lang.append(lang.upper())

        audio_lang = list(dict.fromkeys(audio_lang))
        sub_lang = list(dict.fromkeys(sub_lang))

        # Default if empty
        if not audio_lang:
            audio_lang = ["???"]
        if not sub_lang:
            sub_lang_tag = "[No Subs]"
        else:
            if "ENG" in sub_lang:
                sub_lang_tag = "[Subs ENG+]" if len(sub_lang) > 1 else "[Subs ENG]"
            else:
                first_lang = sub_lang[0]
                sub_lang_tag = f"[Subs {first_lang}+]" if len(sub_lang) > 1 else f"[Subs {first_lang}]"

        # Compute lang_tag
        if is_silent:
            lang_tag = "[Silent]"
        else:
            lang_tag = f"[{' '.join(audio_lang)}]"

        return lang_tag + " " + sub_lang_tag

    async def edit_name(self, meta) -> str:
        if meta["category"] in ("MUSIC", "SPORTS", "XXX"):
            return meta["name"]
        ldu_name = meta["name_notag"]
        tag = meta.get("tag", "-NOGRP")
        year = str(meta.get("year", ""))
        no_tag = meta.get("no_tag", False)

        bracket_fields = ["cut", "ratio", "edition", "repack", "resolution", "source"]

        found = None
        for field in bracket_fields:
            value = (meta.get(field) or "").strip()
            if value and value in ldu_name:
                found = value
                break
        if found and found in ldu_name:
            parts = ldu_name.split(found, 1)
            # Inject the opening bracket before the first matched field
            ldu_name = f"{parts[0].strip()} [{found} {parts[1].strip()}"
            if not no_tag:
                ldu_name += f"{tag}]"
            else:
                ldu_name += "]"

        ldu_name = ldu_name.replace(year, f"({year})")

        lang_tag = self.get_language_tag(meta)
        if lang_tag:
            ldu_name += f" {lang_tag}"

        ldu_name = " ".join(ldu_name.split())
        meta["ldu_name"] = ldu_name
        return ldu_name

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dupes: Dict[str, Any] = {}
        console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        name = meta.get("edition", "") if not meta["category"] == "XXX" else meta["title"]

        params = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
            "tmdbId": meta.get("tmdb", 0),
            "categories[]": await self.get_cat_id(meta),
            "types[]": await self.get_type_id(meta),
            "resolutions[]": await self.get_res_id(meta),
            "name": name,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(self.search_url, params=params)
                if response.status_code == 200:
                    response_json = response.json()
                    for item in response_json.get("data", []):
                        name = item["attributes"]["name"]
                        size = item["attributes"]["size"]
                        match = re.split(r"(-\w*\])", name)
                        if len(match) > 1:
                            name = match[0] + match[1]
                        if "🔥Eternal" not in name:
                            dupes[name] = size
                else:
                    console.print(f"[red]Failed to search: HTTP {response.status_code}[/red]")
            except Exception as e:
                console.print(f"[red]Unable to search for existing torrents: {e}[/red]")
                await asyncio.sleep(5)
        return dupes

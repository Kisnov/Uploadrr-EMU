import re
import traceback

import requests

from src.utils.bbcode import BBCODE
from src.utils.console import console


class COMMON:
    def __init__(self, config):
        self.config = config

    async def unit3d_edit_desc(self, meta, tracker, comparison=False, desc_header="", whitelist=None):
        raise NotImplementedError("This method should be called on DescGenerator instance.")

    async def unit3d_torrent_info(self, tracker, torrent_url, id):
        tmdb = imdb = tvdb = description = category = infohash = mal = None
        imagelist = []
        params = {"api_token": self.config["TRACKERS"][tracker].get("api_key", "")}
        url = f"{torrent_url}{id}"
        response = requests.get(url=url, params=params)
        try:
            response = response.json()
            attributes = response["attributes"]
            category = attributes.get("category")
            description = attributes.get("description")
            tmdb = attributes.get("tmdb_id")
            tvdb = attributes.get("tvdb_id")
            mal = attributes.get("mal_id")
            imdb = attributes.get("imdb_id")
            infohash = attributes.get("info_hash")

            bbcode = BBCODE()  # Instance for BBCode transformations
            description, imagelist = bbcode.clean_unit3d_description(description, torrent_url)
            console.print(f"[green]Successfully grabbed description from {tracker}")
        except Exception:
            console.print(traceback.print_exc())
            console.print(f"[yellow]Invalid Response from {tracker} API.")

        return tmdb, imdb, tvdb, mal, description, category, infohash, imagelist

    async def parseCookieFile(self, cookiefile):
        """Parse a cookies.txt file and return a dictionary of key value pairs
        compatible with requests."""

        cookies = {}
        with open(cookiefile, "r") as fp:
            for line in fp:
                if not re.match(r"^#", line) and not re.match(r"^\s*$", line):  # Simplified regex check
                    lineFields = re.split(r"\s+", line.strip())  # Use regex for splitting on whitespace
                    if len(lineFields) >= 7:  # Ensure enough fields exist
                        cookies[lineFields[5]] = lineFields[6]
        return cookies

    async def filter_dupes(self, dupes, meta):
        if meta["debug"]:
            console.log("[cyan]Pre-filtered dupes")
            console.log(dupes)

        new_dupes = {}
        for each in dupes:
            remove_set = set({meta.get("resolution")}) if meta.get("sd", 0) != 1 else set()
            search_combos = [
                {"search": meta.get("hdr", ""), "search_for": {"HDR", "PQ10"}, "update": {"HDR|PQ10"}},
                {"search": meta.get("hdr", ""), "search_for": {"DV"}, "update": {"DV|DoVi"}},
                {"search": meta.get("hdr", ""), "search_not": {"DV", "DoVi", "HDR", "PQ10"}, "update": {"!(DV)|(DoVi)|(HDR)|(PQ10)"}},
                {"search": str(meta.get("tv_pack", 0)), "search_for": "1", "update": {rf"{meta.get('season')}(?!E\d+)"}},
                {
                    "search": meta.get("episode", ""),
                    "search_for": meta.get("episode", ""),
                    "update": {meta.get("season", ""), meta.get("episode", "")},
                },
            ]
            search_matches = [{"if": {"REMUX", "WEBDL", "WEBRip", "HDTV"}, "in": meta.get("type", "")}]

            for s in search_combos:
                if isinstance(s.get("search"), str) and s.get("search_for"):
                    if any(re.search(x, s["search"], flags=re.IGNORECASE) for x in s["search_for"]):
                        remove_set.update(s["update"])
                if isinstance(s.get("search_not"), str) and s.get("search_not"):
                    if not any(re.search(x, s["search"], flags=re.IGNORECASE) for x in s["search_not"]):
                        remove_set.update(s["update"])

            for sm in search_matches:
                for a in sm["if"]:
                    if a in sm["in"]:
                        remove_set.add(a)

            search = each.lower().replace("-", "").replace(" ", "").replace(".", "")
            for x in remove_set.copy():
                if x:
                    if "|" in x:
                        look_for = x.split("|")
                        for y in look_for:
                            if y.lower() in search:
                                remove_set.discard(x)
                                remove_set.add(y)

            allow = True
            for x in remove_set:
                if x:
                    if not x.startswith("!"):
                        if not re.search(x, search, flags=re.I):
                            allow = False
                    else:
                        if re.search(x.replace("!", "", 1), search, flags=re.I) not in (None, False):
                            allow = False
            if allow and each not in new_dupes:
                new_dupes[each] = dupes[each]
        return new_dupes

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

        resolution = (meta.get("resolution") or "").lower()
        # 1080p y 1080i compiten por el mismo hueco (la búsqueda ya trae los dos).
        resolutions = {"1080p", "1080i"} if resolution in ("1080p", "1080i") else {resolution}
        hdr = meta.get("hdr", "") or ""
        needs_hdr = bool(re.search("HDR|PQ10", hdr, flags=re.I))
        needs_dv = "DV" in hdr
        season, episode = meta.get("season", ""), meta.get("episode", "")
        kind = meta.get("type", "")

        new_dupes = {}
        for each in dupes:
            # Resolución y HDR por palabras enteras: «Adventure» no es Dolby Vision.
            words = set(re.split(r"[^a-z0-9+]+", each.lower()))
            search = each.lower().replace("-", "").replace(" ", "").replace(".", "")
            required = []
            if meta.get("sd", 0) != 1 and resolution:
                required.append(bool(resolutions & words))
            if needs_hdr:
                required.append(bool({"hdr", "hdr10", "hdr10+", "hdr10plus", "pq10"} & words))
            if needs_dv:
                required.append(bool({"dv", "dovi"} & words))
            if str(meta.get("tv_pack", 0)) == "1":
                required.append(bool(re.search(rf"{season}(?!E\d+)", search, flags=re.I)))
            if episode:
                required.append(season.lower() in search and episode.lower() in search)
            required.extend(a.lower() in search for a in ("REMUX", "WEBDL", "WEBRip", "HDTV") if a in kind)
            if all(required):
                new_dupes[each] = dupes[each]
        return new_dupes

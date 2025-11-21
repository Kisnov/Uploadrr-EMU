import asyncio
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console, log


class BP(Unit3d):
    tracker = "BP"
    source_flag = "BP"
    domain = "https://bitporn.eu"
    allowed_imghosts = []
    banned_groups = [""]

    # fmt: off

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        performers = meta.get('porndb', {}).get('performers', '').split(', ')
        tags = [tag.strip() for tag in meta.get('porndb', {}).get('tags', '').split(',') if tag.strip() and tag.strip() not in performers]

        prio_oder = [
            (['trans', 'shemale', 'futa', 'transexual', 'tgirl', 'tguy'], "45"),
            (['vr', 'virtual reality'], "51"),
            (['pregnant', 'preg', 'preggo'], "43"),
            (['gangbang', 'orgy'], "25"),
            (['softcore'], "46"),
            (['classic'], "14"),
            (['family', 'taboo', 'incest', 'stepcest', 'family roleplay'], "21"),
            (['pissing'], "41"),
            (['gay', 'bisexual', 'homosexual', 'homo', 'bb', 'bareback', 'all guy', 'boy on boy', 'man on man'], "27"),
            (['lesbian', 'gg', 'all girl', 'girl on girl'], "33"),
        ]

        for keywords, cat_id in prio_oder:
            for tag in tags:
                if any(keyword == tag.lower() for keyword in keywords):
                    return cat_id

        category_id = {
            "Amateur": "4",
            "Anal": "5",
            "Asian": "6",
            "BBW": "7",
            "BDSM": "8",
            "Big Ass": "9",
            "Big Tits": "10",
            "Black": "11",
            "Cartoon": "12",
            "Casting": "13",
            "Classic": "14",
            "Collection": "15",
            "Creampie": "16",
            "Cumshot": "17",
            "Deepthroat": "18",
            "Extreme": "19",
            "Fansite": "20",
            "Family": "21",
            "Feature": "22",
            "Fetish": "23",
            "Fisting": "24",
            "Gangbang": "25",
            "Game": "26",
            "Gay / Bi": "27",
            "Hair": "28",
            "Hardcore": "29",
            "HiddenCam": "30",
            "Homemade": "31",
            "Interracial": "32",
            "Lesbian": "33",
            "Magyar": "34",
            "Masturbation": "35",
            "Mature": "36",
            "Milf": "37",
            "Old and Young": "38",
            "Parody": "39",
            "Pictures": "40",
            "Pissing": "41",
            "POV": "42",
            "Pregnant": "43",
            "Public": "44",
            "Shemale": "45",
            "Softcore": "46",
            "Squirt": "47",
            "Straight": "48",
            "Teen": "49",
            "Threesome": "50",
            "VR": "51",
            "Uncategorized": "52"
        }

        for tag in tags:
            if tag in category_id:
                return category_id[tag]
        return "52"

    async def get_res_id(self, meta: Dict[str, Any]) -> str:
        resolution = meta["resolution"]
        resolution_id = {
            "8640p": "11",
            "3160p": "15",
            "4320p": "16",
            "2160p": "18",
            "1440p": "14",
            "1080p": "13",
            "1080i": "13",
            "720p": "17",
            "576p": "12",
            "576i": "12",
            "480p": "12",
            "480i": "12",
        }.get(resolution, "11")
        return resolution_id
    # fmt: on

    async def _is_valid(self, client: httpx.AsyncClient, url: Optional[str]) -> str:
        if not url or not isinstance(url, str):
            return ""
        parsed = urlparse(url)
        if not all([parsed.scheme in ("http", "https"), parsed.netloc]):
            return ""

        try:  # Try a HEAD request first (lightweight)
            response = await client.head(url, timeout=10.0)
            if 200 <= response.status_code < 300:
                return url
        except (httpx.RequestError, httpx.TimeoutException):
            pass

        try:  # Fallback GET if HEAD fails
            response = await client.get(url, timeout=10.0)
            if 200 <= response.status_code < 300:
                return url
            else:
                return ""
        except (httpx.RequestError, httpx.TimeoutException):
            return ""

    async def get_extra_data(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        cover_url = meta.get("cover_url")
        banner_url = meta.get("banner_url")

        async with httpx.AsyncClient() as client:
            cover, banner = await asyncio.gather(self._is_valid(client, cover_url), self._is_valid(client, banner_url))

        return {"cover_url": cover, "banner_url": banner}

    async def get_extra_files(self, meta: Dict[str, Any]) -> Dict[str, Tuple[str, str, str]]:
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        cover_path = tmp_dir / "cover.jpg"
        banner_path = tmp_dir / "back.jpg"
        imgs = {}

        if cover_path.exists():
            imgs["cover"] = ("cover.jpg", str(cover_path), "image/jpeg")

        if banner_path.exists():
            imgs["banner"] = ("banner.jpg", str(banner_path), "image/jpeg")

        return imgs

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dupes: Dict[str, Any] = {}
        console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        async def run_search(params: Dict[str, Any], label: str) -> Dict[str, Any]:
            results: Dict[str, Any] = {}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                try:
                    response = await client.get(self.search_url, params=params)
                    if response.status_code == 200:
                        response_json = response.json()
                        for item in response_json.get("data", []):
                            name = item["attributes"]["name"]
                            size = item["attributes"]["size"]
                            results[name] = size
                    else:
                        log.warning(f"[red]Failed to search {label}: HTTP {response.status_code}[/red]")
                except Exception as e:
                    log.error(f"[red]Unable to search {label}: {e}[/red]")
                    await asyncio.sleep(5)
            return results

        # --- Search by file_name (exact match) ---
        if meta.get("video_files"):
            file_name_params = {
                "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
                "file_name": Path(meta["video_files"][0]).name,
            }
            dupes.update(await run_search(file_name_params, "file_name"))

        # --- Search by title/types/resolutions (broader match) ---
        title_params = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
            "resolutions[]": await self.get_res_id(meta),
            "name": meta.get("title", ""),
        }
        dupes.update(await run_search(title_params, "title/resolutions"))
        return dupes

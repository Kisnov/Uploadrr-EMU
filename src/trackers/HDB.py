import glob
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, urlparse

import aiofiles
import httpx
from torf import Torrent
from unidecode import unidecode

from src.exceptions import UploadException
from src.trackers.COMMON import COMMON
from src.utils.console import console
from src.utils.torrentman import TorrentMaker, edit_torrent

"""
NOTE FROM CvT
No Access to HDBits. This Update was done blindly to try attempt a fix for reported issues. 
Please open an issue @ https://codeberg.org/CvT/Uploadrr/issues for anything broken.
Thank you :)
"""


class HDB:
    def __init__(self, config):
        self.config = config
        self.type = "http"  # TODO nexus
        self.tracker = "HDB"
        self.source_flag = "HDBits"
        self.domain = "https://hdbits.org"
        self.username = config["TRACKERS"]["HDB"].get("username", "").strip()
        self.passkey = config["TRACKERS"]["HDB"].get("passkey", "").strip()
        self.announce_url = f"{self.domain}/announce/{self.passkey}"
        self.rehost_images = config["TRACKERS"]["HDB"].get("img_rehost", True)
        self.signature = None
        self.banned_groups = [""]

    async def get_type_category_id(self, meta):
        cat_id = "EXIT"
        # 6 = Audio Track
        # 8 = Misc/Demo
        # 7 = PORN
        # 1 = Movie
        if meta["category"] == "MOVIE":
            cat_id = 1
        # 2 = TV
        if meta["category"] == "TV":
            cat_id = 2
        # 3 = Documentary
        if "documentary" in meta.get("genres", "").lower() or "documentary" in meta.get("keywords", "").lower():
            cat_id = 3
        # 4 = Music
        if meta["category"] == "MUSIC":
            cat_id = 4
        # 5 = Sport
        if meta.get("is_sports", False):
            cat_id = 5

        return cat_id

    async def get_type_codec_id(self, meta):
        codecmap = {"AVC": 1, "H.264": 1, "HEVC": 5, "H.265": 5, "MPEG-2": 2, "VC-1": 3, "XviD": 4, "VP9": 6}
        searchcodec = meta.get("video_codec", meta.get("video_encode"))
        return codecmap.get(searchcodec, "EXIT")

    async def get_type_medium_id(self, meta):
        medium_id = "EXIT"
        # 1 = Blu-ray / HD DVD
        if meta.get("is_disc", "") in ("BDMV", "HD DVD"):
            medium_id = 1
        # 4 = Capture
        if meta.get("type", "") == "HDTV":
            medium_id = 4
            if meta.get("has_encode_settings", False):
                medium_id = 3
        # 3 = Encode
        if meta.get("type", "") in ("ENCODE", "WEBRIP"):
            medium_id = 3
        # 5 = Remux
        if meta.get("type", "") == "REMUX":
            medium_id = 5
        # 6 = WEB-DL
        if meta.get("type", "") == "WEBDL":
            medium_id = 6
        return medium_id

    async def get_res_id(self, resolution):
        resolution_id = {
            "8640p": "10",
            "4320p": "1",
            "2160p": "2",
            "1440p": "3",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
        }.get(resolution, "10")
        return resolution_id

    async def get_tags(self, meta):
        tags = []

        # Web Services:
        service_dict = {
            "AMZN": 28,
            "NF": 29,
            "HULU": 34,
            "DSNP": 33,
            "HMAX": 30,
            "ATVP": 27,
            "iT": 38,
            "iP": 56,
            "STAN": 32,
            "PCOK": 31,
            "CR": 72,
            "PMTP": 69,
            "MA": 77,
            "SHO": 76,
            "BCORE": 66,
            "CORE": 66,
            "CRKL": 73,
            "FUNI": 74,
            "HLMK": 71,
            "HTSR": 79,
            "CRAV": 80,
            "MAX": 88,
        }
        if meta.get("service") in service_dict.keys():
            tags.append(service_dict.get(meta["service"]))

        # Collections
        # Masters of Cinema, The Criterion Collection, Warner Archive Collection
        distributor_dict = {
            "WARNER ARCHIVE": 68,
            "WARNER ARCHIVE COLLECTION": 68,
            "WAC": 68,
            "CRITERION": 18,
            "CRITERION COLLECTION": 18,
            "CC": 18,
            "MASTERS OF CINEMA": 19,
            "MOC": 19,
            "KINO LORBER": 55,
            "KINO": 55,
            "BFI VIDEO": 63,
            "BFI": 63,
            "BRITISH FILM INSTITUTE": 63,
            "STUDIO CANAL": 65,
            "ARROW": 64,
        }
        if meta.get("distributor") in distributor_dict.keys():
            tags.append(distributor_dict.get(meta["distributor"][0]))

        # 4K Remaster,
        if "IMAX" in meta.get("edition", ""):
            tags.append(14)
        if "OPEN MATTE" in meta.get("edition", "").upper():
            tags.append(58)

        # Audio
        # DTS:X, Dolby Atmos, Auro-3D, Silent
        if "DTS:X" in meta["audio"]:
            tags.append(7)
        if "Atmos" in meta["audio"]:
            tags.append(5)
        if meta.get("silent", False) is True:
            console.print("[yellow]zxx audio track found, suggesting you tag as silent")  # 57

        # Video Metadata
        # HDR10, HDR10+, Dolby Vision, 10-bit,
        if "HDR" in meta.get("hdr", ""):
            if "HDR10+" in meta["hdr"]:
                tags.append(25)  # HDR10+
            else:
                tags.append(9)  # HDR10
        if "DV" in meta.get("hdr", ""):
            tags.append(6)  # DV
        if "HLG" in meta.get("hdr", ""):
            tags.append(10)  # HLG

        return tags

    async def edit_name(self, meta):
        hdb_name = meta["name"]
        hdb_name = hdb_name.replace("H.265", "HEVC")
        if meta.get("source", "").upper() == "WEB" and meta.get("service", "").strip():
            hdb_name = hdb_name.replace(f"{meta.get('service', '')} ", "", 1)
        if "DV" in meta.get("hdr", ""):
            hdb_name = hdb_name.replace(" DV ", " DoVi ")
        if "HDR" in meta.get("hdr", ""):
            if "HDR10+" not in meta["hdr"]:
                hdb_name = hdb_name.replace("HDR", "HDR10")
        if meta.get("type") in ("WEBDL", "WEBRIP", "ENCODE"):
            hdb_name = hdb_name.replace(meta["audio"], meta["audio"].replace(" ", "", 1).replace("Atmos", ""))
        else:
            hdb_name = hdb_name.replace(meta["audio"], meta["audio"].replace("Atmos", ""))
        hdb_name = hdb_name.replace(meta.get("aka", ""), "")
        if meta.get("imdb_info"):
            hdb_name = hdb_name.replace(meta["title"], meta["imdb_info"]["aka"])
            if str(meta["year"]) != str(meta.get("imdb_info", {}).get("year", meta["year"])) and str(meta["year"]).strip():
                hdb_name = hdb_name.replace(str(meta["year"]), str(meta["imdb_info"]["year"]))
        hdb_name = hdb_name.replace("PQ10", "HDR").replace("Dubbed", "").replace("Dual-Audio", "").replace("REMUX", "Remux")
        hdb_name = " ".join(hdb_name.split())
        hdb_name = re.sub(r"[^0-9a-zA-ZÀ-ÿ. :&+'\-\[\]]+", "", hdb_name)
        hdb_name = hdb_name.replace(" .", ".").replace("..", ".")
        return hdb_name

    async def upload(self, meta, disctype):
        await self.edit_desc(meta)
        manual_name = meta.get("manual_name")
        hdb_name = await self.edit_name(meta)
        cat_id = await self.get_type_category_id(meta)
        codec_id = await self.get_type_codec_id(meta)
        medium_id = await self.get_type_medium_id(meta)
        hdb_tags = await self.get_tags(meta)

        for each in (cat_id, codec_id, medium_id):
            if each == "EXIT":
                console.print("[bold red]Something didn't map correctly, or this content is not allowed on HDB")
                return
        if "Dual-Audio" in meta["audio"] and not (meta["anime"] or meta["is_disc"]):
            console.print("[bold red]Dual-Audio Encodes are not allowed for non-anime and non-disc content")
            return

        base_dir = Path(meta["base_dir"])
        tmp_dir = base_dir / "tmp" / meta["uuid"]
        desc_path = tmp_dir / f"[{self.tracker}]DESCRIPTION.txt"
        torrent_name = f"[{self.tracker}]{meta['clean_name']}.torrent"
        torrent_path = tmp_dir / torrent_name

        async with aiofiles.open(desc_path, "r", encoding="utf-8") as f:
            hdb_desc = (await f.read()).rstrip()

        torrent = Torrent.read(torrent_path)
        if torrent.piece_size > 16777216:
            console.print("[red]Piece size is OVER 16M and does not work on HDB. Generating a new .torrent")
            torrentmaker = TorrentMaker(self.config)
            await torrentmaker.create(meta, meta["filepath"], "BASE", piece_size_max=16)
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)

        torrent_filename = unidecode(os.path.basename(meta["video"] if len(meta["filelist"]) == 1 else meta["path"]).replace(" ", "."))
        async with aiofiles.open(torrent_path, "rb") as torrent_file:
            torrent_data = await torrent_file.read()

        data = {
            "name": manual_name or hdb_name,
            "category": cat_id,
            "codec": codec_id,
            "medium": medium_id,
            "origin": 0,
            "descr": hdb_desc,
            "techinfo": "",
            "tags[]": hdb_tags,
        }

        # DRY Suggestion: Extract repeated config access to method
        tracker_config = self.config["TRACKERS"][self.tracker]
        if tracker_config.get("internal", False) and meta["tag"][1:] in tracker_config.get("internal_groups", []):
            data["internal"] = 1

        if meta.get("is_disc", "") != "BDMV":
            async with aiofiles.open(tmp_dir / "MEDIAINFO.txt", "r", encoding="utf-8") as f:
                data["techinfo"] = await f.read()

        if meta.get("tvdb_id", 0) != 0:
            data["tvdb"] = meta["tvdb_id"]
        if int(meta.get("imdb_id", 0)) != 0:
            data["imdb"] = f"https://www.imdb.com/title/tt{meta['imdb_id']:07d}/"
        if meta["category"] == "TV":
            data["tvdb_season"] = int(meta.get("season_int", 1))
            data["tvdb_episode"] = int(meta.get("episode_int", 1))

        url = "https://hdbits.org/upload/upload"
        files = {"file": (f"{torrent_filename}.torrent", torrent_data, "application/x-bittorrent")}

        async with httpx.AsyncClient(cookies=await self._get_cookies(meta)) as client:
            if meta["debug"]:
                console.print(url)
                console.print(data)
            else:
                response = await client.post(url, data=data, files=files)
                result_url = str(response.url)
                if response.status_code == 200 and "hdbits.org/details.php" in result_url:
                    id_match = re.search(r"(id=)(\d+)", urlparse(result_url).query)
                    if id_match:
                        await self.download_new_torrent(id_match.group(2), torrent_path)
                    else:
                        raise UploadException(f"Upload to HDB Failed: Could not extract ID from {result_url}", "red")
                else:
                    console.print(data)
                    console.print(f"\n\nResponse: {response.text}")
                    raise UploadException(
                        f"Upload to HDB Failed: result URL {result_url} ({response.status_code}) was not expected", "red"
                    )

    async def _get_cookies(self, meta):
        common = COMMON(config=self.config)
        cookiefile = Path(meta["base_dir"]) / "data" / "cookies" / "HDB.txt"
        return await common.parseCookieFile(cookiefile)

    async def search_existing(self, meta, disctype):
        dupes = []
        console.print("[yellow]Searching for existing torrents on HDB...")
        url = "https://hdbits.org/api/torrents"
        data = {
            "username": self.username,
            "passkey": self.passkey,
            "category": await self.get_type_category_id(meta),
            "codec": await self.get_type_codec_id(meta),
            "medium": await self.get_type_medium_id(meta),
            "search": meta["resolution"],
        }
        if int(meta.get("imdb_id", 0)) != 0:
            data["imdb"] = {"id": meta["imdb_id"]}
        if int(meta.get("tvdb_id", 0)) != 0:
            data["tvdb"] = {"id": meta["tvdb_id"]}

        async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
            try:
                response = await client.post(url, json=data)
                if response.status_code == 200:
                    response_data = response.json()
                    dupes.extend(each["name"] for each in response_data.get("data", []))
                else:
                    console.print(f"[bold red]HTTP request failed. Status: {response.status_code}")
            except httpx.HTTPError as e:
                console.print(f"[bold red]Error searching torrents: {e}")
        return dupes

    async def validate_api(self):
        data = {"username": self.username, "passkey": self.passkey}
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(f"{self.domain}/api/test", content=json.dumps(data))
                return response.json().get("status", 5) == 0
            except httpx.HTTPError:
                return False

    async def validate_cookies(self, meta):
        common = COMMON(config=self.config)
        cookiefile = Path(meta["base_dir"]) / "data" / "cookies" / "HDB.txt"
        if not cookiefile.exists():
            console.print("[bold red]Missing Cookie File. (data/cookies/HDB.txt)")
            return False
        cookies = await common.parseCookieFile(cookiefile)
        async with httpx.AsyncClient(cookies=cookies) as client:
            response = await client.get(self.domain)
            text = response.text
            if meta["debug"]:
                console.print("[cyan]Cookies:")
                console.print(cookies)
                console.print("\n\n")
                console.print(text)
            return '<a href="/logout.php">Logout</a>' in text

    async def download_new_torrent(self, id, torrent_path):
        data = {"username": self.username, "passkey": self.passkey, "id": id}
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.domain}/api/torrents", content=json.dumps(data))
            filename = response.json()["data"][0]["filename"]
            download_url = f"{self.domain}/download.php/{quote(filename)}"
            params = {"passkey": self.passkey, "id": id}
            response = await client.get(download_url, params=params)
            async with aiofiles.open(torrent_path, "wb") as tor:
                await tor.write(response.content)

    async def edit_desc(self, meta):
        base_dir = Path(meta["base_dir"])
        tmp_dir = base_dir / "tmp" / meta["uuid"]

        async with aiofiles.open(tmp_dir / "DESCRIPTION.txt", "r", encoding="utf-8") as f:
            base = await f.read()

        async with aiofiles.open(tmp_dir / f"[{self.tracker}]DESCRIPTION.txt", "w", encoding="utf-8") as descfile:
            from src.utils.bbcode import BBCODE

            if meta["type"] == "WEBDL" and meta.get("service_longname", "") and not meta.get("description"):
                await descfile.write(f"[center][quote]This release is sourced from {meta['service_longname']}[/quote][/center]")

            bbcode = BBCODE()
            if meta.get("discs", []):
                discs = meta["discs"]
                disc_type_handlers = {
                    "DVD": lambda disc: f"[quote=VOB MediaInfo]{disc['vob_mi']}[/quote]\n\n",
                    "BDMV": lambda disc: f"[quote]{disc['summary'].strip()}[/quote]\n\n",
                }
                await descfile.write(disc_type_handlers[discs[0]["type"]](discs[0]))

                if len(discs) >= 2:
                    for disc in discs[1:]:
                        if disc["type"] == "BDMV":
                            await descfile.write(f"[quote={disc.get('name', 'BDINFO')}]{disc['summary']}[/quote]\n\n")
                        elif disc["type"] == "DVD":
                            await descfile.write(
                                f"{disc['name']}:\n[quote={os.path.basename(disc['vob'])}][{disc['vob_mi']}[/quote] [quote={os.path.basename(disc['ifo'])}][{disc['ifo_mi']}[/quote]\n\n"
                            )

            desc = base
            bbcode_transforms = [
                bbcode.convert_code_to_quote,
                bbcode.convert_spoiler_to_hide,
                lambda d: bbcode.convert_comparison_to_centered(d, 1000),
                lambda d: re.sub(r"(\[img=\d+)]", "[img]", d, flags=re.IGNORECASE),
            ]
            for transform in bbcode_transforms:
                desc = transform(desc)
            await descfile.write(desc)

            if self.rehost_images:
                console.print("[green]Rehosting Images...")
                hdbimg_bbcode = await self.hdbimg_upload(meta)
                await descfile.write(f"{hdbimg_bbcode}")
            else:
                images = meta["image_list"]
                if images:
                    await descfile.write("[center]")
                    for each in images[: int(meta["screens"])]:
                        await descfile.write(f"[url={each['web_url']}][img]{each['img_url']}[/img][/url]")
                    await descfile.write("[/center]")
            if self.signature:
                await descfile.write(self.signature)

    async def hdbimg_upload(self, meta):
        image_glob = glob.glob("*.png")
        unwanted_patterns = ["FILE*", "PLAYLIST*", "POSTER*"]
        unwanted_files = {file for pattern in unwanted_patterns for file in glob.glob(pattern)}
        images = list(set(image_glob) - unwanted_files)
        url = "https://img.hdbits.org/upload_api.php"
        data = {
            "username": self.username,
            "passkey": self.passkey,
            "galleryoption": "1",
            "galleryname": meta["name"],
            "thumbsize": "w300",
        }
        screen_count = min(len(images), 3 if meta["category"] == "TV" and meta.get("tv_pack", 0) == 0 else 6)

        async with httpx.AsyncClient() as client:
            files = {}
            for i in range(screen_count):
                async with aiofiles.open(images[i], "rb") as f:
                    files[f"images_files[{i}]"] = (f"image_{i}.png", await f.read(), "image/png")
            response = await client.post(url, data=data, files=files)
            return response.text

    async def get_info_from_torrent_id(self, hdb_id):
        url = "https://hdbits.org/api/torrents"
        data = {"username": self.username, "passkey": self.passkey, "id": hdb_id}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, json=data)
            if response.status_code == 200:
                r = response.json()
                if r.get("data"):
                    first = r["data"][0]
                    return (
                        int(first.get("imdb", {}).get("id") or 0),
                        int(first.get("tvdb", {}).get("id") or 0),
                        first.get("name"),
                        first.get("hash"),
                    )
        return None, None, None, None

    async def search_filename(self, search_term, search_file_folder, meta):
        url = "https://hdbits.org/api/torrents"
        base_dir = Path(meta["base_dir"])
        if search_file_folder == "folder" and meta.get("is_disc"):
            bd_summary_path = base_dir / "tmp" / meta["uuid"] / "BD_SUMMARY_00.txt"
            async with aiofiles.open(bd_summary_path, "r", encoding="utf-8") as f:
                for line in await f.readlines():
                    if "Disc Title:" in line:
                        bd_summary = line.split("Disc Title:")[1].strip()
                        break
                else:
                    console.print(f"[red]Error: 'Disc Title' not found in {bd_summary_path}[/red]")
                    return None, None, None, None, None
            data = {"username": self.username, "passkey": self.passkey, "limit": 100, "search": bd_summary}
            console.print(f"[green]Searching HDB for disc title: [bold yellow]{bd_summary}[/bold yellow]")
        else:
            data = {"username": self.username, "passkey": self.passkey, "limit": 100, "file_in_torrent": os.path.basename(search_term)}
            console.print(f"[green]Searching HDB for file: [bold yellow]{os.path.basename(search_term)}[/bold yellow]")

        async with httpx.AsyncClient() as client:
            response = await client.get(url, json=data)
            if response.status_code == 200:
                r = response.json()
                for each in r.get("data", []):
                    hdb_id = each.get("id")
                    console.print(
                        f"[bold green]Matched release with HDB ID: [yellow]https://hdbits.org/details.php?id={hdb_id}[/yellow][/bold green]"
                    )
                    return (
                        int(each.get("imdb", {}).get("id") or 0),
                        int(each.get("tvdb", {}).get("id") or 0),
                        each.get("name"),
                        each.get("hash"),
                        hdb_id,
                    )
                console.print("[yellow]Could not find a matching release on HDB[/yellow]")
        return None, None, None, None, None

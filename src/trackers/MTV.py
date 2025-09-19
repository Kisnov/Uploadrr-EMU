import asyncio
import pickle
import re
import traceback
import xml.etree.ElementTree
from pathlib import Path
from typing import Any, Dict

import aiofiles
import httpx
from rich.pretty import Pretty
from rich.prompt import Confirm
from torf import Torrent

from src.mediainfo import MediaInfoHandler
from src.utils.console import console, log
from src.utils.torrentman import TorrentMaker, edit_torrent


class MTV:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.type = "http"  # TODO Luminance/Gazelle
        self.tracker = "MTV"
        self.source_flag = "MTV"
        self.timeout = 45
        announce = config["TRACKERS"][self.tracker].get("PID", "PASSKEY_HERE")
        if announce.startswith("https://"):
            self.announce_url = announce
        else:
            self.announce_url = f"http://tracker.morethantv.me:2710/{announce}/announce"
        self.upload_url = "https://www.morethantv.me/upload.php"
        self.forum_link = "https://www.morethantv.me/wiki.php?action=article&id=73"
        self.search_url = "https://www.morethantv.me/api/torznab"
        self.allowed_imghosts = ["ptpimg", "imgbox", "empornium", "ibb"]
        self.banned_groups = {
            "ALL_TYPES": [
                "aXXo",
                "BRrip",
                "CM8",
                "CrEwSaDe",
                "DNL",
                "FaNGDiNG0",
                "FRDS",
                "HD2DVD",
                "HDTime",
                "iPlanet",
                "KiNGDOM",
                "Leffe",
                "mHD",
                "mSD",
                "nHD",
                "nikt0",
                "nSD",
                "NhaNc3",
                "PRODJi",
                "RDN",
                "SANTi",
                "STUTTERSHIT",
                "TERMiNAL",
                "ViSION",
                "WAF",
                "x0r",
                "YIFY",
            ],
            "EVO": ["DISC", "REMUX", "WEBRIP", "HDTV", "DVDRIP", "ENCODE"],
        }

    async def upload(self, meta: Dict[str, Any]):
        cookiefile = Path(meta["base_dir"]) / "data" / "cookies" / "MTV.pkl"

        if Torrent.read(Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BASE.torrent").piece_size > 8388608:
            console.print("[red]Piece size is OVER 8M and does not work on MTV. Generating a new .torrent")
            torrentmaker = TorrentMaker(self.config)
            await torrentmaker.create(meta, meta["filepath"], "BASE", piece_size_max=8)
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)

        cat_id = await self.get_cat_id(meta)
        resolution_id = await self.get_res_id(meta["resolution"])
        source_id = await self.get_source_id(meta)
        origin_id = await self.get_origin_id(meta)
        des_tags = await self.get_tags(meta)

        await self.edit_desc(meta)
        group_desc = await self.edit_group_desc(meta)

        mtv_name = await self.edit_name(meta)
        manual_name = meta.get("manual_name")

        anon = 1 if meta["anon"] != 0 or self.config["TRACKERS"][self.tracker].get("anon", False) else 0

        desc_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"
        desc = desc_path.read_text(encoding="utf-8")

        torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]{meta['clean_name']}.torrent"
        tfile = torrent_path.read_bytes()

        files = {"file_input": (f"{meta['name']}.torrent", tfile)}

        data = {
            "image": "",
            "title": manual_name or mtv_name,
            "category": cat_id,
            "Resolution": resolution_id,
            "source": source_id,
            "origin": origin_id,
            "taglist": des_tags,
            "desc": desc,
            "groupDesc": group_desc,
            "ignoredupes": "1",
            "genre_tags": "---",
            "autocomplete_toggle": "on",
            "fontfont": "-1",
            "fontsize": "-1",
            "auth": await self.get_auth(cookiefile),
            "anonymous": anon,
            "submit": "true",
        }

        param = {}
        if meta["imdb_id"] not in ("0", "", None):
            param["imdbID"] = "tt" + meta["imdb_id"]
        if meta["tmdb"] != 0:
            param["tmdbID"] = meta["tmdb"]
        if meta["tvdb_id"] != 0:
            param["thetvdbID"] = meta["tvdb_id"]
        if meta["tvmaze_id"] != 0:
            param["tvmazeID"] = meta["tvmaze_id"]

        if meta.get("debug", False):
            log.debug("[magenta]DATA 2 SEND[/magenta]:")
            console.print(Pretty(data))
            return "DEBUG"

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            if cookiefile.exists():
                async with aiofiles.open(cookiefile, "rb") as cf:
                    cookies = pickle.load(cf)
                    client.cookies.update(cookies)
            response = await client.post(url=self.upload_url, data=data, files=files)
            try:
                if "torrents.php" in response.url:
                    comment = f"Made by Uploadrr for {response.url}"
                    await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url, comment)
                    return True
                elif "https://www.morethantv.me/upload.php" in response.url:
                    log.warning("Stayed on Upload Page, Upload may have failed - Please Check.")
                    return "Unexpected Response"
                    if "error" in response.text.lower() or "failed" in response.text.lower():
                        log.error("Upload Failed - Review Server Response")
                        console.print(Pretty(data))
                        return False
                elif response.url in ["https://www.morethantv.me/", "https://www.morethantv.me/index.php"]:
                    if "Project Luminance" in response.text:
                        return "Not Logged In"
                    elif "'GroupID' cannot be null" in response.text:
                        log.warning("[red]Likely SITE BUG[/red]: https://www.morethantv.me/forum/thread/3338?")
                        return False
                    elif "Integrity constraint violation" in response.text:
                        log.warning("[red]Likely SITE BUG[/red]: https://www.morethantv.me/forum/thread/3338?")
                        return "Site Bug"

                else:
                    if "authkey.php" in response.url:
                        log.warning("Upload may have failed - Please Check.")
                        return "Unexpected Response"
                    else:
                        console.print(f"response URL: {response.url}")
                        console.print(f"response status: {response.status_code}")
                        console.print(Pretty(data))
                        return False
            except Exception:
                console.warning("[red]It may have uploaded, please go check")
                console.print(Pretty(data))
                traceback.print_exc()
                return "Unknown Error"
        return True

    async def edit_desc(self, meta: dict):
        from src.utils.descgen import DescGenerator

        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        base_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "DESCRIPTION.txt"
        desc_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"

        base = await base_path.read_text(encoding="utf-8")

        images = meta["image_list"]

        async with aiofiles.open(desc_path, mode="w", encoding="utf-8") as desc:
            if meta["bdinfo"] is not None:
                mi_dump = None
                bd_dump_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BD_SUMMARY_00.txt"
                bd_dump = await bd_dump_path.read_text(encoding="utf-8")
            else:
                mi_dump_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "MEDIAINFO.txt"
                mi_dump = (await mi_dump_path.read_text(encoding="utf-8"))[:-65].strip()
                bd_dump = None

            if bd_dump:
                await desc.write("[mediainfo]" + bd_dump + "[/mediainfo]\n\n")
            elif mi_dump:
                await desc.write("[mediainfo]" + mi_dump + "[/mediainfo]\n\n")

            if images:
                await desc.write("[spoiler=Screenshots]\n")
                await descgen._write_images(desc, meta, self.tracker, whitelist=self.allowed_imghosts, img_size=250)
                await desc.write("\n[/spoiler]")

            await desc.write(f"\n\n[spoiler=Notes]{base}[/spoiler]")

    async def edit_group_desc(self, meta):
        description = ""
        if meta["imdb_id"] not in ("0", "", None):
            description += f"https://www.imdb.com/title/tt{meta['imdb_id']}"
        if meta["tmdb"] != 0:
            description += f"\nhttps://www.themoviedb.org/{str(meta['category'].lower())}/{str(meta['tmdb'])}"
        if meta["tvdb_id"] != 0:
            description += f"\nhttps://www.thetvdb.com/?id={str(meta['tvdb_id'])}"
        if meta["tvmaze_id"] != 0:
            description += f"\nhttps://www.tvmaze.com/shows/{str(meta['tvmaze_id'])}"
        if meta["mal_id"] != 0:
            description += f"\nhttps://myanimelist.net/anime/{str(meta['mal_id'])}"

        return description

    async def edit_name(self, meta: Dict[str, Any]):
        mtv_name = meta["uuid"]
        if meta["source"].lower().replace("-", "") in mtv_name.replace("-", "").lower():
            if not meta["is_dir"]:
                mtv_name = Path(mtv_name).stem
        else:
            mtv_name = meta["name"]
            if meta.get("type") in ("WEBDL", "WEBRIP", "ENCODE") and "DD" in meta["audio"]:
                mtv_name = mtv_name.replace(meta["audio"], meta["audio"].replace(" ", "", 1))
            mtv_name = mtv_name.replace(meta.get("aka", ""), "")
            if (
                meta["category"] == "TV"
                and meta.get("tv_pack", 0) == 0
                and meta.get("episode_title_storage", "").strip() != ""
                and meta["episode"].strip() != ""
            ):
                mtv_name = mtv_name.replace(meta["episode"], f"{meta['episode']} {meta['episode_title_storage']}")
            if "DD+" in meta.get("audio", "") and "DDP" in meta["uuid"]:
                mtv_name = mtv_name.replace("DD+", "DDP")
            mtv_name = mtv_name.replace("Dubbed", "").replace("Dual-Audio", "DUAL")
        if meta["tag"] == "":
            mtv_name = f"{mtv_name}-NoGrp"
        mtv_name = " ".join(mtv_name.split())
        mtv_name = re.sub(r"[^0-9a-zA-ZÀ-ÿ. &+'\-\[\]]+", "", mtv_name)
        mtv_name = mtv_name.replace(" ", ".").replace("..", ".")
        return mtv_name

    async def get_res_id(self, resolution):
        resolution_id = {
            "8640p": "0",
            "4320p": "4000",
            "2160p": "2160",
            "1440p": "1440",
            "1080p": "1080",
            "1080i": "1080",
            "720p": "720",
            "576p": "0",
            "576i": "0",
            "480p": "480",
            "480i": "480",
        }.get(resolution, "10")
        return resolution_id

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        if meta["category"] == "MOVIE":
            return 2 if meta["sd"] == 1 else 1
        if meta["category"] == "TV":
            if meta["tv_pack"] == 1:
                return 6 if meta["sd"] == 1 else 5
            else:
                return 4 if meta["sd"] == 1 else 3

    async def get_source_id(self, meta: Dict[str, Any]) -> str:
        if meta["is_disc"] == "DVD":
            return "1"
        elif meta["is_disc"] == "BDMV" or meta["type"] == "REMUX":
            return "7"
        else:
            type_id = {
                "DISC": "1",
                "WEBDL": "9",
                "WEBRIP": "10",
                "HDTV": "1",
                "SDTV": "2",
                "TVRIP": "3",
                "DVD": "4",
                "DVDRIP": "5",
                "BDRIP": "8",
                "VHS": "6",
                "MIXED": "11",
                "Unknown": "12",
                "ENCODE": "7",
            }.get(meta["type"], "0")
        return type_id

    async def get_origin_id(self, meta: Dict[str, Any]) -> str:
        if meta["personalrelease"]:
            return "4"
        elif meta["scene"]:
            return "2"
        else:
            return "3"

    async def get_tags(self, meta):
        tags = []
        tags.extend([x.strip(", ").lower().replace(" ", ".") for x in meta["genres"].split(",")])
        tags.append(meta["resolution"].lower())
        if meta["sd"] == 1:
            tags.append("sd")
        elif meta["resolution"] in ["2160p", "4320p"]:
            tags.append("uhd")
        else:
            tags.append("hd")
        if str(meta["service_longname"]) != "":
            tags.append(f"{meta['service_longname'].lower().replace(' ', '.')}.source")
        for each in ["remux", "WEB.DL", "WEBRip", "HDTV", "BluRay", "DVD", "HDDVD"]:
            if (each.lower().replace(".", "") in meta["type"].lower()) or (each.lower().replace("-", "") in meta["source"]):
                tags.append(each)

        if meta["category"] == "TV":
            if meta.get("tv_pack", 0) == 0:
                tags.extend(["episode.release", "sd.episode" if meta["sd"] == 1 else "hd.episode"])
            else:
                tags.append("sd.season" if meta["sd"] == 1 else "hd.season")

        if meta["category"] == "MOVIE":
            tags.append("sd.movie" if meta["sd"] == 1 else "hd.movie")

        audio_tag = ""
        for each in ["dd", "ddp", "aac", "truehd", "mp3", "mp2", "dts", "dts.hd", "dts.x"]:
            if each in meta["audio"].replace("+", "p").replace("-", ".").replace(":", ".").replace(" ", ".").lower():
                audio_tag = f"{each}.audio"
        tags.append(audio_tag)
        if "atmos" in meta["audio"].lower():
            tags.append("atmos.audio")

        tags.append(meta.get("video_codec").replace("AVC", "h264").replace("HEVC", "h265").replace("-", ""))

        if meta["tag"] != "":
            tags.append(f"{meta['tag'][1:].replace(' ', '.')}.release")
        else:
            tags.append("NOGRP.release")

        if meta["scene"]:
            tags.append("scene.group.release")
        else:
            tags.append("p2p.group.release")

        mihandle = MediaInfoHandler()
        mi = mihandle._cache.get(meta["uuid"])
        if meta.get("is_disc", "") != "BDMV":
            if any(track.get("@type", "") == "Text" for track in mi["media"]["track"]):
                tags.append("subtitles")
        else:
            if len(meta["bdinfo"]["subtitles"]) >= 1:
                tags.append("subtitles")

        tags = " ".join(tags)
        return tags

    async def validate_credentials(self, meta):
        cookiefile = Path(meta["base_dir"]) / "data" / "cookies" / "MTV.pkl"
        if not cookiefile.exists():
            await self.login(cookiefile)
        vcookie = await self.validate_cookies(meta, cookiefile)
        if not vcookie:
            console.print(
                "[red]Failed to validate cookies. Please confirm that the site is up and your username and password is valid."
            )
            recreate = Confirm.ask("Log in again and create new session?")
            if recreate:
                cookiefile.unlink(missing_ok=True)
                await self.login(cookiefile)
                vcookie = await self.validate_cookies(meta, cookiefile)
                return vcookie
            else:
                return False
        vapi = await self.validate_api()
        if not vapi:
            console.print("[red]Failed to validate API. Please confirm that the site is up and your API key is valid.")
        return True

    async def validate_api(self):
        url = self.search_url
        params = {"apikey": self.config["TRACKERS"][self.tracker]["api_key"].strip()}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(url, params=params)
                if not r.is_success:
                    if "unauthorized api key" in r.text.lower():
                        console.print("[red]Invalid API Key")
                    return False
                return True
        except Exception:
            return False

    async def validate_cookies(self, meta, cookiefile):
        url = "https://www.morethantv.me/index.php"
        if cookiefile.exists():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with aiofiles.open(cookiefile, "rb") as cf:
                    cookies = pickle.load(cf)
                    client.cookies.update(cookies)
                resp = await client.get(url=url)
                if meta["debug"]:
                    console.log("[cyan]Validate Cookies:")
                    console.log(client.cookies)
                    console.log(resp.url)
                return "Logout" in resp.text
        return False

    async def get_auth(self, cookiefile):
        url = "https://www.morethantv.me/index.php"
        if cookiefile.exists():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with aiofiles.open(cookiefile, "rb") as cf:
                    cookies = pickle.load(cf)
                    client.cookies.update(cookies)
                resp = await client.get(url=url)
                auth = resp.text.rsplit("authkey=", 1)[1][:32]
                return auth

    async def login(self, cookiefile):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            url = "https://www.morethantv.me/login"
            payload = {
                "username": self.config["TRACKERS"][self.tracker].get("username"),
                "password": self.config["TRACKERS"][self.tracker].get("password"),
                "keeploggedin": 1,
                "cinfo": "1920|1080|24|0",
                "submit": "login",
                "iplocked": 1,
            }
            res = await client.get("https://www.morethantv.me/login")
            token = res.text.rsplit('name="token" value="', 1)[1][:48]
            payload["token"] = token
            resp = await client.post(url=url, data=payload)

            if resp.url.endswith("twofactor/login"):
                otp_uri = self.config["TRACKERS"][self.tracker].get("otp_uri")
                if otp_uri:
                    import pyotp

                    mfa_code = pyotp.parse_uri(otp_uri).now()
                else:
                    mfa_code = console.input("[yellow]MTV 2FA Code: ")

                two_factor_payload = {
                    "token": resp.text.rsplit('name="token" value="', 1)[1][:48],
                    "code": mfa_code,
                    "submit": "login",
                }
                resp = await client.post(url="https://www.morethantv.me/twofactor/login", data=two_factor_payload)

            if "authkey=" in resp.text:
                console.print("[green]Successfully logged in to MTV")
                cookiefile.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(cookiefile, "wb") as cf:
                    await cf.write(pickle.dumps(client.cookies))
            else:
                console.print("[bold red]Something went wrong while trying to log into MTV")
                await asyncio.sleep(1)
                console.print(resp.url)
        return

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dupes = {}
        console.print("[yellow]Searching for existing torrents on site...")
        params = {"t": "search", "apikey": self.config["TRACKERS"][self.tracker]["api_key"].strip(), "q": ""}
        if meta["imdb_id"] not in ("0", "", None):
            params["imdbid"] = "tt" + meta["imdb_id"]
        elif meta["tmdb"] != "0":
            params["tmdbid"] = meta["tmdb"]
        elif meta["tvdb_id"] != 0:
            params["tvdbid"] = meta["tvdb_id"]
        else:
            params["q"] = meta["title"].replace(": ", " ").replace("’", "").replace("'", "")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                rr = await client.get(url=self.search_url, params=params)
                if rr.is_success:
                    response_xml = xml.etree.ElementTree.fromstring(rr.text)
                    for each in response_xml.find("channel").findall("item"):
                        result = each.find("title").text
                        try:
                            size = int(each.find("size").text)
                        except Exception:
                            size = 0
                        dupes[result] = size
                        # CvT: Flying blind, guessing size is can be found under 'size' , needed for size comparison
                else:
                    console.print("[red]Site Seems to be down or not responding to API")
                    console.print("[bold red] Possibility Uploadrr breaks support. Please report if issue repeats.")
        except Exception:
            console.print("[red]Unable to search for existing torrents on site. Most likely the site is down.")
            dupes.append("FAILED SEARCH")
            print(traceback.print_exc())
            await asyncio.sleep(5)

        return dupes

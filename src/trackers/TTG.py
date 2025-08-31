import asyncio
import os
import pickle
import re
from pathlib import Path
from urllib.parse import urlparse

import aiofiles
import httpx
from bs4 import BeautifulSoup
from rich.prompt import Confirm
from unidecode import unidecode

from src.exceptions import UploadException
from src.trackers.COMMON import COMMON
from src.utils.console import console
from src.utils.torrentman import edit_torrent


class TTG:
    def __init__(self, config):
        self.config = config
        self.type = "http"  # custom nexus?
        self.tracker = "TTG"
        self.source_flag = "TTG"
        self.domain = "https://totheglory.im"
        self.username = str(config["TRACKERS"]["TTG"].get("username", "")).strip()
        self.password = str(config["TRACKERS"]["TTG"].get("password", "")).strip()
        self.passid = str(config["TRACKERS"]["TTG"].get("login_question", "0")).strip()
        self.passan = str(config["TRACKERS"]["TTG"].get("login_answer", "")).strip()
        self.uid = str(config["TRACKERS"]["TTG"].get("user_id", "")).strip()
        self.passkey = config["TRACKERS"]["TTG"].get("annouce_key", "").strip()
        self.announce_url = f"{self.domain}/announce/{self.passkey}"
        self.banned_groups = [""]
        self.allowed_imghosts = []

    async def edit_name(self, meta):
        ttg_name = meta["name"]
        remove_list = ["Dubbed", "Dual-Audio"]
        for each in remove_list:
            ttg_name = ttg_name.replace(each, "")
        ttg_name = ttg_name.replace("PQ10", "HDR")
        ttg_name = ttg_name.replace(".", "{@}")
        return ttg_name

    async def get_type_id(self, meta):
        lang = meta.get("original_language", "UNKNOWN").upper()
        if meta["category"] == "MOVIE":
            # 51 = DVDRip
            if meta["resolution"].startswith("720"):
                type_id = 52  # 720p
            if meta["resolution"].startswith("1080"):
                type_id = 53  # 1080p/i
            if meta["is_disc"] == "BDMV":
                type_id = 54  # Blu-ray disc

        elif meta["category"] == "TV":
            if meta.get("tv_pack", 0) != 1:
                # TV Singles
                if meta["resolution"].startswith("720"):
                    type_id = 69  # 720p TV EU/US
                    if lang in ("ZH", "CN", "CMN"):
                        type_id = 76  # Chinese
                if meta["resolution"].startswith("1080"):
                    type_id = 70  # 1080 TV EU/US
                    if lang in ("ZH", "CN", "CMN"):
                        type_id = 75  # Chinese
                if lang in ("KR", "KO"):
                    type_id = 75  # Korean
                if lang in ("JA", "JP"):
                    type_id = 73  # Japanese
            else:
                # TV Packs
                type_id = 87  # EN/US
                if lang in ("KR", "KO"):
                    type_id = 99  # Korean
                if lang in ("JA", "JP"):
                    type_id = 88  # Japanese
                if lang in ("ZH", "CN", "CMN"):
                    type_id = 90  # Chinese

        if "documentary" in meta.get("genres", "").lower().replace(" ", "").replace("-", "") or "documentary" in meta.get(
            "keywords", ""
        ).lower().replace(" ", "").replace("-", ""):
            if meta["resolution"].startswith("720"):
                type_id = 62  # 720p
            if meta["resolution"].startswith("1080"):
                type_id = 63  # 1080
            if meta.get("is_disc", "") == "BDMV":
                type_id = 64  # BDMV

        if "animation" in meta.get("genres", "").lower().replace(" ", "").replace("-", "") or "animation" in meta.get(
            "keywords", ""
        ).lower().replace(" ", "").replace("-", ""):
            if meta.get("sd", 1) == 0:
                type_id = 58

        if meta["resolution"] in ("2160p"):
            type_id = 108
            if meta.get("is_disc", "") == "BDMV":
                type_id = 109

        # I guess complete packs?:
        # 103 = TV Shows KR
        # 101 = TV Shows JP
        # 60 = TV Shows
        return type_id

    async def get_anon(self, anon):
        if anon == 0 and not self.config["TRACKERS"][self.tracker].get("anon", False):
            return "no"
        return "yes"

    ###############################################################
    ######   STOP HERE UNLESS EXTRA MODIFICATION IS NEEDED   ######
    ###############################################################

    async def upload(self, meta):
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        await self.edit_desc(meta)
        manual_name = meta.get("manual_name")
        ttg_name = await self.edit_name(meta)

        base_dir = Path(meta["base_dir"])
        tmp_dir = base_dir / "tmp" / meta["uuid"]

        mi_file = "BD_SUMMARY_00.txt" if meta["bdinfo"] else "MEDIAINFO.txt"
        async with aiofiles.open(tmp_dir / mi_file, "r", encoding="utf-8") as mi_dump:
            mi_content = await mi_dump.read()

        async with aiofiles.open(tmp_dir / f"[{self.tracker}]DESCRIPTION.txt", "r", encoding="utf-8") as desc_file:
            ttg_desc = (await desc_file.read()).rstrip()

        torrent_path = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
        torrent_filename = unidecode(os.path.basename(meta["video"] if len(meta["filelist"]) == 1 else meta["path"]).replace(" ", "."))

        async with aiofiles.open(torrent_path, "rb") as torrent_file:
            torrent_data = await torrent_file.read()

        files = {
            "file": (f"{torrent_filename}.torrent", torrent_data, "application/x-bittorrent"),
            "nfo": ("torrent.nfo", mi_content.encode("utf-8")),
        }
        data = {
            "MAX_FILE_SIZE": "4000000",
            "team": "",
            "hr": "no",
            "name": manual_name or ttg_name,
            "type": await self.get_type_id(meta),
            "descr": ttg_desc,
            "anonymity": await self.get_anon(meta["anon"]),
            "nodistr": "no",
        }
        if int(meta["imdb_id"].replace("tt", "")) != 0:
            data["imdb_c"] = f"tt{meta.get('imdb_id', '').replace('tt', '')}"

        url = f"{self.domain}/takeupload.php"
        cookiefile = base_dir / "data" / "cookies" / "TTG.pkl"

        async with httpx.AsyncClient(cookies=await self._load_cookies(cookiefile)) as client:
            if meta["debug"]:
                console.print(url)
                console.print(data)
            else:
                response = await client.post(url, data=data, files=files)
                if response.url.startswith(f"{self.domain}/details.php?id="):
                    console.print(f"[green]Uploaded to: [yellow]{response.url}[/yellow][/green]")
                    id_match = re.search(r"(id=)(\d+)", urlparse(str(response.url)).query)
                    if id_match:
                        await self.download_new_torrent(id_match.group(2), torrent_path)
                else:
                    console.print(data)
                    console.print("\n\n")
                    console.print(response.text)
                    raise UploadException(
                        f"Upload to TTG Failed: result URL {response.url} ({response.status_code}) was not expected", "red"
                    )

    async def _load_cookies(self, cookiefile):
        if cookiefile.exists():
            async with aiofiles.open(cookiefile, "rb") as cf:
                return pickle.loads(await cf.read())
        return {}

    async def search_existing(self, meta):
        dupes = {}
        cookiefile = Path(meta["base_dir"]) / "data" / "cookies" / "TTG.pkl"

        imdb = f"imdb{meta['imdb_id'].replace('tt', '')}" if int(meta["imdb_id"].replace("tt", "")) != 0 else ""
        res_type = {"BDMV": f"{meta['resolution']} Blu-ray", "DVD": "DVD"}.get(meta.get("is_disc", ""), meta["resolution"])

        search_url = f"{self.domain}/browse.php?search_field={imdb} {res_type}"

        async with httpx.AsyncClient(cookies=await self._load_cookies(cookiefile)) as client:
            response = await client.get(search_url)
            await asyncio.sleep(0.5)
            soup = BeautifulSoup(response.text, "html.parser")
            for each in soup.find_all("a", href=True):
                if each["href"].startswith("/t/"):
                    release = re.search(r"(<b>)(<font.*>)?(.*)<br", str(each))
                    if release:
                        dupes[release.group(3)] = 0  # CVT NOTE: No TTG Access so size is made to be 0 as I dont know the param to grab

        return dupes

    async def validate_credentials(self, meta):
        cookiefile = Path(meta["base_dir"]) / "data" / "cookies" / "TTG.pkl"
        if not cookiefile.exists():
            await self.login(cookiefile)
        vcookie = await self.validate_cookies(meta, cookiefile)
        if not vcookie:
            console.print("[red]Failed to validate cookies. Please confirm that the site is up and your passkey is valid.")
            recreate = Confirm.ask("Log in again and create new session?")
            if recreate:
                if cookiefile.exists():
                    cookiefile.unlink()
                await self.login(cookiefile)
                return await self.validate_cookies(meta, cookiefile)
            return False
        return True

    async def validate_cookies(self, meta, cookiefile):
        url = self.domain
        cookies = await self._load_cookies(cookiefile)
        if not cookies:
            return False

        async with httpx.AsyncClient(cookies=cookies) as client:
            response = await client.get(url)
            if meta["debug"]:
                console.print("[cyan]Cookies:")
                console.print(cookies)
                console.print(response.url)
            return '<a href="/logout.php">Logout</a>' in response.text

    async def login(self, cookiefile):
        url = f"{self.domain}/takelogin.php"
        data = {"username": self.username, "password": self.password, "passid": self.passid, "passan": self.passan}

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data)
            await asyncio.sleep(0.5)

            if str(response.url).endswith("2fa.php"):
                soup = BeautifulSoup(response.text, "html.parser")
                auth_token = soup.find("input", {"name": "authenticity_token"}).get("value")
                two_factor_data = {"otp": console.input("[yellow]TTG 2FA Code: "), "authenticity_token": auth_token, "uid": self.uid}
                two_factor_url = f"{self.domain}/take2fa.php"
                response = await client.post(two_factor_url, data=two_factor_data)
                await asyncio.sleep(0.5)

            if str(response.url).endswith("my.php"):
                console.print("[green]Successfully logged into TTG")
                async with aiofiles.open(cookiefile, "wb") as cf:
                    await cf.write(pickle.dumps(client.cookies))
            else:
                console.print("[bold red]Something went wrong")
                await asyncio.sleep(1)
                console.print(response.text)
                console.print(response.url)

    async def edit_desc(self, meta):
        base_dir = Path(meta["base_dir"])
        tmp_dir = base_dir / "tmp" / meta["uuid"]

        async with aiofiles.open(tmp_dir / "DESCRIPTION.txt", "r", encoding="utf-8") as f:
            base = await f.read()

        async with aiofiles.open(tmp_dir / f"[{self.tracker}]DESCRIPTION.txt", "w", encoding="utf-8") as descfile:
            from src.bbcode import BBCODE

            common = COMMON(config=self.config)

            if int(meta.get("imdb_id", "0").replace("tt", "")) != 0:
                ptgen = await common.ptgen(meta)
                if ptgen.strip():
                    await descfile.write(ptgen)

            if meta["type"] == "WEBDL" and meta.get("service_longname", "") and not meta.get("description"):
                await descfile.write(
                    f"[center][b][color=#ff00ff][size=3]{meta['service_longname']}的无损REMUX片源，没有转码/This release is sourced from {meta['service_longname']} and is not transcoded, just remuxed from the direct {meta['service_longname']} stream[/size][/color][/b][/center]"
                )

            bbcode = BBCODE()
            if meta.get("discs", []):
                discs = meta["discs"]
                for each in discs:
                    if each["type"] == "BDMV":
                        await descfile.write(f"[quote={each.get('name', 'BDINFO')}]{each['summary']}[/quote]\n\n")
                    elif each["type"] == "DVD":
                        await descfile.write(
                            f"{each['name']}:\n[quote={os.path.basename(each['vob'])}][{each['vob_mi']}[/quote] [quote={os.path.basename(each['ifo'])}][{each['ifo_mi']}[/quote]\n\n"
                        )
            else:
                async with aiofiles.open(tmp_dir / "MEDIAINFO.txt", "r", encoding="utf-8") as mi:
                    await descfile.write(f"[quote=MediaInfo]{await mi.read()}[/quote]\n")

            desc = base
            for transform in [
                bbcode.convert_code_to_quote,
                bbcode.convert_spoiler_to_hide,
                lambda d: bbcode.convert_comparison_to_centered(d, 1000),
                lambda d: re.sub(r"(\[img=\d+)]", "[img]", d, flags=re.IGNORECASE),
            ]:
                desc = transform(desc)
            await descfile.write(desc)

            if self.config["DEFAULT"].get("add_trailer", False) and meta.get("category") == "MOVIE" and meta.get("youtube"):
                await descfile.write(f"[center][youtube]{meta['youtube']}[/youtube][/center]")

            img_size = self.config["DEFAULT"]["img_size"]
            desc = desc.replace("[img]", f"[img={img_size}]")
            await descfile.write(desc)
            # TODO use DESC _write_images
            images = meta["image_list"]
            if images:
                await descfile.write("\n[center]")
                for img in images[: int(meta["screens"])]:
                    await descfile.write(f"[url={img['web_url']}][img={img_size}]{img['raw_url']}[/img][/url]")
                await descfile.write("[/center]")

            use_global_sigs = self.config["DEFAULT"]["SIGNATURES"].get("use_global_sigs", False)
            signature, anon_signature = (
                (
                    self.config["DEFAULT"]["SIGNATURES"].get("global_sig", ""),
                    self.config["DEFAULT"]["SIGNATURES"].get("global_anon_sig", ""),
                )
                if use_global_sigs
                else (
                    self.config["TRACKERS"][self.tracker].get("signature", ""),
                    self.config["TRACKERS"][self.tracker].get("anon_signature", ""),
                )
            )
            if signature is None or anon_signature is None:
                console.print("[bold red]WARN[/red]: Signatures missing in config.")

            sig = anon_signature if meta["anon"] != 0 or self.config["TEAS"][self.tracker].get("anon") else signature
            if sig:
                await descfile.write(f"\n{sig}")

    async def download_new_torrent(self, id, torrent_path):
        download_url = f"{self.domain}/dl/{id}/{self.passkey}"
        async with httpx.AsyncClient() as client:
            response = await client.get(download_url)
            if response.status_code == 200:
                async with aiofiles.open(torrent_path, "wb") as tor:
                    await tor.write(response.content)
            else:
                console.print("[red]There was an issue downloading the new .torrent from TTG")
                console.print(response.text)

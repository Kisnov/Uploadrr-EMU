import contextlib
import os
import platform
import re
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from rich.pretty import Pretty

from src.bbcode import BBCODE
from src.trackers.COMMON import COMMON
from src.utils.console import console
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent


class TMG:
    CATEGORIES = {
        "Anime": 5,
        "Movie4K": 13,
        "BD-25/BD-50": 13,
        "1080p_BluRay": 32,
        "720p_Bluray": 33,
        "DVD": 11,
        "WebDl": 17,
        "DVD-Rips": 14,
        "WebRip": 15,
        "VHS_TV": 20,
        "Video CDs": 21,
        "Dubbed Movies": 42,
        "TV/WEB Series": 28,
        "Documentary": 26,
        "TvEpisodesHd": 32,
        "Sports": 25,
        "Encoded_DVDs": 22,
        "Ai": 45,
    }

    TYPE = {"GOLD": 2, "SILVER": 1, "CLASSIC": 0}

    VIP_UPLOAD = {"on": 1, "0": 0}

    LANG_LIST = {
        "English": 1,
        "Hindi": 2,
        "Tamil": 3,
        "Malayalam": 4,
        "Telugu": 5,
        "Marathi": 6,
        "Punjabi": 7,
        "Kannada": 8,
        "Bengali": 9,
    }

    LANG_CODE_MAP = {
        ("en", "eng"): "English",
        ("hi", "hin"): "Hindi",
        ("ta", "tam"): "Tamil",
        ("ml", "mal"): "Malayalam",
        ("te", "tel"): "Telugu",
        ("mr", "mar"): "Marathi",
        ("pa", "pan"): "Punjabi",
        ("kn", "kan"): "Kannada",
        ("bn", "ben"): "Bengali",
    }

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.type = "api"
        self.tracker = "TMG"
        self.announce_url = "https://tmghub.org/announce.php"
        self.source_flag = "Tmghub.org"
        self.signature = self.config["TRACKERS"][self.tracker].get("signature", None)
        self.api_key = config["TRACKERS"]["TMG"].get("PID", "").strip()
        self.upload_url = "https://tmghub.org/uploadapi.php"
        self.banned_groups = ["DDH"]
        self.timeout = httpx.Timeout(60.0)

    async def get_language_id(self, meta: Dict[str, Any]) -> Optional[int]:
        lang_code = meta["original_language"]
        for codes, lang_name in self.LANG_CODE_MAP.items():
            if lang_code in codes:
                return self.LANG_LIST[lang_name]
        return None

    def get_basename(self, meta: Dict[str, Any]) -> str:
        path = next(iter(meta["filelist"]), meta["path"])
        return os.path.basename(path)

    async def get_cat_id(self, meta: Dict[str, Any]) -> int:
        basename = self.get_basename(meta)
        type_ = meta["type"]
        tv_types = ["VHS", "VHSrip", "TVRIP", "HDTV", "SDTV"]
        ai_file_names = ["AI", "AIUpscaled", "AIEnhanced", "Upscaled"]

        if meta.get("anime", 0):
            return self.CATEGORIES["Anime"]

        if meta["category"] == "MOVIE":
            if "Documentary" in meta["genres"]:
                return self.CATEGORIES["Documentary"]
            elif meta["is_disc"] in ("BDMV", "HDDVD") or (meta["type"] == "REMUX" and meta["source"] in ("BluRay", "HDDVD")):
                return self.CATEGORIES["1080p_BluRay"]
            elif meta["type"] == "ENCODE" and meta["source"] in ("BluRay", "HDDVD") and meta["resolution"] in ("1080p", "1080i"):
                return self.CATEGORIES["1080p_BluRay"]
            elif meta["type"] == "ENCODE" and meta["source"] in ("BluRay", "HDDVD") and meta["resolution"] == "720p":
                return self.CATEGORIES["720p_Bluray"]
            elif meta["is_disc"] == "DVD":
                return self.CATEGORIES["DVD"]
            elif meta["is_disc"] == "DVD" or (meta["type"] == "REMUX" and "DVD" in meta["source"]):
                return self.CATEGORIES["Encoded_DVDs"]
            elif meta["type"] == "ENCODE" and "DVD" in meta.get("source", ""):
                return self.CATEGORIES["DVD-Rips"]
            elif meta["type"] == "ENCODE" and "DVD" in meta.get("manual_source", ""):
                return self.CATEGORIES["DVD-Rips"]
            elif type_.upper().startswith("WEBRIP"):
                return self.CATEGORIES["WebRip"]
            elif type_.upper().startswith("WEBDL"):
                return self.CATEGORIES["WebDl"]
            elif "WEB" in type_:
                return self.CATEGORIES["WebRip"]
            elif meta["type"] == "HDTV":
                return self.CATEGORIES["VHS_TV"]
            elif any(vt.lower() in basename.lower() for vt in tv_types):
                console.print("[yellow]Detected TVrip/VHS by filename (edit after upload if incorrect)[/yellow]")
                return self.CATEGORIES["VHS_TV"]
            elif any(ai.lower() in basename.lower() for ai in ai_file_names):
                console.print("[yellow]Detected AI upscale/encode by filename (edit after upload if incorrect)[/yellow]")
                return self.CATEGORIES["Ai"]

        elif meta["category"] == "TV":
            return self.CATEGORIES["TV/WEB Series"]

        raise NotImplementedError("Failed to determine TMG category!")

    async def get_torrent_type(self, meta: Dict[str, Any]) -> int:
        if meta.get("original_language") == "ml":
            file_size = int(meta["mediainfo"]["media"]["track"][0]["FileSize"])
            if 3221225472 < file_size <= 7516192768:
                console.print("[green]File >3GB [white]→ [#C0C0C0]Silver")
                return self.TYPE["SILVER"]
            elif file_size > 7516192768:
                console.print("[green]File >7GB [white]→ [#FFD700]GOLD")
                return self.TYPE["GOLD"]
            else:
                console.print("[green]File <3GB [white]→ [#FF00FF]Classic")
                return self.TYPE["CLASSIC"]
        else:
            console.print("[green]Non-Malayalam [white]→ [#FFD700]GOLD")
            return self.TYPE["GOLD"]

    async def edit_desc(self, meta: Dict[str, Any]) -> None:
        common = COMMON(config=self.config)
        await common.unit3d_edit_desc(meta, self.tracker, comparison=False)

        tmp_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        desc_path = tmp_path / "[TMG]DESCRIPTION.txt"
        base_desc = desc_path.read_text(encoding="utf-8")
        final_desc = []

        if int(meta.get("imdb_id", "0")) != 0:
            if "ptgen" in meta and "format" in meta["ptgen"]:
                ptgen = meta["ptgen"]["format"]
            else:
                degen = DescGenerator(self.config, meta["base_dir"])
                ptgen = await degen.ptgen(meta)
            if ptgen and ptgen.strip():
                if "[/img]" in ptgen:
                    ptgen = ptgen.split("[/img]", 1)[1]
                ptgen = f"[img]{meta.get('imdb_info', {}).get('cover', meta.get('cover', ''))}[/img]{ptgen}"
                final_desc.append(ptgen)

        if meta.get("type") == "WEBDL" and meta.get("service_longname") and not meta.get("description"):
            final_desc.append(
                f"[center][b][color=#ff00ff][size=3]{meta['service_longname']} sourced, not transcoded[/size][/color][/b][/center]"
            )

        final_desc.append(base_desc)
        lines = []
        discs = meta.get("discs")
        if discs:
            for each in discs:
                disc_type = each.get("type")
                if disc_type == "BDMV":
                    lines.append(f"[quote={each.get('name', 'BDINFO')}]{each['summary']}[/quote]\n")
                elif disc_type == "DVD":
                    lines.append(f"{each['name']}:\n")
                    lines.append(
                        f"[quote={os.path.basename(each['vob'])}]{each['vob_mi']}[/quote] "
                        f"[quote={os.path.basename(each['ifo'])}]{each['ifo_mi']}[/quote]\n"
                    )
        else:
            mi_path = tmp_path / "MEDIAINFO.txt"
            try:
                mi = mi_path.read_text(encoding="utf-8")
                lines.append(f"[quote=MediaInfo]{mi}[/quote]\n")
            except Exception as e:
                console.print(f"[red]Could not read MediaInfo: {e}[/red]")
        final_desc.append("\n".join(lines))

        full_desc = "\n\n".join(final_desc)

        bbcode = BBCODE()
        full_desc = bbcode.convert_code_to_quote(full_desc)
        full_desc = bbcode.convert_spoiler_to_hide(full_desc)
        full_desc = re.sub(r"(\[img=\d+)]", "[img]", full_desc, flags=re.IGNORECASE)

        with desc_path.open("w", encoding="utf-8") as descfile:
            descfile.write(full_desc)

    async def upload(self, meta: Dict[str, Any]) -> bool:
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        gold = await self.get_torrent_type(meta)
        lang = await self.get_language_id(meta)
        cat = await self.get_cat_id(meta)
        await self.edit_desc(meta)

        anon = 1 if (meta.get("anon", 0) or self.config["TRACKERS"][self.tracker].get("anon", False)) else 0
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        youtube_video_id = meta.get("youtube", "no_id")

        data = {
            "api_key": self.api_key,
            "filename": meta["uuid"],
            "imdb": meta.get("imdb_id", "").replace("tt", ""),
            "gold": gold,
            "category": cat,
            "team": 0,
            "language": lang,
            "anonymous": anon,
            "youtube_video": youtube_video_id,
        }

        if meta.get("personalrelease", False):
            console.print("[yellow]Selected as VIP torrent[/yellow]")
            data["vip_torrent"] = "on"

        headers = {"User-Agent": f"Uploadrr ({platform.system()} {platform.release()})"}

        if meta.get("debug", False):
            console.print("[magenta]DATA 2 SEND[/magenta]:")
            console.print(Pretty(data))
            await edit_torrent(
                meta,
                self.tracker,
                self.source_flag,
                f"{self.announce_url}?pid={self.api_key}",
                "Created by Uploadrr for TmGHUb - DEBUG MODE",
            )
            return "DEBUG"

        async with httpx.AsyncClient(headers=headers, timeout=self.timeout) as client:
            files = {}
            with contextlib.ExitStack() as stack:
                desc_content = stack.enter_context(open(tmp_dir / f"[{self.tracker}]DESCRIPTION.txt", "r", encoding="utf-8"))
                data["info"] = desc_content.read()

                torrent_path = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
                torrent_file = stack.enter_context(open(torrent_path, "rb"))
                files["torrent"] = (await self.edit_name(meta) + ".torrent", torrent_file, "application/x-bittorrent")

                try:
                    response = await client.post(self.upload_url, files=files, data=data)
                    response.raise_for_status()
                    if 200 <= response.status_code < 300:
                        try:
                            response_json = response.json()
                        except ValueError:
                            console.print(f"[red]Invalid JSON response:[/red] {response.text[:200]}")
                            return False

                        info_message = None
                        view_link = None

                        if isinstance(response_json, dict):
                            if "data" in response_json:
                                data = response_json["data"]
                                if isinstance(data, dict):
                                    info_message = data.get("info")
                                    view_link = data.get("view")

                            elif "info" in response_json:
                                info_message = response_json["info"]

                        if info_message and "success" in info_message.lower():
                            source = f"Created by Uploadrr for {view_link}" if view_link else "Created by Uploadrr for TmGHUb"

                            await edit_torrent(meta, self.tracker, self.source_flag, f"{self.announce_url}?pid={self.api_key}", source)
                            console.print("[bold green]Torrent uploaded successfully!")
                            return True
                        else:
                            await edit_torrent(
                                meta,
                                self.tracker,
                                self.source_flag,
                                f"{self.announce_url}?pid={self.api_key}",
                                "Created by Uploadrr for TmGHUb",
                            )
                            console.print("[bold yellow]Unexpected confirmation format. Please double-check upload.")
                            console.print(Pretty(response_json))
                            return False
                    else:
                        console.print(f"[red]Upload failed: HTTP {response.status_code}[/red]")
                        return False
                except httpx.HTTPStatusError as http_err:
                    console.print(f"[red]HTTP error during upload: {http_err}[/red]")
                    return False
                except Exception:
                    console.print_exception()
                    return False

    async def edit_name(self, meta: Dict[str, Any]) -> str:
        path = Path(meta["path"])
        return path.stem if path.is_file() else path.name

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        # TODO ADD METHOD
        dupes: Dict[str, Any] = {}
        return dupes

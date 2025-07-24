import asyncio
import functools
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import aiofiles
import anitopy
import requests
from guessit import guessit
from pymediainfo import MediaInfo

from src.databases import AniListHandler, IMDbHandler, TMDbHandler, is_scene, search_tvmaze
from src.discparse import DiscParse
from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.utils.console import console

from .utils.loads import get_distributor, get_region, load_json
from .utils.meta import find_largest_clpi, get_dvd_size, get_video_codec


class VideoProcessor:
    """
    Handles video-specific analysis and metadata enrichment.
    Processes discs, integrates APIs, finalizes metadata.
    """

    _regions: Optional[Dict] = None
    _services: Optional[Dict] = None

    def __init__(self, config: Dict, mihandle: MediaInfoHandler, base_dir: Path):
        self.config = config
        self.mihandle = mihandle
        self.base_dir = base_dir
        self.TMDb = TMDbHandler(self.config)
        self.IMDb = IMDbHandler()

    async def process_video(self, meta: Dict) -> Dict:
        """Orchestrate video metadata processing."""
        pm = ProgressManager.get_instance()
        if pm:
            pm.update("Processing video metadata...")

        meta["is_disc"], videoloc, bdinfo, meta["discs"] = await self._get_disc(meta)
        meta.update(bdinfo=bdinfo, video=None, is_sports=False, mediainfo=None)

        meta = await self._process_disc_type(meta, videoloc)
        meta = await self._finalize_metadata(meta)

        if pm:
            pm.update("Video processing complete")

        return meta

    async def _get_disc(self, meta: Dict) -> Tuple[bool, Optional[Path], Optional[Dict], List[Dict]]:
        """Detect and parse discs."""
        is_disc = None
        videoloc = meta["path"]
        bdinfo = None
        discs = []
        parse = DiscParse()
        for path, directories, files in os.walk(meta["path"]):
            for each in directories:
                if each.upper() == "BDMV":  # BDMVs
                    is_disc = "BDMV"
                    disc = {"path": f"{path}/{each}", "name": str(Path(path).name), "type": "BDMV", "summary": "", "bdinfo": ""}
                    discs.append(disc)
                elif each == "VIDEO_TS":  # DVDs
                    is_disc = "DVD"
                    disc = {
                        "path": f"{path}/{each}",
                        "name": str(Path(path).name),
                        "type": "DVD",
                        "vob_mi": "",
                        "ifo_mi": "",
                        "main_set": [],
                        "size": "",
                    }
                    discs.append(disc)
                elif each == "HVDVD_TS":
                    is_disc = "HDDVD"
                    disc = {"path": f"{path}/{each}", "name": str(Path(path).name), "type": "HDDVD", "evo_mi": "", "largest_evo": ""}
                    discs.append(disc)
        if is_disc == "BDMV":
            if meta.get("edit", False) is False:
                discs, bdinfo = await parse.get_bdinfo(discs, meta["uuid"], self.base_dir, meta.get("discs", []))
            else:
                discs, bdinfo = await parse.get_bdinfo(meta["discs"], meta["uuid"], self.base_dir, meta["discs"])
        elif is_disc == "DVD":
            discs = await parse.get_dvdinfo(discs)
            mediainfo_path = self.base_dir / "tmp" / f"{meta['uuid']}/MEDIAINFO.txt"
            async with aiofiles.open(mediainfo_path, "w", encoding="utf-8") as export:
                await export.write(discs[0]["ifo_mi"])
        elif is_disc == "HDDVD":
            discs = await parse.get_hddvd_info(discs)
            mediainfo_path = self.base_dir / "tmp" / f"{meta['uuid']}/MEDIAINFO.txt"
            async with aiofiles.open(mediainfo_path, "w", encoding="utf-8") as export:
                await export.write(discs[0]["evo_mi"])
        discs = sorted(discs, key=lambda d: d["name"])
        return is_disc, videoloc, bdinfo, discs

    async def _process_disc_type(self, meta: Dict, videoloc: Optional[Path]) -> Dict:
        """Handle video processing based on disc type and set initial metadata."""
        guess = None
        manual_year = meta.get("manual_year")
        if meta["is_disc"] == "BDMV":
            video, meta["scene"], meta["imdb"] = await is_scene(meta["path"], meta.get("imdb"))
            meta["video"] = video
            clpi_path = await find_largest_clpi(meta["path"])
            if not meta.get("edit", False):
                await self.mihandle.exportInfo(media_path=str(clpi_path), is_dir=False, uuid=meta["uuid"], export_text=True, bdmv=True)

            try:
                title = meta["discs"][0]["bdinfo"]["title"]
                guess_name = re.sub(r"Blu-ray™|-\s*|Vol\.\s*\d+", "", title).strip()
                opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
                filename = self._cached_guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), opts)["title"]
                meta["search_year"] = manual_year if manual_year > 0 else self._cached_guessit(meta["bdinfo"]["title"]).get("year", "")
            except Exception:
                guess_name = meta["bdinfo"]["label"].replace("-", " ")
                opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
                filename = self._cached_guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), opts)["title"]
                meta["search_year"] = manual_year if manual_year > 0 else self._cached_guessit(meta["bdinfo"]["label"]).get("year", "")
            meta["resolution"] = self.mihandle._map_resolution(
                meta["bdinfo"]["video"][0]["res"], self._cached_guessit(video), "OTHER", "p", "OTHER", 0
            )
            meta["filename"] = filename.split(" AKA ")[0] if " AKA " in filename.replace(".", " ") else filename
            meta["type"] = self.get_type(video, meta["scene"], meta["is_disc"])
            meta["category"] = (
                meta.get("category")
                or self.get_cat(meta["video"], meta["is_disc"], meta.get("discs", []))
                or guess.get("type", "MOVIE").upper()
            )

        elif meta["is_disc"] == "DVD":
            disc_path = meta["discs"][0]["path"]
            video, meta["scene"], meta["imdb"] = await is_scene(meta["path"], meta.get("imdb"))
            guess_name = disc_path.replace("-", " ")
            opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
            meta["filename"] = self._cached_guessit(guess_name, opts)["title"]
            meta["search_year"] = manual_year if manual_year > 0 else self._cached_guessit(video).get("year", "")
            meta["video"] = video
            if not meta.get("edit", False):
                title_set = meta["discs"][0]["main_set"][0][:2]
                vob_path = f"{disc_path}/VTS_{title_set}_1.VOB"
                await self.mihandle.exportInfo(media_path=vob_path, is_dir=False, uuid=meta["uuid"], export_text=True)
            meta["dvd_size"] = get_dvd_size(meta["discs"])
            meta["type"] = self.get_type(video, meta["scene"], meta["is_disc"])
            meta["category"] = (
                meta.get("category")
                or self.get_cat(meta["video"], meta["is_disc"], meta.get("discs", []))
                or guess.get("type", "MOVIE").upper()
            )

        elif meta["is_disc"] == "HDDVD":
            video, meta["scene"], meta["imdb"] = await is_scene(meta["path"], meta.get("imdb"))
            guess_name = meta["discs"][0]["path"].replace("-", "")
            guess_name = disc_path.replace("-", " ")
            opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
            meta["filename"] = self._cached_guessit(guess_name, opts)["title"]
            meta["search_year"] = self._cached_guessit(meta["discs"][0]["path"]).get("year", "")
            meta["video"] = video
            if not meta.get("edit", False):
                await self.mihandle.exportInfo(media_path=video, is_dir=False, uuid=meta["uuid"], export_text=False)
            meta["type"] = self.get_type(video, meta["scene"], meta["is_disc"])
            meta["category"] = (
                meta.get("category")
                or self.get_cat(meta["video"], meta["is_disc"], meta.get("discs", []))
                or guess.get("type", "MOVIE").upper()
            )

        else:  # Non-disc case
            videopath, filelist = self.get_video(videoloc, meta.get("mode", "discord"), meta.get("filelist", []))
            video, meta["scene"], meta["imdb"] = await is_scene(videopath, meta.get("imdb"))
            meta["video"] = str(videopath)
            guess = guess or self._cached_guessit(meta["video"])
            meta["category"] = (
                meta.get("category")
                or self.get_cat(meta["video"], meta["is_disc"], meta.get("discs", []))
                or guess.get("type", "MOVIE").upper()
            )
            if meta["category"] == "TV":
                guess_name = meta["uuid"].replace("-", " ")
            else:
                guess_name = Path(video).name.replace("-", " ")
            opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
            guess_result = self._cached_guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), opts)
            default_result = guessit(re.sub("[^0-9a-zA-Z]+", " ", guess_name), {"excludes": ["country", "language"]})
            meta["filename"] = guess_result.get("title", default_result.get("title", ""))
            meta["search_year"] = manual_year if manual_year > 0 else self._cached_guessit(video).get("year", "")
            if not meta.get("edit", False):
                await self.mihandle.exportInfo(
                    media_path=videopath, is_dir=meta.get("is_dir", False), uuid=meta["uuid"], export_text=True
                )
            meta["type"] = self.get_type(video, meta["scene"], meta["is_disc"])
        if self.is_sports(meta["uuid"]):
            meta["is_sports"] = True
            meta["category"] = "SPORTS"
        return meta

    def get_video(
        self, videoloc: Union[str, Path], mode: str, meta_or_filelist: Union[Dict, List[str]] = None
    ) -> Tuple[Union[Path, None], List[Path]]:
        """Retrieve the primary video file and list of video files from meta or filelist."""
        video_extensions = {".mkv", ".mp4", ".ts"}
        bonus_keywords = ("bonus", "extra", "bloopers", "blooper", "intro", "trailer", "sample", "0")

        def is_bonus_file(filename: Union[str, Path]) -> bool:
            name = Path(filename).stem.lower()
            return any(name.startswith(k) or name.endswith(k) for k in bonus_keywords)

        # Handle meta (dict) or filelist (list)
        video_files = []
        if isinstance(meta_or_filelist, dict):
            video_files = [Path(f) for f in meta_or_filelist.get("video_files", [])]  # Convert strings to Path
        elif isinstance(meta_or_filelist, list):
            video_files = [Path(f) for f in meta_or_filelist if Path(f).suffix.lower() in video_extensions]  # Filter valid videos

        # Fallback to scanning videoloc if no video files
        if not video_files:
            videoloc = Path(videoloc).resolve()
            if videoloc.is_dir():
                video_files = [
                    f
                    for f in videoloc.iterdir()
                    if f.is_file() and f.suffix.lower() in video_extensions and not f.name.startswith("._")
                ]
            elif videoloc.suffix.lower() in video_extensions and not videoloc.name.startswith("._"):
                video_files = [videoloc]

        if not video_files:
            console.print(f"[red]No video files found in {videoloc}")
            if mode == "cli":
                exit()
            return None, []

        # Sort to prioritize non-bonus files
        video_files = sorted(video_files, key=is_bonus_file)
        primary_video = video_files[0]
        return primary_video, video_files

    async def _assign_common_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Assign common metadata attributes"""

        async def fetch_resolution():
            return self.mihandle.get_resolution(self._cached_guessit(meta["video"]), meta["uuid"])

        async def fetch_audio():
            return self.mihandle.get_audio(meta["uuid"], meta, meta.get("bdinfo"))

        async def fetch_3d():
            return self.is_3d(meta.get("bdinfo"))

        async def fetch_source():
            return self.get_source(meta["type"], meta["video"], meta["path"], meta["is_disc"], meta)

        async def fetch_uhd():
            opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
            guess = self._cached_guessit(meta["path"], opts)
            return self.get_uhd(meta["type"], guess, meta["resolution"], meta["path"])
            # return self.get_uhd(meta['type'], self._cached_guessit(meta['path']), meta['resolution'], meta['path'])

        async def fetch_hdr():
            return self.mihandle.get_hdr(meta["uuid"], meta.get("bdinfo"))

        async def fetch_video_encode():
            if meta["is_disc"] != "BDMV":
                return self.mihandle.get_video_encode(meta["uuid"], meta["type"], meta.get("bdinfo"))
            return None, None, False, "0"

        results = await asyncio.gather(
            fetch_resolution(), fetch_audio(), fetch_3d(), fetch_source(), fetch_uhd(), fetch_hdr(), fetch_video_encode()
        )

        meta["resolution"] = meta.get("resolution") or results[0]
        meta["sd"] = 1 if meta["resolution"] in ("480i", "480p", "576i", "576p", "540p") else 0
        meta["lang"], meta["audio"], meta["channels"], meta["has_commentary"], meta["is_silent"] = results[1]
        meta["3D"] = results[2]
        meta["source"], meta["type"] = results[3]
        meta["uhd"] = results[4]
        meta["hdr"] = results[5]

        if meta["is_disc"]:
            meta["distributor"], meta["distributor_id"] = get_distributor(meta["distributor"])
            if meta["is_disc"] == "BDMV":
                meta["region"], meta["region_id"] = get_region(meta["region"])
                # meta["region"] = self.get_region(meta.get("bdinfo"), meta.get("region")) # TODO Test + ERASE
                meta["video_codec"] = get_video_codec(meta["bdinfo"])
        else:
            meta["video_encode"], meta["video_codec"], meta["has_encode_settings"], meta["bit_depth"] = results[6]

        return meta

    async def _finalize_metadata(self, meta: Dict) -> Dict:
        """Finalize metadata based on category and additional processing."""
        if meta.get("search_year") in ("", None):
            meta["search_year"] = await self.mihandle.get_search_year(meta["uuid"], meta)
        if meta["category"] in ("TV", "MOVIE"):
            meta["tmdb"] = meta.get("tmdb_manual", meta.get("tmdb"))
            if not meta.get("imdb") and meta.get("imdb_id"):
                meta["imdb"] = f"tt{meta['imdb_id']}"
            if not meta.get("tmdb") and not meta.get("imdb"):
                meta["category"], meta["tmdb"], meta["imdb"] = self.mihandle.get_tmdb_imdb_from_mediainfo(
                    meta["uuid"], meta["category"], meta["is_disc"], meta["tmdb"], meta["imdb"]
                )
            if not meta.get("tmdb") and not meta.get("imdb"):
                meta = await self.TMDb.get_tmdb_id(meta["filename"], meta["search_year"], meta, meta["category"], meta["filename"])
            elif meta.get("imdb") and not meta.get("tmdb_manual"):
                meta["imdb_id"] = str(meta["imdb"]).replace("tt", "")
                meta = await self.TMDb.get_tmdb_from_imdb(meta, meta["filename"])
            else:
                meta["tmdb_manual"] = meta.get("tmdb")

            if meta.get("unattended") and (meta.get("tmdb") is None or int(meta["tmdb"]) == 0):
                meta["tmdb_not_found"] = True
                console.print(f"[red]Unable to find TMDb match for {Path(meta['uuid']).stem}")
                return meta

            meta = await (self.IMDb.imdb_other_meta(meta) if int(meta["tmdb"]) == 0 else self.TMDb.tmdb_other_meta(meta))
            meta["tvmaze_id"], meta["imdb_id"], meta["tvdb_id"] = await search_tvmaze(
                meta["filename"], meta["search_year"], meta.get("imdb_id", "0"), meta.get("tvdb_id", 0)
            )
            if not meta.get("imdb_id"):
                meta["imdb_id"] = await self.IMDb.search_imdb(meta["filename"], meta["search_year"])
            if not meta.get("imdb_info") and int(meta["imdb_id"]) != 0:
                meta["imdb_info"] = await self.IMDb.get_imdb_info(meta["imdb_id"], meta)

        meta["tag"] = meta.get("tag") or self.get_tag(meta["video"], meta)
        if meta["tag"] and not meta["tag"].startswith("-"):
            meta["tag"] = f"-{meta['tag']}"
        if meta["category"] == "TV":
            meta = await self.get_season_episode(meta["video"], meta)

        # Assign common metadata
        meta = await self._assign_common_metadata(meta)  # After TMDb for Dual/Dub/Silent

        # Additional cleanup
        meta = await self.tag_override(meta)
        if meta["tag"][1:].startswith(meta["channels"]):
            meta["tag"] = meta["tag"].replace(f"-{meta['channels']}", "")
        if meta.get("no_tag", False):
            meta["tag"] = ""
        if meta["type"] in ("WEBDL", "WEBRIP", "HDTV"):
            meta["service"], meta["service_longname"] = (
                self.get_service(meta["video"], meta.get("tag", ""), meta["audio"], meta["filename"])
                if meta.get("service") in (None, "")
                else (meta["service"], meta["service_longname"])
            )

        # meta['distributor'] = self.get_distributor(meta['distributor'])
        meta["edition"], meta["repack"], meta["cut"], meta["ratio"] = self.get_edition(
            meta.get("title", ""), meta["path"], meta.get("bdinfo"), meta["filelist"], meta.get("manual_edition")
        )
        if "REPACK" in meta.get("edition", ""):
            meta["repack"] = re.search(r"REPACK[\d]?", meta["edition"])[0]
            meta["edition"] = re.sub(r"REPACK[\d]?", "", meta["edition"]).strip().replace("  ", " ")

        meta["stream"] = self.stream_optimized(meta.get("stream", False))
        meta["anon"] = meta.get("anon", False)

        if meta["category"] == "SPORTS":
            meta["name"] = meta["title"] = Path(meta["video"]).stem if meta["scene"] else Path(meta["uuid"]).stem

        return meta

    """
    Is a sports release?
    """

    def is_sports(self, name):
        sports_pattern = (
            r"(?:EFL.*|.*mlb.*|.*formula1.*|.*nascar.*|.*nfl.*|.*wrc.*|.*wwe.*|"
            r".*fifa.*|.*boxing.*|.*rally.*|.*ufc.*|.*ppv.*|.*uefa.*|.*nhl.*|"
            r".*nba.*|.*motogp.*|.*moto2.*|.*moto3.*|.*gamenight.*|.*darksport.*|"
            r".*overtake.*)"
        )
        return re.search(sports_pattern, name, re.IGNORECASE) is not None

    """
    Get type, category, edition
    """

    def get_type(self, video, scene, is_disc):
        filename = os.path.basename(video).lower()
        if "remux" in filename:
            return "REMUX"
        elif any(word in filename for word in [" web ", ".web.", "web-dl", "webdl"]):
            return "WEBDL"
        elif "webrip" in filename:
            return "WEBRIP"
        elif "hdtv" in filename:
            return "HDTV"
        elif is_disc is not None:
            return "DISC"
        else:
            return "ENCODE"

    def get_cat(self, video, is_disc, discs):
        if is_disc and len(discs) > 2:
            return "TV"
        category = self._cached_guessit(video.replace("1.0", ""))["type"]
        if category.lower() == "movie":
            return "MOVIE"  # 1
        elif category.lower() in ("tv", "episode"):
            return "TV"  # 2
        else:
            return "MOVIE"

    def get_edition(self, title, video, bdinfo, filelist, manual_edition):
        def normalize_for_compare(s: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s)).strip().lower()

        def extract_canonical_editions(text: str, cuts_dict: Dict[str, str]) -> List[str]:
            if not text:
                return []
            normalized = normalize_for_compare(text)
            result = []
            for pattern, value in cuts_dict.items():
                if re.search(pattern, normalized, re.IGNORECASE):
                    result.append(value)
            return result

        try:
            video = video.lower().startswith("dc") and video.replace("dc", "", 1) or video
            opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
            guess = self._cached_guessit(video, opts)
            repack = ""
            cut = ""
            ratio = ""
            edition = ""

            # Cut Types
            CUTS = {
                re.compile(r"\bdirector'?s?\s*cut\b|\bdc\b", re.IGNORECASE): "Director's Cut",
                re.compile(r"\binternational\s*cut\b", re.IGNORECASE): "International Cut",
                re.compile(r"\bextended\b", re.IGNORECASE): "Extended",
                re.compile(r"\bspecial\s*edition\b", re.IGNORECASE): "Special Edition",
                re.compile(r"\bunrated\b", re.IGNORECASE): "Unrated",
                re.compile(r"\buncut\b", re.IGNORECASE): "Uncut",
                re.compile(r"\bsuper\s*duper\b", re.IGNORECASE): "Super Duper Cut",
            }

            # Handle AI/UPSCALE case
            if manual_edition and any(x in str(manual_edition).upper() for x in ["AI", "UPSCALE"]):
                edition = "AI UPSCALE"
            else:
                manual_str = " ".join(manual_edition) if isinstance(manual_edition, list) else str(manual_edition or "")
                manual_editions = extract_canonical_editions(manual_str, {p.pattern: v for p, v in CUTS.items()})

                if not manual_editions:
                    if bdinfo:
                        edition_raw = self._cached_guessit(bdinfo["label"], opts).get("edition", "")
                    else:
                        edition_raw = guess.get("edition", "")
                    detected_editions = (
                        extract_canonical_editions(edition_raw, {p.pattern: v for p, v in CUTS.items()}) if edition_raw else []
                    )
                else:
                    detected_editions = []

                seen = set()
                combined_editions = []
                for part in manual_editions + detected_editions:
                    key = normalize_for_compare(part)
                    if key not in seen:
                        seen.add(key)
                        combined_editions.append(part)
                edition = " ".join(combined_editions) if combined_editions else ""

            # Process video for additional metadata
            if len(filelist) == 1:
                video = os.path.basename(video)
            video_normalized = re.sub(r"[.\-_!]+", " ", video).strip()
            if title:
                title_normalized = re.sub(r"[.\-_!:]+", " ", title.lower()).strip()
                title_pattern = re.escape(title_normalized).replace(" ", r"\s*")
                video_normalized = re.sub(rf"^{title_pattern}\s*", "", video_normalized.lower(), 1)
            if tag := guess.get("release_group"):
                video_normalized = video_normalized.replace(tag.lower(), "")

            # Extract cut from video_normalized if not already in edition
            cut = next(
                (value for pattern, value in CUTS.items() if pattern.search(video_normalized) and value not in combined_editions), ""
            )

            # Extract ratio (compiled)
            RATIOS = {
                re.compile(r"\bimax\b", re.IGNORECASE): "IMAX",
                re.compile(r"\bopen\s*matte\b", re.IGNORECASE): "Open Matte",
                re.compile(r"\bopen\s*om\b", re.IGNORECASE): "Open Matte",
                re.compile(r"\bmar\b", re.IGNORECASE): "MAR",
            }
            ratio = next((value for pattern, value in RATIOS.items() if pattern.search(video_normalized)), "")

            # Extract repack (compiled, ordered priority)
            REPACK_PATTERNS = [
                (re.compile(r"\brepack3\b|\[repack3\]|v4", re.IGNORECASE), "REPACK3"),
                (re.compile(r"\brepack2\b|\[repack2\]|v3", re.IGNORECASE), "REPACK2"),
                (re.compile(r"\brepack\b|\[repack\]|v2", re.IGNORECASE), "REPACK"),
                (re.compile(r"\bproper\b", re.IGNORECASE), "PROPER"),
                (re.compile(r"\brerip\b", re.IGNORECASE), "RERIP"),
            ]
            for pattern, repack_type in REPACK_PATTERNS:
                if pattern.search(video_normalized):
                    repack = repack_type
                    break

            # Handle hybrid
            if re.search(r"\bhybrid\b", video_normalized, re.IGNORECASE) and not re.search(
                r"\bhybrid\b", title.lower(), re.IGNORECASE
            ):
                edition = f"Hybrid {edition}" if edition else "Hybrid"

            # Clean up edition
            edition = re.sub(r"(repack\d?|rerip|proper)", "", edition, flags=re.IGNORECASE).strip()
            if edition.lower() in ["internal", "limited", "retail"]:
                edition = ""

            return edition, repack, cut, ratio
        except Exception as e:
            console.print(f"[red]Error in get_edition: {e}[/red]")
            return "", "", "", ""

    def is_3d(self, bdinfo):
        if bdinfo is not None:
            if bdinfo["video"][0]["3d"] != "":
                return "3D"
            return ""

    def get_tag(self, video, meta):
        try:
            tag = self._cached_guessit(video)["release_group"]
            tag = f"-{tag}"
        except Exception:
            tag = ""

        if tag.startswith("-"):
            parts = tag[1:].split("-")
            tag = f"-{parts[-1]}"  # Keep only the last part

        if tag == "-":
            tag = ""
        if tag[1:].lower() in ["nogroup", "nogrp"]:
            tag = ""
        return tag

    async def tag_override(self, meta):
        tag_data = load_json(self.base_dir / "data/tags.json")
        for tag in tag_data:
            value = tag_data.get(tag)
            if value.get("in_name", "") == tag and tag in meta["path"]:
                meta["tag"] = f"-{tag}"
            if meta["tag"][1:] == tag:
                for key in value:
                    if key == "type":
                        if meta[key] == "ENCODE":
                            meta[key] = value.get(key)
                    elif key == "personalrelease":
                        meta[key] = bool(value.get(key, False))
                    elif key == "template":
                        meta["desc_template"] = value.get(key)
                    else:
                        meta[key] = value.get(key)
        return meta

    def get_source(self, type: str, video: str, path: str, is_disc: Optional[str], meta: dict):
        try:
            source = meta.get("manual_source")
            if not source:
                try:
                    opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
                    guess = self._cached_guessit(video, opts)
                    source = guess.get("source", self._cached_guessit(path, opts).get("source", "BluRay"))
                except Exception:
                    source = "BluRay"
            if source in ("Blu-ray", "Ultra HD Blu-ray", "BluRay", "BR") or is_disc == "BDMV":
                source = "Blu-ray" if type == "DISC" else "BluRay"

            elif source in ("DVD", "dvd") or is_disc == "DVD":
                system = ""
                try:
                    if is_disc == "DVD":
                        mediainfo = MediaInfo.parse(f"{meta['discs'][0]['path']}/VTS_{meta['discs'][0]['main_set'][0][:2]}_0.IFO")
                    else:
                        mediainfo = MediaInfo.parse(video)
                    for track in mediainfo.tracks:
                        if track.track_type == "Video":
                            system = track.standard or ""
                            break
                except Exception:
                    try:
                        other = self._cached_guessit(video).get("other", [])
                        if "PAL" in other:
                            system = "PAL"
                        elif "NTSC" in other:
                            system = "NTSC"
                    except Exception:
                        system = ""
                source = f"{system} DVD".strip() if type == "REMUX" else system

            elif source in ("Web", "WEB"):
                if type == "ENCODE":
                    type = "WEBRIP"

            elif source in ("HD-DVD", "HD DVD", "HDDVD"):
                source = "HD DVD" if is_disc == "HDDVD" else "HDDVD"

            if type in ("WEBDL", "WEBRIP"):
                source = "Web"
            if source == "Ultra HDTV":
                source = "UHDTV"
        except Exception as e:
            print(f"Error in get_source: {str(e)}")
            source = "BluRay"

        return source, type

    def stream_optimized(self, stream_opt):
        if stream_opt is True:
            stream = 1
        else:
            stream = 0
        return stream

    def get_uhd(self, type: str, guess: dict, resolution: str, path: str) -> str:
        try:
            source = guess.get("source", "")
            other = guess.get("other", "")

            if (source == "Blu-ray" and other == "Ultra HD") or source == "Ultra HD Blu-ray":
                return "UHD"
            if "UHD" in path:
                return "UHD"
            if type in ("DISC", "REMUX", "ENCODE") and resolution == "2160p":
                return "UHD"
            return ""
        except Exception as e:
            console.print(f"Error in get_uhd: {str(e)}")
            return ""

    def get_region(self, bdinfo, region=None):
        label = bdinfo.get("label", bdinfo.get("title", bdinfo.get("path", ""))).replace(".", " ")
        if region is not None:
            region = region.upper()
        else:
            for key, value in self.regions.items():
                if f" {key} " in label:
                    region = value
                    break
        return region if region is not None else ""

    @property
    def regions(self):
        if self._regions is None:
            self._regions = load_json(self.base_dir / "data/regions.json")
        return self._regions

    @property
    def services(self):
        if self._services is None:
            try:
                self._services = load_json(self.base_dir / "data/services.json")
            except Exception as e:
                console.print(f"[red]Error loading services.json: {e}[/red]")
                self._services = {}  # Fallback: Empty dict to avoid crashes
        return self._services

    def get_service(self, video, tag, audio, guess_title):
        try:
            guess = self._cached_guessit(video)
            service = guess.get("streaming_service", "")

            video_name = re.sub("[.()]", " ", video.replace(tag, "").replace(guess_title, ""))
            video_name = video_name.replace("DTS-HD.MA.", "").replace("DTS-HD MA ", "")

            opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
            guess_title_check = self._cached_guessit(video, opts).get("title", "")

            # Iterate over service codes and their alias lists
            for service_code, aliases in self.services.items():
                for key in aliases:
                    if (" " + key + " ") in video_name and key not in guess_title_check:
                        service = service_code
                    elif key == service:
                        service = service_code

            # Find the longest alias for the service code
            service_longname = service
            for service_code, aliases in self.services.items():
                if service_code == service and aliases:
                    service_longname = max(aliases, key=len, default=service_longname)

            if service_longname == "Amazon Prime":
                service_longname = "Amazon"
            return service, service_longname
        except Exception as e:
            console.print(f"[red]Error in get_service: {e}[/red]")
            return "", ""

    async def get_season_episode(self, video: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        filelist = meta["filelist"]
        meta.update({"tv_pack": 0, "episode_title_storage": "", "episode_title": ""})

        opts = frozenset({"excludes": frozenset(["country", "language"])}.items())
        video_guess = self._cached_guessit(video, opts)

        if not meta.get("anime", False):
            # Regular TV processing
            try:
                if meta.get("manual_date"):
                    guess_date = meta["manual_date"]
                else:
                    guess_date = video_guess.get("date")
                    season_int = int(video_guess.get("season", 1))
                    season = f"S{str(season_int).zfill(2)}"
                    if season_int == int(video_guess.get("year", 0)) and f"s{season_int}" not in video.lower():
                        season_int, season = 1, "S01"

                    if len(filelist) == 1:
                        episodes = video_guess.get("episode", 0)
                        if isinstance(episodes, list):
                            episode = "".join(f"E{str(ep).zfill(2)}" for ep in episodes)
                            episode_int = int(episodes[0])
                        else:
                            episode_int = int(episodes)
                            episode = f"E{str(episode_int).zfill(2)}"
                    else:
                        episode, episode_int = "", 0
                        meta["tv_pack"] = 1

                if guess_date:
                    season_int, episode_int = self.daily_to_tmdb_season_episode(meta.get("tmdb"), guess_date)
                    season, episode = str(guess_date), ""

            except Exception:
                console.print_exception()
                season, season_int = "S01", 1
                episode, episode_int = "", 0
                meta["tv_pack"] = 1

        else:
            # Anime processing
            parsed = anitopy.parse(Path(video).name)
            if not meta.get("mal_id"):
                romaji, mal_id, _, season_year, anilist_episodes = await AniListHandler().get_romaji(
                    parsed["anime_title"], meta.get("mal")
                )
                if mal_id:
                    meta["mal_id"] = mal_id

            if not meta.get("tmdb_manual") and not meta.get("tmdb"):
                year = parsed.get("anime_year", str(season_year))
                meta = await self.TMDb.get_tmdb_id(parsed["anime_title"], year, meta, "TV")

            if meta["category"] != "TV":
                return meta

            if tag := parsed.get("release_group"):
                meta["tag"] = f"-{tag}"

            # Episode handling
            if len(filelist) == 1:
                try:
                    episodes = parsed.get("episode_number", video_guess.get("episode", 1))
                    if isinstance(episodes, list):
                        episode = "".join(f"E{str(ep).zfill(2)}" for ep in episodes)
                        episode_int = int(episodes[0])
                    else:
                        episode_int = int(episodes)
                        episode = f"E{str(episode_int).zfill(2)}"
                except Exception:
                    episode, episode_int = "E01", 1
                    console.print(
                        "[bold yellow]There was an error guessing the episode number. Guessing E01. Use [bold green]--episode #[/bold green] to correct if needed"  # noqa: E501
                    )
                    await asyncio.sleep(1.5)
            else:
                episode, episode_int = "", 0
                meta["tv_pack"] = 1

            # Season handling
            try:
                season_int = int(meta.get("season_int", parsed.get("anime_season", video_guess.get("season", 1))))
                season = f"S{str(season_int).zfill(2)}"
            except Exception:
                try:
                    if episode_int >= anilist_episodes:
                        response = requests.post(
                            "https://thexem.info/map/single",
                            params={"id": str(meta["tvdb_id"]), "origin": "tvdb", "absolute": str(episode_int)},
                        ).json()
                        if response["result"] != "failure":
                            season_int = int(response["data"]["scene"]["season"])
                            season = f"S{str(season_int).zfill(2)}"
                            if len(filelist) == 1:
                                episode_int = int(response["data"]["scene"]["episode"])
                                episode = f"E{str(episode_int).zfill(2)}"
                        else:
                            raise Exception
                    else:
                        season, season_int = "S01", 1
                except Exception:
                    try:
                        season_int = int(video_guess.get("season", 1))
                        season = f"S{str(season_int).zfill(2)}"
                    except Exception:
                        season, season_int = "S01", 1
                    console.print(f"[bold yellow]{meta['title']} does not exist on thexem, guessing {season}")
                    console.print(f"[bold yellow]If [green]{season}[/green] is incorrect, use --season to correct")
                    await asyncio.sleep(3)

        if not meta.get("manual_season"):
            meta["season"] = season
        else:
            season_int = int(meta["manual_season"].lower().replace("s", ""))
            meta["season"] = f"S{str(season_int).zfill(2)}"

        if meta.get("manual_episode"):
            episode_int = int(meta["manual_episode"].lower().replace("e", ""))
            meta.update({"episode": f"E{str(episode_int).zfill(2)}", "tv_pack": 0})
        else:
            meta["episode"] = episode

        episode_opts = frozenset({"excludes": frozenset(["part"])}.items())
        episode_guess = self._cached_guessit(video, episode_opts)
        meta["episode_title_storage"] = episode_guess.get("episode_title", "")

        if meta["season"] == "S00" or meta["episode"] == "E00":
            meta["episode_title"] = meta["episode_title_storage"]

        if meta["tv_pack"] == 1:
            szn_guess = self._cached_guessit(os.path.dirname(video))
            part = szn_guess.get("part")
            meta["part"] = f"Part {part}" if part else ""
        else:
            meta["part"] = ""

        meta["season_int"] = season_int
        meta["episode_int"] = episode_int
        return meta

    @functools.lru_cache(maxsize=128)
    def _cached_guessit(self, input_str: str, options: frozenset = None) -> dict:
        try:
            opts = dict(options) if options else {}
            if "excludes" in opts:
                opts["excludes"] = list(opts["excludes"])
            return guessit(input_str, opts)
        except Exception as e:
            console.print(f"[red]Error parsing with guessit: {e}[/red]")
            return {}

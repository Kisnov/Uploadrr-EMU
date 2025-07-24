import asyncio
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tinytag import TinyTag

from src.databases import DiscogsAPI, MusicBrainzAPI
from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.utils.console import console, log


class MusicProcessor:
    """
    Handles music-specific analysis and metadata enrichment.
    Processes tracks, extracts tags/sources, queries APIs (MusicBrainz, Discogs).
    """

    MUSIC_EXTS = {
        ".aac",
        ".ac3",
        ".alac",
        ".eac3",
        ".flac",
        ".m4a",
        ".mp3",
        ".ogg",
        ".ogm",
        ".oga",
        ".ogv",
        ".ogx",
        ".opus",
        ".spx",
        ".wav",
    }
    IGNORE_TERM = ["web", "vinyl", "cd", "mp3", "flac", "wav", "aac", "alac", "khz"]
    LABEL_WORDS = ["records", "label", "music", "productions", "studio", "group", "band", "sound", "entertainment"]

    def __init__(self, mihandle: MediaInfoHandler, base_dir: Path):
        self.mihandle = mihandle
        self.base_dir = base_dir
        self.mb = MusicBrainzAPI("Art2Album / 1.0")
        self.dc = DiscogsAPI("Art2Album / 1.0")

    async def process_music(self, meta: Dict) -> Dict:
        """Music processing: Sorts files, extracts metadata, queries APIs."""
        pm = ProgressManager.get_instance()
        if pm:
            pm.update("Processing music metadata...")

        musicloc = Path(meta["path"])
        tracks, cover, back_cover, proof = self._sort_music(meta, musicloc)
        meta.update(
            track_count=len(tracks),
            user_cover=str(cover) if cover else None,
            back_cover=str(back_cover) if back_cover else None,
            user_proof=str(proof) if proof else None,
        )

        if not self._has_sufficient_music_metadata(meta) or meta.get("mbid_manual"):
            await self._enrich_music_metadata(meta, tracks)

        if pm:
            pm.update("Music processing complete")

        return meta

    def _sort_music(
        self, meta: Dict, musicloc: Path
    ) -> Tuple[List[Tuple[str, str, Path]], Optional[Path], Optional[Path], Optional[Path]]:
        """Sort tracks, detect covers/proof. Helper for process_music."""
        cover, back_cover, proof = None, None, None
        tracks = []

        filelist = [Path(p) for p in meta["filelist"]]
        has_disc_folders = any(any(folder.lower() in p.parent.name.lower() for folder in ["disc", "cd", "record"]) for p in filelist)

        for file_path in filelist:
            if not file_path.is_file() or file_path.name.startswith("._"):
                continue

            relative_path = file_path.relative_to(musicloc)
            file_name = file_path.name
            ext = file_path.suffix.lower()

            if ext in self.MUSIC_EXTS and not (file_name.lower().endswith("sample.mp3") or file_name.lower().startswith("!sample")):
                disc = "Disc 1"
                if has_disc_folders:
                    for part in relative_path.parts[:-1]:
                        if any(folder.lower() in part.lower() for folder in ["disc", "cd", "record"]):
                            disc = part.title()
                            break

                track_match = re.match(r"([A-Z]|\d+)(?:[\s.-])?(.*)", file_name, re.IGNORECASE)
                if not track_match:
                    continue

                track_num = track_match.group(1)
                rest_of_file = track_match.group(2).strip()

                if len(track_num) == 3 and track_num.isdigit():
                    disc = f"Disc {int(track_num[0])}"
                    track_num = track_num[1:]
                    file_name = f"{track_num}-{rest_of_file}"

                tracks.append((disc, file_name, file_path))

        has_alpha_tracks = any(re.match(r"^[A-Z]\d*$", t[1].split()[0], re.IGNORECASE) for t in tracks)
        if has_alpha_tracks:
            meta["source"] = "VINYL"
            tracks = [(t[0].replace("Disc 1", "Record"), t[1], t[2]) for t in tracks]

        tracks.sort(key=self._track_sort_key)

        # Handle images
        front_cover_variants = ["front", "cover", "album", "disc1", "cd1"]
        back_cover_variants = ["back_cover", "rear", "backside", "back"]
        for img_path_str in meta["user_images"]:
            img_path = Path(img_path_str)
            if img_path.suffix.lower() not in {".jpg", ".jpeg"}:
                continue
            file_name_lower = img_path.name.lower().replace(" ", "")

            if "proof" in file_name_lower:
                proof = img_path
                continue
            if any(v in file_name_lower for v in back_cover_variants):
                back_cover = img_path
                continue
            if cover is None and any(v in file_name_lower for v in front_cover_variants):
                cover = img_path
                continue
            if cover is None and "proof" not in file_name_lower:
                cover = img_path

        return tracks, cover, back_cover, proof

    def _track_sort_key(self, track: Tuple[str, str, Path]) -> Tuple[int, int]:
        """Helper sort key for tracks."""
        disc_num_match = re.search(r"\d+", track[0].split()[-1])
        disc_number = int(disc_num_match.group()) if disc_num_match else 1

        track_part = track[1].split()[0]
        match = re.match(r"^([A-Z])?(\d+)(?:[\s\.\-_\,](\d+))?", track_part, re.IGNORECASE)
        if match:
            letter, number, sub_number = match.groups()
            primary = (ord(letter.upper()) - ord("A") + 1) * 1000 if letter else 0
            secondary = int(number or "0") * 10
            tertiary = int(sub_number or "0")
            return (disc_number, primary + secondary + tertiary)
        elif track_part.isdigit():
            return (disc_number, int(track_part))
        return (disc_number, 999)

    async def _enrich_music_metadata(self, meta: Dict, tracks: List[Tuple[str, str, Path]]) -> None:
        """Enrich meta with MediaInfo, tags, APIs. Async for I/O."""
        if not tracks:
            return

        first_audio_file = tracks[0][2]
        mi = await self.mihandle.exportInfo(
            media_path=first_audio_file, is_dir=meta.get("is_dir", False), uuid=meta["uuid"], export_text=True
        )
        meta.update(
            is_disc=False,
            screens=0,
            edition="",
            bdinfo=None,
            tmdb=0,
            imdb=0,
            tvdb_id=0,
            mal_id=0,
            sd=0,
            keywords="",
            category="MUSIC",
            mediainfo=mi,
        )

        album_fname = meta["uuid"].replace("&", "AANDD")
        normalized_af = re.sub(r"[^a-zA-Z0-9\sAANDD]", " ", album_fname).replace("AANDD", "&")
        meta["source"] = meta.get("source", self._get_music_source(normalized_af))
        meta["tag"] = meta.get("tag") or self._extract_tag(album_fname)

        track = next((t for t in mi["media"]["track"] if t["@type"] == "General"), {})
        extra = track.get("extra", {})
        meta["artist"] = track.get("Album_Performer", track.get("Performer")) or extra.get("ARTISTS", extra.get("Performer"))
        meta["album"] = track.get("Album") or extra.get("Album")
        meta["type"] = (track.get("Format").replace("MPEG Audio", "MP3").replace("-", "")).upper()
        recorded_date = track.get("Recorded_Date", track.get("Released_Date", track.get("Year"))) or extra.get(
            "Year", extra.get("Recorded_Date", extra.get("Released_Date"))
        )
        if recorded_date and "-" in recorded_date:
            meta["year"] = recorded_date.split("-")[0]
            meta["release_date"] = recorded_date
        else:
            meta["year"] = recorded_date
            meta["release_date"] = ""

        meta["barcode"] = next(
            (v for k, v in {**track, **extra}.items() if k.lower() in ["barcode", "upc", "scanid", "scan_id"]), None
        )
        meta["catalog_number"] = self._extract_catalog_number(meta["uuid"], extra)

        audio_track = next((t for t in mi["media"]["track"] if t["@type"] == "Audio"), {})
        meta["bit_depth"] = audio_track.get("BitDepth", "")
        meta["bitrate"] = audio_track.get("BitRate_String", "").replace("/", "").replace(" ", "")

        # API queries (async gather for parallel if multiple)
        mbid = meta.get("mbid_manual") or meta.get("mbid")
        if mbid:
            await self.mb.fetch_musicbrainz_mbid(mbid, meta)
        else:
            methods = [
                ("barcode", self._search_by_barcode),
                ("catalog_number", self._search_by_catalog_number),
                ("title_artist", self._search_by_title_artist),
            ]
            for method_name, search_func in methods:
                if await self._try_search_method(meta, search_func):
                    break
            else:
                console.print("[red]WARN[/red]: No MusicBrainz data found using any method.")

        await self._process_with_discogs(meta)

        if not meta.get("tracklist"):
            tracklist = await self._build_tracklist(tracks)
            meta["tracklist"] = tracklist

        for key, value in [("discogs_id", "Discogs: "), ("catalog_number", "CATNO: "), ("barcode", "UPC: ")]:
            if meta.get(key):
                meta["keywords"] += f", {value}{meta[key]}"

    async def _try_search_method(self, meta: Dict, search_func) -> bool:
        """Helper: Try a search method, return True on success."""
        try:
            await search_func(meta)
            if meta.get("mbid"):
                return True
        except Exception as e:
            log.error(f"Error in search method: {e}")
        return False

    async def _search_by_barcode(self, meta: Dict) -> None:
        barcode = meta.get("barcode") or re.search(r"(barcode|upc)-([0-9]+)", meta["uuid"], re.IGNORECASE)
        if barcode:
            if isinstance(barcode, re.Match):
                barcode = barcode.group(2)
            meta["barcode"] = barcode
            await self.mb.search_musicbrainz_by_barcode(barcode, meta)

    async def _search_by_catalog_number(self, meta: Dict) -> None:
        if meta.get("catalog_number"):
            await self.mb.search_by_catalog_number(meta["catalog_number"], meta)

    async def _search_by_title_artist(self, meta: Dict) -> None:
        if meta.get("album") and meta.get("artist"):
            await self.mb.search_releases_by_title_artist(meta["album"], meta["artist"], meta.get("track_count", 0), meta)

    async def _build_tracklist(self, tracks: List[Tuple[str, str, Path]]) -> Dict[str, Dict[str, str]]:
        """Build tracklist with durations asynchronously."""
        tracklist = {}
        tasks = []
        for disc, file, path in tracks:
            tasks.append(self._get_track_duration(disc, file, path))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for disc, clean_track_name, time in results:
            if isinstance(time, Exception):
                log.error(f"Error processing track {clean_track_name}: {time}")
                time = "00:00"
            if disc not in tracklist:
                tracklist[disc] = {}
            tracklist[disc][clean_track_name] = time

        return tracklist

    async def _get_track_duration(self, disc: str, file: str, path: Path) -> Tuple[str, str, str]:
        """Async helper: Get duration for a track."""
        clean_track_name, _ = os.path.splitext(file)
        loop = asyncio.get_running_loop()
        tag = await loop.run_in_executor(None, TinyTag.get, path)
        duration = tag.duration
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        return disc, clean_track_name, f"{minutes:02}:{seconds:02}"

    async def _process_with_discogs(self, meta: Dict) -> None:
        """Query Discogs API asynchronously."""
        discogs_id = meta.get("discogs_id") or meta.get("DISCOGS_RELEASE_ID")
        discogs_url = meta.get("discogs_url")

        if not discogs_id and discogs_url:
            master_match = re.search(r"discogs\.com/master/(\d+)", discogs_url)
            if master_match:
                master_id = master_match.group(1)
                master_data = await self.dc.fetch_master_data(master_id)
                if master_data:
                    main_release_url = master_data.get("main_release_url", "")
                    id_match = re.search(r"releases/(\d+)", main_release_url)
                    if id_match:
                        discogs_id = id_match.group(1)
                        meta["discogs_id"] = discogs_id

        if discogs_id:
            release_data = await self.dc.fetch_discogs_release(discogs_id)
            if release_data:
                barcode = self.dc.extract_barcode(release_data)
                if barcode and not meta.get("mbid"):
                    meta["barcode"] = barcode
                    await self.mb.search_musicbrainz_by_barcode(barcode, meta)
                self.dc.extract_metadata(release_data, meta)

    def _get_music_source(self, normalized_af: str) -> str:
        nafu = normalized_af.upper()
        if " CD " in nafu:
            return "CD"
        elif " VINYL " in nafu:
            return "VINYL"
        elif " WEB " in nafu:
            return "WEB"
        return ""

    def _extract_tag(self, album_fname: str) -> str:
        album_fname = re.sub(r"hi-res", "", album_fname, flags=re.IGNORECASE)
        last_hyphen_index = album_fname.rfind("-")

        tag_match = ""

        if last_hyphen_index != -1:
            after_last_hyphen = album_fname[last_hyphen_index + 1 :].strip()
            outside_parentheses = re.sub(r"\(.*?\)", "", after_last_hyphen).strip()

            if outside_parentheses:
                bracket_tag_match = re.search(r"\[([^\[\]]+)\]$", outside_parentheses)
                if bracket_tag_match:
                    tag = bracket_tag_match.group(1).strip()

                    if self.is_valid_tag(tag):
                        tag_match = f"-{tag}"

                else:
                    tag = re.search(r"^[^-\[\]]+$", outside_parentheses)
                    if tag:
                        tag = tag.group(0).strip()

                        if self.is_valid_tag(tag):
                            tag_match = f"-{tag}"

        return tag_match if tag_match else ""

    def is_valid_tag(self, tag: str) -> bool:
        """Check if a tag is valid for music releases."""
        if not tag or not isinstance(tag, str):
            return False

        tag_lower = tag.lower()
        BARCODE_PATTERN = re.compile(r"\d{2,}")
        CAT_PATTERN = re.compile(r"^[a-zA-Z0-9&]+$")

        if any(term in tag_lower for term in self.IGNORE_TERM):
            return False
        if " " in tag:
            return False
        if BARCODE_PATTERN.search(tag):
            return False
        if not CAT_PATTERN.match(tag):
            return False
        if len(tag) > 15:
            return False
        if tag.isdigit():
            return False
        return True

    def _extract_catalog_number(self, album_fname: str, extra: Dict) -> Optional[str]:
        """Extract CATALOG_NUMBER from album_fname or MediaInfo."""
        LABEL_WORDS = ["records", "label", "music", "productions", "studio", "group", "band", "sound", "entertainment"]
        log.debug(f"Extracting catalog number from: {album_fname}")

        catalog_number = None

        catalog_number = extra.get("CATALOG_NUMBER") or extra.get("CATALOGNUMBER") or extra.get("CATNO") or extra.get("LABELNO")
        if catalog_number:
            log.debug(f"Found catalog number in extra dictionary: {catalog_number}")
            return re.sub(r"[ -]", "", catalog_number.strip())

        catalog_match = re.search(r"\{([^}]+)\}", album_fname)
        if catalog_match:
            catalog_number = catalog_match.group(1).strip()
            log.debug(f"Found catalog number in curly braces: {catalog_number}")

            if any(word in catalog_number.lower() for word in LABEL_WORDS):
                log.debug(f"Excluded as label or descriptor: {catalog_number}")
                return None

        if not catalog_number:
            catalog_match = re.search(r"\{([^}]+)\}", album_fname)
            if catalog_match:
                catalog_candidate = catalog_match.group(1).strip()
                parts = [part.strip() for part in catalog_candidate.split(",") if part.strip()]

                for part in parts:
                    if not any(word in part.lower() for word in LABEL_WORDS):
                        catalog_number = part
                        log.debug(f"Found valid catalog number: {catalog_number}")
                        break
                else:
                    log.debug(f"No valid catalog number found from parts: {parts}")

            if not catalog_number:
                catalog_match_title = re.search(r"\[([A-Za-z0-9\s,]+)\]", album_fname)
                if catalog_match_title:
                    catalog_candidate = catalog_match_title.group(1).strip()
                    parts = [part.strip() for part in catalog_candidate.split(",") if part.strip()]

                    for part in parts:
                        if re.search(r"\w", part) and not any(word in part.lower() for word in LABEL_WORDS):
                            catalog_number = part
                            log.debug(f"Selected valid catalog number: {catalog_number}")
                            break
                    else:
                        log.debug(f"No valid catalog number found from parts: {parts}")

        # Check for exclusion keywords in the selected catalog number
        if catalog_number and any(excl in catalog_number.lower() for excl in self.IGNORE_TERM):
            log.debug(f"Excluded due to keyword in catalog number: {catalog_number}")
            return None

        # Return the cleaned catalog number
        if catalog_number:
            return re.sub(r"[ -]", "", catalog_number.strip())

        log.debug("No catalog number found.")
        return None

    def _has_sufficient_music_metadata(self, meta: Dict) -> bool:
        required = ["artist", "album", "year", "source", "type", "bitrate", "duration", "mbid", "album_cover"]
        return all(meta.get(k) for k in required)

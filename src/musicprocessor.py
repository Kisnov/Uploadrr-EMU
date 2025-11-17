import asyncio
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tinytag import TinyTag

from src.databases import DeezerHandler, DiscogsHandler, MusicBrainzHandler
from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.loads import load_json


class MusicProcessor:
    """
    Handles music-specific analysis and metadata enrichment.
    Processes tracks, extracts tags/sources, queries APIs (MusicBrainz, Discogs, Deezer).
    """

    _music_services: Optional[Dict] = None

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

    @property
    def music_services(self):
        if self._music_services is None:
            try:
                self._music_services = load_json(self.base_dir / "data/music_services.json")
            except Exception as e:
                console.print(f"[red]Error loading music_services.json: {e}[/red]")
                self._music_services = {}  # Fallback: Empty dict to avoid crashes
        return self._music_services

    def __init__(self, mihandle: MediaInfoHandler, base_dir: Path):
        self.mihandle = mihandle
        self.base_dir = base_dir
        self.mb = MusicBrainzHandler("Art2Album / 1.0")
        self.dc = DiscogsHandler("Art2Album / 1.0")
        self.dz = DeezerHandler("Art2Album / 1.0")

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
        meta["service"] = meta.get("service") or self.get_service(meta)
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

        # Temporary values for searching
        temp_artist = meta.get("artist", "")
        temp_album = meta.get("album", "")
        temp_barcode = meta.get("barcode", "")
        temp_catalog_number = meta.get("catalog_number", "")

        # API queries
        mbid = meta.get("mbid_manual") or meta.get("mbid")
        if mbid:
            release = await self.mb.get_release(mbid, includes=["recordings", "genres", "labels", "url-rels", "release-groups"])
            if not release:
                return

            # release-group (for database links)
            rg = None
            rg_id = release.get("release-group", {}).get("id")
            if rg_id:
                rg = await self.mb.get_release_group(rg_id)

            meta["mb_info"] = self.mb.normalize(release, rg)

            self._process_mb_relations(meta["mb_info"]["relations"], meta)

            if not meta.get("barcode"):
                meta["barcode"] = meta["mb_info"].get("barcode") or ""
        else:
            methods = [
                ("barcode", lambda m: self._search_by_barcode(m, temp_barcode)),
                ("catalog_number", lambda m: self._search_by_catalog_number(m, temp_catalog_number)),
                ("title_artist", lambda m: self._search_by_title_artist(m, temp_album, temp_artist, len(tracks))),
            ]
            for method_name, search_func in methods:
                if await self._try_search_method(meta, search_func):
                    break
            else:
                console.print("[red]WARN[/red]: No MusicBrainz data found using any method.")

        await self._process_with_discogs(meta, temp_artist, temp_album, len(tracks))

        await self._process_with_deezer(meta, temp_artist, temp_album, temp_barcode, len(tracks))

        # Set album_cover and album_back
        if meta.get("mbid"):
            cover_list = await self.mb.get_cover_art(meta["mbid"])
            if cover_list and "images" in cover_list:
                for image in cover_list["images"]:
                    if image.get("approved"):
                        if "Front" in image.get("types", []) and not meta.get("album_cover"):
                            meta["album_cover"] = image["thumbnails"].get("500") or image["thumbnails"].get("large")
                        if "Back" in image.get("types", []) and not meta.get("album_back"):
                            meta["album_back"] = image["thumbnails"].get("500") or image["thumbnails"].get("large")

        if not meta.get("album_cover"):
            if meta.get("discogs_info") and meta["discogs_info"].get("images"):
                meta["album_cover"] = meta["discogs_info"]["images"][0].get("resource_url")
            elif meta.get("deezer_info") and meta["deezer_info"].get("cover_big"):
                meta["album_cover"] = meta["deezer_info"].get("cover_big")

        # If no MB info but Discogs or Deezer has barcode, try MB search
        if not meta.get("mb_info"):
            barcode = None
            if meta.get("discogs_info"):
                barcode = self.dc.extract_barcode(meta["discogs_info"])
            elif meta.get("deezer_info"):
                barcode = meta["deezer_info"].get("upc")

            if barcode and barcode != temp_barcode:
                meta["barcode"] = barcode
                await self._search_by_barcode(meta, barcode)

        self._consolidate_metadata(meta)

        if not meta.get("tracklist"):
            tracklist = await self._build_tracklist(tracks)
            meta["tracklist"] = tracklist

    async def _try_search_method(self, meta: Dict, search_func) -> bool:
        """Helper: Try a search method, return True on success."""
        try:
            await search_func(meta)
            if meta.get("mb_info"):
                return True
        except Exception as e:
            log.error(f"Error in search method: {e}")
        return False

    async def _search_by_barcode(self, meta: Dict, barcode: str) -> None:
        if barcode:
            releases = await self.mb.search_by_barcode(barcode)
            if releases and releases.get("releases"):
                best = releases["releases"][0]
                meta["mbid"] = best.get("id")
                release = await self.mb.get_release(
                    meta["mbid"], includes=["recordings", "genres", "labels", "url-rels", "release-groups"]
                )
                if release:
                    rg = None
                    rg_id = release.get("release-group", {}).get("id")
                    if rg_id:
                        rg = await self.mb.get_release_group(rg_id)
                    meta["mb_info"] = self.mb.normalize(release, rg)

    async def _search_by_catalog_number(self, meta: Dict, catalog_number: str) -> None:
        if catalog_number:
            releases = await self.mb.search("release", {"query": f"catno:{catalog_number}", "fmt": "json"})
            if releases and releases.get("releases"):
                best = releases["releases"][0]
                meta["mbid"] = best.get("id")
                release = await self.mb.get_release(
                    meta["mbid"], includes=["recordings", "genres", "labels", "url-rels", "release-groups"]
                )
                if release:
                    rg = None
                    rg_id = release.get("release-group", {}).get("id")
                    if rg_id:
                        rg = await self.mb.get_release_group(rg_id)
                    meta["mb_info"] = self.mb.normalize(release, rg)

    async def _search_by_title_artist(self, meta: Dict, release_title: str, artist_name: str, track_count: int) -> None:
        if release_title and artist_name:
            best = await self.mb.search_releases_filtered(release_title, artist_name, track_count)
            if best:
                meta["mbid"] = best.get("id")
                release = await self.mb.get_release(
                    meta["mbid"], includes=["recordings", "genres", "labels", "url-rels", "release-groups"]
                )
                if release:
                    rg = None
                    rg_id = release.get("release-group", {}).get("id")
                    if rg_id:
                        rg = await self.mb.get_release_group(rg_id)
                    meta["mb_info"] = self.mb.normalize(release, rg)

    async def _process_with_deezer(self, meta: Dict, temp_artist: str, temp_album: str, temp_barcode: str, track_count: int) -> None:
        """Query Deezer API asynchronously."""
        data = None
        if temp_barcode:
            data = await self.dz.fetch_album_by_upc(temp_barcode)
        if not data and temp_artist and temp_album:
            query = f'artist:"{temp_artist}" album:"{temp_album}"'
            data = await self.dz.search_albums_filtered(query, track_count)
        if data:
            filtered = {
                "title": data.get("title"),
                "upc": data.get("upc"),
                "link": data.get("link"),
                "cover": data.get("cover"),
                "cover_big": data.get("cover_big"),
                "label": data.get("label"),
                "nb_tracks": data.get("nb_tracks"),
                "duration": data.get("duration"),
                "tracklist": data.get("tracklist"),
                "explicit_lyrics": data.get("explicit_lyrics"),
                "type": data.get("type"),
                "genres": [g["name"] for g in data.get("genres", {}).get("data", [])],
                "artist": {
                    "name": data.get("artist", {}).get("name"),
                    "picture": data.get("artist", {}).get("picture"),
                    "picture_big": data.get("artist", {}).get("picture_big"),
                },
                "tracks": {
                    "data": [
                        {
                            "title": t.get("title"),
                            "title_short": t.get("title_short"),
                            "link": t.get("link"),
                            "duration": t.get("duration"),
                            "explicit_lyrics": t.get("explicit_lyrics"),
                            "disk_number": t.get("disk_number", 1),
                            "track_position": t.get("track_position"),
                        }
                        for t in data.get("tracks", {}).get("data", [])
                    ]
                },
            }
            meta["deezer_info"] = filtered

    async def _process_with_discogs(self, meta: Dict, temp_artist: str, temp_album: str, track_count: int) -> None:
        """Query Discogs API asynchronously."""
        mb_discogs = meta.get("mb_info", {}).get("relations", {}).get("discogs", [])
        discogs_release_id = None
        discogs_master = None

        for url in mb_discogs:
            if "release" in url:
                match = re.search(r"discogs\.com/release/(\d+)", url)
                if match:
                    discogs_release_id = match.group(1)
            elif "master" in url:
                match = re.search(r"discogs\.com/master/(\d+)", url)
                if match:
                    discogs_master = match.group(1)

        discogs_id = meta.get("discogs_id") or discogs_release_id
        discogs_master = meta.get("discogs_master") or discogs_master

        if not discogs_id and discogs_master:
            master_data = await self.dc.fetch_master_data(discogs_master)
            if master_data:
                main_release_url = master_data.get("main_release_url", "")
                id_match = re.search(r"releases/(\d+)", main_release_url)
                if id_match:
                    discogs_id = id_match.group(1)
                    meta["discogs_id"] = discogs_id

        release_data = None
        if discogs_id:
            release_data = await self.dc.fetch_discogs_release(discogs_id)
        elif temp_artist and temp_album:
            # Search Discogs if no ID from MB
            query = f'artist:"{temp_artist}" album:"{temp_album}"'
            release_data = await self.dc.search_releases_filtered(query, track_count)

        if release_data:
            filtered = {
                "year": release_data.get("year"),
                "resource_url": release_data.get("resource_url"),
                "uri": release_data.get("uri"),
                "artists": [{"name": a.get("name"), "thumbnail_url": a.get("thumbnail_url")} for a in release_data.get("artists", [])],
                "artists_sort": release_data.get("artists_sort"),
                "master_url": release_data.get("master_url"),
                "title": release_data.get("title"),
                "released": release_data.get("released"),
                "identifiers": [i for i in release_data.get("identifiers", []) if i.get("type") in ["Barcode", "ISRC"]],
                "genres": release_data.get("genres"),
                "styles": release_data.get("styles"),
                "tracklist": release_data.get("tracklist"),
                "format": release_data.get("formats", [{}])[0].get("name", "") if release_data.get("formats") else "",
                "images": [img for img in release_data.get("images", []) if img.get("type") == "primary"],
                "thumb": release_data.get("thumb"),
                "is_offensive": release_data.get("is_offensive"),
            }
            meta["discogs_info"] = filtered
            barcode = self.dc.extract_barcode(release_data)
            if barcode and not meta.get("barcode"):
                meta["barcode"] = barcode
            if barcode and not meta.get("mbid"):
                await self._search_by_barcode(meta, barcode)

    def _consolidate_metadata(self, meta: Dict) -> None:
        """
        Consolidate metadata from various sources;
        Preferring Discogs > Deezer > MusicBrainz > tags.
        tags last due to many scene uploads promo through bad tags
        """

        # Artist
        if "discogs_info" in meta:
            artists = meta["discogs_info"].get("artists", [])
            if artists:
                meta["artist"] = ", ".join(a.get("name", "") for a in artists)
        elif "deezer_info" in meta:
            artist = meta["deezer_info"].get("artist", {})
            if artist:
                meta["artist"] = artist.get("name", "")
        elif "mb_info" in meta:
            ac = meta["mb_info"].get("artist-credit", [])
            if ac:
                meta["artist"] = ", ".join(a.get("name", "") for a in ac)

        # Album
        if "discogs_info" in meta:
            title = meta["discogs_info"].get("title")
            if title:
                meta["album"] = title
        elif "deezer_info" in meta:
            title = meta["deezer_info"].get("title")
            if title:
                meta["album"] = title
        elif "mb_info" in meta:
            title = meta["mb_info"].get("title")
            if title:
                meta["album"] = title

        # Year and release_date
        if "discogs_info" in meta:
            year = meta["discogs_info"].get("year")
            if year:
                meta["year"] = str(year)
                meta["release_date"] = str(year)
        elif "deezer_info" in meta:
            date = meta["deezer_info"].get("release_date", "")
            if date:
                meta["release_date"] = date
                meta["year"] = date.split("-")[0]
        elif "mb_info" in meta:
            date = meta["mb_info"].get("date", "")
            if date:
                meta["release_date"] = date
                meta["year"] = date.split("-")[0]

        # Edition
        manual_edition = meta.get("manual_edition")
        if "mb_info" in meta:
            edition = meta["mb_info"].get("disambiguation", "").strip().title()
            corrections = {"itunes": "iTunes", " for ": " for ", "Cd ": "CD ", "Th ": "th "}
            for original, correction in corrections.items():
                edition = re.sub(r"\b" + re.escape(original) + r"\b", correction, edition, flags=re.IGNORECASE)
            edition = re.sub(r"(\d)Th", r"\1th", edition)
            meta["edition"] = manual_edition if manual_edition else edition

        # Source
        if "mb_info" in meta:
            media = meta["mb_info"].get("media", [])
            if media:
                fmt = media[0].get("format", "")
                if fmt:
                    meta["source"] = fmt.replace("Digital Media", "WEB")
        elif "discogs_info" in meta:
            fmt = meta["discogs_info"].get("format", "")
            if fmt:
                meta["source"] = fmt.upper()

        # Keywords (combine unique from all sources)
        keywords = set(meta.get("keywords", "").split(", "))
        if "mb_info" in meta:
            # Status
            status = meta["mb_info"].get("status", "").lower()
            if status in ["bootleg", "unofficial", "promotion", "withdrawn"]:
                keywords.add(status)
            # Release group types
            rg = meta["mb_info"].get("release-group", {})
            ptype = rg.get("primary-type", "").lower()
            if ptype in ["compilation", "live"]:
                keywords.add(ptype)
            for st in rg.get("secondary-types", []):
                stl = st.lower()
                if stl in ["compilation", "live"]:
                    keywords.add(stl)
            keywords.update(meta["mb_info"].get("genres", []))
        # Genres

        if "discogs_info" in meta:
            genres = [g.lower() for g in meta["discogs_info"].get("genres", [])]
            styles = [s.lower() for s in meta["discogs_info"].get("styles", [])]
            keywords.update(genres + styles)
        if "deezer_info" in meta:
            genres = [g.lower() for g in meta["deezer_info"].get("genres", [])]
            keywords.update(genres)
        meta["keywords"] = ", ".join(sorted(k for k in keywords if k)).strip(", ")

        # Catalog number (prefer MB)
        if "mb_info" in meta:
            cat = meta["mb_info"].get("catalog_number")
            if cat:
                meta["catalog_number"] = cat

        # Tracklist (prefer MB > Discogs > Deezer)
        tracklist = None
        if "mb_info" in meta:
            tracklist = {}
            for medium in meta["mb_info"].get("media", []):
                disc = f"Disc {medium['disc']}"
                tracklist[disc] = {}
                tracks = medium.get("tracks", [])
                pad_len = len(str(len(tracks))) if tracks else 1
                for track in tracks:
                    ms = track.get("duration_ms", 0)
                    dur = timedelta(milliseconds=ms)
                    m, s = divmod(dur.seconds, 60)
                    time = f"{m:02}:{s:02}"
                    pad = track["number"].zfill(pad_len)
                    key = f"{pad}. {track['title']}"
                    tracklist[disc][key] = time
        elif "discogs_info" in meta:
            tracklist = {}
            tl = meta["discogs_info"].get("tracklist", [])
            pad_len = len(str(len(tl)))
            for t in tl:
                pos = t.get("position", "")
                if "-" in pos:
                    dnum, tnum = pos.split("-", 1)
                    disc = f"Disc {dnum}"
                else:
                    disc = "Disc 1"
                    tnum = pos
                if disc not in tracklist:
                    tracklist[disc] = {}
                title = t.get("title", "Unknown Track")
                dur = t.get("duration", "")
                pad = str(tnum).zfill(pad_len)
                key = f"{pad}. {title}"
                tracklist[disc][key] = dur
        elif "deezer_info" in meta:
            tracklist = {}
            tl = meta["deezer_info"].get("tracks", {"data": []})["data"]
            disc_tracks = {}
            for t in tl:
                disc_num = t.get("disk_number", 1)
                disc = f"Disc {disc_num}"
                disc_tracks.setdefault(disc, []).append(t)
            for disc, dtracks in disc_tracks.items():
                tracklist[disc] = {}
                pad_len = len(str(len(dtracks)))
                for idx, t in enumerate(sorted(dtracks, key=lambda x: x["track_position"])):
                    dur_sec = t.get("duration", 0)
                    m = dur_sec // 60
                    s = dur_sec % 60
                    time = f"{m:02}:{s:02}"
                    pad = str(t["track_position"]).zfill(pad_len)
                    key = f"{pad}. {t['title']}"
                    tracklist[disc][key] = time
        if tracklist:
            meta["tracklist"] = tracklist

        for key, value in [
            ("discogs_id", "Discogs: "),
            ("discogs_master", "Discogs Master: "),
            ("catalog_number", "CATNO: "),
            ("barcode", "UPC: "),
        ]:
            if meta.get(key):
                meta["keywords"] += f", {value}{meta[key]}" if meta["keywords"] else f"{value}{meta[key]}"

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
        LABEL_WORDS = {"records", "label", "music", "productions", "studio", "group", "band", "sound", "entertainment"}
        log.debug(f"Extracting catalog number from: {album_fname}")

        # Try structured metadata first (fast path)
        for key in ("CATALOG_NUMBER", "CATALOGNUMBER", "CATNO", "LABELNO"):
            if catalog := extra.get(key):
                cleaned = re.sub(r"[ -]", "", catalog.strip())
                log.debug(f"Found catalog number in extra[{key}]: {cleaned}")
                return cleaned

        # Try filename patterns: {catalog} or [catalog, label, year, ...]
        patterns = [(r"\{([^}]+)\}", "curly braces"), (r"\[([A-Za-z0-9\s,]+)\]", "square brackets")]

        for pattern, source in patterns:
            if match := re.search(pattern, album_fname):
                candidate_str = match.group(1).strip()
                parts = (part.strip() for part in candidate_str.split(",") if part.strip())
                for part in parts:
                    lower_part = part.lower()

                    # Skip if it looks like a year
                    if re.fullmatch(r"\d{4}", part):
                        continue

                    # Skip if it contains a label-related word
                    if any(word in lower_part for word in LABEL_WORDS):
                        log.debug(f"Skipped part (label word): {part}")
                        continue

                    # Must contain at least one alphanumeric char (avoid empty/symbols)
                    if not re.search(r"[A-Za-z0-9]", part):
                        continue

                    # Found a plausible candidate
                    log.debug(f"Selected catalog number: {part}")
                    catalog_number = part
                    break
                else:
                    continue  # no valid part in this candidate
                break  # valid catalog_number found
        else:
            catalog_number = None

        if catalog_number:
            if any(excl in catalog_number.lower() for excl in self.IGNORE_TERM):
                log.debug(f"Excluded due to IGNORE_TERM: {catalog_number}")
                return None
            return re.sub(r"[ -]", "", catalog_number.strip())

        log.debug("No catalog number found.")
        return None

    def _has_sufficient_music_metadata(self, meta: Dict) -> bool:
        required = ["artist", "album", "year", "source", "type", "bitrate", "duration", "tracklist", "album_cover"]
        return all(meta.get(k) for k in required)

    def get_service(self, meta: Dict) -> str:
        """Determine the music service from metadata."""
        if meta.get("service"):
            return meta["service"]

        # Check mediainfo for SOURCE in extra
        mi = meta.get("mediainfo", {})
        if mi and "media" in mi and "track" in mi["media"]:
            for track in mi["media"]["track"]:
                if track.get("@type") == "General":
                    extra = track.get("extra", {})
                    for key, value in extra.items():
                        if key.upper() == "SOURCE":
                            service = self._validate_service(value)
                            if service:
                                return service
                    break

        # Fallback to uuid
        uuid = meta.get("uuid", "")
        service = self._extract_service_from_uuid(uuid)
        if service:
            service = self._validate_service(service)
            if service:
                return service

        return ""

    def _validate_service(self, service_name: str) -> str:
        """Validate and return the service code if found in music_services."""
        if not service_name:
            return ""
        service_name_upper = service_name.upper()
        for code, aliases in self.music_services.items():
            for alias in aliases:
                if alias.upper() == service_name_upper:
                    return code
        return ""

    def _extract_service_from_uuid(self, uuid: str) -> str:
        """Extract potential service from uuid as last resort."""
        if not uuid:
            return ""
        uuid_upper = uuid.upper()
        for code, aliases in self.music_services.items():
            for alias in aliases:
                if alias.upper() in uuid_upper:
                    return alias
        return ""

    def _process_mb_relations(self, relations: Dict[str, List[str]], meta: Dict) -> None:
        """Populate meta from cleaned relations (now dict of lists)."""
        # Discogs
        for url in relations.get("discogs", []):
            if match := re.search(r"discogs\.com/release/(\d+)", url):
                meta["discogs_id"] = match.group(1)
            elif match := re.search(r"discogs\.com/master/(\d+)", url):
                meta["discogs_master"] = match.group(1)
            break

        # Single-value
        for key, meta_key in [("allmusic", "allmusic"), ("wikidata", "wikidata")]:
            if urls := relations.get(key):
                meta[meta_key] = urls[0]

        # Genius
        for url in relations.get("genius", []) + relations.get("lyrics", []):
            if "genius.com" in url:
                meta["genius"] = url
                break

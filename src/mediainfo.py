import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import aiofiles
from pymediainfo import MediaInfo

from src.utils.console import console, log

from .args import Args


# TODO if legacy MEDIAINFO_CLEANPATH.txt exists delete then redo MEDIAINFO.txt
class MediaInfoHandler:
    _instance = None

    def __new__(cls, config=None, base_dir=None):
        if cls._instance is None:
            if config is None or base_dir is None:
                raise ValueError("config and base_dir not initialized")
            cls._instance = super().__new__(cls)
            cls._instance.__init_singleton(config, base_dir)
        return cls._instance

    def __init_singleton(self, config: Dict, base_dir: str):
        self.config = config
        self.base_dir = base_dir
        self._cache: Dict[str, dict] = {}

    async def load(
        self, uuid: str, media_path: str, is_dir: bool, export_text: bool = True, dvd_patch: bool = False, bdmv: bool = False
    ) -> dict:
        """Load or generate MediaInfo data, caching the result."""
        if uuid in self._cache:
            return self._cache[uuid]

        media_path = os.path.normpath(media_path)
        tmp_dir = Path(self.base_dir) / "tmp" / uuid
        tmp_dir.mkdir(parents=True, exist_ok=True)
        basename = os.path.basename(media_path)
        json_path = tmp_dir / "MediaInfo.json"

        try:
            # Generate MediaInfo if JSON doesn't exist or DVD patch is required
            if not json_path.exists() or dvd_patch:
                console.print("[bold yellow]Exporting MediaInfo...")
                os.chdir(os.path.dirname(media_path))
                media_info = MediaInfo.parse(media_path, output="JSON", mediainfo_options={"inform_version": "1"})
                async with aiofiles.open(json_path, "w", encoding="utf-8") as export:
                    await export.write(media_info)
                console.print("[bold green]MediaInfo JSON Exported.")

                # Text output
                if export_text:
                    media_info_text = MediaInfo.parse(
                        media_path, output="STRING", full=False, mediainfo_options={"inform_version": "1"}
                    )
                    clean_path = media_info_text
                    if bdmv:
                        # Sanitize text output for BDMV
                        clean_path = await self._generate_bdmv_text_output(media_info_text, media_path, uuid)
                    elif dvd_patch:
                        clean_path = media_info_text.replace(str(tmp_dir), uuid)
                    else:
                        clean_path = media_info_text.replace(media_path, basename)
                    async with aiofiles.open(tmp_dir / "MEDIAINFO.txt", "w", newline="", encoding="utf-8") as export:
                        await export.write(clean_path)
                    console.print("[bold green]MediaInfo text Exported.") if not dvd_patch else console.print(
                        "[dim]DVD[/dim] [bold green]MediaInfo Patched."
                    )

            # Load and cache the JSON
            async with aiofiles.open(json_path, "r", encoding="utf-8") as f:
                media_info = json.loads(await f.read())
                self._cache[uuid] = media_info
                return media_info

        except FileNotFoundError:
            console.print(f"[bold red]File not found: {media_path}")
            raise
        except Exception as e:
            console.print(f"[bold red]Error processing MediaInfo for {media_path}: {e}")
            raise

    async def _generate_bdmv_text_output(self, media_info_text: str, clpi_path: str, uuid: str) -> str:
        """Sanitize text output for BDMV to reference .m2ts, adjust format, and use .m2ts file size."""
        clpi_path = Path(clpi_path)
        clpi_name = clpi_path.stem
        parts = clpi_path.parts
        bdmv_index = parts.index("BDMV")
        disc_path = Path(*parts[:bdmv_index])
        m2ts_path = disc_path / "BDMV" / "STREAM" / f"{clpi_name}.m2ts"

        if not m2ts_path.exists():
            console.print(f"[bold red]Corresponding .m2ts file not found: {m2ts_path}")
            raise FileNotFoundError(f".m2ts file {m2ts_path} not found")

        # Sanitize text output
        lines = media_info_text.splitlines()
        output = []
        for line in lines:
            if line.startswith("Complete name"):
                # Replace .clpi path with .m2ts path, keep from disc root
                complete_name = line.split(":", 1)[1].strip()
                complete_name = complete_name.replace(str(clpi_path), str(m2ts_path))
                complete_name = str(Path(complete_name).relative_to(disc_path))
                output.append(f"Complete name                            : {complete_name}")
            elif line.startswith("Format") and "Blu-ray Clip info" in line:
                output.append("Format                                   : BDAV")
            elif line.startswith("File size"):
                # Use .m2ts file size
                m2ts_size = m2ts_path.stat().st_size
                readable_size = f"{m2ts_size / (1024**3):.2f} GiB"
                output.append(f"File size                                : {readable_size}")
            else:
                output.append(line)

        return "\n".join(output)

    async def exportInfo(
        self, media_path: str, is_dir: bool, uuid: str, export_text: bool = True, dvd_patch: bool = False, bdmv: bool = False
    ) -> dict:
        """Export MediaInfo data to JSON and TXT"""
        return await self.load(uuid, media_path, is_dir, export_text, dvd_patch, bdmv)

    def get_resolution(self, guess, uuid: str) -> str:
        """Extract resolution from cached MediaInfo JSON."""
        mi = self._cache.get(uuid)
        if not mi:
            json_path = Path(self.base_dir) / "tmp" / uuid / "MediaInfo.json"
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    mi = json.load(f)
                    self._cache[uuid] = mi
            else:
                return "Unknown"
        try:
            width = int(mi["media"]["track"][1].get("Width", 0))
            height = int(mi["media"]["track"][1].get("Height", 0))
            scan = mi["media"]["track"][1].get("ScanType", "Progressive")
            scan = "p" if scan == "Progressive" else "i"
        except (KeyError, IndexError, ValueError):
            width, height = 0, 0
            scan = "p"

        width_list = [3840, 2560, 1920, 1280, 1024, 854, 720, 15360, 7680, 0]
        height_list = [2160, 1440, 1080, 720, 576, 540, 480, 8640, 4320, 0]
        closest_width = self._closest(width_list, width)
        closest_height = self._closest(height_list, height)
        res = f"{closest_width}x{closest_height}{scan}"
        return self._map_resolution(res, guess, closest_width, scan, closest_height, height)

    @staticmethod
    def _closest(lst: list, value: int) -> int:
        """Find the closest value in a sorted list not exceeding the target."""
        lst = sorted(lst)
        return next((x for x in lst if value <= x), lst[-1])

    @staticmethod
    def _map_resolution(res: str, guess, width: int, scan: str, height: int, actual_height: int) -> str:
        """Map resolution string to standard format."""
        RESOLUTION_MAP = {
            "15360x8640p": "8640p",
            "7680x4320p": "4320p",
            "3840x2160p": "2160p",
            "2560x1440p": "1440p",
            "1920x1080p": "1080p",
            "1920x1080i": "1080i",
            "1280x720p": "720p",
            "1280x540p": "720p",
            "1280x576p": "720p",
            "1024x576p": "576p",
            "1024x576i": "576i",
            "720x576p": "576p",
            "720x576i": "576i",
            "854x480p": "480p",
            "854x480i": "480i",
            "720x480p": "480p",
            "720x480i": "480i",
            "OTHER": "OTHER",
        }
        WIDTH_FALLBACK = {
            15360: "8640p",
            7680: "4320p",
            3840: "2160p",
            2560: "1440p",
            1920: {"p": "1080p", "i": "1080i"},
            1280: "720p",
            1024: {"p": "576p", "i": "576i"},
            854: {"p": "480p", "i": "480i"},
            720: {"p": "576p", "i": "576i"},
        }

        if actual_height == 540:
            return "OTHER"

        resolution = RESOLUTION_MAP.get(res, res if res in RESOLUTION_MAP.values() else None)
        if resolution:
            return resolution

        try:
            resolution = guess["screen_size"]
            if resolution in RESOLUTION_MAP.values():
                return resolution
        except (KeyError, TypeError):
            pass

        width_mapping = WIDTH_FALLBACK.get(width, "OTHER")
        return width_mapping.get(scan, width_mapping) if isinstance(width_mapping, dict) else width_mapping

    def get_audio(self, uuid: str, meta: dict, bdinfo=None) -> tuple[str, str, bool, bool]:
        """Extract audio information from MediaInfo or BDInfo (fallback)."""
        mi = self._cache.get(uuid)
        if not mi:
            json_path = Path(self.base_dir) / "tmp" / uuid / "MediaInfo.json"
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    mi = json.load(f)
                    self._cache[uuid] = mi

        if not mi and not bdinfo:
            return "Unknown", "Unknown", False, False

        # === Music mode === # TODO MAY NOT NEED ANYMORE? SCRAP or return
        if meta.get("is_music", False):
            tracks = mi.get("media", {}).get("track", []) if mi else []
            audio_tracks = [t for t in tracks if t.get("@type") == "Audio"]

            if not audio_tracks:
                return "No Audio", "Unknown", False, False

            track = audio_tracks[0]
            fmt = track.get("Format", "Unknown")
            channels = track.get("Channels", "Unknown")

            if str(channels).isdigit():
                channels = f"{int(channels)}.0"

            if track.get("Language", "").lower() == "zxx":
                meta["instrumental"] = True

            CODEC_MAP = {
                "DTS": "DTS",
                "AAC": "AAC",
                "AC-3": "DD",
                "E-AC-3": "DDP",
                "MLP FBA": "TrueHD",
                "FLAC": "FLAC",
                "Opus": "Opus",
                "Vorbis": "Vorbis",
                "PCM": "LPCM",
            }
            codec = CODEC_MAP.get(fmt, fmt)
            return f"{codec} {channels}", channels, False, False

        # === Video mode ===
        AUDIO_MAP = {
            "DTS": "DTS",
            "AAC": "AAC",
            "AAC LC": "AAC",
            "AC-3": "DD",
            "E-AC-3": "DDP",
            "MLP FBA": "TrueHD",
            "FLAC": "FLAC",
            "Opus": "Opus",
            "Vorbis": "Vorbis",
            "PCM": "LPCM",
            "LPCM Audio": "LPCM",
            "Dolby Digital Audio": "DD",
            "Dolby Digital Plus Audio": "DDP",
            "Dolby TrueHD Audio": "TrueHD",
            "DTS Audio": "DTS",
            "DTS-HD Master Audio": "DTS-HD MA",
            "DTS-HD High-Res Audio": "DTS-HD HRA",
            "DTS:X Master Audio": "DTS:X",
        }
        commercial_names = {
            "Dolby Digital": "DD",
            "Dolby Digital Plus": "DDP",
            "Dolby TrueHD": "TrueHD",
            "DTS-ES": "DTS-ES",
            "DTS-HD High": "DTS-HD HRA",
            "Free Lossless Audio Codec": "FLAC",
            "DTS-HD Master Audio": "DTS-HD MA",
        }
        format_extra = {"JOC": " Atmos", "16-ch": " Atmos", "Atmos Audio": " Atmos"}
        format_settings_extra = {"Dolby Surround EX": "EX"}
        audio_extra = {"XLL": "-HD MA", "XLL X": ":X", "ES": "-ES"}

        tracks = mi.get("media", {}).get("track", []) if mi else []
        audio_tracks = [t for t in tracks if t.get("@type") == "Audio"]

        fmt = commercial = additional = format_settings = channel_layout = "Unknown"
        channels = chan = "2.0"
        track = {}

        if audio_tracks:
            track_num = 0
            if meta.get("original_language"):
                track_num = next(
                    (
                        i
                        for i, t in enumerate(audio_tracks)
                        if t.get("Language_String2", "").lower() == meta["original_language"].lower()
                        and "commentary" not in t.get("Title", "").lower()
                    ),
                    0,
                )
            track = audio_tracks[min(track_num, len(audio_tracks) - 1)]

            fmt = track.get("Format", "Unknown")
            commercial = track.get("Format_Commercial", fmt)
            additional = track.get("Format_AdditionalFeatures", "")
            format_settings = track.get("Format_Settings", "")
            channels = track.get("Channels_Original", track.get("Channels", "2"))
            channel_layout = track.get("ChannelLayout", track.get("ChannelLayout_Original", ""))

        elif bdinfo:
            audio_data = bdinfo.get("audio", [{}])[0]
            fmt = audio_data.get("codec", "Unknown")
            commercial = fmt
            additional = audio_data.get("atmos_why_you_be_like_this", "")
            channels = audio_data.get("channels", "2.0")
            channel_layout = ""

        # === Channel calculation ===
        try:
            ch_int = int(float(channels))
            if "LFE" in channel_layout:
                chan = f"{ch_int - 1}.1"
            else:
                chan = f"{ch_int}.0" if ch_int <= 2 else f"{ch_int - 1}.1"
        except (ValueError, TypeError):
            # Fallback to channel layout parsing
            if channel_layout and "LFE" in channel_layout:
                chan_count = len(channel_layout.split()) - 1  # Subtract LFE
                chan = f"{chan_count}.1"
            else:
                chan = "2.0"

        # === Silent Film detection ===
        soundmix = meta.get("imdb_info", {}).get("soundmix") or []
        keywords = meta.get("keywords", "").lower().split(",")
        release_date = meta.get("full_date", "")
        year = meta.get("year", "")

        try:
            parsed_date = datetime.strptime(release_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            parsed_date = None

        is_silent = (
            any("silent" in sm.lower() for sm in soundmix)
            or "silent film" in keywords
            or (parsed_date and parsed_date < datetime(1927, 10, 1))
            or (not parsed_date and year.isdigit() and int(year) <= 1926)
            or all(t.get("Language", "").lower() == "zxx" for t in tracks if t.get("@type") == "Audio")
        )

        # === Language Detection ===
        lang = ""
        if meta.get("original_language") and not is_silent:
            original_lang = meta["original_language"].lower()
            languages = set()
            language_tracks = []

            for t in tracks:
                if t.get("@type") != "Audio":
                    continue
                lang_code = t.get("Language_String2", "").lower()
                l3 = t.get("Language_String3", "").lower()
                title = t.get("Title", "").lower()
                if "comment" in title or "score" in title or "review" in title or l3 in ("", "zxx"):
                    continue  # Exclude commentary, isolated score, zxx
                languages.add(lang_code)
                language_tracks.append(t)

            has_original = original_lang in languages
            num_languages = len(languages)

            if num_languages == 0:
                lang = ""
            elif num_languages == 1:
                if has_original:
                    for t in language_tracks:
                        if t.get("Language_String2", "").lower() == original_lang:
                            lang = t.get("Language_String", "").split(" ")[0].upper()
                            break
                else:
                    lang = "DUBBED"
            else:
                if num_languages == 2:
                    lang = "DUAL"
                else:
                    lang = "MULTI"

        # === Commentary Detection ===
        has_commentary = any("commentary" in t.get("Title", "").lower() for t in tracks if t.get("@type") == "Audio")

        # === Codec Naming ===
        codec = commercial_names.get(commercial, AUDIO_MAP.get(fmt, fmt))
        if not codec:
            codec = AUDIO_MAP.get(fmt, fmt) + audio_extra.get(additional, "")

        # DTS:X fix
        if fmt.startswith("DTS") and additional.endswith("X"):
            codec = "DTS:X"
            try:
                chan = f"{int(float(channels)) - 1}.1"
            except Exception:
                pass

        # MPEG Audio fallback
        if fmt == "MPEG Audio":
            codec = track.get("CodecID_Hint", codec)

        extra = format_extra.get(additional, "")
        fsx = format_settings_extra.get(format_settings, "")
        fsx = fsx if fsx == "EX" and chan == "5.1" else ""

        audio_str = f"{codec} {fsx} {chan}{extra}".strip()
        return lang, audio_str, chan, has_commentary, is_silent

    def get_hdr(self, uuid: str, bdinfo: Optional[dict] = None) -> str:
        """Extract HDR and Dolby Vision information from cached MediaInfo JSON."""
        mi = self._cache.get(uuid)
        if not mi:
            json_path = Path(self.base_dir) / "tmp" / uuid / "MediaInfo.json"
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    mi = json.load(f)
                    self._cache[uuid] = mi
            else:
                log.error(f"MediaInfo.json not found for uuid: {uuid}")
                return ""

        try:
            hdr = ""
            dv = ""

            if bdinfo:  # BDMV
                hdr_info = bdinfo["video"][0]["hdr_dv"]
                hdr = "HDR10+" if "HDR10+" in hdr_info else "HDR" if hdr_info == "HDR10" else ""
                dv = "DV" if len(bdinfo["video"]) > 1 and bdinfo["video"][1].get("hdr_dv") == "Dolby Vision" else ""
            else:  # File-based media
                video_track = mi["media"]["track"][1]
                try:
                    if video_track.get("colour_primaries") in ("BT.2020", "REC.2020"):
                        hdr_format = video_track.get(
                            "HDR_Format_Compatibility", video_track.get("HDR_Format_String", video_track.get("HDR_Format", ""))
                        )
                        if "HDR10+" in hdr_format:
                            hdr = "HDR10+"
                        elif "HDR10" in hdr_format:
                            hdr = "HDR"
                        elif not hdr_format and "PQ" in (
                            video_track.get("transfer_characteristics"),
                            video_track.get("transfer_characteristics_Original"),
                        ):
                            hdr = "PQ10"
                        elif "HLG" in video_track.get("transfer_characteristics_Original", ""):
                            hdr = "HLG"
                        elif "BT.2020 (10-bit)" in video_track.get("transfer_characteristics_Original", ""):
                            hdr = "WCG"
                except Exception:
                    pass

                dv = (
                    "DV"
                    if "Dolby Vision" in video_track.get("HDR_Format", "")
                    or "Dolby Vision" in video_track.get("HDR_Format_String", "")
                    else ""
                )

            return f"{dv} {hdr}".strip()
        except Exception as e:
            log.error(f"Error in get_hdr for uuid {uuid}: {str(e)}")
            return ""

    def get_tmdb_imdb_from_mediainfo(self, uuid: str, category: str, is_disc: bool, tmdbid: str, imdbid: str) -> tuple[str, str, str]:
        """Extract TMDb and IMDb IDs from cached MediaInfo JSON."""
        mi = self._cache.get(uuid)
        if not mi:
            json_path = Path(self.base_dir) / "tmp" / uuid / "MediaInfo.json"
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    mi = json.load(f)
                    self._cache[uuid] = mi
            else:
                log.error(f"MediaInfo.json not found for uuid: {uuid}")
                return category, tmdbid, imdbid  # Return unchanged values if no MediaInfo

        try:
            if not is_disc and mi["media"]["track"][0].get("extra"):
                extra = mi["media"]["track"][0]["extra"]
                for key in extra:
                    if key.lower().startswith("tmdb"):
                        parser = Args(config=self.config)
                        category, tmdbid = parser.parse_tmdb_id(id=extra[key], category=category)
                    if key.lower().startswith("imdb"):
                        try:
                            imdbid = str(int(extra[key].replace("tt", ""))).zfill(7)
                            imdbid = f"tt{imdbid}"  # Ensure IMDb ID is in 'tt' format
                        except (ValueError, TypeError):
                            log.debug(f"Invalid IMDb ID in mediainfo: {extra[key]}")
            return category, tmdbid, imdbid
        except Exception as e:
            log.error(f"Error in get_tmdb_imdb_from_mediainfo for uuid {uuid}: {str(e)}")
            return category, tmdbid, imdbid

    def get_video_encode(self, uuid: str, type_: str, bdinfo=None) -> tuple[str, str, bool, str]:
        """Extract video encode information from cached MediaInfo JSON."""
        mi = self._cache.get(uuid)
        if not mi:
            json_path = Path(self.base_dir) / "tmp" / uuid / "MediaInfo.json"
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    mi = json.load(f)
                    self._cache[uuid] = mi
            else:
                return "Unknown", "Unknown", False, "0"

        video_encode = ""
        codec = ""
        bit_depth = "0"
        has_encode_settings = False
        hfr = ""

        try:
            track = mi["media"]["track"][1]
            format = track["Format"]
            format_profile = track.get("Format_Profile", format)
            has_encode_settings = bool(track.get("Encoded_Library_Settings"))
            bit_depth = track.get("BitDepth", "0")
            frame_rate = float(track.get("FrameRate", 0))
            hfr = "HDR" if frame_rate > 30 else ""
        except (KeyError, IndexError):
            format = bdinfo["video"][0]["codec"]
            format_profile = bdinfo["video"][0]["profile"]

        # Codec mapping based on type
        CODEC_MAP = {
            "AVC": {"ENCODE": "x264", "WEBRIP": "x264", "WEBDL": "H.264", "HDTV": "H.264"},
            "HEVC": {"ENCODE": "x265", "WEBRIP": "x265", "WEBDL": "H.265", "HDTV": "H.265"},
            "VP9": "VP9",
            "VC-1": "VC-1",
            "AV1": "AV1",
        }

        format_mapping = CODEC_MAP.get(format, format)
        if isinstance(format_mapping, dict):
            codec = format_mapping.get(type_, format)
        else:
            codec = format_mapping

        log.debug(f"Codec is: {codec}")

        if type_ == "HDTV" and has_encode_settings:
            codec = codec.replace("H.", "x")

        if format in {"VP9", "VC-1", "AV1"}:
            codec = format

        profile_map = {"High 10": "Hi10P"}
        profile = profile_map.get(format_profile, "")

        video_encode = f"{profile} {codec}".strip() if profile or codec else format
        video_codec = f"MPEG-{track.get('Format_Version')}" if format == "MPEG Video" else format

        log.debug(f"vencode: {video_encode}, vcodec: {video_codec}, enc_set: {has_encode_settings}, bit_depth: {bit_depth}")

        return video_encode, video_codec, has_encode_settings, bit_depth, hfr

    async def get_search_year(self, uuid: str, meta: dict) -> str:
        """Extract search year from cached MediaInfo JSON."""
        mi = self._cache.get(uuid)
        if not mi:
            json_path = Path(self.base_dir) / "tmp" / uuid / "MediaInfo.json"
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    mi = json.load(f)
                    self._cache[uuid] = mi
            else:
                log.error(f"MediaInfo.json not found for uuid: {uuid}")
                return ""

        if not mi or not mi.get("media", {}).get("track"):
            return ""

        year_pattern = r"\b\d{4}\b"
        general_track = next((track for track in mi.get("media", {}).get("track", []) if track.get("@type") == "General"), {})

        for field in ["Movie", "Title"]:
            value = general_track.get(field, "")
            if value:
                matches = re.findall(year_pattern, value.strip())
                if matches:
                    return matches[-1]

        possible_fields = ["DATE", "Released_Date", "First_Air_Date"]
        combined_fields = list(general_track.items()) + list(general_track.get("extra", {}).items())

        for key, value in combined_fields:
            normalized_key = re.sub(r"[_\s]", "", key).lower()
            for target in possible_fields:
                if normalized_key == re.sub(r"[_\s]", "", target).lower():
                    date_value = value.strip()
                    if not date_value:
                        continue
                    try:
                        cleaned_date = re.sub(r"^UTC\s*", "", date_value)
                        parsed_date = datetime.strptime(cleaned_date, "%Y-%m-%d")
                        return str(parsed_date.year)
                    except ValueError:
                        if re.match(r"^\d{4}$", date_value):
                            return date_value
                        matches = re.findall(year_pattern, date_value)
                        if matches:
                            return matches[-1]

        return ""

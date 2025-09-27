import argparse
import datetime
import re
import urllib.parse
from argparse import ArgumentParser, ArgumentTypeError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.console import console


class Choices:
    """Case-Insensitive Choice Validator"""

    def __init__(self, valid_choices: List[str]):
        self.valid_choices = {choice.lower(): choice for choice in valid_choices}
        self.original_choices = valid_choices  # For error message

    def __call__(self, value: str) -> str:
        lower_value = value.lower()
        if lower_value not in self.valid_choices:
            raise ArgumentTypeError(f"invalid choice: '{value}' (choose from {', '.join(self.original_choices)})")
        return value


class Args:
    uuid_pattern = r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config: Dict[str, Any] = config

    def parse(self, args: List[str], meta: Dict[str, Any]) -> Tuple[Dict[str, Any], ArgumentParser, List[str]]:
        """Parse command-line arguments and update the meta dictionary."""
        parser = self._setup_parser()
        parsed_args, before_args = self._parse_arguments(parser, args)
        meta = self._process_arguments(vars(parsed_args), before_args, meta)
        return meta, parser, before_args

    def _setup_parser(self) -> ArgumentParser:
        """Set up the ArgumentParser with all defined arguments."""
        parser = ArgumentParser(description="Command-line arguments for Uploadrr")

        # Core arguments
        parser.add_argument("path", nargs="*", help="Path to file/directory")
        (
            parser.add_argument(
                "-fd",
                "--full-directory",
                dest="full_dir",
                default=False,
                action="store_true",
                help="Uploads folder and all content within",
            ),
        )
        parser.add_argument(
            "-s",
            "--screens",
            type=int,
            required=False,
            help="Number of screenshots",
            default=int(self.config.get("SCREENS", {}).get("screen_count", 3)),
        )
        parser.add_argument("-ih", "--imghost", type=str, default=None, help="Image host")

        # Define Choices
        arg_choices = {
            "category": {
                "flags": ("-c", "--category"),
                "type": Choices(["movie", "tv", "fanres", "sports", "music"]),
                "help": "Category [MOVIE, TV, FANRES, SPORTS, MUSIC]",
            },
            "type": {
                "flags": ("-t", "--type"),
                "type": Choices(["disc", "remux", "encode", "webdl", "web-dl", "webrip", "hdtv"]),
                "help": "Type [DISC, REMUX, ENCODE, WEBDL, WEBRIP, HDTV]",
            },
            "manual_source": {
                "flags": ("--source",),
                "type": Choices(["Blu-ray", "BluRay", "DVD", "HDDVD", "WEB", "HDTV", "UHDTV"]),
                "help": "Source [Blu-ray, BluRay, DVD, HDDVD, WEB, HDTV, UHDTV]",
            },
            "resolution": {
                "flags": ("-res", "--resolution"),
                "type": Choices(["2160p", "1080p", "1080i", "720p", "576p", "576i", "480p", "480i", "8640p", "4320p", "other"]),
                "help": "Resolution [2160p, 1080p, etc.]",
            },
            "piece_size_max": {
                "flags": ("-ps", "--piece-size-max"),
                "type": int,
                "choices": [1, 2, 4, 8, 16],
                "help": "Maximum piece size in MiB",
            },
            "torrent_creation": {
                "flags": ("-tc", "--torrent-creation"),
                "type": Choices(["torf", "torrenttools", "mktorrent"]),
                "help": "Tool to create base .torrent",
                "default": self.config["DEFAULT"].get("torrent_creation", "torf"),
            },
        }

        for name, spec in arg_choices.items():
            parser.add_argument(*spec["flags"], **{k: v for k, v in spec.items() if k != "flags"})

        # ID-related arguments
        parser.add_argument("-tmdb", "--tmdb", nargs="*", type=str, dest="tmdb_manual", help="TMDb ID")
        parser.add_argument("-imdb", "--imdb", nargs="*", type=str, help="IMDb ID")
        parser.add_argument("-mal", "--mal", nargs="*", type=str, help="MAL ID")
        parser.add_argument("-mbid", "--mbid", nargs="?", type=self.validate_mbid, dest="mbid_manual", help="MusicBrainz Release ID")
        parser.add_argument("-discogs", "--discogs", nargs="*", type=str, dest="discogs_id", help="Discogs Release ID")

        # Metadata arguments
        parser.add_argument("-g", "--tag", type=str, help="Group tag")
        parser.add_argument("-serv", "--service", nargs="*", type=str, help="Streaming service")
        parser.add_argument("-dist", "--distributor", nargs="*", type=str, help="Disc distributor (e.g., Criterion, BFI)")
        parser.add_argument(
            "-edition",
            "--edition",
            "--repack",
            nargs="*",
            type=str,
            dest="manual_edition",
            default="",
            help="Edition/Repack string (e.g., Director's Cut, REPACK)",
        )
        parser.add_argument("-season", "--season", nargs="*", type=str, help="Season (number)")
        parser.add_argument("-episode", "--episode", nargs="*", type=str, help="Episode (number)")
        parser.add_argument(
            "-daily", "--daily", nargs=1, type=datetime.date.fromisoformat, dest="manual_date", help="Air date of episode (YYYY-MM-DD)"
        )
        parser.add_argument("-year", "--year", dest="manual_year", nargs="?", type=int, default=0, help="Year")

        # Boolean flags
        boolean_args = [
            ("-a --anon", "anon", "Upload anonymously"),
            ("-debug --debug", "debug", "Debug mode with extra info"),
            ("-ddebug --deep-debug", "deep_debug", "Deep debug even more info, hopefully"),
            ("-dr --draft", "draft", "Send to drafts (BHD)"),
            ("-ffdebug --ffdebug", "ffdebug", "Show ffmpeg info during screenshots"),
            ("-hc --hardcoded-subs", "hardcoded_subs", "Contains hardcoded subs"),
            ("-m --manual", "manual", "Manual mode, returns DDL links"),
            ("-modq --mod-queue", "modq", "Opt into Mod Queue"),
            ("-nfo --nfo", "nfo", "Use .nfo in directory for description"),
            ("-nh --nohash", "nohash", "Don't hash .torrent"),
            ("--no-aka", "no_aka", "Remove AKA from title"),
            ("--no-dub", "no_dub", "Remove Dubbed from title"),
            ("-ns --no-seed", "no_seed", "Do not add torrent to client"),
            ("--no-season", "no_season", "Remove Season from title"),
            ("--no-tag", "no_tag", "Remove Group Tag from title"),
            ("--no-year", "no_year", "Remove Year from title"),
            ("-pr --personal-release", "personalrelease", "Personal Release"),
            ("-random --random", "random", "Randomize queue order"),
            ("-rh --rehash", "rehash", "Force hash .torrent"),
            ("-sdc --skip-dupe-check", "skip_dupe_check", "Skip duplicate check"),
            ("-siu --skip-imagehost-upload", "skip_imghost_upload", "Skip uploading to an image host"),
            ("-st --stream", "stream", "Stream Optimized Upload"),
            ("-vs --vapoursynth", "vapoursynth", "Use VapourSynth for screens"),
            ("-webdv --webdv", "webdv", "Contains a Dolby Vision layer"),
            ("-x --xxx", "xxx", "itssaaa pr0n"),
        ]
        for flags, dest, help_text in boolean_args:
            parser.add_argument(*flags.split(), dest=dest, action="store_true", help=help_text)

        # Other arguments
        parser.add_argument("-ptp", "--ptp", nargs="*", type=str, help="PTP torrent ID/permalink")
        parser.add_argument("-blu", "--blu", nargs="*", type=str, help="BLU torrent ID/link")
        parser.add_argument("-hdb", "--hdb", nargs="*", type=str, help="HDB torrent ID/link")
        parser.add_argument("-d", "--desc", nargs="*", help=r"\"[b]Custom Description[/b]\"")
        parser.add_argument("-pb", "--desclink", nargs="*", help=r"\"https://pastebin.com/URL\"")
        parser.add_argument("-df", "--descfile", nargs="*", help=r"\"\path\to\description.txt\"")
        parser.add_argument(
            "-aid",
            "--auto-insert-desc",
            dest="auto_desc",
            action="store_true",
            default=self.config["AUTO"].get("always_aid", False),
            help="Use existing .txt file for description",
        )
        parser.add_argument("-th", "--torrenthash", nargs="*", help="Torrent hash to reuse")
        parser.add_argument("-k", "--keywords", nargs="*", help="Comma-separated keywords")
        parser.add_argument("-reg", "--region", nargs="*", help="Region for discs")
        parser.add_argument(
            "-mt", "--manual-title", dest="manual_name", nargs="*", type=str, help='Manually override name, e.g., "Full Name"'
        )
        parser.add_argument("-client", "--client", nargs="*", help="Override default torrent client")
        parser.add_argument("-qbt", "--qbit-tag", dest="qbit_tag", nargs="*", help="qBittorrent tag")
        parser.add_argument("-qbc", "--qbit-cat", dest="qbit_cat", nargs="*", help="qBittorrent category")
        parser.add_argument("-rtl", "--rtorrent-label", dest="rtorrent_label", nargs="*", help="rTorrent label")
        parser.add_argument(
            "-tk",
            "--trackers",
            nargs="*",
            help="Space-separated trackers (e.g., blu bhd)",
            default=self.config["TRACKERS"].get("default_trackers", "MANUAL"),
        )
        parser.add_argument("-rt", "--randomized", nargs="*", default=0, help="Number of extra random infohash torrents")
        parser.add_argument("-aq", "--auto-queue", dest="auto_queue", help="Automatically queue files in directory")
        parser.add_argument("-sq", "--show-queue", dest="show_queue", action="store_true", help="Show queued files")
        parser.add_argument("-delay", "--delay", dest="delay", type=int, help="Delay between queued torrents (seconds)")
        parser.add_argument("-ua", "--unattended", action="store_true", required=False, help="Unattended Mode")
        parser.add_argument(
            "-fa",
            "--full-auto",
            dest="full_auto",
            nargs="?",
            const=True,
            default=False,
            type=str,
            help="Compiles Queue, Grabs Desc if Avail, Unnatended",
        )
        parser.add_argument(
            "-q",
            "--quiet",
            action="store_true",
            default=self.config["AUTO"].get("keep_quiet", False),
            help="Quiet mode, live processing + summary only",
        )
        parser.add_argument("-fl", "--freeleech", nargs="*", default=0, dest="freeleech", help="Freeleech percentage")

        return parser

    def _parse_arguments(self, parser: ArgumentParser, args: List[str]) -> Tuple[Any, List[str]]:
        return parser.parse_known_args(args)

    def _process_arguments(self, args: Dict[str, Any], before_args: List[str], meta: Dict[str, Any]) -> Dict[str, Any]:
        """Process parsed arguments and update the meta dictionary."""
        # Handle full-auto mode
        if args.get("full_auto", False):
            args["unattended"] = args["auto_desc"] = True
            args["auto_queue"] = args["full_auto"]

        if not args["path"]:
            valid_paths = [p for p in before_args if Path(p).exists()]
            args["path"] = valid_paths if valid_paths else args["path"]
        else:
            args["path"] = [p for p in args["path"] if Path(p).exists()]

        if not args["path"] and not args.get("auto_queue"):
            console.print("[red]No valid paths provided.[/red]")
            args["path"] = []

        # Reset manual TMDb
        if meta.get("tmdb_manual"):
            meta["tmdb_manual"] = None

        # Process each argument (excluding 'path')
        value_processors = {
            "category": lambda v: v.upper() if v else None,
            "type": lambda v: v.upper().replace("-", "") if v else None,
            "manual_source": lambda v: "BluRay"
            if v.lower() == "bluray"
            else "Blu-ray"
            if v.lower() == "blu-ray"
            else v.upper()
            if v
            else None,
            "resolution": lambda v: v.lower() if v else None,
            "imghost": lambda v: v.lower() if v else None,
            "piece_size_max": lambda v: v,
            "torrent_creation": lambda v: v.lower() if v else None,
            "tag": lambda v: f"-{v}" if isinstance(v, str) else "",
            "screens": int,
            "season": lambda v: ("manual_season", v),
            "episode": lambda v: ("manual_episode", v),
            "manual_date": lambda v: v,
            "tmdb_manual": lambda v: self.parse_tmdb_id(v[0], meta.get("category")) if isinstance(v, list) and v else ("", ""),
            "ptp": lambda v: self._parse_tracker_id(v, "ptp", "torrentid"),
            "blu": lambda v: self._parse_blu_id(v),
            "hdb": lambda v: self._parse_tracker_id(v, "hdb", "id"),
            "trackers": lambda v: v if isinstance(v, list) else [v],
        }

        for key, value in args.items():
            if key == "path":
                continue
            elif value not in (None, []):
                if key in value_processors:
                    result = value_processors[key](value)
                    if isinstance(result, tuple) and len(result) == 2:
                        if key == "tmdb_manual":
                            meta["category"], meta[key] = result
                        else:
                            meta[result[0]] = result[1]
                    else:
                        meta[key] = result
                else:
                    meta[key] = self.list_to_string(value) if isinstance(value, list) else value
            elif key in ("manual_edition", "freeleech"):
                meta[key] = value if key != "freeleech" else 100
            else:
                meta[key] = meta.get(key, None)

        return meta

    @staticmethod
    def validate_mbid(mbid: str) -> str:
        if not re.match(Args.uuid_pattern, mbid):
            raise argparse.ArgumentTypeError("Invalid MBID format. Use a valid UUID without quotes or altered dashes.")
        return mbid

    @staticmethod
    def list_to_string(items: List[Any]) -> str:
        if not items:
            return "None"
        return " ".join(str(item) for item in items)

    def parse_tmdb_id(self, id: str, category: Optional[str]) -> Tuple[str, str]:
        try:
            id = id.strip().lower()
            if "/" in id:
                type_part, id_part = id.split("/", 1)
                if type_part == "tv":
                    return "TV", id_part
                elif type_part == "movie":
                    return "MOVIE", id_part
            return category or "", id
        except Exception:
            console.print(f"[red]Invalid TMDb ID format: {id}")
            return category or "", id

    def _parse_tracker_id(self, value: str, tracker: str, query_param: str) -> str:
        if value.startswith("http"):
            try:
                parsed = urllib.parse.urlparse(value)
                return urllib.parse.parse_qs(parsed.query)[query_param][0]
            except Exception as e:
                console.print(f"[red]Failed to parse {tracker} ID from URL: {value}. Error: {e}")
                console.print(f"[red]Continuing without --{tracker}")
                return value
        return value

    def _parse_blu_id(self, value: str) -> str:
        if value.startswith("http"):
            try:
                parsed = urllib.parse.urlparse(value)
                path = parsed.path.rstrip("/")
                return path.split("/")[-1]
            except Exception as e:
                console.print(f"[red]Unable to parse BLU ID from URL: {value}. Error: {e}")
                console.print("[red]Continuing without --blu")
                return value
        return value

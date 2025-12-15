import argparse
import datetime
import re
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.utils.console import console

# =============================================================================
# 🔑 GLOBAL-ONLY ARGUMENTS
# These *cannot* be specified per-item — only once, globally.
# Keep this list minimal and intentional.
# =============================================================================
GLOBAL_ARGS: Set[str] = {
    # Core control flow
    "full_dir",
    "debug",
    "deep_debug",
    "unattended",
    "quiet",
    "show_queue",
    "auto_queue",
    "delay",
    "full_auto",
    "random",
    "skip_dupe_check",
    "auto_desc",
    # Client/queue control
    "no_seed",
    "client",  # override client — likely global policy
    "qbit_tag",
    "qbit_cat",
    "rtorrent_label",
    # Structural
    "trackers",  # default global; per-item would require complex tracker override logic
}


# =============================================================================
# 🧰 Helper Validators (keep pure & testable)
# =============================================================================
def _validate_mbid(mbid: str) -> str:
    uuid_pattern = r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    if not re.match(uuid_pattern, mbid):
        raise argparse.ArgumentTypeError("Invalid MBID format. Must be a valid UUID (e.g., 83d9...)")
    return mbid


def _validate_porndb_id(value: str) -> str:
    if value.isdigit():
        return value
    try:
        uuid.UUID(value)
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid PornDB ID: '{value}'. Must be digits or UUID.")


def _season_or_episode(value: str) -> Dict[str, str]:
    """
    Parse Sxx, Exx, SxxExx → {"manual_season": "S02", "manual_episode": "E05"}
    """
    value = value.strip().lower()
    # Match optional S## and optional E##
    match = re.match(r"s?(\d+)?\s*e?(\d+)?", value)
    if not match:
        return {"manual_season": value}

    season_num, episode_num = match.groups()
    result = {}
    if season_num:
        result["manual_season"] = f"S{int(season_num):02d}"
    if episode_num:
        result["manual_episode"] = f"E{int(episode_num):02d}"
    return result


# =============================================================================
# 🧹 Normalization & Post-Processing
# (Pure functions — no side effects)
# =============================================================================
def normalize_args(ns: argparse.Namespace) -> Dict[str, Any]:
    """
    Convert parsed args → clean meta dict.
    Handles type coercion, casing, simple defaults.
    """
    meta = vars(ns).copy()
    # Remove dummy placeholder
    meta.pop("dummy_path", None)

    # --- Simple type coercions & defaults ---
    if meta.get("screens") is not None:
        meta["screens"] = int(meta["screens"])

    if meta.get("category"):
        meta["category"] = meta["category"].upper()

    if meta.get("type"):
        if meta.get("manual_type"):
            meta["type"] = meta["manual_type"].upper().replace("-", "")
        meta["type"] = meta["type"].upper().replace("-", "")

    if meta.get("manual_source"):
        src = meta["manual_source"].lower()
        if src == "bluray":
            meta["manual_source"] = "BluRay"
        elif src == "blu-ray":
            meta["manual_source"] = "Blu-ray"
        else:
            meta["manual_source"] = src.upper()

    if meta.get("resolution"):
        meta["resolution"] = meta["resolution"].lower()

    if meta.get("imghost"):
        meta["imghost"] = meta["imghost"].lower()

    if meta.get("torrent_creation"):
        meta["torrent_creation"] = meta["torrent_creation"].lower()

    if meta.get("tag") and isinstance(meta["tag"], str):
        meta["tag"] = f"-{meta['tag']}"

    # --- ID parsing (non-failing, just extract best effort) ---
    if meta.get("tmdb"):
        # tmdb can be list (e.g., ["123"] or ["movie/123"])
        val = meta["tmdb"][0] if meta["tmdb"] else ""
        meta["tmdb_manual"] = parse_tmdb_id(val, meta.get("category"))
        # NOTE: returns (category, id) — handled in apply_special_logic

    if meta.get("ptp"):
        meta["ptp"] = _parse_tracker_id(meta["ptp"][0], "ptp", "torrentid") if meta["ptp"] else ""
    if meta.get("blu"):
        meta["blu"] = _parse_blu_id(meta["blu"][0]) if meta["blu"] else ""
    if meta.get("hdb"):
        meta["hdb"] = _parse_tracker_id(meta["hdb"][0], "hdb", "id") if meta["hdb"] else ""

    if meta.get("trackers"):
        # trackers: str or list[str] → always list
        t = meta["trackers"]
        meta["trackers"] = t if isinstance(t, list) else [t]

    # --- Freeleech: default 100 if flag used without value ---
    if meta.get("freeleech") == []:
        meta["freeleech"] = 100
    elif meta.get("freeleech"):
        meta["freeleech"] = int(meta["freeleech"][0]) if isinstance(meta["freeleech"], list) else int(meta["freeleech"])

    # --- Edition: list → space-joined string ---
    if isinstance(meta.get("manual_edition"), list):
        meta["manual_edition"] = " ".join(meta["manual_edition"]) or ""

    # --- Keywords: list → comma-separated string (as expected by uploaders) ---
    if isinstance(meta.get("keywords"), list):
        meta["keywords"] = ",".join(meta["keywords"])

    return meta


def apply_special_logic(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply higher-level logic that depends on multiple fields or side effects.
    """
    meta = meta.copy()

    # --- XXX detection ---
    xxx_flags = ("xxx", "xscene", "xmovie", "jav", "xpack")
    if any(meta.get(flag) for flag in xxx_flags):
        meta["xxx"] = True
        for flag in xxx_flags:
            if meta.get(flag):
                meta[flag] = True

    # --- Full-auto mode ---
    if meta.get("full_auto"):
        meta["unattended"] = True
        meta["auto_desc"] = True
        meta["auto_queue"] = meta["full_auto"]  # could be str (dir) or True

    # --- Debug mode settings ---
    if meta.get("debug") or meta.get("deep_debug"):
        meta["qbit_cat"] = "DEBUG"
        meta["qbit_tag"] = "DEBUG"
        meta["rtorrent_label"] = "DEBUG"

    # --- TMDB post-processing: extract (category, id) → separate fields ---
    if "tmdb_manual" in meta and isinstance(meta["tmdb_manual"], tuple):
        cat, tid = meta["tmdb_manual"]
        meta["category"] = cat or meta.get("category")
        meta["tmdb_manual"] = tid

    # --- Season/Episode parsing from --season/--episode CLI args ---
    for key in ("season", "episode"):
        if meta.get(key):
            extra = _season_or_episode(meta[key])
            # e.g., {"manual_season": "S02", "manual_episode": "E05"}
            meta.update(extra)
            meta.pop(key, None)  # remove raw 'season'/'episode'

    # --- Manual date: ensure it's a date obj (already done by argparse, but confirm) ---
    if meta.get("manual_date") and not isinstance(meta["manual_date"], datetime.date):
        try:
            meta["manual_date"] = datetime.date.fromisoformat(meta["manual_date"])
        except Exception:
            meta.pop("manual_date", None)

    return meta


# =============================================================================
# 🌐 ID Parsing Helpers (URL → ID extraction)
# =============================================================================
def parse_tmdb_id(id_str: str, fallback_category: Optional[str] = None) -> Tuple[str, str]:
    """Returns (detected_category, id)"""
    try:
        id_str = id_str.strip().lower()
        if "/" in id_str:
            parts = id_str.split("/", 2)
            if len(parts) >= 2:
                type_part, id_part = parts[0], parts[1]
                if type_part == "tv":
                    return "TV", id_part
                elif type_part == "movie":
                    return "MOVIE", id_part
        return fallback_category or "", id_str
    except Exception as e:
        console.print(f"[red]⚠️ Failed to parse TMDb ID '{id_str}': {e}")
        return fallback_category or "", id_str


def _parse_tracker_id(url_or_id: str, tracker: str, query_param: str) -> str:
    if url_or_id.startswith("http"):
        try:
            parsed = urllib.parse.urlparse(url_or_id)
            qs = urllib.parse.parse_qs(parsed.query)
            if query_param in qs:
                return qs[query_param][0]
        except Exception as e:
            console.print(f"[red]⚠️ Failed to parse {tracker} ID from URL: {url_or_id} → {e}")
    return url_or_id


def _parse_blu_id(url_or_id: str) -> str:
    if url_or_id.startswith("http"):
        try:
            path = urllib.parse.urlparse(url_or_id).path.rstrip("/")
            return path.split("/")[-1]
        except Exception as e:
            console.print(f"[red]⚠️ Failed to parse BLU ID from URL: {url_or_id} → {e}")
    return url_or_id


# =============================================================================
# 🛠️ Parser Construction
# =============================================================================
def build_parser(config: Dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Uploadrr — Upload media with rich metadata", add_help=True, allow_abbrev=False)
    # Dummy to absorb positional args (will be stripped later)
    parser.add_argument("dummy_path", nargs="*", help=argparse.SUPPRESS)

    # ========================
    # 🌍 GLOBAL-ONLY Arguments
    # (Enforced after parsing via `enforce_global_only`)
    # ========================
    parser.add_argument("-fd", "--full-dir", action="store_true", help="Upload folder and all content within")
    parser.add_argument("-D", "--debug", action="store_true", default=False, help="Enable debug logging")
    parser.add_argument("-DD", "--deep-debug", dest="deep_debug", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("-ua", "--unattended", action="store_true", help="Run without interactive prompts")
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=config.get("AUTO", {}).get("keep_quiet", False),
        help="Quiet mode: only show summary and errors",
    )
    parser.add_argument("-sq", "--show-queue", action="store_true", help="Show queued files before processing")
    parser.add_argument("-aq", "--auto-queue", metavar="DIR", help="Automatically queue all items in DIR")
    parser.add_argument("-delay", "--delay", type=int, help="Delay between uploads (seconds)")
    parser.add_argument(
        "-fa",
        "--full-auto",
        nargs="?",
        const=True,
        default=False,
        help="Full auto: queue, grab desc, unattended (optionally specify dir)",
    )
    parser.add_argument("-random", "--random", action="store_true", help="Randomize upload order")
    parser.add_argument("-sdc", "--skip-dupe-check", action="store_true", help="Skip duplicate checking")
    parser.add_argument("-ns", "--no-seed", action="store_true", help="Do not add torrent to client")
    parser.add_argument("-client", "--client", help="Override default torrent client")
    parser.add_argument("-qbt", "--qbit-tag", dest="qbit_tag", help="qBittorrent tag")
    parser.add_argument("-qbc", "--qbit-cat", dest="qbit_cat", help="qBittorrent category")
    parser.add_argument("-rtl", "--rtorrent-label", dest="rtorrent_label", help="rTorrent label")
    parser.add_argument(
        "-tk",
        "--trackers",
        nargs="*",
        default=config.get("TRACKERS", {}).get("default_trackers", ["MANUAL"]),
        help="Trackers to upload to (e.g., blu bhd)",
    )
    parser.add_argument(
        "-aid",
        "--auto-insert-desc",
        dest="auto_desc",
        action="store_true",
        default=config.get("AUTO", {}).get("always_aid", False),
        help="Use existing .txt file for description",
    )

    # ========================
    # 🎯 Per-Item or Global Arguments
    # ========================
    parser.add_argument(
        "-s", "--screens", type=int, default=int(config.get("SCREENS", {}).get("screen_count", 3)), help="Number of screenshots"
    )
    parser.add_argument("-ih", "--imghost", type=str, default=None, help="Image hosting service")

    # --- Choices ---
    parser.add_argument("-c", "--category", choices=["movie", "tv", "fanres", "sports", "music"], help="Content category")
    parser.add_argument(
        "-t",
        "--type",
        dest="manual_type",
        choices=["disc", "remux", "encode", "webdl", "web-dl", "webrip", "hdtv"],
        help="Release type",
    )
    parser.add_argument(
        "--source", dest="manual_source", choices=["Blu-ray", "BluRay", "DVD", "HDDVD", "WEB", "HDTV", "UHDTV"], help="Source media"
    )
    parser.add_argument(
        "-res",
        "--resolution",
        choices=["2160p", "1080p", "1080i", "720p", "576p", "576i", "480p", "480i", "8640p", "4320p", "other"],
        help="Video resolution",
    )
    parser.add_argument("-ps", "--piece-size-max", type=int, choices=[1, 2, 4, 8, 16], help="Max piece size (MiB)")
    parser.add_argument(
        "-tc",
        "--torrent-creation",
        choices=["torf", "torrenttools", "mktorrent"],
        default=config.get("DEFAULT", {}).get("torrent_creation", "torf"),
        help="Torrent creation tool",
    )

    # --- IDs ---
    parser.add_argument("-tmdb", "--tmdb", nargs="*", help="TMDb ID (e.g., 123 or movie/123)")
    parser.add_argument("-imdb", "--imdb", nargs="*", help="IMDb ID")
    parser.add_argument("-mal", "--mal", nargs="*", help="MyAnimeList ID")
    parser.add_argument("-mbid", "--mbid", type=_validate_mbid, dest="mbid_manual", help="MusicBrainz Release ID (UUID)")
    parser.add_argument("-discogs", "--discogs", nargs="*", dest="discogs_id", help="Discogs Release ID")
    parser.add_argument("-deezer", "--deezer", type=int, dest="deezer_manual", help="Deezer Album ID")
    parser.add_argument("-tpdb", "--tpdb", type=_validate_porndb_id, dest="pdb_manual", help="ThePornDB ID or UUID")

    # --- Metadata ---
    parser.add_argument("-g", "--tag", help="Group tag (e.g., RARBG)")
    parser.add_argument("-serv", "--service", nargs="*", help="Streaming service (e.g., Netflix, Hulu)")
    parser.add_argument("-dist", "--distributor", nargs="*", help="Disc distributor (e.g., Criterion)")
    parser.add_argument(
        "-edition", "--edition", "--repack", nargs="*", dest="manual_edition", help="Edition string (e.g., Director's Cut, REPACK)"
    )
    parser.add_argument("-season", "--season", help="Season (e.g., 2 or S02)")
    parser.add_argument("-episode", "--episode", help="Episode (e.g., 5 or E05)")
    parser.add_argument("-daily", "--daily", dest="manual_date", type=datetime.date.fromisoformat, help="Air date (YYYY-MM-DD)")
    parser.add_argument("-year", "--year", dest="manual_year", type=int, default=0, help="Release year")

    # --- Boolean flags (per-item) ---
    bool_flags = [
        ("-a", "--anon", "anon", "Upload anonymously"),
        ("-dr", "--draft", "draft", "Send to drafts (BHD)"),
        ("-ffdebug", "--ffdebug", "ffdebug", "Show ffmpeg debug during screenshots"),
        ("-hc", "--hardcoded-subs", "hardcoded_subs", "Contains hardcoded subtitles"),
        ("-m", "--manual", "manual", "Manual mode (returns DDL links)"),
        ("-modq", "--mod-queue", "modq", "Opt into Mod Queue"),
        ("-nfo", "--nfo", "nfo", "Use .nfo file for description"),
        ("-nh", "--nohash", "nohash", "Skip hashing"),
        ("-noaka", "--no-aka", "no_aka", "Remove AKA from title"),
        ("-nodub", "--no-dub", "no_dub", "Remove 'Dubbed' from title"),
        ("-noszn", "--no-season", "no_season", "Remove season from title"),
        ("-g0", "--no-tag", "no_tag", "Remove group tag from title"),
        ("-ny", "--no-year", "no_year", "Remove year from title"),
        ("-pr", "--personal-release", "personalrelease", "Mark as personal release"),
        ("-rh", "--rehash", "rehash", "Force rehash"),
        ("-siu", "--skip-imagehost-upload", "skip_imghost_upload", "Skip image upload"),
        ("-st", "--stream", "stream", "Stream-optimized encoding"),
        ("-vs", "--vapoursynth", "vapoursynth", "Use VapourSynth for screenshots"),
        ("-webdv", "--webdv", "webdv", "Contains Dolby Vision layer"),
        ("-x", "--xxx", "xxx", "XXX content"),
        ("-xs", "--xscene", "xscene", "XXX Scene mode"),
        ("-xm", "--xmovie", "xmovie", "XXX Movie mode"),
        ("-jav", "--jav", "jav", "JAV content"),
        ("-xp", "--pack", "xpack", "XXX Pack (mini/mega)"),
    ]
    for short, long, dest, help_text in bool_flags:
        parser.add_argument(short, long, dest=dest, action="store_true", help=help_text)

    # --- Paths & Media ---
    parser.add_argument("-cv", "--cover", dest="user_cover", help="Cover image (path/URL)")
    parser.add_argument("-b", "--banner", dest="user_banner", help="Banner image (path/URL)")
    parser.add_argument("-trailer", "--trailer", dest="user_trailer", help="Trailer file/url")

    # --- Descriptions ---
    parser.add_argument("-d", "--desc", nargs="*", help='Custom description (e.g., "[b]Hi![/b]")')
    parser.add_argument("-pb", "--desclink", help="Pastebin URL")
    parser.add_argument("-df", "--descfile", help="Local description file")

    # --- Other ---
    parser.add_argument("-ptp", "--ptp", nargs="*", help="PTP torrent ID or URL")
    parser.add_argument("-blu", "--blu", nargs="*", help="BLU torrent ID or URL")
    parser.add_argument("-hdb", "--hdb", nargs="*", help="HDB torrent ID or URL")
    parser.add_argument("-th", "--torrenthash", nargs="*", help="Reuse existing torrent hash")
    parser.add_argument("-k", "--keywords", nargs="*", help="Comma-separated keywords")
    parser.add_argument("-reg", "--region", nargs="*", help="Region (for discs)")
    parser.add_argument("-mt", "--manual-title", dest="manual_name", nargs="*", help="Override generated name")
    parser.add_argument("-rt", "--randomized", nargs="*", default=0, help="Number of extra randomized torrents")
    parser.add_argument("-fl", "--freeleech", nargs="*", help="Freeleech percentage (default 100 if flag used alone)")

    return parser


# =============================================================================
# 🔒 Scoping Enforcement
# =============================================================================
def enforce_global_only(ns: argparse.Namespace, is_global: bool, parser: argparse.ArgumentParser) -> None:
    """
    Raises ValueError if a global-only arg is used in a per-item context.
    Ignores default values from config/parser.
    """
    if is_global:
        return

    for arg in GLOBAL_ARGS:
        if hasattr(ns, arg):
            value = getattr(ns, arg)
            default = parser.get_default(arg)
            if value != default:
                flag = arg.replace("_", "-")
                raise ValueError(f"Argument --{flag} is global-only and cannot be used per-item. Move it to the end of the command.")


def group_args_by_path(argv: List[str]) -> Tuple[List[Tuple[List[str], str]], List[str]]:
    """
    Grouping:
    - Only treat a token as a path if:
        a) it doesn't start with '-', AND
        b) it exists as a file/directory, AND
        c) it's NOT consumed by a preceding flag

    Strategy: collect args until we hit a *non-flag, non-consumed* token that is a path.
    We assume most paths come *first* or after `--`.
    """
    items: List[Tuple[List[str], str]] = []
    current_block: List[str] = []
    i = 0
    n = len(argv)

    # Common flags that take path-like args (so their values shouldn't be items)
    PATH_FLAGS = {"--cover", "-cv", "--banner", "-bn", "--trailer", "--descfile", "-df", "--desclink"}

    while i < n:
        arg = argv[i]

        if arg == "--":
            # Explicit separator: everything after is path
            i += 1
            while i < n:
                p = Path(argv[i])
                if p.exists():
                    items.append((current_block.copy(), str(p.resolve())))
                    current_block.clear()
                else:
                    console.print(f"[yellow]⚠️ Non-existent path after '--': {argv[i]}[/yellow]")
                i += 1
            break

        if arg.startswith("-"):
            current_block.append(arg)
            i += 1
            # Look ahead: if next token exists and is not a flag,
            # and this flag is in PATH_FLAGS or likely takes a value, consume it
            if i < n and not argv[i].startswith("-"):
                # Heuristic: if flag is known to take a path, or value looks like a path
                next_val = argv[i]
                next_path = Path(next_val)
                if arg in PATH_FLAGS or next_path.exists() or next_val.lower().endswith((".png", ".jpg", ".mp4", ".txt", ".webp")):
                    current_block.append(next_val)
                    i += 1
            continue

        # Non-flag: likely a path
        p = Path(arg)
        if p.exists() or (i == 0 and not items):  # first non-flag is likely main path
            items.append((current_block.copy(), str(p.resolve() if p.exists() else p)))
            current_block.clear()
        else:
            # Unknown — keep in block (e.g., bare words like "skip" in edit)
            current_block.append(arg)
        i += 1

    return items, current_block


# =============================================================================
# 📦 Exported Interface
# =============================================================================
__all__ = ["build_parser", "normalize_args", "apply_special_logic", "enforce_global_only", "group_args_by_path", "GLOBAL_ARGS"]

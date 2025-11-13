import re
from difflib import SequenceMatcher
from typing import Dict, List, Tuple

from rich.prompt import Confirm

from src.progress import ProgressManager
from src.trackers.COMMON import COMMON
from src.utils.console import console


async def pre_upload_checks(
    meta: Dict, trackers: List[str], tracker_registry: Dict, skipped_details: List[Tuple], path: str, config: Dict
) -> Dict[str, bool]:
    """Perform pre-upload checks and return tracker-specific upload decisions."""
    common = COMMON(config=config)
    tracker_upload_ok = {tracker: True for tracker in trackers}
    progress = ProgressManager.get_instance()
    progress.update("Pre-Upload Checks..")

    if meta.get("skip_dupe_check", False):
        console.print("[yellow]Skipping dupe check..")

    for tracker in trackers:
        if tracker not in tracker_registry and tracker != "MANUAL":
            skipped_details.append((path, f"No such tracker: {tracker}"))
            tracker_upload_ok[tracker] = False
            continue

        tracker_instance = tracker_registry[tracker]["class"](config=config) if tracker != "MANUAL" else None
        if tracker == "MANUAL":
            continue

        if hasattr(tracker_instance, "banned_groups") and check_banned_group(
            tracker, tracker_instance.banned_groups, meta, skipped_details, path
        ):
            skipped_details.append((path, f"Banned group on {tracker}"))
            tracker_upload_ok[tracker] = False
            continue

        if hasattr(tracker_instance, "banned_content"):
            result = await tracker_instance.banned_content(meta)
            is_content_banned = result if isinstance(result, bool) else result[0]
            if is_content_banned:
                skipped_details.append((path, f"Banned content on {tracker}"))
                tracker_upload_ok[tracker] = False
                continue

        if not meta.get("skip_dupe_check", False):
            dupes = await tracker_instance.search_existing(meta)
            if not meta.get("is_music", False) and not meta["xxx"]:
                dupes = await common.filter_dupes(dupes, meta)
            meta, skipped = await dupe_check(dupes, meta, config, skipped_details, path)
            if skipped:
                skipped_details.append((path, f"Potential duplicate on {tracker}"))
                tracker_upload_ok[tracker] = False
                continue

    return tracker_upload_ok


async def dupe_check(dupes: Dict, meta: Dict, config: Dict, skipped_details: List[Tuple], path: str) -> Tuple[Dict, bool]:
    """Check for duplicates and decide if upload should proceed."""
    from rich.table import Table

    progress = ProgressManager.get_instance()
    progress.update("Dupe Check..")

    if meta.get("skip_dupe_check", False):
        console.print("[yellow]Skipping duplicate check as requested.")
        meta["upload"] = True
        return meta, False

    if not dupes:
        console.print("[green]No dupes found")
        meta["upload"] = True
        return meta, False

    if not meta.get("unattended", False):
        table = Table(title="Are these dupes?", show_header=True, header_style="bold underline", expand=True, box=None)
        table.add_column("Name")
        table.add_column("Size", justify="center")
        for name, size in dupes.items():
            size_gb = f"{round(int(size) / (1024**3), 2)} GB" if isinstance(size, (int, float)) and size > 0 else "N/A"
            table.add_row(name, f"[magenta]{size_gb}[/magenta]")
        console.print(table)

    similarity_threshold = max(config.get("AUTO", {}).get("dupe_similarity", 90.00) / 100, 0.70)
    size_tolerance = max(min(config.get("AUTO", {}).get("size_tolerance", 30), 100), 1) / 100
    cleaned_meta_name = re.sub(r"[^\w\s]", "", meta["clean_name"].lower())

    meta_size = meta.get("content_size", 0)
    if not meta.get("rehash"):
        from .torrentman import extract_size_from_torrent

        torrent_size = extract_size_from_torrent(meta["base_dir"], meta["uuid"])
        if torrent_size > 0:
            meta_size = torrent_size
            meta["content_size"] = torrent_size

    for name, size in dupes.items():
        if not isinstance(size, (int, float)) or size <= 0:
            continue
        if meta_size == size:
            console.print("[red]Exact size match. Aborting..")
            return meta, True

        similarity = SequenceMatcher(None, cleaned_meta_name, re.sub(r"[^\w\s]", "", name.lower())).ratio()
        if similarity >= similarity_threshold and abs(meta_size - size) <= size_tolerance * meta_size:
            with progress.pause_for_interaction():
                if meta.get("unattended", False) or not Confirm.ask(" Upload anyways?"):
                    meta["upload"] = False
                    return meta, True

    meta["upload"] = True
    return meta, False


def check_banned_group(tracker: str, banned_group_list: Dict, meta: Dict, skipped_details: List[Tuple], path: str) -> bool:
    """Check if release group is banned on tracker."""
    progress = ProgressManager.get_instance()
    group_tag = meta["tag"][1:] if meta.get("tag") else ""
    if not group_tag:
        return False

    release_type = meta.get("type", "")

    if isinstance(banned_group_list, list):
        banned_group_list = {group.lower(): ["ALL"] for group in banned_group_list if group}

    all_banned = [g.lower() for g in banned_group_list.get("ALL_TYPES", [])]
    if group_tag.lower() in all_banned:
        console.print(
            f"[bold yellow]{group_tag}[/bold yellow][bold red] was found on [bold yellow]{tracker}'s[/bold yellow] list of banned groups."
        )
        with progress.pause_for_interaction():
            if meta.get("unattended", False) or not Confirm.ask("[bold red] Upload Anyways?"):
                return True
        return False

    banned_types = banned_group_list.get(group_tag, None)
    if banned_types is not None:
        if not banned_types or "ALL" in banned_types:
            console.print(
                f"[bold yellow]{group_tag}[/bold yellow][bold red] was found on [bold yellow]{tracker}'s[/bold yellow] list of banned groups."
            )
            if meta.get("unattended", False) or not Confirm.ask("[bold red] Upload Anyways?"):
                return True
            return False
        elif release_type and release_type in banned_types:
            console.print(
                f"[bold yellow]{group_tag}[/bold yellow][bold red] was found on [bold yellow]{tracker}'s[/bold yellow] list of banned groups for type [bold yellow]{release_type}[/bold yellow]."
            )
            if meta.get("unattended", False) or not Confirm.ask("[bold red] Upload Anyways?"):
                return True
            return False

    return False

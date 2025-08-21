import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.prompt import Confirm

from src.clients import Clients
from src.manual import ManualPackager
from src.prep import Prep
from src.progress import ProgressManager
from src.trackers.COMMON import COMMON
from src.utils.console import console, log

from .meta import get_missing
from .ui import build_db_info


async def upload_to_trackers(
    meta: Dict,
    trackers: List[str],
    tracker_registry: Dict,
    successful_uploads: int,
    skipped_files: int,
    skipped_details: List[Tuple],
    path: str,
    progress: ProgressManager,
    client: Clients,
    config: Dict,
) -> Tuple[bool, int]:
    """Upload to trackers using finalized meta."""
    debug = meta.get("debug", False)
    log.debug("inside upload_to_trackers")
    success = False
    for tracker in trackers:
        if tracker == "MANUAL":
            tracker_instance = None
            tracker_type = "MANUAL"
        else:
            tracker_instance = tracker_registry[tracker]["class"](config=config)
            tracker_type = tracker_registry[tracker].get("type")

        if tracker == "MANUAL":
            if await handle_manual_upload(meta, trackers, tracker_registry, config):
                success = True
            else:
                skipped_files += 1
                skipped_details.append((path, "Manual upload skipped"))
        elif tracker_type in ["api", "http", "unit3d"]:
            log.debug("about to upload_to_tracker")
            tracker_success, skipped_files = await upload_to_tracker(
                tracker_instance, tracker, meta, config, client, skipped_files, skipped_details, path, debug
            )
            if tracker_success:
                success = True
        else:
            tracker_success = await handle_special_tracker(
                tracker, tracker_instance, meta, path, skipped_details, skipped_files, config
            )
            if tracker_success:
                success = True

    return success, skipped_files


async def handle_manual_upload(meta: Dict, trackers: List[str], tracker_registry: Dict, config: Dict) -> bool:
    """Handle manual upload packaging."""
    if not (meta.get("unattended", False) or Confirm.ask("Get files for manual upload?", default=True)):
        return False
    packager = ManualPackager(config, Prep(config=config, base_dir=Path(meta["base_dir"])).img_host, Path(meta["base_dir"]))
    common = COMMON(config=config)
    for tracker in trackers:
        if tracker != "MANUAL" and tracker in tracker_registry:
            instance = tracker_registry[tracker]["class"](config=config)
            if tracker_registry[tracker]["type"] in ["api", "unit3d"]:
                await common.unit3d_edit_desc(meta, instance.tracker, instance.signature)
            elif hasattr(instance, "edit_desc"):
                await instance.edit_desc(meta)
    url = await packager.package(meta)
    uuid = f"tmp/{meta['uuid']}"
    console.print(f"[green]{meta['name']} Files at: [yellow]{url or uuid}[/yellow]")
    return bool(url)


async def handle_special_tracker(
    tracker: str, tracker_instance, meta: Dict, path: str, skipped_details: List[Tuple], skipped_files: int, config: Dict
) -> bool:
    """Handle special tracker uploads."""
    debug = "(DEBUG)" if meta.get("debug") else ""
    upload_prompt = f"Upload to {tracker}? {debug}"
    if not (meta.get("unattended", False) or Confirm.ask(upload_prompt)):
        skipped_details.append((path, f"User skipped {tracker}"))
        return False

    if tracker == "AR":
        return await upload_to_ar(tracker_instance, meta, path, skipped_details, config)
    elif tracker in ["BHD", "DC"]:
        success, skipped_files = await upload_to_tracker(
            tracker_instance,
            tracker,
            meta,
            config,
            Clients(config=config),
            skipped_files,
            skipped_details,
            path,
            meta.get("debug", False),
        )
        return success
    elif tracker in ["THR", "PTP", "TL"]:
        console.print(f"Apologies, {tracker} hasn't been implemented yet.")
        return False

    return False


async def upload_to_tracker(
    tracker_instance,
    tracker_name: str,
    meta: Dict,
    config: Dict,
    client: Clients,
    skipped_files: int,
    skipped_details: List[Tuple],
    path: str,
    debug: bool,
) -> Tuple[bool, int]:
    """Generic tracker upload method after pre-checks."""
    console.print(f"Uploading to {tracker_name} {'[dim red](DEBUG)' if debug else ''}")

    result = await tracker_instance.upload(meta)
    if result is True:
        if client:
            await client.add_to_client(meta, tracker_name)
        return True, skipped_files
    elif result == "DEBUG":
        if client:
            await client.add_to_client(meta, tracker_name)
        skipped_files += 1
        skipped_details.append((path, f"{tracker_name} (DEBUG)"))
        return False, skipped_files
    else:
        skipped_files += 1
        skipped_details.append((path, f"{tracker_name} Rejected Upload"))
        return False, skipped_files


async def upload_to_ar(ar, meta: Dict, path: str, skipped_details: List[Tuple], config: Dict) -> bool:
    """Specific upload logic for AlphaRatio."""
    from src.progress import ProgressManager

    from .checks import check_banned_group, dupe_check

    if check_banned_group("AR", ar.banned_groups, meta, skipped_details, path):
        return False
    if not await ar.validate_credentials(meta):
        return False
    if not meta.get("skip_dupe_check", False):
        dupes = await ar.search_existing(meta)
        common = COMMON(config=config)
        dupes = await common.filter_dupes(dupes, meta)
        meta, skipped = await dupe_check(dupes, meta, config, skipped_details, path, ProgressManager(console))
        if skipped or not meta["upload"]:
            return False
    await ar.upload(meta)
    await asyncio.sleep(5)
    client = Clients(config=config)
    await client.add_to_client(meta, "AR")
    if ar.session:
        await ar.close_session()
    return True


async def get_confirmation(meta: Dict, trackers: Optional[List[str]], tracker_registry: Optional[Dict], config: Dict) -> bool:
    """Confirm upload details with the user."""
    console.print(f"Prep material saved to {meta['base_dir']}/tmp/{meta['uuid']}")

    db_info = build_db_info(meta)
    from rich.panel import Panel

    console.print(Panel("\n".join(db_info), title="Database Info", border_style="bold yellow"))

    tag = f" / {meta['tag'][1:]}" if meta.get("tag", "") else ""
    res = meta["source"] if meta.get("is_disc") == "DVD" else meta["resolution"]
    from rich.text import Text

    console.print(Text(f" {res} / {meta['type']}{tag}", style="bold"))
    if meta.get("personal_release", False):
        console.print("[bright_magenta]Personal Release!")

    display_names = []
    if trackers and tracker_registry:
        for tracker in trackers:
            tracker_class = tracker_registry.get(tracker, {}).get("class")
            if not tracker_class:
                continue
            tracker_obj = tracker_class(config=config)
            if hasattr(tracker_obj, "edit_name"):
                name = await tracker_obj.edit_name(meta)
                if name:
                    display_names.append(f"[bold green1]{tracker}[/bold green1]: {name}")
                else:
                    display_names.append(f"[bold green1]{tracker}[/bold green1]: {meta['name']}")
            else:
                display_names.append(f"[bold green1]{tracker}[/bold green1]: {meta['name']}")

    if display_names:
        if len(display_names) == 1:
            console.print(f"{display_names[0]}", force=True)
        else:
            console.print("[bold]NAME[/bold]:", force=True)
            for name in display_names:
                console.print(f" • {name}", force=True)
    else:
        console.print(f"[bold]Name[/bold]: {meta['name']}", force=True)

    if not meta.get("unattended", False):
        if not meta.get("is_sports"):
            get_missing(meta)
        bell = "\a" if config.get("DEFAULT", {}).get("sfx_on_prompt", True) else ""
        console.print(f"[bold yellow]Is this correct?{bell}", force=True)
        from rich.prompt import Confirm

        return Confirm.ask(" Correct?", console=console)
    return True

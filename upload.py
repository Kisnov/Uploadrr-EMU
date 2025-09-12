import asyncio
import sys
import time
from pathlib import Path
from typing import Dict, List

import src.utils.imports  # noqa: F401
from src.args import Args
from src.cleanrr import CleanupHandler
from src.clients import Clients
from src.exceptions import BDInfoExtractionError
from src.prep import Prep
from src.progress import ProgressManager
from src.screenshots.manager import ScreenshotManager
from src.utils.checks import pre_upload_checks
from src.utils.config import check_python_version, load_yaml_config
from src.utils.console import console, log, set_log_level
from src.utils.loads import load_trackers
from src.utils.meta import edit_meta, load_existing_meta, save_meta
from src.utils.queue import apply_delay, normalize_trackers, prepare_queue
from src.utils.torrentman import handle_torrent_creation
from src.utils.ui import print_banner, print_summary
from src.utils.upload import get_confirmation, upload_to_trackers

# Globals
python_cmd = sys.executable
base_dir = Path(__file__).resolve().parent
data_dir = base_dir / "data"
config_path = data_dir / "config.yaml"
legacy_config = data_dir / "config.py"
old_config_path = data_dir / "backup" / "old_config.py"


class Uploadrr:
    def __init__(self, config: Dict):
        self.config = config
        self.handler = CleanupHandler(name="Uploadrr")
        self.client = Clients(config=self.config)
        self.parser = Args(config=self.config)

    async def do_the_thing(self, base_dir: Path) -> None:
        """Main upload orchestration method."""
        print_banner()
        meta = {"base_dir": str(base_dir)}
        meta, _, _ = self.parser.parse(sys.argv[1:], meta)  # Note: parser returns meta, parser, _
        set_log_level(debug=meta.get("debug", False), ddebug=meta.get("deep_debug", False), quiet=meta.get("quiet", False))

        trackers = normalize_trackers(meta)
        tracker_registry = load_trackers(trackers, self.config)
        if not any(t in tracker_registry or t == "MANUAL" for t in trackers):
            console.print(f"[bold red]No valid trackers: {', '.join(trackers)}[/bold red]", justify="center")
            sys.exit(1)

        queue = prepare_queue(meta, sys.argv[1:])  # Adjusted to pass sys.argv[1:] for paths
        if not queue:
            sys.exit(0)

        try:
            await self._process_queue(queue, meta, trackers, tracker_registry, base_dir)
        except KeyboardInterrupt:
            raise

    async def _process_queue(
        self, queue: List[str], base_meta: Dict, trackers: List[str], tracker_registry: Dict, base_dir: Path
    ) -> None:
        log.info("[yellow]Starting queue processing[/yellow]")
        total_files = len(queue)
        successful_uploads = 0
        skipped_files = 0
        skipped_details = []
        skipped_tmdb_files = []

        screenshot_manager = ScreenshotManager(config=self.config, base_dir=base_dir)
        self.screenshot_manager = screenshot_manager

        progress = ProgressManager(console, quiet=base_meta.get("quiet", False), unattended=base_meta.get("unattended", False))
        progress.start(total_files)

        try:
            for i, path in enumerate(queue, 1):
                item_path = Path(path).name
                progress.update(f"Processing {item_path} ({i}/{total_files})")

                meta = dict(base_meta)
                meta["path"] = path
                meta["uuid"] = meta.get("uuid") or item_path

                meta = await load_existing_meta(base_dir, path, meta)
                await apply_delay(meta, self.config)
                prep = Prep(config=self.config, base_dir=base_dir)
                try:
                    meta = await prep.gather_prep(meta=meta, mode="cli")
                except BDInfoExtractionError as e:
                    log.warning(f"[yellow]Skipping due to prep error: {e}[/yellow]")
                    progress.update(f"Processing {item_path}: Skipped (Prep error)")
                    skipped_files += 1
                    skipped_details.append((path, "Prep error"))
                    continue

                await save_meta(meta, base_dir, meta["uuid"])

                if meta.get("tmdb_not_found"):
                    progress.update(f"Processing {item_path}: Skipped (TMDB not found)")
                    skipped_files += 1
                    skipped_details.append((path, "TMDB not found"))
                    skipped_tmdb_files.append(path)  # Collect for summary
                    continue

                tracker_upload_ok = await pre_upload_checks(
                    meta, trackers, tracker_registry, skipped_details, path, progress, self.config
                )
                if not any(tracker_upload_ok.values()):
                    progress.update(f"Processing {item_path}: Skipped (pre-upload checks failed)")
                    skipped_files += 1
                    skipped_details.append((path, "Pre-upload checks failed"))
                    continue

                viable_trackers = [t for t, ok in tracker_upload_ok.items() if ok]
                tracker_whitelists = {
                    t: getattr(tracker_registry[t]["class"](config=self.config), "allowed_imghosts", [])
                    for t in viable_trackers
                    if t in tracker_registry
                }

                if int(meta.get("screens", 0)) > 0 or len(meta.get("user_images", [])) > 0:
                    meta = await screenshot_manager.handle_screenshots(meta, tracker_whitelists)
                    await save_meta(meta, base_dir, meta["uuid"])

                meta = await handle_torrent_creation(self.config, meta)

                if not meta.get("unattended"):
                    with progress.pause_for_interaction():
                        confirm = await get_confirmation(meta, viable_trackers, tracker_registry, self.config)
                        if not confirm:
                            meta = await edit_meta(prep, meta, viable_trackers, tracker_registry, self.config)
                        if not meta:
                            progress.update(f"Processing {Path(path).name}: Skipped (user cancelled)")
                            skipped_files += 1
                            skipped_details.append((path, "User cancelled"))
                            continue
                else:
                    console.print(f"[yellow]Unattended mode: Skipping confirmation for {path}[/yellow]")

                success, skip = await upload_to_trackers(
                    meta,
                    trackers,
                    tracker_registry,
                    successful_uploads,
                    skipped_files,
                    skipped_details,
                    path,
                    progress,
                    self.client,
                    self.config,
                )
                successful_uploads += 1 if success else 0
                skipped_files += 1 if not success else 0

                progress.update(f"Processing {Path(path).name}: {'Uploaded' if success else 'Skipped'}")

        except KeyboardInterrupt:
            progress.update("Cancelling...")
            raise
        finally:
            await save_meta(meta, base_dir, meta["uuid"])
            progress.stop()
        print_summary(total_files, successful_uploads, skipped_files, skipped_details, skipped_tmdb_files)

    async def shutdown(self) -> None:
        """Clean up resources on shutdown."""
        await self.handler.shutdown()


async def main() -> None:
    start_time = time.perf_counter()
    check_python_version()
    if not config_path.exists():
        if legacy_config.exists():
            log.error("Legacy Config detected, please create a new yaml config, thank you!")
            sys.exit(1)
        else:
            log.error("No Config detected, please setup config before starting, thank you!")
            sys.exit(1)
    config = load_yaml_config(config_path)
    uploadrr = Uploadrr(config)
    try:
        await uploadrr.do_the_thing(base_dir)
    except KeyboardInterrupt:
        console.print("[bold red]Interrupted by user. Cleaning up...[/bold red]")
        await uploadrr.shutdown()
        sys.exit(1)
    finally:
        end_time = time.perf_counter()
        console.print("[magenta]see you soon ^.<", justify="center")
        log.debug(f"Uploadrr v2 took {end_time - start_time:.4f} seconds too long.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())

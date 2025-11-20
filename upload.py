#!/usr/bin/env python
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import src.utils.imports  # noqa: F401
from src.args import apply_special_logic, build_parser, enforce_global_only, group_args_by_path, normalize_args
from src.cleanrr import CleanupHandler
from src.clients import Clients
from src.exceptions import BDInfoExtractionError
from src.prep import Prep
from src.progress import ProgressManager
from src.screenshots.manager import ScreenshotManager
from src.utils.checks import pre_upload_checks
from src.utils.config import check_python_version, load_yaml_config
from src.utils.console import console, log, redactor, set_log_level
from src.utils.loads import load_trackers
from src.utils.meta import edit_meta, load_existing_meta, save_meta
from src.utils.queue import apply_delay, expand_auto_queue, normalize_trackers
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

    async def do_the_thing(self, base_dir: Path) -> None:
        print_banner()
        base_meta: Dict[str, Any] = {"base_dir": str(base_dir)}
        raw_argv = sys.argv[1:]

        # =====================================================================
        # 1. EARLY EXPANSION: --auto-queue → inject paths
        # =====================================================================
        expanded_argv = expand_auto_queue(raw_argv)

        # =====================================================================
        # 2. GROUP CLI: per-item blocks + global trailing args
        # =====================================================================
        items, global_argv = group_args_by_path(expanded_argv)

        if not items:
            console.print("[red]❌ No valid paths to process.[/red]")
            sys.exit(1)

        # =====================================================================
        # 3. BUILD PARSER
        # =====================================================================
        parser = build_parser(self.config)

        # =====================================================================
        # 4. PARSE GLOBAL ARGS
        # =====================================================================
        try:
            global_ns = parser.parse_args(global_argv)
            enforce_global_only(global_ns, is_global=True, parser=parser)
        except SystemExit:
            # argparse calls sys.exit(2) on error — catch and re-raise cleanly
            console.print("[red]❌ Error parsing global arguments.[/red]")
            sys.exit(1)
        except Exception as e:
            console.print(f"[red]❌ Global arg parsing failed: {e}[/red]")
            sys.exit(1)

        global_meta = normalize_args(global_ns)
        global_meta = apply_special_logic({**base_meta, **global_meta})

        # =====================================================================
        # 5. PARSE & MERGE PER-ITEM ARGS
        # =====================================================================
        queue: List[Dict[str, Any]] = []
        for i, (arg_block, path) in enumerate(items, 1):
            item_updates: Dict[str, Any] = {}
            if arg_block:
                try:
                    ns = parser.parse_args(arg_block)
                    enforce_global_only(ns, is_global=False, parser=parser)
                    raw = normalize_args(ns)
                    item_updates = {k: v for k, v in raw.items() if v is not None and v != [] and v != ""}
                except Exception as e:
                    console.print(f"[red]❌ Item {i} ({path}): {e}[/red]")
                    sys.exit(1)

            item_meta = {**global_meta, **item_updates}
            item_meta = apply_special_logic(item_meta)
            item_meta["path"] = path
            queue.append(item_meta)
            console.print(f"[green]✔ Item {i}: {Path(path).name}[/green]")

        # =====================================================================
        # 6. SET LOG LEVEL (from final global meta)
        # =====================================================================
        set_log_level(
            debug=global_meta.get("debug", False), ddebug=global_meta.get("deep_debug", False), quiet=global_meta.get("quiet", False)
        )

        # =====================================================================
        # 7. SHOW QUEUE (if requested)
        # =====================================================================
        if global_meta.get("show_queue") or global_meta.get("debug"):
            console.print("\n[bold green]Queuing these files:[/bold green]")
            paths = [Path(item["path"]).name for item in queue]
            from rich.markdown import Markdown
            from rich.style import Style

            console.print(Markdown("\n- " + "\n- ".join(paths), style=Style(color="cyan")))
            console.print(f"\nUnique uploads queued: [bold cyan]{len(paths)}[/bold cyan]\n")

        # =====================================================================
        # 8. VALIDATE TRACKERS
        # =====================================================================
        trackers = normalize_trackers(global_meta)
        tracker_registry = load_trackers(trackers, self.config)
        if not any(t in tracker_registry or t == "MANUAL" for t in trackers):
            console.print(f"[bold red]❌ No valid trackers: {', '.join(trackers)}[/bold red]")
            sys.exit(1)

        # =====================================================================
        # 9. RUN QUEUE
        # =====================================================================
        try:
            await self._process_queue(queue, global_meta, trackers, tracker_registry, base_dir)
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠️ Upload interrupted by user.[/yellow]")
            sys.exit(1)

    async def _process_queue(
        self,
        queue: List[Dict[str, Any]],
        global_meta: Dict[str, Any],
        trackers: List[str],
        tracker_registry: Dict[str, Any],
        base_dir: Path,
    ) -> None:
        """Process the upload queue — one item at a time."""

        log.info("[yellow]Starting queue processing[/yellow]")
        total = len(queue)
        progress = ProgressManager(console, quiet=global_meta.get("quiet", False), unattended=global_meta.get("unattended", False))
        progress.start(total)

        screenshot_manager = ScreenshotManager(config=self.config, base_dir=base_dir)

        successful = skipped = 0
        skipped_details = []
        skipped_tmdb = []

        try:
            for idx, item_meta in enumerate(queue, 1):
                path = item_meta["path"]
                item_name = Path(path).name
                progress.update(f"Processing {item_name} ({idx}/{total})")

                item_meta = await load_existing_meta(base_dir, path, item_meta)
                await apply_delay(item_meta, self.config)

                prep = Prep(config=self.config, base_dir=base_dir)
                try:
                    item_meta = await prep.gather_prep(meta=item_meta, mode="cli")
                except BDInfoExtractionError as e:
                    log.warning(f"[yellow]Skipping due to prep error: {e}[/yellow]")
                    progress.update(f"Processing {item_name}: Skipped (Prep error)")
                    skipped += 1
                    skipped_details.append((path, "Prep error"))
                    continue

                await save_meta(item_meta, base_dir, item_meta["uuid"])

                if item_meta.get("tmdb_not_found"):
                    progress.update(f"Processing {item_name}: Skipped (TMDB not found)")
                    skipped += 1
                    skipped_details.append((path, "TMDB not found"))
                    skipped_tmdb.append(path)
                    continue

                screens_handled = False

                async def handle_screens():
                    nonlocal item_meta
                    item_meta = await screenshot_manager.handle_screenshots(item_meta, tracker_whitelists)
                    await save_meta(item_meta, base_dir, item_meta["uuid"])

                needs_screenshots = int(item_meta.get("screens", 0)) or item_meta.get("user_images")

                if self.config.get("SCREENS", {}).get("generate_b4_confirm", False) and needs_screenshots:
                    # Generation was put after to avoid needless generation if no valid upload found
                    # Some users however wanted it done beforehand to inspect screens -this is for them-
                    await handle_screens()
                    screens_handled = True

                tracker_upload_ok = await pre_upload_checks(item_meta, trackers, tracker_registry, skipped_details, path, self.config)
                if not any(tracker_upload_ok.values()):
                    progress.update(f"Processing {item_name}: Skipped (pre-upload checks failed)")
                    skipped += 1
                    skipped_details.append((path, "Pre-upload checks failed"))
                    continue

                viable_trackers = [t for t, ok in tracker_upload_ok.items() if ok]
                if not item_meta.get("unattended"):
                    with progress.pause_for_interaction():
                        confirm = await get_confirmation(item_meta, viable_trackers, tracker_registry, self.config)
                        if not confirm:
                            item_meta = await edit_meta(prep, item_meta, viable_trackers, tracker_registry, self.config)
                        if not item_meta:
                            progress.update(f"Processing {item_name}: Skipped (user cancelled)")
                            skipped += 1
                            skipped_details.append((path, "User cancelled"))
                            continue
                else:
                    console.print(f"[yellow]Unattended mode: Skipping confirmation for {path}[/yellow]")

                tracker_whitelists = {
                    t: getattr(tracker_registry[t]["class"](config=self.config), "allowed_imghosts", [])
                    for t in viable_trackers
                    if t in tracker_registry
                }

                if needs_screenshots and not screens_handled:
                    await handle_screens()

                item_meta = await handle_torrent_creation(self.config, item_meta)

                success, _ = await upload_to_trackers(
                    item_meta,
                    viable_trackers,
                    tracker_registry,
                    successful,
                    skipped,
                    skipped_details,
                    path,
                    progress,
                    self.client,
                    self.config,
                )
                successful += int(success)
                skipped += int(not success)

                progress.update(f"Processing {item_name}: {'Uploaded' if success else 'Skipped'}")

        except KeyboardInterrupt:
            progress.update("Cancelling...")
            raise
        finally:
            if "item_meta" in locals():
                await save_meta(item_meta, base_dir, item_meta.get("uuid"))
            progress.stop()

        print_summary(total, successful, skipped, skipped_details, skipped_tmdb)

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
    redactor.load_from_config(config)
    redactor.add_rule(str(base_dir), "Uploadrr")
    redactor.add_rule(str(Path.home()), "HOME")
    if sys.platform == "win32":
        winbase = str(base_dir.as_posix())
        winhome = str(Path.home().as_posix())
        redactor.add_rule(winbase, "Uploadrr")
        redactor.add_rule(winhome, "HOME")
    uploadrr = Uploadrr(config)
    try:
        await uploadrr.do_the_thing(base_dir)
    except KeyboardInterrupt:
        console.print("[bold red]Interrupted by user. Cleaning up...[/bold red]")
        raise

    finally:
        end_time = time.perf_counter()
        console.print("[magenta]see you soon ^.<", justify="center")
        log.debug(f"Uploadrr v2 took {end_time - start_time:.4f} seconds too long.")


if __name__ == "__main__":
    cleanrr = CleanupHandler(name="Uploadrr")
    try:
        if sys.platform == "win32":
            import functools
            from asyncio.proactor_events import _ProactorBasePipeTransport

            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

            def silence_event_loop_closed(func):
                @functools.wraps(func)
                def wrapper(self, *args, **kwargs):
                    try:
                        return func(self, *args, **kwargs)
                    except RuntimeError as e:
                        if str(e) != "Event loop is closed":
                            raise

                return wrapper

            _ProactorBasePipeTransport.__del__ = silence_event_loop_closed(_ProactorBasePipeTransport.__del__)
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("[bold red]Interrupted by user. Cleaning up...[/bold red]")
        asyncio.run(cleanrr.shutdown())
        sys.exit(1)

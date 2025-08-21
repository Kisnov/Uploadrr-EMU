import asyncio
import sys
from pathlib import Path
from typing import Dict, List

from rich.markdown import Markdown
from rich.progress import Progress, TimeRemainingColumn
from rich.style import Style

from src.utils.console import console


def normalize_trackers(meta: Dict) -> List[str]:
    """Normalize tracker list from meta or config."""
    trackers = meta["trackers"]
    if isinstance(trackers, list):
        trackers = ",".join(trackers)
    trackers = [t.strip().upper() for t in trackers.split(",")]
    if meta.get("manual", False):
        trackers.insert(0, "MANUAL")
    return trackers


def prepare_queue(meta: Dict, argv: List[str]) -> List[str]:
    """Prepare file queue based on input arguments."""
    queue = []
    if meta.get("auto_queue"):
        directory = Path(meta["auto_queue"])
        if not directory.is_dir():
            console.print(f"[red]Directory not found: {directory}[/red]")
            sys.exit(1)
        queue = list_directory(str(directory))
    else:
        paths = [arg for arg in argv if Path(arg).exists()]  # Filter valid paths from argv
        if not paths:
            console.print("[red]No valid paths provided.[/red]")
            sys.exit(1)
        queue = [str(Path(path).resolve()) for path in paths]

    if not queue:
        console.print("[red]No valid items to process.[/red]")
        sys.exit(1)

    if meta.get("show_queue") or meta.get("debug"):
        console.print("\n[bold green]Queuing these files:[/bold green]")
        console.print(Markdown("\n- " + "\n- ".join(queue), style=Style(color="cyan")))
    console.print(f"\nUnique uploads queued: [bold cyan]{len(queue)}[/bold cyan]")
    return queue


def list_directory(directory: str) -> List[str]:
    """List non-hidden files in directory."""
    return [str(file.resolve()) for file in Path(directory).iterdir() if not file.name.startswith(".")]


async def apply_delay(meta: Dict, config: Dict) -> None:
    """Apply delay between uploads if specified."""
    delay = meta.get("delay") or config.get("AUTO", {}).get("delay", 0)
    if delay > 0:
        with Progress("[progress.description]{task.description}", TimeRemainingColumn(), transient=True) as progress:
            task = progress.add_task("[cyan]Auto delay...", total=delay)
            for _ in range(delay):
                await asyncio.sleep(1)
                progress.update(task, advance=1)

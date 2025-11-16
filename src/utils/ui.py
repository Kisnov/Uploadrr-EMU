import os
from typing import Dict, List, Tuple

from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from src.utils.console import console


def print_banner() -> None:
    ascii_art = r"""
┌────────────────────────────────────────────────────────────────────────┐
│  _____  __________ ______ _______ _______ ________ ________ ________   │
│  __  / / /___  __ \___  / __  __ \___    |___  __ \___  __ \___  __ \  │
│  _  / / / __  /_/ /__  /  _  / / /__  /| |__  / / /__  /_/ /__  /_/ /  │
│  / /_/ /  _  ____/ _  /___/ /_/ / _  ___ |_  /_/ / _  _, _/ _  _, _/   │
│  \____/   /_/      /_____/\____/  /_/  |_|/_____/  /_/ |_|  /_/ |_|    │
└────────────────────────────────────────────────────────── CvT ─────────┘

    """
    console.print(Align.center(Text(f"\n\n{ascii_art}\n", style="bold")))


def print_summary(
    total_files: int, successful_uploads: int, skipped_files: int, skipped_details: List[Tuple], skipped_tmdb_files: List[str]
) -> None:
    """Print upload summary."""
    if total_files > 0:
        console.print(
            Panel(
                f"Processed [bold bright_magenta]{total_files}[/bold bright_magenta] Unique Uploads\n"
                f"Successful Uploads: [bold green]{successful_uploads}[/bold green]\n"
                f"Failed Uploads: [bold red]{skipped_files}[/bold red]",
                title="Upload Summary",
                border_style="bold cyan",
            ),
            force=True,
        )

    if skipped_files > 0:
        for reason, files in group_skipped_details(skipped_details).items():
            console.print(
                Panel(
                    Group(*[
                        Text(f"Path: {path}\n" + "\n".join(f"• {os.path.basename(f)}" for f in file_list))
                        for path, file_list in files.items()
                    ]),
                    title=reason,
                    border_style="bold red" if "banned" in reason.lower() else "bold yellow",
                ),
                force=True,
            )

    if skipped_tmdb_files:
        grouped = {}
        for f in skipped_tmdb_files:
            dir_path = os.path.dirname(f) + os.sep
            grouped.setdefault(dir_path, []).append(f)
        console.print(
            Panel(
                Group(*[
                    Text(f"Path: {path}\n" + "\n".join(f"• {os.path.basename(f)}" for f in files)) for path, files in grouped.items()
                ]),
                title="TMDb ID not found",
                border_style="bold red",
            ),
            force=True,
        )


def group_skipped_details(skipped_details: List[Tuple]) -> Dict[str, Dict[str, List[str]]]:
    """Group skipped details by reason and path."""
    tracker_skip_map = {}
    for path, reason in skipped_details:
        tracker_skip_map.setdefault(reason, {}).setdefault(os.path.dirname(path) + os.sep, []).append(path)
    return tracker_skip_map


def build_db_info(meta: Dict) -> List[str]:
    """Build database info display."""
    if meta.get("is_music"):
        db_info = [
            f"[bold]Album[/bold]: {meta['album']} ({meta['year']})",
            f"[bold]Artist[/bold]: {meta['artist']}",
            f"[bold]Category[/bold]: {meta['category']}",
        ]
        if meta.get("mbid"):
            db_info.append(f"\n[bold]MBID[/bold]: https://musicbrainz.org/release/{meta['mbid']}")
        if meta.get("discogs_url"):
            db_info.append(f"[bold]Discogs[/bold]: {meta['discogs_url']}")
        return db_info
    elif meta.get("is_sports"):
        return [f"[bold]Title[/bold]: {meta['title']}", f"[bold]Category[/bold]: {meta['category']}"]

    elif meta.get("xxx"):
        porndb = meta.get("porndb", {})
        db_info = [
            f"[bold]Title[/bold]: {meta['title']} ({porndb.get('date', meta['search_year'])})",
            f"[bold]Overview[/bold]: {porndb.get('description', 'N/A')}",
            f"[bold]Category[/bold]: {meta['category']}",
        ]
        if porndb.get("url"):
            db_info.append(f"\n[bold]URL[/bold]: {porndb['url']}")
        return db_info
    else:
        db_info = [
            f"[bold]Title[/bold]: {meta['title']} ({meta['year']})",
            f"[bold]Overview[/bold]: {meta['overview']}",
            f"[bold]Category[/bold]: {meta['category']}",
        ]
        if int(meta.get("tmdb", 0)):
            db_info.append(f"TMDB: https://www.themoviedb.org/{meta['category'].lower()}/{meta['tmdb']}")
        if int(meta.get("imdb_id", "0")):
            db_info.append(f"IMDB: https://www.imdb.com/title/tt{meta['imdb_id']}")
        if int(meta.get("tvdb_id", "0")):
            db_info.append(f"TVDB: https://www.thetvdb.com/?id={meta['tvdb_id']}&tab=series")
        if int(meta.get("mal_id", 0)):
            db_info.append(f"MAL: https://myanimelist.net/anime/{meta['mal_id']}")
        return db_info

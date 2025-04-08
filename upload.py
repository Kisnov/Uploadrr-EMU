import requests
from src.args import Args
from src.clients import Clients
from src.prep import Prep
from src.trackers.COMMON import COMMON
import json
from pathlib import Path
import asyncio
import os
import sys
import re
import platform
import multiprocessing
import logging
import shutil
import glob
import subprocess
import traceback
import time
import random
from packaging.version import Version
from src.console import console, log, set_log_level
from rich.markdown import Markdown
from rich.style import Style
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich.align import Align
from rich.rule import Rule
from rich.console import Group
from rich.progress import Progress, TimeRemainingColumn
from difflib import SequenceMatcher
import bencodepy as bencode
from urllib.parse import urlparse, parse_qs
import importlib

# Detect python3 or fallback to default python command
python_cmd = shutil.which("python3") or "python"

base_dir = os.path.dirname(os.path.realpath(__file__))
data_dir = os.path.join(base_dir, 'data')
config_path = os.path.abspath(os.path.join(data_dir, 'config.py'))
old_config_path = os.path.abspath(os.path.join(data_dir, 'backup', 'old_config.py'))
minimum_version = Version('1.0.5')

def get_backup_name(path, suffix='_bu'):
    base, ext = os.path.splitext(path)
    counter = 1
    while os.path.exists(path):
        path = f"{base}{suffix}{counter}{ext}"
        counter += 1
    return path

if not os.path.exists(config_path):  
    console.print("[bold red] It appears you have no config file, please ensure to configure and place `/data/config.py`")
    exit()

try:
    from data.config import config 
except ImportError as e:
    console.print(f"[bold red]Error importing config: {str(e)}[/bold red]")
    exit()

def reconfigure():
    console.print("[bold red]WARN[/bold red]: Version out of date, automatic upgrade in progress")
    try:
        if os.path.exists(old_config_path):
            backup_name = get_backup_name(old_config_path)
            shutil.move(old_config_path, backup_name)
        shutil.move(config_path, old_config_path)
    except Exception as e:
        console.print("[bold red]ERROR[/bold red]: Unable to proceed with automatic upgrade. Please rename `config.py` to `old_config.py` move it to 'data/backup` and run `python3 data/reconfig.py`")
        console.print(f"Error: {str(e)}")
        exit()

    result = subprocess.run(
        [python_cmd, os.path.join(data_dir, "reconfig.py"), "--output-dir", data_dir],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        console.print(f"[bold red]Error during reconfiguration: {result.stderr}[/bold red]")
        exit()

    if os.path.exists(config_path):
        console.print("[bold green]Congratulations! config.py was successfully updated.[/bold green]")
    else:
        console.print("[bold][red]ERROR[/red]: config.py not found in the expected directory after reconfiguration.[/bold]")
        exit()

    console.print("Please double-check new config and ensure client settings were appropriately set.")
    console.print("[bold yellow]WARN[/bold yellow]: After verification of config, rerun command.")
    console.print("[dim green]Thanks for using Uploadrr :)")
    exit()

if 'version' not in config or Version(config.get('version')) < minimum_version:  # Check for version and reconfigures
    reconfigure()

try:
    from data.backup import example_config
    if 'version' in example_config.config and Version(example_config.config.get('version')) > Version(config.get('version')):
        console.print("[bold yellow]WARN[/bold yellow]: Config version out of date, updating is recommended.")
        console.print("[bold yellow]WARN[/bold yellow]: Simply pass --reconfig")
except Exception as e:
    console.print(f"[bold red]Error: {str(e)}[/bold red]")
    pass

# Initialize client and argument parser
try:
    client = Clients(config=config)
    parser = Args(config)
except Exception as e:
    console.print(f"[bold red]Error initializing client or parser: {e}[/bold red]")
    sys.exit(1)

async def do_the_thing(base_dir):
    print_banner()
    meta = {'base_dir': base_dir}

    # Parse the command-line arguments and update meta
    meta, help, before_args = parser.parse(sys.argv[1:], meta)

    #LOG LEVEL
    set_log_level(debug=True) if meta.get('debug') else set_log_level(debug=False)
    set_log_level(ddebug=True) if meta.get('deep_debug') else set_log_level(ddebug=False)

    # If 'reconfig' is set in meta, run reconfigure()
    if meta.get("reconfig", False):
        reconfigure()

    # Determine trackers to use
    if meta.get('trackers', None) is not None:
        trackers = meta['trackers']
        if isinstance(trackers, list):  # From nargs='*'
            trackers = ','.join(trackers)
    else:
        trackers = config['TRACKERS']['default_trackers']

    # Normalize trackers
    if isinstance(trackers, str):
        trackers = [t.strip() for t in trackers.split(',')] if ',' in trackers else [trackers.strip()]
    trackers = [t.upper() for t in trackers]
    if meta.get('manual', False):
        trackers.insert(0, "MANUAL")

    # Load only the specified trackers
    TRACKER_REGISTRY = load_trackers(trackers, config)
    tracker_list = list(TRACKER_REGISTRY.keys())
    logging.debug(f"Loaded trackers: {', '.join(tracker_list)}")

    # Check if we have any valid trackers
    valid_trackers = [t for t in trackers if t in TRACKER_REGISTRY or t == "MANUAL"]
    if not valid_trackers:
        logging.error(f"[bold]No valid trackers provided.[/bold] Specified trackers: {', '.join(trackers)}")
        console.print(f"[bold red]Please check your tracker names or config and try again.[/bold red]", justify="center")
        sys.exit(1)

    # Collect existing paths from the arguments
    paths = []
    for each in sys.argv[1:]:
        if os.path.exists(each):
            paths.append(os.path.abspath(each))
        else:
            break

    # Clean up tmp directory if 'cleanup' flag is set
    if meta.get('cleanup', False):
        tmp_dir = f"{base_dir}/tmp"
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
            console.print("[bold green]Successfully emptied tmp directory")
        else:
            console.print("[bold yellow]tmp directory is already empty")

    if meta.get('auto_queue'):
        directory = meta['auto_queue']
        if os.path.isdir(directory):
            queue = list_directory(directory)
            if meta.get('show_queue') or meta.get('debug'):
                md_text = "\n - ".join(queue)
                console.print("\n[bold green]Automatically queuing these files:[/bold green]", end='')
                console.print(Markdown(f"- {md_text.rstrip()}\n\n", style=Style(color='cyan')))
            console.print(f"\nUnique uploads queued: [bold cyan]{len(queue)}[/bold cyan]")
        else:
            console.print(f"[red]Directory: [bold red]{directory}[/bold red] does not exist")
            exit(1)
    else:
        if not meta['path']:
            exit(0)
        path = meta['path']
        path = os.path.abspath(path)
        if path.endswith('"'):
            path = path[:-1]
        queue = []
        if os.path.exists(path):
            meta, help, before_args = parser.parse(tuple(' '.join(sys.argv[1:]).split(' ')), meta)
            queue = [path]
        else:
            if os.path.exists(os.path.dirname(path)) and len(paths) <= 1:
                escaped_path = path.replace('[', '[[]')
                globs = glob.glob(escaped_path)
                queue = globs
                if len(queue) != 0:
                    if meta.get('show_queue') or meta.get('debug'):
                        md_text = "\n - ".join(queue)
                        console.print("\n[bold green]Queuing these files:[/bold green]", end='')
                        console.print(Markdown(f"- {md_text.rstrip()}\n\n", style=Style(color='cyan')))
                    console.print(f"\nUnique Uploads Queued: [bold cyan]{len(queue)}[/bold cyan]\n")
                else:
                    console.print(f"[red]Path: [bold red]{path}[/bold red] does not exist")
            elif os.path.exists(os.path.dirname(path)) and len(paths) != 1:
                queue = paths
                if meta.get('show_queue') or meta.get('debug'):
                    md_text = "\n - ".join(queue)
                    console.print("\n[bold green]Queuing these files:[/bold green]", end='')
                    console.print(Markdown(f"- {md_text.rstrip()}\n\n", style=Style(color='cyan')))
                console.print(f"\nUnique uploads queued: [bold cyan]{len(queue)}[/bold cyan]\n")
            elif not os.path.exists(os.path.dirname(path)):
                split_path = path.split()
                p1 = split_path[0]
                for i, each in enumerate(split_path):
                    try:
                        if os.path.exists(p1) and not os.path.exists(f"{p1} {split_path[i+1]}"):
                            queue.append(p1)
                            p1 = split_path[i+1]
                        else:
                            p1 += f" {split_path[i+1]}"
                    except IndexError:
                        if os.path.exists(p1):
                            queue.append(p1)
                        else:
                            console.print(f"[red]Path: [bold red]{p1}[/bold red] does not exist")
                if len(queue) >= 1:
                    if meta.get('show_queue') or meta.get('debug'):
                        md_text = "\n - ".join(queue)
                        console.print("\n[bold green]Queuing these files:[/bold green]", end='')
                        console.print(Markdown(f"- {md_text.rstrip()}\n\n", style=Style(color='cyan')))
                    console.print(f"\nTotal items queued: [bold cyan]{len(queue)}[/bold cyan]\n")
            else:
                console.print("[red]There was an issue with your input. If you think this was not an issue, please make a report that includes the full command used.")
                exit()

    delay = meta.get('delay', 0) or config['AUTO'].get('delay', 0)
    base_meta = {k: v for k, v in meta.items()}

    # Initialize counters
    total_files = len(queue)
    successful_uploads = 0
    skipped_files = 0
    skipped_details = []
    skipped_tmdb_files = []
    current_file = 1

    if meta.get('random'):
        random.shuffle(queue)
    elif meta.get('auto_queue'):
        queue = sorted(queue, key=str.lower)
    else:
        queue = queue

    for path in queue:
        meta = {k: v for k, v in base_meta.items()}
        meta['path'] = path
        meta['uuid'] = None
        try:
            with open(f"{base_dir}/tmp/{os.path.basename(path)}/meta.json", encoding='utf-8') as f:
                saved_meta = json.load(f)
                for key, value in saved_meta.items():
                    overwrite_list = [
                        'base_dir', 'path', 'trackers', 'dupe', 'debug', 'anon', 'category', 'type', 'screens', 'nohash', 'manual_edition', 'imdb', 'tmdb_manual', 'mbid_manual', 'mal', 'manual', 'manual_name', 
                        'hdb', 'ptp', 'blu', 'no_season', 'no_aka', 'no_year', 'no_dub', 'no_tag', 'no_seed', 'client', 'desclink', 'descfile', 'desc', 'draft', 'region', 'freeleech', 
                        'personalrelease', 'unattended', 'season', 'episode', 'torrent_creation', 'qbit_tag', 'qbit_cat', 'skip_imghost_upload', 'imghost', 'manual_source', 'webdv', 'hardcoded-subs'
                    ]
                    if meta.get(key, None) != value and key in overwrite_list:
                        saved_meta[key] = meta[key]
                meta = saved_meta
                f.close()
        except FileNotFoundError:
            pass
        
        console.print(Align.center(f"\n\n——— Processing # [bold bright_cyan]{current_file}[/bold bright_cyan] of [bold bright_magenta]{total_files}[/bold bright_magenta] ———"))
        if delay > 0:
            with Progress("[progress.description]{task.description}", TimeRemainingColumn(), transient=True) as progress:
                task = progress.add_task("[cyan]Auto delay...", total=delay)
                for i in range(delay):
                    await asyncio.sleep(1)
                    progress.update(task, advance=1)
        console.print(f"[green]Gathering info for {os.path.basename(path)}")
        if meta['imghost'] is None:
            meta['imghost'] = config['DEFAULT']['img_host_1']
        if meta['unattended']:
            console.print("[yellow]Running in Auto Mode")
                
        current_file += 1
        prep = Prep(screens=meta.get('screens', 3), img_host=meta.get('imghost', 'imgbox'), config=config)
        meta = await prep.gather_prep(meta=meta, mode='cli')

        # Gather TMDb ID
        if meta.get('tmdb_not_found'):
            skipped_files += 1
            skipped_tmdb_files.append(path)
            continue

        try:
            meta['name_notag'], meta['name'], meta['clean_name'], meta['potential_missing'] = await prep.get_name(meta)
            if any(val is None for val in (meta['name_notag'], meta['name'], meta['clean_name'], meta['potential_missing'])):
                raise ValueError("Name values are None")
        except Exception as e:
            skipped_files += 1
            skipped_details.append((path, f'Error getting name: {str(e)}'))
            if meta['is_music']:
                log.error("get_name was called")            
            continue

        if meta.get('image_list', False) in (False, []) and meta.get('skip_imghost_upload', False) == False:
            return_dict = {}
            meta['image_list'], dummy_var = prep.upload_screens(meta, meta['screens'], 1, 0, meta['screens'],[], return_dict)
            if meta['debug']:
                console.print(meta['image_list'])
            # meta['uploaded_screens'] = True
        elif meta.get('skip_imghost_upload', False) and not meta.get('image_list', False):
            meta['image_list'] = []


        if not os.path.exists(os.path.abspath(f"{meta['base_dir']}/tmp/{meta['uuid']}/BASE.torrent")):
            reuse_torrent = None
            if not meta.get('rehash', False):
                reuse_torrent = await client.find_existing_torrent(meta)
                if reuse_torrent != None:
                    prep.create_base_from_existing_torrent(reuse_torrent, meta['base_dir'], meta['uuid'])
            if not meta['nohash'] and reuse_torrent is None:
                prep.create_torrent(meta, Path(meta['path']), "BASE", meta.get('piece_size_max', 0))
            if meta['nohash']:
                meta['client'] = "none"
        elif os.path.exists(os.path.abspath(f"{meta['base_dir']}/tmp/{meta['uuid']}/BASE.torrent")) and meta.get('rehash', False) is True and meta['nohash'] is False:
            prep.create_torrent(meta, Path(meta['path']), "BASE", meta.get('piece_size_max', 0))
        if int(meta.get('randomized', 0)) >= 1:
            prep.create_random_torrents(meta['base_dir'], meta['uuid'], meta['randomized'], meta['path'])

        with open (f"{meta['base_dir']}/tmp/{meta['uuid']}/meta.json", 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=4)
            f.close()
        confirm = get_confirmation(meta)  
        while not confirm:
            if meta.get('is_music', False):
                console.print("Input args that need correction e.g.(--tag PMEDIA --mbid e7bb5806-36bb-45a7-b6ea-0b6de4eed94b)") 
            else:    
                console.print("Input args that need correction e.g.(--tag NTb --category tv --tmdb 12345)")  
            console.print("Enter 'skip' if no correction needed", style="dim")
            editargs = Prompt.ask("")
            if editargs.lower() == 'skip':
                break
            elif editargs == '':
                console.print("Invalid input. Please try again or type 'skip' to pass.", style="dim")
            else:
                editargs = (meta['path'],) + tuple(editargs.split())
                if meta['debug']:
                    editargs = editargs + ("--debug",)
                meta, help, before_args = parser.parse(editargs, meta)
                meta['edit'] = True
                meta = await prep.gather_prep(meta=meta, mode='cli') 
                meta['name_notag'], meta['name'], meta['clean_name'], meta['potential_missing'] = await prep.get_name(meta)
                confirm = get_confirmation(meta)
                if confirm:
                    break

        console.print(f"Processing: [bold cyan]{path}[/bold cyan]")
        common = COMMON(config=config)

        for tracker in trackers:
            if meta.get('name', '').endswith('DUPE?'):
                meta['name'] = meta['name'].replace(' DUPE?', '')
            
            debug = "(DEBUG)" if meta.get('debug') else ""

            if tracker not in TRACKER_REGISTRY and tracker != "MANUAL":
                logging.error(f"[bold red]Error:[/bold red] Tracker '{tracker}' not recognized or supported.")
                skipped_files += 1
                skipped_details.append((path, f"No such tracker: {tracker}"))
                continue

            # Instantiate tracker only if needed (skip for MANUAL)
            if tracker != "MANUAL":
                tracker_info = TRACKER_REGISTRY[tracker]
                tracker_instance = tracker_info["class"](config=config)
                tracker_type = tracker_info["type"]
            else:
                tracker_instance = None
                tracker_type = None

            # Standard upload flow for api/http/unit3d trackers (for now)
            if tracker != "MANUAL" and tracker_type in ['api', 'http', 'unit3d']:
                upload_prompt = f"Upload to {tracker_instance.tracker}? {debug}"
                upload_to_tracker_flag = meta.get('unattended', False) or Confirm.ask(upload_prompt)
                
                if not upload_to_tracker_flag:
                    skipped_files += 1
                    skipped_details.append((path, f"User skipped upload on {tracker_instance.tracker}"))
                    continue
                
                if tracker_type == 'http' and not await tracker_instance.validate_credentials(meta):
                    skipped_files += 1
                    skipped_details.append((path, f"Invalid credentials for {tracker_instance.tracker}"))
                    continue

                success, skipped_files = await upload_to_tracker(
                    tracker_instance, tracker_instance.tracker, meta, config, client,
                    skipped_files, skipped_details, path, debug
                )
                if success:
                    successful_uploads += 1

            # MANUAL
            elif tracker == "MANUAL":
                do_manual = meta.get('unattended', False) or Confirm.ask("Get files for manual upload?", default=True)
                if do_manual:
                    for manual_tracker in trackers:
                        if manual_tracker != 'MANUAL' and manual_tracker in TRACKER_REGISTRY:
                            manual_instance = TRACKER_REGISTRY[manual_tracker]["class"](config=config)
                            if TRACKER_REGISTRY[manual_tracker]["type"] == 'api':
                                await common.unit3d_edit_desc(meta, manual_instance.tracker, manual_instance.signature)
                            else:
                                await manual_instance.edit_desc(meta)
                    url = await prep.package(meta)
                    if not url:
                        console.print(f"[yellow]Unable to upload prep files, they can be found at `tmp/{meta['uuid']}`")
                    else:
                        console.print(f"[green]{meta['name']}")
                        console.print(f"[green]Files can be found at: [yellow]{url}[/yellow]")

            # ALPHARATIO
            elif tracker == "AR":
                ar = tracker_instance
                upload_to_ar = meta['unattended'] or Confirm.ask(f"Upload to AlphaRatio? {debug}")
                if upload_to_ar:
                    console.print("Uploading to AlphaRatio")
                    if check_banned_group("AR", ar.banned_groups, meta, skipped_details, path):
                        skipped_files += 1
                        skipped_details.append((path, "Banned group on AlphaRatio"))
                        continue
                    if await ar.validate_credentials(meta):
                        dupes = await ar.search_existing(meta)
                        dupes = await common.filter_dupes(dupes, meta)
                        meta, skipped = dupe_check(dupes, meta, config, skipped_details, path)
                        if skipped:
                            skipped_files += 1
                            skipped_details.append((path, tracker))
                            continue
                    if meta['upload']:
                        await ar.upload(meta)
                        #await ar.update_torrent_file
                        await asyncio.sleep(5)
                        await client.add_to_client(meta, "AR")
                        successful_uploads += 1
                if ar.session:
                    await ar.close_session()                        

            # BEYONDHD
            elif tracker == "BHD":
                bhd = tracker_instance
                draft_int = await bhd.get_live(meta)
                draft = "Draft" if draft_int == 0 else "Live"
                upload_to_bhd = meta['unattended'] or Confirm.ask(f"Upload to BHD? ({draft}) {debug}")
                if upload_to_bhd:
                    success, skipped_files = await upload_to_tracker(bhd, "BHD", meta, config, client, skipped_files, skipped_details, path, debug)
                    if success:
                        successful_uploads += 1

            # DigitalCore
            elif tracker == "DC":
                dc = tracker_instance
                upload_to_dc = meta['unattended'] or Confirm.ask(f"Upload to DigitalCore? {debug}")
                if upload_to_dc:
                    console.print("Uploading to DigitalCore")
                    if await dc.validate_credentials(meta):
                        success, skipped_files = await upload_to_tracker(dc, "DC", meta, config, client, skipped_files, skipped_details, path, debug)
                        if success:
                            await asyncio.sleep(5)
                            successful_uploads += 1
                    if dc.session:
                        await dc.close_session()

            # THR
            elif tracker == "THR":
                upload_to_thr = meta['unattended'] or Confirm.ask(f"Upload to THR? {debug}")
                if upload_to_thr:
                    console.print("Uploading to THR")
                    if meta.get('imdb_id', '0') == '0':
                        while True:
                            imdb_id = Prompt.ask("Please enter a valid IMDB id (e.g., tt1234567)")
                            if re.match(r'tt\d{7}', imdb_id):
                                meta['imdb_id'] = imdb_id.replace('tt', '').zfill(7)
                                break
                            print("Invalid IMDB id. Please try again.")
                    if meta.get('youtube', None) is None:
                        while True:
                            youtube = Prompt.ask("Unable to find youtube trailer, please link one\n[dim] e.g.(https://www.youtube.com/watch?v=dQw4w9WgXcQ)[/dim]")
                            video_id = parse_qs(urlparse(youtube).query).get('v', [None])[0] or youtube
                            if video_id:
                                meta['youtube'] = video_id
                                break
                            print("Invalid YouTube URL or ID.")
                    thr = tracker_instance
                    try:
                        with requests.Session() as session:
                            console.print("[yellow]Logging in to THR")
                            session = thr.login(session)
                            success, skipped_files = await upload_to_tracker(thr, "THR", meta, config, client, skipped_files, skipped_details, path, debug)
                            if success:
                                successful_uploads += 1
                    except:
                        console.print(traceback.print_exc())

            # PassThePopcorn
            elif tracker == "PTP":
                upload_to_ptp = meta['unattended'] or Confirm.ask(f"Upload to PTP? {debug}")
                if upload_to_ptp:
                    console.print("Uploading to PTP")
                    if meta.get('imdb_id', '0') == '0':
                        while True:
                            imdb_id = Prompt.ask("Please enter a valid IMDB id (e.g., tt1234567)")
                            if re.match(r'tt\d{7}', imdb_id):
                                meta['imdb_id'] = imdb_id.replace('tt', '').zfill(7)
                                break
                            print("Invalid IMDB id.")
                    ptp = tracker_instance
                    if check_banned_group("PTP", ptp.banned_groups, meta, skipped_details, path):
                        skipped_files += 1
                        skipped_details.append((path, "Banned group on PTP"))
                        continue
                    try:
                        groupID = await ptp.get_group_by_imdb(meta['imdb_id'])
                        if groupID is None:
                            console.print("[yellow]No Existing Group found")
                            if meta.get('youtube', None) is None:
                                while True:
                                    youtube = Prompt.ask("Unable to find youtube trailer, please link one\n[dim] e.g.(https://www.youtube.com/watch?v=dQw4w9WgXcQ)[/dim]")
                                    video_id = parse_qs(urlparse(youtube).query).get('v', [None])[0] or youtube
                                    if video_id:
                                        meta['youtube'] = video_id
                                        break
                                    print("Invalid YouTube URL or ID.")
                            meta['upload'] = True
                        else:
                            dupes = await ptp.search_existing(groupID, meta)
                            dupes = await common.filter_dupes(dupes, meta)
                            meta, skipped = dupe_check(dupes, meta, config, skipped_details, path)
                            if skipped:
                                skipped_files += 1
                                skipped_details.append((path, "PTP"))
                                continue
                        if meta.get('imdb_info', {}) == {}:
                            meta['imdb_info'] = await prep.get_imdb_info(meta['imdb_id'], meta)
                        if meta['upload']:
                            ptpUrl, ptpData = await ptp.fill_upload_form(groupID, meta)
                            await ptp.upload(meta, ptpUrl, ptpData)
                            await asyncio.sleep(5)
                            await client.add_to_client(meta, "PTP")
                            successful_uploads += 1
                    except:
                        console.print(traceback.print_exc())

            # TorrentLeech
            elif tracker == "TL":
                upload_to_tl = meta.get('unattended', False) or Confirm.ask(f"Upload to {tracker_instance.tracker}? {debug}")
                if upload_to_tl:
                    console.print(f"Uploading to {tracker_instance.tracker}")
                    if hasattr(tracker_instance, 'banned_groups') and check_banned_group(tracker_instance.tracker, tracker_instance.banned_groups, meta, skipped_details, path):
                        skipped_files += 1
                        skipped_details.append((path, f"Banned group on {tracker_instance.tracker}"))
                        continue
                    await tracker_instance.upload(meta)
                    await client.add_to_client(meta, tracker_instance.tracker)
                    successful_uploads += 1

    ### FEEDBACK ###

    def format_path_with_files(path, files):
        formatted_text = Text()
        formatted_text.append("Path: ")
        formatted_text.append(f"{path}", style="bold")  # Removed the newline here
        for i, file in enumerate(files):
            file_name = os.path.basename(file)
            # Add a newline before each file, but not before the first one
            #if i != 0:
            formatted_text.append("\n")
            formatted_text.append(f"• {file_name}") 
        return formatted_text

    if total_files > 0:
        console.print()
        console.print(Panel(
            f"Processed [bold bright_magenta]{total_files}[/bold bright_magenta] Unique Uploads\n"
            f"Successful Uploads: [bold green]{successful_uploads}[/bold green]\n"
            f"Failed Uploads: [bold red]{skipped_files}[/bold red]",
            title="Upload Summary",
            border_style="bold cyan"
        ))

    # Handle skipped files
    if skipped_files > 0:
        tracker_skip_map = {}
        for detail in skipped_details:
            if len(detail) == 2:
                file, reason = detail
                tracker = "Unknown Tracker"
            else:
                file, reason, tracker = detail
            if reason not in tracker_skip_map:
                tracker_skip_map[reason] = []
            tracker_skip_map[reason].append((file, tracker))

        for reason, files in tracker_skip_map.items():
            reason_text = f"{reason}"
            reason_style = "bold red" if "banned" in reason.lower() or "rejected" in reason.lower() or "no such tracker" in reason.lower() else "bold yellow"
            
            path_file_map = {}
            for file, _ in files:
                path = os.path.dirname(file) + os.sep  
                if path not in path_file_map:
                    path_file_map[path] = []
                path_file_map[path].append(file)

            combined_renderable = []
            for i, (path, files) in enumerate(path_file_map.items()):
                if i != 0:
                    combined_renderable.append(Text())
                formatted_text = format_path_with_files(path, files)
                combined_renderable.append(formatted_text)


            if 'duplicate' in reason.lower():
                combined_renderable.append(Text("\nTip: If 100% sure not a dupe pass with --skip-dupe-check", style="dim"))

            reason_panel = Panel(
                renderable=Group(*combined_renderable),
                title=reason_text,
                border_style=reason_style
            )

            console.print(reason_panel)

    # Handle skipped TMDB files
    if skipped_tmdb_files:
        path_file_map = {}
        for file in skipped_tmdb_files:
            path = os.path.dirname(file) + os.sep
            if path not in path_file_map:
                path_file_map[path] = []
            path_file_map[path].append(file)
        
        combined_renderable = []
        for i, (path, files) in enumerate(path_file_map.items()):
            if i != 0:
                combined_renderable.append(Text())
            formatted_text = format_path_with_files(path, files)
            combined_renderable.append(formatted_text)

        combined_renderable.append(Text("\nTip: Pass individually with --tmdb #####", style="dim"))

        reason_panel = Panel(
            renderable=Group(*combined_renderable),
            title="TMDb ID not found",
            border_style="bold red"
        )

        console.print(reason_panel)

async def upload_to_tracker(tracker_instance, tracker_name, meta, config, client, skipped_files, skipped_details, path, debug):
    console.print(f"Uploading to {tracker_name} {'(DEBUG)' if meta['debug'] else ''}")
    common = COMMON(config=config)

    # Check for banned groups
    if hasattr(tracker_instance, 'banned_groups') and check_banned_group(tracker_name, tracker_instance.banned_groups, meta, skipped_details, path):
        skipped_files += 1
        skipped_details.append((path, f"Banned group on {tracker_name}"))
        return False, skipped_files

    # Check other banned content 
    if hasattr(tracker_instance, 'other_banned_content'):
        result = await tracker_instance.other_banned_content(meta, path)
        # Incase a tuple isn't returned (ie: someone returns a simple True/False)
        if isinstance(result, tuple):
            is_content_banned, is_absolute = result
        else:
            is_content_banned = result
            is_absolute = False
        if is_content_banned:
            # Absolute Ban (unskipable)
            if is_absolute:
                console.print(f"[bold yellow]{tracker_name}[/bold yellow][bold red] strictly prohibits this content.")
                skipped_files += 1
                skipped_details.append((path, f"Banned content on {tracker_name}"))
                return False, skipped_files
            else:
                # Regular Ban: Allow User Override
                console.print(f"[bold yellow]{tracker_name}[/bold yellow][bold red] flagged this content as banned.")
                if meta.get('unattended', False) or not Confirm.ask("[bold red] Upload Anyways?"):
                    skipped_files += 1
                    skipped_details.append((path, f"Banned content on {tracker_name}"))
                    return False, skipped_files

    # Search for duplicates
    dupes = await tracker_instance.search_existing(meta)
    if not meta.get('is_music', False):
        dupes = await common.filter_dupes(dupes, meta)
    meta, skipped = dupe_check(dupes, meta, config, skipped_details, path)
    if skipped:
        skipped_files += 1
        skipped_details.append((path, f"Potential duplicate on {tracker_name}"))
        return False, skipped_files

    # Perform the upload
    if meta['upload']:
        upload_success = await tracker_instance.upload(meta)
        if upload_success:
            if tracker_name == 'SN':
                await asyncio.sleep(16)
            await client.add_to_client(meta, tracker_name)
            return True, skipped_files
        else:
            skipped_files += 1
            skipped_details.append((path, f"{tracker_name} {'(DEBUG MODE)' if meta['debug'] else 'Rejected Upload'}"))
            return False, skipped_files
    return False, skipped_files


def get_confirmation(meta):
    ddebug = meta.get('deep_debug')
    if meta['debug'] or ddebug:
        console.print("[bold red]DEBUG: True")
    console.print(f"Prep material saved to {meta['base_dir']}/tmp/{meta['uuid']}")
    console.print()

    if meta['is_music']:
        db_info = [
            f"[bold]Album[/bold]: {meta['album']} ({meta['year']})",
            f"[bold]Artist[/bold]: {meta['artist']}",
            f"[bold]Category[/bold]: {meta['category']}",
        ]
        if meta.get('mbid'):
            db_info.append(f"\n[bold]MBID[/bold]: https://musicbrainz.org/release/{meta['mbid']}")
        if meta.get('discogs_url'):
            db_info.append(f"[bold]Discogs[/bold]: {meta['discogs_url']}")
    else: 
        db_info = [
            f"[bold]Title[/bold]: {meta['title']} ({meta['year']})\n",
            f"[bold]Overview[/bold]: {meta['overview']}\n",
            f"[bold]Category[/bold]: {meta['category']}\n",
        ]

    if int(meta.get('tmdb', 0)) != 0:
        db_info.append(f"TMDB: https://www.themoviedb.org/{meta['category'].lower()}/{meta['tmdb']}")
    if int(meta.get('imdb_id', '0')) != 0:
        db_info.append(f"IMDB: https://www.imdb.com/title/tt{meta['imdb_id']}")
    if int(meta.get('tvdb_id', '0')) != 0:
        db_info.append(f"TVDB: https://www.thetvdb.com/?id={meta['tvdb_id']}&tab=series")
    if int(meta.get('mal_id', 0)) != 0:
        db_info.append(f"MAL : https://myanimelist.net/anime/{meta['mal_id']}")

    console.print(Panel("\n".join(db_info), title="Database Info", border_style="bold yellow"))
    console.print()
    if int(meta.get('freeleech', '0')) != 0:
        console.print(f"[bold]Freeleech[/bold]: {meta['freeleech']}")
    if meta['tag'] == "":
            tag = ""
    else:
        tag = f" / {meta['tag'][1:]}"
    if meta['is_disc'] == "DVD":
        res = meta['source']
    else:
        res = meta['resolution']

    console.print(Text(f" {res} / {meta['type']}{tag}", style="bold"))
    if meta.get('personalrelease', False):
        console.print("[bright_magenta]Personal Release!")
    console.print()
    if not meta.get('unattended', False):
        get_missing(meta)
        ring_the_bell = "\a" if config['DEFAULT'].get("sfx_on_prompt", True) is True else "" # \a rings the bell
        console.print(f"[bold yellow]Is this correct?{ring_the_bell}") 
        console.print(f"[bold]Name[/bold]: {meta['name']}")
        confirm = Confirm.ask(" Correct?")
    else:
        console.print(f"[bold]Name[/bold]: {meta['name']}")
        confirm = True
    return confirm

def dupe_check(dupes, meta, config, skipped_details, path):
    if meta.get('dupe', False):
        console.print("[yellow]Skipping duplicate check as requested.")
        meta['upload'] = True   
        return meta, False  # False indicates not skipped
    
    if not dupes:
        console.print("[green]No dupes found")
        meta['upload'] = True   
        return meta, False  # False indicates not skipped

    table = Table(
        title="Are these dupes?",
        title_justify="center",
        show_header=True,
        header_style="bold underline",
        expand=True,
        show_lines=False,
        box=None
    )

    table.add_column("Name")
    table.add_column("Size", justify="center")

    for name, size in dupes.items():
        try:
            if "GB" in str(size).upper():
                size_gb = str(size).upper()
            else:
                size = int(size)
                if size > 0:
                    size_gb = str(round(size / (1024 ** 3), 2)) + " GB"  # Convert size to GB
                else:
                    size_gb = "N/A"
        except ValueError:
            size_gb = "N/A"
        table.add_row(name, f"[magenta]{size_gb}[/magenta]")

    console.print()
    console.print(table)
    console.print()

    def preprocess_string(text):
        text = re.sub(r'\[[a-z]{3}\]', '', text, flags=re.IGNORECASE)
        text = re.sub(r'[^\w\s]', '', text)
        text = text.lower()
        return text

    def handle_similarity(similarity, meta):
        if similarity == 1.0:
            console.info(f"[red]Found exact name match. Aborting..")
            meta['upload'] = False
            return meta, True  # True indicates skipped
        elif meta['unattended']:
            console.print(f"[red]Found potential dupe with {similarity * 100:.2f}% similarity. Aborting.")
            meta['upload'] = False
            return meta, True  # True indicates skipped
        else:
            upload = Confirm.ask(" Upload Anyways?")
            if not upload:
                meta['upload'] = False
                return meta, True  # True indicates skipped
        return meta, False  # False indicates not skipped

    similarity_threshold = max(config['AUTO'].get('dupe_similarity', 90.00) / 100, 0.70)
    console.print(f"Similarity: [red]{similarity_threshold}")
    size_tolerance = max(min(config['AUTO'].get('size_tolerance', 1 if meta['unattended'] else 30), 100), 1) / 100
    console.print(f"Size Tolerance: [red]{size_tolerance}")
    cleaned_meta_name = preprocess_string(meta['clean_name'])

    for name, size in dupes.items():
        if isinstance(size, str) and "GB" in size:
            size = float(size.replace(" GB", "")) * (1024 ** 3)  # Convert GB to bytes
        elif isinstance(size, (int, float)) and size != 0:
            size = int(size)
        else:
            console.print(f"Skipping invalid size for {name}: {size}")
            continue  # Skip to the next iteration if size is invalid

        meta_size = meta.get('content_size')
        if meta_size is None:
            meta_size = extract_size_from_torrent(meta['base_dir'], meta['uuid'])

        log.dinfo(f"Comparing {name} with size {size} bytes to {meta['clean_name']} with size {meta_size} bytes")

        # Define a maximum size tolerance to catch abnormally huge differences
        max_tolerance = config['AUTO'].get('max_size_tolerance', 25) / 100  # Default to 25%

        if meta_size == size:
            # Exact size match
            console.print(f"[red]Exact size match. [dim](byte-for-byte)[/dim] Aborting..")
            cleaned_dupe_name = preprocess_string(name)
            # similarity = SequenceMatcher(None, cleaned_meta_name, cleaned_dupe_name).ratio()
            return meta, True #Skip Upload

        elif abs(meta_size - size) > max_tolerance * meta_size:
            # Abnormally huge size difference
            log.dinfo("Size difference exceeds max tolerance (%.2f%%): %d bytes.", max_tolerance * 100, abs(meta_size - size))

        elif abs(meta_size - size) <= size_tolerance * meta_size:
            # Size is within reasonable tolerance
            log.dinfo(f"Size difference within tolerance: {abs(meta_size - size)} bytes.")
            cleaned_dupe_name = preprocess_string(name)
            similarity = SequenceMatcher(None, cleaned_meta_name, cleaned_dupe_name).ratio()

            if similarity >= similarity_threshold:
                log.dinfo(f"[yellow]Close size match ({abs(meta_size - size)} bytes difference) with {similarity * 100:.2f}% name similarity.")
                upload = Confirm.ask(" Upload anyways?")
                if not upload:
                    meta['upload'] = False
                    return meta, True #Skip Upload
            else:
                log.dinfo(f"[green]Close size match, but low name similarity ({similarity * 100:.2f}%). Proceeding.")

        else:
            # Size difference exceeds regular tolerance but is not abnormally large
            log.dinfo(f"Size difference exceeds regular tolerance but within max tolerance: {abs(meta_size - size)} bytes.")
            cleaned_dupe_name = preprocess_string(name)
            similarity = SequenceMatcher(None, cleaned_meta_name, cleaned_dupe_name).ratio()

            if similarity >= similarity_threshold:
                log.dinfo(f"[yellow]Large size difference but high name similarity ({similarity * 100:.2f}%). Treating as potential dupe.")
                upload = Confirm.ask(" Upload anyways?")
                if not upload:
                    meta['upload'] = False
                    return meta, True #Skip Upload
            else:
                console.print(f"[green]Large size difference and low name similarity ({similarity * 100:.2f}%). Proceeding.")
    # If no matches found
    console.print("[yellow]No dupes found above the similarity threshold. Uploading anyways.")
    meta['upload'] = True
    return meta, False  # Proceed with upload


def extract_size_from_torrent(base_dir, uuid):
    torrent_path = f"{base_dir}/tmp/{uuid}/BASE.torrent"
    with open(torrent_path, 'rb') as f:
        torrent_data = bencode.decode(f.read())
    
    info = torrent_data[b'info']
    if b'files' in info:
        # Multi-file torrent
        return sum(file[b'length'] for file in info[b'files'])
    else:
        # Single-file torrent
        return info[b'length']


# Return True if banned group
def check_banned_group(tracker, banned_group_list, meta, skipped_details, path):
    if not meta.get('tag'):  # Safer check for missing/empty tag
        return False

    group_name = meta['tag'][1:] #Case-sensitive matching
    release_type = meta.get('type', '')   

    # Fallback for old list format: convert to dict with "ALL" as default
    if isinstance(banned_group_list, list):
        banned_group_list = {group.lower(): ["ALL"] for group in banned_group_list if group}

    all_banned = [g.lower() for g in banned_group_list.get("ALL_TYPES", [])]
    if group_name in all_banned:
        console.print(f"[bold yellow]{meta['tag'][1:]}[/bold yellow][bold red] was found on [bold yellow]{tracker}'s[/bold yellow] list of banned groups.")
        if meta.get('unattended', False) or not Confirm.ask("[bold red] Upload Anyways?"):
            return True
        return False

    # Check if group has specific banned types
    banned_types = banned_group_list.get(group_name, None)
    if banned_types is not None:  # Group is explicitly listed
        if not banned_types or "ALL" in banned_types:  # Check "ALL" as uppercase
            console.print(f"[bold yellow]{meta['tag'][1:]}[/bold yellow][bold red] was found on [bold yellow]{tracker}'s[/bold yellow] list of banned groups.")
            if meta.get('unattended', False) or not Confirm.ask("[bold red] Upload Anyways?"):
                return True
            return False
        elif release_type and release_type in banned_types:  # Direct comparison, no lowercase
            console.print(f"[bold yellow]{meta['tag'][1:]}[/bold yellow][bold red] was found on [bold yellow]{tracker}'s[/bold yellow] list of banned groups for type [bold yellow]{release_type}[/bold yellow].")
            if meta.get('unattended', False) or not Confirm.ask("[bold red] Upload Anyways?"):
                return True
            return False
    
    return False  # Good to go!

def get_missing(meta):
    info_notes = {
        'edition' : 'Special Edition/Release',
        'description' : "Please include Remux/Encode Notes if possible (either here or edit your upload)",
        'service' : "WEB Service e.g.(AMZN, NF)",
        'region' : "Disc Region",
        'imdb' : 'IMDb ID (tt1234567)',
        'distributor' : "Disc Distributor e.g.(BFI, Criterion, etc)"
    }
    missing = []
    if meta.get('imdb_id', '0') == '0':
        meta['imdb_id'] = '0'
        meta['potential_missing'].append('imdb_id')
    if len(meta['potential_missing']) > 0:
        for each in meta['potential_missing']:
            if str(meta.get(each, '')).replace(' ', '') in ["", "None", "0"]:
                if each == "imdb_id":
                    each = 'imdb' 
                missing.append(f"--{each} | {info_notes.get(each)}")
    if missing != []:
        console.print(Rule("Potentially missing information", style="bold yellow"))
        for each in missing:
            if each.split('|')[0].replace('--', '').strip() in ["imdb"]:
                console.print(Text(each, style="bold red"))
            else:
                console.print(each)

    console.print()
    return

# Dynamically load in requested trackers
def load_trackers(tracker_names, config=None):
    tracker_dir = Path(__file__).parent / "src" / "trackers"
    tracker_registry = {}
    ignore_files = {"COMMON.py", "UNIT3D_TEMPLATE.py", "__init__.py"}
    
    if not isinstance(tracker_names, list):
        tracker_names = [tracker_names]
    tracker_names = [t.strip().upper() for t in tracker_names if t.strip()]
    
    for tracker in tracker_names:
        if tracker in ignore_files or tracker == "MANUAL":  # Skip MANUAL as it’s not a module
            continue
            
        module_name = f"src.trackers.{tracker}"
        tracker_file = tracker_dir / f"{tracker}.py"
        
        if not tracker_file.exists():
            logging.error(f"[bold] NO SUCH TRACKER.[/bold] '{tracker}' not found at {tracker_file}")
            continue
            
        try:
            module = importlib.import_module(module_name)
            tracker_class = getattr(module, tracker, None)
            if not tracker_class:
                logging.warning(f"No class '{tracker}' found in {module_name}")
                continue
                
            # Instantiate temporarily to scrape metadata
            temp_instance = tracker_class(config=config)
            tracker_name = getattr(temp_instance, "tracker", tracker)  
            tracker_type = getattr(temp_instance, "type", "other")
            
            tracker_registry[tracker_name] = {
                "class": tracker_class,
                "type": tracker_type
            }
        except ImportError as e:
            logging.error(f"Failed to import {module_name}: {e}")
        except Exception as e:
            logging.error(f"Error processing {module_name}: {e}")
    
    return tracker_registry

def print_banner():
    ascii_art = r"""
      ┌────────────────────────────────────────────────────────────────────┐      
      │_____  __________ ______ _______ _______ ________ ________ ________ │      
      │__  / / /___  __ \___  / __  __ \___    |___  __ \___  __ \___  __ \│      
      │_  / / / __  /_/ /__  /  _  / / /__  /| |__  / / /__  /_/ /__  /_/ /│      
      │/ /_/ /  _  ____/ _  /___/ /_/ / _  ___ |_  /_/ / _  _, _/ _  _, _/ │      
      │\____/   /_/      /_____/\____/  /_/  |_|/_____/  /_/ |_|  /_/ |_|  │      
      └────────────────────────────────────────────────CvT─x─TheLDU.to─────┘      
                                                                                 
    """
    console.print(Align.center(Text(f"\n\n{ascii_art}\n", style='bold')))

def list_directory(directory):
    items = []
    for file in os.listdir(directory):
        if not file.startswith('.'):
            items.append(os.path.abspath(os.path.join(directory, file)))
    return items


if __name__ == '__main__':
    pyver = platform.python_version_tuple()
    if int(pyver[0]) != 3:
        console.print("[bold red]Python2 Detected, please use python3")
        exit()
    else:
        if int(pyver[1]) <= 6:
            console.print("[bold red]Python <= 3.6 Detected, please use Python >=3.7")
            loop = asyncio.get_event_loop()
            loop.run_until_complete(do_the_thing(base_dir))
        else:
            asyncio.run(do_the_thing(base_dir))
        

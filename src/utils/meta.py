import itertools
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.prompt import Prompt
from rich.text import Text

from src.args import apply_special_logic, build_parser, enforce_global_only, supplied_args
from src.name import NameGenerator
from src.utils.console import console, log


async def save_meta(meta: Dict, base_dir: Path, uuid: str) -> bool:
    """Save meta.json. Devuelve False si no se pudo escribir (el fallo se registra y no se propaga)."""
    try:
        meta_dir = base_dir / "tmp" / uuid
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta_path = meta_dir / "meta.json"
        # Escritura atómica: un corte a mitad no deja meta.json truncado.
        tmp_path = meta_path.with_name(meta_path.name + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, meta_path)
        log.debug(f"Saved meta.json to {meta_path}")
        return True
    except Exception as e:
        log.error(f"Failed to save meta.json: {e}")
        return False


def limpiar_tmp_item(base_dir: Path, path: str) -> bool:
    """Borra por completo ``<base_dir>/tmp/<uuid>`` (uuid = nombre de ``path``), el estado de una subida anterior.

    Sin ``--resume`` cada ítem empieza de cero: un BASE.torrent o unas capturas de una ejecución previa pueden
    no corresponder ya a los ficheros (se volvieron a preparar con el mismo nombre). Solo se borra un directorio
    real dentro de ``tmp``: ``tmp`` en sí puede ser un symlink (en la imagen apunta a /config), pero ``tmp/<uuid>``
    no se sigue si lo fuera. Un fallo se avisa y no aborta. Devuelve True si se borró.
    """
    uuid = Path(path).name
    if uuid in ("", ".", ".."):
        return False
    tmp_dir = base_dir / "tmp"
    item_dir = tmp_dir / uuid
    try:
        if not os.path.lexists(item_dir):
            return False
        if item_dir.is_symlink() or not item_dir.is_dir():
            log.warning(f"[yellow]Not wiping {item_dir}: it is not a real directory[/yellow]")
            return False
        if item_dir.resolve().parent != tmp_dir.resolve():
            log.warning(f"[yellow]Not wiping {item_dir}: it resolves outside tmp[/yellow]")
            return False
        shutil.rmtree(item_dir)
        log.debug(f"Wiped {item_dir}")
        return True
    except OSError as e:
        log.warning(f"[yellow]Could not wipe {item_dir}: {e}[/yellow]")
        return False


def queda_estado_viejo(base_dir: Path, path: str) -> bool:
    """True si ``tmp/<uuid>`` sigue ahí (un directorio, un enlace…) o no se puede comprobar si está: si no se sabe,
    se trata como que queda, para no seguir con restos de otra ejecución."""
    item_dir = base_dir / "tmp" / Path(path).name
    try:
        return item_dir.is_symlink() or item_dir.exists()
    except OSError:
        return True


async def marcar_paso_hecho(meta: Dict, paso: str, base_dir: Optional[Path] = None) -> None:
    """Anota ``paso`` en ``meta["pasos_hechos"]``, guarda meta.json y avisa a quien lanza Uploadrr.

    La línea ``Step done: <paso>`` va con ``print`` y no con Rich, que parte las líneas a 80 columnas en una
    tubería; emuwarez-uploadrr la lee tal cual. Se imprime también cuando el paso se reutiliza en un
    ``--resume``, para que la lista de cada ejecución esté completa.
    """
    pasos = meta.get("pasos_hechos")
    if not isinstance(pasos, list):
        pasos = []
    if paso not in pasos:
        pasos.append(paso)
    meta["pasos_hechos"] = pasos
    guardado = await save_meta(meta, Path(base_dir if base_dir is not None else meta["base_dir"]), meta["uuid"])
    if guardado:  # sin la marca en meta.json, un --resume no la encontraría: no se anuncia
        print(f"Step done: {paso}", flush=True)


async def load_existing_meta(base_dir: Path, path: str, new_meta: Dict) -> Dict:
    """Load existing metadata, overwriting specified keys with CLI Args."""
    meta_file = base_dir / "tmp" / Path(path).name / "meta.json"
    # fmt: off
    overwrite_keys = [
        "anon", "aviso_baja_calidad", "base_dir", "blu", "category", "client", "debug","deezer_manual", "desc",
        "descfile", "desclink", "draft", "dupe", "episode", "ffdebug", "freeleech", "full_dir",
        "hardcoded-subs", "hdb", "imdb", "imghost", "mal", "manual", "manual_edition", "manual_episode", "manual_name",
        "manual_season", "manual_source", "manual_type", "mbid_manual", "no_aka", "no_dub", "no_seed", "no_season", "no_tag",  "no_year",
        "nohash", "path", "personalrelease", "ptp", "qbit_cat", "qbit_tag", "randomized", "rehash",
        "region", "resolution", "resume", "screens", "season", "skip_dupe_check", "skip_imghost_upload", "solo_cliente", "tag", "torrent_creation",
        "trackers", "tmdb_manual", "unattended", "webdv",
        # XXX keys
         "jav", "pack", "pdb_manual", "user_cover", "user_banner", "user_trailer", "xmovies", "xscene", "xxx"
    ]
    # fmt: on
    my_meta = {
        "base_dir": str(new_meta.get("base_dir", base_dir)),
        "path": new_meta.get("path", path),
        "uuid": new_meta.get("uuid", Path(path).name),
    }

    try:
        with meta_file.open("r", encoding="utf-8") as f:
            saved_meta = json.load(f)
            for key, value in saved_meta.items():
                if key not in my_meta:
                    my_meta[key] = value
            for key in overwrite_keys:
                if key in new_meta and key not in ["base_dir", "path", "uuid"]:
                    my_meta[key] = new_meta[key]
            for key, value in new_meta.items():
                if key not in my_meta:
                    my_meta[key] = value
    except FileNotFoundError:
        log.debug(f"No existing meta.json at {meta_file}")
        for key, value in new_meta.items():
            if key not in my_meta:
                my_meta[key] = value
    except Exception as e:
        log.error(f"Error loading meta.json: {e}")
        for key, value in new_meta.items():
            if key not in my_meta:
                my_meta[key] = value
    log.ddebug(f"meta keys after load_existing_meta: {list(my_meta.keys())}")
    return my_meta


async def edit_meta(
    prep, meta: Dict[str, Any], trackers: Optional[List[str]], tracker_registry: Optional[Dict], config: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Handle manual metadata correction with CLI-like editing."""
    parser = build_parser(config)  # reuse standard parser

    while True:
        console.print("Input args to correct (e.g., --tag PMEDIA --tmdb 12345) or 'skip'")
        raw_input = Prompt.ask("").strip()

        if raw_input.lower() == "skip":
            return None

        if not raw_input:
            console.print("⚠️ Invalid input. Try again or 'skip'.", style="dim")
            continue

        try:
            edit_argv = raw_input.split()
            ns = parser.parse_args(edit_argv)
            enforce_global_only(ns, is_global=False, parser=parser)

        except (ValueError, SystemExit, Exception) as e:
            console.print(f"[red]❌ Parse error: {e}[/red]")
            console.print("[dim]Hint: Use --flag value (e.g., --tmdb 12345, --tag GROUP)[/dim]")
            continue

        edit_meta_dict = supplied_args(edit_argv, parser)
        new_meta = {**meta, **edit_meta_dict}
        new_meta = apply_special_logic(new_meta)
        new_meta["edit"] = True

        base_dir = Path(meta["base_dir"])
        new_meta = await load_existing_meta(base_dir, meta["path"], new_meta)

        reprocess_keys = ["tmdb_manual", "imdb", "mbid", "discogs", "pdb_manual"]
        if any(key in edit_meta_dict for key in reprocess_keys):
            new_meta = await prep.gather_prep(meta=new_meta, mode="cli")

        # Regenerate name (if non-sports)
        if new_meta.get("category", "").upper() != "SPORTS":
            name_gen = NameGenerator()
            (
                new_meta["name_notag"],
                new_meta["name"],
                new_meta["clean_name"],
                new_meta["potential_missing"],
            ) = await name_gen.get_name(new_meta)

        # Save
        await save_meta(new_meta, base_dir, new_meta["uuid"])

        # Confirm
        from src.utils.upload import get_confirmation

        if await get_confirmation(new_meta, trackers, tracker_registry, config):
            return new_meta

        meta = new_meta


def get_missing(meta: Dict) -> None:
    """Display potentially missing metadata."""
    info_notes = {
        "edition": "Special Edition/Release",
        "description": "Remux/Encode Notes",
        "service": "WEB Service (e.g., AMZN)",
        "region": "Disc Region",
        "imdb": "IMDb ID (tt1234567)",
        "distributor": "Disc Distributor (e.g., Criterion)",
    }
    missing = [f"--{k} | {info_notes.get(k)}" for k in meta.get("potential_missing", []) if not str(meta.get(k, "")).strip()]
    if missing:
        console.print("Potentially missing information", style="bold yellow")
        for item in missing:
            console.print(Text(item, style="bold red" if "imdb" in item else "yellow"))


def get_video_codec(bdinfo: Dict) -> str:
    codecs = {"MPEG-2 Video": "MPEG-2", "MPEG-4 AVC Video": "AVC", "MPEG-H HEVC Video": "HEVC", "VC-1 Video": "VC-1"}
    codec = codecs.get(bdinfo["video"][0]["codec"], "")
    return codec


def find_largest_clpi(disc_path: str):
    """Find the largest .clpi file in BDMV/CLIPINF or BDMV/BACKUP/CLIPINF."""
    base_path = Path(disc_path)
    clpi_paths = [base_path / "BDMV" / "CLIPINF", base_path / "BDMV" / "BACKUP" / "CLIPINF"]

    largest_clpi = None
    max_size = 0

    for clpi_dir in clpi_paths:
        if clpi_dir.exists():
            for clpi_file in clpi_dir.glob("*.clpi"):
                file_size = clpi_file.stat().st_size
                if file_size > max_size:
                    max_size = file_size
                    largest_clpi = clpi_file

    return largest_clpi


def get_dvd_size(discs):
    sizes = []
    dvd_sizes = []
    for each in discs:
        sizes.append(each["size"])
    grouped_sizes = [list(i) for j, i in itertools.groupby(sorted(sizes))]
    for each in grouped_sizes:
        if len(each) > 1:
            dvd_sizes.append(f"{len(each)}x{each[0]}")
        else:
            dvd_sizes.append(each[0])
    dvd_sizes.sort()
    compact = " ".join(dvd_sizes)
    return compact

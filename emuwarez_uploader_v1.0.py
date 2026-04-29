#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emuwarez_upload.py

Automatiza el flujo eMuwarez + Uploadrr:

- Primer arranque: crea/verifica ./.venv, instala dependencias de Uploadrr y configura claves/rutas.
- Uploadrr se asume en ./Uploadrr, junto a este script.
- Gestiona configuración propia clara o cifrada e inyecta temporalmente en ./Uploadrr/data/config.yaml:
    DEFAULT.tmdb
    TRACKERS.EMU.api_key / anon
    SCREENS.API_KEYS / img_hosts
    TORRENT_CLIENTS.watch o TORRENT_CLIENTS.qbit
- Analiza MKV con MediaInfo.
- Busca en TMDb y muestra coincidencias ordenadas por similitud.
- Propone nomenclatura eMuwarez.
- En ejecución normal pregunta si continuar y aplica. --dry-run no toca nada.
- Si hay varios MKV, se trata como temporada completa y Uploadrr recibe la carpeta final.
"""

from __future__ import annotations

import argparse
import ast
import atexit
import base64
import difflib
import errno
import getpass
import hashlib
import hmac
import json
import logging
import os
import platform
import select
import signal
import ssl
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import venv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


APP_NAME = "emuwarez-renamer"
CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_ENC_FILE = CONFIG_DIR / "config.enc.json"
LOG_DIR = CONFIG_DIR / "logs"

LOGGER = logging.getLogger(APP_NAME)
LOGGER.addHandler(logging.NullHandler())

DEFAULT_TRACKER = "EMU"
DEFAULT_TAG = "EMUWAREZ"
DEFAULT_HARDLINK_BASE = "/mnt/storage/uploads/emu"
DEFAULT_WATCH_FOLDER = "~/test_torrents"
DEFAULT_IMAGE_HOST = "imgbox"
DEFAULT_IMG_HOSTS = ["imgbox", "ptpimg", "ptscreens", "onlyimage", "hamster", "imghost", "imgbb", "lensdump", "pixhost", "fastpic"]
IMAGE_API_HOSTS = ["imgbb", "ptpimg", "lensdump", "ptscreens", "onlyimage"]
IMAGE_HOST_LABELS = {
    "imgbb": "ImgBB",
    "ptpimg": "PTPIMG",
    "lensdump": "Lensdump",
    "ptscreens": "PTScreens",
    "onlyimage": "OnlyImage",
}

MIN_PYTHON = (3, 10)
RECOMMENDED_PYTHON = (3, 11)
PYTHON_CANDIDATES = ("python3.11", "python3.10", "python3.12", "python3.13", "python3")
NON_RECOMMENDED_VENV_ACCEPTED = False

TMDB_BASE = "https://api.themoviedb.org/3"
EMU_TEST_URL = "https://emuwarez.com/api/torrents/filter?perPage=1"

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m2ts", ".ts"}

REQUIRED_SYSTEM_TOOLS = ("mediainfo", "mkvpropedit")
SYSTEM_TOOL_PACKAGES = {"mediainfo": "mediainfo", "mkvpropedit": "mkvtoolnix"}

# Imports que Uploadrr puede tener en rutas opcionales. No se instalan,
# no aparecen como pendientes, y no bloquean el flujo EMU normal.
OPTIONAL_UPLOADRR_IMPORTS = {"awsmfunc", "torrentman", "vapoursynth", "oxipng", "pyoxipng"}

# Paquetes problemáticos en algunas distros/arquitecturas. Se intentan con
# una estrategia especial, pero si fallan no bloquean si Uploadrr arranca.
PROBLEMATIC_REQUIREMENT_NAMES = {"pyrobase"}

# El script usa stdlib para que pueda arrancar antes de crear el venv.
# Si en el futuro se añade alguna dependencia propia, ponerla aquí.
SCRIPT_PIP_DEPS: list[str] = []

IMPORT_TO_PIP = {
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "Crypto": "pycryptodome",
    "lxml": "lxml",
}

LANG_MAP = {
    "es": "ESP", "spa": "ESP", "spanish": "ESP", "castilian": "ESP", "castellano": "ESP", "es-es": "ESP",
    "es-419": "LAT", "latino": "LAT", "latam": "LAT", "latin american spanish": "LAT",
    "en": "ING", "eng": "ING", "english": "ING",
    "ja": "JAP", "jpn": "JAP", "japanese": "JAP",
    "ko": "COR", "kor": "COR", "korean": "COR",
    "fr": "FRA", "fra": "FRA", "fre": "FRA", "french": "FRA",
    "de": "ALE", "deu": "ALE", "ger": "ALE", "german": "ALE",
    "it": "ITA", "ita": "ITA", "italian": "ITA",
    "pt": "POR", "por": "POR", "portuguese": "POR",
    "ca": "CAT", "cat": "CAT", "catalan": "CAT",
}

SOURCE_PATTERNS = [
    (re.compile(r"\b(?:bd[ ._-]?remux|bluray[ ._-]?remux)\b", re.I), "BDRemux"),
    (re.compile(r"\b(?:full[ ._-]?bd|bd50|bd25)\b", re.I), "FBD"),
    (re.compile(r"\b(?:full[ ._-]?dvd|dvd9|dvd5)\b", re.I), "FDVD"),
    (re.compile(r"\b(?:web[ ._-]?dl|webdl)\b", re.I), "WEB-DL"),
    (re.compile(r"\b(?:web[ ._-]?rip|webrip)\b", re.I), "WEBRip"),
    (re.compile(r"\b(?:hdtv)\b", re.I), "HDTV"),
    (re.compile(r"\b(?:micro[ ._-]?hd|mhd)\b", re.I), "MHD"),
    (re.compile(r"\b(?:blu[ ._-]?ray|bdrip|brrip|bdr)\b", re.I), "BluRay"),
    (re.compile(r"\b(?:dvd[ ._-]?rip|dvdrip|dvd)\b", re.I), "SD"),
]

EPISODE_PATTERNS = [
    re.compile(r"(?i)\bS(?P<s>\d{1,2})[ ._-]*E(?P<e>\d{1,3})\b"),
    re.compile(r"(?i)\b(?P<s>\d{1,2})x(?P<e>\d{1,3})\b"),
    re.compile(r"(?i)\bSeason[ ._-]*(?P<s>\d{1,2}).*?Episode[ ._-]*(?P<e>\d{1,3})\b"),
    re.compile(r"(?i)\bTemporada[ ._-]*(?P<s>\d{1,2}).*?(?:Capitulo|Capítulo|Episodio)[ ._-]*(?P<e>\d{1,3})\b"),
]
SEASON_RE = re.compile(r"(?i)^(?:season|temporada)[ ._-]*(\d{1,2})$|^S(\d{1,2})$")


# -------------------------
# UI
# -------------------------

class UI:
    color = True
    silent = False
    R = "\033[0m"
    B = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GRN = "\033[32m"
    YEL = "\033[33m"
    BLU = "\033[34m"
    MAG = "\033[35m"
    CYN = "\033[36m"
    GRY = "\033[90m"


def color(text: Any, code: str) -> str:
    return f"{code}{text}{UI.R}" if UI.color else str(text)


def bold(x: Any) -> str:
    return color(x, UI.B)


def cyan(x: Any) -> str:
    return color(x, UI.CYN)


def green(x: Any) -> str:
    return color(x, UI.GRN)


def yellow(x: Any) -> str:
    return color(x, UI.YEL)


def red(x: Any) -> str:
    return color(x, UI.RED)


def magenta(x: Any) -> str:
    return color(x, UI.MAG)


def gray(x: Any) -> str:
    return color(x, UI.GRY)


def current_log_file() -> Path:
    return LOG_DIR / "emuwarez-renamer.log"


def setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = current_log_file()
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.DEBUG)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    LOGGER.addHandler(handler)
    LOGGER.propagate = False
    LOGGER.info("Inicio emuwarez-renamer")
    atexit.register(shutdown_logging)
    return log_file


def shutdown_logging() -> None:
    for handler in list(LOGGER.handlers):
        try:
            handler.flush()
            handler.close()
        finally:
            LOGGER.removeHandler(handler)


def show_log_path(log_file: Optional[Path] = None) -> None:
    path = log_file or current_log_file()
    print()
    print(gray("Log de diagnóstico:"))
    print("  " + cyan(path))


def log_debug(msg: str) -> None:
    LOGGER.debug(msg)


def ok(msg: str) -> None:
    LOGGER.info(msg)
    if not UI.silent:
        print(green("✓") + " " + msg)


def warn(msg: str) -> None:
    LOGGER.warning(msg)
    print(yellow("⚠") + " " + msg)


def err(msg: str) -> None:
    LOGGER.error(msg)
    print(red("✗") + " " + msg)


def action(msg: str) -> None:
    LOGGER.info(msg)
    if not UI.silent:
        print(magenta("→") + " " + msg)


def kv(key: str, value: Any, width: int = 24) -> None:
    if not UI.silent:
        print(f"  {gray((key + ':').ljust(width))} {cyan(value)}")


def status_kv(ok_status: bool, key: str, value: Any, width: int = 24) -> None:
    """Fila técnica alineada con estado visual."""
    if UI.silent:
        return
    mark = green("✓") if ok_status else red("✗")
    print(f"  {mark} {gray((key + ':').ljust(width))} {cyan(value)}")


def box(title: str, subtitle: Optional[str] = None) -> None:
    if UI.silent:
        return
    width = 78
    print()
    print(color("╔" + "═" * (width - 2) + "╗", UI.BLU))
    print(color("║", UI.BLU) + bold(title.center(width - 2)) + color("║", UI.BLU))
    if subtitle:
        print(color("║", UI.BLU) + cyan(subtitle.center(width - 2)) + color("║", UI.BLU))
    print(color("╚" + "═" * (width - 2) + "╝", UI.BLU))
    print()


def rule(title: Optional[str] = None) -> None:
    if UI.silent:
        return
    width = 78
    print()
    if title:
        rest = max(2, width - len(title) - 5)
        print(gray("─" * 2) + " " + bold(title) + " " + gray("─" * rest))
    else:
        print(gray("─" * width))
    print()


def die(msg: str, code: int = 1) -> None:
    err(msg)
    raise SystemExit(code)


def prompt(text: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    print()
    value = input(f"{cyan(text)}{gray(suffix)}: ").strip()
    return value if value else (default or "")


def prompt_secret(text: str, current: Optional[str] = None) -> str:
    print()
    if current:
        print(gray("  Ya hay un valor configurado. Pulsa Enter para conservarlo."))
    value = getpass.getpass(f"{cyan(text)}: ").strip()
    return value if value else (current or "")


def explain(text: str) -> None:
    for line in textwrap.dedent(text).strip().splitlines():
        print(gray("  " + line.strip()))


def prompt_with_help(text: str, help_text: str, default: Optional[str] = None) -> str:
    print()
    print(cyan(text))
    explain(help_text)
    suffix = f" [{default}]" if default not in (None, "") else ""
    value = input(f"{cyan('Valor')}{gray(suffix)}: ").strip()
    return value if value else (default or "")


def prompt_password_masked(prompt_text: str) -> str:
    """Lee una contraseña mostrando asteriscos. Si no hay TTY, usa getpass."""
    if not sys.stdin.isatty():
        return getpass.getpass(prompt_text)

    if os.name == "nt":
        try:
            import msvcrt  # type: ignore
            print(prompt_text, end="", flush=True)
            chars: list[str] = []
            while True:
                ch = msvcrt.getwch()
                if ch in {"\r", "\n"}:
                    print()
                    return "".join(chars)
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in {"\b", "\x7f"}:
                    if chars:
                        chars.pop()
                        print("\b \b", end="", flush=True)
                    continue
                if ch in {"\x00", "\xe0"}:
                    msvcrt.getwch()
                    continue
                chars.append(ch)
                print("*", end="", flush=True)
        except ImportError:
            return getpass.getpass(prompt_text)

    try:
        import termios
        import tty
    except ImportError:
        return getpass.getpass(prompt_text)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    chars: list[str] = []
    print(prompt_text, end="", flush=True)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in {"\r", "\n"}:
                print()
                return "".join(chars)
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x04":
                print()
                raise EOFError
            if ch in {"\x7f", "\b"}:
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
                continue
            if ch in {"\x00", "\xe0"}:
                continue
            chars.append(ch)
            print("*", end="", flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def prompt_secret_with_help(text: str, help_text: str, current: Optional[str] = None) -> str:
    print()
    print(cyan(text))
    explain(help_text)
    if current:
        print(gray("  Ya hay un valor configurado. Pulsa Enter para conservarlo."))
    value = prompt_password_masked(f"{cyan('Valor')}: ").strip()
    return value if value else (current or "")


def prompt_api_key_with_help(text: str, help_text: str, current: Optional[str] = None) -> str:
    print()
    print(cyan(text))
    explain(help_text)
    if current:
        print(gray(f"  Valor actual: {mask_secret(current)}. Pulsa Enter para conservarlo."))
    value = input(f"{cyan('Valor')}: ").strip()
    return value if value else (current or "")


def yesno_with_help(text: str, help_text: str, default: bool = False) -> bool:
    print()
    print(cyan(text))
    explain(help_text)
    suffix = "S/n" if default else "s/N"
    value = input(f"{cyan('Respuesta')} {gray('[' + suffix + ']')}: ").strip().lower()
    if not value:
        return default
    return value in {"s", "si", "sí", "y", "yes"}


def yesno(text: str, default: bool = False) -> bool:
    suffix = "S/n" if default else "s/N"
    print()
    value = input(f"{cyan(text)} {gray('[' + suffix + ']')}: ").strip().lower()
    if not value:
        return default
    return value in {"s", "si", "sí", "y", "yes"}


def shell_join(cmd: list[str]) -> str:
    import shlex
    return " ".join(shlex.quote(str(x)) for x in cmd)


def version_tuple_to_text(version) -> str:
    return ".".join(str(x) for x in version)


def parse_python_version(text: str) -> Optional[tuple[int, int, int]]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def python_version(executable) -> Optional[tuple[int, int, int]]:
    try:
        proc = subprocess.run(
            [str(executable), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return parse_python_version(proc.stdout.strip())


def python_executable_path(executable: str) -> Optional[str]:
    if os.path.sep in executable:
        path = Path(executable).expanduser()
        return str(path) if path.exists() else None
    return shutil.which(executable)


def ensure_launcher_python_supported() -> None:
    current = sys.version_info[:3]
    if current < (*MIN_PYTHON, 0):
        err(f"Python actual: {version_tuple_to_text(current)}")
        print(
            yellow("Este script requiere Python 3.10 o superior para ejecutarse.\n")
            + "Recomendado y seguro: "
            + cyan("Python 3.11")
        )
        found = python_executable_path("python3.11")
        if found:
            print("Ejecuta:\n  " + cyan(f"{found} {Path(__file__).name} --configure"))
        raise SystemExit(1)


def confirm_launcher_python_recommended() -> None:
    """No pregunta por la versión del Python lanzador.

    El script puede ejecutarse con Python >= 3.10; la versión importante para
    dependencias es la del venv, y ahí se prioriza Python 3.11.
    """
    return

def choose_venv_python(preferred: Optional[str] = None, *, allow_prompt: bool = True) -> str:
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    candidates.extend([x for x in PYTHON_CANDIDATES if x not in candidates])

    discovered: list[tuple[str, tuple[int, int, int]]] = []
    for candidate in candidates:
        exe = python_executable_path(candidate)
        if not exe:
            continue
        ver = python_version(exe)
        if not ver:
            continue
        if ver >= (*MIN_PYTHON, 0):
            discovered.append((exe, ver))

    if not discovered:
        die(
            "No se encontró ningún Python válido para crear el venv.\n"
            "Recomendado: python3.11\n"
            "Mínimo aceptado: Python 3.10\n"
            "Instala Python 3.11 y python3.11-venv antes de continuar."
        )

    for exe, ver in discovered:
        if ver[:2] == RECOMMENDED_PYTHON:
            kv("Python recomendado", f"{exe} ({version_tuple_to_text(ver)})")
            return exe

    exe, ver = discovered[0]
    warn("No se encontró python3.11")
    kv("Python alternativo", f"{exe} ({version_tuple_to_text(ver)})")
    print(
        yellow("Aviso: ")
        + "el script está recomendado y probado para Python 3.11. "
        + "Otra versión puede causar errores con dependencias de Uploadrr."
    )
    if allow_prompt and not yesno(f"¿Continuar con Python {version_tuple_to_text(ver)}?", False):
        die("Cancelado. Instala python3.11 y vuelve a ejecutar.")
    return exe


def venv_version() -> Optional[tuple[int, int, int]]:
    if not venv_python().exists():
        return None
    return python_version(venv_python())


def check_venv_python_version(preferred: Optional[str] = None) -> None:
    global NON_RECOMMENDED_VENV_ACCEPTED

    ver = venv_version()
    if not ver:
        return

    if ver < (*MIN_PYTHON, 0):
        warn(f"El venv usa Python {version_tuple_to_text(ver)}, que no está soportado.")
        recreate_venv(preferred_python=preferred, force=True)
        return

    if ver[:2] == RECOMMENDED_PYTHON:
        status_kv(True, "Python venv", f"{version_tuple_to_text(ver)} recomendado")
        return

    if NON_RECOMMENDED_VENV_ACCEPTED:
        status_kv(True, "Python venv", f"{version_tuple_to_text(ver)} no recomendado, aceptado")
        return

    status_kv(False, "Python venv", f"{version_tuple_to_text(ver)} no recomendado")
    print(
        yellow("Aviso: ")
        + "Python 3.11 es la versión recomendada y segura. "
        + "Este entorno puede funcionar, pero podría fallar con algunas dependencias."
    )
    py311 = python_executable_path("python3.11")
    if py311 and yesno("¿Recrear .venv usando python3.11?", True):
        recreate_venv(preferred_python="python3.11", force=True)
        return
    if not yesno("¿Continuar usando este venv igualmente?", False):
        die("Cancelado por versión de Python no recomendada en el venv.")
    NON_RECOMMENDED_VENV_ACCEPTED = True


def maybe_offer_system_update() -> None:
    """Ofrece actualizar el sistema antes del bootstrap. Nunca lo hace sin permiso."""
    if platform.system().lower() != "linux":
        return
    if not shutil.which("apt"):
        return
    if not sys.stdin.isatty():
        return

    rule("Comprobación previa del sistema")
    print(
        "Es recomendable tener el sistema actualizado antes de compilar/instalar dependencias.\n"
        + yellow("Aviso: ")
        + "un upgrade puede tardar y cambiar paquetes del sistema."
    )
    if not yesno("¿Ejecutar ahora 'sudo apt update && sudo apt upgrade -y'?", False):
        return

    sudo = [] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["sudo"]
    if sudo and not shutil.which("sudo"):
        warn("sudo no está disponible. Ejecuta manualmente: sudo apt update && sudo apt upgrade -y")
        return

    for cmd in ([*sudo, "apt", "update"], [*sudo, "apt", "upgrade", "-y"]):
        action(shell_join(cmd))
        proc = subprocess.run(cmd, cwd=script_dir())
        if proc.returncode != 0:
            warn(f"El comando falló: {shell_join(cmd)}")
            if not yesno("¿Continuar igualmente?", False):
                die("Cancelado durante actualización del sistema.")
            return
    ok("Actualización del sistema completada")


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def install_system_packages(packages: list[str]) -> bool:
    if platform.system().lower() != "linux" or not shutil.which("apt"):
        warn("Instalación automática solo soportada con apt en Linux.")
        return False
    sudo = [] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["sudo"]
    if sudo and not shutil.which("sudo"):
        warn("sudo no está disponible. Instala manualmente: sudo apt install " + " ".join(packages))
        return False
    if yesno("¿Ejecutar 'sudo apt update' antes de instalar?", True):
        proc = subprocess.run([*sudo, "apt", "update"], cwd=script_dir())
        if proc.returncode != 0:
            warn("apt update falló.")
    cmd = [*sudo, "apt", "install", "-y", *packages]
    action(shell_join(cmd))
    proc = subprocess.run(cmd, cwd=script_dir())
    return proc.returncode == 0


def ensure_system_tools(tools: tuple[str, ...] = REQUIRED_SYSTEM_TOOLS, *, allow_install: bool = True, fatal_mediainfo: bool = True) -> bool:
    missing = [tool for tool in tools if not command_exists(tool)]

    for tool in tools:
        found = shutil.which(tool)
        if found:
            status_kv(True, tool, found)
        else:
            pkg = SYSTEM_TOOL_PACKAGES.get(tool, "paquete desconocido")
            status_kv(False, tool, f"NO ENCONTRADO · apt: {pkg}")

    if not missing:
        return True

    packages: list[str] = []
    for tool in missing:
        pkg = SYSTEM_TOOL_PACKAGES.get(tool)
        if pkg and pkg not in packages:
            packages.append(pkg)

    warn("Faltan herramientas externas requeridas: " + ", ".join(missing))
    if allow_install and packages and sys.stdin.isatty():
        kv("Paquetes apt", " ".join(packages))
        if yesno("¿Instalar ahora estas dependencias de sistema?", True):
            if install_system_packages(packages):
                still_missing = [tool for tool in tools if not command_exists(tool)]
                if not still_missing:
                    for tool in tools:
                        kv(tool, shutil.which(tool))
                    ok("Herramientas externas instaladas correctamente")
                    return True
                warn("Siguen faltando: " + ", ".join(still_missing))

    if "mediainfo" in missing and fatal_mediainfo:
        die("mediainfo es obligatorio. Instala: sudo apt install mediainfo")
    if "mkvpropedit" in missing:
        warn("mkvpropedit no está disponible; no se podrá cambiar el título interno MKV. Paquete: mkvtoolnix")
    return not ("mediainfo" in missing and fatal_mediainfo)

def maybe_offer_python311_install() -> None:
    py311 = python_executable_path("python3.11")
    if py311:
        status_kv(True, "Python 3.11", py311)
        return

    status_kv(False, "Python 3.11", "no encontrado")
    if platform.system().lower() != "linux" or not shutil.which("apt") or not sys.stdin.isatty():
        warn("No se encontró python3.11. Recomendado: instalar Python 3.11 antes de continuar.")
        return

    print("Python 3.11 es la versión recomendada y segura para crear .venv.")
    if not yesno("¿Intentar instalar Python 3.11 y python3.11-venv ahora?", False):
        return
    install_system_packages(["python3.11", "python3.11-venv"])


# -------------------------
# Paths / config
# -------------------------

def script_dir() -> Path:
    return Path(__file__).resolve().parent


def uploadrr_dir() -> Path:
    base = script_dir()
    if (base / "upload.py").exists() and (base / "data").is_dir():
        return base
    return base / "Uploadrr"


def uploadrr_py() -> Path:
    return uploadrr_dir() / "upload.py"


def uploadrr_config() -> Path:
    return uploadrr_dir() / "data" / "config.yaml"


def venv_path() -> Path:
    return script_dir() / ".venv"


def venv_python() -> Path:
    return venv_path() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def expand_path(value: str | Path) -> Path:
    return Path(str(value)).expanduser().resolve()


@dataclass
class AppConfig:
    hardlink_base: Path
    tag: str
    tmdb_api_key: Optional[str] = None
    emu_api_key: Optional[str] = None
    emu_anon: bool = False
    image_default_host: str = DEFAULT_IMAGE_HOST
    image_api_keys: dict[str, Optional[str]] = field(default_factory=dict)
    img_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_IMG_HOSTS))
    torrent_client: str = "watch"
    watch_folder: Optional[Path] = None
    qbit_url: str = "http://127.0.0.1"
    qbit_port: str = "8080"
    qbit_user: str = ""
    qbit_pass: str = ""
    qbit_torrent_storage_dir: str = ""
    qbit_content_layout: str = "Original"
    qbit_verify_certificate: bool = False
    config_encrypted: bool = False


def clean_optional_secret(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip().strip("\"'")
    if not value or value.lower() in {"null", "none", "~", "api_key", "apitmdb", "emuapikey"}:
        return None
    return value


def mask_secret(value: Optional[str]) -> str:
    value = clean_optional_secret(value)
    if not value:
        return "no configurado"
    if len(value) <= 8:
        return "configurado"
    return value[:4] + "…" + value[-4:]


def app_config_to_data(cfg: AppConfig) -> dict[str, Any]:
    return {
        "hardlink_base": str(cfg.hardlink_base),
        "release_tag": cfg.tag,
        "tmdb_api_key": cfg.tmdb_api_key,
        "emu_api_key": cfg.emu_api_key,
        "emu_anon": cfg.emu_anon,
        "image_default_host": cfg.image_default_host,
        "image_api_keys": cfg.image_api_keys,
        "img_hosts": cfg.img_hosts,
        "torrent_client": cfg.torrent_client,
        "watch_folder": str(cfg.watch_folder) if cfg.watch_folder else DEFAULT_WATCH_FOLDER,
        "qbit_url": cfg.qbit_url,
        "qbit_port": cfg.qbit_port,
        "qbit_user": cfg.qbit_user,
        "qbit_pass": cfg.qbit_pass,
        "qbit_torrent_storage_dir": cfg.qbit_torrent_storage_dir,
        "qbit_content_layout": cfg.qbit_content_layout,
        "qbit_verify_certificate": cfg.qbit_verify_certificate,
    }


def app_config_from_data(data: dict[str, Any], *, encrypted: bool = False) -> AppConfig:
    lines = read_yaml_lines() if uploadrr_config().exists() else []
    image_api_keys = {host: clean_optional_secret((data.get("image_api_keys") or {}).get(host)) for host in IMAGE_API_HOSTS}
    if not any(image_api_keys.values()):
        for host in IMAGE_API_HOSTS:
            image_api_keys[host] = clean_optional_secret(get_screen_api_key(lines, host))

    img_hosts = data.get("img_hosts") or get_img_hosts(lines) or list(DEFAULT_IMG_HOSTS)
    torrent_client = (data.get("torrent_client") or get_block_key(lines, "DEFAULT", "default_torrent_client") or "watch").strip("\"'")
    watch_folder_value = data.get("watch_folder") or get_watch_folder(lines) or DEFAULT_WATCH_FOLDER

    return AppConfig(
        hardlink_base=expand_path(data.get("hardlink_base", DEFAULT_HARDLINK_BASE)),
        tag=(data.get("release_tag") or data.get("tag") or DEFAULT_TAG).upper(),
        tmdb_api_key=clean_optional_secret(data.get("tmdb_api_key") or get_block_key(lines, "DEFAULT", "tmdb")),
        emu_api_key=clean_optional_secret(data.get("emu_api_key") or get_emu_api_key(lines)),
        emu_anon=bool(data.get("emu_anon", get_emu_anon(lines) or False)),
        image_default_host=(data.get("image_default_host") or DEFAULT_IMAGE_HOST).strip(),
        image_api_keys=image_api_keys,
        img_hosts=list(dict.fromkeys([str(x).strip() for x in img_hosts if str(x).strip()] + DEFAULT_IMG_HOSTS)),
        torrent_client=torrent_client,
        watch_folder=expand_path(watch_folder_value),
        qbit_url=str(data.get("qbit_url") or "http://127.0.0.1"),
        qbit_port=str(data.get("qbit_port") or "8080"),
        qbit_user=str(data.get("qbit_user") or ""),
        qbit_pass=str(data.get("qbit_pass") or ""),
        qbit_torrent_storage_dir=str(data.get("qbit_torrent_storage_dir") or ""),
        qbit_content_layout=str(data.get("qbit_content_layout") or "Original"),
        qbit_verify_certificate=bool(data.get("qbit_verify_certificate", False)),
        config_encrypted=encrypted,
    )


def _derive_config_keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    key = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return key[:32], key[32:]


def _xor_hmac_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out[: len(data)]))


def encrypt_config_payload(data: dict[str, Any], passphrase: str) -> dict[str, Any]:
    salt = os.urandom(16)
    nonce = os.urandom(16)
    enc_key, mac_key = _derive_config_keys(passphrase, salt)
    plain = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ciphertext = _xor_hmac_stream(plain, enc_key, nonce)
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return {
        "version": 1,
        "kdf": "scrypt",
        "cipher": "hmac-sha256-stream",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
    }


def decrypt_config_payload(payload: dict[str, Any], passphrase: str) -> dict[str, Any]:
    salt = base64.b64decode(payload["salt"])
    nonce = base64.b64decode(payload["nonce"])
    ciphertext = base64.b64decode(payload["ciphertext"])
    tag = base64.b64decode(payload["tag"])
    enc_key, mac_key = _derive_config_keys(passphrase, salt)
    expected = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Contraseña incorrecta o configuración cifrada dañada.")
    plain = _xor_hmac_stream(ciphertext, enc_key, nonce)
    return json.loads(plain.decode("utf-8"))


PASSPHRASE_SYMBOLS = "!@#$%^&*_-+=.,:;"


def passphrase_errors(value: str) -> list[str]:
    errors: list[str] = []
    if len(value) < 12:
        errors.append("mínimo 12 caracteres")
    if not any(ch.isupper() for ch in value):
        errors.append("al menos una mayúscula")
    if not any(ch.islower() for ch in value):
        errors.append("al menos una minúscula")
    if not any(ch.isdigit() for ch in value):
        errors.append("al menos un número")
    if not any(ch in PASSPHRASE_SYMBOLS for ch in value):
        errors.append("al menos un símbolo permitido")
    if any(ch.isspace() for ch in value):
        errors.append("sin espacios, tabuladores ni saltos de línea")

    allowed = set(PASSPHRASE_SYMBOLS)
    invalid = sorted({ch for ch in value if not (ch.isalnum() or ch in allowed)})
    if invalid:
        rendered = " ".join(repr(ch) for ch in invalid[:8])
        errors.append(f"caracteres no permitidos: {rendered}")
    return errors


def prompt_new_passphrase() -> str:
    while True:
        print()
        print(cyan("Passphrase de cifrado"))
        explain(
            "Debe tener mínimo 12 caracteres, mayúscula, minúscula, número y símbolo. "
            f"Símbolos permitidos: {PASSPHRASE_SYMBOLS}"
        )
        p1 = prompt_password_masked(f"{cyan('Valor')}: ").strip()
        p2 = prompt_password_masked(f"{cyan('Repite valor')}: ").strip()
        if p1 != p2:
            warn("Las contraseñas no coinciden.")
            continue
        errors = passphrase_errors(p1)
        if errors:
            err("La passphrase no cumple los requisitos:")
            for item in errors:
                print("  " + yellow("- " + item))
            continue
        return p1


def load_app_config(passphrase: Optional[str] = None) -> Optional[AppConfig]:
    try:
        if CONFIG_ENC_FILE.exists():
            if passphrase is None:
                if not sys.stdin.isatty():
                    die(f"La configuración está cifrada. Usa --config-pass-file o --config-pass-env para desbloquear {CONFIG_ENC_FILE}.")
                passphrase = prompt_password_masked(f"{cyan('Contraseña de configuración cifrada')}: ").strip()
            payload = json.loads(CONFIG_ENC_FILE.read_text(encoding="utf-8"))
            data = decrypt_config_payload(payload, passphrase)
            return app_config_from_data(data, encrypted=True)

        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return app_config_from_data(data, encrypted=False)

        return None
    except Exception as exc:
        if CONFIG_ENC_FILE.exists():
            die(f"No se pudo desbloquear la configuración cifrada: {exc}")
        warn(f"No se pudo leer la configuración propia: {exc}")
        return None


def save_app_config(cfg: AppConfig, *, encrypt: bool = False, passphrase: Optional[str] = None) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = app_config_to_data(cfg)
    if encrypt:
        if not passphrase:
            passphrase = prompt_new_passphrase()
        CONFIG_ENC_FILE.write_text(json.dumps(encrypt_config_payload(data, passphrase), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
        cfg.config_encrypted = True
        ok(f"Configuración cifrada guardada en {CONFIG_ENC_FILE}")
        return

    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if CONFIG_ENC_FILE.exists():
        CONFIG_ENC_FILE.unlink()
    cfg.config_encrypted = False
    ok(f"Configuración guardada en {CONFIG_FILE}")


# -------------------------
# Uploadrr config.yaml line-based editor
# -------------------------

def read_yaml_lines() -> list[str]:
    if not uploadrr_config().exists():
        return []
    return uploadrr_config().read_text(encoding="utf-8", errors="ignore").splitlines()


def write_yaml_lines(lines: list[str]) -> None:
    path = uploadrr_config()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def find_top_block(lines: list[str], key: str) -> tuple[int, int]:
    start = -1
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:\s*(?:#.*)?$", line):
            start = i
            break
    if start < 0:
        return -1, -1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j] and not lines[j].startswith((" ", "\t", "#")) and re.match(r"^[^:#]+\s*:", lines[j]):
            end = j
            break
    return start, end


def get_block_key(lines: list[str], block: str, key: str, indent: int = 2) -> Optional[str]:
    start, end = find_top_block(lines, block)
    if start < 0:
        return None
    pattern = re.compile(rf"^\s{{{indent}}}{re.escape(key)}\s*:\s*(.*?)\s*(?:#.*)?$")
    for line in lines[start + 1 : end]:
        m = pattern.match(line)
        if m:
            return m.group(1).strip().strip("\"'")
    return None


def set_block_key(lines: list[str], block: str, key: str, value: str, indent: int = 2) -> str:
    start, end = find_top_block(lines, block)
    entry = " " * indent + f"{key}: {value}"
    if start < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"{block}:", entry])
        return f"{block}.{key} creado"

    pattern = re.compile(rf"^\s{{{indent}}}{re.escape(key)}\s*:")
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            lines[i] = entry
            return f"{block}.{key} actualizado"

    lines.insert(start + 1, entry)
    return f"{block}.{key} creado"


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    value = str(value)
    return "'" + value.replace("'", "''") + "'"


def python_literal_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    return repr(str(value))


def render_inline_mapping(mapping: dict[str, Any]) -> str:
    return "{" + ", ".join(f"{repr(k)}: {python_literal_scalar(v)}" for k, v in mapping.items()) + "}"


def find_child_block(lines: list[str], top_block: str, child: str, indent: int = 2) -> tuple[int, int]:
    start, end = find_top_block(lines, top_block)
    if start < 0:
        return -1, -1
    pattern = re.compile(rf"^\s{{{indent}}}{re.escape(child)}\s*:")
    child_start = -1
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            child_start = i
            break
    if child_start < 0:
        return -1, -1
    child_end = end
    next_child = re.compile(rf"^\s{{{indent}}}\S[^:]*\s*:")
    for j in range(child_start + 1, end):
        if next_child.match(lines[j]):
            child_end = j
            break
    return child_start, child_end


def set_child_inline_mapping(lines: list[str], top_block: str, child: str, mapping: dict[str, Any], indent: int = 2) -> str:
    top_start, top_end = find_top_block(lines, top_block)
    entry = " " * indent + f"{child}: {render_inline_mapping(mapping)}"
    if top_start < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"{top_block}:", entry])
        return f"{top_block}.{child} creado"

    child_start, child_end = find_child_block(lines, top_block, child, indent=indent)
    if child_start < 0:
        lines.insert(top_start + 1, entry)
        return f"{top_block}.{child} creado"

    lines[child_start:child_end] = [entry]
    return f"{top_block}.{child} actualizado"


def get_emu_api_key(lines: list[str]) -> Optional[str]:
    start, end = find_child_block(lines, "TRACKERS", "EMU", indent=2)
    if start < 0:
        return None
    for line in lines[start + 1 : end]:
        m = re.match(r"^\s{4}api_key\s*:\s*(.*?)\s*(?:#.*)?$", line)
        if m:
            return clean_optional_secret(m.group(1))
    return None


def get_emu_anon(lines: list[str]) -> Optional[bool]:
    start, end = find_child_block(lines, "TRACKERS", "EMU", indent=2)
    if start < 0:
        return None
    for line in lines[start + 1 : end]:
        m = re.match(r"^\s{4}anon\s*:\s*(.*?)\s*(?:#.*)?$", line)
        if m:
            return m.group(1).strip().strip("\"'").lower() in {"true", "yes", "1", "si", "sí"}
    return None


def set_emu_api_key(lines: list[str], api_key: str) -> str:
    return set_emu_config(lines, api_key=api_key, anon=None)


def set_emu_config(lines: list[str], api_key: Optional[str] = None, anon: Optional[bool] = None) -> str:
    trackers_start, trackers_end = find_top_block(lines, "TRACKERS")
    if trackers_start < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("TRACKERS:")
        trackers_start = len(lines) - 1

    emu_start, emu_end = find_child_block(lines, "TRACKERS", "EMU", indent=2)
    if emu_start < 0:
        insert_at = trackers_start + 1
        block = ["  EMU:"]
        if api_key is not None:
            block.append(f"    api_key: {yaml_scalar(api_key)}")
        block.append(f"    anon: {yaml_scalar(False if anon is None else anon)}")
        lines[insert_at:insert_at] = block
        return "TRACKERS.EMU creado"

    changed = False
    if api_key is not None:
        rendered = f"    api_key: {yaml_scalar(api_key)}"
        for i in range(emu_start + 1, emu_end):
            if re.match(r"^\s{4}api_key\s*:", lines[i]):
                lines[i] = rendered
                changed = True
                break
        if not changed:
            lines.insert(emu_start + 1, rendered)
            emu_end += 1
            changed = True

    if anon is not None:
        rendered = f"    anon: {yaml_scalar(anon)}"
        replaced = False
        for i in range(emu_start + 1, emu_end):
            if re.match(r"^\s{4}anon\s*:", lines[i]):
                lines[i] = rendered
                replaced = True
                break
        if not replaced:
            lines.insert(emu_start + 1, rendered)
        changed = True

    return "TRACKERS.EMU actualizado" if changed else "TRACKERS.EMU sin cambios"


def get_torrent_client(lines: list[str]) -> str:
    return (get_block_key(lines, "DEFAULT", "default_torrent_client") or "watch").strip("\"'")


def get_watch_folder(lines: list[str]) -> Optional[str]:
    # Formato real de Uploadrr: TORRENT_CLIENTS.watch como dict inline.
    tc_start, tc_end = find_child_block(lines, "TORRENT_CLIENTS", "watch", indent=2)
    if tc_start >= 0:
        block = "\n".join(lines[tc_start:tc_end])
    else:
        start, end = find_top_block(lines, "watch")
        block = "\n".join(lines[start:end]) if start >= 0 else "\n".join(lines)

    m = re.search(r"watch_folder['\"]?\s*:\s*['\"]([^'\"]+)", block)
    if m:
        return m.group(1)

    m = re.search(r"^\s+watch_folder\s*:\s*(.*?)\s*$", block, re.M)
    if m:
        return m.group(1).strip().strip("\"'")
    return None


def set_watch_folder(lines: list[str], folder: str) -> str:
    set_block_key(lines, "DEFAULT", "default_torrent_client", yaml_scalar("watch"))
    return set_child_inline_mapping(
        lines,
        "TORRENT_CLIENTS",
        "watch",
        {"torrent_client": "watch", "watch_folder": folder},
        indent=2,
    )


def set_qbit_config(lines: list[str], cfg: AppConfig) -> str:
    set_block_key(lines, "DEFAULT", "default_torrent_client", yaml_scalar("qbit"))
    mapping = {
        "torrent_client": "qbit",
        "enable_search": True,
        "qbit_url": cfg.qbit_url,
        "qbit_port": cfg.qbit_port,
        "qbit_user": cfg.qbit_user,
        "qbit_pass": cfg.qbit_pass,
        "torrent_storage_dir": cfg.qbit_torrent_storage_dir,
        "content_layout": cfg.qbit_content_layout,
        "VERIFY_WEBUI_CERTIFICATE": cfg.qbit_verify_certificate,
    }
    return set_child_inline_mapping(lines, "TORRENT_CLIENTS", "qbit", mapping, indent=2)


def tmdb_api_key() -> Optional[str]:
    return clean_optional_secret(get_block_key(read_yaml_lines(), "DEFAULT", "tmdb"))


def emu_api_key() -> Optional[str]:
    return get_emu_api_key(read_yaml_lines())


def get_screen_api_key(lines: list[str], host: str) -> Optional[str]:
    api_start, api_end = find_child_block(lines, "SCREENS", "API_KEYS", indent=2)
    if api_start < 0:
        return None
    for line in lines[api_start + 1 : api_end]:
        m = re.match(rf"^\s{{4}}{re.escape(host)}\s*:\s*(.*?)\s*(?:#.*)?$", line)
        if m:
            return clean_optional_secret(m.group(1))
    return None


def set_screen_api_key(lines: list[str], host: str, api_key: Optional[str]) -> str:
    rendered = f"    {host}: {yaml_scalar(clean_optional_secret(api_key))}"
    screens_start, _ = find_top_block(lines, "SCREENS")
    if screens_start < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["SCREENS:", "  API_KEYS:", rendered])
        return f"SCREENS.API_KEYS.{host} creado"

    api_start, api_end = find_child_block(lines, "SCREENS", "API_KEYS", indent=2)
    if api_start < 0:
        lines.insert(screens_start + 1, "  API_KEYS:")
        lines.insert(screens_start + 2, rendered)
        return f"SCREENS.API_KEYS.{host} creado"

    for i in range(api_start + 1, api_end):
        if re.match(rf"^\s{{4}}{re.escape(host)}\s*:", lines[i]):
            lines[i] = rendered
            return f"SCREENS.API_KEYS.{host} actualizado"
    lines.insert(api_start + 1, rendered)
    return f"SCREENS.API_KEYS.{host} creado"


def get_screen_api_keys(lines: list[str]) -> dict[str, Optional[str]]:
    return {host: get_screen_api_key(lines, host) for host in IMAGE_API_HOSTS}


def set_screen_api_keys(lines: list[str], keys: dict[str, Optional[str]]) -> list[str]:
    return [set_screen_api_key(lines, host, keys.get(host)) for host in IMAGE_API_HOSTS]


def get_imgbb_api_key(lines: list[str]) -> Optional[str]:
    return get_screen_api_key(lines, "imgbb")


def set_imgbb_api_key(lines: list[str], api_key: Optional[str]) -> str:
    return set_screen_api_key(lines, "imgbb", api_key)


def imgbb_api_key() -> Optional[str]:
    return get_imgbb_api_key(read_yaml_lines())


def get_img_hosts(lines: list[str]) -> list[str]:
    start, end = find_top_block(lines, "SCREENS")
    if start < 0:
        return []
    for line in lines[start + 1 : end]:
        m = re.match(r"^\s{2}img_hosts\s*:\s*\[(.*?)\](.*)$", line)
        if m:
            hosts = [x.strip().strip("\"'") for x in m.group(1).split(",")]
            return [x for x in hosts if x]
    return []


def build_img_hosts_order(default_host: str, image_api_keys: dict[str, Optional[str]]) -> list[str]:
    configured = [host for host in IMAGE_API_HOSTS if clean_optional_secret(image_api_keys.get(host))]
    ordered: list[str] = []
    for host in [*configured, default_host, *DEFAULT_IMG_HOSTS]:
        host = (host or "").strip()
        if host and host not in ordered:
            ordered.append(host)
    return ordered


def set_img_hosts(lines: list[str], hosts: list[str]) -> str:
    screens_start, screens_end = find_top_block(lines, "SCREENS")
    entry = "  img_hosts: [" + ", ".join(hosts) + "] # order dictates prefrence!"
    if screens_start < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["SCREENS:", entry])
        return "SCREENS.img_hosts creado"
    for i in range(screens_start + 1, screens_end):
        if re.match(r"^\s{2}img_hosts\s*:", lines[i]):
            lines[i] = entry
            return "SCREENS.img_hosts actualizado"
    lines.insert(screens_start + 1, entry)
    return "SCREENS.img_hosts creado"


def uploadrr_config_backup() -> Path:
    return uploadrr_config().with_suffix(uploadrr_config().suffix + ".bak")


def ensure_config_backup_exists() -> None:
    path = uploadrr_config()
    backup = uploadrr_config_backup()
    if path.exists() and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        LOGGER.info("Backup limpio creado: %s", backup)


def restore_uploadrr_config_from_backup() -> bool:
    backup = uploadrr_config_backup()
    path = uploadrr_config()
    if not backup.exists():
        LOGGER.warning("No existe backup para restaurar: %s", backup)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, path)
    LOGGER.info("config.yaml restaurado desde %s", backup)
    return True


def inject_app_config_into_uploadrr(cfg: AppConfig) -> None:
    ensure_config_backup_exists()
    if uploadrr_config_backup().exists():
        restore_uploadrr_config_from_backup()
    lines = read_yaml_lines()
    if cfg.tmdb_api_key:
        set_block_key(lines, "DEFAULT", "tmdb", yaml_scalar(cfg.tmdb_api_key))
    set_block_key(lines, "DEFAULT", "default_torrent_client", yaml_scalar(cfg.torrent_client))
    if cfg.emu_api_key:
        set_emu_config(lines, cfg.emu_api_key, cfg.emu_anon)
    set_screen_api_keys(lines, cfg.image_api_keys)
    set_img_hosts(lines, cfg.img_hosts or build_img_hosts_order(cfg.image_default_host, cfg.image_api_keys))
    if cfg.torrent_client == "qbit":
        set_qbit_config(lines, cfg)
    else:
        set_watch_folder(lines, str(cfg.watch_folder or expand_path(DEFAULT_WATCH_FOLDER)))
    write_yaml_lines(lines)
    LOGGER.info("config.yaml inyectado temporalmente")


_CONFIG_RESTORE_REGISTERED = False


def register_config_restore_handlers() -> None:
    global _CONFIG_RESTORE_REGISTERED
    if _CONFIG_RESTORE_REGISTERED:
        return
    _CONFIG_RESTORE_REGISTERED = True
    atexit.register(restore_uploadrr_config_from_backup)

    def _handler(signum, frame):  # type: ignore[no-untyped-def]
        restore_uploadrr_config_from_backup()
        raise KeyboardInterrupt

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except Exception:
                pass


# -------------------------
# HTTP/API validation
# -------------------------

def http_get(url: str, headers: Optional[dict[str, str]] = None, timeout: int = 15) -> tuple[int, Any, str]:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "emuwarez-auto-upload/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
            except Exception:
                data = raw
            return r.status, data, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, None, raw
    except Exception as e:
        return 0, None, str(e)


def validate_tmdb(api_key: str) -> bool:
    url = TMDB_BASE + "/configuration?" + urllib.parse.urlencode({"api_key": api_key})
    status, _, raw = http_get(url)
    if status == 200:
        ok("TMDb API key válida")
        return True
    err(f"TMDb no validó la clave. HTTP={status}")
    print(gray(str(raw)[:500]))
    return False


def validate_emu(api_key: str) -> Optional[bool]:
    """Valida EMU con el mismo patrón que funciona con curl.

    Equivalente:
      curl -i -sS \
        -H "Authorization: Bearer ${EMU_API_KEY}" \
        -H "Accept: application/json" \
        -H "User-Agent: emuwarez-auto-upload-test/1.0" \
        "https://emuwarez.com/api/torrents/filter?perPage=1"

    Devuelve:
      True  -> clave validada.
      False -> clave rechazada de forma fiable.
      None  -> no verificable ahora: 429, 5xx, red, Cloudflare, etc.
    """
    status, _, raw = http_get(
        EMU_TEST_URL,
        {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "emuwarez-auto-upload-test/1.0",
        },
    )
    if 200 <= status < 300:
        ok("EMU API key válida")
        return True

    if status in {401, 403}:
        err(f"eMuwarez rechazó la clave. HTTP={status}")
        print(gray(str(raw)[:700]))
        return False

    if status == 429:
        warn("eMuwarez respondió HTTP 429: límite de peticiones. No se puede confirmar la clave ahora.")
        print(gray(str(raw)[:700]))
        return None

    if status == 0 or 500 <= status <= 599:
        warn(f"eMuwarez respondió HTTP={status}. Parece un problema temporal de red/servidor/Cloudflare.")
        print(gray(str(raw)[:700]))
        return None

    warn(f"Respuesta inesperada de eMuwarez. HTTP={status}. No se puede confirmar la clave.")
    print(gray(str(raw)[:700]))
    return None



def http_post_form(url: str, form: dict[str, str], headers: Optional[dict[str, str]] = None, timeout: int = 20) -> tuple[int, Any, str]:
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "emuwarez-auto-upload/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return r.status, parsed, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        return e.code, parsed, raw
    except Exception as e:
        return 0, None, str(e)


def validate_imgbb(api_key: str) -> Optional[bool]:
    """Valida ImgBB subiendo una imagen PNG 1x1 temporal con expiración de 60 segundos.

    ImgBB no ofrece un endpoint simple de validación de clave; la comprobación
    realista es hacer una subida mínima. La imagen expira automáticamente.
    """
    tiny_png_base64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "xw8AAoMBgB9Wz4kAAAAASUVORK5CYII="
    )
    url = "https://api.imgbb.com/1/upload?" + urllib.parse.urlencode({"key": api_key})
    status, data, raw = http_post_form(
        url,
        {
            "image": tiny_png_base64,
            "expiration": "60",
            "name": "emuwarez-auto-upload-test",
        },
        {"Accept": "application/json"},
    )

    if status == 200 and isinstance(data, dict) and data.get("success") is True:
        ok("ImgBB API key válida")
        return True

    if status in {400, 401, 403}:
        err(f"ImgBB rechazó la clave. HTTP={status}")
        if isinstance(data, dict):
            message = data.get("error", {}).get("message") or data.get("message") or raw
            print(gray(str(message)[:700]))
        else:
            print(gray(str(raw)[:700]))
        return False

    if status == 429:
        warn("ImgBB respondió HTTP 429: límite de peticiones. No se puede confirmar la clave ahora.")
        print(gray(str(raw)[:700]))
        return None

    if status == 0 or 500 <= status <= 599:
        warn(f"ImgBB respondió HTTP={status}. Parece un problema temporal de red/servidor.")
        print(gray(str(raw)[:700]))
        return None

    warn(f"Respuesta inesperada de ImgBB. HTTP={status}. No se puede confirmar la clave.")
    print(gray(str(raw)[:700]))
    return None


def validate_unverified_image_host(host: str, api_key: str) -> Optional[bool]:
    """Marcador de validación para hosts sin endpoint fiable documentado en el script."""
    if not clean_optional_secret(api_key):
        return False
    warn(f"No hay una comprobación fiable integrada para {IMAGE_HOST_LABELS.get(host, host)}. Se puede guardar igualmente.")
    return None


def validate_ptpimg(api_key: str) -> Optional[bool]:
    return validate_unverified_image_host("ptpimg", api_key)


def validate_lensdump(api_key: str) -> Optional[bool]:
    return validate_unverified_image_host("lensdump", api_key)


def validate_ptscreens(api_key: str) -> Optional[bool]:
    return validate_unverified_image_host("ptscreens", api_key)


def validate_onlyimage(api_key: str) -> Optional[bool]:
    return validate_unverified_image_host("onlyimage", api_key)


IMAGE_VALIDATORS = {
    "imgbb": validate_imgbb,
    "ptpimg": validate_ptpimg,
    "lensdump": validate_lensdump,
    "ptscreens": validate_ptscreens,
    "onlyimage": validate_onlyimage,
}


def validate_image_api(host: str, api_key: str) -> Optional[bool]:
    validator = IMAGE_VALIDATORS.get(host, validate_unverified_image_host)
    return validator(api_key)  # type: ignore[misc]


def validate_imgbox() -> Optional[bool]:
    status, _, raw = http_get("https://imgbox.com/", {"User-Agent": "emuwarez-auto-upload-test/1.0"}, timeout=12)
    if 200 <= status < 400:
        ok("Imgbox disponible")
        return True
    warn(f"Imgbox no pudo validarse. HTTP={status}. Se continuará igualmente.")
    if raw:
        print(gray(str(raw)[:500]))
    return None


def tmdb_get(api_key: str, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    query = dict(params or {})
    query["api_key"] = api_key
    url = TMDB_BASE + endpoint + "?" + urllib.parse.urlencode(query)
    status, data, raw = http_get(url)
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"TMDb HTTP {status}: {str(raw)[:250]}")
    return data


# -------------------------
# Bootstrap / venv / imports
# -------------------------

def check_layout(strict: bool = True, show: bool = True) -> bool:
    good = True

    if uploadrr_dir().exists():
        if show:
            kv("Uploadrr", uploadrr_dir())
    else:
        if show:
            kv("Uploadrr", f"NO ENCONTRADO: {uploadrr_dir()}")
        good = False

    if uploadrr_py().exists():
        if show:
            kv("upload.py", uploadrr_py())
    else:
        if show:
            kv("upload.py", f"NO ENCONTRADO: {uploadrr_py()}")
        good = False

    if uploadrr_config().exists():
        if show:
            kv("config.yaml", uploadrr_config())
    else:
        if show:
            kv("config.yaml", f"NO ENCONTRADO: {uploadrr_config()}")

    if show:
        if good:
            ok("Estructura de Uploadrr correcta")
        else:
            err("Estructura de Uploadrr incompleta")

    if strict and not good:
        die("Coloca este script en el mismo directorio donde está la carpeta Uploadrr.")
    return good

def venv_has_pip() -> bool:
    """Comprueba si el Python del venv puede ejecutar pip."""
    if not venv_python().exists():
        return False
    proc = subprocess.run(
        [str(venv_python()), "-m", "pip", "--version"],
        cwd=script_dir(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode == 0


def repair_pip_with_ensurepip() -> bool:
    """Intenta instalar pip dentro del venv usando ensurepip."""
    if not venv_python().exists():
        return False

    action("pip no está disponible en el venv; intentando reparar con ensurepip")
    proc = subprocess.run(
        [str(venv_python()), "-m", "ensurepip", "--upgrade"],
        cwd=script_dir(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode == 0 and venv_has_pip():
        ok("pip reparado correctamente con ensurepip")
        return True

    err("No se pudo reparar pip con ensurepip")
    print(gray(proc.stdout[-4000:]))
    return False


def recreate_venv(preferred_python: Optional[str] = None, force: bool = False) -> None:
    """Recrea el venv usando Python 3.11 si está disponible."""
    if venv_path().exists():
        warn(f"El entorno virtual se va a recrear: {venv_path()}")
        if not force and not yesno("¿Recrear .venv automáticamente?", True):
            die("No se puede continuar sin un entorno virtual funcional.")
        action(f"Eliminando venv anterior: {venv_path()}")
        shutil.rmtree(venv_path())

    py = choose_venv_python(preferred_python, allow_prompt=True)
    action(f"Creando entorno virtual con {py}: {venv_path()}")
    proc = subprocess.run(
        [py, "-m", "venv", str(venv_path())],
        cwd=script_dir(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        version = python_version(py)
        suffix = f"{version[0]}.{version[1]}" if version else "3"
        die(
            "No se pudo crear el entorno virtual. En Debian/Ubuntu suele resolverse con:\n"
            f"  sudo apt install python{suffix}-venv\n"
            "o, si no existe ese paquete exacto:\n"
            "  sudo apt install python3-venv\n\n"
            f"Detalle:\n{proc.stdout[-4000:]}"
        )

    if not venv_has_pip() and not repair_pip_with_ensurepip():
        version = venv_version() or python_version(py)
        suffix = f"{version[0]}.{version[1]}" if version else "3"
        die(
            "El venv se creó, pero no tiene pip. Instala el soporte de venv y vuelve a ejecutar:\n"
            f"  sudo apt install python{suffix}-venv\n"
            "o:\n"
            "  sudo apt install python3-venv"
        )
    status_kv(True, "Venv", venv_path())
    status_kv(True, "pip", "disponible")


def ensure_venv_ready(preferred_python: Optional[str] = None) -> None:
    """Garantiza que ./.venv existe, usa una versión aceptable y tiene pip operativo."""
    if not venv_python().exists():
        status_kv(False, "Venv", f"NO ENCONTRADO: {venv_path()}")
        recreate_venv(preferred_python=preferred_python)
        return

    status_kv(True, "Venv", venv_path())
    status_kv(True, "Python ejecutable", venv_python())
    check_venv_python_version(preferred=preferred_python)

    if venv_has_pip():
        status_kv(True, "pip", "disponible")
        return

    status_kv(False, "pip", "NO DISPONIBLE")
    if repair_pip_with_ensurepip():
        return

    recreate_venv(preferred_python=preferred_python, force=True)

def run_pip(args: list[str], label: str) -> bool:
    # El venv se verifica una vez por fase. No lo reimprimimos antes de cada pip
    # para mantener la salida limpia y sin campos repetidos.
    if not venv_python().exists():
        die("No existe el Python del venv. Ejecuta primero --configure o --bootstrap.")
    action(label)
    cmd = [str(venv_python()), "-m", "pip", *args]
    proc = subprocess.run(
        cmd,
        cwd=script_dir(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode == 0:
        ok(label)
        return True

    err(label)
    print(gray(proc.stdout[-5000:]))
    return False


def create_venv_if_needed(preferred_python: Optional[str] = None) -> None:
    # Nombre conservado por compatibilidad con el resto del script.
    ensure_venv_ready(preferred_python=preferred_python)

def local_modules(root: Path) -> set[str]:
    modules: set[str] = set()
    if not root.exists():
        return modules
    for item in root.iterdir():
        if item.name.startswith("."):
            continue
        if item.is_file() and item.suffix == ".py":
            modules.add(item.stem)
        elif item.is_dir():
            modules.add(item.name)
    return modules


def stdlib_modules() -> set[str]:
    modules = set(getattr(sys, "stdlib_module_names", set()))
    modules.update(
        """
        os sys re json pathlib subprocess argparse typing dataclasses urllib shutil tempfile
        ast venv getpass difflib time textwrap collections itertools functools math logging
        importlib platform hashlib base64 email html csv sqlite3 configparser glob fnmatch
        random string traceback inspect enum copy io contextlib multiprocessing threading
        asyncio socket ssl http xml zipfile tarfile gzip bz2 lzma statistics decimal fractions
        """.split()
    )
    return modules


def scan_python_imports(root: Path) -> dict[str, set[Path]]:
    found: dict[str, set[Path]] = {}
    if not root.exists():
        return found

    for py_file in root.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__"} for part in py_file.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.setdefault(alias.name.split(".")[0], set()).add(py_file)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                found.setdefault(node.module.split(".")[0], set()).add(py_file)
    return found


def external_imports_uploadrr() -> list[str]:
    local = local_modules(uploadrr_dir())
    std = stdlib_modules()
    imports = scan_python_imports(uploadrr_dir())
    return [
        name
        for name in sorted(imports)
        if name not in local
        and name not in std
        and name not in OPTIONAL_UPLOADRR_IMPORTS
        and not name.startswith("_")
    ]


def module_available_in_venv(module: str) -> bool:
    if not venv_python().exists():
        return False
    code = "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
    return (
        subprocess.run(
            [str(venv_python()), "-c", code, module],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def scan_imports_command(standalone: bool = True) -> list[str]:
    if standalone:
        box("EMUWAREZ AUTO UPLOAD", "Imports detectados en Uploadrr")
        check_layout(strict=False, show=True)
    else:
        rule("Imports detectados en Uploadrr")

    imports = external_imports_uploadrr()
    if not imports:
        warn("No se detectaron imports externos en Uploadrr.")
        return []

    print(f"  {gray('Módulo'.ljust(24))} {gray('Estado')}")
    print("  " + gray("─" * 42))
    for name in imports:
        installed = module_available_in_venv(name)
        status = (green("✓") + " instalado") if installed else (yellow("⚠") + " pendiente")
        # Se alinea el texto antes de aplicar color para que los códigos ANSI no rompan columnas.
        print(f"  {cyan(name.ljust(24))} {status}")
    return imports

def requirement_name(line: str) -> str:
    clean = line.split("#", 1)[0].strip()
    clean = re.sub(r"^\s*-e\s+", "", clean)
    clean = re.split(r"\s*(?:==|>=|<=|~=|!=|>|<|\[|;)\s*", clean, 1)[0]
    return clean.strip().lower().replace("_", "-")


def requirement_lines(req: Path) -> list[str]:
    lines: list[str] = []
    for raw in req.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement", "-c ", "--constraint")):
            warn(f"Entrada de requirements no expandida automáticamente: {line}")
            continue
        lines.append(line)
    return lines


def install_requirement_line(line: str) -> bool:
    name = requirement_name(line)

    if name in PROBLEMATIC_REQUIREMENT_NAMES:
        warn(f"{line} es problemático en algunos entornos; se intentará sin build isolation.")
        run_pip(["install", "setuptools<81", "wheel"], "Preparando setuptools para paquete problemático")
        if run_pip(["install", "--no-build-isolation", line], f"Instalando {line}"):
            return True
        warn(f"No se pudo instalar {line}. No se bloquea aquí: se verificará si Uploadrr realmente lo necesita.")
        return False

    return run_pip(["install", line], f"Instalando dependencia: {line}")


def install_uploadrr_declared_deps() -> None:
    req = uploadrr_dir() / "requirements.txt"
    pyproject = uploadrr_dir() / "pyproject.toml"
    setup_py = uploadrr_dir() / "setup.py"

    if req.exists():
        # Evita el fallo visto con pyrobase:
        # ModuleNotFoundError: No module named 'pkg_resources'
        # Primero preparamos setuptools y probamos una instalación global sin build isolation.
        run_pip(["install", "setuptools<81", "wheel"], "Preparando entorno de build para requirements")
        if run_pip(["install", "--no-build-isolation", "-r", str(req)], f"Instalando dependencias desde {req}"):
            return

        warn("La instalación completa de requirements.txt falló. Se instalarán las dependencias una a una para aislar fallos.")
        failures: list[str] = []
        for line in requirement_lines(req):
            if not install_requirement_line(line):
                failures.append(line)

        if failures:
            warn("No se pudieron instalar algunas dependencias declaradas:")
            for item in failures:
                print("  " + yellow("- " + item))
            warn("No se detiene aquí. La verificación real será arrancar Uploadrr; si arranca, esas dependencias no son críticas para este flujo.")
        return

    if pyproject.exists() or setup_py.exists():
        if not run_pip(["install", "--no-build-isolation", str(uploadrr_dir())], "Instalando Uploadrr como paquete local"):
            warn("No se pudo instalar Uploadrr como paquete local. Se continuará con la verificación real de arranque.")
    else:
        warn("Uploadrr no tiene requirements.txt, pyproject.toml ni setup.py.")


def pip_package_for_import(module: str) -> str:
    return IMPORT_TO_PIP.get(module, module)


def missing_module_from_output(output: str) -> Optional[str]:
    patterns = [
        r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]",
        r"ImportError:\s+No module named ['\"]([^'\"]+)['\"]",
        r"No module named ['\"]([^'\"]+)['\"]",
    ]
    for pattern in patterns:
        m = re.search(pattern, output)
        if m:
            return m.group(1).split(".")[0]
    return None


def install_missing_uploadrr_imports() -> None:
    """Instala imports externos principales que falten.

    Los imports marcados en OPTIONAL_UPLOADRR_IMPORTS se ocultan totalmente del
    diagnóstico y no se instalan. El resto sí se intenta instalar porque Uploadrr
    puede fallar en su comprobación de arranque, como ocurre con tmdbsimple.
    """
    missing = [m for m in external_imports_uploadrr() if not module_available_in_venv(m)]
    if not missing:
        ok("Imports externos principales de Uploadrr disponibles en el venv")
        return

    kv("Imports pendientes", ", ".join(missing))
    failures: list[str] = []
    for module in missing:
        if not install_one_missing_module(module):
            failures.append(module)

    if failures:
        warn("No se pudieron instalar estos imports principales: " + ", ".join(failures))
        warn("Se continuará con la verificación real de Uploadrr para decidir si son críticos.")

def uploadrr_help_probe() -> tuple[bool, str, list[str]]:
    """Prueba Uploadrr de forma compatible con su parser.

    En algunas versiones, `upload.py --help` intenta procesar rutas y termina con
    'No valid paths to process'. En cambio, `upload.py HELP --help` muestra el
    help real. Por eso usamos el dummy `HELP`.
    """
    cmd = [str(venv_python()), str(uploadrr_py()), "HELP", "--help"]
    proc = subprocess.run(
        cmd,
        cwd=script_dir(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = proc.stdout or ""
    missing = missing_module_from_output(output)
    help_seen = "usage: upload.py" in output and ("Uploadrr" in output or "options:" in output)
    traceback = "Traceback (most recent call last)" in output
    success = help_seen and not missing and not traceback
    return success, output, cmd


def install_one_missing_module(module: str) -> bool:
    if module in OPTIONAL_UPLOADRR_IMPORTS:
        warn(f"{module} está marcado como opcional; no se fuerza su instalación.")
        return False
    package = pip_package_for_import(module)
    if requirement_name(package) in PROBLEMATIC_REQUIREMENT_NAMES:
        return install_requirement_line(package)
    return run_pip(["install", package], f"Instalando módulo requerido por Uploadrr: {module} ({package})")


def ensure_uploadrr_startup_ready(max_attempts: int = 5) -> bool:
    for _ in range(max_attempts):
        ok_probe, output, cmd = uploadrr_help_probe()
        if ok_probe:
            ok("Uploadrr arranca correctamente con el venv")
            return True

        missing = missing_module_from_output(output)
        if missing:
            if missing in OPTIONAL_UPLOADRR_IMPORTS:
                warn(f"Uploadrr menciona un módulo opcional no disponible: {missing}")
                warn("No se detiene aquí porque está marcado como opcional para este flujo.")
                return True
            if install_one_missing_module(missing):
                continue

        err("Uploadrr no pudo verificarse con el venv")
        kv("Comando", shell_join(cmd))
        print(gray(output[-5000:]))
        return False

    err("Uploadrr sigue sin arrancar tras varios intentos de reparación")
    return False


def check_uploadrr() -> bool:
    box("EMUWAREZ AUTO UPLOAD", "Verificando Uploadrr")
    check_layout(strict=True, show=False)

    if not venv_python().exists():
        kv("Venv", "NO ENCONTRADO")
        return False

    ok_probe, output, cmd = uploadrr_help_probe()
    kv("Comando", shell_join(cmd))
    if ok_probe:
        ok("Uploadrr arranca correctamente con el venv")
        return True

    missing = missing_module_from_output(output)
    if missing and missing in OPTIONAL_UPLOADRR_IMPORTS:
        warn(f"Uploadrr informa de un módulo opcional no disponible: {missing}")
        warn("No se considera error crítico para este flujo.")
        return True

    err("Uploadrr no pudo arrancar con el venv")
    print(gray(output[-5000:]))
    return False

def bootstrap(preferred_python: Optional[str] = None, ask_system_update: bool = False, allow_system_install: bool = True) -> None:
    box("EMUWAREZ AUTO UPLOAD", "Bootstrap del entorno")

    rule("Estructura")
    layout_ok = check_layout(strict=True, show=False)
    status_kv(layout_ok, "Directorio base", script_dir())
    status_kv(uploadrr_dir().exists(), "Uploadrr", uploadrr_dir())
    status_kv(uploadrr_py().exists(), "upload.py", uploadrr_py())
    status_kv(uploadrr_config().exists(), "Config YAML", uploadrr_config())
    status_kv(True, "Venv", venv_path())

    if ask_system_update:
        maybe_offer_system_update()
    
    rule("Herramientas del sistema")
    ensure_system_tools(REQUIRED_SYSTEM_TOOLS, allow_install=allow_system_install)


    rule("Entorno virtual")
    maybe_offer_python311_install()
    create_venv_if_needed(preferred_python=preferred_python)

    rule("Dependencias Python")
    print("  Se instalarán y verificarán las dependencias necesarias para el")
    print("  funcionamiento estándar de Uploadrr dentro del entorno virtual.")
    print()

    if not run_pip(["install", "--upgrade", "pip", "setuptools<81", "wheel"], "Actualizando entorno base de Python"):
        die("No se pudo actualizar pip/setuptools/wheel dentro del venv.")

    if SCRIPT_PIP_DEPS:
        if not run_pip(["install", *SCRIPT_PIP_DEPS], "Instalando dependencias del script"):
            die("No se pudieron instalar las dependencias propias del script.")

    install_uploadrr_declared_deps()
    install_missing_uploadrr_imports()
    scan_imports_command(standalone=False)

    rule("Verificación Uploadrr")
    if not ensure_uploadrr_startup_ready():
        die("Bootstrap incompleto. Uploadrr no arranca todavía.")

def check_runtime_apis(cfg: AppConfig) -> None:
    LOGGER.info("Validando APIs en comprobación rápida")
    if not cfg.tmdb_api_key:
        die("TMDb API key no configurada. Ejecuta --configure.")
    if not validate_tmdb(cfg.tmdb_api_key):
        die("TMDb API key no validada.")

    if not cfg.emu_api_key:
        die("EMU API key no configurada. Ejecuta --configure.")
    emu_result = validate_emu(cfg.emu_api_key)
    if emu_result is not True:
        die("EMU API key no validada.")

    # Imgbox es el host por defecto y su validación no debe bloquear el flujo.
    imgbox_result = validate_imgbox()
    if imgbox_result is not True:
        warn("Imgbox no pudo validarse; se continuará igualmente.")


def ensure_runtime_ready(cfg: AppConfig, preferred_python: Optional[str] = None, allow_system_install: bool = True) -> None:
    """
    Comprobación rápida visible antes del flujo normal.

    Mantiene oculto el ruido técnico de las comprobaciones, pero muestra una barra
    para que el usuario vea que el script sigue trabajando.
    """
    box("EMUWAREZ AUTO UPLOAD", "Comprobación rápida")
    rule("Entorno")
    progress_bar(0, "Iniciando comprobación")

    def check_step(percent: int, label: str, fn) -> None:  # type: ignore[no-untyped-def]
        progress_bar(percent, label, "comprobando", None)
        previous_silent = UI.silent
        UI.silent = True
        try:
            fn()
        except Exception:
            UI.silent = previous_silent
            progress_bar(percent, label, "falló", False, final=True)
            LOGGER.exception("Falló comprobación rápida: %s", label)
            raise
        finally:
            UI.silent = previous_silent
        progress_bar(percent, label, "", True)

    check_step(15, "Estructura Uploadrr", lambda: check_layout(strict=True, show=False))
    check_step(30, "Herramientas del sistema", lambda: ensure_system_tools(REQUIRED_SYSTEM_TOOLS, allow_install=allow_system_install))
    check_step(45, "Entorno virtual", lambda: ensure_venv_ready(preferred_python=preferred_python))

    if SCRIPT_PIP_DEPS:
        def verify_script_deps() -> None:
            if not run_pip(["install", *SCRIPT_PIP_DEPS], "Verificando dependencias del script"):
                die("No se pudieron verificar/instalar las dependencias propias del script.")
        check_step(58, "Dependencias script", verify_script_deps)

    check_step(65, "Dependencias Uploadrr", install_missing_uploadrr_imports)

    def verify_uploadrr() -> None:
        if not ensure_uploadrr_startup_ready():
            warn("Uploadrr no arranca todavía; intentando reparar dependencias")
            install_uploadrr_declared_deps()
            install_missing_uploadrr_imports()
            if not ensure_uploadrr_startup_ready():
                die("Uploadrr no está listo. Revisa el error anterior.")

    check_step(82, "Arranque Uploadrr", verify_uploadrr)
    check_step(95, "APIs", lambda: check_runtime_apis(cfg))
    progress_bar(100, "Entorno listo", "", True, final=True)


# -------------------------
# Configuration wizard
# -------------------------

def writable_dir(path_value: str | Path, label: str) -> Path:
    path = expand_path(path_value)
    if not path.exists():
        warn(f"{label} no existe: {path}")
        if yesno("¿Crear carpeta?", True):
            path.mkdir(parents=True, exist_ok=True)
        else:
            die(f"No se puede continuar sin {label}")

    if not path.is_dir():
        die(f"{label} no es una carpeta: {path}")

    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".emu_test_", delete=True) as tmp:
            tmp.write(b"ok")
        ok(f"{label} existe y es escribible: {path}")
    except Exception as exc:
        die(f"{label} no es escribible: {exc}")

    return path


def intro_config() -> None:
    box("EMUWAREZ AUTO UPLOAD", "Configuración inicial")
    print(
        textwrap.dedent(
            f"""
            Este asistente dejará preparado el flujo de subida a eMuwarez con Uploadrr.

            Pasos de la configuración:
              {green('1.')} Entorno: verifica Uploadrr, herramientas externas y ./.venv.
              {green('2.')} APIs: configura TMDb para metadatos y EMU para publicar subidas.
              {green('3.')} Capturas: elige host de imágenes y, si quieres, añade APIs de hosts compatibles.
              {green('4.')} Torrent: configura watch folder o qBittorrent como destino.
              {green('5.')} Subida: define anonimato en EMU, carpeta de hardlinks y tag/grupo.
              {green('6.')} Seguridad: guarda la configuración en claro o cifrada.

            Después de la configuración:
            {green('1.')} Recibe una película, episodio o carpeta.
            {green('2.')} Carga la configuración (si está encriptada pedirá la contraseña).
            {green('3.')} Comprueba entorno, dependencias, APIs y Uploadrr.
            {green('4.')} Busca y confirma el ID correcto en TMDb y analiza el contenido con MediaInfo.
            {green('6.')} Propone nombres segun el formato de eMuwarez para torrent y archivo.
            {green('7.')} Sube el torrent a eMuwarez con Uploadrr y muestra resultado, trailer detectado y log.
            """
        ).strip()
    )
    print()


def validate_optional_image_key(host: str, current: Optional[str]) -> Optional[str]:
    label = IMAGE_HOST_LABELS.get(host, host)
    while True:
        api_key = prompt_api_key_with_help(
            f"API key de {label} (opcional; Enter para omitir)",
            f"Clave usada por Uploadrr para subir capturas a {label}. Si no la configuras, se guardará como null.",
            current,
        )
        if not api_key:
            warn(f"{label} no configurado. Se guardará como null.")
            return None
        action(f"Probando {label}")
        result = validate_image_api(host, api_key)
        if result is True:
            return api_key
        if yesno(f"No se pudo validar {label}. ¿Guardar la API key igualmente?", False):
            return api_key
        if not yesno(f"¿Reintentar {label}?", True):
            return None


def select_image_default_host(current: str) -> str:
    hosts = list(dict.fromkeys([current or DEFAULT_IMAGE_HOST, *DEFAULT_IMG_HOSTS]))
    print()
    print(cyan("Host de imágenes por defecto"))
    explain("Host principal que Uploadrr intentará usar para alojar capturas. Imgbox es el valor recomendado por defecto y no requiere API key.")
    for i, host in enumerate(hosts, 1):
        print(f"  {cyan(str(i).rjust(2))}. {host}")
    while True:
        choice = input(f"\n{cyan('Selecciona host')} {gray('[' + (current or DEFAULT_IMAGE_HOST) + ']')}: ").strip().lower()
        if not choice:
            return current or DEFAULT_IMAGE_HOST
        if choice.isdigit() and 1 <= int(choice) <= len(hosts):
            return hosts[int(choice) - 1]
        if choice in hosts:
            return choice
        warn("Host inválido.")


def qbit_base_url(qbit_url: str, qbit_port: str) -> str:
    url = (qbit_url or "http://127.0.0.1").strip().rstrip("/")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "http://" + url
    parsed = urllib.parse.urlparse(url)
    if qbit_port and parsed.hostname and parsed.port is None:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host + f":{qbit_port}"
        if parsed.username or parsed.password:
            userinfo = parsed.username or ""
            if parsed.password:
                userinfo += ":" + parsed.password
            netloc = userinfo + "@" + netloc
        url = urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "", ""))
    return url.rstrip("/")


def qbit_urlopen(req: urllib.request.Request, *, timeout: int, verify_certificate: bool):
    context = None
    if req.full_url.lower().startswith("https://") and not verify_certificate:
        context = ssl._create_unverified_context()
    return urllib.request.urlopen(req, timeout=timeout, context=context)


def validate_qbittorrent_connection(
    qbit_url: str,
    qbit_port: str,
    qbit_user: str,
    qbit_pass: str,
    verify_certificate: bool,
) -> Optional[bool]:
    base = qbit_base_url(qbit_url, qbit_port)
    action(f"Probando qBittorrent: {base}")
    login_url = base + "/api/v2/auth/login"
    form = urllib.parse.urlencode({"username": qbit_user, "password": qbit_pass}).encode("utf-8")
    headers = {
        "User-Agent": "emuwarez-auto-upload-test/1.0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": base,
        "Origin": base,
    }
    try:
        req = urllib.request.Request(login_url, data=form, headers=headers, method="POST")
        with qbit_urlopen(req, timeout=15, verify_certificate=verify_certificate) as response:
            raw = response.read().decode("utf-8", "replace").strip()
            cookie = response.headers.get("Set-Cookie", "")
            if response.status != 200 or "SID=" not in cookie:
                warn(f"qBittorrent no confirmó el login. HTTP={response.status} Respuesta={raw[:120] or 'vacía'}")
                return False
            sid = cookie.split(";", 1)[0]

        version_req = urllib.request.Request(
            base + "/api/v2/app/version",
            headers={
                "User-Agent": "emuwarez-auto-upload-test/1.0",
                "Referer": base,
                "Origin": base,
                "Cookie": sid,
            },
        )
        with qbit_urlopen(version_req, timeout=15, verify_certificate=verify_certificate) as version_response:
            version = version_response.read().decode("utf-8", "replace").strip()
            if version_response.status == 200 and version:
                ok("Conexión qBittorrent correcta")
                kv("Versión qBittorrent", version)
                return True
            warn(f"qBittorrent login correcto, pero no se pudo leer la versión. HTTP={version_response.status}")
            return None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        warn(f"No se pudo conectar con qBittorrent. HTTP={exc.code}")
        if raw:
            print(gray(raw[:500]))
        return False
    except Exception as exc:
        warn(f"No se pudo conectar con qBittorrent: {exc}")
        return None


def configure_torrent_client(current_client: str, current_watch: str, old_cfg: Optional[AppConfig]) -> dict[str, Any]:
    print()
    print(cyan("Cliente torrent"))
    explain("Define dónde dejará Uploadrr el torrent generado. Watch folder es más simple; qBittorrent permite enviarlo al Web UI.")
    print("  " + cyan("1.") + " watch folder")
    print("  " + cyan("2.") + " qBittorrent")
    default_choice = "2" if current_client == "qbit" else "1"
    choice = input(f"\n{cyan('Selecciona cliente')} {gray('[' + default_choice + ']')}: ").strip() or default_choice

    if choice in {"2", "qbit", "qbittorrent"}:
        while True:
            qbit_url = prompt_with_help("URL qBittorrent", "URL base del Web UI de qBittorrent.", old_cfg.qbit_url if old_cfg else "http://127.0.0.1")
            qbit_port = prompt_with_help("Puerto qBittorrent", "Puerto del Web UI de qBittorrent.", old_cfg.qbit_port if old_cfg else "8080")
            qbit_user = prompt_with_help("Usuario qBittorrent", "Usuario del Web UI de qBittorrent.", old_cfg.qbit_user if old_cfg else "")
            qbit_pass = prompt_secret_with_help("Contraseña qBittorrent", "Contraseña del Web UI de qBittorrent. Se guardará en la configuración propia.", old_cfg.qbit_pass if old_cfg else "")
            qbit_storage = prompt_with_help("torrent_storage_dir", "Ruta del directorio BT_backup/torrent_storage_dir que usa Uploadrr para búsquedas/verificación.", old_cfg.qbit_torrent_storage_dir if old_cfg else "")
            qbit_layout = prompt_with_help("content_layout", "Layout de contenido que se pasará a qBittorrent. Uploadrr suele usar Original.", old_cfg.qbit_content_layout if old_cfg else "Original")
            qbit_verify = yesno_with_help("¿Verificar certificado HTTPS del Web UI?", "Déjalo en No si usas qBittorrent local o certificado autofirmado.", old_cfg.qbit_verify_certificate if old_cfg else False)

            qbit_ok = validate_qbittorrent_connection(qbit_url, qbit_port, qbit_user, qbit_pass, qbit_verify)
            if qbit_ok is True:
                break
            if yesno("¿Reintentar configuración de qBittorrent?", True):
                continue
            if yesno("¿Guardar esta configuración de qBittorrent igualmente?", False):
                break

        return {
            "torrent_client": "qbit",
            "watch_folder": expand_path(current_watch),
            "qbit_url": qbit_url,
            "qbit_port": qbit_port,
            "qbit_user": qbit_user,
            "qbit_pass": qbit_pass,
            "qbit_torrent_storage_dir": qbit_storage,
            "qbit_content_layout": qbit_layout,
            "qbit_verify_certificate": qbit_verify,
        }

    watch_folder = writable_dir(
        prompt_with_help(
            "Carpeta watch del cliente torrent",
            "Carpeta vigilada por tu cliente torrent. Uploadrr dejará ahí el .torrent generado.",
            current_watch,
        ),
        "Carpeta watch",
    )
    return {
        "torrent_client": "watch",
        "watch_folder": watch_folder,
        "qbit_url": old_cfg.qbit_url if old_cfg else "http://127.0.0.1",
        "qbit_port": old_cfg.qbit_port if old_cfg else "8080",
        "qbit_user": old_cfg.qbit_user if old_cfg else "",
        "qbit_pass": old_cfg.qbit_pass if old_cfg else "",
        "qbit_torrent_storage_dir": old_cfg.qbit_torrent_storage_dir if old_cfg else "",
        "qbit_content_layout": old_cfg.qbit_content_layout if old_cfg else "Original",
        "qbit_verify_certificate": old_cfg.qbit_verify_certificate if old_cfg else False,
    }


def configure(
    skip_bootstrap: bool = False,
    preferred_python: Optional[str] = None,
    ask_system_update: bool = True,
    allow_system_install: bool = True,
    config_passphrase: Optional[str] = None,
) -> AppConfig:
    intro_config()
    ensure_config_backup_exists()

    if not skip_bootstrap:
        bootstrap(preferred_python=preferred_python, ask_system_update=ask_system_update, allow_system_install=allow_system_install)
    else:
        check_layout(strict=True)

    if not uploadrr_config().exists():
        warn(f"No existe {uploadrr_config()}")
        if yesno("¿Crear config.yaml mínimo?", False):
            write_yaml_lines(
                [
                    "DEFAULT:",
                    "  tmdb: null",
                    "  add_trailer: false",
                    "  add_logo: true",
                    "  default_torrent_client: 'watch'",
                    "  sfx_on_prompt: true",
                    "  always_anon: false",
                    "",
                    "TRACKERS:",
                    "  EMU:",
                    "    api_key: null",
                    "    anon: false",
                    "",
                    "SCREENS:",
                    "  API_KEYS:",
                    "    imgbb: null",
                    "    ptpimg: null",
                    "    lensdump: null",
                    "    ptscreens: null",
                    "    onlyimage: null",
                    "  img_hosts: [imgbox, ptpimg, ptscreens, onlyimage, hamster, imghost, imgbb, lensdump, pixhost, fastpic] # order dictates prefrence!",
                    "  screen_count: 6",
                    "",
                    "TORRENT_CLIENTS:",
                    f"  watch: {{'torrent_client': 'watch', 'watch_folder': {python_literal_scalar(str(expand_path(DEFAULT_WATCH_FOLDER)))}}}",
                ]
            )
            ensure_config_backup_exists()
        else:
            die("No puedo configurar Uploadrr sin config.yaml.")

    lines = read_yaml_lines()
    current_tmdb = get_block_key(lines, "DEFAULT", "tmdb")
    current_emu = get_emu_api_key(lines)
    current_emu_anon = get_emu_anon(lines) or False
    current_image_keys = get_screen_api_keys(lines)
    current_hosts = get_img_hosts(lines) or list(DEFAULT_IMG_HOSTS)
    current_watch = get_watch_folder(lines) or DEFAULT_WATCH_FOLDER
    current_client = get_torrent_client(lines)
    old_cfg = load_app_config(passphrase=config_passphrase)
    if old_cfg:
        current_tmdb = old_cfg.tmdb_api_key or current_tmdb
        current_emu = old_cfg.emu_api_key or current_emu
        current_emu_anon = old_cfg.emu_anon
        current_image_keys.update({k: v for k, v in old_cfg.image_api_keys.items() if v})
        current_hosts = old_cfg.img_hosts or current_hosts
        current_watch = str(old_cfg.watch_folder or current_watch)
        current_client = old_cfg.torrent_client or current_client

    box("EMUWAREZ AUTO UPLOAD", "Configuración de APIs")

    while True:
        tmdb = prompt_api_key_with_help(
            "API key de TMDb",
            "API key necesaria para buscar metadatos, títulos, año y coincidencias de películas/series en TMDb.",
            clean_optional_secret(current_tmdb),
        )
        action("Probando TMDb")
        if validate_tmdb(tmdb):
            break
        if not yesno("¿Reintentar TMDb?", True):
            die("TMDb no validado.")

    while True:
        emu = prompt_api_key_with_help(
            "API key de EMU / eMuwarez",
            "API key necesaria para autenticar y publicar subidas en eMuwarez.",
            current_emu,
        )
        action("Probando eMuwarez")
        emu_result = validate_emu(emu)
        if emu_result is True:
            break
        if emu_result is None:
            if yesno("No se pudo verificar EMU ahora. ¿Guardar la API key igualmente y continuar?", False):
                break
            continue
        if not yesno("¿Reintentar EMU?", True):
            die("EMU no validado.")

    emu_anon = yesno_with_help(
        "¿Subir a EMU como anónimo?",
        "Controla TRACKERS.EMU.anon. Por defecto es No para que las subidas no sean anónimas.",
        current_emu_anon,
    )

    box("EMUWAREZ AUTO UPLOAD", "Capturas / imágenes")
    image_default_host = select_image_default_host(current_hosts[0] if current_hosts else DEFAULT_IMAGE_HOST)
    image_api_keys = {host: current_image_keys.get(host) for host in IMAGE_API_HOSTS}
    if yesno_with_help(
        "¿Quieres configurar APIs de otros hosts de imágenes?",
        "Opcional. Si configuras alguna API, ese host se pondrá al principio de SCREENS.img_hosts.",
        False,
    ):
        for host in IMAGE_API_HOSTS:
            image_api_keys[host] = validate_optional_image_key(host, image_api_keys.get(host))
    img_hosts = build_img_hosts_order(image_default_host, image_api_keys)

    box("EMUWAREZ AUTO UPLOAD", "Rutas y cliente torrent")
    default_hardlink = str(old_cfg.hardlink_base) if old_cfg else DEFAULT_HARDLINK_BASE
    hardlink_base = writable_dir(
        prompt_with_help(
            "Carpeta base de hardlinks",
            "Carpeta limpia donde se crearán hardlinks para preparar la subida sin duplicar espacio si está en el mismo filesystem.",
            default_hardlink,
        ),
        "Carpeta hardlinks",
    )
    torrent_data = configure_torrent_client(current_client, current_watch, old_cfg)

    box("EMUWAREZ AUTO UPLOAD", "Subida")
    tag = (
        prompt_with_help(
            "Tag / grupo de subida",
            "Grupo que aparecerá al final del nombre. Ejemplo: Pelicula 2024 1080p BluRay x264 ESP DD 5.1 SUBS-EMUWAREZ",
            old_cfg.tag if old_cfg else DEFAULT_TAG,
        )
        or DEFAULT_TAG
    ).upper()

    cfg = AppConfig(
        hardlink_base=hardlink_base,
        tag=tag,
        tmdb_api_key=tmdb,
        emu_api_key=emu,
        emu_anon=emu_anon,
        image_default_host=image_default_host,
        image_api_keys=image_api_keys,
        img_hosts=img_hosts,
        torrent_client=torrent_data["torrent_client"],
        watch_folder=torrent_data["watch_folder"],
        qbit_url=torrent_data["qbit_url"],
        qbit_port=torrent_data["qbit_port"],
        qbit_user=torrent_data["qbit_user"],
        qbit_pass=torrent_data["qbit_pass"],
        qbit_torrent_storage_dir=torrent_data["qbit_torrent_storage_dir"],
        qbit_content_layout=torrent_data["qbit_content_layout"],
        qbit_verify_certificate=torrent_data["qbit_verify_certificate"],
    )

    box("EMUWAREZ AUTO UPLOAD", "Seguridad")
    encrypt_config = yesno_with_help(
        "¿Cifrar la configuración propia?",
        "Si eliges Sí, las claves se guardarán en config.enc.json. En ejecución podrás desbloquearlas con --config-pass-file o --config-pass-env.",
        old_cfg.config_encrypted if old_cfg else False,
    )
    passphrase = prompt_new_passphrase() if encrypt_config else None
    save_app_config(cfg, encrypt=encrypt_config, passphrase=passphrase)

    restore_uploadrr_config_from_backup()

    box("CONFIGURACIÓN GUARDADA")
    status_kv(True, "Config propia", CONFIG_ENC_FILE if encrypt_config else CONFIG_FILE)
    status_kv(True, "TMDb API", mask_secret(cfg.tmdb_api_key))
    status_kv(True, "EMU API", mask_secret(cfg.emu_api_key))
    status_kv(True, "EMU anónimo", cfg.emu_anon)
    status_kv(True, "Img hosts", ", ".join(cfg.img_hosts))
    status_kv(True, "Hardlinks", hardlink_base)
    status_kv(True, "Torrent client", cfg.torrent_client)
    status_kv(True, "Tag", tag)
    status_kv(True, "Log", current_log_file())
    return cfg


# -------------------------
# TMDb / names
# -------------------------

def strip_release_junk(value: str) -> str:
    """Limpia basura típica de releases para búsquedas TMDb.

    Ejemplos que corrige:
      Las crónicas de Narnia 1 [www.newpct.com][toni32]
      Pelicula.2005.1080p.BluRay.x264-GRUPO
    """
    text = str(value or "")
    text = Path(text).stem
    text = text.replace(".", " ").replace("_", " ")

    # Elimina bloques cerrados y, si queda un corchete abierto de web/uploader,
    # corta desde ahí. Para búsqueda TMDb es preferible perder edición/grupo a
    # enviar basura tipo [www newpct com].
    text = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", text)
    text = re.sub(r"[\[\(\{].*$", " ", text)

    # Elimina dominios y todo lo que venga después de sitios de descarga.
    text = re.sub(r"(?i)\b(?:www\.)?[a-z0-9-]+\.(?:com|net|org|tv|es|me|io)\b.*$", " ", text)
    text = re.sub(r"(?i)\b(?:www|newpct|pctmix|divxtotal|elitefreak|dontorrent|torrent|torrents|descargas)\b.*$", " ", text)

    # Quita patrones de episodio, años y ruido técnico.
    text = re.sub(r"(?i)\bS\d{1,2}E\d{1,3}\b|\b\d{1,2}x\d{1,3}\b", " ", text)
    text = re.sub(r"\b(19\d{2}|20\d{2})\b.*$", " ", text)
    noise = [
        "4320p", "2160p", "1080p", "720p", "576p", "480p",
        "bluray", "blu ray", "web-dl", "webdl", "webrip", "hdtv",
        "x264", "x265", "h264", "h265", "hevc", "avc", "dts",
        "aac", "ddp", "dd", "subs", "multi", "dual", "bdremux",
        "hdr", "dv", "remux", "rip", "proper", "repack",
    ]
    for token in noise:
        text = re.sub(rf"(?i)\b{re.escape(token)}\b", " ", text)

    # Quita restos de grupo al final.
    text = re.sub(r"\s+-\s+[A-Za-z0-9]{2,}$", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -_:")


def clean_title(value: str) -> str:
    return strip_release_junk(value)


def tmdb_query_variants(primary: str, score_inputs: list[str]) -> list[str]:
    """Genera búsquedas alternativas para TMDb de más específica a más limpia."""
    variants: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value or "").strip(" -_:")
        if value and value.lower() not in {v.lower() for v in variants}:
            variants.append(value)

    for raw in [primary, *score_inputs]:
        cleaned = strip_release_junk(raw)
        add(cleaned)

        # Quita número de saga suelto al final: "Narnia 1" -> "Narnia".
        no_part_number = re.sub(r"\s+\d{1,2}$", "", cleaned).strip()
        add(no_part_number)

        # Quita sufijos tipo "parte 1", "part 1".
        no_part_word = re.sub(r"(?i)\s+(?:parte|part|capitulo|capítulo)\s+\d{1,2}$", "", cleaned).strip()
        add(no_part_word)

    # Variante específica frecuente para esta saga y otros nombres bilingües.
    for value in list(variants):
        if re.search(r"(?i)\bnarnia\b", value):
            add("Las crónicas de Narnia")
            add("The Chronicles of Narnia")

    return variants[:8]

def similarity(a: str, b: str) -> float:
    a = clean_title(a).lower()
    b = clean_title(b).lower()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def parse_tmdb_arg(value: str, default_kind: str) -> tuple[str, int]:
    value = value.strip().lower().replace("tmdb:", "")
    m = re.match(r"^(movie|tv)/(\d+)$", value)
    if m:
        return m.group(1), int(m.group(2))
    if value.isdigit():
        return default_kind, int(value)
    raise ValueError("TMDb debe ser movie/123, tv/123 o un número.")


def result_title_year(result: dict[str, Any], kind: str) -> tuple[str, str]:
    title = result.get("title") or result.get("name") or result.get("original_title") or result.get("original_name") or "Sin título"
    date = result.get("release_date") if kind == "movie" else result.get("first_air_date")
    return title, (date or "0000")[:4]


def choose_tmdb(
    api_key: str,
    query: str,
    kind: str,
    score_inputs: list[str],
    manual: Optional[str] = None,
) -> tuple[str, int, dict[str, Any]]:
    if manual:
        tmdb_kind, tmdb_id = parse_tmdb_arg(manual, kind)
        details = tmdb_get(api_key, f"/{tmdb_kind}/{tmdb_id}", {"language": "es-ES"})
        return tmdb_kind, tmdb_id, details

    attempted: list[str] = []
    results: list[dict[str, Any]] = []

    for search_query in tmdb_query_variants(query, score_inputs):
        attempted.append(search_query)
        data = tmdb_get(
            api_key,
            f"/search/{kind}",
            {"query": search_query, "language": "es-ES", "include_adult": "false"},
        )
        results = data.get("results") or []
        if results:
            if search_query != query:
                kv("Búsqueda usada", search_query)
            break

    if not results:
        warn("TMDb no encontró resultados con las búsquedas limpias generadas.")
        if attempted:
            print(gray("Búsquedas probadas:"))
            for item in attempted:
                print("  " + gray("- " + item))
        manual_id = prompt("Introduce ID manual, ej. movie/411 o tv/37854")
        tmdb_kind, tmdb_id = parse_tmdb_arg(manual_id, kind)
        details = tmdb_get(api_key, f"/{tmdb_kind}/{tmdb_id}", {"language": "es-ES"})
        return tmdb_kind, tmdb_id, details

    for item in results:
        title, _ = result_title_year(item, kind)
        targets = [title, item.get("original_title") or "", item.get("original_name") or ""]
        item["_emu_score"] = max(
            [similarity(src, target) for src in score_inputs for target in targets if src and target] or [0.0]
        )

    results.sort(key=lambda x: (x.get("_emu_score", 0.0), x.get("popularity", 0.0)), reverse=True)
    candidates = results[:10]

    print()
    print(bold("Coincidencias TMDb ordenadas por similitud:"))
    print()
    for i, item in enumerate(candidates, 1):
        title, year = result_title_year(item, kind)
        score = round(float(item.get("_emu_score", 0.0)) * 100)
        print(f"  {cyan(str(i).rjust(2))}. {green((str(score)+'%').rjust(4))}  {bold(title)} {gray('(' + year + ')')} {gray(kind + '/' + str(item.get('id')))}")
        overview = (item.get("overview") or "").replace("\n", " ")[:100]
        if overview:
            print("      " + gray(overview + ("…" if len(overview) >= 100 else "")))

    while True:
        choice = prompt("Selecciona coincidencia, 'm' manual o Enter para 1", "1").lower()
        if choice in {"m", "manual"}:
            manual_id = prompt("TMDb ID")
            tmdb_kind, tmdb_id = parse_tmdb_arg(manual_id, kind)
            details = tmdb_get(api_key, f"/{tmdb_kind}/{tmdb_id}", {"language": "es-ES"})
            return tmdb_kind, tmdb_id, details
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            item = candidates[int(choice) - 1]
            tmdb_id = int(item["id"])
            details = tmdb_get(api_key, f"/{kind}/{tmdb_id}", {"language": "es-ES"})
            return kind, tmdb_id, details
        warn("Selección inválida.")

def title_and_year(details: dict[str, Any], kind: str) -> tuple[str, str]:
    title = details.get("title") if kind == "movie" else details.get("name")
    title = title or details.get("original_title") or details.get("original_name") or "Sin título"
    date = details.get("release_date") if kind == "movie" else details.get("first_air_date")
    return re.sub(r"\s+", " ", title).strip(), (date or "0000")[:4]


# -------------------------
# MediaInfo parsing
# -------------------------

@dataclass
class MediaInfoData:
    resolution: str
    source: str
    video_codec: str
    hdr_tokens: list[str]
    audio: list[dict[str, str]]
    subtitles: list[dict[str, str]]
    has_subs: bool
    internal_title: Optional[str]


@dataclass
class FilePlan:
    source: Path
    renamed: Path
    hardlink: Path
    title_spaces: str
    filename: str
    media: MediaInfoData
    season: Optional[int]
    episode: Optional[int]


@dataclass
class RunPlan:
    media_type: str
    tmdb_arg: str
    title: str
    year: str
    season_pack: bool
    season: Optional[int]
    torrent_title: str
    hardlink_folder: Path
    files: list[FilePlan]
    upload_commands: list[list[str]]


def run_mediainfo_json(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["mediainfo", "--Output=JSON", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "mediainfo falló")
    return json.loads(proc.stdout)


def media_tracks(info: dict[str, Any]) -> list[dict[str, Any]]:
    return info.get("media", {}).get("track", []) or []


def first_track(info: dict[str, Any], track_type: str) -> dict[str, Any]:
    return next((x for x in media_tracks(info) if x.get("@type") == track_type), {})


def map_resolution(video: dict[str, Any]) -> str:
    def number(value: Any) -> int:
        match = re.search(r"\d+", str(value or "0"))
        return int(match.group()) if match else 0

    width = number(video.get("Width") or video.get("Stored_Width"))
    height = number(video.get("Height") or video.get("Stored_Height"))

    # En cine scope/cinemascope, un 1080p real puede venir como 1920x800
    # por recorte vertical. Por eso se usa también la anchura, no solo la altura.
    if width >= 7600 or height >= 4000:
        return "4320p FUHD"
    if width >= 3800 or height >= 2000:
        return "2160p UHD"
    if width >= 1900 or height >= 1000:
        return "1080p"
    if width >= 1200 or height >= 700:
        return "720p"
    if height >= 560:
        return "576p SD"
    if height >= 520:
        return "540p SD"
    if width or height:
        return "480p SD"
    return "1080p"

def detect_source(path: Path) -> str:
    text = " ".join([path.name, path.parent.name, path.parent.parent.name])
    for pattern, value in SOURCE_PATTERNS:
        if pattern.search(text):
            return value
    return "BluRay"


def detect_video_codec(video: dict[str, Any], path: Path) -> str:
    text = f"{path.name} {video.get('WritingLibrary','')} {video.get('Encoded_Library','')}".lower()
    fmt = str(video.get("Format") or video.get("CodecID") or "").lower()

    if "x264" in text:
        return "x264"
    if "x265" in text:
        return "x265"
    if "av1" in fmt:
        return "AV1"
    if "vp9" in fmt:
        return "VP9"
    if "vp8" in fmt:
        return "VP8"
    if "vvc" in fmt or "h.266" in fmt or "h266" in fmt:
        return "VVC"
    if "hevc" in fmt or "h.265" in fmt or "h265" in fmt:
        return "HEVC"
    if "avc" in fmt or "h.264" in fmt or "h264" in fmt:
        return "AVC"
    if "vc-1" in fmt or "vc1" in fmt:
        return "VC-1"
    if "mpeg" in fmt:
        return "MPEG"
    return str(video.get("Format") or "AVC").strip()


def detect_hdr(video: dict[str, Any]) -> list[str]:
    text = " ".join(str(video.get(k) or "") for k in ("HDR_Format", "HDR_Format_String", "Format_Profile")).lower()
    dv = bool("dolby vision" in text or re.search(r"\bdv\b", text))
    hdr = "hdr" in text or "smpte st 2086" in text or "hlg" in text
    if dv and hdr:
        return ["DV", "HDR"]
    if dv:
        return ["DV"]
    if hdr:
        return ["HDR"]
    return []


def audio_codec(audio: dict[str, Any]) -> str:
    text = " ".join(
        str(audio.get(k) or "")
        for k in ("Format", "Format_Profile", "Format_Commercial_IfAny", "Format_AdditionalFeatures")
    ).lower()

    if "dts-hd master" in text or "dts-hd ma" in text:
        base = "DTS-HD MA"
    elif "dts-hd high" in text or "dts-hd hra" in text:
        base = "DTS-HD HRA"
    elif "dts:x" in text:
        base = "DTS:X"
    elif "truehd" in text:
        base = "TrueHD"
    elif "mlp" in text:
        base = "MLP"
    elif "e-ac-3" in text or "eac3" in text:
        base = "DD+"
    elif "ac-3" in text or "ac3" in text:
        base = "DD"
    elif "dts" in text:
        base = "DTS"
    elif "aac" in text:
        base = "AAC LC" if "lc" in text or "low complexity" in text else "AAC HE" if "he" in text else "AAC"
    elif "opus" in text:
        base = "OPUS"
    elif "flac" in text:
        base = "FLAC"
    elif "alac" in text:
        base = "ALAC"
    elif "mp3" in text or "mpeg audio" in text:
        base = "MP3"
    elif "pcm" in text:
        base = "PCM"
    else:
        base = str(audio.get("Format") or "DD").strip()

    if "atmos" in text and "Atmos" not in base:
        base += " Atmos"
    return base


def audio_channels(audio: dict[str, Any]) -> str:
    raw = str(audio.get("Channel_s_") or audio.get("Channels") or "").lower()
    match = re.search(r"\d+", raw)
    n = int(match.group()) if match else 2
    return {
        1: "Mono",
        2: "2.0",
        3: "3.0",
        4: "3.1",
        5: "5.0",
        6: "5.1",
        7: "6.1",
        8: "7.1",
    }.get(n, "2.0")


def map_audio_language(value: str, file_path: Path, index: int) -> str:
    key = (value or "").strip().lower()
    if key in LANG_MAP:
        return LANG_MAP[key]

    warn(f"Idioma de audio desconocido/no mapeado en {file_path.name}, pista {index}: {value or 'vacío'}")
    print("Códigos habituales: ESP, LAT, ING, JAP, COR, FRA, ALE, ITA, CAT, POR")
    return prompt("Introduce código de idioma", "ING").upper()



def map_subtitle_language(value: str) -> str:
    key = (value or "").strip().lower()
    return LANG_MAP.get(key, (value or "UND").strip().upper()[:3] or "UND")


def subtitle_format(track: dict[str, Any]) -> str:
    fmt = str(track.get("Format") or track.get("CodecID") or "SUB").strip()
    return fmt or "SUB"


def subtitle_forced(track: dict[str, Any]) -> str:
    text = " ".join(str(track.get(k) or "") for k in ("Forced", "Forced_String", "Title")).lower()
    return "Sí" if "yes" in text or "sí" in text or "forced" in text or "forzado" in text else "No"


def read_media_info(path: Path) -> MediaInfoData:
    info = run_mediainfo_json(path)
    general = first_track(info, "General")
    video = first_track(info, "Video")

    audio_items: list[dict[str, str]] = []
    audio_index = 0
    for track in media_tracks(info):
        if track.get("@type") != "Audio":
            continue
        audio_index += 1
        audio_items.append(
            {
                "lang": map_audio_language(track.get("Language") or track.get("Language_String") or "", path, audio_index),
                "codec": audio_codec(track),
                "channels": audio_channels(track),
            }
        )

    # ESP siempre primero.
    audio_items.sort(key=lambda x: (0 if x["lang"] == "ESP" else 1, x["lang"], x["codec"]))

    subtitle_items: list[dict[str, str]] = []
    subtitle_index = 0
    for track in media_tracks(info):
        if track.get("@type") != "Text":
            continue
        subtitle_index += 1
        subtitle_items.append(
            {
                "lang": map_subtitle_language(track.get("Language") or track.get("Language_String") or ""),
                "format": subtitle_format(track),
                "forced": subtitle_forced(track),
                "title": str(track.get("Title") or "").strip(),
            }
        )

    return MediaInfoData(
        resolution=map_resolution(video),
        source=detect_source(path),
        video_codec=detect_video_codec(video, path),
        hdr_tokens=detect_hdr(video),
        audio=audio_items,
        subtitles=subtitle_items,
        has_subs=bool(subtitle_items),
        internal_title=general.get("Title") or general.get("Movie"),
    )


def audio_name(audio_items: list[dict[str, str]]) -> str:
    if not audio_items:
        return "Mudo"

    langs: list[str] = []
    for item in audio_items:
        if item["lang"] not in langs:
            langs.append(item["lang"])

    same_codec_channels = len({(a["codec"], a["channels"]) for a in audio_items}) == 1

    if same_codec_channels and len(langs) == 2 and "ESP" in langs:
        return f"DUAL {audio_items[0]['codec']} {audio_items[0]['channels']}"

    if same_codec_channels and len(langs) >= 4:
        return f"MULTI {audio_items[0]['codec']} {audio_items[0]['channels']}"

    parts: list[str] = []
    for item in audio_items:
        parts.extend([item["lang"], item["codec"], item["channels"]])
    return " ".join(parts)


# -------------------------
# Season/file detection
# -------------------------

def season_from_folder(path: Path) -> Optional[int]:
    for item in (path, path.parent):
        m = SEASON_RE.match(item.name)
        if m:
            return int(m.group(1) or m.group(2))
    return None


def season_episode_from_name(path: Path, input_path: Path) -> tuple[Optional[int], Optional[int]]:
    text = " ".join([path.name, path.parent.name, input_path.name])
    for pattern in EPISODE_PATTERNS:
        m = pattern.search(text)
        if m:
            return int(m.group("s")), int(m.group("e"))

    season = season_from_folder(input_path) or season_from_folder(path.parent)
    episode_match = re.search(
        r"(?i)\bE(?:p(?:isode)?)?[ ._-]?(\d{1,3})\b|\bcap(?:itulo|ítulo)?[ ._-]?(\d{1,3})\b",
        path.stem,
    )
    if season and episode_match:
        episode = int(next(g for g in episode_match.groups() if g))
        return season, episode

    return season, None


def collect_video_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_EXTS else []

    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file()
        and item.suffix.lower() in VIDEO_EXTS
        and not re.search(r"(?i)\b(sample|trailer|extra|extras)\b", item.name)
    )


def infer_media_type(files: list[Path], root: Path, forced: Optional[str], season_forced: bool = False) -> str:
    if forced:
        return forced
    if season_forced:
        return "tv"
    if season_from_folder(root):
        return "tv"
    for item in files[:5]:
        season, episode = season_episode_from_name(item, root)
        if season or episode:
            return "tv"
    return "movie"


def parse_season_arg(value: Optional[str]) -> Optional[int]:
    if value in (None, "", "auto"):
        return None
    m = re.search(r"\d{1,2}", str(value))
    if not m:
        die("--season debe ser un número, S02, o usarse sin valor para autodetectar.")
    return int(m.group())


def safe_filename_from_title(title: str) -> str:
    clean = re.sub(r"[\\/?:%*|\"<>]", "", title)
    clean = clean.replace(":", "")
    clean = re.sub(r"\s+", ".", clean)
    clean = re.sub(r"\.{2,}", ".", clean)
    return clean.strip(".")


def build_emu_title(
    title: str,
    year: str,
    media: MediaInfoData,
    tag: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    pack: bool = False,
) -> str:
    prefix = title
    if pack and season:
        prefix += f" S{season:02d}"
    elif season and episode:
        prefix += f" S{season:02d}E{episode:02d}"

    parts = [
        prefix,
        year,
        media.resolution,
        media.source,
        *media.hdr_tokens,
        media.video_codec,
        audio_name(media.audio),
    ]
    if media.has_subs:
        parts.append("SUBS")

    return " ".join(part for part in parts if part).strip() + f"-{tag}"


def uploadrr_type(source: str) -> Optional[str]:
    return {
        "BDRemux": "remux",
        "BluRay": "encode",
        "MHD": "encode",
        "WEB-DL": "webdl",
        "WEBRip": "webrip",
        "HDTV": "hdtv",
        "FBD": "disc",
        "FDVD": "disc",
    }.get(source)


def uploadrr_source(source: str, resolution: str) -> Optional[str]:
    return {
        "BDRemux": "BluRay",
        "BluRay": "BluRay",
        "MHD": "BluRay",
        "WEB-DL": "WEB",
        "WEBRip": "WEB",
        "HDTV": "HDTV",
        "FBD": "BluRay",
        "FDVD": "DVD",
        "SD": "DVD",
    }.get(source)


def uploadrr_resolution(resolution: str) -> str:
    m = re.match(r"(\d+p)", resolution)
    return m.group(1) if m else "other"


def build_uploadrr_command(
    target: Path,
    title: str,
    media_type: str,
    tmdb_arg: str,
    season: Optional[int],
    episode: Optional[int],
    media: MediaInfoData,
) -> list[str]:
    # Siempre se usa el Python del venv. No se activa el venv en la shell;
    # llamar directamente a ./.venv/bin/python es más fiable para scripts.
    cmd = [
        str(venv_python()),
        str(uploadrr_py()),
        str(target),
        "-tk",
        DEFAULT_TRACKER,
        "-mt",
        title,
        "-tmdb",
        tmdb_arg,
        "-ua",
        "-c",
        "tv" if media_type == "tv" else "movie",
        "-res",
        uploadrr_resolution(media.resolution),
    ]

    # No se fuerza -t ni --source para EMU. En algunas configuraciones de
    # Uploadrr esos valores acaban convirtiéndose en un type_id rechazado por
    # el tracker. El tipo/fuente ya quedan reflejados en el título manual.

    if season:
        cmd.extend(["-season", f"S{season:02d}"])
    if episode:
        cmd.extend(["-episode", f"E{episode:02d}"])

    return cmd


def build_plan(
    cfg: AppConfig,
    root: Path,
    files: list[Path],
    media_type: str,
    tmdb_kind: str,
    tmdb_id: int,
    tmdb_details: dict[str, Any],
    forced_season: Optional[int] = None,
) -> RunPlan:
    title, year = title_and_year(tmdb_details, tmdb_kind)
    season_pack = media_type == "tv" and len(files) > 1

    season_guess = forced_season or season_from_folder(root)
    if media_type == "tv" and not season_guess and files:
        season_guess, _ = season_episode_from_name(files[0], root)

    if media_type == "tv" and not season_guess:
        season_guess = int(prompt("No detecté temporada. Número", "1"))

    hardlink_folder = cfg.hardlink_base / (f"{title} ({year})" if year and year != "0000" else title)
    if media_type == "tv":
        hardlink_folder = hardlink_folder / f"Season {season_guess}"

    file_plans: list[FilePlan] = []
    for source_file in files:
        if source_file.suffix.lower() != ".mkv":
            warn(f"Archivo no MKV detectado: {source_file}")
            if not yesno("¿Continuar con este archivo?", False):
                continue

        media = read_media_info(source_file)

        season = episode = None
        if media_type == "tv":
            season, episode = season_episode_from_name(source_file, root)
            season = season or season_guess
            if not episode:
                episode = int(prompt(f"No detecté episodio para {source_file.name}. Número", "1"))

        title_spaces = build_emu_title(title, year, media, cfg.tag, season, episode)
        filename = safe_filename_from_title(title_spaces) + source_file.suffix.lower()

        file_plans.append(
            FilePlan(
                source=source_file,
                renamed=source_file.with_name(filename),
                hardlink=hardlink_folder / filename,
                title_spaces=title_spaces,
                filename=filename,
                media=media,
                season=season,
                episode=episode,
            )
        )

    if not file_plans:
        die("No quedan archivos válidos para procesar.")

    tmdb_arg = f"{tmdb_kind}/{tmdb_id}"
    first_media = file_plans[0].media

    if season_pack:
        torrent_title = build_emu_title(title, year, first_media, cfg.tag, season_guess, None, pack=True)
        upload_commands = [
            build_uploadrr_command(
                hardlink_folder,
                torrent_title,
                media_type,
                tmdb_arg,
                season_guess,
                None,
                first_media,
            )
        ]
    else:
        torrent_title = file_plans[0].title_spaces
        upload_commands = [
            build_uploadrr_command(
                fp.hardlink,
                fp.title_spaces,
                media_type,
                tmdb_arg,
                fp.season,
                fp.episode,
                fp.media,
            )
            for fp in file_plans
        ]

    return RunPlan(
        media_type=media_type,
        tmdb_arg=tmdb_arg,
        title=title,
        year=year,
        season_pack=season_pack,
        season=season_guess if media_type == "tv" else None,
        torrent_title=torrent_title,
        hardlink_folder=hardlink_folder,
        files=file_plans,
        upload_commands=upload_commands,
    )


def nearest_existing_parent(path: Path) -> Optional[Path]:
    current = path
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def hardlink_device_issues(plan: RunPlan) -> list[str]:
    issues: list[str] = []
    seen: set[tuple[int, int]] = set()
    for fp in plan.files:
        dst_parent = nearest_existing_parent(fp.hardlink.parent)
        if not dst_parent:
            continue
        try:
            src_dev = fp.source.parent.stat().st_dev
            dst_dev = dst_parent.stat().st_dev
        except OSError:
            continue
        pair = (src_dev, dst_dev)
        if src_dev != dst_dev and pair not in seen:
            seen.add(pair)
            issues.append(
                f"'{fp.source.parent}' y '{dst_parent}' están en filesystems distintos. "
                "Un hardlink normal no puede cruzar filesystems."
            )
    return issues


def show_hardlink_warnings(plan: RunPlan, fatal: bool = False) -> None:
    issues = hardlink_device_issues(plan)
    if not issues:
        ok("Comprobación hardlink: origen y destino parecen compatibles")
        return
    for issue in issues:
        warn(issue)
    msg = (
        "Los hardlinks fallarán si origen y destino están en filesystems distintos. "
        "Mueve la carpeta base de hardlinks al mismo disco/montaje que los archivos originales "
        "o usa otra estrategia."
    )
    if fatal:
        die(msg)
    warn(msg)


# -------------------------
# Output / apply
# -------------------------

def show_detected_file(fp: FilePlan, plan: RunPlan) -> None:
    rule(f"Detectado: {fp.source.name}")
    kv("Tipo", "Serie" if plan.media_type == "tv" else "Película")
    kv("TMDb", plan.tmdb_arg)
    kv("Título", plan.title)
    kv("Año estreno", plan.year)
    if fp.season:
        kv("Temporada", f"S{fp.season:02d}")
    if fp.episode:
        kv("Episodio", f"E{fp.episode:02d}")

    print()
    print(bold("Vídeo:"))
    kv("Resolución", fp.media.resolution)
    kv("Fuente", fp.media.source)
    kv("Códec", fp.media.video_codec)
    kv("HDR/DV", " ".join(fp.media.hdr_tokens) if fp.media.hdr_tokens else "No detectado")

    print()
    print(bold("Audio:"))
    if fp.media.audio:
        for i, audio in enumerate(fp.media.audio, 1):
            print(f"  {cyan(str(i)+'.')} {audio['lang']:<8} {audio['codec']} {audio['channels']}")
    else:
        print("  Mudo")

    print()
    print(bold("Subtítulos:"))
    if fp.media.subtitles:
        for i, sub in enumerate(fp.media.subtitles, 1):
            forced = " " + yellow("(forzado)") if sub.get("forced") == "Sí" else ""
            title = f" - {sub.get('title')}" if sub.get("title") else ""
            print(f"  {cyan(str(i)+'.')} {sub['lang']:<8} {sub['format']}{forced}{gray(title)}")
        kv("Nombre usado", "SUBS")
    else:
        print("  No detectados")
        kv("Nombre usado", "—")


def show_plan(plan: RunPlan) -> None:
    box("EMUWAREZ AUTO UPLOAD", "Propuesta")
    kv("Archivos", len(plan.files))
    kv("Modo Uploadrr", "Carpeta completa / temporada" if plan.season_pack else "Archivo individual")

    for fp in plan.files:
        show_detected_file(fp, plan)
        rule("Propuesta eMuwarez")
        print(bold("Título del Torrent en Emuwarez:"))
        print("  " + green(plan.torrent_title if plan.season_pack else fp.title_spaces))
        print()
        print(bold("Nombre y Título interno del archivo:"))
        print("  Archivo:        " + green(fp.filename))
        print("  Título interno: " + green(fp.title_spaces))
        print()
        print(bold("Hardlink:"))
        print("  " + cyan(str(fp.hardlink)))



def proposal_action() -> str:
    print()
    print(bold("Acción sobre la propuesta:"))
    print("  " + cyan("[E]") + " Editar nombre propuesto")
    print("  " + cyan("[Y]") + " Continuar y aplicar cambios")
    print("  " + cyan("[N]") + " Cancelar")
    while True:
        value = input(f"\n{cyan('Selecciona una opción')} {gray('[Y]')}: ").strip().lower()
        if not value:
            return "y"
        if value in {"e", "editar"}:
            return "e"
        if value in {"y", "s", "si", "sí", "yes", "continuar"}:
            return "y"
        if value in {"n", "no", "cancelar"}:
            return "n"
        warn("Opción inválida. Usa E, Y o N.")


def rebuild_upload_commands(plan: RunPlan) -> None:
    if plan.season_pack:
        first_media = plan.files[0].media
        plan.upload_commands = [
            build_uploadrr_command(
                plan.hardlink_folder,
                plan.torrent_title,
                plan.media_type,
                plan.tmdb_arg,
                plan.season,
                None,
                first_media,
            )
        ]
    else:
        plan.torrent_title = plan.files[0].title_spaces
        plan.upload_commands = [
            build_uploadrr_command(
                fp.hardlink,
                fp.title_spaces,
                plan.media_type,
                plan.tmdb_arg,
                fp.season,
                fp.episode,
                fp.media,
            )
            for fp in plan.files
        ]


def update_file_title(fp: FilePlan, new_title: str) -> None:
    fp.title_spaces = re.sub(r"\s+", " ", new_title).strip()
    fp.filename = safe_filename_from_title(fp.title_spaces) + fp.source.suffix.lower()
    fp.renamed = fp.source.with_name(fp.filename)
    fp.hardlink = fp.hardlink.parent / fp.filename


def edit_plan_interactive(plan: RunPlan) -> None:
    rule("Edición manual de la propuesta")
    if plan.season_pack:
        new_torrent = prompt("Título del Torrent en Emuwarez", plan.torrent_title)
        plan.torrent_title = re.sub(r"\s+", " ", new_torrent).strip()

        if yesno("¿Editar también nombres/títulos internos de los episodios?", False):
            for fp in plan.files:
                new_title = prompt(f"Título interno para {fp.source.name}", fp.title_spaces)
                update_file_title(fp, new_title)
    else:
        fp = plan.files[0]
        new_title = prompt("Título del Torrent en Emuwarez / título interno", fp.title_spaces)
        update_file_title(fp, new_title)
        plan.torrent_title = fp.title_spaces

    rebuild_upload_commands(plan)


def update_mkv_internal_title(path: Path, title: str) -> None:
    if path.suffix.lower() != ".mkv":
        warn("No es MKV; no se cambia título interno.")
        return
    if not shutil.which("mkvpropedit"):
        warn("mkvpropedit no disponible; no se cambia título interno.")
        return

    proc = subprocess.run(
        ["mkvpropedit", str(path), "--edit", "info", "--set", f"title={title}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode == 0:
        ok(f"Título interno MKV: {title}")
    else:
        warn("No se pudo actualizar título interno MKV: " + (proc.stderr.strip() or proc.stdout.strip()))


def apply_file_plan(fp: FilePlan) -> None:
    current_path = fp.source

    if fp.source != fp.renamed:
        if fp.renamed.exists():
            die(f"Ya existe el archivo destino: {fp.renamed}")
        fp.source.rename(fp.renamed)
        current_path = fp.renamed
        ok(f"Archivo renombrado: {fp.renamed.name}")
    else:
        ok("Nombre de archivo ya correcto")

    update_mkv_internal_title(current_path, fp.title_spaces)

    fp.hardlink.parent.mkdir(parents=True, exist_ok=True)
    if fp.hardlink.exists():
        try:
            if os.path.samefile(current_path, fp.hardlink):
                ok("Hardlink ya existente")
                return
        except Exception:
            pass
        die(f"Ya existe el destino del hardlink: {fp.hardlink}")

    try:
        os.link(current_path, fp.hardlink)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            die(
                "No se pudo crear el hardlink porque origen y destino están en filesystems distintos.\n"
                f"Origen:  {current_path}\n"
                f"Destino: {fp.hardlink}\n"
                "Un hardlink requiere que ambos estén en el mismo filesystem."
            )
        die(f"No se pudo crear el hardlink: {exc}")
    ok(f"Hardlink creado: {fp.hardlink}")



def strip_ansi(text: str) -> str:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b\][^\a]*(?:\a|\x1b\\)", "", text)
    return text.replace("\b", "")


def progress_bar(percent: int, label: str, state: str = "", ok_state: Optional[bool] = None, final: bool = False) -> None:
    percent = max(0, min(100, int(percent)))
    width = 28
    done = int(round(width * percent / 100))
    bar = "█" * done + "░" * (width - done)
    suffix = ""
    if ok_state is True:
        suffix = " " + green("✓")
    elif ok_state is False:
        suffix = " " + red("✗")
    elif state:
        suffix = " " + state
    line = f"  [{cyan(bar)}] {percent:3d}%  {label}{suffix}"
    print("\r\033[K" + line, end="\n" if final else "", flush=True)


def show_uploadrr_command(cmd: list[str]) -> None:
    rule("Comando")
    target = cmd[2] if len(cmd) > 2 else ""
    title = ""
    tmdb = ""
    tracker = DEFAULT_TRACKER
    category = ""
    resolution = ""
    for i, arg in enumerate(cmd):
        if arg == "-mt" and i + 1 < len(cmd):
            title = cmd[i + 1]
        elif arg == "-tmdb" and i + 1 < len(cmd):
            tmdb = cmd[i + 1]
        elif arg == "-tk" and i + 1 < len(cmd):
            tracker = cmd[i + 1]
        elif arg == "-c" and i + 1 < len(cmd):
            category = cmd[i + 1]
        elif arg == "-res" and i + 1 < len(cmd):
            resolution = cmd[i + 1]
    kv("Archivo", target)
    if title:
        kv("Título", title)
    if tmdb:
        kv("TMDb", tmdb)
    kv("Tracker", tracker)
    if category:
        kv("Categoría", category)
    if resolution:
        kv("Resolución", resolution)


def parse_uploadrr_progress(fragment: str) -> Optional[tuple[int, str, str, Optional[bool]]]:
    clean = strip_ansi(fragment)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return None
    if "Rejected Upload" in clean or "Validation Error" in clean or '"success":false' in clean:
        return 100, "Uploadrr", "falló", False
    if "Torrent uploaded successfully" in clean or "Uploaded successfully" in clean:
        return 100, "Uploadrr", "finalizado", True
    if "Starting queue processing" in clean:
        return 10, "Procesando cola", "", True
    if "Processing as video" in clean:
        return 14, "Procesando vídeo", "", None
    if "Exporting MediaInfo" in clean:
        return 18, "MediaInfo", "exportando", None
    if "MediaInfo JSON Exported" in clean or "MediaInfo text Exported" in clean:
        return 22, "MediaInfo", "", True
    if "Processing video metadata" in clean:
        return 24, "Metadatos vídeo", "procesando", None
    if "Searching for existing torrents" in clean:
        return 26, "Duplicados", "buscando", None
    if "No dupes found" in clean:
        return 28, "Duplicados", "no encontrados", True
    if "Generating screenshots" in clean:
        return 32, "Capturas", "generando", None
    m = re.search(r"Uploading screenshots\s+(\d+)\s*/\s*(\d+)", clean, re.I)
    if m:
        cur, total = int(m.group(1)), max(1, int(m.group(2)))
        pct = 30 + int((cur / total) * 20)
        return pct, "Capturas", f"{cur}/{total}", cur >= total
    m = re.search(r"Hashing with torf:\s*([0-9]+(?:\.[0-9]+)?)%", clean, re.I)
    if m:
        hp = float(m.group(1))
        pct = 50 + int((hp / 100.0) * 45)
        return pct, "Creando torrent", f"{hp:.1f}%", hp >= 100
    if "Uploading to EMU" in clean:
        return 96, "Subiendo a EMU", "", None
    return None


def run_uploadrr_command(cmd: list[str], debug_uploadrr: bool = False) -> None:
    box("EMUWAREZ AUTO UPLOAD", "Uploadrr")
    show_uploadrr_command(cmd)
    rule("Progreso Uploadrr")
    progress_bar(0, "Preparando Uploadrr")
    output_parts: list[str] = []
    last_pct = 0
    last_signature: Optional[tuple[int, str, str, Optional[bool]]] = None
    master_fd: Optional[int] = None
    slave_fd: Optional[int] = None
    rc = 1
    try:
        master_fd, slave_fd = os.openpty()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.Popen(
            cmd,
            cwd=uploadrr_dir(),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
        )
        os.close(slave_fd)
        slave_fd = None
        buffer = ""
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.15)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if not data:
                    if proc.poll() is not None:
                        break
                    continue
                text = data.decode("utf-8", "replace")
                output_parts.append(text)
                if debug_uploadrr:
                    print(text, end="", flush=True)
                normalized = text.replace("\r", "\n")
                candidates = [normalized]
                buffer += normalized
                parts = re.split(r"\n+", buffer)
                buffer = parts.pop() if parts else ""
                candidates.extend(parts)
                for piece in candidates:
                    parsed = parse_uploadrr_progress(piece)
                    if not parsed:
                        continue
                    pct, label, state, ok_state = parsed
                    if pct < last_pct and ok_state is not False:
                        pct = last_pct
                    sig = (pct, label, state, ok_state)
                    if sig != last_signature:
                        last_pct = max(last_pct, pct)
                        last_signature = sig
                        progress_bar(pct, label, state, ok_state)
            elif proc.poll() is not None:
                break
        rc = proc.wait()
        if buffer:
            parsed = parse_uploadrr_progress(buffer)
            if parsed:
                pct, label, state, ok_state = parsed
                progress_bar(max(last_pct, pct), label, state, ok_state)
    finally:
        for fd in (slave_fd, master_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
    raw_output = "".join(output_parts)
    if rc != 0:
        progress_bar(100, "Uploadrr", "falló", False, final=True)
        rule("Error Uploadrr")
        print(gray("\n".join(strip_ansi(raw_output).splitlines()[-40:])[-6000:]))
        raise subprocess.CalledProcessError(rc, cmd)
    progress_bar(100, "Uploadrr", "finalizado", True, final=True)

def find_emu_description_file(plan: RunPlan) -> Optional[Path]:
    tmp_dir = uploadrr_dir() / "tmp"
    if not tmp_dir.exists():
        return None

    # Ruta prioritaria confirmada por Uploadrr:
    # Uploadrr/tmp/{Nombre.Del.Archivo.Final.mkv}/[EMU]DESCRIPTION.txt
    for fp in plan.files:
        exact = tmp_dir / fp.filename / "[EMU]DESCRIPTION.txt"
        if exact.exists():
            return exact

    candidates = sorted(
        tmp_dir.rglob("[EMU]DESCRIPTION.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None

    final_names = {fp.filename for fp in plan.files}
    for candidate in candidates:
        if candidate.parent.name in final_names:
            return candidate

    return candidates[0]


def extract_youtube_trailer_url(description_file: Path) -> Optional[str]:
    try:
        text = description_file.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        warn(f"No se pudo leer la descripción de Uploadrr: {exc}")
        return None

    m = re.search(r"\[youtube\]\s*([A-Za-z0-9_-]+)\s*\[/youtube\]", text, re.I)
    if not m:
        return None
    return f"https://www.youtube.com/watch?v={m.group(1)}"


def show_upload_result_summary(plan: RunPlan) -> None:
    rule("Resultado de subida")
    kv("Torrent EMU", plan.torrent_title)

    description_file = find_emu_description_file(plan)
    if not description_file:
        kv("Descripción Uploadrr", "No encontrada")
        kv("Trailer", "No verificable")
        LOGGER.warning("Descripción Uploadrr no encontrada en %s", uploadrr_dir() / "tmp")
        return

    kv("Descripción Uploadrr", description_file)
    trailer_url = extract_youtube_trailer_url(description_file)
    if trailer_url:
        kv("Trailer", trailer_url)
        LOGGER.info("Trailer detectado: %s", trailer_url)
    else:
        kv("Trailer", "No detectado")
        LOGGER.info("Trailer no detectado en %s", description_file)


def execute_plan(plan: RunPlan, dry_run: bool, no_upload: bool, debug_uploadrr: bool = False) -> None:
    if dry_run:
        box("RESUMEN DRY RUN")
        kv("Archivos analizados", len(plan.files))
        kv("Hardlinks simulados", len(plan.files))
        kv("Uploadrr simulado", len(plan.upload_commands))
        ok("No se ha modificado ningún archivo.")
        print("Para aplicar, ejecuta el mismo comando " + bold("sin --dry-run") + ".")
        return

    box("APLICANDO CAMBIOS")
    for index, fp in enumerate(plan.files, 1):
        rule(f"Archivo {index} de {len(plan.files)}")
        apply_file_plan(fp)

    if no_upload:
        warn("--no-upload activo: no se ejecuta Uploadrr.")
        return

    for cmd in plan.upload_commands:
        run_uploadrr_command(cmd, debug_uploadrr=debug_uploadrr)

    show_upload_result_summary(plan)


# -------------------------
# Main processing
# -------------------------

def process_path(args: argparse.Namespace) -> None:
    passphrase = resolve_config_passphrase(args, required=False)
    cfg = load_app_config(passphrase=passphrase)

    if not cfg:
        cfg = configure(
            skip_bootstrap=False,
            preferred_python=args.python,
            ask_system_update=not args.skip_system_update,
            allow_system_install=not args.no_system_install,
            config_passphrase=passphrase,
        )

    register_config_restore_handlers()
    restore_uploadrr_config_from_backup()
    inject_app_config_into_uploadrr(cfg)

    try:
        ensure_runtime_ready(cfg, preferred_python=args.python, allow_system_install=not args.no_system_install)

        root = expand_path(args.path)
        if not root.exists():
            die(f"No existe: {root}")

        files = collect_video_files(root)
        if not files:
            die("No se encontraron vídeos en la ruta indicada.")

        forced_season = parse_season_arg(getattr(args, "season", None))
        season_forced = getattr(args, "season", None) is not None
        media_type = infer_media_type(files, root, args.type, season_forced=season_forced)

        if len(files) > 1 and media_type != "tv":
            warn("Se detectaron varios vídeos en la carpeta, pero no se ha detectado una temporada.")
            if yesno("¿Tratar esta carpeta como una temporada completa?", False):
                media_type = "tv"
            else:
                die("Cancelado para evitar procesar varias películas como si fueran una única subida. Usa --season si es una temporada.")

        first_media: Optional[MediaInfoData] = None
        try:
            first_media = read_media_info(files[0])
        except Exception as exc:
            warn(f"No pude leer MediaInfo antes de buscar TMDb: {exc}")

        if media_type == "tv" and season_from_folder(root):
            seed_name = root.parent.name
        else:
            seed_name = root.name if root.is_dir() else root.stem

        score_inputs = [seed_name, files[0].stem]
        if first_media and first_media.internal_title:
            score_inputs.append(first_media.internal_title)

        query_candidates = tmdb_query_variants(clean_title(seed_name) or seed_name, score_inputs)
        query = query_candidates[0] if query_candidates else (clean_title(seed_name) or seed_name)

        box("EMUWAREZ AUTO UPLOAD", "Búsqueda TMDb")
        kv("Tipo", "Serie" if media_type == "tv" else "Película")
        kv("Búsqueda inicial", query)
        if len(query_candidates) > 1:
            kv("Búsquedas alternativas", ", ".join(query_candidates[1:4]))
        if first_media and first_media.internal_title:
            kv("Título interno detectado", first_media.internal_title)

        key = cfg.tmdb_api_key or tmdb_api_key()
        if not key:
            die(f"No encuentro DEFAULT.tmdb en la configuración. Ejecuta --configure.")

        rule("TMDb")
        action(f"Buscando en TMDb: {query}")
        tmdb_kind, tmdb_id, details = choose_tmdb(key, query, media_type, score_inputs, args.tmdb)
        plan = build_plan(cfg, root, files, media_type, tmdb_kind, tmdb_id, details, forced_season=forced_season)

        while True:
            show_plan(plan)
            show_hardlink_warnings(plan, fatal=not args.dry_run)

            if args.dry_run:
                execute_plan(plan, dry_run=True, no_upload=args.no_upload, debug_uploadrr=args.debug_uploadrr)
                return

            if args.yes:
                break

            action_choice = proposal_action()
            if action_choice == "e":
                edit_plan_interactive(plan)
                continue
            if action_choice == "n":
                warn("Cancelado. No se ha modificado nada.")
                return
            break

        execute_plan(plan, dry_run=False, no_upload=args.no_upload, debug_uploadrr=args.debug_uploadrr)
    finally:
        restore_uploadrr_config_from_backup()



# -------------------------
# Utility commands
# -------------------------

def doctor(passphrase: Optional[str] = None) -> None:
    box("EMUWAREZ AUTO UPLOAD", "Doctor")
    check_layout(strict=False)

    if venv_python().exists():
        kv("Venv", venv_python())
        try:
            proc = subprocess.run([str(venv_python()), "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            kv("Python venv", proc.stdout.strip())
        except Exception:
            pass
    else:
        warn("Venv no existe")

    for tool in ("mediainfo", "mkvpropedit"):
        found = shutil.which(tool)
        if found:
            kv(tool, found)
        else:
            kv(tool, "NO ENCONTRADO")

    cfg = load_app_config(passphrase=passphrase)
    if cfg:
        kv("Config propia", CONFIG_ENC_FILE if cfg.config_encrypted else CONFIG_FILE)
        kv("Hardlinks", cfg.hardlink_base)
        kv("Tag", cfg.tag)
        kv("TMDb API", mask_secret(cfg.tmdb_api_key))
        kv("EMU API", mask_secret(cfg.emu_api_key))
        kv("EMU anónimo", cfg.emu_anon)
        kv("Img hosts", ", ".join(cfg.img_hosts))
        kv("Torrent client", cfg.torrent_client)
        kv("Watch folder", cfg.watch_folder or "no configurado")
    else:
        warn("Config propia no existe")

    lines = read_yaml_lines()
    kv("config.yaml", uploadrr_config())
    kv("config.yaml.bak", uploadrr_config_backup() if uploadrr_config_backup().exists() else "no existe")
    kv("DEFAULT.tmdb", "configurado" if get_block_key(lines, "DEFAULT", "tmdb") else "no configurado")
    kv("TRACKERS.EMU.api_key", "configurado" if get_emu_api_key(lines) else "no configurado")
    kv("TRACKERS.EMU.anon", get_emu_anon(lines))
    kv("SCREENS.img_hosts", ", ".join(get_img_hosts(lines) or []))
    kv("TORRENT_CLIENTS.watch", get_watch_folder(lines) or "no configurado")
    kv("Log", current_log_file())


def test_apis(passphrase: Optional[str] = None) -> None:
    box("EMUWAREZ AUTO UPLOAD", "Test APIs")
    cfg = load_app_config(passphrase=passphrase)
    if cfg:
        tmdb = cfg.tmdb_api_key
        emu = cfg.emu_api_key
        image_keys = cfg.image_api_keys
    else:
        lines = read_yaml_lines()
        tmdb = tmdb_api_key()
        emu = emu_api_key()
        image_keys = get_screen_api_keys(lines)

    if tmdb:
        action("Probando TMDb")
        validate_tmdb(tmdb)
    else:
        warn("TMDb no configurado")

    if emu:
        action("Probando EMU")
        validate_emu(emu)
    else:
        warn("EMU no configurado")

    action("Probando Imgbox")
    validate_imgbox()

    for host, api_key in image_keys.items():
        if api_key:
            action(f"Probando {IMAGE_HOST_LABELS.get(host, host)}")
            validate_image_api(host, api_key)
        else:
            warn(f"SCREENS.API_KEYS.{host} no configurado")


def resolve_config_passphrase(args: argparse.Namespace, required: bool = False) -> Optional[str]:
    pass_file = getattr(args, "config_pass_file", None)
    pass_env = getattr(args, "config_pass_env", None)
    if pass_file:
        path = expand_path(pass_file)
        try:
            value = path.read_text(encoding="utf-8").splitlines()[0].strip()
        except Exception as exc:
            die(f"No se pudo leer --config-pass-file: {exc}")
        if not value and required:
            die("El archivo de contraseña está vacío.")
        return value or None
    if pass_env:
        value = os.environ.get(pass_env, "").strip()
        if not value and required:
            die(f"La variable de entorno {pass_env} no está definida o está vacía.")
        return value or None
    return None


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emuwarez_upload.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Renombra/prepara subidas eMuwarez y lanza Uploadrr.",
        epilog=textwrap.dedent(
            f"""
            Estructura esperada:
              ./emuwarez_upload.py
              ./Uploadrr/upload.py
              ./Uploadrr/data/config.yaml
              ./.venv/

            Primer uso:
              ./emuwarez_upload.py --configure

            Configuración cifrada en automatización:
              ./emuwarez_upload.py "/ruta/Pelicula" --config-pass-file ~/.config/emuwarez-renamer/passphrase
              EMUWAREZ_CONFIG_PASS='...' ./emuwarez_upload.py "/ruta/Pelicula" --config-pass-env EMUWAREZ_CONFIG_PASS

            Uso normal: muestra propuesta y pregunta si continuar. No hace falta --apply.
              ./emuwarez_upload.py "/ruta/Pelicula"
              ./emuwarez_upload.py "/ruta/One Piece/Season 2"
              ./emuwarez_upload.py /ruta/One Piece/Season 2

            Simulación:
              ./emuwarez_upload.py "/ruta/Pelicula" --dry-run

            Entorno virtual:
              Se crea en ./.venv usando Python 3.11 si está disponible.
              Otras versiones >=3.10 requieren confirmación.
              Uploadrr se ejecuta siempre con ./.venv/bin/python.

            Log:
              {current_log_file()}
            """
        ),
    )
    parser.add_argument("path", nargs="*", help="Ruta de película o carpeta de temporada. Con espacios, usa comillas o escapado con \\.")
    parser.add_argument("-p", "--path", dest="path_parts", nargs="+", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="Simula todo; no modifica nada")
    parser.add_argument("--yes", action="store_true", help="Aplica sin confirmación final")
    parser.add_argument("--no-upload", action="store_true", help="No ejecuta Uploadrr")
    parser.add_argument("--season", nargs="?", const="auto", help="Forzar carpeta como temporada completa. Opcional: número, 2 o S02")
    parser.add_argument("--debug-uploadrr", "--uploader-debug", dest="debug_uploadrr", action="store_true", help="Mostrar salida original completa de Uploadrr")
    parser.add_argument("--tmdb", help="Forzar TMDb: movie/123, tv/123 o número")
    parser.add_argument("--type", choices=["movie", "tv"], help="Forzar tipo")
    parser.add_argument("--configure", action="store_true", help="Bootstrap + configuración inicial")
    parser.add_argument("--bootstrap", action="store_true", help="Crear/verificar .venv e instalar/verificar Uploadrr")
    parser.add_argument("--scan-imports", action="store_true", help="Mostrar imports externos de ./Uploadrr")
    parser.add_argument("--check-uploadrr", action="store_true", help="Ejecutar ./Uploadrr/upload.py HELP --help con el venv")
    parser.add_argument("--doctor", action="store_true", help="Comprobar entorno/configuración")
    parser.add_argument("--test-apis", action="store_true", help="Probar TMDb, EMU, Imgbox y hosts de imágenes configurados")
    parser.add_argument("--config-pass-file", help="Archivo con la contraseña para desbloquear config.enc.json")
    parser.add_argument("--config-pass-env", help="Nombre de variable de entorno con la contraseña para desbloquear config.enc.json")
    parser.add_argument("--python", dest="python", help="Python para crear/recrear .venv. Recomendado: python3.11")
    parser.add_argument("--skip-system-update", action="store_true", help="No preguntar por sudo apt update && sudo apt upgrade -y durante configuración/bootstrap")
    parser.add_argument("--no-system-install", action="store_true", help="No intentar instalar mediainfo/mkvtoolnix automáticamente con apt")
    parser.add_argument("--no-color", action="store_true", help="Desactivar colores")
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)  # compatibilidad antigua
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    UI.color = not args.no_color
    log_file = setup_logging()
    LOGGER.info("Argumentos recibidos: %s", [a for a in (argv or sys.argv[1:]) if not str(a).startswith("--config-pass")])
    ensure_launcher_python_supported()
    confirm_launcher_python_recommended()
    if getattr(args, "path_parts", None):
        args.path = " ".join(args.path_parts)
    elif isinstance(args.path, list):
        args.path = " ".join(args.path)

    try:
        if args.apply:
            warn("--apply ya no es necesario; en modo normal se pregunta y se aplica si aceptas.")

        if args.bootstrap:
            bootstrap(preferred_python=args.python, ask_system_update=not args.skip_system_update, allow_system_install=not args.no_system_install)
            return 0
        if args.scan_imports:
            scan_imports_command()
            return 0
        if args.check_uploadrr:
            ensure_venv_ready(preferred_python=args.python)
            return 0 if check_uploadrr() else 1
        passphrase = resolve_config_passphrase(args, required=False)
        if args.doctor:
            doctor(passphrase=passphrase)
            return 0
        if args.test_apis:
            test_apis(passphrase=passphrase)
            return 0
        if args.configure:
            configure(preferred_python=args.python, ask_system_update=not args.skip_system_update, allow_system_install=not args.no_system_install, config_passphrase=passphrase)
            return 0
        if args.path:
            process_path(args)
            return 0

        if not load_app_config(passphrase=resolve_config_passphrase(args, required=False)):
            configure(preferred_python=args.python, ask_system_update=not args.skip_system_update, allow_system_install=not args.no_system_install, config_passphrase=resolve_config_passphrase(args, required=False))
            return 0

        print("Usa --help. Ejemplo:")
        print(cyan('./emuwarez_upload.py "/ruta/Pelicula"'))
        return 0

    except KeyboardInterrupt:
        print()
        warn("Interrumpido por el usuario.")
        LOGGER.warning("Ejecución cancelada por Ctrl+C")
        restore_uploadrr_config_from_backup()
        show_log_path(log_file)
        return 130
    except subprocess.CalledProcessError as exc:
        err(f"Comando falló: {shell_join([str(x) for x in exc.cmd]) if isinstance(exc.cmd, list) else exc.cmd}")
        restore_uploadrr_config_from_backup()
        show_log_path(log_file)
        return exc.returncode or 1
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code:
            restore_uploadrr_config_from_backup()
            show_log_path(log_file)
        return code
    except Exception as exc:
        LOGGER.exception("Error no controlado")
        err(str(exc))
        restore_uploadrr_config_from_backup()
        show_log_path(log_file)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

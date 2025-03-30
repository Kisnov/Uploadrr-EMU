import importlib
from src.console import log

MODULE_TO_PIP = {
    "ffmpeg": "ffmpeg-python",
    "cinemagoer": "IMDbPY",
}

REQUIRED_MODULES = [
    "aiofiles",
    "anitopy",
    "beautifulsoup4",
    "bencode.py",
    "cinemagoer",
    "deluge-client",
    "ffmpeg",
    "guessit",
    "httpx",
    "imdb",
    "Jinja2",
    "langcodes",
    "nest_asyncio",
    "packaging",
    "pathlib",
    "pymediainfo",
    "pyimgbox",
    "pyotp",
    "pyoxipng",
    "pyrobase",
    "pyyaml",
    "qbittorrent-api",
    "requests",
    "rich",
    "tinytag",
    "torf",
    "tmdbsimple",
    "unidecode",
]

def check_imports():
    missing_modules = []
    for module in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            missing_modules.append(MODULE_TO_PIP.get(module, module))  # Use pip package name if mapped

    if missing_modules:
        log.error("\n[bold red]Missing required modules:[/bold red]")
        log.error(" ".join(missing_modules))
        log.error("\nPlease install them with:")
        log.error(f"\n    pip install {' '.join(missing_modules)}\n")
        exit(1)  # Exit with an error code

check_imports()

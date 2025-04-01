import importlib
from src.console import console, log

MODULE_TO_PIP = {
    "bencode": "bencode.py",
    "bs4": "beautifulsoup4",
    "deluge_client": "deluge-client",
    "ffmpeg": "ffmpeg-python",
    "imdb": "IMDbPY",
    "jinja2": "Jinja2",
    "oxipng": "pyoxipng",
    "qbittorrentapi": "qbittorrent-api",
    "yaml": "PyYAML",
}

REQUIRED_MODULES = [
    "aiocache",
    "aiofiles",
    "anitopy",
    "bencode",
    "bs4",
    "deluge_client",
    "ffmpeg",
    "guessit",
    "httpx",
    "imdb",
    "jinja2",
    "langcodes",
    "nest_asyncio",
    "oxipng",
    "packaging",
    "pathlib",
    "pymediainfo",
    "pyimgbox",
    "pyotp",
    "pyrobase",
    "qbittorrentapi",
    "requests",
    "rich",
    "tinytag",
    "tmdbsimple",
    "torf",
    "unidecode",
    "yaml",
]

def check_imports():
    missing_modules = []
    for module in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            missing_modules.append(MODULE_TO_PIP.get(module, module))  # Use pip package name if mapped

    if missing_modules:
        log.error("\n[bold red]Missing modules found:[/bold red]")
        console.print(" ".join(missing_modules))
        log.error("\nPlease install them with:")
        console.print(f"\n   [magenta] pip install {' '.join(missing_modules)}\n")
        exit(1)
        
check_imports()

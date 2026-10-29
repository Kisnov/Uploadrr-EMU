import asyncio
import contextlib
import inspect
import io
import platform
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import bs4
import httpx
import torf
from rich.pretty import Pretty

from src.utils.console import console, log
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent, extract_size_from_torrent

# Segundos de espera antes de cada reintento.
UPLOAD_RETRY_DELAYS = (5, 15)
DOWNLOAD_RETRY_DELAYS = (5, 15, 30)
# Errores del servidor o de Cloudflare en los que la subida se repite.
RETRY_STATUSES = {500, 502, 503, 504, 520, 521, 522, 523, 524}
MAX_SEARCH_PAGES = 20


class DupeSearchError(RuntimeError):
    """No se pudo comprobar si el contenido ya está en el tracker."""


def _same_content(downloaded: bytes, base: Path) -> bool:
    """Si el .torrent descargado describe los mismos ficheros y piezas que base (el tracker solo cambia el infohash)."""
    try:
        theirs = torf.Torrent.read_stream(io.BytesIO(downloaded), validate=False).metainfo["info"]
        ours = torf.Torrent.read(base, validate=False).metainfo["info"]
    except (torf.TorfError, KeyError):
        return False
    return all(theirs.get(key) == ours.get(key) for key in ("name", "piece length", "pieces", "length", "files"))


class Unit3d:
    """
    Base class for Unit3D trackers.
    Subclasses must define:
    - tracker: str
    - source_flag: str
    - domain: str
    - allowed_imghosts: list[str]
    - banned_groups: list[str] or dict

    And implement/override as needed:
    - async def get_cat_id(self, meta: Dict[str, Any]) -> str
    - async def get_type_id(self, meta: Dict[str, Any]) -> str
    - async def get_res_id(self, meta: Dict[str, Any]) -> str
    - async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]
    - async def edit_name(self, meta: Dict[str, Any]) -> str (optional, for custom naming)
    - async def upload(self, meta: Dict[str, Any]) -> bool
    - async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.type = "unit3d"
        self.timeout = httpx.Timeout(45.0)
        self.upload_url = f"{self.domain}/api/torrents/upload"
        self.search_url = f"{self.domain}/api/torrents/filter"

    # fmt: off
    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            'MUSIC': '3'
        }.get(category_name, "0")
        return category_id

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        media_type = meta["type"]
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "FLAC": "7"
        }.get(media_type, "0")
        return type_id

    async def get_res_id(self, meta: Dict[str, Any]) -> str:
        resolution = meta["resolution"]
        resolution_id = {
            "8640p": "10",
            "4320p": "1",
            "2160p": "2",
            "1440p": "3",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
        }.get(resolution, "10")
        return resolution_id
    # fmt: on
    async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]:
        """
        Check if content is banned.
        Returns (True, True) for immediate ban, (True, False) for warning, (False, False) for no ban.
        """
        return False, False

    async def get_extra_data(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Hook for extra data payload fields. Override in Trackers."""
        return {}

    async def get_extra_files(self, meta: Dict[str, Any]) -> Dict[str, Tuple[str, str, str]]:
        """
        Hook for extra file uploads. Override in subclasses for specific trackers.
        Return a dict where keys are form field names (e.g., 'cover', 'banner'),
        and values are tuples: (filename, path_to_file, mime_type).
        """
        return {}

    async def upload(self, meta: Dict[str, Any]) -> bool:
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        cat_id = await self.get_cat_id(meta)
        type_id = await self.get_type_id(meta)
        resolution_id = await self.get_res_id(meta)

        await descgen.edit_desc(meta, self.tracker)
        # region_id = await common.unit3d_region_ids(meta.get("region")) # TODO TEST THIS
        xanon = (
            1
            if (self.config["TRACKERS"][self.tracker].get("xanon", False) or self.config.get("ADULT", {}).get("always_anon", False))
            and meta.get("xxx")
            else 0
        )
        anon = (
            1
            if (meta.get("anon", 0) or self.config["TRACKERS"][self.tracker].get("anon", False))
            or self.config["DEFAULT"].get("always_anon", False)
            or xanon
            else 0
        )
        modq = 1 if meta.get("modq", False) or self.config["TRACKERS"][self.tracker].get("modq", False) else 0

        # Prepare file contents with context managers
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        if meta.get("bdinfo") is not None:
            mi_dump = None
            bd_dump = open(tmp_dir / "BD_SUMMARY_00.txt", "r", encoding="utf-8").read()
        else:
            mi_dump = open(tmp_dir / "MEDIAINFO.txt", "r", encoding="utf-8").read()
            bd_dump = None
        desc = open(tmp_dir / f"[{self.tracker}]DESCRIPTION.txt", "r", encoding="utf-8").read()
        torrent_path = tmp_dir / "BASE.torrent"
        nfo_file = meta.get("nfo_file")
        # cover = meta.get('album_cover', '')
        is_music = meta["is_music"]

        # Determine name: manual_name > edit_name (if method exists) > meta["name"]
        name = meta.get("manual_name")
        if not name and hasattr(self, "edit_name"):
            result = self.edit_name(meta)
            if inspect.isawaitable(result):
                name = await result
            else:
                name = result
        name = name or meta["name"]

        performers = meta.get("porndb", {}).get("performers", "") or ""
        tags = meta.get("porndb", {}).get("tags", "") or ""

        if performers and tags:
            adult_keywords = f"{performers}, {tags}"
        else:
            adult_keywords = performers or tags
        keywords = meta["keywords"]

        if meta["category"] in ["TV", "MOVIE"]:  # safety for now due to music/sports issue
            keywords = meta["keywords"].split(", ") if meta["keywords"] else []
            imdb_keywords = meta.get("imdb_info", {}).get("keywords", "").split(", ")
            keywords.extend(imdb_keywords)
            unique_keywords = list({kw.strip() for kw in keywords if kw.strip()})
            keywords = ", ".join(unique_keywords)

        # Build payload
        data = {
            "name": name,
            "description": desc,
            "mediainfo": mi_dump,
            "category_id": cat_id,
            "type_id": type_id,
            "tmdb": meta.get("tmdb", 0),
            "imdb": meta.get("imdb_id", 0),
            "tvdb": meta.get("tvdb_id", 0),
            "mal": meta.get("mal_id", 0),
            "igdb": 0,
            "anonymous": anon,
            "stream": meta["stream"],
            "sd": meta["sd"],
            "keywords": keywords or adult_keywords,
            "personal_release": int(meta.get("personalrelease", False)),
            "internal": 0,
            "featured": 0,
            "free": 0,
            "doubleup": 0,
            "sticky": 0,
            "mod_queue_opt_in": modq,
        }

        if not is_music:
            data.update({"resolution_id": resolution_id, "bdinfo": bd_dump})

        data.update(await self.get_extra_data(meta))

        # Internal
        if self.config["TRACKERS"][self.tracker].get("internal", False):
            if meta.get("tag", "") and meta["tag"][1:] in self.config["TRACKERS"][self.tracker].get("internal_groups", []):
                data["internal"] = "1"

        if meta.get("region_id", 0) != 0:
            data["region_id"] = meta["region_id"]
        if meta.get("distributor_id", 0) != 0:
            data["distributor_id"] = meta["distributor_id"]
        if meta.get("category") == "TV":
            data["season_number"] = int(meta.get("season_int", 0))
            data["episode_number"] = int(meta.get("episode_int", 0))

        headers = {"User-Agent": f"Uploadrr / v2.0 ({platform.system()} {platform.release()})"}
        params = {"api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip()}

        extra_files_meta = await self.get_extra_files(meta)

        if meta.get("debug", False):
            log.debug("[bright_magenta]DATA 2 SEND[/bright_magenta]:")
            console.print(Pretty(data))
            log.debug("[bright_magenta]FILES 2 SEND[/bright_magenta]:")
            files_info = {"torrent": torrent_path.name}
            if nfo_file:
                files_info["nfo"] = str(Path(nfo_file).name)
            for field, (fname, path, mime) in extra_files_meta.items():
                files_info[field] = str(Path(path).name)
            console.print(Pretty(files_info))
            await edit_torrent(meta, None, self.tracker, "DEBUG", "https://UNIT3D.CHANGES/INFOHASH/DEBUG-ONLY", "Uploadrr DEBUG-MODE")
            return "DEBUG"

        # Lo que ya hay con este nombre: si luego hay que buscar la subida, solo cuenta un torrent nuevo.
        previous = await self._links_named(meta, name)
        response, attempts = await self._post_upload(data, torrent_path, nfo_file, extra_files_meta, headers, params)

        raw_url = content = None
        # Un intento sin respuesta o con 5xx pudo entrar igualmente: lo busco antes de darlo por fallido. El
        # siguiente intento, en cambio, choca con el nombre ya usado. El tracker reescribe el .torrent y no da un
        # id de la subida, así que solo vale uno nuevo con este nombre, este tamaño y las mismas piezas.
        if response is None or response.status_code in RETRY_STATUSES or (attempts > 1 and "has already been taken" in response.text):
            size = extract_size_from_torrent(meta["base_dir"], meta["uuid"])
            current = await self._links_named(meta, name) if previous is not None and size else None
            new = sorted(link for link, found in current - previous if found == size) if current is not None else []
            for link in new:
                candidate, _ = await self._download_torrent(link, params, headers)
                if candidate is not None and _same_content(candidate, torrent_path):
                    raw_url, content = link, candidate
                    break
            if raw_url:
                console.print(f"[yellow]{self.tracker}: un intento anterior ya había subido el torrent")
            elif response is None:
                console.print(f"[red]Upload failed: {self.tracker} no ha respondido tras {attempts} intentos")
                return False

        if raw_url is None:
            if not 200 <= response.status_code < 300:
                self._print_http_error(response)
                return False
            try:
                response_json = response.json()
            except ValueError:
                response_json = None
            if not isinstance(response_json, dict):
                log.error(f"Unexpected response:\n{response.text}")
                return False
            if not response_json.get("success", False):
                message = response_json.get("message", "No message provided")
                console.print(f"[red]Upload failed: {message}[/red]")
                if "data" in response_json:
                    console.print(f"[cyan]Error details:[/cyan] {response_json['data']}")
                return False
            raw_url = response_json.get("data")
            if not isinstance(raw_url, str) or not raw_url:
                console.print("[yellow]Torrent upload succeeded, but failed to download the torrent file: no hay enlace[/yellow]")
                return True

        torrent_id = re.search(r"/(\d+)\.", raw_url)
        if torrent_id:
            # Línea fija para quien lanza esto: la ficha del torrent subido.
            print(f"Torrent URL: {self.domain}/torrents/{torrent_id.group(1)}", flush=True)
        # El tracker firma el .torrent: el que se le da al cliente es el que se descarga aquí,
        # con la misma autenticación que la subida (eMuwarez da 403 sin ella).
        if content is None:
            content, error = await self._download_torrent(raw_url, params, headers)
            if error:
                console.print(f"[yellow]Torrent upload succeeded, but failed to download the torrent file: {error}[/yellow]")
                return True
        try:
            new_torrent = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
            new_torrent.write_bytes(content)
            url = raw_url.replace("/download", "s")
            url = re.sub(r"/(\d+)\..*$", r"/\1", url)
            await edit_torrent(torrent_path=new_torrent, comment=f"Made by Uploadrr for: {url}")
        except Exception as e:
            console.print(f"[red]Torrent upload succeeded, but failed to save the torrent file: {e}[/red]")
            return True
        console.print("[bold green]Torrent uploaded successfully!")
        return True

    async def _post_upload(self, data, torrent_path, nfo_file, extra_files_meta, headers, params):
        """POST de la subida, repetido ante cortes y errores 5xx. Devuelve (respuesta o None, intentos)."""
        attempts = len(UPLOAD_RETRY_DELAYS) + 1
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, attempts + 1):
                with contextlib.ExitStack() as stack:
                    files = {"torrent": ("torrent.torrent", stack.enter_context(open(torrent_path, "rb")), "application/x-bittorrent")}
                    if nfo_file:
                        files["nfo"] = ("nfo.nfo", stack.enter_context(open(nfo_file, "rb")), "application/octet-stream")
                    for field, (fname, path, mime) in extra_files_meta.items():
                        files[field] = (fname, stack.enter_context(open(path, "rb")), mime)
                    try:
                        response = await client.post(self.upload_url, data=data, files=files, headers=headers, params=params)
                        error = f"HTTP {response.status_code}" if response.status_code in RETRY_STATUSES else None
                    except httpx.HTTPError as e:
                        response, error = None, type(e).__name__
                if error is None or attempt == attempts:
                    return response, attempt
                delay = UPLOAD_RETRY_DELAYS[attempt - 1]
                console.print(f"[yellow]{self.tracker}: {error} al subir, reintento en {delay}s ({attempt}/{attempts})")
                await asyncio.sleep(delay)

    async def _download_torrent(self, url, params, headers) -> Tuple[Optional[bytes], Optional[str]]:
        """Descarga el .torrent, reintentando mientras el tracker lo prepara. Devuelve (contenido, error)."""
        # La API key solo va al dominio del tracker, y sin seguir redirecciones.
        try:
            target, origin = httpx.URL(url), httpx.URL(self.upload_url)
        except httpx.InvalidURL as e:
            return None, type(e).__name__
        if (target.scheme, target.host, target.port) != (origin.scheme, origin.host, origin.port):
            return None, "el enlace apunta a otro dominio"
        attempts = len(DOWNLOAD_RETRY_DELAYS) + 1
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, attempts + 1):
                try:
                    response = await client.get(url, params=params, headers=headers)
                except (httpx.HTTPError, httpx.InvalidURL) as e:
                    error = type(e).__name__
                else:
                    if response.status_code != 200:
                        error = f"HTTP {response.status_code}"
                    elif not response.content.startswith(b"d"):
                        error = "la respuesta no es un .torrent"
                    else:
                        return response.content, None
                if attempt < attempts:
                    delay = DOWNLOAD_RETRY_DELAYS[attempt - 1]
                    console.print(f"[yellow]{self.tracker}: {error} al descargar el .torrent, reintento en {delay}s")
                    await asyncio.sleep(delay)
        return None, error

    async def _links_named(self, meta: Dict[str, Any], name: str) -> Optional[set]:
        """(enlace de descarga, tamaño) de los torrents llamados exactamente name, o None si no se puede buscar."""
        try:
            items = await self._search(meta, name=name)
        except DupeSearchError:
            return None
        found = ((attributes.get("download_link"), attributes["size"]) for attributes in items if attributes["name"] == name)
        return {(link, size) for link, size in found if isinstance(link, str) and link}

    def _print_http_error(self, response: httpx.Response) -> None:
        if response.status_code == 302:
            log.error("Redirect (302): la API key no parece válida")
        elif response.status_code == 403:
            log.error("Forbidden (403): la cuenta no tiene permiso de subida o Cloudflare ha bloqueado la petición")
        elif response.status_code in (404, 500):
            soup = bs4.BeautifulSoup(response.text, "html.parser")
            error_heading = soup.select_one("h1.error__heading")
            error_body = soup.select_one("div.error__body")

            if error_heading:
                log.error(error_heading.text.strip())
            if error_body:
                log.error(error_body.text.strip())
            if not error_heading and not error_body:
                console.print(f"[blue]Raw Response:[/blue] {response.text}")
        else:
            log.warning(f"[yellow]Unexpected Status Code ({response.status_code}):[/yellow]")
            console.print(response.text)

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dupes: Dict[str, Any] = {}
        if self.tracker in ("LCD", "LT", "TTR"):
            console.print(f"[yellow]Buscando torrents existentes en {self.tracker}...")
        else:
            console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        for attributes in await self._search(meta):
            dupes[attributes["name"]] = attributes["size"]
        return dupes

    async def _search(self, meta: Dict[str, Any], name: str = "") -> list:
        """Los atributos de todos los resultados de /api/torrents/filter, página a página.

        Si alguna página falla no se sabe si ya está subido: DupeSearchError, nunca "nada encontrado".
        """
        token = self.config["TRACKERS"][self.tracker]["api_key"].strip()
        resolution_id = await self.get_res_id(meta)
        params = {
            "api_token": token,
            "tmdbId": meta.get("tmdb", 0),
            "categories[]": await self.get_cat_id(meta),
            "types[]": await self.get_type_id(meta),
            # 1080p y 1080i compiten por el mismo hueco.
            "resolutions[]": ["3", "4"] if resolution_id in ("3", "4") else resolution_id,
            "name": name,
            "perPage": 100,
        }
        url = httpx.URL(self.search_url)
        origin = (url.scheme, url.host, url.port)
        items = []
        headers = {"User-Agent": f"Uploadrr / v2.0 ({platform.system()} {platform.release()})"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(MAX_SEARCH_PAGES):
                try:
                    response = await client.get(url, params=params, headers=headers)
                except httpx.HTTPError as e:
                    raise DupeSearchError(f"No se pudo buscar duplicados en {self.tracker}: {e!r}") from e
                if response.status_code != 200:
                    raise DupeSearchError(f"La búsqueda de duplicados en {self.tracker} devolvió HTTP {response.status_code}")
                try:
                    body = response.json()
                    if not isinstance(body["data"], list):
                        raise TypeError("data no es una lista")
                    for entry in body["data"]:
                        attributes = entry["attributes"]
                        # Sin nombre y tamaño no se puede comparar con lo que se va a subir.
                        if not isinstance(attributes["name"], str) or not isinstance(attributes["size"], (int, float)):
                            raise TypeError("resultado sin nombre o tamaño")
                        items.append(attributes)
                    # Solo null (o no venir) cierra la paginación; cualquier otro valor tiene que ser una URL.
                    links = body.get("links", {})
                    if links is not None and not isinstance(links, dict):
                        raise TypeError("links no es un objeto")
                    next_url = (links or {}).get("next")
                    if next_url is None:
                        return items
                    # El enlace de la página siguiente ya lleva los filtros (params lo vaciaría).
                    url = httpx.URL(next_url)
                except (ValueError, KeyError, TypeError, AttributeError, httpx.InvalidURL) as e:
                    raise DupeSearchError(f"La búsqueda de duplicados en {self.tracker} devolvió una respuesta inesperada") from e
                # La API key solo va a su propio dominio.
                if (url.scheme, url.host, url.port) != origin:
                    raise DupeSearchError(f"La búsqueda de duplicados en {self.tracker} pagina hacia otro dominio")
                if "api_token" not in url.params:
                    url = url.copy_add_param("api_token", token)
                params = None
        raise DupeSearchError(f"La búsqueda de duplicados en {self.tracker} tiene más de {MAX_SEARCH_PAGES} páginas")

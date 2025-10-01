import http.cookiejar
import pickle
from pathlib import Path
from typing import Callable, Optional

import aiofiles
import httpx

from src.utils.console import log


class CookieMonster:
    """Utility for handling cookie loading, saving, and validation across trackers.
    Always saves as 'pickle'. Automatically converts legacy Netscape (.txt) files to pickle (.pkl) if found.
    """

    def __init__(self, cookie_file: Path):
        """
        :param cookie_file: Path to the cookie file (must end with .pkl, e.g., data/cookies/{tracker}.pkl).
        """
        if cookie_file.suffix != ".pkl":
            raise ValueError("Cookie file must have a .pkl extension for pickle format.")
        self.cookie_file = cookie_file
        self.netscape_file = cookie_file.with_suffix(".txt")  # Legacy Netscape file path for auto-conversion

    async def load(self, client: httpx.AsyncClient) -> bool:
        """
        Load cookies from file and update the client's cookies.
        - Checks for .pkl first.
        - If .pkl missing and .txt (Netscape) exists, converts .txt to .pkl, deletes .txt, then loads .pkl.
        Returns True if loaded successfully, False if no file exists, conversion fails, or other error.
        """
        if self.cookie_file.exists():
            return await self._load_pickle(client)

        # Auto-convert legacy Netscape .txt if it exists
        if self.netscape_file.exists():
            try:
                await self._convert_netscape_to_pickle()
                if await self._load_pickle(client):
                    try:
                        self.netscape_file.unlink()
                        log.info(f"Deleted legacy Netscape file {self.netscape_file}")
                    except OSError as e:
                        log.warning(f"Failed to delete {self.netscape_file}: {e}")
                    return True
                else:
                    log.warning("Converted pickle file failed to load; retaining .txt")
                    return False
            except Exception as e:
                log.error(f"Failed to convert {self.netscape_file}: {e}")
                return False

        log.warning(f"No cookie file found (.pkl or legacy .txt): {self.cookie_file}")
        return False

    async def save(self, client: httpx.AsyncClient) -> bool:
        """Save the client's cookies to file in pickle format."""
        self._ensure_cookie_dir()
        try:
            cookie_dict = {}
            for cookie in client.cookies.jar:
                cookie_dict[cookie.name] = {
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "expires": cookie.expires,
                    "secure": cookie.secure,
                }
            async with aiofiles.open(self.cookie_file, "wb") as f:
                await f.write(pickle.dumps(cookie_dict))
            log.debug(f"Cookies saved to {self.cookie_file}")
            return True
        except Exception as e:
            log.error(f"Failed to save cookies to {self.cookie_file}: {e}")
            return False

    async def _load_pickle(self, client: httpx.AsyncClient) -> bool:
        try:
            async with aiofiles.open(self.cookie_file, "rb") as f:
                content = await f.read()

            if not content:
                log.error(f"Cookie file {self.cookie_file} is empty. Deleting file.")
                self.cookie_file.unlink(missing_ok=True)
                return False

            cookie_dict = pickle.loads(content)
            if not isinstance(cookie_dict, dict):
                log.error(f"Invalid pickle file {self.cookie_file}: Loaded object is not a dictionary. Deleting file.")
                self.cookie_file.unlink(missing_ok=True)
                return False

            # Reconstruct CookieJar
            jar = http.cookiejar.CookieJar()
            for name, attrs in cookie_dict.items():
                cookie = http.cookiejar.Cookie(
                    version=0,
                    name=name,
                    value=attrs["value"],
                    port=None,
                    port_specified=False,
                    domain=attrs["domain"],
                    domain_specified=bool(attrs["domain"]),
                    domain_initial_dot=attrs["domain"].startswith("."),
                    path=attrs["path"],
                    path_specified=bool(attrs["path"]),
                    secure=attrs["secure"],
                    expires=attrs["expires"],
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest={},
                    rfc2109=False,
                )
                jar.set_cookie(cookie)
            client.cookies.jar = jar
            log.info(f"Cookies loaded from {self.cookie_file}")
            return True

        except (OSError, pickle.UnpicklingError, TypeError) as e:
            log.error(f"Failed to load cookies from {self.cookie_file}: {e}")
            try:
                self.cookie_file.unlink(missing_ok=True)
            except Exception as e2:
                log.warning(f"Could not delete corrupt cookie file {self.cookie_file}: {e2}")
            return False

    async def validate(
        self,
        client: httpx.AsyncClient,
        validate_url: str,
        check_func: Callable[[httpx.Response], bool],
        extra_headers: Optional[dict] = None,
    ) -> bool:
        """
        Validate cookies by making a GET request to validate_url.
        :param client: The httpx.AsyncClient with loaded cookies.
        :param validate_url: URL to request for validation (e.g., index or upload page).
        :param check_func: Callable that takes httpx.Response and returns bool (valid/invalid).
                           Can have side effects (e.g., extract auth token).
        :param extra_headers: Optional dict of extra headers for the request.
        Returns True if valid, False otherwise.
        """
        headers = extra_headers or {}
        try:
            response = await client.get(validate_url, headers=headers)
            response.raise_for_status()
            is_valid = check_func(response)
            if is_valid:
                log.debug(f"Cookies validated successfully for {validate_url}")
            else:
                log.warning(f"Cookies invalid for {validate_url}")
            return is_valid
        except Exception as e:
            log.error(f"Validation request failed for {validate_url}: {e}")
            return False

    def _httpx_to_cookiejar(cookies: httpx.Cookies) -> http.cookiejar.CookieJar:
        jar = http.cookiejar.CookieJar()
        for cookie in cookies.jar:
            jar.set_cookie(cookie)
        return jar

    def _cookiejar_to_httpx(jar: http.cookiejar.CookieJar) -> httpx.Cookies:
        cookies = httpx.Cookies()
        for cookie in jar:
            cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        return cookies

    async def _convert_netscape_to_pickle(self) -> None:
        if not self.netscape_file.exists():
            raise FileNotFoundError(f"Legacy Netscape file {self.netscape_file} does not exist")

        jar = http.cookiejar.MozillaCookieJar(str(self.netscape_file))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception as e:
            raise ValueError(f"Invalid legacy Netscape cookie file {self.netscape_file}: {e}") from e

        self._ensure_cookie_dir()
        async with aiofiles.open(self.cookie_file, "wb") as f:
            await f.write(pickle.dumps(jar))

    def _ensure_cookie_dir(self) -> None:
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)

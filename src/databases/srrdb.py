import os
import urllib.parse
from typing import Tuple

import httpx

from src.utils.console import console, log


async def is_scene(video: str, imdb: str = None) -> Tuple[str, bool, str]:
    """Check if a video is a scene release using SRRDB."""
    scene = False
    base = urllib.parse.quote(os.path.splitext(os.path.basename(video))[0])
    urls = [f"https://api.srrdb.com/v1/search/r:{base}", f"https://api.srrdb.com/v1/imdb/{base}"]

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(urls[0], timeout=httpx.Timeout(30.0))
            response.raise_for_status()
            data = response.json()
            if int(data.get("resultsCount", 0)) > 0:
                video = f"{data['results'][0]['release']}.mkv"
                scene = True
                console.print(f"[green]SRRDB: Matched to {data['results'][0]['release']}")

                if imdb is None:
                    r = await client.get(urls[1])
                    r.raise_for_status()
                    imdb_data = r.json()
                    imdb = imdb_data["releases"][0].get("imdb", imdb) if imdb_data.get("releases") else imdb
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            console.print("[yellow]SRRDB: No match found or request timed out")
            log.debug(f"Exception occurred: {e}")
    return video, scene, imdb

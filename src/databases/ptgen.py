import json

import aiofiles
import httpx

from src.progress import ProgressManager
from src.utils.console import console


async def ptdesc(meta, ptgen_site="", ptgen_retry=3):
    ptgen = ""
    url = "https://ptgen.zhenzhen.workers.dev"
    if ptgen_site != "":
        url = ptgen_site
    params = {}
    data = {}

    pm = ProgressManager.get_instance()

    async with httpx.AsyncClient(timeout=60.0) as client:
        if int(meta.get("imdb_id", "0")) != 0:
            params = {"search": meta["imdb"], "source": "imdb"}
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                json_response = response.json()

                if json_response.get("success") and "data" in json_response and len(json_response["data"]) > 0:
                    params["url"] = json_response["data"][0]["link"]
                    console.print(f"Extracted Douban URL: {params['url']}")
                elif json_response.get("success") and "imdb_id" in json_response:
                    console.print("Using direct IMDB data from response")
                    meta["ptgen"] = json_response
                else:
                    console.print(f"API returned error: {json_response.get('error', 'Unknown error')}")

            except Exception as e:
                console.print(f"Error parsing response: {e}")

            if not params.get("url"):
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    params["url"] = response.json()["data"][0]["link"]
                    console.print(f"Extracted Douban URL: {params['url']}")
                except Exception as e:
                    console.print(f"Error extracting Douban URL: {e}")
                    console.print("[red]Unable to get data from ptgen using IMDb")
                    if meta.get("unattended", False):
                        return ""
                    if pm:
                        with pm.pause_for_interaction():
                            params["url"] = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")
                    else:
                        params["url"] = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")
        else:
            console.print("[red]No IMDb id was found.")
            if meta.get("unattended", False):
                return ""
            if pm:
                with pm.pause_for_interaction():
                    params["url"] = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")
            else:
                params["url"] = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")

        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            json_response = response.json()

            if json_response.get("error") is not None:
                for retry in range(ptgen_retry):
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    if response.json().get("error") is None:
                        break

            ptgen = response.json()
            meta["ptgen"] = ptgen
            async with aiofiles.open(f"{meta['base_dir']}/tmp/{meta['uuid']}/meta.json", "w") as f:
                await f.write(json.dumps(meta, indent=4))

            ptgen = ptgen["format"]
            if "[/img]" in ptgen:
                ptgen = ptgen.split("[/img]")[1]
            ptgen = f"[img]{meta.get('imdb_info', {}).get('cover', meta.get('cover', ''))}[/img]{ptgen}"
        except Exception:
            console.print_exception()
            console.print(response.text if "response" in locals() else "No response")
            console.print("[bold red]There was an error getting the ptgen \nUploading without ptgen")
            return ""

    return ptgen

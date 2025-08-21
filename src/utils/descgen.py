import json
import urllib.parse
from pathlib import Path
from typing import Dict

import aiofiles
import httpx
import requests

from src.progress import ProgressManager
from src.trackers.PTP import PTP

from .console import console


class DescGenerator:
    """
    Utility for generating descriptions based on metadata.
    Handles various sources like PTP, BLU, templates, NFO, links, and custom files.
    """

    def __init__(self, config: Dict, base_dir: Path):
        self.config = config
        self.base_dir = base_dir
        self.pm = ProgressManager.get_instance()

    async def gen_desc(self, meta: Dict) -> Dict:
        desclink = meta.get("desclink", None)
        auto_desc = meta.get("auto_desc", False) or self.config["AUTO"].get("always_aid", False)
        nfo_file = meta.get("nfo_file", None)
        description_path = self.base_dir / "tmp" / f"{meta['uuid']}" / "DESCRIPTION.txt"
        descfile_content = ""
        custom_desc_written = False

        if self.pm:
            self.pm.update("Generating description...")

        description_path.parent.mkdir(parents=True, exist_ok=True)

        with open(description_path, "w", newline="", encoding="utf-8") as description:
            if (desclink, meta.get("descfile", None), meta["desc"]) == (None, None, None):
                desc_source = []
                if meta.get("ptp_manual") is not None:
                    desc_source.append("PTP")
                if meta.get("blu_manual") is not None:
                    desc_source.append("BLU")
                desc_source = desc_source[0] if len(desc_source) == 1 else None

                # Try PTP description via API
                if (
                    meta.get("ptp")
                    and str(self.config["TRACKERS"].get("PTP", {}).get("useAPI")).lower() == "true"
                    and desc_source in ["PTP", None]
                ):
                    ptp = PTP(config=self.config)
                    ptp_desc = await ptp.get_ptp_description(meta["ptp"], meta["is_disc"])
                    if ptp_desc.strip():
                        description.write(ptp_desc)
                        description.write("\n")
                        meta["description"] = "PTP"
                        custom_desc_written = True

                # Try BLU description
                if not custom_desc_written and meta.get("blu_desc", "").strip() and desc_source in ["BLU", None]:
                    blu_desc = meta["blu_desc"].strip()
                    if blu_desc:
                        description.write(blu_desc)
                        meta["description"] = "BLU"
                        custom_desc_written = True

                # Try Jinja2 template
                if meta.get("desc_template"):
                    from jinja2 import Template

                    template_path = Path(meta["base_dir"]) / "data" / "templates" / f"{meta['desc_template']}.txt"
                    if template_path.is_file():
                        with open(template_path, "r", encoding="utf-8") as f:
                            desc_template = Template(f.read())
                            template_desc = desc_template.render(meta)
                            if template_desc.strip():
                                description.write(template_desc)
                                description.write("\n")
                                custom_desc_written = True

                # Add NFO file if requested
                if meta["nfo"] and nfo_file:
                    nfo_path = Path(nfo_file)
                    if nfo_path.is_file():
                        with open(nfo_path, "r", encoding="utf-8") as nfo_f:
                            nfo_content = nfo_f.read()
                        description.write("\n[code]")
                        description.write(nfo_content)
                        description.write("[/code]\n")
                        meta["description"] = "CUSTOM"
                        custom_desc_written = True

                if desclink:
                    parsed = urllib.parse.urlparse(desclink.replace("/raw/", "/"))
                    split_path = parsed.path.rstrip("/").split("/")
                    if len(split_path) > 1:
                        new_path = "/" + "/".join(split_path[:-1] + ["raw", split_path[-1]])
                    else:
                        new_path = "/raw" + parsed.path

                    raw_url = parsed._replace(path=new_path)
                    raw_str = urllib.parse.urlunparse(raw_url)
                    response = requests.get(raw_str)
                    response.encoding = "utf-8"
                    description.write(response.text)
                    description.write("\n")
                    meta["description"] = "CUSTOM"
                    custom_desc_written = True

                # Auto-desc from local file
                if auto_desc:
                    path = Path(meta.get("path", ""))
                    uuid = meta.get("uuid", "")
                    if uuid:
                        uuid = Path(uuid).stem
                    desc_folder = self.config["AUTO"].get("description_folder")
                    descfile = None

                    if desc_folder:
                        desc_folder = Path(desc_folder)
                        if desc_folder.is_dir():
                            potential_file = desc_folder / f"{uuid}.txt"
                            if potential_file.is_file():
                                descfile = potential_file

                    if descfile is None and path.exists():
                        if path.is_file():
                            descfile = path.with_suffix(".txt")
                            if not descfile.is_file():
                                descfile = path.parent / f"{uuid}.txt"
                        elif path.is_dir():
                            descfile = path / f"{uuid}.txt"
                            if not descfile.is_file():
                                descfile = path / "description.txt"

                    if descfile and descfile.is_file():
                        with open(descfile, "r", encoding="utf-8") as f:
                            descfile_content = f.read()
                        if not any(line.strip().startswith("# USE:") for line in descfile_content.splitlines()):
                            descfile_content = "# USE: full\n" + descfile_content
                        description.write("\n" + descfile_content + "\n")
                        meta["description"] = "CUSTOM"
                        custom_desc_written = True

                # Manual desc
                desc = meta.get("desc")
                if desc and desc != descfile_content:
                    description.write("\n" + desc + "\n")
                    meta["description"] = "CUSTOM"
                    custom_desc_written = True

                # External descfile
                descfile_path = meta.get("descfile")
                if descfile_path:
                    p = Path(descfile_path)
                    if p.is_file():
                        with open(p, "r", encoding="utf-8") as f:
                            descfile_text = f.read()
                        description.write("\n" + descfile_text + "\n")
                        meta["description"] = "CUSTOM"
                        custom_desc_written = True

                # Final fallback: write meta["desc"] if present
                if meta.get("desc"):
                    description.write("\n" + meta["desc"] + "\n")
                    meta["description"] = "CUSTOM"
                    custom_desc_written = True

        if self.pm:
            self.pm.update("Description generation complete")

        return meta

    async def ptgen(self, meta: Dict, ptgen_site="", ptgen_retry=3):
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

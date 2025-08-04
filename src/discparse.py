import asyncio
import json
import os
import shutil
import sys
import traceback
import xml.etree.ElementTree as ET
from collections import OrderedDict
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pymediainfo import MediaInfo

from src.exceptions import BDInfoExtractionError
from src.progress import ProgressManager
from src.utils.console import console, log


class DiscParse:
    def __init__(self):
        pass

    async def get_bdinfo(self, discs: List[Dict], folder_id: str, base_dir: str, meta_discs: List[Dict]) -> Tuple[List[Dict], Dict]:
        save_dir = Path(base_dir) / "tmp" / folder_id

        for i, disc in enumerate(discs):
            path = Path(disc["path"]).resolve()
            summary_file = save_dir / f"BD_SUMMARY_{str(i).zfill(2)}.txt"
            full_file = save_dir / f"BD_FULL_{str(i).zfill(2)}.txt"
            ext_file = save_dir / "BD_SUMMARY_EXT.txt"

            bdinfo_text = None
            title_from_xml = None

            # Check existing summary file
            if summary_file.exists() and summary_file.stat().st_size > 0:
                bdinfo_text = summary_file
                summary_content = await asyncio.to_thread(summary_file.read_text, encoding="utf-8")
                if summary_content.startswith("Title: "):
                    title_from_xml = summary_content.splitlines()[0].replace("Title: ", "").strip()
                    log.debug(f"Using existing title from {summary_file}: {title_from_xml}")
            else:
                # Get title from XML if no valid summary
                title_from_xml = await self._get_title_from_xml(path)
                log.debug(f"Extracted title from XML: {title_from_xml}")

            # Use full file if no summary or meta_discs is empty
            if not bdinfo_text and (not meta_discs and full_file.exists() and full_file.stat().st_size > 0):
                bdinfo_text = full_file

            # Process BDInfo if no valid existing data
            if not bdinfo_text:
                bdinfo_text = await self._process_bdinfo(path, save_dir, base_dir)

            if bdinfo_text:
                try:
                    bd_summary, ext_bd_summary, files_section = await self._extract_bdinfo_sections(bdinfo_text)

                    async with asyncio.Lock():
                        if not summary_file.exists() or summary_file.stat().st_size == 0:
                            summary_content = f"Title: {title_from_xml or 'Unknown'}\n{bd_summary.strip()}"
                            await asyncio.to_thread(summary_file.write_text, summary_content, encoding="utf-8")
                            log.debug(f"Wrote new {summary_file} with title: {title_from_xml}")
                        if not ext_file.exists() or ext_file.stat().st_size == 0:
                            await asyncio.to_thread(ext_file.write_text, ext_bd_summary.strip(), encoding="utf-8")

                    bdinfo = await self.parse_bdinfo(bd_summary, files_section, path)
                    if bdinfo.get("title") is None or not bdinfo.get("title"):
                        bdinfo["title"] = title_from_xml
                    elif title_from_xml and bdinfo.get("title") != title_from_xml:
                        log.debug(f"BDInfo title '{bdinfo['title']}' overridden by XML title '{title_from_xml}'")
                        bdinfo["title"] = title_from_xml

                    disc["summary"] = bd_summary.strip()
                    disc["bdinfo"] = bdinfo

                    if bdinfo_text != full_file and bdinfo_text != summary_file:
                        await asyncio.to_thread(shutil.copyfile, bdinfo_text, full_file)
                        if bdinfo_text.exists():
                            bdinfo_text.unlink()
                            log.debug(f"Removed temporary file: {bdinfo_text}")

                except Exception:
                    console.print(f"Error processing BDInfo: {traceback.format_exc()}")
                    await asyncio.sleep(5)
                    continue
        if not discs or "bdinfo" not in discs[0]:
            raise BDInfoExtractionError(f"No BDInfo extracted for folder: {folder_id}")

        return discs, discs[0]["bdinfo"]

    async def _process_bdinfo(self, path: Path, save_dir: Path, base_dir: str) -> Optional[Path]:
        """Process BDInfo execution and return output file path."""
        if sys.platform.startswith("darwin"):
            await self.remove_system_files(path)
        await self.run_bdinfo(path, save_dir, base_dir)

        for file in await asyncio.to_thread(list, save_dir.iterdir()):
            if file.name.startswith("BDINFO"):
                return file
        return None

    async def _extract_bdinfo_sections(self, bdinfo_text: Path) -> Tuple[str, str, str]:
        """Extract relevant sections from BDInfo text asynchronously with fallback."""
        text = await asyncio.to_thread(bdinfo_text.read_text, encoding="utf-8")

        files_section = ""
        quick_summary = ""
        ext_bd_summary = ""

        if "QUICK SUMMARY:" in text:
            main_sections = text.split("QUICK SUMMARY:", 2)
            if "FILES:" in main_sections[0]:
                files_parts = main_sections[0].split("FILES:", 2)
                if len(files_parts) > 1 and "CHAPTERS:" in files_parts[1]:
                    files_section = files_parts[1].split("CHAPTERS:", 2)[0].split("-------------")[-1]
                elif len(files_parts) > 1:
                    files_section = files_parts[1]
            if len(main_sections) > 1:
                quick_summary = main_sections[1].split("********************", 1)[0].rstrip()
        else:
            quick_summary = text.split("********************", 1)[0].rstrip()

        if "[code]" in text:
            ext_sections = text.split("[code]", 3)
            if len(ext_sections) > 2 and "FILES:" in ext_sections[2]:
                ext_bd_summary = ext_sections[2].split("FILES:", 1)[0].rstrip()
            elif len(ext_sections) > 2:
                ext_bd_summary = ext_sections[2].rstrip()

        # Fallback if sections are empty
        if not files_section:
            files_section = "No files section found"
        if not quick_summary:
            quick_summary = "No quick summary found"
        if not ext_bd_summary:
            ext_bd_summary = "No extended summary found"

        return quick_summary, ext_bd_summary, files_section

    async def remove_system_files(self, path: Path) -> None:
        """Removes macOS indexing (spotlight) files starting with '._'"""

        async def remove_file(file_path: Path):
            try:
                file_path.unlink()
                log.debug(f"Removed system file: {file_path}")
            except Exception as e:
                log.error(f"Error removing {file_path}: {e}")

        tasks = []
        for root, dirs, files in await asyncio.to_thread(path.walk):
            for filename in files:
                if filename.startswith("._"):
                    file_path = Path(root) / filename
                    tasks.append(remove_file(file_path))

        if tasks:
            await asyncio.gather(*tasks)

    async def run_bdinfo(self, path: Path, save_dir: Path, base_dir: str) -> None:
        """Run BDInfo executable based on platform."""
        bdinfo_path = Path(base_dir) / "bin" / "BDInfo" / "BDInfo.exe"
        args = ["-w", str(path), str(save_dir)]
        pm = ProgressManager.get_instance()

        if sys.platform.startswith(("linux", "darwin")):
            cmd = ["mono", str(bdinfo_path), *args]
        else:
            cmd = [str(bdinfo_path), *args]

        try:
            with pm.pause_for_interaction():
                proc = await asyncio.create_subprocess_exec(*cmd)
                await proc.wait()
            pm.update(f"BDInfo scan complete: {path.name}")

        except Exception as e:
            pm.update(f"[Error] BDInfo failed: {e}")
            log.error(f"BDInfo execution failed: {e}")

    async def _get_title_from_xml(self, path: Path) -> Optional[str]:
        """Extract title from bdmt_*.xml file if available."""
        xml_files = [f for f in (path / "META" / "DL").glob("bdmt_*.xml")]
        if not xml_files:
            log.debug(f"No bdmt_*.xml file found in {path / 'META' / 'DL'}")
            return ""

        try:
            xml_content = await asyncio.to_thread(Path(xml_files[0]).read_text, encoding="utf-8")
            root = ET.fromstring(xml_content)
            namespace = {"di": "urn:BDA:bdmv;discinfo"}
            title_elem = root.find(".//di:title/di:name", namespace)
            return title_elem.text if title_elem is not None else ""
        except (ET.ParseError, IndexError) as e:
            log.error(f"Error parsing XML for title: {e}")
            return ""

    async def parse_bdinfo(self, bdinfo_input: str, files: str, path: Path) -> Dict:
        """Parse BDInfo data into structured dictionary."""
        bdinfo = {
            "video": [],
            "audio": [],
            "subtitles": [],
            "path": str(path),
            "title": None,  # Default to None
        }

        for line in bdinfo_input.splitlines():
            line_lower = line.strip().lower()
            if line_lower.startswith("*"):
                line_lower = line_lower[1:].strip()

            if line_lower.startswith("playlist:"):
                bdinfo["playlist"] = line.split(":", 1)[1].split(".", 1)[0].strip()
            elif line_lower.startswith("disc size:"):
                size = float(line.split(":", 1)[1].split("bytes", 1)[0].replace(",", "")) / (1 << 30)
                bdinfo["size"] = size
            elif line_lower.startswith("length:"):
                bdinfo["length"] = line.split(":", 1)[1].split(".", 1)[0].strip()
            elif line_lower.startswith("video:"):
                video_data = line.split(":", 1)[1].split("/", 12)
                video_data.extend([""] * (9 - len(video_data)))
                n = 1 if "Eye" in video_data[2].strip() else 0
                bdinfo["video"].append({
                    "codec": video_data[0].strip(),
                    "bitrate": video_data[1].strip(),
                    "res": video_data[n + 2].strip(),
                    "fps": video_data[n + 3].strip(),
                    "aspect_ratio": video_data[n + 4].strip(),
                    "profile": video_data[n + 5].strip(),
                    "bit_depth": video_data[n + 6].strip(),
                    "hdr_dv": video_data[n + 7].strip(),
                    "color": video_data[n + 8].strip(),
                    "3d": video_data[2].strip() if n else "",
                })
            elif line_lower.startswith("audio:"):
                audio_line = line.split("(")[0].strip()
                audio_data = audio_line.split(":", 1)[1].split("/")
                n = 1 if "Atmos" in audio_data[2].strip() else 0
                bdinfo["audio"].append({
                    "language": audio_data[0].strip(),
                    "codec": audio_data[1].strip(),
                    "channels": audio_data[n + 2].strip(),
                    "sample_rate": audio_data[n + 3].strip(),
                    "bitrate": audio_data[n + 4].strip(),
                    "bit_depth": audio_data[n + 5].strip() if n + 5 < len(audio_data) else "",
                    "atmos_why_you_be_like_this": audio_data[2].strip() if n else "",
                })
            elif line_lower.startswith("disc title:"):
                bdinfo["title"] = line.split(":", 1)[1].strip()
            elif line_lower.startswith("disc label:"):
                bdinfo["label"] = line.split(":", 1)[1].strip()
            elif line_lower.startswith("subtitle:"):
                bdinfo["subtitles"].append(line.split(":", 1)[1].split("/")[0].strip())

        bdinfo["files"] = []
        for line in files.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                filename, _, duration = parts[0], parts[1], parts[2]
                if all(c.isdigit() or c == ":" for c in duration) and duration.count(":") >= 1:
                    bdinfo["files"].append({"file": filename, "length": duration})

        return bdinfo

    """
    Parse VIDEO_TS and get mediainfos
    """

    async def get_dvdinfo(self, discs):
        for each in discs:
            path = each.get("path")
            os.chdir(path)
            files = glob("VTS_*.VOB")
            files.sort()
            # Switch to ordered dictionary
            filesdict = OrderedDict()
            main_set = []
            # Use ordered dictionary in place of list of lists
            for file in files:
                trimmed = file[4:]
                if trimmed[:2] not in filesdict:
                    filesdict[trimmed[:2]] = []
                filesdict[trimmed[:2]].append(trimmed)
            main_set_duration = 0
            for vob_set in filesdict.values():
                # Parse media info for this VOB set
                vob_set_mi = MediaInfo.parse(f"VTS_{vob_set[0][:2]}_0.IFO", output="JSON")
                vob_set_mi = json.loads(vob_set_mi)
                vob_set_duration = vob_set_mi["media"]["track"][1]["Duration"]

                # If the duration of the new vob set > main set by more than 10% then it's our new main set
                # This should make it so TV shows pick the first episode
                if (float(vob_set_duration) * 1.00) > (float(main_set_duration) * 1.10) or len(main_set) < 1:
                    main_set = vob_set
                    main_set_duration = vob_set_duration
            each["main_set"] = main_set
            set = main_set[0][:2]
            each["vob"] = vob = f"{path}/VTS_{set}_1.VOB"
            each["ifo"] = ifo = f"{path}/VTS_{set}_0.IFO"
            each["vob_mi"] = MediaInfo.parse(
                os.path.basename(vob), output="STRING", full=False, mediainfo_options={"inform_version": "1"}
            ).replace("\r\n", "\n")
            each["ifo_mi"] = MediaInfo.parse(
                os.path.basename(ifo), output="STRING", full=False, mediainfo_options={"inform_version": "1"}
            ).replace("\r\n", "\n")
            each["vob_mi_full"] = MediaInfo.parse(vob, output="STRING", full=False, mediainfo_options={"inform_version": "1"}).replace(
                "\r\n", "\n"
            )
            each["ifo_mi_full"] = MediaInfo.parse(ifo, output="STRING", full=False, mediainfo_options={"inform_version": "1"}).replace(
                "\r\n", "\n"
            )

            size = sum(os.path.getsize(f) for f in os.listdir(".") if os.path.isfile(f)) / float(1 << 30)
            if size <= 7.95:
                dvd_size = "DVD9"
                if size <= 4.37:
                    dvd_size = "DVD5"
            each["size"] = dvd_size
        return discs

    async def get_hddvd_info(self, discs):
        for each in discs:
            path = each.get("path")
            os.chdir(path)
            files = glob("*.EVO")
            size = 0
            largest = files[0]
            # get largest file from files
            for file in files:
                file_size = os.path.getsize(file)
                if file_size > size:
                    largest = file
                    size = file_size
            each["evo_mi"] = MediaInfo.parse(
                os.path.basename(largest), output="STRING", full=False, mediainfo_options={"inform_version": "1"}
            )
            each["largest_evo"] = os.path.abspath(f"{path}/{largest}")
        return discs

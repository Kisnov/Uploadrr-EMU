from pathlib import Path
from typing import Dict, List, Set
from urllib.parse import urlparse

from src.utils.console import log

from .generator import ScreenshotGenerator
from .up2host import ScreenshotUploader


class RehostManager:
    def __init__(self, config, base_dir, uploader: ScreenshotUploader):
        self.config = config
        self.base_dir = base_dir
        self.uploader = uploader

    async def ensure_compliance(self, meta: Dict, tracker_whitelists: Dict[str, List[str]]) -> Dict:
        if meta.get("skip_imghost_upload", False) or not tracker_whitelists:
            log.debug("Skipping rehosting: upload skipped or no tracker whitelists")
            return meta

        meta["rehost_list"] = meta.get("rehost_list", {})
        base_images = meta.get("image_list", {"main": [], "episodes": {}})
        screens_count = meta.get("screens", 3)
        per_episode = self.config["SCREENS"].get("per_episode", 3)

        for tracker, whitelist in tracker_whitelists.items():
            if not whitelist:
                log.debug(f"Skipping tracker {tracker}: empty whitelist")
                continue
            whitelist_hosts = set(whitelist)
            compliant_main = self._get_compliant_images(base_images["main"], meta["rehost_list"], whitelist_hosts, screens_count)
            if len(compliant_main) < screens_count:
                log.debug(f"Need to rehost {screens_count - len(compliant_main)} main images for tracker {tracker}")
                viable_hosts = self._get_viable_hosts(self.config, meta, {tracker: whitelist})
                # Filter out hosts already in rehost_list with sufficient images
                viable_hosts = [
                    h
                    for h in viable_hosts
                    if h not in meta["rehost_list"] or len(meta["rehost_list"].get(h, {}).get("main", [])) < screens_count
                ]
                if viable_hosts:
                    target_host = viable_hosts[0]
                    log.debug(f"Rehosting to {target_host} for tracker {tracker}")
                    rehosted = await self._rehost_images(
                        meta, base_images["main"], compliant_main, screens_count - len(compliant_main), target_host
                    )
                    meta["rehost_list"].setdefault(target_host, {"main": [], "episodes": {}})
                    meta["rehost_list"][target_host]["main"] = rehosted[-screens_count:]  # Replace to avoid duplicates
                    log.debug(f"Stored {len(rehosted)} rehosted main images in rehost_list[{target_host}]")
                else:
                    log.warning(
                        f"No viable hosts available for tracker {tracker} to rehost {screens_count - len(compliant_main)} main images"
                    )
            else:
                log.debug(f"Skipping rehost for tracker {tracker}: {len(compliant_main)} compliant main images found")

            if meta.get("tv_pack") == 1:
                for ep_name, ep_images in base_images.get("episodes", {}).items():
                    compliant_ep = self._get_compliant_images(ep_images, meta["rehost_list"], whitelist_hosts, per_episode)
                    if len(compliant_ep) < per_episode:
                        log.debug(
                            f"Need to rehost {per_episode - len(compliant_ep)} images for episode {ep_name} and tracker {tracker}"
                        )
                        viable_hosts = self._get_viable_hosts(self.config, meta, {tracker: whitelist})
                        viable_hosts = [
                            h
                            for h in viable_hosts
                            if h not in meta["rehost_list"]
                            or len(meta["rehost_list"].get(h, {}).get("episodes", {}).get(ep_name, [])) < per_episode
                        ]
                        if viable_hosts:
                            target_host = viable_hosts[0]
                            log.debug(f"Rehosting episode {ep_name} to {target_host} for tracker {tracker}")
                            rehosted = await self._rehost_images(
                                meta, ep_images, compliant_ep, per_episode - len(compliant_ep), target_host
                            )
                            meta["rehost_list"].setdefault(target_host, {"main": [], "episodes": {}})
                            meta["rehost_list"][target_host]["episodes"].setdefault(ep_name, [])[:] = rehosted[-per_episode:]
                            log.debug(f"Stored {len(rehosted)} rehosted images for episode {ep_name} in rehost_list[{target_host}]")
                        else:
                            log.warning(
                                f"No viable hosts available for tracker {tracker} to rehost {per_episode - len(compliant_ep)} images for episode {ep_name}"
                            )
                    else:
                        log.debug(
                            f"Skipping rehost for episode {ep_name} and tracker {tracker}: {len(compliant_ep)} compliant images found"
                        )
        return meta

    def _get_compliant_images(self, images: List, rehost_images: Dict[str, Dict], whitelist_hosts: Set[str], count: int) -> List:
        compliant = []

        # Check image_list images
        for img in images:
            if not all(img.get(k) for k in ("img_url", "raw_url", "web_url")):
                log.debug(f"Skipping image_list image due to missing keys: {img}")
                continue
            host = self._extract_host(img.get("img_url", ""), whitelist_hosts)
            if host in whitelist_hosts:
                compliant.append(img)
            if len(compliant) >= count:
                break

        # Check rehost_list images for each whitelisted host
        for host in whitelist_hosts:
            rehost_main = rehost_images.get(host, {}).get("main", [])
            for img in rehost_main:
                if not all(img.get(k) for k in ("img_url", "raw_url", "web_url")):
                    log.debug(f"Skipping rehost_list[{host}] image due to missing keys: {img}")
                    continue
                img_host = self._extract_host(img.get("img_url", ""), {host})
                if img_host == host:
                    compliant.append(img)
                else:
                    log.debug(f"Image in rehost_list[{host}] does not match host {host}: {img.get('img_url', '')}")
                if len(compliant) >= count:
                    break

        log.debug(
            f"Found {len(compliant)} compliant images out of {len(images)} image_list and {sum(len(r.get('main', [])) for r in rehost_images.values())} rehost_list images for whitelist: {whitelist_hosts}"
        )
        return compliant[:count]

    async def _rehost_images(self, meta, base_images, compliant, shortfall, target_host):
        tmp_dir = self.base_dir / "tmp" / meta["uuid"]
        to_rehost = [img for img in base_images if img not in compliant and Path(tmp_dir / img["local_file"]).exists()][:shortfall]
        log.debug(f"Selected {len(to_rehost)} images to rehost to {target_host}: {[img['local_file'] for img in to_rehost]}")
        if len(to_rehost) < shortfall:
            log.debug(f"Generating {shortfall - len(to_rehost)} additional screenshots to meet shortfall")
            generator = ScreenshotGenerator(self.config, self.base_dir)
            new_screens = await generator.generate_screenshots(meta, meta["filename"], meta["uuid"])
            to_rehost.extend(new_screens["main"][: shortfall - len(to_rehost)])
            log.debug(f"After generation, {len(to_rehost)} images to rehost: {[img['local_file'] for img in to_rehost]}")
        rehosted = await self.uploader.upload_screenshots(meta, {"main": to_rehost}, [target_host], meta.get("tracker_whitelists", {}))
        log.debug(f"Rehosted {len(rehosted['main'])} images to {target_host}: {[img.get('img_url', '') for img in rehosted['main']]}")
        return rehosted["main"]

    @staticmethod
    def _extract_host(url: str, whitelist: Set[str]) -> str:
        if not whitelist:
            return urlparse(url).hostname or ""
        hostname = urlparse(url).hostname or ""
        for wl in whitelist:
            if hostname == wl or hostname.endswith("." + wl) or hostname == wl.split(".")[0]:
                return wl
        return ""

    @staticmethod
    def _get_viable_hosts(config, meta: Dict, tracker_whitelists: Dict[str, List[str]]) -> List[str]:
        all_whitelists = [set(hosts) for hosts in tracker_whitelists.values() if hosts]
        merged_whitelist = set.intersection(*all_whitelists) if all_whitelists else None  # None means "allow all"

        preferred = []
        if meta.get("imghost"):
            preferred.append(meta["imghost"])
        preferred.extend(config["SCREENS"].get("img_hosts", []))
        preferred.append("imgbox")

        if merged_whitelist is not None:
            filtered = [host for host in preferred if host in merged_whitelist]
            if filtered:
                return filtered
            return list(merged_whitelist)  # fallback to whitelist order if nothing matched
        else:
            return preferred  # unrestricted

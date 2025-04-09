import gc
import os
import sys
import subprocess
import psutil
from typing import Dict, List, Set
from urllib.parse import urlparse

from .generator import ScreenshotGenerator
from .up2host import ScreenshotUploader
from src.console import log

class RehostManager:
    def __init__(self, config, base_dir, uploader: ScreenshotUploader):
        self.config = config
        self.base_dir = base_dir
        self.uploader = uploader

    async def ensure_compliance(self, meta: Dict, tracker_whitelists: Dict[str, List[str]],
                                            base_images: List, screen_count: int) -> Dict:
        """Ensure images comply with tracker whitelists using image_list and rehost_list."""
    
        if meta.get('skip_imghost_upload', False):
            log.debug("Skipping rehosting due to skip_imghost_upload")
            return meta

        meta['rehost_list'] = meta.get('rehost_list', {})

        for tracker, whitelist in tracker_whitelists.items():
            whitelist_hosts = set(whitelist) if whitelist else set()
            all_images = base_images + sum(meta['rehost_list'].values(), [])
            compliant_images = self._get_compliant_images(all_images, whitelist_hosts, screen_count)

            shortfall = screen_count - len(compliant_images)
            if shortfall > 0 and whitelist_hosts:
                log.debug(f"Tracker {tracker} needs {shortfall} more compliant images")
                viable_hosts = self._get_viable_hosts(meta, {tracker: whitelist})
                if not viable_hosts:
                    log.error(f"No viable hosts for {tracker}")
                    continue
                target_host = viable_hosts[0]
                rehosted = await self._rehost_images(
                    meta, base_images, compliant_images, shortfall, whitelist, tracker, target_host
                )
                meta['rehost_list'].setdefault(target_host, []).extend(rehosted)
                meta['rehost_list'][target_host] = meta['rehost_list'][target_host][-screen_count:]

        return meta

    def _get_compliant_images(self, images: List, whitelist_hosts: Set[str], screen_count: int) -> List:
        """Filter images that comply with the whitelist, matching subdomains."""
        compliant = []
        for img in images:
            if len(compliant) >= screen_count:
                break
            img_url = img['img_url'] if isinstance(img, dict) else img
            if not whitelist_hosts or self._extract_host(img_url, whitelist_hosts):
                compliant.append(img)
        return compliant

    async def _rehost_images(self, meta: Dict, base_images: List, compliant_images: List,
                    shortfall: int, whitelist: List[str], tracker: str,target_host: str) -> List:
        """Rehost only needed screens"""
        to_rehost = [img['local_file'] for img in base_images if img not in compliant_images][:shortfall]
        
        if len(to_rehost) < shortfall:
            log.debug(f"Generating {shortfall - len(to_rehost)} additional screenshots for {tracker}")
            generator = ScreenshotGenerator(self.config, self.base_dir)
            new_screenshots = await generator.generate_screenshots(
                meta, meta['filename'], meta['uuid'], count=shortfall - len(to_rehost)
            )
            to_rehost.extend(new_screenshots)

        if to_rehost:
            viable_hosts = [target_host]
            log.debug(f"Rehosting {len(to_rehost)} images to {target_host} for {tracker}")
            return await self.uploader.upload_screenshots(
                meta, screens=to_rehost, viable_hosts=viable_hosts, rehost=True
            )
        return []

    @staticmethod
    def _extract_host(url: str, whitelist: Set[str] = None) -> str:
        """Extract host, matching subdomains or base name with whitelist."""
        hostname = urlparse(url).hostname or url
        if not hostname:
            return ""
        base_name = hostname.split('.')[0]
        if not whitelist:
            return base_name

        for wl in whitelist:
            if '.' in wl:  # TLD included
                if hostname == wl or hostname.endswith('.' + wl):
                    return wl.split('.')[0]  # Return module name
            else:  # No TLD
                if f"{wl}." in hostname:  # Check for "hostname."
                    return wl
        return ""

    @staticmethod
    def _get_viable_hosts(meta: Dict, tracker_whitelists: Dict[str, List[str]]) -> List[str]:
        """Determine viable hosts from whitelists, returning module names."""
        all_hosts = [meta.get('imghost')] + [
            meta.get('config', {}).get('SCREENS', {}).get(f'img_host_{i}')
            for i in range(1, 10) if f'img_host_{i}' in meta.get('config', {}).get('SCREENS', {})
        ] + ['imgbox']
        all_hosts = [h for h in all_hosts if h]

        if not tracker_whitelists:
            return [h.split('.')[0] if '.' in h else h for h in all_hosts]

        whitelist_sets = [set(wl) for wl in tracker_whitelists.values() if wl]
        if not whitelist_sets:
            return [h.split('.')[0] if '.' in h else h for h in all_hosts]
        common_hosts = set.intersection(*whitelist_sets) if len(whitelist_sets) > 1 else whitelist_sets[0]

        viable = []
        for host in all_hosts:
            hostname = host
            base_name = host.split('.')[0] if '.' in host else host
            if hostname in common_hosts or any(hostname.endswith('.' + wl) for wl in common_hosts) or \
               any(f"{wl}." in hostname for wl in common_hosts if '.' not in wl):
                viable.append(base_name)
        return viable if viable else [wl.split('.')[0] for wl in common_hosts]

class ScreenshotManager:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.generator = ScreenshotGenerator(config, base_dir)
        self.uploader = ScreenshotUploader(config, base_dir)
        self.rehoster = RehostManager(config, base_dir, self.uploader)

    async def handle_screenshots(self, meta: Dict, tracker_whitelists: Dict[str, List[str]] = None, task_widget=None):
        """Handle screenshot generation and initial upload."""
        try:
            existing = meta.get('image_list', [])

            if not meta.get('skip_screenshots', False) and not meta.get('is_music', False):
                screenshots = await self.generator.generate_screenshots(meta, meta['filename'], meta['uuid'], task_widget=task_widget)
            else:
                screenshots = existing

            if not meta.get('skip_imghost_upload', False) and screenshots:
                viable_hosts = RehostManager._get_viable_hosts(meta, tracker_whitelists)
                if not viable_hosts:
                    log.error("No viable hosts for initial upload")
                    meta['image_list'] = existing or screenshots
                else:
                    if meta.get('is_music', False) and screenshots == []:
                        if meta.get("user_cover") and not meta.get("album_cover"):
                            screenshots.append(meta.get("user_cover"))
                        if meta.get("back_cover") and not meta.ger("album_back"):
                            screenshots.append(meta.get("back_cover"))
                        if meta.get("user_proof"):
                            screenshots.append(meta.get("user_proof"))  
                    log.debug(f"Initial upload to {viable_hosts[0]}")
                    uploaded = await self.uploader.upload_screenshots(
                        meta, screens=screenshots, viable_hosts=viable_hosts , task_widget=task_widget
                    )
                    meta['image_list'] = uploaded if uploaded else (existing or screenshots)
            else:
                meta['image_list'] = existing
            return meta

        finally:
            await self._cleanup()
            self._reset_terminal()

    async def ensure_compliance(self, meta: Dict, tracker_whitelists: Dict[str, List[str]]) -> Dict:
        """Ensure image compliance with tracker whitelists."""
        screen_count = meta.get('screens', 3)
        return await self.rehoster.ensure_compliance(
            meta, tracker_whitelists, meta.get('image_list', []), screen_count
        )

    @staticmethod
    def _extract_host(url: str, whitelist: Set[str]) -> str:
        """Extract host with whitelist matching, returning base module name."""
        hostname = urlparse(url).hostname or url
        base_name = hostname.split('.')[0] if '.' in hostname else hostname
        for wl in whitelist:
            if hostname == wl or hostname.endswith('.' + wl):
                return base_name
        return ""
        
    async def _cleanup(self):
        """Clean up child processes and resources."""
        current_process = psutil.Process()
        children = current_process.children(recursive=True)
        for child in children:
            if 'ffmpeg' in child.name().lower() or 'oxipng' in child.name().lower():
                child.terminate()
        psutil.wait_procs(children, timeout=3)
        gc.collect()


    def _reset_terminal(self):
        if os.name == "posix":
            try:
                if not sys.stderr.closed:
                    sys.stderr.flush()
                if hasattr(sys.stdin, "isatty") and sys.stdin.isatty() and not sys.stdin.closed:
                    subprocess.run(["stty", "sane"], check=False)
                    try:
                        import termios
                        termios.tcflush(sys.stdin.fileno(), termios.TCIOFLUSH)
                    except (ImportError, OSError):
                        pass
                if not sys.stdout.closed:
                    sys.stdout.write("\033[0m")
                    sys.stdout.flush()
                    sys.stdout.write("\033[?25h")
                    sys.stdout.flush()
                os.system("jobs -p | xargs -r kill 2>/dev/null")
            except Exception as e:
                log.debug(f"Error during terminal reset: {e}")

        elif os.name == "nt":
            try:
                import ctypes

                ctypes.windll.kernel32.SetConsoleTextAttribute(
                    ctypes.windll.kernel32.GetStdHandle(-11), 7
                )

                sys.stdout.write("\033[?25h")
                sys.stdout.flush()

                if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
                    try:
                        import msvcrt
                        while msvcrt.kbhit():
                            msvcrt.getch()
                    except ImportError:
                        pass

                subprocess.run("taskkill /F /IM cmd.exe /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            except Exception as e:
                log.debug(f"Error during terminal reset: {e}")
import asyncio
from typing import Dict, Optional

from src.progress import ProgressManager
from src.trackers.AITHER import AITHER
from src.trackers.BLU import BLU
from src.trackers.HDB import HDB
from src.trackers.PTP import PTP
from src.utils.console import log


class MetaScraper:
    """
    Handles integration with tracker APIs (PTP, BLU, HDB) to fetch existing release data.
    Reusable across media types (video, music); queries for vetted metadata to avoid redundant local matching.
    Async for concurrent API calls; integrates ProgressManager for UI.
    """

    def __init__(self, config: Dict, pm: ProgressManager = None):
        self.config = config
        self.pm = pm

    async def integrate(self, meta: Dict) -> Dict:
        """Query trackers for existing releases and enrich meta with vetted data."""
        if self.pm:
            self.pm.update("Integrating tracker APIs...")

        tasks = []
        if meta.get("ptp") and self.config["TRACKERS"].get("PTP", {}).get("useAPI", False):
            tasks.append(self._fetch_ptp(meta))
        if meta.get("blu"):
            tasks.append(self._fetch_blu(meta))
        if meta.get("hdb"):
            tasks.append(self._fetch_hdb(meta))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    log.error(f"Tracker API error: {result}")
                elif result:  # Assuming fetch methods return enriched dict or None
                    meta.update(result)

        # Post-integration: If data fetched, override local guesses (e.g., title, year) with vetted info
        if meta.get("ptp_data"):
            self._apply_vetted_data(meta, "ptp_data")
        elif meta.get("blu_data"):
            self._apply_vetted_data(meta, "blu_data")
        elif meta.get("hdb_data"):
            self._apply_vetted_data(meta, "hdb_data")
        elif meta.get("ath_data"):
            self._apply_vetted_data(meta, "ath_data")

        if self.pm:
            self.pm.update("Tracker integration complete")

        return meta

    async def _fetch_ptp(self, meta: Dict) -> Optional[Dict]:
        """Fetch and process PTP data asynchronously."""
        try:
            ptp = PTP(config=self.config)
            ptp_data = await ptp.get_ptp_description(meta["ptp"], meta.get("is_disc", False))  # Adjust to actual API method
            # Process/enrich: e.g., extract title, year, etc.
            return {"ptp_data": ptp_data} if ptp_data else None
        except Exception as e:
            log.error(f"PTP fetch failed: {e}")
            return None

    async def _fetch_blu(self, meta: Dict) -> Optional[Dict]:
        """Fetch and process BLU data asynchronously."""
        try:
            blu = BLU(config=self.config)
            blu_data = await blu.some_api_call(meta["blu"])  # Adjust to actual (e.g., get description or metadata)
            return {"blu_data": blu_data, "blu_desc": blu_data.get("description")} if blu_data else None
        except Exception as e:
            log.error(f"BLU fetch failed: {e}")
            return None

    async def _fetch_hdb(self, meta: Dict) -> Optional[Dict]:
        """Fetch and process HDB data asynchronously."""
        try:
            hdb = HDB(config=self.config)
            hdb_data = await hdb.some_api_call(meta["hdb"])  # Adjust to actual
            return {"hdb_data": hdb_data} if hdb_data else None
        except Exception as e:
            log.error(f"HDB fetch failed: {e}")
            return None

    async def _fetch_ath(self, meta: Dict) -> Optional[Dict]:
        """Fetch and process ATH data asynchronously."""
        try:
            ath = AITHER(config=self.config)
            ath_data = await ath.some_api_call(meta["AITHER"])
            return {"ath_data": ath_data, "ath_desc": ath_data.get("description")} if ath_data else None
        except Exception as e:
            log.error(f"ATHIER fetch failed: {e}")
            return None

    def _apply_vetted_data(self, meta: Dict, source_key: str) -> None:
        """Helper: Override local meta with vetted tracker data (e.g., title, year)."""
        vetted = meta.get(source_key, {})
        if vetted:
            # Example overrides: Prioritize vetted info
            meta["title"] = vetted.get("title", meta["title"])
            meta["year"] = vetted.get("year", meta["year"])
            # Add more (e.g., edition, description) as needed
            log.debug(f"Applied vetted data from {source_key}")

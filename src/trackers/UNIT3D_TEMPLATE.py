from typing import Any, Dict, Tuple

from src.trackers.UNIT3D import Unit3d


class SpecificTracker(Unit3d):
    tracker = "Abbreviated"
    source_flag = "Source_Flag_AKA_Stamp"
    domain = "https://domain.tld"
    allowed_imghosts = []  # ex: ["ibb.co", "ptscreens.com"] leave empty if no restrictions
    banned_groups = [""]  # Supports Dict with types (See: Wiki or BLU.py for examples)

    async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]:
        """
        Check if content is banned.
        Returns (True, True) for immediate ban, (True, False) for warning, (False, False) for no ban.
        """
        # TODO: Implement specific banned content checks based on tracker rules
        return False, False

    # Example override: If this tracker needs custom upload logic
    # async def upload(self, meta: Dict[str, Any]) -> bool:
    #     # Custom pre-upload step
    #     print(f"Custom logic for {self.tracker}")
    #     return await super().upload(meta)  # Reuse base, or replace entirely

import json
from pathlib import Path
from typing import Dict, Optional

class DistributorMapper:
    _distributors: Dict[str, int] = None

    @classmethod
    def _load_distributors(cls) -> Dict[str, int]:
        """Loads distributors with UNIt3D ID mappings from JSON and caches them."""
        if cls._distributors is None:
            json_path = Path(__file__).parent.parent / "data" / "distributors.json"
            with json_path.open("r", encoding="utf-8") as f:
                cls._distributors = json.load(f)
        return cls._distributors

    @staticmethod
    def get_distributor(distributor: Optional[str]) -> tuple[str, int]:
        """
        Validates distributor name, returning its name and ID.

        Args:
            distributor: The distributor name to validate (case-insensitive).

        Returns:
            A tuple of (DISTRUBTOR NAME, ID) if valid, else ("", 0).
        """
        if not distributor or distributor in ["None", ""]:
            return ("", 0)
        distributor_upper = distributor.strip().upper()
        distributors = DistributorMapper._load_distributors()
        return (distributor_upper, distributors.get(distributor_upper, 0))
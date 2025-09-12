import os
import platform
import sys
from pathlib import Path

import yaml

from src.utils.console import console, log


def get_backup_name(path: str, suffix: str = "_bu") -> str:
    """Generate a unique backup filename."""
    base, ext = os.path.splitext(path)
    counter = 1
    new_path = f"{base}{suffix}{counter}{ext}"
    while os.path.exists(new_path):
        counter += 1
        new_path = f"{base}{suffix}{counter}{ext}"
    return new_path


def load_yaml_config(path: Path) -> dict:
    """Load YAML config from file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}
        return config_data
    except Exception as e:
        log.error(f"[bold red]Error loading config.yaml: {e}[/bold red]")
        sys.exit(1)


def check_python_version() -> None:
    """Ensure Python version compatibility."""
    pyver = platform.python_version_tuple()
    if int(pyver[0]) != 3 or int(pyver[1]) <= 8:
        console.print("[bold red]Legacy Python Detected. Please use Python 3.9+[/bold red]")
        sys.exit(1)

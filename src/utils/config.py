import os
import platform
import shutil
import subprocess
import sys
import time
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


def reconfigure(config_path: Path, legacy_config: Path, Legacy: bool = False) -> None:
    """Handle reconfiguration of outdated config."""
    data_dir = config_path.parent
    old_config_path = data_dir / "backup" / "old_config.py"

    if Legacy:
        log.warning("Legacy `config.py` detected, reconfiguring.")
        current_config = legacy_config
    else:
        log.warning("Config out of date, automatic upgrade in progress")
        current_config = config_path

    try:
        if old_config_path.exists():
            backup_base = str(old_config_path)
            backup_name = os.path.basename(get_backup_name(backup_base))
            backup_path = data_dir / "backup" / backup_name
            shutil.move(str(old_config_path), backup_path)
            log.info(f"Backed up {old_config_path} to {backup_path}")

        if current_config.exists():
            shutil.move(str(current_config), str(old_config_path))
            log.info(f"Moved {current_config} to {old_config_path}")
        else:
            log.warning(f"{current_config} not found, assuming fresh reconfiguration")

        result = subprocess.run(
            [sys.executable, str(data_dir / "reconfig.py"), "--output-dir", str(data_dir)], capture_output=True, text=True, check=False
        )

        log.debug(f"reconfig.py stdout: {result.stdout}")
        log.debug(f"reconfig.py stderr: {result.stderr}")

        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)

        if not config_path.exists():
            log.error(f"Config file {config_path} not found after reconfiguration")
            raise FileNotFoundError(f"Config not generated at {config_path}")

        if not Legacy:
            console.print("[bold green]Congratulations! Your config was successfully updated.[/bold green]")
        else:
            console.print("[bold green]Welcome to Uploadrr v2[/bold green]", justify="center")
            console.print("Your config is now yaml. Please read docs or reach out for any questions.")

    except (subprocess.CalledProcessError, Exception) as e:
        log.error(f"Unable to proceed with automatic upgrade: {e}")
        console.print(f"[bold red]ERROR[/bold red]: Automatic upgrade failed - {e}")
        console.print("Please manually update config.yaml and run `python3 data/reconfig.py --output-dir data`")
        sys.exit(1)

    console.print("Please double check the config after reconfiguration, there could be errors.")
    with console.status("[bold cyan]Counting down...[/bold cyan]") as status:
        for i in range(4, 0, -1):
            status.update(f"[bold cyan]Continuing in {i} seconds...[/bold cyan]")
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                console.print("[bold red]Interrupted by user. Exiting.[/bold red]")
                sys.exit(0)
    console.print("Attempting to use as-is.. [dim]Good Luck, Chuck.")


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

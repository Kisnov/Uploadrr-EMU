import yaml
import copy
import argparse
from rich.console import Console
from rich.prompt import Prompt
from packaging import version
from pathlib import Path

console = Console()

# Header for output files
HEADER = """
##---------THE LAST DIGITAL UNDERGROUND PRESENTS-------##
##                                                     ##
##                 Special Recruitment :)              ##
##          @ https://TheLDU.to/application            ##
##                                                     ##
##                              Ref: Uploadrr by CvT   ##
##-----------------------------------------------------##
"""

# Define keys for each section
DEFAULT_KEYS = {'tmdb_api', 'add_trailer', 'default_torrent_client', 'sfx_on_prompt'}
SCREENS_KEYS = {
    'imgbb_api', 'ptpimg_api', 'lensdump_api', 'ptscreens_api', 'oeimg_api',
    'img_host_1', 'img_host_2', 'img_host_3', 'img_host_4', 'img_host_5', 'img_host_6', 'img_host_7',
    'screens', 'img_size', 'optimize_images'
}
SIGNATURE_KEYS = {'global_sig', 'global_anon_sig', 'global_pr_sig', 'global_anon_pr_sig'}
TRACKER_SIGNATURE_KEYS = {'signature', 'anon_signature', 'pr_signature', 'anon_pr_signature'}

def read_python_config(file_path):
    """Read a Python config file and return the config dictionary."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
            exec(content, globals())
        return globals()['config']
    except SyntaxError:
        # If exec fails, it might be YAML misnamed as .py
        console.print(f"[bold yellow]WARN[/bold yellow]: {file_path} appears to be YAML, not Python. Treating as YAML.")
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return yaml.safe_load(file) or {}
        except Exception as e:
            console.print(f"[bold red]ERROR[/bold red]: Failed to parse {file_path} as YAML - {e}")
            raise
    except Exception as e:
        console.print(f"[bold red]ERROR[/bold red]: Failed to read {file_path} - {e}")
        raise

def read_yaml_config(file_path):
    """Read a YAML config file and return the dictionary."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file) or {}
    except Exception:
        return {'version': '0'}

def format_value(value, is_signature=False):
    """Format a value for YAML output, handling signatures specially."""
    if is_signature and isinstance(value, str):
        # If the string contains a newline, assume it was meant to start with \n
        if '\n' in value:
            cleaned_value = value.replace('\n', '').strip()
            return f'"\\n{cleaned_value}"'
        # Otherwise, preserve the string as-is (could already have \n if raw/escaped)
        return f'"{value.replace('"', '\\"')}"'
    elif isinstance(value, str):
        return f'"{value.replace('"', '\\"')}"'
    elif isinstance(value, bool):
        return str(value).lower()
    elif value is None:
        return 'null'
    else:
        return str(value)

def write_yaml_config(file_path, config_dict):
    """Write the config dictionary to a YAML file with controlled spacing."""
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(HEADER.strip() + "\n\n")
        
        # Write version first
        file.write(f"version: '{config_dict['version']}'\n\n")
        
        # Write other top-level sections
        for section in ['DEFAULT', 'SCREENS', 'SIGNATURES', 'AUTO', 'TRACKERS', 'TORRENT_CLIENTS']:
            if section in config_dict and config_dict[section]:
                file.write(f"{section}:\n")
                content = config_dict[section]
                
                if section == 'TRACKERS':
                    # Handle default_trackers
                    if 'default_trackers' in content:
                        file.write(f"  default_trackers: {format_value(content['default_trackers'])}\n")
                    
                    # Handle tracker sub-blocks
                    for tracker, settings in content.items():
                        if tracker == 'default_trackers':
                            continue
                        file.write(f"  {tracker}:\n")
                        for key, value in settings.items():
                            is_sig = key in TRACKER_SIGNATURE_KEYS
                            file.write(f"    {key}: {format_value(value, is_signature=is_sig)}\n")
                        file.write("\n")  # Blank line between tracker sub-blocks
                else:
                    # Handle non-tracker sections
                    for key, value in content.items():
                        is_sig = section == 'SIGNATURES' and key in SIGNATURE_KEYS
                        file.write(f"  {key}: {format_value(value, is_signature=is_sig)}\n")
                file.write("\n")  # Blank line between top-level sections

def transform_config(old_config):
    """Transform an old Python config to the new YAML structure."""
    new_config = {'version': '2.0.0'}
    
    # Split DEFAULT into DEFAULT, SCREENS, and SIGNATURES
    if 'DEFAULT' in old_config:
        old_default = old_config['DEFAULT']
        new_config['DEFAULT'] = {k: v for k, v in old_default.items() if k in DEFAULT_KEYS}
        new_config['SCREENS'] = {k: v for k, v in old_default.items() if k in SCREENS_KEYS}
        new_config['SIGNATURES'] = {k: v for k, v in old_default.items() if k in SIGNATURE_KEYS.union({'use_global_sigs'})}
    else:
        new_config['DEFAULT'] = {}
        new_config['SCREENS'] = {}
        new_config['SIGNATURES'] = {}

    # Copy AUTO section as-is
    if 'AUTO' in old_config:
        new_config['AUTO'] = old_config['AUTO']

    # Transform TRACKERS section
    if 'TRACKERS' in old_config:
        trackers = old_config['TRACKERS']
        default_trackers = trackers.pop('default_trackers', None)
        new_config['TRACKERS'] = {}
        for tracker, settings in trackers.items():
            new_tracker_settings = {}
            if 'announce_url' in settings:
                pid = settings['announce_url'].split('announce/')[-1]
                new_tracker_settings['PID'] = pid
            for key, value in settings.items():
                if key != 'announce_url':
                    new_tracker_settings[key] = value
            new_config['TRACKERS'][tracker] = new_tracker_settings
        if default_trackers:
            new_config['TRACKERS']['default_trackers'] = default_trackers

    # Copy TORRENT_CLIENTS as-is
    if 'TORRENT_CLIENTS' in old_config:
        new_config['TORRENT_CLIENTS'] = old_config['TORRENT_CLIENTS']

    return new_config

def merge_configs(base_config, user_config):
    """Merge user config with base config, preserving user values and using base version."""
    merged = copy.deepcopy(base_config)
    for section in user_config:
        if section in merged and section != 'version':  # Skip version, use base_config's version
            if isinstance(user_config[section], dict) and isinstance(merged[section], dict):
                merged[section].update(user_config[section])
            else:
                merged[section] = user_config[section]
        elif section != 'version':
            merged[section] = user_config[section]
    return merged

def reconfigure(output_dir):
    """Main reconfiguration function."""
    data_dir = Path(output_dir)
    backup_dir = data_dir / "backup"
    base_config_path = backup_dir / "example_config.yaml"  # Use YAML base config
    old_config_path = backup_dir / "old_config.py"        # User’s old config
    new_config_path = data_dir / "config.yaml"

    if not base_config_path.exists():
        console.print(f"[bold red]ERROR[/bold red]: {base_config_path} not found.")
        return 1
    if not old_config_path.exists():
        console.print(f"[bold red]ERROR[/bold red]: {old_config_path} not found.")
        return 1

    # Load base config (always YAML)
    try:
        with open(base_config_path, 'r', encoding='utf-8') as file:
            base_config = yaml.safe_load(file) or {}
        if 'version' not in base_config:
            console.print(f"[bold red]ERROR[/bold red]: {base_config_path} lacks a version field.")
            return 1
    except Exception as e:
        console.print(f"[bold red]ERROR[/bold red]: Failed to load {base_config_path} - {e}")
        return 1

    # Load old config (could be Python or YAML)
    old_config = read_python_config(old_config_path)
    is_yaml = not isinstance(old_config, dict) or 'version' in old_config and ':' in open(old_config_path, 'r', encoding='utf-8').readline()

    if is_yaml:
        new_config = merge_configs(base_config, old_config)
    else:
        new_config = transform_config(old_config)
        new_config['version'] = base_config['version']

    if new_config_path.exists():
        current_config = read_yaml_config(new_config_path)
        current_version = current_config.get('version', '0')
        if version.parse(current_version) >= version.parse(base_config['version']):
            console.print(f"[bold yellow]WARN[/bold yellow]: Current config version ({current_version}) is already >= base version ({base_config['version']}).")
            overwrite = Prompt.ask("Overwrite existing config?", choices=["y", "N"], default="N")
            if overwrite.lower() != 'y':
                return 0
        elif version.parse(current_version) > version.parse(base_config.get('version', '0')):
            console.print(f"[bold red]WARN[/bold red]: Current config has a higher version ({current_version}) than base ({base_config['version']}).")
            overwrite = Prompt.ask("Continue (may downgrade)?", choices=["y", "N"], default="N")
            if overwrite.lower() != 'y':
                return 0
        else:
            overwrite = Prompt.ask(f"[bold yellow]WARN[/bold yellow]: config.yaml exists (version {current_version}). Overwrite with version {base_config['version']}?", choices=["y", "N"], default="N")
            if overwrite.lower() != 'y':
                return 0

    try:
        write_yaml_config(new_config_path, new_config)
        console.print(f"[bold green]Success![/bold green] config.yaml was updated to version {base_config['version']}.")
        console.print("[bold yellow]Note[/bold yellow]: Verify your client settings in the new config.")
        return 0
    except Exception as e:
        console.print(f"[bold red]ERROR[/bold red]: Failed to write config - {e}")
        return 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Reconfigure the config file to YAML.')
    parser.add_argument('--output-dir', type=str, required=True, help='Directory for the new config.yaml.')
    args = parser.parse_args()
    exit_code = reconfigure(args.output_dir)
    exit(exit_code)
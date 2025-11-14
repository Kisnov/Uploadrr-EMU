>[!WARNING]  
> Branches recently changed where `v2` is the default and now `main`  
> The old default `master` branch is now `legacy`  
> **To update your local repo:**
> ```bash  
>git checkout v2
>git branch -m main
>git branch -u origin/main main
>```
> To keep using legacy do the same just swap `v2` -> `master` and `main` -> `legacy`


# Uploadrr

Uploadrr is a tool for preping and uploading torrents for Movies, TV Shows, Music & Adult Content. It automates metadata gathering, screenshot generation, torrent creation, and uploading to various private trackers.

This project is a fork of [Upload-Assistant](https://github.com/L4GSP1KE/Upload-Assistant) by L4G, with the goal of giving users more control over the upload process.

## Installation

### Requirements

- **Python 3.9+** (for Python 3.7/8 try [legacy branch](https://codeberg.org/CvT/Uploadrr/src/branch/legacy))
- **FFmpeg** (video processing and screenshots)
- **Mediainfo** (metadata)
- **[Mono](https://www.mono-project.com/)** is **required** on Linux or MacOS for BDInfo. (Not needed if you never plan on doing Full BluRay Discs)
### Clone + Install Modules

1. Clone the repository:
   ```bash
   git clone https://codeberg.org/CvT/Uploadrr.git
   cd Uploadrr
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Uploadrr uses a YAML configuration file located at `data/config.yaml`. Copy the example configuration and customize it for your needs:

```bash
cp data/backup/example_config.yaml data/config.yaml
```

### Key Configuration Sections

>[!IMPORTANT]  
> For Movies/TV/Anime you need a TMDb API (v3) key from **[TMDb](https://developer.themoviedb.org/docs/getting-started)**
> For Adult Content you should have a TPDb key from **[TPDb](https://theporndb.net/user/api-tokens)**

- **DEFAULT**: Basic settings like TMDb API key, default torrent client, and anonymity preferences
- **ADULT**: This is your adult config, TPDb key, seperate screen api keys if desired, adult anon override, contactsheet settings etc.
- **SCREENS**: Screenshot generation settings, including API keys for image hosting services
- **SIGNATURES**: Custom signatures for uploads (global and per-tracker)
- **AUTO**: Automation settings like delay between uploads and duplicate detection
- **TRACKERS**: Configuration for supported private trackers (API keys, usernames, etc.)
- **TORRENT_CLIENTS**: Settings for torrent clients (qBittorrent, Deluge, etc.)

>[!TIP] 
>See the __[Full Config Guide](https://codeberg.org/CvT/Uploadrr/wiki/Full-Config-Guide)__ for detailed options and supported trackers.

## Usage

Run Uploadrr from the project directory:

```bash
python upload.py [options] <path_to_media>
```

### Command Line Options

- `--help`: Show available options
- `--debug`: Enable debug logging
- `--unattended`: Skip confirmation prompts
- `--screens <number>`: Number of screenshots to generate
- `--trackers <list>`: list of trackers to upload to

### Examples

Upload a movie to default trackers:
```bash
python upload.py /path/to/movie.mkv
```

Upload TV show with custom screenshots:
```bash
python upload.py --screens 8 --trackers BLU HDB /path/to/tv_show/
```

Upload some music:
```bash
python upload.py /path/to/album
```

>[!NOTE]
> For adult content one of `--xxx`, `--xscene` , `--xmovie`, `-jav` or `--pack` is necessary.  
> For best results be specific.

Upload some adult scene:
```bash
python upload.py --xxx /path/to/adultscene.mp4
```

Upload adult movie split scene:
```bash
python upload.py -xm /path/to/adult_movie2000
```


## Features

- Automatic metadata gathering from TMDb/IMDb or MusizBrainz/Discogs or TPDb
- Screenshot generation with customizable settings (tonempaping, frame stats, contact sheets etc)
- Torrent creation with multiple client support
- Support for numerous private trackers
- Batch processing capabilities
- Duplicate detection and prevention
- Customizable signatures and description handling

## Contributing

Contributions are welcome! Please feel free to submit issues, feature requests, or pull requests.

## Acknowledgments

All credit for the original codebase goes to [L4G](https://github.com/L4GSP1KE) and contributors to the [Upload-Assistant](https://github.com/L4GSP1KE/Upload-Assistant) project.

Many hands make light work - thank you to everyone who has contributed!
Specific shoutouts to @LustWorks (Adult-Support),  @Lokilicious (RHD Support), and @evaporate (Misc Fixes)
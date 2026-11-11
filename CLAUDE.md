# Uploadrr-EMU

Standalone repo derived from Uploadrr (via codeberg.org/ccx94/Uploadrr-EMU), used only to upload to eMuwarez, a
UNIT3D tracker (key `EMU`, https://emu-warez.com). Its one caller is emuwarez-uploadrr, which clones `main`
into its Docker image at `/opt/Uploadrr` and runs it unattended:

`python upload.py PATH -tk EMU -mt NAME -tmdb ID -ua -c movie -res RES` (series: `-c tv`, plus `-season SXX -episode EXX`)

with stdin closed and `HOME=/` when the container has no user.

Only the EMU path matters. The other trackers (BLU, ACM, AITHER, PTP…) are inherited code: don't fix, review
or report their errors unless they break an eMuwarez upload.

## Workflow

- Sole maintainer: Gabriel (Kisnov). Main repo `Kisnov/Uploadrr-EMU`, base `main`. Branch and PR, grouped
  (no PR per one-line fix), non-draft so CodeRabbit reviews. Conventional Commits with a Spanish summary.
- Merged changes reach the app only when its image is rebuilt.
- Not in a GitHub fork network: nothing syncs from upstream. Pull a change from Codeberg only on purpose,
  by hand, and credit it in the commit.

## Contract with emuwarez-uploadrr (change both repos together)

- Exit code: 0 only if something was uploaded. Duplicates, tracker rejections, TMDb misses and missing video
  exit 1. Any unhandled exception exits 1 too.
- Fail closed: a dupe search that cannot run raises `DupeSearchError`. Never turn a failed request into
  "nothing found".
- After a successful upload it prints `Torrent URL: https://emu-warez.com/torrents/<id>` with plain `print`.
  Rich wraps at 80 columns on a pipe, so machine-read lines must not go through `console.print`.
- The app parses output lines verbatim (`uploadrr_progress.py`, `upload_result_parser.py` in the app). Don't
  reword `Searching for existing torrents`, `No dupes found`, `Uploading to EMU`,
  `Torrent uploaded successfully`, `Adding and rechecking torrent`, `Added to:`,
  `INCORRECT QBIT LOGIN CREDENTIALS`, `Torrent upload succeeded, but failed to`, `Hashing with <tool>: X%`,
  `Uploading screenshots:`, `Incomplete season pack:`, `Step done:` without updating the app.
- `--resume` (`-rs`, per item, `meta["resume"]`): without it, before each queue item `tmp/<uuid>` (`uuid` = name of
  the uploaded folder/file) is wiped entirely (`limpiar_tmp_item` in `src/utils/meta.py`; only a real directory
  inside `tmp`, never followed if it is a symlink; a failure is logged and does not abort). With it, what is marked
  as done is reused. `resume` is in `overwrite_keys` of `load_existing_meta`, so a saved `resume: true` is not inherited.
- `--aviso-baja-calidad` (`-avbc`, per item, `meta["aviso_baja_calidad"]`, in `overwrite_keys`): the app passes it
  when the video is below 720p, and `edit_desc` puts `AVISO_BAJA_CALIDAD` (`src/utils/descgen.py`) at the top of
  `[EMU]DESCRIPTION.txt`, also with `# USE: as-is`. The app keeps the threshold; this repo keeps the text.
- `--solo-cliente ID` (`-sc`, `meta["solo_cliente"]`, an int, in `overwrite_keys`): the app adds it (plus `--resume`) to
  the usual argv when the torrent is already on eMuwarez but not in qBittorrent. After prep (and the TMDb check) the item
  skips screenshots, dupe search, confirmation, hashing and upload; it implies `resume` (no wipe). It reads
  `attributes.download_link` from `GET /api/torrents/ID` (with or without the `data` wrapper), saves
  `[EMU]<clean_name>.torrent` like an upload, checks its file names and sizes against `PATH` (hardlinks into the library:
  a mismatch deletes it and exits 1) and calls `add_to_client`; qBittorrent rechecks only in this mode
  (`is_skip_checking=False`). Exit 0 only if `add_to_client` returned True (only qBittorrent reports it); any failure
  prints a reason and exits 1. `Torrent URL:` and `Torrent upload succeeded, but failed to` are never printed here.
- Done steps: `meta["pasos_hechos"]` (list in `meta.json`) holds `screenshots` and `torrent`. Each is marked and
  saved first, then printed with plain `print(..., flush=True)` as `Step done: screenshots` / `Step done: torrent`
  (also when the step is reused in a `--resume`; nothing is printed for a step that did not run, e.g. a skipped item).
  `screenshots` is marked only if no main screenshot is left without a URL (or none were needed); `torrent` when
  `BASE.torrent` exists and decodes, or with `nohash`. `BASE.torrent` is reused only if `torrent` is marked and it
  decodes; otherwise it is deleted and rebuilt. `Torrent URL:` stays the only mark of "uploaded to the tracker".
- The `.torrent` is hashed with mkbrr when it is on `PATH` (the app image installs a pinned one), torf otherwise.
  The mkbrr result must list exactly the files torf would pick, or it is rebuilt with torf. When mkbrr warns of
  an incomplete season pack, `print` writes `Incomplete season pack: season N, missing episodes A, B`.
- `config.yaml` keys the app stages before each run and restores afterwards: `DEFAULT.tmdb`,
  `DEFAULT.add_logo`, `TRACKERS.EMU.api_key`, `TRACKERS.BLU.useAPI` (forced to `false`), `SCREENS.img_hosts`,
  `SCREENS.API_KEYS`, `SCREENS.per_episode` (`0`, only the first episode of a pack, unless the app is set to screenshot
  every episode), `TORRENT_CLIENTS.qbit.*`. When `torrent_storage_dir` doesn't exist (it resolves against this
  repo's cwd), the app drops it from `TORRENT_CLIENTS.qbit` and sets `enable_search: False`.

## Verify

- `requirements.txt` pins exact versions of the direct dependencies, bumped only by Dependabot PRs. Transitive ones
  stay free on purpose: the app installs this file into the same environment as its own FastAPI stack.
  `pip install -r requirements.txt` on `python:3.12-slim` needs nothing else; running `upload.py` also needs
  `ffmpeg` and `mediainfo` (the `Dockerfile` installs them).
- Tests: `pip install pytest pytest-cov`, then `python -m pytest tests -q -p no:cacheprovider` (in Docker; ~10 s).
  `tests/conftest.py` blocks network, DNS and subprocesses; known bugs are `xfail`. `tests/test_unit3d.py`
  runs the dupe search and the upload against a fake UNIT3D tracker on localhost. Anything else: focused scripts in
  Docker (`upload.py` on a test folder, exit code).
- The redactor rewrites config secrets in all Rich output.
- CI (`.github/workflows/ci.yml`) runs critical ruff rules (`E9,F63,F7,F82`), `compileall` and pytest with coverage on
  every PR, and uploads it to Codecov (informational only, `codecov.yml`).
  Style is left to CodeRabbit on changed lines.
- `zizmor.yml` audits the workflows and `dependabot.yml` when `.github/` changes; run `zizmor --gh-token "$(gh auth token)" .` locally (without a token it skips online audits) before pushing a workflow edit.

## Secrets and auth

- The tracker API key travels as the `api_token` query param. Never print `params`, full request URLs or
  `http_err.request.url`. Plain `print` and raw `logging` handlers skip the redactor: keep them for
  machine-read lines that hold no secrets.
- Re-downloading the signed `.torrent` after upload (`src/trackers/UNIT3D.py`) must send the same `params`
  and `User-Agent` as the upload POST, or eMuwarez answers 403 and `[EMU]<name>.torrent` is never written.
- `data/config.yaml` and `data/config.yaml.bak` are not tracked: this repo's defaults live in
  `data/backup/example_config.yaml`. `data/tags.json` is tracked although `.gitignore` lists it: placeholders only.

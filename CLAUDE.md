# Uploadrr-EMU

Fork of Uploadrr (public upstream: codeberg.org/ccx94/Uploadrr-EMU) used only to upload to eMuwarez, a
UNIT3D tracker (key `EMU`, https://emu-warez.com). Its one caller is emuwarez-uploadrr, which clones `main`
into its Docker image at `/opt/Uploadrr` and runs it unattended:

`python upload.py <path> -tk EMU -mt <name> -tmdb <id> -ua -c movie|tv -res <res> [-season Sxx -episode Exx]`

with stdin closed and `HOME=/` when the container has no user.

Only the EMU path matters. The other trackers (BLU, ACM, AITHER, PTP…) are inherited code: don't fix, review
or report their errors unless they break an eMuwarez upload.

## Workflow

- Sole maintainer: Gabriel (Kisnov). Main repo `Kisnov/Uploadrr-EMU`, base `main`. Branch and PR, grouped
  (no PR per one-line fix), non-draft so CodeRabbit reviews. Conventional Commits with a Spanish summary.
- Merged changes reach the app only when its image is rebuilt.

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
  `INCORRECT QBIT LOGIN CREDENTIALS`, `Torrent upload succeeded, but failed to`, `Hashing with torf`,
  `Uploading screenshots:` without updating the app.
- `config.yaml` keys the app stages before each run and restores afterwards: `DEFAULT.tmdb`,
  `DEFAULT.add_logo`, `TRACKERS.EMU.api_key`, `SCREENS.img_hosts`, `SCREENS.API_KEYS`, `TORRENT_CLIENTS.qbit.*`.

## Verify

- `pip install -r requirements.txt` on `python:3.12-slim` needs no extras or pins. There is no test suite:
  check changes with focused scripts in Docker (fake tracker server, `upload.py` on a test folder, exit code).
- The redactor rewrites config secrets in all Rich output.
- CI (`.github/workflows/ci.yml`) runs critical ruff rules (`E9,F63,F7,F82`) and `compileall` on every PR.
  Style is left to CodeRabbit on changed lines.

## Secrets and auth

- The tracker API key travels as the `api_token` query param. Never print `params`, full request URLs or
  `http_err.request.url`. Plain `print` and raw `logging` handlers skip the redactor: keep them for
  machine-read lines that hold no secrets.
- Re-downloading the signed `.torrent` after upload (`src/trackers/UNIT3D.py`) must send the same `params`
  and `User-Agent` as the upload POST, or eMuwarez answers 403 and `[EMU]<name>.torrent` is never written.
- `data/config.yaml`, `config.yaml.bak` and `tags.json` are tracked even though `.gitignore` lists them.
  They hold placeholders only: never commit real keys.

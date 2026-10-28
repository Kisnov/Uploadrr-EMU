# Uploadrr-EMU

[![CI](https://github.com/Kisnov/Uploadrr-EMU/actions/workflows/ci.yml/badge.svg)](https://github.com/Kisnov/Uploadrr-EMU/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![Licencia: GPL-3.0](https://img.shields.io/github/license/Kisnov/Uploadrr-EMU)](LICENSE)

Fork de [Uploadrr](https://codeberg.org/CvT/Uploadrr) dedicado a subir a **[eMuwarez](https://emu-warez.com)**, un
tracker UNIT3D (clave `EMU`). Prepara la subida (mediainfo, capturas, descripción, `.torrent`), busca duplicados,
sube y añade el torrent firmado a qBittorrent para sembrarlo.

Lo usa **emuwarez-uploadrr**, un servicio web autoalojado que cruza la biblioteca de Radarr/Sonarr con eMuwarez y
lanza este script sin intervención en cada subida. También se puede usar a mano desde la terminal.

> [!NOTE]
> Solo se mantiene la ruta de eMuwarez. Los demás trackers, la música y el contenido adulto vienen heredados de
> Uploadrr y no se revisan ni se prueban.

## Qué cambia respecto a Uploadrr

- **No sube a ciegas.** Si la búsqueda de duplicados falla (error de red, HTTP distinto de 200, redirección al login,
  respuesta mal formada), no se sube. La búsqueda recorre todas las páginas de resultados y trata 1080p y 1080i como
  la misma resolución.
- **Subida robusta.** Repite el envío ante cortes y errores 5xx sin duplicar el torrent, y descarga el `.torrent`
  firmado por eMuwarez con la misma autenticación que la subida, reintentando mientras el tracker lo prepara.
- **Salida pensada para otro programa.** Sale con `0` solo si ha subido algo. Tras una subida imprime
  `Torrent URL: https://emu-warez.com/torrents/<id>` en su propia línea, y muestra el progreso (hash, capturas)
  aunque la salida no sea una terminal.
- **En español.** Prefiere el tráiler en español (también en series y anime) y el logo de la ficha en español cuando
  TMDb lo tiene. Firma propia en la descripción.
- **Arreglos de hosts de imágenes.** pixhost (migró a `.cc`) e imgbb. Si ningún host acepta las capturas, la subida
  falla en vez de salir sin imágenes.
- Si qBittorrent falla después de subir, la subida no cuenta como fallida.
- Dependencias directas con versión fija en `requirements.txt`.

## Requisitos

- Python 3.12 (es la versión que se prueba).
- `ffmpeg` y `mediainfo`.
- Opcional: `mktorrent` para crear el `.torrent` más rápido (`DEFAULT.torrent_creation: mktorrent`).
- Solo para discos Blu-ray completos en Linux o macOS: [Mono](https://www.mono-project.com/), para BDInfo.

```bash
git clone https://github.com/Kisnov/Uploadrr-EMU.git
cd Uploadrr-EMU
pip install -r requirements.txt
```

## Configuración

Todo está en `data/config.yaml`. Para empezar, copia el ejemplo y rellena tus claves (no las subas nunca a git):

```bash
cp data/backup/example_config.yaml data/config.yaml
```

Para eMuwarez hace falta:

| Clave | Qué es |
|---|---|
| `DEFAULT.tmdb` | API key de [TMDb](https://developer.themoviedb.org/docs/getting-started) (v3) |
| `TRACKERS.EMU.api_key` | API key de tu cuenta de eMuwarez |
| `SCREENS.img_hosts`, `SCREENS.API_KEYS` | Hosts de imágenes para las capturas, en orden de preferencia, y sus claves |
| `TORRENT_CLIENTS.qbit` | qBittorrent al que se añade el torrent para sembrarlo |

Con emuwarez-uploadrr no hace falta tocarlo a mano: la aplicación escribe estas claves antes de cada subida y las
restaura después. El resto de opciones están en `data/backup/example_config.yaml` y en la
[guía de configuración de Uploadrr](https://codeberg.org/CvT/Uploadrr/wiki/Full-Config-Guide).

## Uso

Así lo lanza emuwarez-uploadrr para una película:

```bash
python upload.py "/ruta/a/la/película" -tk EMU -mt "Nombre del torrent" -tmdb 603 -ua -c movie -res 1080p
```

Para series, `-c tv` y además `-season S01` (y `-episode E01` si es un episodio suelto).

| Opción | Qué hace |
|---|---|
| `-tk EMU` | Tracker al que se sube |
| `-mt` | Nombre del torrent, en lugar del que generaría Uploadrr |
| `-tmdb` | ID de TMDb |
| `-ua` | Sin preguntas. Ante un posible duplicado o un grupo vetado, no sube |
| `-c` | Categoría: `movie` o `tv` |
| `-res` | Resolución: `2160p`, `1080p`, `1080i`, `720p`, `576p`, `576i`, `480p`, `480i`, `4320p`, `8640p` u `other` |

`python upload.py --help` lista todas las opciones.

**Código de salida:** `0` solo si se ha subido. Un duplicado, un rechazo del tracker, un ID de TMDb sin ficha, una
carpeta sin vídeo o cualquier error que impida la subida salen con `1`. Un fallo de qBittorrent después de subir
no cambia el `0`.

## Desarrollo

```bash
pip install pytest
python -m pytest tests -q -p no:cacheprovider
```

Las pruebas lanzan la búsqueda de duplicados y la subida contra un tracker UNIT3D falso en local. El CI pasa las
reglas graves de `ruff` y `compileall` en cada PR. `CLAUDE.md` recoge el contrato con emuwarez-uploadrr (qué líneas
de la salida lee y qué claves de la configuración toca): si cambias una de ellas, hay que cambiar la aplicación a la
vez.

## Origen y créditos

[Upload-Assistant](https://github.com/L4GSP1KE/Upload-Assistant) de L4G →
[Uploadrr](https://codeberg.org/CvT/Uploadrr) de CvT →
[Uploadrr-EMU](https://codeberg.org/ccx94/Uploadrr-EMU) de ccx94, que añadió eMuwarez → este fork.

Todo el código de base es de L4G, CvT y quienes contribuyeron a esos proyectos. Licencia GPL-3.0, ver
[LICENSE](LICENSE).

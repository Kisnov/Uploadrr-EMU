"""``--resume``: sin él cada ítem borra su ``tmp/<uuid>``; con él se reutiliza solo lo marcado en ``pasos_hechos``.

Las líneas ``Step done: screenshots`` / ``Step done: torrent`` las lee emuwarez-uploadrr tal cual."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from torf import Torrent

import upload
from src.args import apply_special_logic, build_parser, normalize_args, supplied_args
from src.screenshots.manager import ScreenshotManager
from src.utils import meta as meta_mod
from src.utils import torrentman
from src.utils.console import console
from src.utils.meta import limpiar_tmp_item, load_existing_meta, marcar_paso_hecho, queda_estado_viejo


def _pasos(capsys) -> list[str]:
    """Las líneas «Step done:» de la salida (el resto es lo que el log de Rich imprima)."""
    return [linea for linea in capsys.readouterr().out.splitlines() if linea.startswith("Step done")]


def _run(coro):
    return asyncio.run(coro)


def _torrent_valido(destino: Path, origen: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    t = Torrent(path=origen, private=True)
    t.generate()
    t.write(destino, overwrite=True)


# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
def test_resume_es_un_bool_por_item_y_no_choca_con_otras_opciones():
    parser = build_parser({})  # argparse lanza si una opción se repite
    opciones = [o for a in parser._actions for o in a.option_strings]
    assert opciones.count("-rs") == 1 and opciones.count("--resume") == 1

    assert normalize_args(parser.parse_args([]))["resume"] is False
    assert normalize_args(parser.parse_args(["-rs"]))["resume"] is True
    assert normalize_args(parser.parse_args(["--resume"]))["resume"] is True


def test_resume_por_item_solo_aparece_si_se_pasa():
    parser = build_parser({})
    assert "resume" not in supplied_args(["-s", "4"], parser)
    assert supplied_args(["--resume"], parser)["resume"] is True


def test_un_resume_guardado_no_se_hereda_si_no_se_pasa(tmp_path):
    item = tmp_path / "Peli"
    guardado = tmp_path / "tmp" / "Peli"
    guardado.mkdir(parents=True)
    (guardado / "meta.json").write_text(json.dumps({"resume": True, "pasos_hechos": ["torrent"], "tmdb": "1"}))

    parser = build_parser({})
    nuevo = apply_special_logic({"base_dir": str(tmp_path), **normalize_args(parser.parse_args([]))})
    resultado = _run(load_existing_meta(tmp_path, str(item), nuevo))

    assert resultado["resume"] is False
    assert resultado["pasos_hechos"] == ["torrent"]  # lo gestiona upload.py: aquí solo se fusiona

    con = apply_special_logic({"base_dir": str(tmp_path), **normalize_args(parser.parse_args(["-rs"]))})
    assert _run(load_existing_meta(tmp_path, str(item), con))["resume"] is True


# ---------------------------------------------------------------------------
# Borrado de tmp/<uuid>
# ---------------------------------------------------------------------------
def test_limpiar_borra_todo_el_estado_del_item(tmp_path):
    estado = tmp_path / "tmp" / "Peli"
    estado.mkdir(parents=True)
    (estado / "meta.json").write_text("{}")
    (estado / "BASE.torrent").write_bytes(b"viejo")
    (estado / "sub").mkdir()
    (estado / "sub" / "captura.png").write_bytes(b"png")
    otro = tmp_path / "tmp" / "Otra"
    otro.mkdir()
    (otro / "meta.json").write_text("{}")

    assert limpiar_tmp_item(tmp_path, "/media/uploads/Peli") is True

    assert not estado.exists()
    assert (otro / "meta.json").exists()  # solo el suyo


def test_limpiar_sin_estado_previo_no_hace_nada(tmp_path):
    assert limpiar_tmp_item(tmp_path, "/media/Peli") is False
    assert not (tmp_path / "tmp").exists()


def test_limpiar_no_sigue_un_symlink_en_tmp_uuid(tmp_path):
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    (fuera / "importante.txt").write_text("no tocar")
    (tmp_path / "tmp").mkdir()
    os.symlink(fuera, tmp_path / "tmp" / "Peli", target_is_directory=True)

    assert limpiar_tmp_item(tmp_path, "/media/Peli") is False

    assert (fuera / "importante.txt").read_text() == "no tocar"
    assert (tmp_path / "tmp" / "Peli").is_symlink()


def test_limpiar_funciona_si_tmp_es_un_symlink_como_en_la_imagen(tmp_path):
    config = tmp_path / "config"
    (config / "Peli").mkdir(parents=True)
    (config / "Peli" / "meta.json").write_text("{}")
    base = tmp_path / "app"
    base.mkdir()
    os.symlink(config, base / "tmp", target_is_directory=True)

    assert limpiar_tmp_item(base, "/media/Peli") is True

    assert not (config / "Peli").exists()
    assert config.exists()


@pytest.mark.parametrize("ruta", ["/", "..", "/media/..", ""])
def test_limpiar_nunca_borra_tmp_ni_nada_fuera_con_nombres_raros(tmp_path, ruta):
    (tmp_path / "tmp" / "Peli").mkdir(parents=True)
    (tmp_path / "tmp" / "Peli" / "meta.json").write_text("{}")

    assert limpiar_tmp_item(tmp_path, ruta) is False

    assert (tmp_path / "tmp" / "Peli" / "meta.json").exists()


def test_un_fallo_al_borrar_avisa_y_no_aborta(tmp_path, monkeypatch):
    (tmp_path / "tmp" / "Peli").mkdir(parents=True)
    avisos = []

    def falla(*_a, **_k):
        raise PermissionError("sin permiso")

    monkeypatch.setattr(shutil, "rmtree", falla)
    monkeypatch.setattr(meta_mod.log, "warning", lambda msg, *a, **k: avisos.append(msg))

    assert limpiar_tmp_item(tmp_path, "/media/Peli") is False

    assert any("sin permiso" in a for a in avisos)
    assert (tmp_path / "tmp" / "Peli").exists()


# ---------------------------------------------------------------------------
# Marcas y línea «Step done:»
# ---------------------------------------------------------------------------
def test_marcar_guarda_en_meta_json_e_imprime_con_print_y_no_con_rich(tmp_path, capsys, monkeypatch):
    def rich_prohibido(*_a, **_k):
        raise AssertionError("Step done: no puede pasar por Rich, que parte las líneas a 80 columnas")

    monkeypatch.setattr(console, "print", rich_prohibido)
    meta = {"base_dir": str(tmp_path), "uuid": "Peli " + "x" * 120}

    _run(marcar_paso_hecho(meta, "screenshots"))
    _run(marcar_paso_hecho(meta, "torrent"))
    _run(marcar_paso_hecho(meta, "torrent"))  # un resume lo repite: la lista no se duplica, la línea sí

    assert capsys.readouterr().out == "Step done: screenshots\nStep done: torrent\nStep done: torrent\n"
    guardado = json.loads((tmp_path / "tmp" / meta["uuid"] / "meta.json").read_text())
    assert guardado["pasos_hechos"] == ["screenshots", "torrent"]


# ---------------------------------------------------------------------------
# Capturas: ¿falta alguna URL?
# ---------------------------------------------------------------------------
@pytest.fixture
def gestor():
    return ScreenshotManager.__new__(ScreenshotManager)  # no hace falta el generador ni los hosts


def test_faltan_capturas_segun_las_urls_de_las_principales(gestor):
    con_url = {"img_url": "https://host/a.png", "raw_url": "https://host/a.png"}
    sin_url = {"local_file": "/tmp/a.png"}

    assert gestor.faltan_capturas({}) is True
    assert gestor.faltan_capturas({"image_list": {"main": []}}) is True
    assert gestor.faltan_capturas({"image_list": {"main": [con_url, sin_url]}}) is True
    assert gestor.faltan_capturas({"image_list": {"main": [con_url, con_url]}}) is False
    assert gestor.faltan_capturas({"image_list": [con_url]}) is False  # formato antiguo: lista
    # solo cuentan las principales
    assert gestor.faltan_capturas({"image_list": {"main": [con_url], "episodes": {"S01E02": [sin_url]}}}) is False


# ---------------------------------------------------------------------------
# BASE.torrent
# ---------------------------------------------------------------------------
class TorrentMakerFalso:
    creados = 0

    def __init__(self, config):
        pass

    async def create(self, meta, path, piece_size_max=None):
        type(self).creados += 1
        _torrent_valido(Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BASE.torrent", path)


@pytest.fixture
def torrents(tmp_path, monkeypatch):
    TorrentMakerFalso.creados = 0
    monkeypatch.setattr(torrentman, "TorrentMaker", TorrentMakerFalso)

    class ClientesFalso:
        def __init__(self, config):
            pass

        async def find_existing_torrent(self, meta):
            return None

    import src.clients

    monkeypatch.setattr(src.clients, "Clients", ClientesFalso)
    contenido = tmp_path / "media" / "Peli"
    contenido.mkdir(parents=True)
    (contenido / "Peli.mkv").write_bytes(b"x" * 4096)
    base = tmp_path / "app"
    return contenido, base, base / "tmp" / "Peli" / "BASE.torrent"


def _meta(contenido, base, **extra):
    return {"base_dir": str(base), "uuid": "Peli", "path": str(contenido), **extra}


def test_torrent_sin_marca_se_rehace_aunque_exista(torrents, capsys):
    contenido, base, torrent = torrents
    _torrent_valido(torrent, contenido)  # válido pero sin marca: puede ser de otros ficheros

    meta = _run(torrentman.handle_torrent_creation({}, _meta(contenido, base)))

    assert TorrentMakerFalso.creados == 1
    assert meta["pasos_hechos"] == ["torrent"]
    assert _pasos(capsys) == ["Step done: torrent"]


def test_torrent_marcado_y_valido_se_reutiliza_e_imprime_la_marca(torrents, capsys):
    contenido, base, torrent = torrents
    _torrent_valido(torrent, contenido)
    antes = torrent.read_bytes()

    meta = _run(torrentman.handle_torrent_creation({}, _meta(contenido, base, resume=True, pasos_hechos=["screenshots", "torrent"])))

    assert TorrentMakerFalso.creados == 0
    assert torrent.read_bytes() == antes
    assert meta["pasos_hechos"] == ["screenshots", "torrent"]
    assert _pasos(capsys) == ["Step done: torrent"]


@pytest.mark.parametrize("contenido_roto", [b"", b"esto no es bencode", b"d4:infoe", b"i42e"])
def test_torrent_marcado_pero_corrupto_se_rehace(torrents, capsys, contenido_roto):
    contenido, base, torrent = torrents
    torrent.parent.mkdir(parents=True)
    torrent.write_bytes(contenido_roto)

    _run(torrentman.handle_torrent_creation({}, _meta(contenido, base, resume=True, pasos_hechos=["torrent"])))

    assert TorrentMakerFalso.creados == 1
    assert torrentman._base_torrent_valido(torrent)
    assert _pasos(capsys) == ["Step done: torrent"]


def test_si_la_importacion_del_cliente_no_deja_un_torrent_valido_se_crea_uno(torrents, capsys, monkeypatch):
    contenido, base, torrent = torrents

    class ClientesConTorrent:
        def __init__(self, config):
            pass

        async def find_existing_torrent(self, meta):
            return "hash"

    async def importacion_rota(self, reuse, base_dir, uuid):
        torrent.parent.mkdir(parents=True, exist_ok=True)
        torrent.write_bytes(b"roto")

    import src.clients

    monkeypatch.setattr(src.clients, "Clients", ClientesConTorrent)
    monkeypatch.setattr(TorrentMakerFalso, "from_existing", importacion_rota, raising=False)

    meta = _run(torrentman.handle_torrent_creation({}, _meta(contenido, base)))

    assert TorrentMakerFalso.creados == 1
    assert torrentman._base_torrent_valido(torrent)
    assert meta["pasos_hechos"] == ["torrent"] and _pasos(capsys) == ["Step done: torrent"]


def test_rehash_rehace_aunque_este_marcado(torrents):
    contenido, base, torrent = torrents
    _torrent_valido(torrent, contenido)

    _run(torrentman.handle_torrent_creation({}, _meta(contenido, base, rehash=True, pasos_hechos=["torrent"])))

    assert TorrentMakerFalso.creados == 1


def test_nohash_marca_el_paso(torrents, capsys):
    contenido, base, torrent = torrents

    meta = _run(torrentman.handle_torrent_creation({}, _meta(contenido, base, nohash=True)))

    assert TorrentMakerFalso.creados == 0 and not torrent.exists()
    assert meta["client"] == "none" and meta["pasos_hechos"] == ["torrent"]
    assert _pasos(capsys) == ["Step done: torrent"]


def test_si_no_queda_un_torrent_valido_no_se_marca(torrents, capsys, monkeypatch):
    contenido, base, _ = torrents

    async def no_crea(self, meta, path, piece_size_max=None):
        return None

    monkeypatch.setattr(TorrentMakerFalso, "create", no_crea)

    meta = _run(torrentman.handle_torrent_creation({}, _meta(contenido, base)))

    assert "pasos_hechos" not in meta
    assert _pasos(capsys) == []


# ---------------------------------------------------------------------------
# La cola entera (upload.py), con todo lo de fuera simulado
# ---------------------------------------------------------------------------
URL = {"img_url": "https://host/a.png", "raw_url": "https://host/a.png"}


class Aplicacion:
    """Sustitutos de lo que toca red o procesos; ``capturas`` decide qué devuelve el gestor de capturas."""

    capturas: list = []
    fases: list = []
    sellos_al_crear_torrent: list = []

    class Prep:
        def __init__(self, config, base_dir):
            pass

        async def gather_prep(self, meta, mode="cli"):
            meta["uuid"] = Path(meta["path"]).name
            return meta

    class Capturas:
        def __init__(self, config, base_dir):
            pass

        async def handle_screenshots(self, meta, whitelists):
            Aplicacion.fases.append("capturas")
            meta["image_list"] = {"main": list(Aplicacion.capturas), "episodes": {}}
            return meta

        def faltan_capturas(self, meta):
            return ScreenshotManager.faltan_capturas(self, meta)

        _missing_urls = ScreenshotManager._missing_urls
        _is_valid_url = ScreenshotManager._is_valid_url


class TrackerFalso:
    def __init__(self, config):
        pass


@pytest.fixture
def cola(tmp_path, monkeypatch):
    Aplicacion.capturas = [URL]
    Aplicacion.fases = []

    async def checks(meta, trackers, registry, skipped, path, config):
        return {"EMU": True}

    async def torrent(config, meta):
        Aplicacion.fases.append("torrent")
        await marcar_paso_hecho(meta, "torrent")
        return meta

    async def subir(*_a, **_k):
        return True, None

    monkeypatch.setattr(upload, "Prep", Aplicacion.Prep)
    monkeypatch.setattr(upload, "ScreenshotManager", Aplicacion.Capturas)
    monkeypatch.setattr(upload, "pre_upload_checks", checks)
    monkeypatch.setattr(upload, "handle_torrent_creation", torrent)
    monkeypatch.setattr(upload, "upload_to_trackers", subir)
    monkeypatch.setattr(upload, "print_summary", lambda *a, **k: None)

    base = tmp_path / "app"
    base.mkdir()
    item = tmp_path / "media" / "Peli"
    item.mkdir(parents=True)

    def lanzar(**meta):
        app = upload.Uploadrr.__new__(upload.Uploadrr)
        app.config = {"SCREENS": {}}
        app.client = None
        item_meta = {"path": str(item), "unattended": True, "screens": 3, "base_dir": str(base), **meta}
        _run(app._process_queue([item_meta], {"quiet": True, "unattended": True}, ["EMU"], {"EMU": {"class": TrackerFalso}}, base))

    return base, lanzar


def _estado_viejo(base: Path) -> Path:
    estado = base / "tmp" / "Peli"
    estado.mkdir(parents=True)
    (estado / "BASE.torrent").write_bytes(b"viejo")
    (estado / "captura.png").write_bytes(b"png")
    (estado / "meta.json").write_text(json.dumps({"pasos_hechos": ["screenshots", "torrent"], "image_list": {"main": [URL]}}))
    return estado


def test_sin_resume_se_borra_el_estado_y_se_hace_todo(cola, capsys):
    base, lanzar = cola
    estado = _estado_viejo(base)

    lanzar()

    assert not (estado / "captura.png").exists() and not (estado / "BASE.torrent").exists()
    assert Aplicacion.fases == ["capturas", "torrent"]
    assert _pasos(capsys).count("Step done: screenshots") == 1
    guardado = json.loads((estado / "meta.json").read_text())
    assert guardado["pasos_hechos"] == ["screenshots", "torrent"]


def test_sin_resume_un_borrado_fallido_salta_el_item_y_no_hereda_las_marcas_viejas(cola, monkeypatch, capsys):
    base, lanzar = cola
    estado = _estado_viejo(base)
    antes = (estado / "meta.json").read_text()
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError("no")))

    with pytest.raises(SystemExit) as salida:
        lanzar()

    assert salida.value.code == 1                  # un elemento saltado sale con 1, como cualquier otro
    assert Aplicacion.fases == [] and _pasos(capsys) == []
    assert (estado / "meta.json").read_text() == antes


def test_con_resume_se_conserva_el_estado_y_se_imprimen_las_marcas_reutilizadas(cola, capsys):
    base, lanzar = cola
    estado = _estado_viejo(base)

    lanzar(resume=True)

    assert (estado / "captura.png").exists()
    assert _pasos(capsys) == ["Step done: screenshots", "Step done: torrent"]


def test_la_marca_de_capturas_no_se_pone_si_faltan_urls(cola, capsys):
    base, lanzar = cola
    Aplicacion.capturas = [URL, {"local_file": "/tmp/b.png"}]

    lanzar()

    assert _pasos(capsys) == ["Step done: torrent"]
    assert "screenshots" not in json.loads((base / "tmp" / "Peli" / "meta.json").read_text())["pasos_hechos"]


def test_sin_capturas_pendientes_el_paso_se_da_por_hecho(cola, capsys):
    base, lanzar = cola

    lanzar(screens=0)

    assert Aplicacion.fases == ["torrent"]
    assert _pasos(capsys) == ["Step done: screenshots", "Step done: torrent"]


def test_con_generate_b4_confirm_tambien_se_marca(cola, capsys):
    base, lanzar = cola

    def lanzar_b4(**meta):
        app = upload.Uploadrr.__new__(upload.Uploadrr)
        app.config = {"SCREENS": {"generate_b4_confirm": True}}
        app.client = None
        item = base.parent / "media" / "Peli"
        item_meta = {"path": str(item), "unattended": True, "screens": 3, "base_dir": str(base), **meta}
        _run(app._process_queue([item_meta], {"quiet": True, "unattended": True}, ["EMU"], {"EMU": {"class": TrackerFalso}}, base))

    lanzar_b4()

    assert Aplicacion.fases == ["capturas", "torrent"]
    assert _pasos(capsys).count("Step done: screenshots") == 1


def test_un_item_saltado_no_imprime_pasos(cola, capsys, monkeypatch):
    base, lanzar = cola

    async def rechaza(meta, trackers, registry, skipped, path, config):
        return {"EMU": False}

    monkeypatch.setattr(upload, "pre_upload_checks", rechaza)

    with pytest.raises(SystemExit) as salida:
        lanzar()

    assert salida.value.code == 1
    assert _pasos(capsys) == []



def test_si_no_se_puede_borrar_el_estado_viejo_se_salta_el_item(cola, monkeypatch, capsys):
    base, lanzar = cola
    estado = _estado_viejo(base)
    monkeypatch.setattr(upload, "limpiar_tmp_item", lambda *_a, **_k: False)  # el borrado falla y deja el directorio

    with pytest.raises(SystemExit):
        lanzar()

    assert Aplicacion.fases == []                  # ni capturas ni torrent: no se sube nada con restos viejos
    assert _pasos(capsys) == []
    assert (estado / "captura.png").exists()       # y el estado viejo no se toca


def test_si_no_hay_estado_viejo_el_borrado_que_no_hace_nada_no_salta_el_item(cola, monkeypatch):
    base, lanzar = cola
    monkeypatch.setattr(upload, "limpiar_tmp_item", lambda *_a, **_k: False)  # nada que borrar: devuelve False

    lanzar()

    assert Aplicacion.fases == ["capturas", "torrent"]


def test_queda_estado_viejo_si_no_se_puede_comprobar(tmp_path, monkeypatch):
    base = tmp_path / "app"
    assert queda_estado_viejo(base, "/media/Peli") is False            # no hay nada
    (base / "tmp" / "Peli").mkdir(parents=True)
    assert queda_estado_viejo(base, "/media/Peli") is True             # hay un directorio

    def denegado(self):
        raise PermissionError("no")

    monkeypatch.setattr(Path, "is_symlink", denegado)
    assert queda_estado_viejo(base, "/media/Peli") is True             # no se sabe: se trata como que queda


def test_si_no_se_puede_ni_comprobar_el_estado_viejo_se_salta_el_item(cola, monkeypatch):
    base, lanzar = cola
    monkeypatch.setattr(upload, "limpiar_tmp_item", lambda *_a, **_k: False)
    monkeypatch.setattr(upload, "queda_estado_viejo", lambda *_a, **_k: True)

    with pytest.raises(SystemExit):
        lanzar()

    assert Aplicacion.fases == []


def test_al_regenerar_el_torrent_se_quita_la_marca_antes_de_crearlo(torrents, monkeypatch):
    contenido, base, torrent = torrents
    torrent.parent.mkdir(parents=True)
    torrent.write_bytes(b"roto")
    visto = {}

    async def falla(self, meta, path, piece_size_max=None):
        visto["pasos"] = list(meta["pasos_hechos"])
        raise RuntimeError("hash a medias")

    monkeypatch.setattr(TorrentMakerFalso, "create", falla)
    meta = _meta(contenido, base, resume=True, pasos_hechos=["screenshots", "torrent"])

    with pytest.raises(RuntimeError):
        _run(torrentman.handle_torrent_creation({}, meta))

    assert visto["pasos"] == ["screenshots"]                           # la marca vieja no sobrevive a un fallo
    assert meta["pasos_hechos"] == ["screenshots"]


def test_si_meta_json_no_se_puede_guardar_no_se_anuncia_el_paso(tmp_path, capsys, monkeypatch):
    base = tmp_path / "app"
    meta = {"base_dir": str(base), "uuid": "Peli"}

    async def no_guarda(*_a, **_k):
        return False

    monkeypatch.setattr(meta_mod, "save_meta", no_guarda)
    _run(marcar_paso_hecho(meta, "torrent"))

    assert _pasos(capsys) == []                       # sin la marca guardada, un --resume no la encontraría
    assert meta["pasos_hechos"] == ["torrent"]


def test_save_meta_dice_si_pudo_guardar(tmp_path):
    assert _run(meta_mod.save_meta({"a": 1}, tmp_path, "Peli")) is True
    assert _run(meta_mod.save_meta({"a": object()}, tmp_path, "Peli")) is False   # no serializable


def test_al_regenerar_las_capturas_se_quita_su_marca_antes(cola):
    base, lanzar = cola
    estado = base / "tmp" / "Peli"
    estado.mkdir(parents=True)
    (estado / "meta.json").write_text(json.dumps({"pasos_hechos": ["screenshots", "torrent"]}))
    Aplicacion.capturas = []                             # el gestor «falla»: sin imágenes

    lanzar(resume=True)

    guardado = json.loads((estado / "meta.json").read_text())
    assert "screenshots" not in guardado["pasos_hechos"]  # un fallo no deja la marca vieja diciendo «hecho»


def test_nohash_no_da_por_bueno_un_torrent_sin_marca(torrents, capsys):
    contenido, base, torrent = torrents
    _torrent_valido(torrent, contenido)                  # existe, válido, pero sin marca

    meta = _run(torrentman.handle_torrent_creation({}, _meta(contenido, base, nohash=True, resume=True)))

    assert not torrent.exists()                          # un --resume posterior no lo reutilizará
    assert meta["pasos_hechos"] == ["torrent"] and _pasos(capsys) == ["Step done: torrent"]


def test_nohash_conserva_un_torrent_marcado(torrents):
    contenido, base, torrent = torrents
    _torrent_valido(torrent, contenido)

    _run(torrentman.handle_torrent_creation({}, _meta(contenido, base, nohash=True, resume=True, pasos_hechos=["torrent"])))

    assert torrent.exists()

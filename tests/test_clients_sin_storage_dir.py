"""src/clients.py cuando `torrent_storage_dir` no existe o falta (config que deja la app).

La ruta se resuelve contra el cwd. Si no existe, la app la quita de `TORRENT_CLIENTS.qbit` y pone
`enable_search: False`: buscar un .torrent ya hecho tiene que devolver None sin tocar qBittorrent ni reventar,
y añadir el torrent después de subir tiene que seguir funcionando igual."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from torf import Torrent

from src import clients
from src.clients import Clients

QBIT_SIN_DIR = {
    "torrent_client": "qbit",
    "enable_search": False,
    "qbit_url": "http://127.0.0.1",
    "qbit_port": "8080",
    "qbit_user": "u",
    "qbit_pass": "p",
}


def _config(**qbit):
    return {
        "DEFAULT": {"default_torrent_client": "qbit"},
        "TORRENT_CLIENTS": {"qbit": {**QBIT_SIN_DIR, **qbit}},
        "TRACKERS": {},
    }


class QbitProhibido:
    """Con la búsqueda apagada no se abre ni una conexión a qBittorrent."""

    def __init__(self, **_kwargs):
        raise AssertionError("no hay que crear un cliente de qBittorrent para buscar un .torrent")


class Qbit:
    anadidos: list = []

    def __init__(self, **_kwargs):
        pass

    def auth_log_in(self):
        pass

    def torrents_add(self, **kwargs):
        Qbit.anadidos.append(kwargs)
        return "Ok."

    def torrents_info(self, torrent_hashes=None):
        return [{"hash": torrent_hashes}]

    def torrents_resume(self, infohash):
        pass


@pytest.fixture
def meta(tmp_path):
    video = tmp_path / "Peli.2020.mkv"
    video.write_bytes(b"x" * 2048)
    return {"path": str(video), "filelist": [str(video)], "clean_name": "Peli", "uuid": "Peli.2020.mkv", "base_dir": str(tmp_path)}


def _buscar(config, meta):
    return asyncio.run(Clients(config).find_existing_torrent(meta))


def test_sin_torrent_storage_dir_y_sin_busqueda_no_hay_torrent_que_reutilizar(meta, capsys, monkeypatch):
    monkeypatch.setattr(clients.qbittorrentapi, "Client", QbitProhibido)

    assert _buscar(_config(), meta) is None

    salida = capsys.readouterr().out
    assert "No valid .torrent found" in salida
    assert "Invalid torrent_storage_dir" not in salida  # la clave falta: no es una ruta mal puesta


def test_con_enable_search_activo_pero_sin_dir_tampoco_peta_si_no_hay_hash(meta, monkeypatch):
    """Una config sin la clave y con la búsqueda encendida llega al cliente: con un qBittorrent vacío, None."""

    class QbitVacio(Qbit):
        def torrents_info(self, torrent_hashes=None):
            return []

    monkeypatch.setattr(clients.qbittorrentapi, "Client", QbitVacio)

    assert _buscar(_config(enable_search=True), meta) is None


def test_una_ruta_inexistente_avisa_y_devuelve_none_sin_buscar(meta, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(clients.qbittorrentapi, "Client", QbitProhibido)
    inexistente = tmp_path / "no" / "existe"

    assert _buscar(_config(torrent_storage_dir=str(inexistente), enable_search=True), meta) is None

    # Rich parte el aviso a 80 columnas (no es una línea que lea la app): se compara sin saltos.
    assert "".join(f"Invalid torrent_storage_dir path: {inexistente}".split()) in "".join(capsys.readouterr().out.split())


def test_la_ruta_relativa_se_resuelve_contra_el_cwd(meta, capsys, monkeypatch, tmp_path):
    """Igual que en el contenedor: «BT_backup» solo existe si el cwd lo tiene."""
    monkeypatch.setattr(clients.qbittorrentapi, "Client", QbitProhibido)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    config = _config(torrent_storage_dir="BT_backup", enable_search=False)

    assert _buscar(config, meta) is None
    assert "Invalid torrent_storage_dir path: BT_backup" in capsys.readouterr().out

    (cwd / "BT_backup").mkdir()
    assert _buscar(config, meta) is None
    salida = capsys.readouterr().out
    assert "Invalid torrent_storage_dir" not in salida and "No valid .torrent found" in salida


def test_un_hash_de_meta_sin_dir_no_peta_y_no_se_reutiliza(meta, monkeypatch):
    monkeypatch.setattr(clients.qbittorrentapi, "Client", QbitProhibido)
    meta = {**meta, "torrenthash": "a" * 40}

    assert _buscar(_config(), meta) is None


def test_sin_cliente_o_con_none_no_se_busca_nada(meta):
    config = _config()
    assert _buscar(config, {**meta, "client": "none"}) is None
    config["DEFAULT"]["default_torrent_client"] = None
    assert _buscar(config, meta) is None


def test_la_config_sin_dir_sirve_para_anadir_el_torrent_despues_de_subir(meta, tmp_path, capsys, monkeypatch):
    """Añadir a qBittorrent no usa torrent_storage_dir: la línea «Added to:» sale igual."""
    monkeypatch.setattr(clients.qbittorrentapi, "Client", Qbit)
    Qbit.anadidos = []

    async def sin_espera(_s):
        pass

    monkeypatch.setattr(clients.asyncio, "sleep", sin_espera)
    carpeta = tmp_path / "tmp" / meta["uuid"]
    carpeta.mkdir(parents=True)
    torrent = Torrent(path=Path(meta["path"]), private=True)
    torrent.generate()
    torrent.write(carpeta / "[EMU]Peli.torrent")

    asyncio.run(Clients(_config()).add_to_client(meta, "EMU"))

    salida = capsys.readouterr().out
    assert "Adding and rechecking torrent" in salida and "Added to:" in salida
    assert len(Qbit.anadidos) == 1

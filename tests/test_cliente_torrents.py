"""Añadir a qBittorrent después de subir (src/clients.py y src/utils/upload.py)."""

import asyncio

import pytest

from src import clients
from src.utils import upload


class Torrent:
    infohash = "a" * 40

    def dump(self):
        return b"d4:infod4:name1:xee"


class Qbit:
    """Un qBittorrent que acepta todo y solo enseña el torrent si `aparece`."""

    aparece = True

    def __init__(self, **_kwargs):
        self.reanudados = []

    def auth_log_in(self):
        pass

    def torrents_add(self, **_kwargs):
        return "Ok."

    def torrents_info(self, torrent_hashes):
        return [{"hash": torrent_hashes}] if self.aparece else []

    def torrents_resume(self, infohash):
        self.reanudados.append(infohash)


@pytest.fixture
def qbit(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.qbittorrentapi, "Client", Qbit)

    async def sin_espera(_segundos):
        pass

    monkeypatch.setattr(clients.asyncio, "sleep", sin_espera)
    cliente = {"qbit_url": "http://127.0.0.1", "qbit_port": 8080, "qbit_user": "u", "qbit_pass": "p"}

    def anadir():
        c = clients.Clients(config={"TRACKERS": {}})
        asyncio.run(c._add_to_qbittorrent(tmp_path, tmp_path / "t.torrent", Torrent(), {}, "/l", "/r", cliente, "EMU"))

    return anadir


def test_si_qbittorrent_lo_muestra_se_da_por_anadido(qbit, capsys):
    qbit()
    assert "Added to:" in capsys.readouterr().out


def test_si_no_aparece_no_se_da_por_anadido(qbit, capsys, monkeypatch):
    monkeypatch.setattr(Qbit, "aparece", False)
    with pytest.raises(RuntimeError, match="no muestra el torrent"):
        qbit()
    assert "Added to:" not in capsys.readouterr().out


def test_subido_pero_el_cliente_falla_deja_la_linea_fija(capsys):
    class Tracker:
        async def upload(self, _meta):
            return True

    class Cliente:
        async def add_to_client(self, _meta, _tracker):
            raise RuntimeError("Failed to connect to qBittorrent.\nConnection refused")

    resultado = asyncio.run(upload.upload_to_tracker(Tracker(), "EMU", {}, {}, Cliente(), 0, [], "/x", False))

    assert resultado == (True, 0)
    assert "Torrent client add failed: Failed to connect to qBittorrent. Connection refused\n" in capsys.readouterr().out

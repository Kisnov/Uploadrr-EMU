"""Añadir a qBittorrent después de subir (src/clients.py y src/utils/upload.py)."""

import asyncio

import pytest
import torf

from src import clients
from src.utils import upload


class Torrent:
    infohash = "a" * 40
    mode = "singlefile"
    name = "x"

    def dump(self):
        return b"d4:infod4:name1:xee"


class Qbit:
    """Un qBittorrent que acepta todo y solo enseña el torrent si `aparece`."""

    aparece = True
    login = True
    anadidos: list = []
    # Lo que va enseñando torrents_info tras la comprobación: (state, progress) por consulta; el último se repite.
    estados: list = [("stoppedUP", 1)]
    arrancados: list = []
    reanudados: list = []
    ya_estaba = False

    def __init__(self, **_kwargs):
        self._consultas = 0

    def auth_log_in(self):
        if not self.login:
            raise clients.qbittorrentapi.LoginFailed()

    respuesta = "Ok."

    def torrents_add(self, **kwargs):
        Qbit.anadidos.append(kwargs)
        return Qbit.respuesta

    def torrents_info(self, torrent_hashes):
        if not self.aparece or not (Qbit.anadidos or Qbit.ya_estaba):
            return []
        estado, progreso = Qbit.estados[min(self._consultas, len(Qbit.estados) - 1)]
        self._consultas += 1
        return [{"hash": torrent_hashes, "state": estado, "progress": progreso}]

    def torrents_resume(self, infohash):
        Qbit.reanudados.append(infohash)

    def torrents_recheck(self, torrent_hashes):
        pass

    def torrents_start(self, torrent_hashes):
        Qbit.arrancados.append(torrent_hashes)


@pytest.fixture
def qbit(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.qbittorrentapi, "Client", Qbit)
    monkeypatch.setattr(Qbit, "anadidos", [])
    monkeypatch.setattr(Qbit, "arrancados", [])
    monkeypatch.setattr(Qbit, "reanudados", [])
    monkeypatch.setattr(Qbit, "estados", [("stoppedUP", 1)])
    monkeypatch.setattr(Qbit, "ya_estaba", False)
    monkeypatch.setattr(Qbit, "respuesta", "Ok.")

    reloj = [0.0]  # sin esperas de verdad: el reloj avanza lo que se «duerme»

    async def sin_espera(segundos):
        reloj[0] += segundos

    monkeypatch.setattr(clients.asyncio, "sleep", sin_espera)
    monkeypatch.setattr(clients.time, "monotonic", lambda: reloj[0])
    cliente = {"qbit_url": "http://127.0.0.1", "qbit_port": 8080, "qbit_user": "u", "qbit_pass": "p"}

    def anadir(meta=None, torrent=None, ruta=None):
        c = clients.Clients(config={"TRACKERS": {}})
        return asyncio.run(c._add_to_qbittorrent(ruta or tmp_path, tmp_path / "t.torrent", torrent or Torrent(), meta or {},
                                                 "/l", "/r", cliente, "EMU"))

    return anadir


def test_si_qbittorrent_lo_muestra_se_da_por_anadido(qbit, capsys):
    assert qbit() is True
    assert "Added to:" in capsys.readouterr().out
    assert Qbit.anadidos[0]["is_skip_checking"] is True  # recién hasheado: no hace falta comprobarlo


def test_sin_solo_cliente_entra_arrancado(qbit):
    assert qbit() is True
    assert Qbit.anadidos[0]["is_stopped"] is False and Qbit.reanudados and Qbit.arrancados == []


def test_con_solo_cliente_entra_parado_y_arranca_si_la_comprobacion_da_100(qbit, capsys):
    Qbit.estados = [("checkingUP", 0.4), ("checkingUP", 0.9), ("stoppedUP", 1)]
    assert qbit({"solo_cliente": 42}) is True
    assert Qbit.anadidos[0]["is_skip_checking"] is False and Qbit.anadidos[0]["is_stopped"] is True
    assert Qbit.reanudados == [] and Qbit.arrancados == [Torrent.infohash]
    assert "Added to:" in capsys.readouterr().out


def test_con_solo_cliente_se_queda_parado_si_faltan_piezas(qbit, capsys):
    # Mismo nombre y tamaño pero otros bytes: arrancarlo bajaría piezas encima de los hardlinks.
    Qbit.estados = [("checkingDL", 0.5), ("stoppedDL", 0.97)]
    assert qbit({"solo_cliente": 42}) is False
    assert Qbit.arrancados == [] and Qbit.reanudados == []
    out = capsys.readouterr().out
    assert "se queda parado" in out and "Added to:" not in out


def test_con_solo_cliente_espera_a_que_empiece_la_comprobacion(qbit, monkeypatch):
    # Justo tras pedirla, qBittorrent aún enseña el estado de antes: no se decide con eso.
    reloj = iter(range(0, 1000, 3))
    monkeypatch.setattr(clients.time, "monotonic", lambda: next(reloj))
    Qbit.estados = [("stoppedDL", 0), ("checkingDL", 0.2), ("stoppedUP", 1)]
    assert qbit({"solo_cliente": 42}) is True


def test_un_torrent_de_carpeta_cuya_raiz_es_path_va_en_la_carpeta_de_encima(qbit, tmp_path):
    serie = tmp_path / "Serie"
    serie.mkdir()

    class DeCarpeta(Torrent):
        mode, name = "multifile", "Serie"

    assert qbit({"category": "TV", "video_files": ["uno.mkv"]}, torrent=DeCarpeta(), ruta=serie) is True
    assert Qbit.anadidos[0]["save_path"].rstrip("/").endswith(tmp_path.name)  # no .../Serie/Serie


def test_credenciales_malas_no_se_dan_por_anadido(qbit, capsys, monkeypatch):
    monkeypatch.setattr(Qbit, "login", False)
    assert qbit() is False
    assert "INCORRECT QBIT LOGIN CREDENTIALS" in capsys.readouterr().out
    assert Qbit.anadidos == []


@pytest.mark.parametrize(("extra", "esperado"), [({}, True), ({"no_seed": True}, False), ({"client": "none"}, False)])
def test_add_to_client_dice_si_lo_ha_anadido(qbit, tmp_path, extra, esperado):
    contenido = tmp_path / "Peli.mkv"
    contenido.write_bytes(b"x" * 100)
    (tmp_path / "tmp" / "u").mkdir(parents=True)
    t = torf.Torrent(path=contenido, private=True)
    t.generate()
    t.write(tmp_path / "tmp" / "u" / "[EMU]Peli.torrent")
    config = {
        "DEFAULT": {"default_torrent_client": "qbit"},
        "TORRENT_CLIENTS": {"qbit": {"torrent_client": "qbit", "qbit_url": "http://127.0.0.1", "qbit_port": 8080,
                                     "qbit_user": "u", "qbit_pass": "p"}},
        "TRACKERS": {},
    }
    meta = {"base_dir": str(tmp_path), "uuid": "u", "clean_name": "Peli", "path": str(contenido), **extra}
    assert asyncio.run(clients.Clients(config=config).add_to_client(meta, "EMU")) is esperado


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


def test_con_solo_cliente_no_toca_un_torrent_que_ya_esta(qbit, capsys):
    # Su 100 % sería de antes, no de esta comprobación.
    Qbit.ya_estaba = True
    assert qbit({"solo_cliente": 42}) is False
    assert Qbit.anadidos == [] and Qbit.arrancados == []
    assert "ya está en qBittorrent" in capsys.readouterr().out


@pytest.mark.parametrize("tipo", ["rtorrent", "deluge", "transmission", "watch"])
def test_con_solo_cliente_otro_cliente_ni_se_intenta(tmp_path, monkeypatch, capsys, tipo):
    contenido = tmp_path / "x.mkv"
    contenido.write_bytes(b"x" * 100)
    (tmp_path / "tmp" / "u").mkdir(parents=True)
    torrent = torf.Torrent(path=contenido, private=True)
    torrent.generate()
    torrent.write(tmp_path / "tmp" / "u" / "[EMU]x.torrent")
    c = clients.Clients(config={"TRACKERS": {}, "DEFAULT": {"default_torrent_client": "c"},
                                "TORRENT_CLIENTS": {"c": {"torrent_client": tipo}}})
    llamados = []

    async def metodo(*_a):
        llamados.append(tipo)
        return True

    for nombre in ("_add_to_rtorrent", "_add_to_deluge", "_add_to_transmission", "_add_to_watch_folder"):
        monkeypatch.setattr(c, nombre, metodo)
    meta = {"base_dir": str(tmp_path), "uuid": "u", "clean_name": "x", "path": str(tmp_path / "x.mkv"), "solo_cliente": 42}
    assert asyncio.run(c.add_to_client(meta, "EMU")) is False
    assert llamados == [] and "solo funciona con qBittorrent" in capsys.readouterr().out


def test_con_solo_cliente_no_sigue_si_qbittorrent_no_lo_anade(qbit, capsys):
    # Otro lo añadió entre la consulta y el add: «Fails.» y ese torrent no entró parado.
    Qbit.respuesta = "Fails."
    assert qbit({"solo_cliente": 42}) is False
    assert Qbit.arrancados == [] and Qbit.reanudados == []
    assert "no lo ha añadido (Fails.)" in capsys.readouterr().out


@pytest.mark.parametrize(("respuesta", "esperado"), [
    ({"success_count": 1, "failure_count": 0, "pending_count": 0}, True),
    ({"success_count": 0, "failure_count": 0, "pending_count": 1}, True),  # 5.2: asíncrono
    ({"success_count": 0, "failure_count": 1, "pending_count": 0}, False),
], ids=["json-ok", "json-pendiente", "json-fallo"])
def test_con_solo_cliente_entiende_la_respuesta_json_de_qbittorrent_5_2(qbit, respuesta, esperado):
    Qbit.respuesta = respuesta
    assert qbit({"solo_cliente": 42}) is esperado
    assert Qbit.arrancados == ([Torrent.infohash] if esperado else [])


def test_con_solo_cliente_no_toca_el_que_aparece_arrancado(qbit, capsys):
    # El add asíncrono falló y el que aparece es el de otro, ya en marcha.
    Qbit.estados = [("uploading", 1)]
    assert qbit({"solo_cliente": 42}) is False
    assert Qbit.arrancados == []
    assert "no está parado (uploading)" in capsys.readouterr().out

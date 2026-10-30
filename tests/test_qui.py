"""Añadir a qBittorrent a través del proxy de qui (src/clients.py), contra un qui falso en local."""

import asyncio
import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src import clients

HASH = "a" * 40
_connect = socket.socket.connect


def _solo_local(sock, address):
    if address[0] not in ("127.0.0.1", "::1"):
        raise RuntimeError(f"los tests solo pueden conectar con el qui falso, no con {address[0]}")
    return _connect(sock, address)


def _sin_procesos(*_args, **_kwargs):
    raise RuntimeError("los tests no pueden lanzar procesos")


@pytest.fixture(autouse=True)
def sin_red_ni_procesos(monkeypatch):
    """Sustituye al de conftest: el qui falso escucha en localhost y es lo único a lo que se conecta."""
    monkeypatch.setattr(socket.socket, "connect", _solo_local)
    monkeypatch.setattr(subprocess, "Popen", _sin_procesos)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _sin_procesos)


class Torrent:
    infohash = HASH

    def dump(self):
        return b"d4:infod4:name1:xee"


@pytest.fixture
def qui():
    """Contesta como qBittorrent detrás de /proxy/<clave>/ y apunta cada petición."""
    peticiones = []
    respuestas = {
        "auth/login": "Ok.",
        "app/webapiVersion": "2.11.4",
        "app/version": "v5.1.0",
        "torrents/add": "Ok.",
        "torrents/info": json.dumps([{"hash": HASH}]),
    }

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            longitud = int(self.headers.get("Content-Length") or 0)
            peticiones.append((self.command, self.path.split("?")[0], self.rfile.read(longitud)))
            cuerpo = next((r for ruta, r in respuestas.items() if self.path.split("?")[0].endswith(ruta)), "").encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        do_GET = do_POST = _serve

        def log_message(self, *_args):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}/proxy/clave-de-qui", peticiones
    servidor.shutdown()


def test_con_qui_todo_va_por_el_proxy_y_sin_credenciales(qui, tmp_path, capsys):
    url, peticiones = qui
    cliente = {"qui_proxy_url": url + "/"}  # sin qbit_url/qbit_user/qbit_pass: con qui no hacen falta

    asyncio.run(clients.Clients(config={"TRACKERS": {}})._add_to_qbittorrent(
        tmp_path, tmp_path / "t.torrent", Torrent(), {}, "/l", "/r", cliente, "EMU"))

    rutas = [ruta for _metodo, ruta, _cuerpo in peticiones]
    assert "Added to:" in capsys.readouterr().out
    assert all(ruta.startswith("/proxy/clave-de-qui/api/v2/") for ruta in rutas)
    assert {"/proxy/clave-de-qui/api/v2/auth/login", "/proxy/clave-de-qui/api/v2/torrents/add"} <= set(rutas)
    assert not any(b"password=p" in cuerpo or b"username=u" in cuerpo for _m, _r, cuerpo in peticiones)


def test_sin_qui_usa_qbit_url_y_credenciales(monkeypatch):
    creado = {}
    monkeypatch.setattr(clients.qbittorrentapi, "Client", lambda **kwargs: creado.update(kwargs))
    clients.Clients._qbit_client({"qbit_url": "http://qbit", "qbit_port": 8080, "qbit_user": "u", "qbit_pass": "p"})
    assert creado == {"host": "http://qbit", "port": 8080, "username": "u", "password": "p",
                      "VERIFY_WEBUI_CERTIFICATE": True}

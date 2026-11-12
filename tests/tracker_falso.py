"""Un UNIT3D falso en localhost para los tests: guarda cada petición, con el cuerpo multipart del POST ya separado."""

import json
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def partes_multipart(content_type: str, cuerpo: bytes) -> tuple[dict, dict]:
    """(campos de texto, ficheros) de un cuerpo multipart/form-data."""
    mensaje = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + cuerpo)
    campos, ficheros = {}, {}
    for parte in mensaje.iter_parts():
        nombre = parte.get_param("name", header="content-disposition")
        if parte.get_filename() is not None:
            ficheros[nombre] = parte.get_payload(decode=True)
        else:
            campos[nombre] = parte.get_payload(decode=True).decode("utf-8")
    return campos, ficheros


class FakeTracker:
    """Cada prueba pone en routes[(método, ruta)] una función query -> (status, cuerpo, *(cabecera, valor)).

    `bodies` guarda, por cada POST, (campos, ficheros); `last_body` es el del que se está contestando."""

    def __init__(self):
        self.requests = []
        self.bodies = []
        self.last_body = ({}, {})
        self.routes = {}
        tracker = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                parts = urlsplit(self.path)
                query = parse_qs(parts.query, keep_blank_values=True)
                cuerpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                tracker.requests.append((self.command, parts.path, query))
                if self.command == "POST":
                    tracker.last_body = partes_multipart(self.headers.get("Content-Type", ""), cuerpo)
                    tracker.bodies.append(tracker.last_body)
                status, body, *headers = tracker.routes[(self.command, parts.path)](query)
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    for name, value in headers:
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # el cliente ya se fue por timeout

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # Con la IP, "/127." pasaría por el id del torrent que se saca de la URL.
        self.url = f"http://localhost:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self, method, path):
        return [query for m, p, query in self.requests if (m, p) == (method, path)]

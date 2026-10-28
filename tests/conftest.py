"""Los tests corren sin red, sin claves y sin ffmpeg/mediainfo/mono: cualquier
intento de salir fuera falla en el acto en vez de colgarse o depender del equipo."""
from __future__ import annotations

import asyncio
import socket
import subprocess

import pytest


class SalidaBloqueada(RuntimeError):
    pass


def _bloquear(*_args, **_kwargs):
    raise SalidaBloqueada("los tests no pueden usar la red ni lanzar procesos: haz mock")


@pytest.fixture(autouse=True)
def sin_red_ni_procesos(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _bloquear)
    monkeypatch.setattr(socket, "create_connection", _bloquear)
    monkeypatch.setattr(subprocess, "Popen", _bloquear)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _bloquear)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _bloquear)


class FicheroFalso:
    """Sustituto de un fichero de aiofiles: guarda lo que se escribe."""

    def __init__(self):
        self.partes: list[str] = []

    async def write(self, texto: str) -> None:
        self.partes.append(texto)

    @property
    def texto(self) -> str:
        return "".join(self.partes)


@pytest.fixture
def fichero():
    return FicheroFalso()

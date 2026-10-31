"""Opciones de la línea de órdenes que no usa emuwarez-uploadrr: banderas por elemento, -episode, -d/-df/-pb y -pr."""
from __future__ import annotations

import asyncio

import pytest
import requests

import upload
from src.args import apply_special_logic, build_parser, normalize_args
from src.cleanrr import CleanupHandler
from src.utils import descgen
from src.utils.descgen import DescGenerator

CONFIG = {"AUTO": {"always_aid": False}, "TRACKERS": {}, "SIGNATURES": {"use_global_sigs": "default", "pr_signature": "[PR]"}}


class Respuesta:
    text = "Desde pastebin"

    def __init__(self, status):
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(self.status)


def _meta(argv):
    return apply_special_logic(normalize_args(build_parser({}).parse_args(argv)))


def _descripcion(tmp_path, argv, **extra):
    meta = {**_meta(argv), "uuid": "Peli", **extra}
    meta = asyncio.run(DescGenerator(CONFIG, tmp_path).gen_desc(meta))
    return meta, (tmp_path / "tmp" / "Peli" / "DESCRIPTION.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("antes", "despues", "pantallas"),
    [
        (["-s", "4"], ["-ua", "-tk", "EMU"], 4),
        (["-s", "3"], ["-ua", "-tk", "EMU", "-s", "5"], 3),  # 3 es el valor por defecto, pero lo pide el elemento
    ],
)
def test_las_banderas_de_un_elemento_no_pisan_las_globales(tmp_path, monkeypatch, antes, despues, pantallas):
    video = tmp_path / "Peli.mkv"
    video.write_bytes(b"")
    cola = []

    async def procesar(self, queue, *_args):
        cola.extend(queue)

    monkeypatch.setattr(CleanupHandler, "_register_signal_handlers", lambda self: None)
    monkeypatch.setattr(upload.Uploadrr, "_process_queue", procesar)
    monkeypatch.setattr(upload.sys, "argv", ["upload.py", *antes, str(video), *despues])

    asyncio.run(upload.Uploadrr({"TRACKERS": {"EMU": {"api_key": "x"}}, "DEFAULT": {}}).do_the_thing(tmp_path))

    (meta,) = cola
    assert (meta["screens"], meta["unattended"], meta["trackers"]) == (pantallas, True, ["EMU"])


def test_rt_es_un_numero():
    assert _meta(["-rt", "2"])["randomized"] == 2


@pytest.mark.parametrize(
    ("argv", "temporada", "episodio"),
    [
        (["-episode", "5"], None, "E05"),
        (["-season", "2", "-episode", "5"], "S02", "E05"),
        (["-season", "0"], "S00", None),
    ],
)
def test_un_numero_solo_es_del_argumento_que_lo_trae(argv, temporada, episodio):
    meta = _meta(argv)
    assert (meta.get("manual_season"), meta.get("manual_episode")) == (temporada, episodio)


def test_d_escribe_la_descripcion_una_vez(tmp_path):
    meta, texto = _descripcion(tmp_path, ["-d", "Hola", "mundo"])
    assert texto == "\nHola mundo\n"
    assert meta["description"] == "CUSTOM"


def test_d_igual_al_txt_automatico_no_se_repite(tmp_path):
    video = tmp_path / "Peli.mkv"
    video.write_bytes(b"")
    video.with_suffix(".txt").write_text("Notas", encoding="utf-8")

    _, texto = _descripcion(tmp_path, ["-aid", "-d", "Notas"], path=str(video))

    assert texto == "\n# USE: full\nNotas\n"


def test_df_escribe_el_fichero(tmp_path):
    fichero = tmp_path / "desc.txt"
    fichero.write_text("Desde fichero", encoding="utf-8")

    _, texto = _descripcion(tmp_path, ["-df", str(fichero)])

    assert texto == "\nDesde fichero\n"


def test_pb_descarga_el_raw_del_pastebin(tmp_path, monkeypatch):
    pedidas = []
    monkeypatch.setattr(descgen.requests, "get", lambda url, timeout: pedidas.append(url) or Respuesta(200))

    _, texto = _descripcion(tmp_path, ["-pb", "https://pastebin.com/abc123"])

    assert pedidas == ["https://pastebin.com/raw/abc123"]
    assert texto == "Desde pastebin\n"


def test_pb_con_error_no_escribe_la_pagina_de_error(tmp_path, monkeypatch):
    monkeypatch.setattr(descgen.requests, "get", lambda url, timeout: Respuesta(404))

    with pytest.raises(requests.HTTPError):
        _descripcion(tmp_path, ["-pb", "https://pastebin.com/abc123"])


def test_pr_pone_la_firma_de_personal_release(fichero):
    meta = _meta(["-pr"])
    asyncio.run(DescGenerator(CONFIG, None)._write_signature(fichero, meta, "EMU"))
    assert fichero.texto == "\n[PR]"

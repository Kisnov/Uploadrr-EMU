"""TMDb con el id que da la app (-tmdb, -c, -ua): un fallo no cambia de obra ni pregunta por teclado."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.databases import tmdb as modulo
from src.databases.tmdb import TMDbHandler


@pytest.fixture
def tmdb(monkeypatch):
    handler = TMDbHandler({"DEFAULT": {"tmdb": "x"}})

    async def nada(*_a, **_k):
        return None

    handler.rate_limit = nada
    monkeypatch.setattr(modulo.asyncio, "sleep", nada)
    return handler


def test_un_corte_no_cambia_la_categoria_del_id_dado(tmdb):
    """tv/603 y movie/603 son obras distintas: reintentar con la otra categoría subía los datos de otra."""
    pedidas = []

    async def get(endpoint, params):
        pedidas.append(endpoint)
        raise httpx.ReadTimeout("corte")

    tmdb.client.get = get
    meta = asyncio.run(tmdb.tmdb_other_meta({"category": "MOVIE", "tmdb": "603", "tmdb_manual": "603", "unattended": True}))

    assert set(pedidas) == {"/movie/603"}
    assert meta["category"] == "MOVIE"


def test_un_404_desatendido_no_pregunta(tmdb, monkeypatch):
    def preguntar(*_a, **_k):
        raise AssertionError("no hay teclado")

    async def get(endpoint, params):
        peticion = httpx.Request("GET", "https://api.themoviedb.org/3" + endpoint)
        raise httpx.HTTPStatusError("404", request=peticion, response=httpx.Response(404, request=peticion))

    monkeypatch.setattr(modulo.Prompt, "ask", preguntar)
    tmdb.client.get = get
    meta = asyncio.run(tmdb.tmdb_other_meta({"category": "MOVIE", "tmdb": "603", "tmdb_manual": "603", "unattended": True}))

    assert meta["tmdb_not_found"] is True

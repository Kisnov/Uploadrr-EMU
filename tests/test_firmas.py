"""Firmas de la descripción (SIGNATURES / use_global_sigs, src/utils/descgen.py).

El fork usa `use_global_sigs: default` con su firma propia en SIGNATURES."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.utils.descgen import DescGenerator

GLOBAL = {
    "signature": "[GLOBAL]",
    "anon_signature": "[GLOBAL-ANON]",
    "pr_signature": "[GLOBAL-PR]",
    "anon_pr_signature": "[GLOBAL-ANON-PR]",
}


def _firma(fichero, *, modo="default", tracker_cfg=None, meta=None):
    config = {"SIGNATURES": {"use_global_sigs": modo, **GLOBAL}, "TRACKERS": {"EMU": tracker_cfg or {}}}
    asyncio.run(DescGenerator(config, Path("."))._write_signature(fichero, meta or {}, "EMU"))
    return fichero.texto


def test_por_defecto_sale_la_firma_global(fichero):
    assert _firma(fichero) == "\n[GLOBAL]"


def test_por_defecto_la_del_tracker_gana(fichero):
    assert _firma(fichero, tracker_cfg={"signature": "[EMU]"}) == "\n[EMU]"


@pytest.mark.parametrize("meta, esperada", [
    ({"anon": 1}, "[GLOBAL-ANON]"),
    ({"personalrelease": True}, "[GLOBAL-PR]"),
    ({"anon": 1, "personalrelease": True}, "[GLOBAL-ANON-PR]"),
])
def test_anonima_y_personal_release(fichero, meta, esperada):
    assert _firma(fichero, meta=meta) == f"\n{esperada}"


def test_el_tracker_anonimo_usa_la_firma_anonima(fichero):
    assert _firma(fichero, tracker_cfg={"anon": True}) == "\n[GLOBAL-ANON]"


def test_null_no_pone_firma(fichero):
    assert _firma(fichero, modo=None, tracker_cfg={"signature": "[EMU]"}) == ""


def test_la_config_de_ejemplo_lleva_la_firma_del_fork():
    import yaml

    config = yaml.safe_load(Path("data/backup/example_config.yaml").read_text(encoding="utf-8"))
    sigs = config["SIGNATURES"]
    assert sigs["use_global_sigs"] == "default"
    assert "uploadrr-signature.webp" in sigs["signature"]
    assert "uploadrr-signature.webp" in sigs["pr_signature"]


@pytest.mark.xfail(raises=UnboundLocalError, strict=True,
                   reason="BUG: tracker_config solo se define en la rama default; con true revienta")
def test_true_usa_solo_la_global(fichero):
    assert _firma(fichero, modo=True, tracker_cfg={"signature": "[EMU]"}) == "\n[GLOBAL]"


@pytest.mark.xfail(raises=UnboundLocalError, strict=True,
                   reason="BUG: tracker_config solo se define en la rama default; con false revienta")
def test_false_usa_la_del_tracker(fichero):
    assert _firma(fichero, modo=False, tracker_cfg={"signature": "[EMU]"}) == "\n[EMU]"


@pytest.mark.xfail(strict=True,
                   reason="BUG: busca 'anon_pr_sig' en el tracker en vez de 'anon_pr_signature', y cae en la global")
def test_la_firma_anonima_pr_del_tracker_se_respeta(fichero):
    meta = {"anon": 1, "personalrelease": True}
    assert _firma(fichero, tracker_cfg={"anon_pr_signature": "[EMU-ANON-PR]"}, meta=meta) == "\n[EMU-ANON-PR]"

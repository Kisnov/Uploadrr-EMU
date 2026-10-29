"""Transformaciones de BBCode (src/utils/bbcode.py)."""
from __future__ import annotations

import pytest

from src.utils.bbcode import BBCODE

bb = BBCODE()


@pytest.mark.parametrize("metodo, entrada, salida", [
    ("convert_pre_to_code", "[pre]x[/pre]", "[code]x[/code]"),
    ("convert_code_to_pre", "[code]x[/code]", "[pre]x[/pre]"),
    ("convert_hide_to_spoiler", "[hide=A]x[/hide]", "[spoiler=A]x[/spoiler]"),
    ("convert_spoiler_to_hide", "[spoiler]x[/spoiler]", "[hide]x[/hide]"),
    ("convert_spoiler_to_code", "[spoiler]x[/spoiler]", "[code]x[/code]"),
    ("convert_code_to_quote", "[code]x[/code]", "[quote]x[/quote]"),
    ("convert_note_to_quote", "[note]x[/note]", "[quote]x[/quote]"),
    ("remove_spoiler", "[spoiler=A]x[/spoiler]", "x"),
    ("convert_to_align", "[center]a[/center][left]b[/left]", "[align=center]a[/align][align=left]b[/align]"),
    ("gazelle_bbcode", "[center][spoiler]x[/spoiler][/center]", "[align=center][hide]x[/hide][/align]"),
])
def test_conversiones_simples(metodo, entrada, salida):
    assert getattr(bb, metodo)(entrada) == salida


def test_comparacion_a_desplegable():
    desc = "[comparison=Source, Encode]https://i.x/1.png https://i.x/2.png[/comparison]"
    out = bb.convert_comparison_to_collapse(desc, 1000)
    assert out.startswith("[spoiler=Source vs Encode][center]Source | Encode[/center]\n")
    assert "[url=https://i.x/1.png][img=350]https://i.x/1.png[/img][/url][url=https://i.x/2.png]" in out


def test_comparacion_centrada_ajusta_el_tamano():
    desc = "[comparison=A,B,C]https://i.x/1.png,https://i.x/2.png,https://i.x/3.png[/comparison]"
    out = bb.convert_comparison_to_centered(desc, 600)
    assert out.startswith("[center]A | B | C\n") and "[img=200]" in out


def test_desplegable_con_seis_imagenes_pasa_a_comparacion():
    imgs = "".join(f"[img]https://i.x/{i}.png[/img]" for i in range(6))
    tag = f"[spoiler=Source vs Encode]{imgs}[/spoiler]"
    out = bb.convert_collapse_to_comparison(tag, "spoiler", [tag])
    assert out.startswith("[comparison=Source, Encode]https://i.x/0.png\n")


def test_desplegable_con_pocas_imagenes_se_queda():
    tag = "[spoiler=A vs B][img]https://i.x/0.png[/img][/spoiler]"
    assert bb.convert_collapse_to_comparison(tag, "spoiler", [tag]) == tag


def test_limpiar_pre():
    assert bb.extrac_n_clean_pre_content("[pre][b]x[/b] [url=y]z[/url][/pre]") == "[pre]x z[/pre]"


def test_descripcion_de_ptp():
    desc = "[align=center][size=4]Hola &amp; adiós[/size][/align]\r\n[quote]q[/quote][hr]\n\n\n[mediainfo]MI[/mediainfo]"
    assert bb.clean_ptp_description(desc, "") == "Hola & adiós\n[code]q[/code]"


def test_descripcion_de_ptp_de_bdmv_sin_mediainfo_queda_vacia():
    """Solo BDMV: un DVD entra antes por la rama `is_disc != "BDMV"`, así que el
    "DVD" de la condición siguiente no se evalúa nunca."""
    assert bb.clean_ptp_description("texto", "BDMV") == ""
    assert bb.clean_ptp_description("texto", "DVD") == "texto"


def test_descripcion_unit3d_saca_las_imagenes_y_quita_el_center_vacio():
    desc = "Texto\n[center][url=https://h.x/v/1][img]https://h.x/i/1.png[/img][/url][/center]"
    out, imagenes = bb.clean_unit3d_description(desc, "https://tracker.x/torrents/1")
    assert out == "Texto"
    assert imagenes == [{"img_url": "https://h.x/i/1.png", "raw_url": "https://h.x/i/1.png", "web_url": "https://h.x/v/1"}]


@pytest.mark.xfail(strict=True, reason="BUG: los enlaces de imagen se pasan a minúsculas y los hosts distinguen mayúsculas")
def test_descripcion_unit3d_respeta_las_mayusculas_de_las_imagenes():
    desc = "[url=https://h.x/v/AbC][img]https://h.x/i/AbC.png[/img][/url]"
    _, imagenes = bb.clean_unit3d_description(desc, "https://tracker.x/torrents/1")
    assert imagenes[0]["raw_url"] == "https://h.x/i/AbC.png"


@pytest.mark.xfail(strict=True,
                   reason="BUG: la regex usa '{site_netloc}' literal (no es f-string) y nunca quita los enlaces del propio tracker")
def test_descripcion_unit3d_quita_los_enlaces_al_propio_tracker():
    desc = "Ver [url=https://tracker.x/torrents/9]otra subida[/url]"
    out, _ = bb.clean_unit3d_description(desc, "https://tracker.x/torrents/1")
    assert out == "Ver otra subida"

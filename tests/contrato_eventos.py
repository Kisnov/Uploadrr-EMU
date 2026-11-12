"""Valida un evento contra contrato_eventos.json (en los tests; en ejecución no se valida)."""

import json
from pathlib import Path

CONTRATO = json.loads((Path(__file__).resolve().parents[1] / "contrato_eventos.json").read_text(encoding="utf-8"))
TIPOS = {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list, "null": type(None)}


def _es(valor, tipo) -> bool:
    return any(isinstance(valor, TIPOS[t]) and not (t == "integer" and isinstance(valor, bool)) for t in ([tipo] if isinstance(tipo, str) else tipo))


def _campo(nombre, valor, spec) -> None:
    assert _es(valor, spec["tipo"]), f"{nombre}={valor!r} no es {spec['tipo']}"
    if "valores" in spec:
        assert valor in CONTRATO["valores"][spec["valores"]], f"{nombre}={valor!r} no está en {spec['valores']}"
    if "min" in spec:
        assert spec["min"] <= valor <= spec["max"], f"{nombre}={valor!r} fuera de rango"


def validar(evento: dict) -> None:
    comunes = CONTRATO["comunes"]
    assert evento.get("v") == comunes["v"]["valor"]
    assert isinstance(evento.get("ts"), str)
    assert evento.get("ev") in CONTRATO["eventos"], f"evento desconocido: {evento.get('ev')!r}"
    spec = CONTRATO["eventos"][evento["ev"]]
    campos = {**spec["requeridos"], **spec["opcionales"]}
    assert set(spec["requeridos"]) <= set(evento), f"faltan {set(spec['requeridos']) - set(evento)}"
    sobran = set(evento) - set(comunes) - set(campos)
    assert not sobran, f"campos fuera del contrato: {sobran}"
    for nombre, valor in evento.items():
        if nombre in campos:
            _campo(nombre, valor, campos[nombre])
    if evento["ev"] == "aviso":
        datos = spec["datos_por_codigo"][evento["codigo"]]
        assert set(evento["datos"]) == set(datos)
        for nombre, valor in evento["datos"].items():
            assert _es(valor, datos[nombre]["tipo"])
            if datos[nombre]["tipo"] == "array":
                assert all(_es(x, datos[nombre]["items"]) for x in valor)


def validar_linea_final(eventos: list) -> None:
    """Cada ejecución acaba con exactamente un resultado, y es la última línea."""
    assert [e["ev"] for e in eventos].count("resultado") == 1 and eventos[-1]["ev"] == "resultado"

"""Canal de eventos JSON Lines para quien lanza Uploadrr (``--eventos RUTA``); el esquema es contrato_eventos.json.

Sin la opción todo son no-ops. Un evento nunca lleva secretos (ni claves, ni URLs con ellas, ni valores de config)."""

import json
import os
from datetime import datetime, timezone
from typing import Any, List, Optional

from src.utils.console import log

VERSION = 1

_ruta: Optional[str] = None
_avisado = False  # el fallo de escritura se registra una sola vez
_resultado = False  # ya hay un resultado para el ítem en curso
_pct: dict = {}  # último porcentaje emitido por fase


def ruta_de_argv(argv: List[str]) -> Optional[str]:
    """La RUTA de ``--eventos RUTA`` o ``--eventos=RUTA`` en argv, antes de analizar nada más."""
    for i, arg in enumerate(argv):
        if arg == "--eventos" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--eventos="):
            return arg.split("=", 1)[1]
    return None


def configurar(ruta: Optional[str]) -> None:
    global _ruta, _avisado, _resultado
    # Absoluta: el programa cambia de directorio a mitad (mediainfo, capturas, torrent).
    _ruta, _avisado, _resultado = (os.path.abspath(ruta) if ruta else None), False, False
    _pct.clear()


def emitir(ev: str, **campos: Any) -> None:
    """Añade una línea JSON al fichero; un fallo de escritura se avisa una vez y no corta la subida."""
    global _avisado
    if not _ruta:
        return
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    try:
        linea = json.dumps({"v": VERSION, "ts": ts, "ev": ev, **campos}, ensure_ascii=False)
        with open(_ruta, "a", encoding="utf-8", newline="\n") as f:
            f.write(linea + "\n")
    except Exception as e:
        if not _avisado:
            _avisado = True
            log.warning(f"[yellow]No se pueden escribir los eventos: {type(e).__name__}[/yellow]")


def inicio(uuid: str, modo: str, reanuda: bool) -> None:
    global _resultado
    _resultado = False
    emitir("inicio", uuid=uuid, modo=modo, reanuda=bool(reanuda))


def fase(clave: str, estado: str, pct: Optional[int] = None) -> None:
    if estado == "empieza":
        _pct.pop(clave, None)
    emitir("fase", clave=clave, estado=estado, **({"pct": pct} if pct is not None else {}))


def avance(clave: str, pct: float) -> None:
    """Una fase ``avance`` como mucho por porcentaje entero."""
    entero = max(0, min(100, int(pct)))
    if entero > _pct.get(clave, -1):
        _pct[clave] = entero
        fase(clave, "avance", entero)


def resultado(ok: bool, codigo: str, detalle: Optional[str] = None) -> None:
    """Última línea de cada ítem; un segundo resultado del mismo ítem se ignora."""
    global _resultado
    if _resultado:
        return
    _resultado = True
    emitir("resultado", ok=ok, codigo=codigo, **({"detalle": detalle} if detalle else {}))


def hay_resultado() -> bool:
    return _resultado


def cliente(ok: bool, nombre: str, ruta: Optional[str] = None, motivo: Optional[str] = None) -> None:
    campos = {"ruta": ruta} if ruta else {}
    if motivo:
        campos["motivo"] = motivo
    emitir("cliente", ok=ok, cliente=nombre, **campos)


def cliente_fallo(motivo: str) -> None:
    """Un rechazo antes de llegar al cliente (--solo-cliente): fase y evento de un solo golpe."""
    fase("cliente", "empieza")
    fase("cliente", "fallida")
    cliente(False, "ninguno", motivo=motivo)

#!/usr/bin/env python3
"""Grader determinista de py_funcs: extrae bloque ```python``` y corre los asserts.

Contrato (motor/evaluate.py):
  grade(ejemplo, salida, revelar=False) -> {"score": 0..1, "ok": bool, "feedback": str}

  - Extrae el PRIMER bloque ```python ... ``` de la salida del modelo.
  - Corre expected["tests"] contra el codigo via motor.sandbox_py.
  - score = fraccion de asserts que pasan; ok = pasan todos.
  - Feedback con revelar=True: el assert que fallo y su error.
  - Feedback con revelar=False: SOLO el tipo de error
    (AssertionError, Timeout, SyntaxError, formato, bloqueado).
  - Codigo rechazado por filtros: score 0, feedback "bloqueado por seguridad".
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from motor import sandbox_py  # noqa: E402

_RE_BLOQUE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)


def extraer_bloque(salida):
    """Primer bloque ```python``` de la salida, o None si no hay."""
    if not isinstance(salida, str):
        return None
    m = _RE_BLOQUE.search(salida)
    if not m:
        return None
    codigo = m.group(1).strip()
    return codigo or None


def _tipo_de(error):
    """Tipo de error para revelar=False (solo el tipo, nunca el assert)."""
    if "SyntaxError" in error:
        return "SyntaxError"
    if "Timeout" in error or "timeout" in error:
        return "Timeout"
    primera = error.split(":", 1)[0].strip()
    if "Assert" in primera:
        return "AssertionError"
    return primera or "Error"


def grade(ejemplo, salida, revelar=False):
    ejemplo = ejemplo or {}
    expected = ejemplo.get("expected") or {}
    tests = expected.get("tests") or []
    test_imports = expected.get("test_imports") or []

    codigo = extraer_bloque(salida)
    if codigo is None:
        return {
            "score": 0.0,
            "ok": False,
            "feedback": "formato: sin bloque ```python```" if revelar else "formato",
        }

    res = sandbox_py.ejecutar(codigo, tests, test_imports)
    estado = res["estado"]
    total = res["total"]
    pasan = res["pasan"]
    errores = res.get("errores") or []

    if estado == "bloqueado":
        return {"score": 0.0, "ok": False, "feedback": "bloqueado por seguridad"}
    if estado == "formato":
        return {
            "score": 0.0,
            "ok": False,
            "feedback": "formato: sin asserts o codigo vacio" if revelar else "formato",
        }

    score = (pasan / total) if total else 0.0
    ok = bool(total) and pasan == total

    if estado == "timeout":
        feedback = (
            "Timeout (el codigo no termino en 10s)" if revelar else "Timeout"
        )
    elif not errores:
        feedback = "ok: pasan todos los asserts" if revelar else "ok"
    elif not revelar:
        feedback = _tipo_de(errores[0].get("error", ""))
    else:
        lineas = [f"{pasan}/{total} asserts pasan"]
        for e in errores:
            lineas.append(f"falla: {e.get('assert', '?')} -> {e.get('error', '?')}")
        feedback = " | ".join(lineas)

    return {"score": round(score, 6), "ok": ok, "feedback": feedback}

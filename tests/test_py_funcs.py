"""Tests de py_funcs: sandbox, timeout, grader (score/feedback) y extraccion.

Repo real intacto: los asserts del sandbox corren subprocess python -I con
timeout acortado donde hace falta (el default real es 10 s).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from motor import sandbox_py  # noqa: E402
from tasks.py_funcs import grader as grader_py  # noqa: E402

# tarea real (una fila de dev, recortada a 1 assert rapido)
DEV = ROOT / "tasks" / "py_funcs" / "dev.jsonl"


def _ejemplo_dev():
    with DEV.open(encoding="utf-8") as fh:
        return json.loads(fh.readline())


# --- sandbox: filtros estaticos ---------------------------------------------


@pytest.mark.parametrize(
    "codigo",
    [
        "import os\nprint(os.getcwd())",
        "import sys",
        "from os import path",
        "import subprocess",
        "from pathlib import Path",
        "import urllib.request",
        "import socket",
        "import shutil",
        "import ctypes",
        "import requests",
        "x = 1\nopen('f.txt', 'w')",
        "open('f.txt', 'a')",
        "eval('1+1')",
        "exec('x=1')",
        "__import__('os')",
    ],
)
def test_sandbox_bloquea_imports_y_filtros(codigo):
    res = sandbox_py.ejecutar(codigo, ["assert True"])
    assert res["estado"] == "bloqueado"
    assert res["pasan"] == 0


def test_sandbox_deja_pasar_codigo_limpio():
    res = sandbox_py.ejecutar("def f():\n  return 1", ["assert f() == 1"])
    assert res["estado"] == "ok"
    assert res["pasan"] == 1 and res["total"] == 1


# --- sandbox: timeout corta while True --------------------------------------


def test_timeout_corta_while_true():
    res = sandbox_py.ejecutar(
        "while True:\n  pass", ["assert True"], timeout=2
    )
    assert res["estado"] == "timeout"
    assert res["pasan"] == 0


# --- grader: extraccion del bloque -------------------------------------------


def test_extraer_primer_bloque_python():
    salida = (
        "Acá va la solución:\n"
        "```python\ndef f(x):\n    return x + 1\n```\n"
        "Listo.\n"
        "```python\ndef g():\n    pass\n```"
    )
    codigo = grader_py.extraer_bloque(salida)
    assert codigo == "def f(x):\n    return x + 1"
    assert "def g()" not in codigo


def test_extraer_sin_bloque_devuelve_none():
    assert grader_py.extraer_bloque("def f(): pass") is None
    assert grader_py.extraer_bloque("") is None
    assert grader_py.extraer_bloque(None) is None


# --- grader: score objetivo --------------------------------------------------


def _ejemplo_local(tests):
    return {
        "id": "t-1",
        "input": {"enunciado": "x", "firma_ejemplo": tests[0]},
        "expected": {"tests": tests, "test_imports": []},
    }


def test_grader_codigo_correcto_score_1():
    ej = _ejemplo_local(["assert doble(2) == 4", "assert doble(0) == 0"])
    salida = "```python\ndef doble(n):\n    return n * 2\n```"
    g = grader_py.grade(ej, salida, revelar=True)
    assert g["score"] == 1.0
    assert g["ok"] is True


def test_grader_codigo_incorrecto_score_menor_1():
    ej = _ejemplo_local(["assert doble(2) == 4", "assert doble(0) == 0"])
    salida = "```python\ndef doble(n):\n    return n + 2\n```"
    g = grader_py.grade(ej, salida, revelar=False)
    assert 0.0 <= g["score"] < 1.0
    assert g["ok"] is False


def test_grader_bloqueado_feedback_fijo():
    ej = _ejemplo_local(["assert True"])
    salida = "```python\nimport os\n```"
    for revelar in (False, True):
        g = grader_py.grade(ej, salida, revelar=revelar)
        assert g["score"] == 0.0
        assert g["feedback"] == "bloqueado por seguridad"


def test_grader_sin_formato():
    ej = _ejemplo_local(["assert True"])
    g = grader_py.grade(ej, "a ver, sin bloque", revelar=False)
    assert g["score"] == 0.0
    assert g["feedback"] == "formato"


# --- feedback: revelar=False no filtra asserts -------------------------------


def test_revelar_false_no_filtra_asserts():
    """revelar=False -> solo el TIPO; ni el assert ni el valor esperado."""
    ej = _ejemplo_local(["assert doble(2) == 4"])
    salida = "```python\ndef doble(n):\n    return n * 3\n```"
    g = grader_py.grade(ej, salida, revelar=False)
    assert g["feedback"] == "AssertionError"
    assert "doble" not in g["feedback"]
    assert "==" not in g["feedback"]
    assert "4" not in g["feedback"]

    g_rev = grader_py.grade(ej, salida, revelar=True)
    assert "doble(2) == 4" in g_rev["feedback"]


def test_revelar_true_muestra_assert_que_falla():
    ej = _ejemplo_local(["assert doble(2) == 4", "assert doble(3) == 6"])
    salida = "```python\ndef doble(n):\n    return n * 3\n```"
    g = grader_py.grade(ej, salida, revelar=True)
    assert "doble(2) == 4" in g["feedback"]
    assert "1/2" in g["feedback"] or "0/2" in g["feedback"]

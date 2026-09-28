"""Tests F2.0-bis: el grader no se puede enganar con objetos truchos.

El filtro de texto de dunders se esquiva (ej. '__e'+'q__'); el gate
estructural es _tipo_seguro en el hijo (ast.Compare -> operandos nativos
exactos). Nada real: sandbox local, sin red ni LLM.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from motor import sandbox_py  # noqa: E402

TRUCHA_EQ_CONCAT = (
    "T=type('T',(),{'__e'+'q__':lambda s,o:True})\n"
    "def f(x):\n"
    "    return T()\n"
)

TRUCHA_MOCK_ANY = (
    "import unittest.mock as m\n"
    "def f(x):\n"
    "    return m.ANY\n"
)

TRUCHA_CLASE_SETATTR = (
    "class C:\n"
    "    pass\n"
    "setattr(C,'__e'+'q__',lambda s,o: True)\n"
    "def f(x):\n"
    "    return C()\n"
)


def test_trucha_eq_por_concatenacion_da_0():
    res = sandbox_py.ejecutar(TRUCHA_EQ_CONCAT, ["assert f(1)==1", "assert f(2)==2"])
    assert res["pasan"] == 0
    assert res["total"] == 2
    assert res["errores"]
    assert all("tipo no nativo" in e["error"] for e in res["errores"])


def test_trucha_mock_any_da_0():
    res = sandbox_py.ejecutar(TRUCHA_MOCK_ANY, ["assert f(1)==1", "assert f(2)==2"])
    assert res["pasan"] == 0
    assert res["total"] == 2
    assert res["errores"]
    assert all("tipo no nativo" in e["error"] for e in res["errores"])


def test_clase_con_eq_trucho_da_0():
    res = sandbox_py.ejecutar(TRUCHA_CLASE_SETATTR, ["assert f(1)==1"])
    assert res["pasan"] == 0
    assert all("tipo no nativo" in e["error"] for e in res["errores"])


def test_honesta_anidada_pasa():
    codigo = (
        "def f(x):\n"
        "    return {'a': [1, (2.5, 's')], 'b': (True, None), 'c': {1, 2}}\n"
    )
    res = sandbox_py.ejecutar(
        codigo,
        ["assert f(0)=={'a': [1, (2.5, 's')], 'b': (True, None), 'c': {1, 2}}"],
    )
    assert res["estado"] == "ok"
    assert res["pasan"] == 1


def test_assert_con_isclose_sigue_funcionando():
    res = sandbox_py.ejecutar(
        "def f(x):\n    return x/3\n",
        ["assert math.isclose(f(1), 0.3333333333333333)"],
        ["import math"],
    )
    assert res["estado"] == "ok"
    assert res["pasan"] == 1


def test_falla_honesta_se_sigue_reportando():
    res = sandbox_py.ejecutar(
        "def doble(n):\n    return 2*n\n",
        ["assert doble(2)==4", "assert doble(3)==999"],
    )
    assert res["pasan"] == 1
    assert len(res["errores"]) == 1
    assert "AssertionError" in res["errores"][0]["error"]


def test_traceback_no_filtra_rutas_locales():
    """El feedback de train viaja al reflector remoto: sin usuario ni rutas locales."""
    import os
    from motor import sandbox_py
    r = sandbox_py.ejecutar("x = 1/0\ndef f(a):\n    return a", ["assert f(1) == 1"])
    blob = str(r)
    home = os.path.expanduser("~")
    assert home not in blob and home.replace("\\", "/") not in blob
    assert "siafb_sbx_" not in blob

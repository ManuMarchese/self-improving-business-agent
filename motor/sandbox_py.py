#!/usr/bin/env python3
"""Sandbox ligero para codigo Python generado (tarea py_funcs).

RIESGO ACEPTADO (ver MAPA.md): NO es un sandbox real. El codigo corre en la PC
del usuario con filtros estaticos + timeout. La proteccion real sigue siendo:
integridad por hashes + examen fuera del repo + revision de git.

Contrato:
  ejecutar(codigo, asserts, test_imports=None, timeout=10) -> dict con claves:
    - "estado": "ok" | "bloqueado" | "timeout" | "crash" | "formato"
    - "pasan":  int  (asserts que pasaron; solo confiable en estado ok/crash
                      parcial — en crash total vale 0)
    - "total":  int  (len(asserts))
    - "detalle": str (stderr/stdout truncado a 2000 chars, o motivo de bloqueo)
    - "errores": list[{"assert": str, "error": str}]  (asserts fallidos con su
                      error; vacio si estado bloqueado/timeout previo)

Filtros estaticos ANTES de ejecutar (estado "bloqueado", pasan=0):
  - import/from de: os, sys, subprocess, shutil, socket, urllib, requests,
    ctypes, pathlib (tambien submodulos: import os.path, from pathlib import ...)
  - open( en modo escritura (segundo arg o keyword mode= con w/a/x/+/b rara)
  - eval(, exec(, __import__(
  - dunders de comparacion/identidad: __eq__, __ne__, __hash__, __class__
    (trampa conocida F2.0: un objeto con __eq__ siempre True pasa
    `assert f(x) == y` sin resolver nada; feedback "bloqueado por
    seguridad: dunder de comparacion")
TODO filtro rechazado devuelve pasan=0. Nada de esto se ejecuta.
Se mantiene como defensa en profundidad (F2.0-bis no lo saca).

Gate estructural F2.0-bis (en el PROCESO HIJO, por assert, con ast):
  - Cada assert se parsea con ast ANTES de ejecutarse. Si es una
    comparacion (ast.Compare), cada operando se evalua UNA vez a un
    valor temporal y se exige _tipo_seguro(valor) para TODOS los
    operandos; recien despues se aplica la comparacion sobre los
    valores ya evaluados (sin re-llamar a la funcion candidata).
  - _tipo_seguro es recursivo y acepta SOLO tipos nativos exactos
    (type(x) is ..., nunca isinstance: una subclase con __eq__ trucho
    NO pasa): int, float, complex, bool, str, bytes, NoneType, y
    list/tuple/set/frozenset/dict cuyos elementos (y claves) tambien
    sean seguros. Cualquier otro tipo -> el assert falla con
    "tipo no nativo: <nombre>".
  - Cierra las trampas que esquivan el filtro de texto (probadas contra
    este sandbox): type('T',(),{'__e'+'q__':...}) y unittest.mock.ANY.
LIMITACION (anotada a proposito): los asserts que NO son Compare
(ej. `assert math.isclose(f(x), y)`, `assert f(x)`) se ejecutan igual
que antes, sin gate de tipos. Un objeto con __bool__ siempre True
pasaria `assert f(x)`. Alcanza con Compare porque cubre ~todo MBPP
(dev py_funcs: 188/191 asserts son Compare).

Ejecucion: subprocess `python -I` (aislado: sin site, sin env vars de usuario,
sin directorio actual en sys.path), cwd = directorio temporal VACIO, env
minimal, timeout 10 s, stdout/stderr truncados a 2000 caracteres.

Los asserts se evaluan UNO POR UNO (separa) para poder reportar la fraccion
que pasa aunque el primero falle: cada assert corre en su propio proceso.
"""
import os
import re
import subprocess
import sys
import tempfile

TIMEOUT_S = 10
TRUNCAR = 2000
MODULOS_BLOQUEADOS = (
    "os", "sys", "subprocess", "shutil", "socket",
    "urllib", "requests", "ctypes", "pathlib",
)

_RE_IMPORT = re.compile(
    r"(?m)^\s*(?:import|from)\s+([A-Za-z_][\w.]*)"
)
_RE_OPEN = re.compile(r"\bopen\s*\(")
_RE_OPEN_MODO = re.compile(
    r"""open\s*\(\s*[^,)]*\s*,\s*(?:mode\s*=\s*)?['"]([^'"]*)['"]"""
)
_RE_EVAL = re.compile(r"\beval\s*\(")
_RE_EXEC = re.compile(r"\bexec\s*\(")
_RE_DUNDER = re.compile(r"__import__\s*\(")
_RE_DUNDER_COMP = re.compile(r"__(eq|ne|hash|class)__")


def motivo_bloqueo(codigo):
    """Filtros estaticos. Devuelve str (motivo) si bloquea, o None si pasa."""
    if not isinstance(codigo, str) or not codigo.strip():
        return "formato: codigo vacio"
    for m in _RE_IMPORT.finditer(codigo):
        raiz = m.group(1).split(".")[0]
        if raiz in MODULOS_BLOQUEADOS:
            return f"bloqueado por seguridad: import de {raiz}"
    if _RE_OPEN.search(codigo):
        m = _RE_OPEN_MODO.search(codigo)
        if m is None:
            # open(x) sin modo explicito es lectura -> pasa; open(f, "w") cae aca
            # arriba. open(f, mode="w") tambien. Si hay coma pero no matcheamos
            # el modo, revisamos cualquier string con w/a/x/r+ cerca.
            cerca = re.search(
                r"""open\s*\([^)]*['"][^'"]*[wax+][^'"]*['"]""", codigo
            )
            if cerca:
                return "bloqueado por seguridad: open() en modo escritura"
        else:
            modo = m.group(1)
            if any(c in modo for c in "wax+") or modo in ("r+", "a+", "w+"):
                return "bloqueado por seguridad: open() en modo escritura"
            if re.search(r"[wax+]", modo):
                return "bloqueado por seguridad: open() en modo escritura"
    if _RE_EVAL.search(codigo):
        return "bloqueado por seguridad: eval()"
    if _RE_EXEC.search(codigo):
        return "bloqueado por seguridad: exec()"
    if _RE_DUNDER.search(codigo):
        return "bloqueado por seguridad: __import__()"
    if _RE_DUNDER_COMP.search(codigo):
        return "bloqueado por seguridad: dunder de comparacion"
    return None


def _anonimizar(texto, td):
    """Saca rutas locales de tracebacks: la carpeta temporal y el home del usuario.
    El feedback de train viaja al reflector (proveedor remoto): no debe llevar el
    nombre de usuario ni rutas de la maquina."""
    t = texto or ""
    for ruta, marca in ((td, "<sandbox>"), (os.path.expanduser("~"), "<home>")):
        if ruta:
            for variante in {ruta, ruta.replace("\\", "/"), os.path.realpath(ruta)}:
                t = t.replace(variante, marca)
    return t


def _truncar(texto, n=TRUNCAR):
    t = texto or ""
    if len(t) > n:
        return t[:n] + f"...[truncado {len(t) - n} chars]"
    return t


def _env_minimo():
    """Env minimal: solo lo indispensable para que python arranque."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    return {k: v for k, v in env.items() if v}


# Preludio que corre EN EL HIJO (despues del codigo candidato): parsea el
# assert con ast y, si es Compare, exige operandos de tipo nativo exacto.
# Los builtins se toman de `builtins` (no del namespace del modulo) para que
# un candidato que redefina eval/exec/compile/type no rompa el gate.
_PRELUDIO_TIPOS = """
import ast as _siafb_ast
import builtins as _siafb_bi
import operator as _siafb_operator
import sys as _siafb_sys
_siafb_NoneType = _siafb_bi.type(None)
def _tipo_seguro(_siafb_v):
    _siafb_t = _siafb_bi.type(_siafb_v)
    if (_siafb_t is _siafb_bi.int or _siafb_t is _siafb_bi.float
            or _siafb_t is _siafb_bi.complex or _siafb_t is _siafb_bi.bool
            or _siafb_t is _siafb_bi.str or _siafb_t is _siafb_bi.bytes
            or _siafb_t is _siafb_NoneType):
        return True
    if (_siafb_t is _siafb_bi.list or _siafb_t is _siafb_bi.tuple
            or _siafb_t is _siafb_bi.set or _siafb_t is _siafb_bi.frozenset):
        for _siafb_x in _siafb_v:
            if not _tipo_seguro(_siafb_x):
                return False
        return True
    if _siafb_t is _siafb_bi.dict:
        for _siafb_k, _siafb_x in _siafb_v.items():
            if not _tipo_seguro(_siafb_k) or not _tipo_seguro(_siafb_x):
                return False
        return True
    return False
_siafb_OPS = {
    _siafb_ast.Eq: _siafb_operator.eq,
    _siafb_ast.NotEq: _siafb_operator.ne,
    _siafb_ast.Lt: _siafb_operator.lt,
    _siafb_ast.LtE: _siafb_operator.le,
    _siafb_ast.Gt: _siafb_operator.gt,
    _siafb_ast.GtE: _siafb_operator.ge,
    _siafb_ast.Is: _siafb_operator.is_,
    _siafb_ast.IsNot: _siafb_operator.is_not,
    _siafb_ast.In: lambda _a, _b: _siafb_operator.contains(_b, _a),
    _siafb_ast.NotIn: lambda _a, _b: not _siafb_operator.contains(_b, _a),
}
def _siafb_correr(_siafb_src):
    _siafb_mod = _siafb_ast.parse(_siafb_src)
    if (_siafb_bi.len(_siafb_mod.body) == 1
            and _siafb_bi.isinstance(_siafb_mod.body[0], _siafb_ast.Assert)):
        _siafb_stmt = _siafb_mod.body[0]
        _siafb_test = _siafb_stmt.test
        if _siafb_bi.isinstance(_siafb_test, _siafb_ast.Compare):
            _siafb_nodos = [_siafb_test.left] + _siafb_bi.list(_siafb_test.comparators)
            _siafb_vals = []
            for _siafb_n in _siafb_nodos:
                _siafb_c = _siafb_bi.compile(
                    _siafb_ast.Expression(_siafb_n), "<siafb_assert>", "eval")
                _siafb_vals.append(_siafb_bi.eval(_siafb_c))
            for _siafb_v in _siafb_vals:
                if not _tipo_seguro(_siafb_v):
                    raise AssertionError(
                        "tipo no nativo: " + _siafb_bi.type(_siafb_v).__name__)
            _siafb_ok = True
            for _siafb_i, _siafb_op in enumerate(_siafb_test.ops):
                _siafb_fn = _siafb_OPS.get(_siafb_bi.type(_siafb_op))
                if _siafb_fn is None:
                    raise AssertionError(
                        "operador no soportado: " + _siafb_bi.type(_siafb_op).__name__)
                if not _siafb_fn(_siafb_vals[_siafb_i], _siafb_vals[_siafb_i + 1]):
                    _siafb_ok = False
                    break
            if not _siafb_ok:
                if _siafb_stmt.msg is not None:
                    _siafb_mc = _siafb_bi.compile(
                        _siafb_ast.Expression(_siafb_stmt.msg), "<siafb_assert>", "eval")
                    raise AssertionError(_siafb_bi.str(_siafb_bi.eval(_siafb_mc)))
                raise AssertionError(_siafb_src)
            return
    _siafb_bi.exec(_siafb_bi.compile(_siafb_mod, "<siafb_assert>", "exec"))
"""


def _script_un_assert(codigo, test_imports, un_assert):
    """Programa que importa, define y evalua UN assert; imprime OK o FAIL.

    El assert viaja como string (_siafb_assert_src) y se transforma con ast
    EN EL HIJO (ver _PRELUDIO_TIPOS): Compare -> gate de tipo nativo por
    operando; lo demas -> exec tal cual (limitacion documentada arriba).
    """
    partes = []
    if test_imports:
        partes.extend(str(x) for x in test_imports)
    partes.append(codigo)
    partes.append(_PRELUDIO_TIPOS)
    partes.append(f"_siafb_assert_src = {un_assert!r}")
    partes.append("""
try:
    _siafb_correr(_siafb_assert_src)
    _siafb_sys.stdout.write("SIAFB_OK")
except BaseException as _siafb_e:
    _siafb_sys.stdout.write("SIAFB_FAIL:" + type(_siafb_e).__name__ + ": " + str(_siafb_e))
""")
    return "\n".join(partes)


def _correr(script, timeout):
    """python -I en cwd temporal vacio, env minimal, timeout. -> (rc, out, err, timed_out)."""
    with tempfile.TemporaryDirectory(prefix="siafb_sbx_") as td:
        path = os.path.join(td, "t.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
        try:
            proc = subprocess.run(
                [sys.executable, "-I", path],
                cwd=td,
                env=_env_minimo(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
            return (proc.returncode, _anonimizar(proc.stdout or "", td),
                    _anonimizar(proc.stderr or "", td), False)
        except subprocess.TimeoutExpired as ex:
            out = ex.stdout or ""
            err = ex.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            return -1, _anonimizar(out, td), _anonimizar(err, td), True


def ejecutar(codigo, asserts, test_imports=None, timeout=TIMEOUT_S):
    """Corre los asserts contra el codigo. Ver docstring del modulo."""
    asserts = list(asserts or [])
    total = len(asserts)
    if total == 0:
        return {
            "estado": "formato", "pasan": 0, "total": 0,
            "detalle": "sin asserts", "errores": [],
        }

    motivo = motivo_bloqueo(codigo)
    if motivo:
        return {
            "estado": "bloqueado", "pasan": 0, "total": total,
            "detalle": motivo, "errores": [],
        }

    pasan = 0
    errores = []
    for un_assert in asserts:
        script = _script_un_assert(codigo, test_imports, un_assert)
        rc, out, err, timed_out = _correr(script, timeout)
        if timed_out:
            return {
                "estado": "timeout", "pasan": pasan, "total": total,
                "detalle": _truncar(f"timeout {timeout}s en: {un_assert}\n{err}"),
                "errores": errores,
            }
        if out.startswith("SIAFB_OK"):
            pasan += 1
        elif out.startswith("SIAFB_FAIL:"):
            partes = out.split(":", 2)
            tipo = partes[1] if len(partes) > 1 else "?"
            msg = partes[2] if len(partes) > 2 else ""
            errores.append({"assert": un_assert, "error": f"{tipo}: {msg}"})
        else:
            # crash antes del print (SyntaxError/NameError en tiempo de carga)
            errores.append({
                "assert": un_assert,
                "error": _truncar(err or out or f"exit {rc}"),
            })

    estado = "ok" if pasan == total else "ok"
    return {
        "estado": estado, "pasan": pasan, "total": total,
        "detalle": "", "errores": errores,
    }

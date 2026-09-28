#!/usr/bin/env python3
"""Ciclo ACE que aprende: Generador -> Reflector -> Curador -> Juez.

Solo stdlib. Uso:
  python -m motor learn --task T --parent ID --rondas K --nonce S [--lote N]

Base: "Agentic Context Engineering" (ICLR 2026): el agente mejora acumulando
un PLAYBOOK de reglas cortas con contadores util/danina, actualizado con
deltas chicos (nunca reescribiendo todo). Solo se acepta un cambio que mejora
en datos que el aprendiz NO vio (dev).

Cada ronda:
  1. GENERADOR: evalua la policy padre sobre train (lote N, default todo
     train) con revelar=True, reutilizando motor.evaluate.cmd_eval
     (results en runs_motor/..., evento en ledger).
  2. REFLECTOR (1 llamada LLM, rol rapido, via llm.complete): recibe SOLO
     datos de train: playbook actual (bullets con id) + hasta 8 fallidos
     (enunciado, salida recortada a 1500 chars, feedback completo) + 3 que
     pasaron. Pide JSON estricto:
       {"nuevas":[{"regla":"...","por_que":"..."}],
        "utiles":["b1",...], "daninas":["b3",...]}
     Maximo 3 reglas nuevas, cada una <= 200 chars, GENERALES (sobre como
     escribir funciones Python que pasen tests), no sobre un problema puntual.
     Si el JSON no parsea -> 1 reintento pidiendo solo JSON; si falla de
     nuevo, la ronda no agrega reglas (se registra).
  3. CURADOR (determinista, SIN LLM): playbook.jsonl con filas
     {"id","regla","util","danina","origen","creado"}.
      - Rechaza reglas que filtran datos: si contienen un nombre de funcion
        de los asserts del lote, un id "mbpp-", o un literal numerico/lista
        copiado de un assert -> se descarta con motivo (anti-memorizacion).
        Desde F2.2-fix: coincidencia por PALABRA COMPLETA (regex con word
        boundary), case-sensitive con el identificador exacto, e ignora los
        identificadores cortos (<= 3 letras) y los comunes (keywords +
        builtins + python/count/sort/find...). Antes matcheaba subcadenas
        sueltas: "python" en "bloque Python" y "for" en "formula" mataban
        reglas generales buenas (R1 ronda 1: 3/3 descartadas, 0 aprendidas).
      - Descarta casi-duplicados (difflib ratio > 0.8 contra existentes).
      - Suma contadores util/danina segun el reflector; poda reglas con
        danina >= util + 2; tope 25 reglas (saca las de peor util-danina).
      - JUEZ EFICIENTE (F2.2-fix): si el hijo queda IDENTICO al padre (sin
        reglas nuevas y sin cambios de contadores) -> RECHAZA directo con
        motivo "sin reglas nuevas" SIN evaluar dev (ahorra ~60 llamadas;
        era el caso real de R1 ronda 1: 60 ejemplos evaluados al pedo).
  4. HIJO: policies/T/<siguiente id> (v1, v2...) con el mismo prompt.md,
     el playbook nuevo y policy.json {padre, creada_por:"learn-ace", ronda,
     nonce}.
  5. JUEZ: evalua padre e hijo en dev con el MISMO nonce (S-dev) (si el
     padre ya tiene corrida dev con ese nonce, reusala) y corre compare.py.
     ACEPTA -> policies/T/ACTIVA pasa a apuntar al hijo. RECHAZA -> el hijo
     queda con status "rechazada" en su policy.json (no se borra: arbol de
     versiones estilo Darwin Godel Machine) y la proxima ronda parte de la
     ACTIVA.
  6. Evento "learn" en el ledger (chain_append): ronda, padre, hijo,
     reglas_agregadas, reglas_rechazadas_por_filtro(+motivos), media
     padre/hijo dev, IC pareado, decision.
  7. Si el proveedor falla (exit 4 del eval) -> la ronda se aborta limpio
     y se registra; NUNCA se acepta un hijo con eval invalida.

Codigos: 0 = termino (aunque todo RECHAZA/ABORTA) | 1 = error de uso.
"""

import builtins
import difflib
import json
import keyword
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import agent  # noqa: E402
from motor import compare, evaluate, llm  # noqa: E402

TASKS_DIR = _ROOT / "tasks"
POLICIES_DIR = _ROOT / "policies"
RUNS_MOTOR = _ROOT / "runs_motor"
EVALS_DIR = _ROOT / "evals"
LEDGER = EVALS_DIR / "ledger.jsonl"
MANIFEST = EVALS_DIR / "MANIFEST.json"

MAX_NUEVAS = 3
MAX_REGLA_LEN = 200
MAX_FALLOS = 8
MAX_PASADOS = 3
SALIDA_TRUNC = 1500
TOPE_REGLAS = 25
DUP_UMBRAL = 0.8

EXIT_OK = 0
EXIT_ERROR = 1

# --- Filtro anti-memorizacion por palabra completa (F2.2-fix) ---------------
# El filtro viejo matcheaba SUBCADENAS sueltas: "python" aparecia en casi toda
# regla general ("bloque Python"), "for" dentro de "formula", "Valid" dentro de
# "valido". Mato las 3 reglas buenas de la ronda 1 de R1 (ver RUNSTATE MOTOR V2).
# Ahora: coincidencia por PALABRA COMPLETA (regex con word boundary), case-
# sensitive con el identificador exacto, y se ignoran los identificadores que
# son demasiado cortos o palabras de uso comun para identificar un problema.

_MIN_LEN_NOMBRE = 4     # un nombre de <= 3 letras no identifica nada concreto
_MIN_LEN_STR = 6        # un literal string corto es ruido, no una huella
_MIN_DIGITOS_NUM = 3    # "123" cuenta; "12" no

_PALABRAS_COMUNES = frozenset({
    "python", "python3", "list", "string", "number", "count", "sum", "max",
    "min", "sort", "sorted", "find", "check", "get", "is", "text", "value",
    "values", "key", "keys", "item", "items", "name", "size", "len", "str",
    "int", "float", "bool", "dict", "set", "tuple", "range", "print", "abs",
    "round", "reversed", "enumerate", "zip", "map", "filter", "any", "all",
    "isinstance", "type", "repr", "ord", "chr", "open", "split", "join",
    "strip", "replace", "upper", "lower", "append", "extend", "read", "write",
    "assert", "return", "input", "output", "result", "results", "test",
    "tests", "main", "self", "cls", "func", "function", "true", "false",
    "none",
}) | frozenset(keyword.kwlist) | frozenset(
    n.lower() for n in dir(builtins) if not n.startswith("_")
)

_RE_LLAMADA = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_RE_NUM = re.compile(r"-?\b\d+(?:\.\d+)?\b")
_RE_STR = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_RE_LISTA = re.compile(r"\[[^\[\]]{2,}\]")


def _palabra_completa(tok, texto, case_sensitive=True):
    """True si `tok` aparece en `texto` como palabra completa.

    El token se escapa y se le exige un word boundary (`\\b`) en ambos bordes.
    Para tokens con signos (guiones, corchetes) el `\\b` nativo no aplica:
    se usan lookarounds negativos de `[A-Za-z0-9_]` en los bordes alfanumericos.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    escapado = re.escape(tok)
    if tok[0].isalnum() or tok[0] == "_":
        escapado = r"(?<![A-Za-z0-9_])" + escapado
    if tok[-1].isalnum() or tok[-1] == "_":
        escapado = escapado + r"(?![A-Za-z0-9_])"
    return re.search(escapado, texto, flags) is not None


def _parse_args(args):
    flags = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            flags[a[2:]] = args[i + 1]
            i += 2
        elif a.startswith("--"):
            flags[a[2:]] = True
            i += 1
        else:
            flags.setdefault("_pos", []).append(a)
            i += 1
    return flags


def _read_jsonl(path):
    rows = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            rows.append(json.loads(ln))
    return rows


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_playbook(policy_dir):
    """Filas del playbook como dicts. Linea no-JSON = regla suelta (compat)."""
    p = Path(policy_dir) / "playbook.jsonl"
    if not p.exists():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and "regla" in d:
            rows.append({
                "id": str(d.get("id", f"b{len(rows) + 1}")),
                "regla": str(d.get("regla", "")),
                "util": int(d.get("util", 0) or 0),
                "danina": int(d.get("danina", 0) or 0),
                "origen": str(d.get("origen", "")),
                "creado": str(d.get("creado", "")),
            })
    return rows


def _siguiente_num_id(rows):
    mx = 0
    for r in rows or []:
        m = re.fullmatch(r"b(\d+)", str(r.get("id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def build_reflector_prompt(playbook_rows, fallidos, pasados):
    """Prompt del reflector: SOLO datos de train. Nunca incluir dev.

    playbook_rows: [{"id","regla","util","danina",...}]
    fallidos/pasados: [{"input":dict, "output":str, "feedback":str}]
    No se incluye ningun id de ejemplo (ni train ni dev): solo enunciados.
    """
    lineas = []
    lineas.append(
        "Sos el REFLECTOR de un agente que aprende a escribir funciones "
        "Python que pasen tests. Recibis SOLO datos de ENTRENAMIENTO (train). "
        "Nunca viste los datos de validacion (dev)."
    )
    lineas.append("")
    lineas.append("PLAYBOOK ACTUAL (reglas cortas acumuladas):")
    if not playbook_rows:
        lineas.append("(vacio: todavia no hay reglas)")
    else:
        for r in playbook_rows:
            lineas.append(
                f"- {r.get('id')}: {r.get('regla')} "
                f"[util={r.get('util', 0)}, danina={r.get('danina', 0)}]"
            )
    lineas.append("")
    lineas.append(f"CASOS FALLIDOS DE TRAIN (hasta {MAX_FALLOS}):")
    if not fallidos:
        lineas.append("(ninguno: todo paso)")
    for k, c in enumerate((fallidos or [])[:MAX_FALLOS], 1):
        inp = json.dumps(c.get("input") or {}, ensure_ascii=False, sort_keys=True)
        out = str(c.get("output") or "")[:SALIDA_TRUNC]
        fb = str(c.get("feedback") or "")
        lineas.append(f"[fallo {k}] input: {inp}")
        lineas.append(f"[fallo {k}] salida: {out}")
        lineas.append(f"[fallo {k}] feedback: {fb}")
    lineas.append("")
    lineas.append(f"CASOS QUE PASARON DE TRAIN (hasta {MAX_PASADOS}):")
    if not pasados:
        lineas.append("(ninguno)")
    for k, c in enumerate((pasados or [])[:MAX_PASADOS], 1):
        inp = json.dumps(c.get("input") or {}, ensure_ascii=False, sort_keys=True)
        out = str(c.get("output") or "")[:SALIDA_TRUNC]
        fb = str(c.get("feedback") or "")
        lineas.append(f"[ok {k}] input: {inp}")
        lineas.append(f"[ok {k}] salida: {out}")
        lineas.append(f"[ok {k}] feedback: {fb}")
    lineas.append("")
    lineas.append(
        "Devolve SOLO un JSON estricto (sin markdown, sin texto fuera) con "
        "esta forma exacta:\n"
        '{"nuevas":[{"regla":"...","por_que":"..."}], '
        '"utiles":["b1",...], "daninas":["b3",...]}'
    )
    lineas.append(
        f"Reglas: maximo {MAX_NUEVAS} nuevas, cada una <= {MAX_REGLA_LEN} "
        "caracteres, GENERALES (sobre como escribir funciones Python que "
        "pasen tests: formato del bloque, imports, tipos, bordes). PROHIBIDO "
        "mencionar un problema puntual, un nombre de funcion de los ejemplos, "
        "un id (mbpp-...), o copiar literales (numeros, strings, listas) de "
        "los casos. 'utiles'/'daninas' citan ids del playbook que ayudaron o "
        "danaron en estos casos."
    )
    return "\n".join(lineas)


def parse_reflector_json(text):
    """Parsea la respuesta del reflector. Devuelve dict normalizado o None."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    data = None
    try:
        data = json.loads(t)
    except ValueError:
        ini = t.find("{")
        fin = t.rfind("}")
        if ini >= 0 and fin > ini:
            try:
                data = json.loads(t[ini:fin + 1])
            except ValueError:
                return None
        else:
            return None
    if not isinstance(data, dict):
        return None
    nuevas = data.get("nuevas", [])
    utiles = data.get("utiles", [])
    daninas = data.get("daninas", [])
    if not isinstance(nuevas, list):
        nuevas = []
    if not isinstance(utiles, list):
        utiles = []
    if not isinstance(daninas, list):
        daninas = []
    norm_nuevas = []
    for item in nuevas:
        if isinstance(item, dict) and isinstance(item.get("regla"), str):
            norm_nuevas.append({
                "regla": item["regla"].strip(),
                "por_que": str(item.get("por_que", ""))[:500],
            })
        elif isinstance(item, str) and item.strip():
            norm_nuevas.append({"regla": item.strip(), "por_que": ""})
    norm = {
        "nuevas": norm_nuevas[:MAX_NUEVAS],
        "utiles": [str(x) for x in utiles if isinstance(x, str)],
        "daninas": [str(x) for x in daninas if isinstance(x, str)],
    }
    return norm


def extraer_nombres_funcion(asserts):
    """Nombres llamados en los asserts del lote que identifican un problema.

    Filtro (F2.2-fix): se ignoran los nombres de <= 3 letras y los que son
    keywords / builtins / palabras de uso comun (python, count, sort...): una
    regla general que los cita ("bloque Python", "usá str.split") no es
    memorizacion. Lo que sobrevive si identifica un problema puntual.
    """
    out = set()
    for a in asserts or []:
        for m in _RE_LLAMADA.finditer(str(a)):
            name = m.group(1)
            if len(name) < _MIN_LEN_NOMBRE:
                continue
            if name.lower() in _PALABRAS_COMUNES:
                continue
            out.add(name)
    return out


def extraer_literales(asserts):
    """Huellas literales de los asserts: numeros, strings y listas.

    Reglas (F2.2-fix, anti-falsos-positivos):
      - Numeros: solo >= 3 digitos o con decimales (10 y 3.5 son ruido;
        12345 o 3.14159 son huellas).
      - Strings: solo >= 6 caracteres y que no sean palabras comunes.
      - Listas/tuplas literales: se mantienen TODAS (>= 2 elementos): son el
        filtro fuerte real ("[1, 2, 3]" en una regla es copia del assert).
    """
    out = set()
    for a in asserts or []:
        s = str(a)
        for m in _RE_NUM.finditer(s):
            tok = m.group(0).lstrip("+-")
            if "." in tok or len(tok.replace(".", "")) >= _MIN_DIGITOS_NUM:
                out.add(tok)
        for m in _RE_STR.finditer(s):
            content = m.group(1) if m.group(1) is not None else m.group(2)
            if not content or len(content) < _MIN_LEN_STR:
                continue
            if content.lower().strip() in _PALABRAS_COMUNES:
                continue
            out.add(content)
        for m in _RE_LISTA.finditer(s):
            lit = m.group(0).strip()
            if len(lit) >= 5:
                out.add(lit)
    return out


def _variantes_identificador(name):
    """Formas en las que un identificador puede citarse en una regla.

    Un nombre de funcion de un assert se escribe con mayusculas y minusculas
    seguidas (find_Max_Num, contarVocales). Si el filtro solo aceptara el
    identificador EXACTO, una regla que lo cita con capitalizacion de
    snippet (Find_Max_Num / FIND_MAX_NUM) se colaria como general. Por eso se
    exige palabra completa de ALGUNA de estas variantes:
      - el identificador exacto (find_Max_Num),
      - capitalizado como snippet (Find_Max_Num),
      - todo en mayusculas (FIND_MAX_NUM).
    No se prueba lowercase (find_max_num): ahi se confunde con lenguaje comun
    ("find", "max" y "num" son palabras sueltas) y abre la puerta a falsos
    positivos.
    """
    base = name.strip()
    if not base:
        return []
    out = [base]
    cap = base[0].upper() + base[1:]
    if cap != base:
        out.append(cap)
    up = base.upper()
    if up != base and up != cap:
        out.append(up)
    return out


def motivo_filtro(regla, nombres_funcion, literales):
    """Motivo por el que una regla se descarta, o None si pasa el filtro.

    Anti-memorizacion por PALABRA COMPLETA (no subcadena): matchear
    subcadenas mataba reglas generales ("python" dentro de "bloque Python",
    "for" dentro de "formula", "Valid" dentro de "valido").
    """
    r = (regla or "").strip()
    if not r:
        return "regla vacia"
    if "mbpp-" in r.lower():
        return "contiene id mbpp- (memorizacion)"
    # Nombres: palabra completa de alguna variante del identificador
    # (exacto / capitalizado / mayusculas), no subcadena.
    for fn in sorted(nombres_funcion or set()):
        for var in _variantes_identificador(fn):
            if _palabra_completa(var, r, case_sensitive=True):
                return f"contiene nombre de funcion del lote: {fn}"
    # Literales: palabra completa. La lista "[1, 2, 3]" si es huella clara.
    for lit in sorted(literales or set(), key=len, reverse=True):
        if not lit or len(lit) < 2:
            continue
        if _palabra_completa(lit, r, case_sensitive=True):
            corto = lit if len(lit) <= 40 else lit[:40] + "..."
            return f"contiene literal de assert: {corto}"
    if len(r) > MAX_REGLA_LEN:
        return f"supera {MAX_REGLA_LEN} caracteres ({len(r)})"
    return None


def es_duplicado(regla, existentes):
    """True si difflib ratio > 0.8 contra alguna regla existente."""
    a = (regla or "").strip().lower()
    if not a:
        return False, None
    for r in existentes or []:
        b = str(r.get("regla", "")).strip().lower()
        if not b:
            continue
        if difflib.SequenceMatcher(None, a, b).ratio() > DUP_UMBRAL:
            return True, r.get("id")
    return False, None


def _playbook_serializado(rows):
    """Forma canonica de un playbook para comparar padre vs hijo.

    Lista de tuplas (id, regla, util, danina) en orden: si padre e hijo
    coinciden aca, el hijo es IDENTICO al padre (no hay nada nuevo que juzgar
    en dev). No se comparan origen/creado (metadatos, no afectan el prompt).
    """
    return [
        (
            str(r.get("id", "")),
            str(r.get("regla", "")),
            int(r.get("util", 0) or 0),
            int(r.get("danina", 0) or 0),
        )
        for r in (rows or [])
    ]


def curar_playbook(existentes, propuestas, utiles, daninas,
                   nombres_funcion, literales, origen="learn-ace", creado=""):
    """Curador determinista. Devuelve (nuevo, agregadas, rechazadas, podadas).

    - existentes: [{"id","regla","util","danina","origen","creado"}]
    - propuestas: [{"regla","por_que"}] (se toman las primeras 3)
    - utiles/daninas: [ids] del reflector (solo cuentan si el id existe)
    - rechazadas: [{"regla","motivo"}]
    - podadas: [ids] (poda danina>=util+2 + tope 25 por peor util-danina)
    """
    nuevo = [dict(r) for r in (existentes or [])]
    por_id = {r["id"]: r for r in nuevo}
    for uid in (utiles or []):
        if uid in por_id:
            por_id[uid]["util"] = int(por_id[uid].get("util", 0) or 0) + 1
    for did in (daninas or []):
        if did in por_id:
            por_id[did]["danina"] = int(por_id[did].get("danina", 0) or 0) + 1

    agregadas = []
    rechazadas = []
    prox = _siguiente_num_id(nuevo)
    for prop in (propuestas or [])[:MAX_NUEVAS]:
        regla = (prop.get("regla", "") or "").strip() if isinstance(prop, dict) else str(prop).strip()
        if not regla:
            rechazadas.append({"regla": regla, "motivo": "regla vacia"})
            continue
        mot = motivo_filtro(regla, nombres_funcion, literales)
        if mot:
            rechazadas.append({"regla": regla, "motivo": mot})
            continue
        dup, de_id = es_duplicado(regla, nuevo)
        if dup:
            rechazadas.append({"regla": regla, "motivo": f"casi-duplicado de {de_id} (ratio>0.8)"})
            continue
        nid = f"b{prox}"
        prox += 1
        fila = {"id": nid, "regla": regla, "util": 0, "danina": 0,
                "origen": origen, "creado": creado}
        nuevo.append(fila)
        agregadas.append({"id": nid, "regla": regla})

    podadas = []
    vivos = []
    for r in nuevo:
        u = int(r.get("util", 0) or 0)
        d = int(r.get("danina", 0) or 0)
        if d >= u + 2:
            podadas.append(r["id"])
        else:
            vivos.append(r)
    nuevo = vivos
    if len(nuevo) > TOPE_REGLAS:
        # Peor util-danina primero; desempate: mas danina, luego id mayor
        # (las nuevas sobran antes que las viejas probadas).
        orden = sorted(nuevo, key=lambda r: (
            int(r.get("util", 0) or 0) - int(r.get("danina", 0) or 0),
            -int(r.get("danina", 0) or 0),
            str(r.get("id", "")),
        ))
        sobran = len(nuevo) - TOPE_REGLAS
        fuera = set(x["id"] for x in orden[:sobran])
        podadas.extend(sorted(fuera))
        nuevo = [r for r in nuevo if r["id"] not in fuera]
    # Orden estable por id para que el playbook sea legible y dif-friendly.
    nuevo.sort(key=lambda r: str(r.get("id", "")))
    return nuevo, agregadas, rechazadas, podadas


def _siguiente_policy_id(task):
    tdir = POLICIES_DIR / task
    mx = -1
    if tdir.is_dir():
        for d in tdir.iterdir():
            if d.is_dir():
                m = re.fullmatch(r"v(\d+)", d.name)
                if m:
                    mx = max(mx, int(m.group(1)))
    return f"v{mx + 1}"


def _leer_activa(task):
    p = POLICIES_DIR / task / "ACTIVA"
    try:
        txt = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return txt or None


def _escribir_activa(task, pid):
    p = POLICIES_DIR / task / "ACTIVA"
    p.write_text(str(pid).strip() + "\n", encoding="utf-8")


def _ledger_events():
    try:
        return _read_jsonl(LEDGER)
    except (OSError, ValueError):
        return []


def _buscar_dev_valida(task, policy, nonce_dev):
    """Ultima eval dev valida (event=eval) para task/policy/nonce con archivo."""
    mejor = None
    for e in _ledger_events():
        if not isinstance(e, dict):
            continue
        if e.get("event") != "eval":
            continue
        if e.get("task") != task or e.get("policy") != policy:
            continue
        if e.get("split") != "dev":
            continue
        if e.get("nonce") != nonce_dev:
            continue
        mejor = e
    if not mejor:
        return None
    rel = mejor.get("results", "")
    if not rel:
        return None
    p = _ROOT / Path(*str(rel).split("/")) if not Path(str(rel)).is_absolute() else Path(str(rel))
    if p.exists():
        return mejor
    # Path absoluto o relativo roto: no reusable
    return None


def _ultimo_ledger(n_antes):
    evs = _ledger_events()
    return evs[n_antes:]


def _ejecutar_eval(task, policy, split, nonce, n_lote=None):
    """Corre evaluate.cmd_eval y devuelve (exit_code, ledger_entry|None).

    ledger_entry es el ultimo evento eval/eval_invalida de esta corrida
    (buscado por task/policy/split/nonce entre los eventos nuevos).
    """
    n_antes = len(_ledger_events())
    args = ["--task", task, "--policy", policy, "--split", split]
    if nonce is not None:
        args += ["--nonce", str(nonce)]
    if n_lote is not None:
        args += ["--n", str(n_lote)]
    t0 = time.monotonic()
    try:
        rc = evaluate.cmd_eval(args)
    except SystemExit as ex:
        rc = int(ex.code) if isinstance(ex.code, int) else EXIT_ERROR
    nuevos = _ultimo_ledger(n_antes)
    entry = None
    for e in reversed(nuevos):
        if not isinstance(e, dict):
            continue
        if e.get("event") not in ("eval", "eval_invalida"):
            continue
        if e.get("task") == task and e.get("policy") == policy and e.get("split") == split:
            entry = e
            break
    if entry is None:
        for e in reversed(nuevos):
            if isinstance(e, dict) and e.get("event") in ("eval", "eval_invalida"):
                entry = e
                break
    entry = dict(entry) if entry else None
    if entry is not None:
        entry["_exit"] = rc
        entry["_segundos_llamado"] = round(time.monotonic() - t0, 3)
    return rc, entry


def _results_de(entry):
    """Carga las filas de results.jsonl de un ledger entry (o [])."""
    if not entry:
        return []
    rel = entry.get("results", "")
    if not rel:
        return []
    p = _ROOT / Path(*str(rel).split("/")) if not Path(str(rel)).is_absolute() else Path(str(rel))
    try:
        return _read_jsonl(p)
    except (OSError, ValueError):
        return []


def _lote_asserts(task, n_lote):
    """Asserts del lote de train (primeros N) para el filtro anti-memoria."""
    rows = _read_jsonl(TASKS_DIR / task / "train.jsonl")
    if n_lote is not None:
        try:
            rows = rows[:max(0, int(n_lote))]
        except (TypeError, ValueError):
            pass
    asserts = []
    for r in rows:
        exp = (r.get("expected") or {}) if isinstance(r, dict) else {}
        tests = exp.get("tests") if isinstance(exp, dict) else None
        if isinstance(tests, list):
            asserts.extend(str(t) for t in tests)
    return asserts, rows


def _crear_hijo(task, padre_id, hijo_id, nuevo_playbook, ronda, nonce):
    pdir = POLICIES_DIR / task / padre_id
    hdir = POLICIES_DIR / task / hijo_id
    if hdir.exists():
        raise FileExistsError(f"ya existe policies/{task}/{hijo_id}/")
    prompt_md = (pdir / "prompt.md").read_text(encoding="utf-8")
    try:
        padre_meta = _load_json(pdir / "policy.json")
    except (OSError, ValueError):
        padre_meta = {}
    rol = padre_meta.get("rol_modelo", "rapido")
    hdir.mkdir(parents=True, exist_ok=False)
    (hdir / "prompt.md").write_text(prompt_md, encoding="utf-8")
    with (hdir / "playbook.jsonl").open("w", encoding="utf-8") as fh:
        for r in nuevo_playbook:
            fh.write(json.dumps({
                "id": r.get("id"), "regla": r.get("regla"),
                "util": int(r.get("util", 0) or 0),
                "danina": int(r.get("danina", 0) or 0),
                "origen": r.get("origen", "learn-ace"),
                "creado": r.get("creado", ""),
            }, ensure_ascii=False) + "\n")
    meta = {
        "id": hijo_id,
        "padre": padre_id,
        "creada_por": "learn-ace",
        "ronda": ronda,
        "nonce": nonce,
        "rol_modelo": rol,
        "status": "candidata",
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (hdir / "policy.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return meta


def _marcar_hijo(task, hijo_id, status):
    p = POLICIES_DIR / task / hijo_id / "policy.json"
    try:
        meta = _load_json(p)
    except (OSError, ValueError):
        meta = {"id": hijo_id}
    meta["status"] = status
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
    return meta


def _ultimo_compare():
    for e in reversed(_ledger_events()):
        if isinstance(e, dict) and e.get("event") == "compare":
            return e
    return None


def cmd_learn(args):
    flags = _parse_args(list(args or []))
    task = str(flags.get("task") or "")
    padre_ini = str(flags.get("parent") or "")
    rondas = flags.get("rondas")
    nonce = str(flags.get("nonce") or "")
    lote = flags.get("lote", flags.get("n"))

    if not task or not padre_ini or not nonce:
        print("Uso: python -m motor learn --task T --parent ID --rondas K --nonce S [--lote N]")
        return EXIT_ERROR
    try:
        rondas = int(rondas)
    except (TypeError, ValueError):
        print(f"--rondas invalido: {rondas}")
        return EXIT_ERROR
    if rondas < 1:
        print("--rondas debe ser >= 1")
        return EXIT_ERROR
    n_lote = None
    if lote is not None and lote is not True:
        try:
            n_lote = int(str(lote))
        except ValueError:
            print(f"--lote invalido: {lote}")
            return EXIT_ERROR
        if n_lote is not None and n_lote < 1:
            print("--lote debe ser >= 1")
            return EXIT_ERROR

    if not (TASKS_DIR / task).is_dir():
        print(f"Sin tasks/{task}/")
        return EXIT_ERROR
    if not (POLICIES_DIR / task / padre_ini).is_dir():
        print(f"Sin policies/{task}/{padre_ini}/")
        return EXIT_ERROR

    dev_nonce = f"{nonce}-dev"
    actual = padre_ini
    # Si ya hay ACTIVA, la ronda 1 parte de lo que dice (el --parent manda
    # solo cuando no hay ACTIVA o coincide).
    act0 = _leer_activa(task)
    if act0 and (POLICIES_DIR / task / act0).is_dir():
        if act0 != padre_ini:
            print(f"learn: ACTIVA={act0} distinta de --parent {padre_ini}: "
                  f"se parte de ACTIVA ({act0}).")
            actual = act0

    for ronda in range(1, rondas + 1):
        padre_ronda = actual
        train_nonce = f"{nonce}-train-r{ronda}"
        reflector_nonce = f"{nonce}-reflect-r{ronda}"
        print(f"\n=== learn {task} ronda {ronda}/{rondas} padre={padre_ronda} nonce={nonce} ===")

        # 1. GENERADOR: padre sobre train con revelar=True (lo hace evaluate).
        rc_train, ev_train = _ejecutar_eval(task, padre_ronda, "train", train_nonce, n_lote)
        if rc_train != evaluate.EXIT_OK or not ev_train or ev_train.get("event") != "eval":
            motivo = (ev_train or {}).get("motivo", f"train eval exit {rc_train}") if ev_train else f"train eval exit {rc_train} (sin ledger)"
            print(f"ronda {ronda}: GENERADOR invalido (exit {rc_train}): {motivo}. Se aborta la ronda.")
            agent._chain_append(LEDGER, {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "learn", "task": task, "ronda": ronda, "padre": padre_ronda,
                "hijo": None, "nonce": nonce, "train_nonce": train_nonce,
                "dev_nonce": dev_nonce, "reglas_propuestas": [],
                "reglas_agregadas": [], "reglas_rechazadas_por_filtro": [],
                "reglas_podadas": [], "media_padre": None, "media_hijo": None,
                "media_d": None, "ic": None, "mejoras": None, "regresiones": None,
                "decision": "ABORTADA", "motivo": f"generador: {motivo}",
            })
            nxt = _leer_activa(task)
            if nxt and (POLICIES_DIR / task / nxt).is_dir():
                actual = nxt
            continue

        train_rows = _results_de(ev_train)
        fallidos, pasados = [], []
        for r in train_rows:
            if r.get("llm_error"):
                continue
            (fallidos if not r.get("ok") else pasados).append({
                "input": r.get("input") or {},
                "output": r.get("output") or "",
                "feedback": r.get("feedback") or "",
            })
        fallidos = fallidos[:MAX_FALLOS]
        pasados = pasados[:MAX_PASADOS]

        asserts_lote, _ = _lote_asserts(task, n_lote)
        nombres = extraer_nombres_funcion(asserts_lote)
        literales = extraer_literales(asserts_lote)
        playbook_actual = load_playbook(POLICIES_DIR / task / padre_ronda)

        # 2. REFLECTOR: 1 llamada LLM rol rapido, SOLO train.
        prompt_ref = build_reflector_prompt(playbook_actual, fallidos, pasados)
        try:
            r1 = llm.complete(prompt_ref, rol="reflector", nonce=reflector_nonce)
            texto = r1.get("text", "")
        except Exception as ex:
            motivo = f"reflector: {type(ex).__name__}: {ex}"
            print(f"ronda {ronda}: {motivo}. Se aborta la ronda.")
            agent._chain_append(LEDGER, {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "learn", "task": task, "ronda": ronda, "padre": padre_ronda,
                "hijo": None, "nonce": nonce, "train_nonce": train_nonce,
                "dev_nonce": dev_nonce, "reglas_propuestas": [],
                "reglas_agregadas": [], "reglas_rechazadas_por_filtro": [],
                "reglas_podadas": [], "media_padre": None, "media_hijo": None,
                "media_d": None, "ic": None, "mejoras": None, "regresiones": None,
                "decision": "ABORTADA", "motivo": motivo,
            })
            nxt = _leer_activa(task)
            if nxt and (POLICIES_DIR / task / nxt).is_dir():
                actual = nxt
            continue

        parsed = parse_reflector_json(texto)
        reflector_error = None
        if parsed is None:
            # 1 reintento pidiendo solo JSON.
            try:
                r2 = llm.complete(
                    "Devolve SOLO el JSON pedido, sin markdown ni texto fuera. "
                    "Forma exacta: {\"nuevas\":[{\"regla\":\"...\",\"por_que\":\"...\"}], "
                    "\"utiles\":[], \"daninas\":[]} "
                    f"Respuesta anterior que no parseo: {texto[:2000]}",
                    rol="reflector", nonce=reflector_nonce + "-retry",
                )
                parsed = parse_reflector_json(r2.get("text", ""))
            except Exception as ex:
                parsed = None
                reflector_error = f"reintento reflector: {type(ex).__name__}: {ex}"
            if parsed is None and reflector_error is None:
                reflector_error = "json_invalido (2 intentos): la ronda no agrega reglas"
                print(f"ronda {ronda}: {reflector_error}")
        if parsed is None:
            parsed = {"nuevas": [], "utiles": [], "daninas": []}
        propuestas = parsed.get("nuevas", [])
        utiles = parsed.get("utiles", [])
        daninas = parsed.get("daninas", [])

        # 3. CURADOR determinista.
        creado = datetime.now(timezone.utc).isoformat(timespec="seconds")
        nuevo_pb, agregadas, rechazadas, podadas = curar_playbook(
            playbook_actual, propuestas, utiles, daninas,
            nombres, literales, origen=f"learn-ace-r{ronda}", creado=creado,
        )

        # 4-bis. JUEZ EFICIENTE: si el hijo es IDENTICO al padre (mismas reglas
        # en el mismo orden, mismos contadores) no se evalua dev: RECHAZA
        # directo con "sin reglas nuevas". Caso tipico: R1 ronda 1, las 3
        # propuestas cayeron en el filtro y el juez igual evaluo 60 ejemplos
        # de dev para terminar RECHAZANDO con d=0. Ahorra ~60 llamadas.
        if _playbook_serializado(nuevo_pb) == _playbook_serializado(playbook_actual):
            motivo = "sin reglas nuevas (playbook identico al padre): no se evalua dev"
            print(f"ronda {ronda}: JUEZ RECHAZA directo: {motivo}.")
            agent._chain_append(LEDGER, {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "learn", "task": task, "ronda": ronda,
                "padre": padre_ronda,
                "hijo": None, "nonce": nonce, "train_nonce": train_nonce,
                "dev_nonce": dev_nonce,
                "reglas_propuestas": [p.get("regla", "") for p in propuestas],
                "reglas_agregadas": [],
                "reglas_rechazadas_por_filtro": rechazadas,
                "reglas_podadas": podadas, "utiles": utiles, "daninas": daninas,
                "media_padre": None, "media_hijo": None, "n_dev": 0,
                "media_d": None, "ic": None, "mejoras": None,
                "regresiones": None, "empates": None,
                "decision": "RECHAZA", "motivo": motivo,
                "reflector_error": reflector_error,
            })
            nxt = _leer_activa(task)
            if nxt and (POLICIES_DIR / task / nxt).is_dir():
                actual = nxt
            continue

        # 4. HIJO.
        hijo_id = _siguiente_policy_id(task)
        try:
            _crear_hijo(task, padre_ronda, hijo_id, nuevo_pb, ronda, nonce)
        except (OSError, ValueError, FileExistsError) as ex:
            motivo = f"hijo {hijo_id} no se pudo crear: {ex}"
            print(f"ronda {ronda}: {motivo}")
            agent._chain_append(LEDGER, {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "learn", "task": task, "ronda": ronda, "padre": padre_ronda,
                "hijo": hijo_id, "nonce": nonce, "train_nonce": train_nonce,
                "dev_nonce": dev_nonce,
                "reglas_propuestas": [p.get("regla", "") for p in propuestas],
                "reglas_agregadas": agregadas,
                "reglas_rechazadas_por_filtro": rechazadas,
                "reglas_podadas": podadas, "media_padre": None, "media_hijo": None,
                "media_d": None, "ic": None, "mejoras": None, "regresiones": None,
                "decision": "ABORTADA", "motivo": motivo,
            })
            continue

        # 5. JUEZ: padre e hijo en dev con el MISMO nonce (reuso si existe).
        ev_padre = _buscar_dev_valida(task, padre_ronda, dev_nonce)
        if ev_padre is None:
            rc_p, ev_padre = _ejecutar_eval(task, padre_ronda, "dev", dev_nonce)
        else:
            rc_p = evaluate.EXIT_OK
            print(f"ronda {ronda}: JUEZ reusa dev del padre {padre_ronda} nonce={dev_nonce}")
        rc_h, ev_hijo = _ejecutar_eval(task, hijo_id, "dev", dev_nonce)

        def _mean(ev):
            try:
                return float(ev.get("mean")) if ev and ev.get("mean") is not None else None
            except (TypeError, ValueError):
                return None

        media_padre, media_hijo = _mean(ev_padre), _mean(ev_hijo)
        if rc_p != evaluate.EXIT_OK or rc_h != evaluate.EXIT_OK or not ev_padre or not ev_hijo:
            motivo = (f"juez con eval invalida "
                      f"(padre exit {rc_p} event {(ev_padre or {}).get('event')}, "
                      f"hijo exit {rc_h} event {(ev_hijo or {}).get('event')}). "
                      f"NUNCA se acepta con eval invalida.")
            print(f"ronda {ronda}: {motivo}")
            _marcar_hijo(task, hijo_id, "abortada")
            agent._chain_append(LEDGER, {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "learn", "task": task, "ronda": ronda, "padre": padre_ronda,
                "hijo": hijo_id, "nonce": nonce, "train_nonce": train_nonce,
                "dev_nonce": dev_nonce,
                "reglas_propuestas": [p.get("regla", "") for p in propuestas],
                "reglas_agregadas": agregadas,
                "reglas_rechazadas_por_filtro": rechazadas,
                "reglas_podadas": podadas, "media_padre": media_padre,
                "media_hijo": media_hijo, "media_d": None, "ic": None,
                "mejoras": None, "regresiones": None,
                "decision": "ABORTADA", "motivo": motivo,
                "reflector_error": reflector_error,
            })
            nxt = _leer_activa(task)
            if nxt and (POLICIES_DIR / task / nxt).is_dir():
                actual = nxt
            continue

        # Padre e hijo validos: compare.py decide (deja evento "compare").
        res_p = ev_padre.get("results", "")
        res_h = ev_hijo.get("results", "")
        pa = str((_ROOT / Path(*str(res_p).split("/"))) if not Path(str(res_p)).is_absolute() else res_p)
        pb = str((_ROOT / Path(*str(res_h).split("/"))) if not Path(str(res_h)).is_absolute() else res_h)
        n_cmp_antes = len(_ledger_events())
        try:
            rc_cmp = compare.cmd_compare(["--a", pa, "--b", pb])
        except SystemExit as ex:
            rc_cmp = int(ex.code) if isinstance(ex.code, int) else compare.EXIT_ERROR
        ev_cmp = None
        for e in reversed(_ultimo_ledger(n_cmp_antes)):
            if isinstance(e, dict) and e.get("event") == "compare":
                ev_cmp = e
                break
        if ev_cmp is None:
            ev_cmp = _ultimo_compare() or {}
        decision = "ACEPTA" if rc_cmp == compare.EXIT_ACEPTA else "RECHAZA"
        motivo_cmp = str(ev_cmp.get("motivo", "")) if decision == "RECHAZA" else ""
        media_d = ev_cmp.get("media_d")
        ic = ev_cmp.get("ic")
        mejoras = ev_cmp.get("mejoras")
        regresiones = ev_cmp.get("regresiones")
        empates = ev_cmp.get("empates")
        n_dev = ev_cmp.get("n")

        if decision == "ACEPTA":
            _marcar_hijo(task, hijo_id, "aceptada")
            _escribir_activa(task, hijo_id)
            print(f"ronda {ronda}: JUEZ ACEPTA {padre_ronda} -> {hijo_id} "
                  f"(dev {media_padre} vs {media_hijo}, d={media_d} IC={ic}). "
                  f"ACTIVA={hijo_id}.")
            motivo_learn = f"acepta: d={media_d} IC={ic} mejoras={mejoras} regresiones={regresiones}"
            actual = hijo_id
        else:
            _marcar_hijo(task, hijo_id, "rechazada")
            motivo_learn = f"rechaza: {motivo_cmp} (d={media_d} IC={ic})"
            print(f"ronda {ronda}: JUEZ RECHAZA {hijo_id} ({motivo_learn}). "
                  f"ACTIVA sigue en {_leer_activa(task) or padre_ronda}.")
            nxt = _leer_activa(task)
            if nxt and (POLICIES_DIR / task / nxt).is_dir():
                actual = nxt

        agent._chain_append(LEDGER, {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": "learn", "task": task, "ronda": ronda,
            "padre": padre_ronda,
            "hijo": hijo_id, "nonce": nonce, "train_nonce": train_nonce,
            "dev_nonce": dev_nonce,
            "reglas_propuestas": [p.get("regla", "") for p in propuestas],
            "reglas_agregadas": agregadas,
            "reglas_rechazadas_por_filtro": rechazadas,
            "reglas_podadas": podadas, "utiles": utiles, "daninas": daninas,
            "media_padre": media_padre, "media_hijo": media_hijo,
            "n_dev": n_dev, "media_d": media_d, "ic": ic,
            "mejoras": mejoras, "regresiones": regresiones, "empates": empates,
            "decision": decision, "motivo": motivo_learn,
            "reflector_error": reflector_error,
            "compare_a": compare.ruta_repo(pa), "compare_b": compare.ruta_repo(pb),
        })

    print(f"\nlearn {task}: fin {rondas} rondas. ACTIVA={_leer_activa(task) or '(sin ACTIVA)'}")
    return EXIT_OK

#!/usr/bin/env python3
"""Adaptador LLM del motor — solo stdlib (sin dependencias externas).

Llama a `opencode run` como LLM limpio, SIN herramientas y SIN cargar AGENTS.md
del repo (ahorra tokens y el modelo no puede tocar archivos):

  - `--agent plan`: agente built-in en modo solo lectura (se niega a ejecutar).
    Hallazgo: agentes custom con tools deny rompen el free tier de OpenCode Zen
    (HTTP 403 FreeTierError "can only be used from within OpenCode"); `plan` funciona.
  - Ejecutable REAL, no el shim .cmd: en Windows shutil.which devuelve
    opencode.cmd (batch) y cmd.exe TRUNCA argumentos multilinea (el modelo solo
    veia la 1a linea del prompt) y encolaba el .cmd. Fix: si el which termina en
    .cmd/.bat, se resuelve <dir del .cmd>/node_modules/opencode-ai/bin/opencode.exe
    si existe (el binario node real) y se ejecuta ese.
  - cwd FUERA del repo: %TEMP%/siafb_llm_cwd (vacio). Con cwd dentro del repo,
    opencode detectaba el proyecto (git root), cargaba AGENTS.md y el agente
    plan LEIA archivos (prompt de prueba devolvio un resumen de RUNSTATE,
    tokens_in 23869). Fix probado: cwd en temp -> responde la palabra del
    prompt, tokens_in ~3.9k, sin contexto del proyecto.

Costo medido del system prompt de `plan`: ~11.3k tokens de entrada por llamada
(cache 0), cost $0 (free tier). Ver motor/_descubrimiento/run_json.txt.

Uso:
  from motor.llm import complete
  r = complete("Respondé exactamente: OK", rol="rapido")

Roles (motor/models.json, F2.0 2026-09-23):
  rapido  -> opencode/muse-spark-1.3-contributor-free (Meta; SOLO datos
             publicos, ver "aviso_datos" en models.json)
  fuerte  -> opencode/nemotron-3-ultra-free
  respaldo_rapido (ambos roles si agotan reintentos transitorios):
            opencode/mimo-v2.6-flash-free
  Nota real: Nemotron 3 Ultra devolvio "504 A Timeout Occurred" con contextos
  grandes -> 504 es transitorio (retry_policy) y tras reintentos se cae al respaldo.

Retorno de complete(): {"text", "model", "tokens_in", "tokens_out",
                        "latency_s", "cached"}

Robustez F2.0 (2026-09-23):
  - Timeout por intento: env MOTOR_TIMEOUT, default 90 s (antes 180 fijo).
  - Intentos por candidato: env MOTOR_INTENTOS, default 3 (antes
    INTENTOS_MAX=5 de retry_policy: con 5 x 180 s x 2 modelos el
    cortacircuito de evaluate.py disparaba tarde tras horas de espera).
  - AVISO DE DATOS (motor/models.json "aviso_datos"): Muse Spark
    Contributor = Meta puede entrenar con los prompts. SOLO tareas con
    datos publicos (py_funcs/MBPP). PROHIBIDO para datos de leads/personas.

Detalles:
  - subprocess SIN shell=True; ejecutable resuelto con shutil.which
    (en Windows el shim real es opencode.cmd).
  - Reintentos con scripts/retry_policy.py: transitorio reintenta, permanente no.
  - system= se anteponen al prompt (opencode run no tiene flag --system).
  - Cache: .cache/llm/<sha256(modelo+system+prompt+nonce)>.json
    (nonce distinto = muestra nueva). Acierto -> cached=True, latency_s ~0.
  - Cada intento REAL deja una linea en .cache/llm_usage.jsonl
    (ts, model, tokens_in, tokens_out, latency_s, ok). Cache y mock NO loguean.
  - Semáforo global: MOTOR_MAX_PAR (default 2) llamadas reales concurrentes.
  - Tests sin red: MOTOR_LLM=mock + MOTOR_MOCK_FILE con
    {"substring del prompt": "respuesta"} (match sobre system+prompt).

Sesiones en opencode.db: cada `opencode run` crea una sesion con title
`MOTOR-LLM:<rol>...` (flag --title). El meter `run cost` solo cuenta sesiones
cuyo title trae `RUN:<slug>` -> las del motor NO se mezclan con el costo de
corridas. Identificarlas: title LIKE 'MOTOR-LLM:%' o directory = %TEMP%/siafb_llm_cwd.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from retry_policy import (  # noqa: E402
    ErrorCritico,
    ErrorPermanente,
    clasificar,
    con_reintentos,
)

_MODELS_FILE = Path(__file__).resolve().parent / "models.json"
_LLM_CWD = Path(tempfile.gettempdir()) / "siafb_llm_cwd"
_CACHE_ROOT = _ROOT / ".cache"
_CACHE_DIR = _CACHE_ROOT / "llm"
_USAGE_FILE = _CACHE_ROOT / "llm_usage.jsonl"

_ROLES = ("rapido", "fuerte", "reflector")
_OLLAMA_URL = os.environ.get("MOTOR_OLLAMA_URL", "http://localhost:11434")
_AGENT = "plan"  # built-in solo lectura: ni tools ni ejecucion (ver docstring)

_MAX_PAR = max(1, int(os.environ.get("MOTOR_MAX_PAR", "2") or 2))
_SEM = threading.Semaphore(_MAX_PAR)


def _timeout_default():
    """Timeout por intento en segundos: env MOTOR_TIMEOUT, default 90."""
    try:
        return max(1, int(os.environ.get("MOTOR_TIMEOUT", "90") or 90))
    except (TypeError, ValueError):
        return 90


def _intentos_motor():
    """Intentos por candidato del motor: env MOTOR_INTENTOS, default 3."""
    try:
        return max(1, int(os.environ.get("MOTOR_INTENTOS", "3") or 3))
    except (TypeError, ValueError):
        return 3


class LLMError(Exception):
    """Fallo de la llamada. code = HTTP si se conoce (None = clasificar por texto)."""

    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def _load_models():
    try:
        data = json.loads(_MODELS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise ErrorPermanente(f"motor/models.json ilegible: {ex}") from ex
    if not isinstance(data, dict):
        raise ErrorPermanente("motor/models.json no es un objeto")
    return data


def _model_for(rol):
    if rol not in _ROLES:
        raise ValueError(f"rol invalido: {rol!r} (use uno de {_ROLES})")
    models = _load_models()
    primary = models.get(rol)
    if not primary:
        raise ErrorPermanente(f"models.json sin modelo para rol {rol!r}")
    # Respaldo por rol (respaldo_<rol>); si no hay clave propia, respaldo_rapido.
    # Un respaldo vacio ("") = sin caida a otro modelo (medicion consistente).
    clave = f"respaldo_{rol}"
    respaldo = models[clave] if clave in models else (models.get("respaldo_rapido") or "")
    candidates = [primary]
    if respaldo and respaldo != primary:
        candidates.append(respaldo)
    return candidates


def _cache_key(model, system, prompt, nonce):
    blob = "\x00".join((model, system or "", prompt or "", nonce or ""))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_get(key):
    path = _CACHE_DIR / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "text" not in data:
        return None
    return data


def _cache_put(key, result):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_DIR / f".tmp-{os.getpid()}-{key[:8]}"
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _CACHE_DIR / f"{key}.json")
    except OSError:
        pass  # cache rota no rompe la llamada


def _usage_log(model, tokens_in, tokens_out, latency_s, ok):
    line = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "latency_s": round(float(latency_s or 0.0), 3),
        "ok": bool(ok),
    }
    try:
        _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        with open(_USAGE_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def parse_run_output(raw):
    """Parsea la salida NDJSON de `opencode run --format json`.

    Pura (sin IO): junta todos los `text`, suma tokens de cada `step_finish`
    (un step = una invocacion real del modelo) y detecta eventos `error`.
    Devuelve {"text", "tokens_in", "tokens_out", "error"} (error=None si no hubo).
    """
    text_parts, tin, tout, error = [], 0, 0, None
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue  # logs basura fuera de formato: se ignoran
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "text":
            part = ev.get("part") or {}
            t = part.get("text")
            if isinstance(t, str) and t:
                text_parts.append(t)
        elif etype == "step_finish":
            part = ev.get("part") or {}
            tok = part.get("tokens") or {}
            tin += int(tok.get("input") or 0)
            tout += int(tok.get("output") or 0)
        elif etype == "error":
            error = ev.get("error")
    return {"text": "".join(text_parts), "tokens_in": tin, "tokens_out": tout, "error": error}


def _http_code_from(text):
    m = re.search(r"\b([45]\d{2})\b", text or "")
    return int(m.group(1)) if m else None


def _env_llm():
    """Entorno del subproceso: PWD/INIT_CWD apuntan al cwd vacio fuera del repo.

    BUG 3 (2026-09-23): opencode toma el directorio del proyecto de la variable
    PWD heredada por encima del cwd real. Lanzado desde bash parado en el repo,
    el LLM del motor abria el repo (AGENTS.md + opencode.json con edit allow) y
    llego a escribir archivos .py en la raiz. Forzar PWD lo impide.
    """
    env = dict(os.environ)
    env["PWD"] = str(_LLM_CWD)
    env["INIT_CWD"] = str(_LLM_CWD)
    env.pop("OLDPWD", None)
    return env


def _opencode_exe():
    """Binario real de opencode. Si which da .cmd/.bat, usa el .exe de node.

    cmd.exe trunca argumentos multilinea (BUG 1: el modelo solo veia la 1a
    linea del prompt) y el .cmd en si encola. El .exe vive en
    <dir del .cmd>/node_modules/opencode-ai/bin/opencode.exe (npm global).
    """
    for name in ("opencode", "opencode.cmd"):
        path = shutil.which(name)
        if not path:
            continue
        if path.lower().endswith((".cmd", ".bat")):
            exe = (
                Path(path).parent
                / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
            )
            if exe.exists():
                return str(exe)
        return path
    raise ErrorPermanente("opencode no encontrado en PATH (shutil.which fallo)")


def _run_once(model, message, timeout):
    """Una invocacion real de `opencode run`. Devuelve text/tokens o levanta.

    Transitorio (timeout, 429, 5xx incluido 504) -> LLMError con code transitorio.
    Permanente (400/401/403/404, FreeTierError 403) -> LLMError code permanente.
    """
    cmd = [
        _opencode_exe(),
        "run",
        "--format", "json",
        "--agent", _AGENT,
        "--model", model,
        "--title", "MOTOR-LLM",
        message,
    ]
    _LLM_CWD.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_LLM_CWD),
            env=_env_llm(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as ex:
        latency = time.monotonic() - t0
        _usage_log(model, 0, 0, latency, False)
        raise LLMError(f"timeout tras {timeout}s: {ex}", code=0) from ex

    latency = time.monotonic() - t0
    parsed = parse_run_output(proc.stdout or "")
    if parsed["error"]:
        err = parsed["error"]
        data = err.get("data") if isinstance(err, dict) else None
        data = data if isinstance(data, dict) else {}
        msg = data.get("message") or (err.get("name") if isinstance(err, dict) else "") or str(err)
        code = data.get("statusCode")
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None
        if code is None:
            code = _http_code_from(str(msg))
        _usage_log(model, 0, 0, latency, False)
        raise LLMError(str(msg), code=code)

    if proc.returncode != 0:
        blob = (proc.stderr or "") + "\n" + (proc.stdout or "")
        _usage_log(model, 0, 0, latency, False)
        raise LLMError(
            f"opencode exit {proc.returncode}: {blob.strip()[:400]}",
            code=_http_code_from(blob),
        )

    _usage_log(model, parsed["tokens_in"], parsed["tokens_out"], latency, True)
    return {
        "text": parsed["text"],
        "tokens_in": parsed["tokens_in"],
        "tokens_out": parsed["tokens_out"],
    }


def _seed_de(nonce):
    """Semilla entera reproducible a partir del nonce (mismo nonce = misma muestra)."""
    return int(hashlib.sha256((nonce or "").encode("utf-8")).hexdigest()[:8], 16)


def _run_ollama(model, message, timeout, nonce=None):
    """Modelo LOCAL via Ollama (http://localhost:11434). Solo stdlib (urllib).

    model = "ollama/<nombre>" (ej. ollama/qwen2.5:1.5b). Sin limites de uso,
    sin costo y ningun dato sale de la PC. Temperatura baja + semilla del nonce.
    Errores de red/timeout -> transitorio; HTTP 404 (modelo no bajado) -> permanente.
    """
    import urllib.error
    import urllib.request

    nombre = model.split("/", 1)[1]
    body = json.dumps({
        "model": nombre,
        "prompt": message,
        "stream": False,
        "options": {"temperature": 0.2, "seed": _seed_de(nonce), "num_ctx": 4096},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{_OLLAMA_URL}/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        _usage_log(model, 0, 0, time.monotonic() - t0, False)
        raise LLMError(f"ollama HTTP {ex.code}: {ex.reason}", code=ex.code) from ex
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        _usage_log(model, 0, 0, time.monotonic() - t0, False)
        raise LLMError(f"ollama no responde (timeout/red): {ex}", code=0) from ex
    t_in, t_out = int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
    _usage_log(model, t_in, t_out, time.monotonic() - t0, True)
    return {"text": data.get("response", ""), "tokens_in": t_in, "tokens_out": t_out}


def _mock_complete(model, system, prompt):
    path = os.environ.get("MOTOR_MOCK_FILE", "")
    if not path:
        raise ErrorPermanente("MOTOR_LLM=mock pero sin MOTOR_MOCK_FILE")
    try:
        table = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise ErrorPermanente(f"MOTOR_MOCK_FILE ilegible: {ex}") from ex
    if not isinstance(table, dict):
        raise ErrorPermanente("MOTOR_MOCK_FILE debe ser {substring: respuesta}")
    hay = "\n\n".join(x for x in (system, prompt) if x)
    for key, val in table.items():
        if key and str(key) in hay:
            return {"text": str(val), "tokens_in": 0, "tokens_out": 0}
    return {"text": "", "tokens_in": 0, "tokens_out": 0}


def _una_llamada(model, message, timeout, nonce):
    """Despacha al backend: ollama/<m> local por HTTP; el resto via opencode run."""
    if model.startswith("ollama/"):
        return _run_ollama(model, message, timeout, nonce)
    return _run_once(model, message, timeout)


def _real_complete(candidates, message, timeout, nonce=None):
    """Reintentos por candidato + caida al respaldo. Semáforo global."""
    last_ex = None
    intentos = _intentos_motor()
    with _SEM:
        for idx, model in enumerate(candidates):
            try:
                out = con_reintentos(
                    lambda m=model: _una_llamada(m, message, timeout, nonce),
                    intentos=intentos,
                    dormir=True,
                )
                return model, out
            except (ErrorCritico, ErrorPermanente):
                raise
            except Exception as ex:  # transitorio agotado u otro
                last_ex = ex
                if clasificar(getattr(ex, "code", None), str(ex)) != "transitorio":
                    raise
                if idx == len(candidates) - 1:
                    raise  # ultimo candidato y aun transitorio: se propaga
    raise last_ex if last_ex else ErrorCritico("sin candidatos de modelo")


def complete(prompt, *, rol="rapido", system=None, timeout=None, nonce=None):
    """Una llamada LLM limpia. Ver docstring del modulo para el contrato.

    timeout=None -> env MOTOR_TIMEOUT (default 90 s).
    """
    if not isinstance(prompt, str):
        raise TypeError("prompt debe ser str")
    if timeout is None:
        timeout = _timeout_default()
    candidates = _model_for(rol)
    model_for_key = candidates[0]
    key = _cache_key(model_for_key, system, prompt, nonce)

    t_read = time.monotonic()
    hit = _cache_get(key)
    if hit is not None:
        return {
            "text": hit.get("text", ""),
            "model": hit.get("model", model_for_key),
            "tokens_in": int(hit.get("tokens_in", 0)),
            "tokens_out": int(hit.get("tokens_out", 0)),
            "latency_s": round(time.monotonic() - t_read, 3),
            "cached": True,
        }

    message = "\n\n".join(x for x in (system, prompt) if x)
    mock = os.environ.get("MOTOR_LLM", "") == "mock"
    t0 = time.monotonic()
    if mock:
        model = model_for_key
        out = _mock_complete(model, system, prompt)
    else:
        model, out = _real_complete(candidates, message, timeout, nonce)
    latency = round(time.monotonic() - t0, 3)

    result = {
        "text": out["text"],
        "model": model,
        "tokens_in": int(out.get("tokens_in", 0)),
        "tokens_out": int(out.get("tokens_out", 0)),
        "latency_s": latency,
        "cached": False,
    }
    _cache_put(key, result)
    return result

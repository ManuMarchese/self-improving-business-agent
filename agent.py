#!/usr/bin/env python3
"""Agente de negocio con auto-mejora — bootstrap minimo (Fase 1).

Solo stdlib, sin frameworks ni dependencias.

Uso:
  python agent.py status          pendientes, hechas y proxima tarea
  python agent.py next            muestra la proxima tarea pendiente
  python agent.py add "texto"     agrega una tarea pendiente
  python agent.py done ["nota"]   marca la primera pendiente como hecha
  python agent.py learn "texto"   agrega un aprendizaje a memory.md
  python agent.py memadd TAG "texto"   agrega un hecho al log append-only (memory_log.jsonl)
  python agent.py memfind TERMINO       busca en el log (grep) y muestra coincidencias
  python agent.py memstats              total de entradas del log

Corridas (runs/):
  python agent.py run new "objetivo" [--hours N]   crea runs/<slug>/ con brief.md + journal.jsonl
  python agent.py run budget --slug <slug> --searches N --ig N --posts N --readkb KB
                                                   estima tokens del brief (limite = 2x)
  python agent.py run log [--slug <slug>] "nota"   agrega una linea al journal de la corrida
  python agent.py run status [--slug <slug>]       estado de la corrida (journal + usage)
  python agent.py run cost [--slug <slug>]         lee el uso REAL de tokens/cost desde opencode.db
                                                   (read-only); con slug escribe runs/<slug>/usage.json
  python agent.py run close [--slug <slug>]        mide, actualiza costs.md y prepara el cierre
  python agent.py run resume                       que leer para retomar (protocolo)
  python agent.py run checkpoint [--slug <S>] "estado" [--next "proximo"]
                                                   guarda estado reanudable de mitad de corrida
  python agent.py run intervene <slug> <tipo> "motivo"   registra intervencion humana
                                                   tipo en {gate, friccion, limite-externo}
  python agent.py run step <slug> "descripcion"    registra un paso ejecutado OK
  python agent.py run report [--slug <S>]          pasos + intervenciones por corrida
                                                   (sin eventos nuevos = "sin instrumentar")

Meter de tokens: opencode.db se abre SOLO LECTURA (?mode=ro). La sesion delegada debe empezar
con el primer mensaje "RUN:<slug> ..." para que el meter la encuentre por titulo.

Golden Set (golden/): ejemplos congelados de resultado conocido. Cada cambio de reglas se mide
contra ellos (baseline en golden/results.jsonl). Decision exacta + tolerancia de la rubrica;
regresion en caso con verdad humana (peso 3) = VETO (KEEP bloqueado).
  python agent.py golden build                                    valida datasets/ y congela golden.jsonl
  python agent.py golden rescore --task <task> --rubric <f> --out <hoja>
                                                                 chequeos codigo ($0) + hoja ciega (sin expected)
  python agent.py golden diff <hoja-puntuada> --task <task> --rubric <f> [--scorer <s>]
                                                                 compara, Raw + Weighted + veto, apenda results
  python agent.py golden sample --task <t> --run <slug> --input '<json>' --output '<json>' --reason <m>
                                                                 produccion -> live_queue (revisa un humano)
  python agent.py golden promote <sample_id> --id <ID> --score <n> --decision <d>
                                                                  humano -> datasets/ + promoted (luego build)
  python agent.py golden autopsy --task <t>        fallos del golden -> PROPUESTA (humano aplica)
  python agent.py golden lineage <ID>              cadena supersedes + evals donde fallo (lectura)
  python agent.py golden impact --task <t>         blast-radius: fragilidad pre-cambio (lectura)

Auditoria y esqueleto (F0 blindaje):
  python agent.py doctor               audita el repo (secretos, higiene, integridad,
                                      skeleton fresco). Solo lectura, NUNCA escribe.
                                      Exit 0 = OK, 1 = bloquea push.
  python agent.py skeleton [--out DIR] genera el esqueleto publico (allowlist, sin
                                      negocio) en skeleton/ (default). Determinista.
  python agent.py verify [--scope runs|golden|memory|all]
                                      verifica cadenas de auditoria (prev/hash) en logs.
  python agent.py ask "texto"           clasifica el pedido (pipeline|run-state|memory|direct),
                                      lo ejecuta si es memoria/estado y lo registra en
                                      memory_queries.jsonl (veredicto: pending).

Formato de tasks.txt (una tarea por linea):
  [ ] texto de tarea pendiente
  [x] texto hecha [2026-09-19] nota opcional
  Las lineas que empiezan con # son comentarios.

Memoria en 2 temperaturas:
  - Caliente: memory.md (curado, presupuesto ~100 lineas).
  - Fria: memory_log.jsonl (append-only, 1 hecho por linea JSON).
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "tasks.txt"
MEMORY = ROOT / "memory.md"
MEMLOG = ROOT / "memory_log.jsonl"


def load_tasks():
    """Devuelve lista de (nro_linea, hecha, texto). Ignora comentarios y vacias."""
    items = []
    if not TASKS.exists():
        return items
    for i, raw in enumerate(TASKS.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[x]") or line.startswith("[X]"):
            items.append((i, True, line[3:].strip()))
        elif line.startswith("[ ]"):
            items.append((i, False, line[3:].strip()))
        else:  # linea sin marca = pendiente
            items.append((i, False, line))
    return items


def cmd_status():
    items = load_tasks()
    pending = [t for _, done, t in items if not done]
    done = [t for _, done, t in items if done]
    print(f"Pendientes: {len(pending)} | Hechas: {len(done)}")
    if pending:
        print(f"Proxima: {pending[0]}")
    else:
        print("Sin pendientes. Pedi tu OBJETIVO DE NEGOCIO o agrega tareas con: python agent.py add \"...\"")


def cmd_next():
    for _, done, text in load_tasks():
        if not done:
            print(text)
            return
    print("Sin pendientes.")


def cmd_add(text):
    if not text.strip():
        print("Texto vacio, nada que agregar.")
        return
    with TASKS.open("a", encoding="utf-8") as f:
        f.write(f"[ ] {text.strip()}\n")
    print("Tarea agregada.")


def cmd_done(note=""):
    lines = TASKS.read_text(encoding="utf-8").splitlines(keepends=True) if TASKS.exists() else []
    match = note.strip().lower()
    pending = []
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("[x]") or s.startswith("[X]"):
            continue
        body = s[3:].strip() if s.startswith("[ ]") else s
        pending.append((idx, body))
    if match:
        for idx, body in pending:
            if match in body.lower():
                entry = f"[x] {body} [{date.today().isoformat()}]"
                lines[idx] = entry + "\n"
                _atomic_write(TASKS, "".join(lines))
                print(f"Hecha: {body} (match por texto)")
                return
    if pending:
        idx, body = pending[0]
        entry = f"[x] {body} [{date.today().isoformat()}]"
        if match:
            entry += f" {note.strip()}"
        lines[idx] = entry + "\n"
        TASKS.write_text("".join(lines), encoding="utf-8")
        print(f"Hecha: {body}")
        return
    print("No hay pendientes para marcar.")


def cmd_learn(text):
    if not text.strip():
        print("Texto vacio, nada que aprender.")
        return
    entry = f"- [{date.today().isoformat()}] {text.strip()}\n"
    if not MEMORY.exists():
        _atomic_write(MEMORY, "# Memoria\n\n## Aprendizajes\n")
    content = MEMORY.read_text(encoding="utf-8")
    if "## Aprendizajes" not in content:
        content = content.rstrip() + "\n\n## Aprendizajes\n"
    # Inserta el aprendizaje justo despues del encabezado ## Aprendizajes
    head, sep, tail = content.partition("## Aprendizajes")
    tail = tail.lstrip("\n")
    # respeta subsecciones: inserta antes de la proxima linea que empiece con ##
    parts = tail.splitlines(keepends=True)
    insert_at = len(parts)
    for i, ln in enumerate(parts):
        if ln.startswith("## "):
            insert_at = i
            break
    parts.insert(insert_at, entry)
    _atomic_write(MEMORY, head + sep + "\n" + "".join(parts))
    print("Aprendizaje guardado en memory.md.")


def cmd_memadd(tag, text):
    if not text.strip():
        print("Texto vacio, nada que agregar.")
        return
    entry = {
        "ts": datetime.now(timezone.utc).astimezone().date().isoformat(),
        "tag": (tag.strip() or "general"),
        "fact": text.strip(),
    }
    _chain_seal(MEMLOG, entry)
    line = json.dumps(entry, ensure_ascii=False)
    with MEMLOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"Loggato [{entry['tag']}]: {entry['fact'][:80]}")


def cmd_memfind(term):
    if not MEMLOG.exists():
        print("Log vacio.")
        return
    term_l = term.lower()
    hits = 0
    for raw in MEMLOG.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        blob = (entry.get("fact", "") + " " + entry.get("tag", "")).lower()
        if term_l in blob:
            hits += 1
            print(f"[{entry.get('ts')}] [{entry.get('tag')}] {entry.get('fact')}")
    if not hits:
        print("Sin coincidencias en el log.")


def cmd_memstats():
    if not MEMLOG.exists():
        print("0 entradas en el log.")
        return
    n = sum(1 for l in MEMLOG.read_text(encoding="utf-8").splitlines() if l.strip())
    print(f"{n} entradas en memory_log.jsonl (trigger mem0: 500).")


# --- Corridas (runs/) y meter de tokens --------------------------------

RUNS = ROOT / "runs"
COSTS = ROOT / "costs.md"


def _db_path():
    cands = []
    env = os.environ.get("OPENCODE_DB")
    if env:
        cands.append(Path(env))
    home = Path.home()
    for p in (
        home / ".local/share/opencode/opencode.db",
        Path(os.environ.get("LOCALAPPDATA", "")) / "opencode/opencode.db",
        Path(os.environ.get("XDG_DATA_HOME", "")) / "opencode/opencode.db",
    ):
        cands.append(p)
    for p in cands:
        if p.exists():
            return p
    return None


def _db_ro():
    p = _db_path()
    if not p:
        print("opencode.db no encontrada (rutas estandar ni env OPENCODE_DB).", file=sys.stderr)
        return None
    uri = "file:" + p.as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _tokens_of(session_id, cur):
    cur.execute("SELECT data FROM message WHERE session_id=?", (session_id,))
    t = {"n": 0, "input": 0, "output": 0, "reas": 0, "cread": 0, "cwrite": 0, "cost": 0.0}
    for (d,) in cur.fetchall():
        try:
            o = json.loads(d)
        except ValueError:
            continue
        tok = o.get("tokens") or {}
        cache = tok.get("cache") or {}
        t["n"] += 1
        t["input"] += tok.get("input", 0)
        t["output"] += tok.get("output", 0)
        t["reas"] += tok.get("reasoning", 0)
        t["cread"] += cache.get("read", 0)
        t["cwrite"] += cache.get("write", 0)
        t["cost"] += o.get("cost") or 0
    return t


def _print_tokens(t, label):
    total = t["input"] + t["output"] + t["reas"] + t["cread"] + t["cwrite"]
    print(
        f"{label}: msgs={t['n']:,} in={t['input']:,} out={t['output']:,} "
        f"reas={t['reas']:,} cacheR={t['cread']:,} cacheW={t['cwrite']:,} | "
        f"efectivo={total:,} | cost=${t['cost']:.4f}"
    )


def _slugify(txt):
    s = re.sub(r"[^a-z0-9]+", "-", txt.lower()).strip("-")
    return (s or "run")[:40]


def _resolve_run(slug):
    if slug:
        p = RUNS / slug
    else:
        dirs = [d for d in RUNS.iterdir() if d.is_dir() and (d / "brief.md").exists()]
        if not dirs:
            print("Sin corridas en runs/.")
            raise SystemExit(1)
        p = max(dirs, key=lambda d: d.stat().st_mtime)
    if not p.exists() or not (p / "brief.md").exists():
        print(f"No existe runs/{p.name} con brief.md")
        raise SystemExit(1)
    return p


def _chain_canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _chain_prev(path):
    """Hash de la ultima linea encadenada del archivo, o None. Solo lectura."""
    try:
        lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            o = json.loads(raw)
        except ValueError:
            continue
        if isinstance(o, dict) and isinstance(o.get("hash"), str):
            return o["hash"]
    return None


def _chain_seal(path, entry):
    """Agrega prev+hash a entry (cadena de auditoria; lineas legacy sin hash se ignoran)."""
    prev = _chain_prev(path) or "GENESIS"
    entry["prev"] = prev
    entry["hash"] = hashlib.sha256(
        _chain_canon({k: v for k, v in entry.items() if k != "hash"})
    ).hexdigest()
    return entry


def _trace(event, payload):
    """Telemetria minima append-only (raiz trace.jsonl). Nunca rompe el flujo."""
    try:
        _rotate(ROOT / "trace.jsonl")
        line = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event}
        line.update(payload or {})
        with open(ROOT / "trace.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


_ROTATE_BYTES = 5 * 1024 * 1024


def _rotate(path):
    """Rotación simple: si pasa 5MB, el actual pasa a .1 (se pierde el .1 previo)."""
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > _ROTATE_BYTES:
            old = Path(str(p) + ".1")
            if old.exists():
                old.unlink()
            p.rename(old)
    except OSError:
        pass


def _atomic_write(path, text, encoding="utf-8"):
    """Sobrescribe sin ventana de corrupción: tmp + fsync + rename atómico.
    Misma firma que Path.write_text: reemplazo directo en llamadas existentes."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock_acquire(name, timeout=10):
    """Lock cooperativo por mkdir atómica en TEMP. Solo secciones cortas (ej: promote)."""
    lockdir = Path(tempfile.gettempdir()) / f"siafb-{name}.lock"
    start = time.time()
    while True:
        try:
            lockdir.mkdir(parents=False)
            break
        except FileExistsError:
            try:
                owner = json.loads((lockdir / "owner.json").read_text(encoding="utf-8"))
                if time.time() - float(owner.get("ts", 0)) > 120:
                    shutil.rmtree(lockdir, ignore_errors=True)
                    continue
            except (OSError, ValueError):
                pass
            if time.time() - start > timeout:
                print(f"lock {name} ocupado: otro proceso escribe; reintentá.")
                raise SystemExit(1)
            time.sleep(1)
    (lockdir / "owner.json").write_text(
        json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8"
    )


def _lock_release(name):
    shutil.rmtree(Path(tempfile.gettempdir()) / f"siafb-{name}.lock", ignore_errors=True)


def _journal_append(rdir, event, msg):
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, "msg": msg}
    _chain_seal(rdir / "journal.jsonl", entry)
    with (rdir / "journal.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _trace("run", {"run": rdir.name, "run_event": event})


def cmd_run_new(title, hours):
    if not title.strip():
        print('Uso: python agent.py run new "objetivo" [--hours N]')
        return
    RUNS.mkdir(exist_ok=True)
    slug = date.today().isoformat() + "-" + _slugify(title)
    rdir = RUNS / slug
    rdir.mkdir(exist_ok=True)
    brief = (
        f"---\nslug: {slug}\ntitle: {title.strip()}\ncreated: {datetime.now().isoformat(timespec='seconds')}\n"
        f"mode: delegado\n---\n\n# BRIEF - {title.strip()}\n\n## Objetivo\n<1-2 lineas>\n\n"
        "## Alcance\n- Dentro:\n- Fuera:\n\n## Criterios de salida\n1.\n\n## Presupuesto\n"
        f"- Tiempo: {hours} h\n- Tokens: pendiente -> python agent.py run budget --slug {slug}\n\n"
        "## Checkpoints\n- [ ] 25%:\n- [ ] 50%:\n- [ ] 75%:\n- [ ] 100%:\n\n"
        "## Fuentes permitidas\n-\n\n## Notas\n"
        "- Primer mensaje de la sesion delegada = RUN:<slug> ...\n"
    )
    (rdir / "brief.md").write_text(brief, encoding="utf-8")
    _journal_append(rdir, "new", f"Corrida creada. Objetivo: {title.strip()}")
    print(f"Corrida creada: runs/{slug}/")
    print(f"Abrí la sesion delegada con el primer mensaje: RUN:{slug} {title.strip()}")


def cmd_run_budget(slug, searches, ig, posts, readkb):
    rdir = _resolve_run(slug)
    est = 2500 + searches * 2800 + ig * (600 + posts * 150) + readkb * 285
    brief_file = rdir / "brief.md"
    text = brief_file.read_text(encoding="utf-8")
    new_line = (
        f"- Tokens: est ~{est:,} (limite 2x = ~{est * 2:,}) | "
        f"searches={searches} ig={ig} posts={posts} readkb={readkb}"
    )
    lines = text.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.strip().startswith("- Tokens:"):
            lines[i] = new_line + "\n"
            break
    _atomic_write(brief_file, "".join(lines))
    _journal_append(rdir, "budget", f"Estimado {est:,} tokens")
    print(f"Presupuesto escrito en {brief_file.name}: est ~{est:,} / limite ~{est * 2:,}")


def cmd_run_log(slug, msg):
    if not msg.strip():
        print("Mensaje vacio.")
        return
    rdir = _resolve_run(slug)
    _journal_append(rdir, "log", msg.strip())
    print("Journal actualizado:", rdir.name)


def cmd_run_status(slug):
    rdir = _resolve_run(slug)
    print("Run:", rdir.name)
    print("Brief:", (rdir / "brief.md"))
    jf = rdir / "journal.jsonl"
    if jf.exists():
        lines = [l for l in jf.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"Journal ({len(lines)} entradas, ultimas 5):")
        for l in lines[-5:]:
            try:
                o = json.loads(l)
                print(f"- {o.get('ts')} [{o.get('event')}] {o.get('msg')}")
            except ValueError:
                print("-", l)
    if (rdir / "usage.json").exists():
        print("usage.json:", (rdir / "usage.json").read_text(encoding="utf-8"))


_TIPOS_INTERVENCION = ("gate", "friccion", "limite-externo")


def cmd_run_intervene(slug, tipo, motivo):
    if tipo not in _TIPOS_INTERVENCION:
        print(f"Tipo invalido: {tipo!r}. Usar uno de: {', '.join(_TIPOS_INTERVENCION)}")
        return 1
    if not slug or not motivo.strip():
        print('Uso: python agent.py run intervene <slug> <tipo> "motivo"')
        return 1
    rdir = _resolve_run(slug)
    _journal_append(rdir, "intervencion", f"[{tipo}] {motivo.strip()}")
    print(f"Intervencion [{tipo}] registrada en {rdir.name}")
    return 0


def cmd_run_step(slug, desc):
    if not slug or not desc.strip():
        print('Uso: python agent.py run step <slug> "descripcion"')
        return 1
    rdir = _resolve_run(slug)
    _journal_append(rdir, "paso-ok", desc.strip())
    print(f"Paso registrado en {rdir.name}")
    return 0


def _summarize_journal(jf, journal_text=None):
    """Cuenta instrumentacion en un journal. Pura: testeable con dir temporal."""
    intervs = {"gate": 0, "friccion": 0, "limite-externo": 0}
    try:
        text = journal_text if journal_text is not None else Path(jf).read_text(encoding="utf-8")
        lines = text.splitlines()
    except OSError:
        return {"pasos": 0, "intervenciones": dict(intervs), "instrumentada": False}
    pasos, seen = 0, False
    for ln in lines:
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        ev = o.get("event")
        if ev == "paso-ok":
            pasos += 1
            seen = True
        elif ev == "intervencion":
            seen = True
            m = str(o.get("msg", ""))
            for t in intervs:
                if m.startswith(f"[{t}]"):
                    intervs[t] += 1
                    break
    return {"pasos": pasos, "intervenciones": intervs, "instrumentada": seen}


_DIFICULTAD_RE = re.compile(r"^- Dificultad:\s*([123])", re.M)
_DIFICULTAD_PESOS = {1: 0, 2: 1, 3: 2}


def _dificultad(brief_file, brief_text=None):
    """Dificultad 1|2|3 leida de brief.md. Pura: ausente o invalida -> 1."""
    try:
        text = brief_text if brief_text is not None else Path(brief_file).read_text(encoding="utf-8")
    except OSError:
        return 1
    m = _DIFICULTAD_RE.search(text)
    if not m:
        return 1
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return 1


_API_LLAMADAS_RE = re.compile(r"(\d+)\s+llamadas?\s+API", re.IGNORECASE)


def _journal_tss(journal_text):
    """Timestamps parseados del journal en orden de aparicion. Pura: sin IO."""
    tss = []
    for ln in (journal_text or "").splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        ts = o.get("ts") if isinstance(o, dict) else None
        if not isinstance(ts, str) or not ts.strip():
            continue
        try:
            tss.append(datetime.fromisoformat(ts.strip().replace("Z", "+00:00")))
        except ValueError:
            continue
    return tss


def _journal_api_counts(journal_text):
    """Conteos N de 'N llamadas API' solo en eventos oleada* (pipeline).

    Pura: sin IO. El resto de eventos se ignora (un paso-ok/log no puede
    fabricar evidencia API: solo el pipeline emite 'oleada').
    """
    ns = []
    for ln in (journal_text or "").splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        if not str(o.get("event", "")).startswith("oleada"):
            continue
        for m in _API_LLAMADAS_RE.finditer(str(o.get("msg", ""))):
            try:
                ns.append(int(m.group(1)))
            except (TypeError, ValueError):
                continue
    return ns


def _brief_api_floor(brief_text):
    """2 si la linea '- Tokens:' trae searches|ig|posts con algun valor >0, else 1."""
    for ln in (brief_text or "").splitlines():
        if ln.strip().startswith("- Tokens:"):
            for m in re.finditer(r"\b(searches|ig|posts)\s*=\s*(\d+)", ln):
                try:
                    v = int(m.group(2))
                except (TypeError, ValueError):
                    continue
                if v > 0:
                    return 2
            break
    return 1


def _brief_tiempo_mayor_3(brief_text):
    """True si el brief trae '- Tiempo: N h' con N>3. Solo display."""
    m = re.search(r"-\s*Tiempo:\s*([\d]+(?:[.,][\d]+)?)\s*h", brief_text or "")
    if not m:
        return False
    try:
        n = float(m.group(1).replace(",", "."))
    except (TypeError, ValueError):
        return False
    return n > 3


def _piso_evidencia(brief_text, journal_text):
    """Piso de dificultad por evidencia del JOURNAL (hash-chain, tamper-evident).

    El journal manda; el brief es DISPLAY/auxiliar: solo se usa como fallback
    en la dimension que el journal no cubre (corrida vieja sin esos eventos).

    - floor 3: el journal trae intervencion `[gate]`, o wall (ultimo ts menos
      primero, append-only: no forjable sin romper la cadena) >3h.
    - floor 2: alguna oleada journaliza `N llamadas API` con N>0.
    - fallback brief (solo si el journal no alcanza): sin `N llamadas API` en
      oleadas -> Tokens con searches|ig|posts>0 da 2; con <2 ts validos ->
      Tiempo N>3 da 3. Sin nada, 1.
    """
    brief_text = brief_text or ""
    journal_text = journal_text or ""
    if "[gate]" in journal_text:
        return 3
    tss = _journal_tss(journal_text)
    api_ns = _journal_api_counts(journal_text)
    piso_journal = 1
    tiempo_conocido = len(tss) >= 2
    api_conocido = len(api_ns) > 0
    if tiempo_conocido:
        try:
            primero = tss[0].replace(tzinfo=None)
            ultimo = tss[-1].replace(tzinfo=None)
            delta = (ultimo - primero).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delta = 0
        if delta is not None and delta > 3 * 3600:
            return 3
        piso_journal = max(piso_journal, 1)
    if api_conocido and any(n > 0 for n in api_ns):
        piso_journal = 2
    if tiempo_conocido and api_conocido:
        return piso_journal
    piso = piso_journal
    if not api_conocido:
        piso = max(piso, _brief_api_floor(brief_text))
    if not tiempo_conocido and _brief_tiempo_mayor_3(brief_text):
        return 3
    return piso


def cmd_run_report(slug=None, runs_dir=None):
    base = Path(runs_dir) if runs_dir else RUNS
    if slug:
        targets = [_resolve_run(slug)]
    else:
        targets = sorted(
            [d for d in base.iterdir() if d.is_dir() and (d / "brief.md").exists()],
            key=lambda d: d.name,
        )
    if not targets:
        print("Sin corridas en runs/.")
        return 0
    cerradas = friccion0 = total0 = 0
    qw = pf = pt = 0
    sobre = 0
    racha = racha_max = 0
    for rdir in targets:
        try:
            brief_text = (rdir / "brief.md").read_text(encoding="utf-8")
        except OSError:
            brief_text = ""
        try:
            journal_text = (rdir / "journal.jsonl").read_text(encoding="utf-8")
        except OSError:
            journal_text = ""
        s = _summarize_journal(rdir / "journal.jsonl", journal_text)
        estado = "cerrada" if (rdir / "closeout.md").exists() else "abierta"
        iv = s["intervenciones"]
        if not s["instrumentada"]:
            print(f"- {rdir.name}: {estado} | sin instrumentar")
            racha = 0
            continue
        d_decl = _dificultad(rdir / "brief.md", brief_text)
        piso = _piso_evidencia(brief_text, journal_text)
        d = d_decl if d_decl > piso else piso
        estrella = "*" if d_decl > piso else ""
        w = _DIFICULTAD_PESOS.get(d, 0)
        print(
            f"- {rdir.name}: {estado} | pasos {s['pasos']} | "
            f"gate {iv['gate']} friccion {iv['friccion']} limite-externo {iv['limite-externo']} | D={d}{estrella} w={w}"
        )
        if estado == "cerrada":
            cerradas += 1
            if estrella:
                sobre += 1
            if iv["friccion"] == 0:
                friccion0 += 1
            if sum(iv.values()) == 0:
                total0 += 1
            qw += w
            if iv["friccion"] == 0:
                pf += w
            if sum(iv.values()) == 0:
                pt += w
            if d >= 2 and iv["friccion"] == 0:
                racha += 1
                racha_max = max(racha_max, racha)
            else:
                racha = 0
        else:
            racha = 0
    if slug is None:
        linea = (
            f"Corridas cerradas (instrumentadas): {cerradas} | "
            f"0 friccion: {friccion0} | 0 total: {total0}"
        )
        if sobre > 0:
            linea += f" | D* sobre-evidencia: {sobre} (prioritarias para sorteo-auditoría)"
        print(linea)
        if qw == 0:
            print(
                f"0 friccion: {friccion0}/{cerradas} raw + pond N/A (solo D1) | "
                f"0 total: {total0}/{cerradas} raw + pond N/A (solo D1)"
            )
        else:
            print(
                f"0 friccion: {friccion0}/{cerradas} raw + {pf}/{qw} pond | "
                f"0 total: {total0}/{cerradas} raw + {pt}/{qw} pond"
            )
        if racha_max >= 3:
            print(f"ALERTA: racha 0-fricción x{racha_max} (D≥2), auditar")
    return 0


def cmd_run_resume():
    print("1. Leé RUNSTATE.md completo (narrativa + NEXT STEP).")
    r = _resolve_run(None)
    print(f"2. Corrida activa (más reciente): runs/{r.name}/ -> brief.md, journal.jsonl, usage.json")
    cp = r / "checkpoint.md"
    if cp.exists():
        print(f"   Checkpoint vigente: {cp.read_text(encoding='utf-8').splitlines()[3:6]}")
        print("   (journal manda ante duda; el checkpoint es caché del último estado)")
    print("3. Reconciliá contra disco real (git status, mtimes) antes de seguir.")


def cmd_run_cost(slug):
    if not slug:
        _dashboard()
        return
    con = _db_ro()
    if con is None:
        return
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        "SELECT id FROM session WHERE title LIKE ? ORDER BY time_created",
        (f"%RUN:{slug}%",),
    )
    rows = cur.fetchall()
    con.close()
    if not rows:
        print(f"Sin sesion titulada RUN:{slug} en opencode.db.")
        print("Recordá: la sesion delegada debe arrancar con 'RUN:<slug>' (primer mensaje).")
        return
    t = {"n": 0, "input": 0, "output": 0, "reas": 0, "cread": 0, "cwrite": 0, "cost": 0.0}
    con = _db_ro()
    cur = con.cursor()
    for r in rows:
        s = _tokens_of(r["id"], cur)
        for k in t:
            t[k] += s[k]
    con.close()
    _print_tokens(t, f"RUN:{slug} ({len(rows)} sesion/es)")
    rdir = _resolve_run(slug)
    payload = {
        "slug": slug,
        "sessions": len(rows),
        "total": {
            "msgs": t["n"],
            "input": t["input"],
            "output": t["output"],
            "reasoning": t["reas"],
            "cache_read": t["cread"],
            "cache_write": t["cwrite"],
            "cost_usd": t["cost"],
        },
    }
    _atomic_write(rdir / "usage.json", json.dumps(payload, ensure_ascii=False, indent=1))
    print("usage.json escrito en", rdir)


def _dashboard():
    con = _db_ro()
    if con is None:
        return
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        "SELECT id, title, time_created, parent_id FROM session "
        "WHERE directory LIKE ? ORDER BY time_created DESC LIMIT 12",
        (f"%{ROOT.name}%",),
    )
    rows = cur.fetchall()
    if not rows:
        print("Sin sesiones en este directorio.")
        con.close()
        return
    for r in rows:
        t = _tokens_of(r["id"], cur)
        when = datetime.fromtimestamp(r["time_created"] / 1000).strftime("%d/%m %H:%M")
        sub = " (sub)" if r["parent_id"] else ""
        total = t["input"] + t["output"] + t["reas"] + t["cread"] + t["cwrite"]
        print(
            f"{when} | msgs={t['n']:>4} in={t['input']:>9,} out={t['output']:>7,} "
            f"reas={t['reas']:>6,} cacheR={t['cread']:>10,} $={t['cost']:.4f}{sub}"
        )
        print(f"         {r['title'][:90]}")
    con.close()


def _ensure_costs_header():
    if not COSTS.exists():
        _atomic_write(
            COSTS,
            "# Costos por corrida (tokens reales desde opencode.db, lectura read-only)\n\n"
            "| fecha | tarea | slug | est | input | output | reasoning | cacheR | efectivo | $USD |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n",
        )


def cmd_run_close(slug):
    rdir = _resolve_run(slug)
    slug = rdir.name
    if not (rdir / "usage.json").exists():
        cmd_run_cost(slug)
    if not (rdir / "usage.json").exists():
        print("Sin meter (no se encontro la sesion RUN:<slug>). Cierre de costos abortado.")
        return
    usage = json.loads((rdir / "usage.json").read_text(encoding="utf-8"))
    t = usage["total"]
    est = "-"
    tarea = slug
    for ln in (rdir / "brief.md").read_text(encoding="utf-8").splitlines():
        m = re.search(r"est ~([\d,]+)", ln)
        if m:
            est = m.group(1)
        m = re.match(r"^title:\s*(.+)$", ln.strip())
        if m:
            tarea = m.group(1).strip()[:30]
    _ensure_costs_header()
    eff = t["input"] + t["output"] + t["reasoning"] + t["cache_read"] + t["cache_write"]
    row = (
        f"| {date.today().isoformat()} | {tarea} | {slug} | {est} | "
        f"{t['input']:,} | {t['output']:,} | {t['reasoning']:,} | {t['cache_read']:,} | "
        f"{eff:,} | ${t['cost_usd']:.4f} |\n"
    )
    with COSTS.open("a", encoding="utf-8") as f:
        f.write(row)
    _journal_append(rdir, "close", "Costos registrados en costs.md")
    print("costs.md actualizado. Cierre:", rdir.name)
    print("Falta: narrativa del closeout en RUNSTATE + archivar corrida.")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, rest = argv[1], argv[2:]
    if cmd == "status":
        cmd_status()
    elif cmd == "next":
        cmd_next()
    elif cmd == "add":
        cmd_add(" ".join(rest))
    elif cmd == "done":
        cmd_done(" ".join(rest))
    elif cmd == "learn":
        cmd_learn(" ".join(rest))
    elif cmd == "memadd":
        if len(rest) < 1:
            print("Uso: python agent.py memadd TAG \"texto\"")
            return 1
        tag = rest[0]
        cmd_memadd(tag, " ".join(rest[1:]))
    elif cmd == "memfind":
        cmd_memfind(" ".join(rest))
    elif cmd == "memstats":
        cmd_memstats()
    elif cmd == "run":
        return _cmd_run(rest)
    elif cmd == "golden":
        return _cmd_golden(rest)
    elif cmd == "doctor":
        return cmd_doctor()
    elif cmd == "ask":
        return cmd_ask(" ".join(rest))
    elif cmd == "verify":
        vflags, _ = _parse_args(rest)
        return cmd_verify(vflags.get("scope", "") or "all")
    elif cmd == "skeleton":
        sflags, _ = _parse_args(rest)
        return cmd_skeleton(sflags.get("out", "") or "skeleton")
    else:
        print(f"Comando desconocido: {cmd}\n{__doc__}")
        return 1
    return 0


def _parse_args(args):
    flags = {}
    pos = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            name = a[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[name] = args[i + 1]
                i += 2
            else:
                flags[name] = True
                i += 1
        else:
            pos.append(a)
            i += 1
    return flags, pos


def _cmd_run(args):
    if not args:
        print(
            "Uso: python agent.py run "
            "new | budget | log | status | cost | close | resume | checkpoint | "
            "intervene | step | report"
        )
        return 1
    sub, rest = args[0], args[1:]
    flags, pos = _parse_args(rest)
    slug = flags.get("slug")
    if sub == "new":
        cmd_run_new(" ".join(pos), flags.get("hours", "2"))
    elif sub == "budget":
        cmd_run_budget(
            slug,
            int(flags.get("searches", "0")),
            int(flags.get("ig", "0")),
            int(flags.get("posts", "3")),
            int(flags.get("readkb", "0")),
        )
    elif sub == "log":
        cmd_run_log(slug, " ".join(pos))
    elif sub == "status":
        cmd_run_status(slug)
    elif sub == "cost":
        cmd_run_cost(slug)
    elif sub == "close":
        cmd_run_close(slug)
    elif sub == "resume":
        cmd_run_resume()
    elif sub == "checkpoint":
        nxt = flags.get("next", "")
        return cmd_run_checkpoint(slug, " ".join(pos), nxt)
    elif sub == "intervene":
        if slug:
            tipo, motivo = (pos[0] if pos else ""), " ".join(pos[1:])
        else:
            slug = pos[0] if pos else ""
            tipo = pos[1] if len(pos) > 1 else ""
            motivo = " ".join(pos[2:])
        return cmd_run_intervene(slug, tipo, motivo)
    elif sub == "step":
        if slug:
            desc = " ".join(pos)
        else:
            slug = pos[0] if pos else ""
            desc = " ".join(pos[1:])
        return cmd_run_step(slug, desc)
    elif sub == "report":
        return cmd_run_report(slug)
    else:
        print(f"Subcomando desconocido: {sub}")
        return 1
    return 0


GOLDEN_DIR = ROOT / "golden"

_DECISIONS = ("califica", "no_califica", "descarte")


def _load_checks(task, gdir=None):
    gdir = gdir or GOLDEN_DIR
    path = gdir / "checks" / f"{task}.py"
    if not task or not path.exists():
        print(f"Sin modulo checks para task '{task}' ({gdir}/checks/{task}.py).")
        return None
    spec = importlib.util.spec_from_file_location(f"golden_checks_{task}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _validate_golden_row(row):
    errs = []
    if not isinstance(row, dict):
        return ["fila no es objeto"]
    for k in ("id", "task", "weight", "frozen_at", "input", "expected"):
        if k not in row:
            errs.append(f"falta {k}")
    if errs:
        return errs
    if not re.match(r"^[A-Z]+-[A-Z0-9]+$", str(row.get("id", ""))):
        errs.append("id invalida")
    if not re.match(r"^[a-z0-9_]+$", str(row.get("task", ""))):
        errs.append("task invalida")
    w = row.get("weight")
    if not isinstance(w, (int, float)) or w < 1:
        errs.append("weight >= 1")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(row.get("frozen_at", ""))):
        errs.append("frozen_at YYYY-MM-DD")
    if not isinstance(row.get("input"), dict):
        errs.append("input debe ser objeto")
    exp = row.get("expected")
    if not isinstance(exp, dict):
        errs.append("expected debe ser objeto")
    else:
        if exp.get("decision") not in _DECISIONS:
            errs.append("expected.decision invalida")
        for k in ("score", "truth_source"):
            if k not in exp:
                errs.append(f"falta expected.{k}")
        if exp.get("decision") == "descarte" and exp.get("score") is not None:
            errs.append("descarte con score no-null")
        if exp.get("score") is not None and not isinstance(exp.get("score"), (int, float)):
            errs.append("expected.score numerico o null")
    return errs


def _read_jsonl(path):
    rows = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            rows.append(json.loads(ln))
    return rows


def cmd_golden_build(gdir=None):
    gdir = gdir or GOLDEN_DIR
    ds = gdir / "datasets"
    files = sorted(ds.glob("*.jsonl")) if ds.exists() else []
    if not files:
        print(f"Sin datasets en {gdir}/datasets/.")
        return 1
    ok_rows, problems = [], 0
    for f in files:
        for row in _read_jsonl(f):
            errs = _validate_golden_row(row)
            mod = _load_checks(row.get("task", ""), gdir)
            if mod is None:
                errs.append("sin modulo checks")
            else:
                errs += [f"checks: {e}" for e in mod.validate(row)]
            if errs:
                problems += 1
                print(f"MAL {f.name} {row.get('id', '?')}: {'; '.join(errs)}")
            else:
                ok_rows.append(row)
    by_id = {}
    for r in ok_rows:
        by_id[r["id"]] = r
    changed = True
    while changed:
        changed = False
        for r in list(by_id.values()):
            sup = r.get("supersedes")
            if sup and sup in by_id:
                del by_id[sup]
                changed = True
    out = gdir / "golden.jsonl"
    _atomic_write(
        out,
        "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
            for r in sorted(by_id.values(), key=lambda r: r["id"])
        ),
        encoding="utf-8",
    )
    tasks = {}
    for r in by_id.values():
        tasks[r["task"]] = tasks.get(r["task"], 0) + 1
    print(f"golden.jsonl: {len(by_id)} filas {tasks} ({problems} problemas)")
    return 1 if problems else 0


def _load_golden(task=None):
    path = GOLDEN_DIR / "golden.jsonl"
    if not path.exists():
        print("Falta golden/golden.jsonl (corre: python agent.py golden build).")
        return []
    rows = _read_jsonl(path)
    if task:
        rows = [r for r in rows if r.get("task") == task]
    return rows


def cmd_golden_rescore(task, rubric_path, out_path):
    if not task or not rubric_path or not out_path:
        print("Uso: python agent.py golden rescore --task <task> --rubric <f> --out <hoja>")
        return 1
    rub = json.loads(Path(rubric_path).read_text(encoding="utf-8"))
    mod = _load_checks(task)
    if mod is None:
        return 1
    rows = _load_golden(task)
    if not rows:
        print(f"Sin filas para task '{task}'.")
        return 1
    auto, blind = 0, []
    for r in rows:
        hf = mod.hard_filter(r)
        exp_dec = r.get("expected", {}).get("decision")
        if exp_dec == "descarte":
            if not hf:
                print(f"INCONSISTENTE {r['id']}: expected descarte pero el filtro no muerde.")
                return 1
            auto += 1
        else:
            if hf:
                print(f"INCONSISTENTE {r['id']}: expected {exp_dec} pero el filtro muerde ({hf}).")
                return 1
            blind.append(
                {
                    "id": r["id"],
                    "task": task,
                    "input": r["input"],
                    "rubric": rub.get("version", "?"),
                    "score_dims": [d["key"] for d in rub.get("dimensions", [])],
                }
            )
    Path(out_path).write_text(
        "".join(json.dumps(b, ensure_ascii=False) + "\n" for b in blind), encoding="utf-8"
    )
    print(f"rescore {task}: {auto} auto-descarte (codigo $0) + {len(blind)} a puntuar ciego -> {out_path}")
    print("La sesion que puntua NO debe leer golden.jsonl, datasets/ ni results.jsonl.")
    return 0


def cmd_golden_diff(scored_path, task, rubric_path, scorer="", dry_run=False):
    if not scored_path or not task or not rubric_path:
        print("Uso: python agent.py golden diff <hoja> --task <t> --rubric <f> [--scorer <s>]")
        return 1
    rub = json.loads(Path(rubric_path).read_text(encoding="utf-8"))
    tol = float(rub.get("score_tolerance", 0.5))
    rubver = rub.get("version", "?")
    mod = _load_checks(task)
    if mod is None:
        return 1
    rows = {r["id"]: r for r in _load_golden(task)}
    if not rows:
        return 1
    scored = {s["id"]: s for s in _read_jsonl(Path(scored_path))}
    fails, wp, wt, npass, ntot = [], 0.0, 0.0, 0, 0
    for rid in sorted(rows):
        r = rows[rid]
        w = float(r.get("weight", 1))
        exp = r.get("expected", {})
        wt += w
        ntot += 1
        if exp.get("decision") == "descarte":
            hf = mod.hard_filter(r)
            ok = bool(hf)
            detail = f"filtro {hf}" if ok else "el filtro NO muerde (suite inconsistente)"
        else:
            s = scored.get(rid)
            if s is None:
                ok, detail = False, "sin puntuar (FAIL: no evadir)"
            else:
                try:
                    sval = float(s.get("score"))
                except (TypeError, ValueError):
                    ok, detail = False, (
                        "hoja MALFORMADA: score ausente o no-numerico "
                        "(el contrato pide clave 'score'; ver rubrica v3)"
                    )
                else:
                    dec = mod.decide(sval, s.get("dims") or {}, r)
                    close = exp.get("score") is not None and abs(sval - float(exp.get("score"))) <= tol
                    ok = dec == exp.get("decision") and close
                    detail = f"viene {dec}/{s.get('score')} vs esp {exp.get('decision')}/{exp.get('score')}"
        if ok:
            npass += 1
            wp += w
            print(f"  PASS {rid} (w{w:g}) {detail}")
        else:
            fails.append(rid)
            print(f"  FAIL {rid} (w{w:g}) {detail}")
    for sid in sorted(set(scored) - set(rows)):
        print(f"  AVISO {sid}: puntuado pero no esta en golden (se ignora)")
    pct = (100.0 * wp / wt) if wt else 0.0
    veto = any(float(rows[f].get("weight", 1)) >= 3 for f in fails)
    res = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task": task,
        "rubric": rubver,
        "code_sha": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12],
        "raw_pass": npass,
        "raw_total": ntot,
        "weighted_pct": round(pct, 1),
        "fails": fails,
        "veto": veto,
        "scorer": scorer,
    }
    _chain_seal(GOLDEN_DIR / "results.jsonl", res)
    _trace(
        "golden_diff",
        {
            "task": task,
            "rubric": rubver,
            "raw": f"{npass}/{ntot}",
            "weighted": pct,
            "veto": veto,
            "scorer": scorer,
            "dry_run": dry_run,
        },
    )
    if dry_run:
        print("DRY-RUN: no se escribe results.jsonl.")
    else:
        with open(GOLDEN_DIR / "results.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
    print(f"Raw: {npass}/{ntot} | Weighted: {pct:.1f}% | fails: {fails or '-'}")
    if veto:
        print("VETO DURO: regresion en caso con verdad humana (peso 3) -> KEEP BLOQUEADO.")
    else:
        print("Sin veto.")
    return 0


def cmd_golden_sample(task, run_slug, input_json, output_json, reason):
    if not task:
        print("Uso: golden sample --task <t> --run <slug> --input '<json>' --output '<json>' [--reason <m>]")
        return 1
    qpath = GOLDEN_DIR / "live" / "live_queue.jsonl"
    existing = _read_jsonl(qpath) if qpath.exists() else []
    sid = f"LQ-{date.today().isoformat()}-{len(existing) + 1:03d}"
    entry = {
        "sample_id": sid,
        "task": task,
        "run_slug": run_slug,
        "input": json.loads(input_json or "{}"),
        "system_output": json.loads(output_json or "{}"),
        "reason": reason or "manual",
        "sampled_at": date.today().isoformat(),
        "status": "pending",
    }
    with open(qpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"Encolado {sid} ({task}, motivo: {entry['reason']}). Revisa un humano y luego:")
    print(f"  python agent.py golden promote {sid} --id <ID> --score <n> --decision <d>")
    return 0


def cmd_golden_promote(sample_id, new_id, score, decision):
    if not sample_id or not new_id or score is None or decision not in _DECISIONS:
        print("Uso: golden promote <sample_id> --id <ID> --score <n> --decision <califica|no_califica|descarte>")
        return 1
    _lock_acquire("promote")
    try:
        qpath = GOLDEN_DIR / "live" / "live_queue.jsonl"
        queue = _read_jsonl(qpath) if qpath.exists() else []
        found = next((e for e in queue if e.get("sample_id") == sample_id), None)
        if found is None:
            print(f"Sample {sample_id} no encontrado.")
            return 1
        if found.get("status") != "pending":
            print(f"Sample {sample_id} ya procesado ({found.get('status')}).")
            return 1
        found["status"] = "promoted"
        _atomic_write(qpath, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in queue))
    finally:
        _lock_release("promote")
    op = (os.environ.get("AGENT_OPERATOR", "") or "humano").strip() or "humano"
    row = {
        "id": new_id,
        "task": found["task"],
        "weight": 3,
        "frozen_at": date.today().isoformat(),
        "input": found["input"],
        "expected": {
            "decision": decision,
            "score": score,
            "truth_source": f"humano:{op} {date.today().isoformat()} (live {sample_id})",
        },
    }
    errs = _validate_golden_row(row)
    if errs:
        print(f"Fila invalida: {errs}")
        return 1
    dpath = GOLDEN_DIR / "datasets" / f"{found['task']}.jsonl"
    with open(dpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    ppath = GOLDEN_DIR / "live" / "promoted.jsonl"
    with open(ppath, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"sample_id": sample_id, "golden_id": new_id, "promoted_at": date.today().isoformat(), "reason": found.get("reason", "")},
                ensure_ascii=False,
            )
            + "\n"
        )
    print(f"Promovido {sample_id} -> {new_id} en datasets/{found['task']}.jsonl. Corre golden build para congelar.")
    return 0


def _cmd_golden(args):
    if not args:
        print("Uso: python agent.py golden build | rescore | diff | sample | promote | autopsy | lineage | impact")
        return 1
    sub, rest = args[0], args[1:]
    flags, pos = _parse_args(rest)
    if sub == "build":
        return cmd_golden_build()
    elif sub == "rescore":
        return cmd_golden_rescore(flags.get("task", ""), flags.get("rubric", ""), flags.get("out", ""))
    elif sub == "diff":
        if not pos:
            print("Uso: python agent.py golden diff <hoja> --task <t> --rubric <f> [--scorer <s>]")
            return 1
        return cmd_golden_diff(pos[0], flags.get("task", ""), flags.get("rubric", ""), flags.get("scorer", ""), bool(flags.get("dry-run", False)))
    elif sub == "sample":
        return cmd_golden_sample(
            flags.get("task", ""), flags.get("run", ""), flags.get("input", "{}"), flags.get("output", "{}"), flags.get("reason", "manual")
        )
    elif sub == "promote":
        sc = flags.get("score")
        score = None if sc in (None, True) else float(sc)
        return cmd_golden_promote(pos[0] if pos else "", flags.get("id", ""), score, flags.get("decision", ""))
    elif sub == "autopsy":
        return cmd_golden_autopsy(flags.get("task", ""))
    elif sub == "lineage":
        return cmd_golden_lineage(pos[0] if pos else "")
    elif sub == "impact":
        return cmd_golden_impact(flags.get("task", ""))
    else:
        print(f"Subcomando desconocido: {sub}")
        return 1
    return 0


# --- Doctor (auditor read-only) + Skeleton (generador publico) --------
# F0 blindaje. doctor NUNCA escribe: solo lee e informa. Exit 0 = OK, 1 = bloquea push.

_SECRET_RES = [
    (re.compile(r"(?i)sessionid\s*=\s*(\S+)"), "posible sessionid de IG"),
    (re.compile(r"(?i)csrftoken\s*=\s*(\S+)"), "posible csrftoken de IG"),
    (re.compile(r"(?i)(?:password|passwd|secret|api_key)\s*=\s*(\S+)"), "posible credencial"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "clave privada"),
    (re.compile(r"EAA[A-Za-z0-9_-]{20,}"), "posible token Meta"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\s\"']+"), "ruta absoluta Windows"),
    (re.compile("/ho" + "me/[^\\s\"':]+"), "ruta absoluta unix"),
]
# Valores de credencial/sesion mas cortos que esto son texto informativo, no secretos.
_MIN_SECRET_LEN = 4
_PLACEHOLDER_RES = [
    re.compile(r"cambia-", re.I),
    re.compile(r"changeme", re.I),
    re.compile(r"<[^>]+>"),
    re.compile(r"x{3,}", re.I),
    re.compile(r"ejemplo", re.I),
    re.compile(r"example", re.I),
]
_GITIGNORE_REQUIRED = [
    "cookies*.txt",
    "*.priv/",
    "*.pem",
    "*.key",
    ".env*",
    "!.env.mobile.example",
]
_SKIP_SCAN_RES = [
    re.compile(r"\.example$"),
    re.compile(r"skeleton/build/"),
    re.compile(r"__pycache__"),
    re.compile(r"\.pyc$"),
]


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_snapshot(root):
    snap = {}
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel or rel.endswith(".pyc"):
            continue
        try:
            snap[rel] = _sha256_file(p)
        except OSError:
            snap[rel] = "UNREADABLE"
    return snap


def _tracked_files():
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        if out.returncode == 0 and out.stdout.strip():
            return [ROOT / ln for ln in out.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for p in sorted(ROOT.rglob("*")):
        if p.is_file() and ".git" not in p.parts and "skeleton/build" not in p.as_posix():
            found.append(p)
    return found


def _is_placeholder(value):
    v = value.strip().strip("\"'")
    if not v or v.startswith("$"):
        return True
    return any(rx.search(v) for rx in _PLACEHOLDER_RES)


def _scan_secrets(files):
    hits = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        if any(rx.search(rel) for rx in _SKIP_SCAN_RES):
            continue
        try:
            if p.stat().st_size > 2_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for rx, label in _SECRET_RES:
                m = rx.search(line)
                if not m:
                    continue
                val = m.group(1) if m.lastindex else m.group(0)
                if len(val.strip().strip("\"'")) < _MIN_SECRET_LEN and m.lastindex:
                    continue
                if _is_placeholder(val):
                    continue
                hits.append(f"{rel}:{i}: {label}")
                break
    return hits


def _check_gitignore():
    gi = ROOT / ".gitignore"
    if not gi.exists():
        return ["falta .gitignore"]
    body = gi.read_text(encoding="utf-8")
    return [f".gitignore sin patron requerido: {pat}" for pat in _GITIGNORE_REQUIRED if pat not in body]


def _check_integrity_tmp():
    """golden build sobre COPIA TEMPORAL (nunca escribe el repo). Devuelve (fails, warns)."""
    fails, warns = [], []
    if not (ROOT / "LICENSE").exists():
        fails.append("falta LICENSE")
    with tempfile.TemporaryDirectory(prefix="siafb-doc-") as tmp:
        gd = Path(tmp) / "golden"
        (gd / "datasets").mkdir(parents=True)
        (gd / "checks").mkdir(parents=True)
        (gd / "rubrics").mkdir(parents=True)
        for sub in ("datasets", "checks", "rubrics"):
            src = GOLDEN_DIR / sub
            if src.exists():
                for f in sorted(src.glob("*")):
                    if f.is_file():
                        shutil.copy2(f, gd / sub / f.name)
        (gd / "schema.json").write_text(
            (GOLDEN_DIR / "schema.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        rc = cmd_golden_build(gd)
        if rc != 0:
            fails.append(f"golden build sobre copia temporal fallo (rc={rc})")
    return fails, warns


def cmd_doctor():
    fails, warns = [], []
    gj = GOLDEN_DIR / "golden.jsonl"
    before = _sha256_file(gj) if gj.exists() else None

    files = _tracked_files()
    hits = _scan_secrets(files)
    if hits:
        fails += [f"secreto: {h}" for h in hits]
    print(f"doctor: secretos ........ {'FAIL' if hits else 'OK'} ({len(files)} archivos)")

    gi_fails = _check_gitignore()
    fails += gi_fails
    print(f"doctor: higiene ......... {'FAIL' if gi_fails else 'OK'}")

    int_fails, int_warns = _check_integrity_tmp()
    fails += int_fails
    warns += int_warns
    print(f"doctor: integridad ...... {'FAIL' if int_fails else 'OK'} (build en copia temp)")

    sk = ROOT / "skeleton"
    if not sk.exists():
        warns.append("skeleton/ no existe: SKIP (se genera en PASO 5)")
        print("doctor: skeleton ........ SKIP (no existe)")
    else:
        with tempfile.TemporaryDirectory(prefix="siafb-skel-") as tmp:
            fresh = Path(tmp) / "fresh"
            build_skeleton(fresh)
            a, b = _tree_snapshot(sk), _tree_snapshot(fresh)
            diff = sorted(set(a) ^ set(b) | {k for k in set(a) & set(b) if a[k] != b[k]})
            if diff:
                fails.append(f"skeleton desactualizado ({len(diff)} diffs; regenerar)")
                for d in diff[:10]:
                    warns.append(f"  diff: {d}")
            print(f"doctor: skeleton ........ {'FAIL' if diff else 'OK'}")

    after = _sha256_file(gj) if gj.exists() else None
    if before != after:
        fails.append("doctor ESCRIBIO golden.jsonl (violacion read-only)")
    print(f"doctor: read-only ...... {'FAIL' if before != after else 'OK'} (golden.jsonl intacto)")

    for w in warns:
        print(f"  warn: {w}")
    if fails:
        print(f"doctor: RESULTADO FAIL ({len(fails)} problemas)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("doctor: RESULTADO OK")
    return 0


# --- Skeleton: plantillas genericas embebidas (sin negocio) -----------------

_SKEL_README = """# Self-Improving Business Agent (beta publica)

Agente de negocio con auto-mejora, operado por archivos (el estado vive en disco, no en la ventana
de contexto). Investiga con subagentes, prospecta con evidencia y se evalua con un Golden Set con
veto: ninguna regla cambia sin pasar el examen. Sin dependencias (stdlib-only), sin nube obligatoria.

Licencia: MIT (ver `LICENSE`). Estado: beta — la ingenieria esta probada en produccion privada;
los numeros de ejemplo son sinteticos.

## Instalacion guiada (5 minutos)

1. `powershell -ExecutionPolicy Bypass -File scripts/install-beta.ps1` — verifica Python,
   crea credenciales locales y corre la auditoria.
2. Lee `QUICKSTART.md` — tu primera corrida paso a paso.
3. `python agent.py doctor` — verde antes de cada push.

## Que hace solo / que te pide

- Solo: investigar (lectura), puntuar con evidencia, correr oleadas, medir su autonomia,
  commitear local, revivirse en mobile.
- Te pide (gates): gastar dinero, enviar mensajes, publicar, borrar, credenciales,
  cualquier cosa irreversible. Eso no se automatiza por diseño.

## Estructura

- `agent.py` — CLI stdlib, cero dependencias.
- `AGENTS.md` — reglas y protocolo de reanudacion ("segui" / "en que estabamos").
- `workflows/` — subagentes deterministicos: investigar, prospectar, juzgar, sandbox
  (prompt + ambiente + tools fijos y versionados).
- `RUNSTATE.md` — punto unico de reanudacion (empeza por aca).
- `strategy.md` — loop de mejora + formato KEEP/REJECT.
- `golden/` — framework de evaluacion con veto (datasets DEMO sinteticos).
- `runs/` — templates de brief y closeout.
- `COMO_FUNCIONA.md` — explicacion simple del sistema.

## Defini tu negocio

Edita `RUNSTATE.md` (seccion "Tarea actual"), `tasks.txt` y `strategy.md` con tu
negocio (<TU-NEGOCIO>), tu cliente ideal (<TU-ICP>) y tu oferta. Nada de eso viene incluido.
"""

_SKEL_QUICKSTART = """# QUICKSTART (5 minutos)

1. `powershell -ExecutionPolicy Bypass -File scripts/install-beta.ps1`
2. `python agent.py status` — ver tareas demo.
3. `python agent.py golden build` — validar el Golden Set sintetico (tiene que decir 0 problemas).
4. `python agent.py run new "mi primera corrida" --hours 2` — abrir una corrida.
5. `python agent.py run budget --slug <slug> --searches 0 --ig 0 --posts 0 --readkb 5`
6. Trabaja por pasos; cierra con `run close` y `run report`.
7. Cerra la sesion y abri otra: deci "en que estabamos" — el agente retoma sin contexto.
8. `python agent.py doctor` antes de cada push. Verde = push permitido.

Si algo falla 3 veces igual: para, lee el error, no insistas en rafaga.
"""

_SKEL_AGENTS = """# <TU-PROYECTO>

Agente de negocio con capacidad de auto-mejora.

## Estructura
- `README.md` — descripcion del proyecto
- `AGENTS.md` — instrucciones para agentes (este archivo)
- `opencode.json` — config del proyecto para opencode

## Convenciones
- Proponer plan antes de cambios grandes.
- Verificar cada paso antes de avanzar.
- No inventar datos: usar solo fuentes reales y verificables.
- Commits pequeños y mensajes concisos.

## Reanudacion (cualquier sesion nueva, cualquier modelo)

El estado NO vive en el contexto de la conversacion: vive en `RUNSTATE.md` (raiz).
Este archivo se lee siempre al arrancar una sesion sobre este proyecto.

**Triggers del usuario:**
- `"segui"` / `"continua"` / `"dale"` → protocolo de reanudacion y ejecutar el NEXT STEP directo.
- `"en que estabamos"` / `"en que vamos"` / `"que hay"` → protocolo de reanudacion, RESUME en ≤6 lineas,
  proponer proximo paso y pedir confirmacion. NO tocar archivos antes del OK.
- Cualquier otra cosa sin contexto → empezar por el protocolo igual (nunca asumir que el usuario trae contexto).

**Protocolo de reanudacion (en este orden):**
1. Leer `RUNSTATE.md` completo. Si indica un brief activo, leer `runs/<slug>/brief.md`.
2. Reconciliar contra disco real: `git status`, mtimes de `Archivos tocados`. Si el doc contradice el disco,
   corregir `RUNSTATE.md` con lo que la realidad dice.
3. Resumir: objetivo, paso actual M de N, ultimo resultado, proxima decision pendiente.
4. `"segui"` → ejecutar NEXT STEP y persistir el turnpoint con la tool de edicion DESPUES de cada paso.
5. `"en que estabamos"` → proponer el proximo paso y esperar confirmacion.
6. Divergencia irreconciliable doc↔disco → reportar ambas versiones, NUNCA adivinar.

**Regla de escritura (invariante):** nunca terminar un paso sin actualizar `RUNSTATE.md`.
Persistir el Estado ANTES de cada paso dificil/irreversible. Un paso es re-ejecutable sin daño (idempotente).

**Gates (lo unico que frena una corrida):** gastar dinero, enviar mensajes, publicar, borrar informacion,
modificar sistemas externos, usar credenciales, acciones irreversibles. Todo lo demas corre solo.

**Un agente, un proyecto:** el estado, las metricas y los costos de este agente se acotan a la carpeta
de ESTE repo. Sesiones que corrieron en otras carpetas son de OTRO proyecto: no se citan aqui.

**Presupuesto de tokens:** cada corrida lleva estimado en su brief (`python agent.py run budget`) y un
limite de 2x el estimado. Si el meter real lo supera, cortar en el proximo checkpoint. Al cerrar,
`python agent.py run close` mide el uso real y actualiza `costs.md`.
"""

_SKEL_STRATEGY = """# Estrategia — v0.1 (plantilla)

## Objetivos del agente

Negocio: **<TU-NEGOCIO>** — <1 linea: que vende>.
- Cliente ideal (<TU-ICP>): <1 linea>.
- Oferta: <1 linea>.
- Objetivo 1: <medible>.

## Como opero

Loop por cada tarea u objetivo:

TASK → PLAN → EXECUTE → RESULT → EVALUATE → IDENTIFY BOTTLENECK →
RESEARCH → EXPERIMENT → MEASURE → KEEP / REJECT →
UPDATE MEMORY + STRATEGY → NEXT ITERATION

## Reglas

1. ROI > complejidad. Si una mejora no da valor medible, se descarta.
2. Actividad ≠ mejora. Cuenta mejor resultado, menor tiempo, menor costo o nueva capacidad util.
3. Cada herramienta nueva debe justificar: problema → solucion → prueba → resultado.
4. Pido autorizacion antes de: gastar dinero, enviar mensajes, publicar, borrar informacion, modificar sistemas externos, usar credenciales, acciones irreversibles.
5. Reanudacion (invariante): el estado vive en `RUNSTATE.md`, NO en el contexto. Nunca terminar un paso sin actualizarlo. Pasos idempotentes y re-ejecutables.
6. Ninguna regla cambia sin pasar el Golden Set (`python agent.py golden rescore/diff`); regresion en caso peso 3 = VETO.

## Cambios de estrategia

Cuando un experimento demuestra una mejor forma de operar, lo registro asi:

- Problema:
- Hipotesis:
- Cambio:
- Resultado anterior:
- Resultado nuevo:
- Evidencia:
- Decision: KEEP / REJECT

## Historial de cambios

- v0.1: bootstrap del esqueleto. Sin objetivo todavia; sin historial medible.
"""

_SKEL_TASKS = """# Tareas — se gestionan con: python agent.py status | next | add | done

[ ] Definir negocio, cliente ideal y oferta en RUNSTATE.md + strategy.md
[ ] Primera corrida con meter (run new + budget + close)
[ ] Reemplazar las filas DEMO del golden por casos reales del negocio
"""

_SKEL_RUNSTATE = """# RUNSTATE — punto unico de reanudacion

Actualizado: <FECHA> (bootstrap del esqueleto).

> Este archivo ES el estado del proyecto. Se reescribe con la tool de edicion despues de cada paso.
> Cualquier sesion nueva (mismo modelo u otro) lo lee y puede continuar sin contexto previo.
> Al cerrar una corrida, se archiva en `runs/<slug>/closeout.md` y se crea el nuevo RUNSTATE.

## Narrativa (para reanudar sin contexto)

Proyecto "<TU-PROYECTO>": un agente de negocio para <TU-NEGOCIO> que se auto-mejora.
Cliente ideal (<TU-ICP>): <1 linea>.
Base lista: reanudacion por archivos, meter de tokens reales, Golden Set demo (filas sinteticas).

## Tarea actual

- NEXT STEP: definir negocio, cliente ideal y oferta; luego primera corrida con meter.

## Decisiones

- (vacio: 1 linea por decision con fecha)

## Gates (necesitan al operador)

- Gastar dinero, enviar mensajes, publicar, borrar informacion, credenciales, acciones irreversibles.

## Archivos tocados

- (1 linea por archivo tocado, se actualiza en cada paso)

## Historial de corridas

- (1 linea por corrida cerrada: slug + resultado)
"""

_SKEL_GOLDEN_README = """# Golden Set — demostrar que el agente mejora (con numeros, no opiniones)

Cada cambio de reglas se mide contra ejemplos congelados de resultado conocido.
Si el pass-rate baja o un caso con verdad humana regresa → la "mejora" era un
empeoramiento → KEEP bloqueado.

## Estructura

- `schema.json`: contrato tipado de una fila (el `build` lo hace cumplir).
- `datasets/<task>.jsonl`: fuente por task (agregar una task = sumar 1 archivo aca).
  Las filas DEMO son sinteticas: reemplazalas por casos reales de tu negocio.
- `golden.jsonl`: COMPILADO congelado (lo genera `build`; no se edita a mano).
- `results.jsonl`: append-only con el scoreboard por corrida de evaluacion.
- `rubrics/<task>.<ver>.json`: rubrica modular (tolerancia, dimensiones, prompt ciego).
- `checks/<task>.py`: filtros duros de la task (interfaz fija: `hard_filter/decide/validate`).
- `live/`: muestreo de produccion pendiente de revision humana + auditoria de promociones.
- `pending/`: hojas ciegas de trabajo (que puntua la sesion ciega, sin expected).

## Reglas de rigor

- Decision exacta obligatoria (`califica|no_califica|descarte`); score con tolerancia de la rubrica.
- Filas inmutables: corregir = agregar fila con `supersedes`; el core resuelve la ultima.
- Peso 3 = verdad humana documentada. Regresion en peso 3 = **VETO DURO**: KEEP bloqueado.
- Scoreboard con ambos: Raw X/Y y Weighted Z%.
- `input.gate` congelado preserva el juicio del momento (no se re-derivan filtros viejos).

## Uso

```
python agent.py golden build                                   # valida + congela golden.jsonl
python agent.py golden rescore --task <task> --rubric <rubrics/f.json> --out <hoja>
python agent.py golden diff <hoja-puntuada> --task <task> --rubric <rubrics/f.json>
python agent.py golden sample --task <task> --run <slug> --input '<json>' --output '<json>' --reason <motivo>
python agent.py golden promote <sample_id> --id <NUEVO-ID> --score <n> --decision <d>
```
"""

_SKEL_CHECKS_DEMO = '''"""Checks de ejemplo para la task demo_basico (esqueleto generico).

Interfaz fija que el core (agent.py golden) importa por nombre de task:
  hard_filter(row) -> str | None   razon de descarte, o None si pasa el gate
  decide(total, dims, row) -> str  califica | no_califica (solo si pasa el gate)
  validate(row) -> list[str]       errores de consistencia (vacia = ok)

Reemplaza este modulo por los filtros reales de tu negocio manteniendo la interfaz.
"""


def hard_filter(row):
    gate = (row.get("input") or {}).get("gate", "")
    if isinstance(gate, str) and gate.startswith("descarte"):
        return gate
    return None


def decide(total, dims, row):
    try:
        total = float(total)
    except (TypeError, ValueError):
        return "no_califica"
    if total >= 7:
        return "califica"
    return "no_califica"


def validate(row):
    errs = []
    inp = row.get("input") or {}
    exp = row.get("expected") or {}
    gate = inp.get("gate", "")
    if exp.get("decision") == "descarte" and not (
        isinstance(gate, str) and gate.startswith("descarte")
    ):
        errs.append("expected=descarte pero input.gate no es descarte")
    if isinstance(gate, str) and gate.startswith("descarte") and exp.get("decision") != "descarte":
        errs.append("input.gate es descarte pero expected.decision no")
    if exp.get("decision") == "descarte" and exp.get("score") is not None:
        errs.append("descarte debe llevar score null")
    return errs
'''

_SKEL_RUBRIC_DEMO = (
    '{"version": "v1", "task": "demo_basico", "score_tolerance": 0.5, '
    '"decision_rule": "califica si total>=7; si filtro-duro dispara -> descarte (no se puntua)", '
    '"score_scale": {"min": 0, "max": 10, "step": 1}, '
    '"dimensions": ['
    '{"key": "A", "max": 5, "rubric": "Criterio A de ejemplo (0-5). Reemplazar por dimension real."}, '
    '{"key": "B", "max": 5, "rubric": "Criterio B de ejemplo (0-5). Reemplazar por dimension real."}], '
    '"blind_prompt_template": "Puntua este caso con las dimensiones A/B (0-5 cada una). '
    'Usa SOLO la evidencia dada. Devuelve JSON exacto con id, dims, total y notes. No inventes datos."}'
)

_SKEL_DATASET_DEMO = (
    '{"id": "DEMO-001", "task": "demo_basico", "weight": 1, "frozen_at": "2026-09-19", '
    '"input": {"gate": "pass", "caso": "Caso Demo Uno (sintetico)", "metricas": {"A": 4, "B": 4}}, '
    '"expected": {"decision": "califica", "score": 8, '
    '"truth_source": "skeleton:demo-sintetico (NO es verdad humana)"}}\n'
    '{"id": "DEMO-002", "task": "demo_basico", "weight": 1, "frozen_at": "2026-09-19", '
    '"input": {"gate": "pass", "caso": "Caso Demo Dos (sintetico)", "metricas": {"A": 3, "B": 2}}, '
    '"expected": {"decision": "no_califica", "score": 5, '
    '"truth_source": "skeleton:demo-sintetico (NO es verdad humana)"}}\n'
    '{"id": "DEMO-003", "task": "demo_basico", "weight": 1, "frozen_at": "2026-09-19", '
    '"input": {"gate": "descarte:ejemplo-sin-datos", "caso": "Caso Demo Tres (sintetico)"}, '
    '"expected": {"decision": "descarte", "score": null, '
    '"truth_source": "skeleton:demo-sintetico (NO es verdad humana)"}}\n'
)

_MOBILE_REPLACEMENTS = [
    ("self improving business agent", "<TU-PROYECTO>"),
    ("Detectado en esta PC (19/09/2026):", "Detectado en esta PC (<FECHA>):"),
    ("opencode `1.18.31`", "opencode `<TU-VERSION>`"),
    ("Ma" + "nu", "<TU-NOMBRE>"),
]
_MOBILE_RX_REPLACEMENTS = [
    (re.compile(r"desktop-[a-z0-9]+"), "<TU-PC>"),
    (re.compile(r"moto-g[0-9]+-5g"), "<TU-CELU>"),
]
# IPs universales (loopback / todas las interfaces) NO son sensibles: se conservan.
_MOBILE_IP_RX = re.compile(r"\b(?!(?:127|0)\.)(?:\d{1,3}\.){3}\d{1,3}\b")

_SKEL_MOBILE_FALLBACK = """# Trabajar esta sesion desde el movil

Guia generica (el generador no encontro MOBILE.md en el origen, asi que este
template es minimo: completalo con tus datos).

## 1. Iniciar el servidor

```powershell
.\\scripts\\start-mobile.ps1
```

## 2. Conectar la app

1. Server URL: `http://<TU-IP>:<PUERTO>` (o la URL de tu tunel).
2. Username/Password: los de `.env.mobile` (nunca commitear ese archivo).
3. Verificacion rapida: abrir `<ServerURL>/global/health` debe devolver `{"healthy":true,...}`.

## 3. Seguridad

- Auth siempre activada, incluso en red privada.
- Si perdes el telefono: rota la password, detene el servidor.
"""

_SKEL_COPY_TRANSFORMS = {
    "LICENSE": [],
    "golden/schema.json": [("ej. joy" + "brand", "ej. mi-negocio")],
    "workflows/investigar.md": [],
    "workflows/prospectar.md": [("Ma" + "nu", "<TU-NOMBRE>")],
    "workflows/juzgar.md": [],
    "workflows/sandbox.md": [],
    "scripts/install-beta.ps1": [],
}
_SKEL_COPY_RXTRANSFORMS = {
    "LICENSE": [
        (re.compile(r"(?m)^Copyright \(c\) \d+ .*$"), "Copyright (c) <AÑO> <TU-NOMBRE>")
    ],
}

_SKEL_COPIES = [
    "agent.py",
    ".gitignore",
    "LICENSE",
    "opencode.json",
    ".env.mobile.example",
    "COMO_FUNCIONA.md",
    "runs/brief.template.md",
    "runs/closeout.template.md",
    "scripts/start-mobile.ps1",
    "scripts/stop-mobile.ps1",
    "scripts/ig_check.py",
    "scripts/install-beta.ps1",
    "workflows/investigar.md",
    "workflows/prospectar.md",
    "workflows/juzgar.md",
    "workflows/sandbox.md",
    "golden/schema.json",
]

_SKEL_EMBEDDED = {
    "README.md": _SKEL_README,
    "QUICKSTART.md": _SKEL_QUICKSTART,
    "AGENTS.md": _SKEL_AGENTS,
    "strategy.md": _SKEL_STRATEGY,
    "tasks.txt": _SKEL_TASKS,
    "RUNSTATE.md": _SKEL_RUNSTATE,
    "golden/README.md": _SKEL_GOLDEN_README,
    "golden/checks/demo_basico.py": _SKEL_CHECKS_DEMO,
    "golden/rubrics/demo_basico.v1.json": _SKEL_RUBRIC_DEMO,
    "golden/datasets/demo_basico.jsonl": _SKEL_DATASET_DEMO,
    "golden/live/.gitkeep": "",
    "golden/pending/.gitkeep": "",
}


def _skel_write(out, rel, text):
    p = out / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, text)
    return rel


def build_skeleton(out_dir):
    """Genera el esqueleto publico en out_dir. Determinista: mismo input, mismos bytes."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for rel in sorted(_SKEL_COPIES):
        src = ROOT / rel
        if rel == "agent.py":
            text = Path(__file__).read_text(encoding="utf-8")
        else:
            text = src.read_text(encoding="utf-8")
        for old, new in _SKEL_COPY_TRANSFORMS.get(rel, []):
            text = text.replace(old, new)
        for rx, new in _SKEL_COPY_RXTRANSFORMS.get(rel, []):
            text = rx.sub(new, text)
        written.append(_skel_write(out, rel, text))
    for rel in sorted(_SKEL_EMBEDDED):
        written.append(_skel_write(out, rel, _SKEL_EMBEDDED[rel]))
    mob_src = ROOT / "MOBILE.md"
    if mob_src.exists():
        mob = mob_src.read_text(encoding="utf-8")
        for old, new in _MOBILE_REPLACEMENTS:
            mob = mob.replace(old, new)
        for rx, new in _MOBILE_RX_REPLACEMENTS:
            mob = rx.sub(new, mob)
        mob = _MOBILE_IP_RX.sub("<TU-IP>", mob)
    else:
        mob = _SKEL_MOBILE_FALLBACK
    written.append(_skel_write(out, "MOBILE.template.md", mob))
    rc = cmd_golden_build(out / "golden")
    if rc != 0:
        raise SystemExit(f"skeleton: golden build fallo (rc={rc})")
    written.append("golden/golden.jsonl")
    for pyc in sorted(out.rglob("__pycache__")):
        shutil.rmtree(pyc, ignore_errors=True)
    return sorted(written)


def cmd_skeleton(out="skeleton"):
    dest = ROOT / out if not os.path.isabs(out) else Path(out)
    written = build_skeleton(dest)
    print(f"skeleton: {len(written)} archivos en {dest}")
    return 0


def cmd_golden_autopsy(task):
    """Autopsia de fallos del golden: solo PROPONE (borrador). Un humano convierte
    la propuesta en cambio y la pasa por el golden. Nunca auto-aplica."""
    if not task:
        print("Uso: python agent.py golden autopsy --task <t>")
        return 1
    rows = {r["id"]: r for r in _load_golden(task)}
    if not rows:
        return 1
    rpath = GOLDEN_DIR / "results.jsonl"
    res = _read_jsonl(rpath) if rpath.exists() else []
    res = [x for x in res if x.get("task") == task]
    n = len(res)
    fails = {}
    for x in res:
        for f in x.get("fails", []):
            fails[f] = fails.get(f, 0) + 1
    print(f"autopsy {task}: {len(rows)} filas, {n} evals registradas")
    for rid in sorted(rows):
        exp = rows[rid].get("expected", {})
        fc = fails.get(rid, 0)
        print(
            f"  {rid} (w{rows[rid].get('weight', 1):g}) esp "
            f"{exp.get('decision')}/{exp.get('score')}: fallo {fc}/{n}"
        )
    print("PROPUESTA (borrador: humano la convierte en cambio + golden):")
    flagged = False
    for rid in sorted(rows):
        fc = fails.get(rid, 0)
        if fc >= 2 or (n == 1 and fc == 1):
            flagged = True
            if float(rows[rid].get("weight", 1)) >= 3:
                print(f"  - {rid}: ALERTA verdad humana en disputa ({fc}/{n}). Revisar con humano; NO tocar sin OK.")
            else:
                print(f"  - {rid}: candidata a supersedes ({fc}/{n} fallos). Corregir expected o agregar evidencia.")
    if not flagged:
        print("  - (sin candidatas: todo estable)")
    print("Nota v0: trabaja con veredictos, no con dimensiones (viven en pending/*.scored).")
    return 0


def cmd_golden_lineage(rid):
    """Cadena supersedes de una fila + en que evals aparecio como fallo. Solo lectura."""
    if not rid:
        print("Uso: python agent.py golden lineage <ID>")
        return 1
    ds = GOLDEN_DIR / "datasets"
    all_rows = []
    for f in sorted(ds.glob("*.jsonl")) if ds.exists() else []:
        all_rows += _read_jsonl(f)
    by_id = {r["id"]: r for r in all_rows}
    if rid not in by_id:
        print(f"{rid} no esta en datasets/")
        return 1
    chain, seen, cur = [rid], {rid}, by_id[rid]
    while cur.get("supersedes") and cur["supersedes"] not in seen and cur["supersedes"] in by_id:
        cur = by_id[cur["supersedes"]]
        chain.append(cur["id"])
        seen.add(cur["id"])
    kids = sorted(r["id"] for r in all_rows if r.get("supersedes") == rid)
    print(f"lineage {rid}: {' -> '.join(reversed(chain))}" + (f" -> [{', '.join(kids)} la reemplaza(n)]" if kids else " (vigente)"))
    for cid in reversed(chain):
        r = by_id[cid]
        exp = r.get("expected", {})
        print(
            f"  {cid} | task={r.get('task')} w={r.get('weight', 1)} "
            f"frozen={r.get('frozen_at')} esp={exp.get('decision')}/{exp.get('score')} | {exp.get('truth_source', '?')}"
        )
    rpath = GOLDEN_DIR / "results.jsonl"
    if rpath.exists():
        for x in _read_jsonl(rpath):
            hit = [f for f in x.get("fails", []) if f in seen or f == rid]
            if hit:
                print(f"  fallo en eval {x.get('ts')} [{x.get('task')}/{x.get('rubric')}]: {hit}")
    return 0


def cmd_run_checkpoint(slug, msg, nxt):
    """Estado reanudable de mitad de corrida: journal (historia) + checkpoint.md (ULTIMO)."""
    if not msg.strip():
        print('Uso: python agent.py run checkpoint [--slug S] "estado" [--next "proximo"]')
        return 1
    if nxt is True:
        nxt = ""
    rdir = _resolve_run(slug)
    _journal_append(rdir, "checkpoint", msg.strip() + (f" | NEXT: {nxt.strip()}" if (nxt or "").strip() else ""))
    _atomic_write(
        rdir / "checkpoint.md",
        f"# CHECKPOINT — {rdir.name}\n\n- ts: {datetime.now().isoformat(timespec='seconds')}\n"
        f"- estado: {msg.strip()}\n- next: {(nxt or '').strip() or '-'}\n\n"
        "> Historial completo en journal.jsonl. Este archivo = ULTIMO estado (se sobrescribe).\n",
    )
    print("checkpoint guardado:", rdir.name)
    return 0


def cmd_verify(scope="all"):
    """Verifica cadenas de auditoria (prev/hash). Legacy sin hash = prefijo confiable, no falla."""
    if scope not in ("all", "runs", "golden", "memory"):
        print("Uso: python agent.py verify [--scope runs|golden|memory|all]")
        return 1
    targets = []
    if scope in ("all", "runs") and RUNS.exists():
        targets += [(p, f"run:{p.parent.name}") for p in sorted(RUNS.glob("*/journal.jsonl"))]
    if scope in ("all", "golden"):
        targets.append((GOLDEN_DIR / "results.jsonl", "golden:results"))
    if scope in ("all", "memory"):
        targets.append((MEMLOG, "memory:log"))
    fails = 0
    for path, label in targets:
        if not path.exists():
            print(f"  SKIP {label} (no existe)")
            continue
        legacy, chained, prev, bad = 0, 0, None, None
        for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                bad = (i, "linea no-JSON")
                break
            if not isinstance(o, dict) or not isinstance(o.get("hash"), str):
                legacy += 1
                continue
            if o.get("prev") != (prev or "GENESIS"):
                bad = (i, "prev roto (linea reordenada/insertada?)")
                break
            if hashlib.sha256(_chain_canon({k: v for k, v in o.items() if k != "hash"})).hexdigest() != o["hash"]:
                bad = (i, "hash no coincide (linea editada?)")
                break
            prev = o["hash"]
            chained += 1
        if bad:
            fails += 1
            print(f"  FAIL {label} linea {bad[0]}: {bad[1]}")
        else:
            print(f"  OK {label}: {chained} encadenadas, {legacy} legacy")
    print("verify: FAIL" if fails else "verify: OK")
    return 1 if fails else 0


def cmd_golden_impact(task):
    """Blast-radius pre-cambio: fragilidad de cada fila ante un umbral mas duro. Solo lectura."""
    if not task:
        print("Uso: python agent.py golden impact --task <t>")
        return 1
    rows = _load_golden(task)
    if not rows:
        return 1
    mod = _load_checks(task)
    if mod is None:
        return 1
    print(f"impact {task}: margen de cada fila al borde de decision (score esperado como proxy)")
    for r in sorted(rows, key=lambda r: r["id"]):
        exp = r.get("expected", {})
        if exp.get("decision") == "descarte":
            print(f"  {r['id']} (w{r.get('weight', 1):g}): descarte por filtro-duro (si el filtro se afloja, entra a puntuar)")
            continue
        s = float(exp.get("score") or 0)
        w = float(r.get("weight", 1))
        flags = []
        if w >= 3:
            flags.append("VETO si cae")
        print(f"  {r['id']} (w{w:g}) esp {exp.get('decision')}/{s:g} (margen {s - 7:+g} sobre umbral 7) {' '.join(flags)}".rstrip())
    cals = [r for r in rows if r.get("expected", {}).get("decision") == "califica"]
    bajo8 = [r["id"] for r in cals if float(r["expected"].get("score") or 0) < 8]
    print(f"Si el umbral subiera a 8: caerían {len(bajo8)} ({', '.join(sorted(bajo8)) or '-'})")
    return 0


_QLOG = ROOT / "memory_queries.jsonl"

_ASK_PIPELINE_VERBS = (
    "puntuar", "puntua", "evaluar", "evalua", "analizar", "analiza",
    "mira", "ficha", "score",
)
_ASK_RUN_RE = re.compile(
    r"(segu[ií]|contin[uú]a|\bdale\b|en qu[eé] est|tareas?|pendientes?|^status$|pr[óo]xima|^next$"
    r"|\bestado\b|\bresumen\b)",
    re.I,
)
_ASK_MEM_RE = re.compile(
    r"(record[aá]|acordate|acuerdate|qu[eé] sabemos|qui[eé]n|quienes|c[oó]mo resolv|casos? de"
    r"|antecedente|qu[eé] hizo|patr[oó]n|patrones|aprendimos|qu[eé] aprend|problema|ayuda|duda"
    r"|no me (cierra|sale|anda|funciona)|c[oó]mo hago)",
    re.I,
)
_HANDLE_RE = re.compile(r"@[a-z0-9_.]{2,30}")


def _qlog(query, route, top_ids):
    try:
        _rotate(_QLOG)
        with open(_QLOG, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "query": query[:300],
                        "route": route,
                        "top_ids": top_ids or [],
                        "verdict": "pending",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass


def _load_rag():
    from importlib.util import spec_from_file_location, module_from_spec

    p = ROOT / "scripts" / "rag_memoria.py"
    if not p.exists():
        return None
    spec = spec_from_file_location("siafb_rag", p)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_ask(text):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    q = (text or "").strip()
    if not q:
        print('Uso: python agent.py ask "pregunta o pedido"')
        return 1
    low = q.lower()
    handles = sorted(set(_HANDLE_RE.findall(low)))
    if handles and any(v in low for v in _ASK_PIPELINE_VERBS):
        print("ROUTE:pipeline [handles + verbo de evaluación]")
        print(
            "NEXT: python scripts/ig_check.py --posts 6 " + " ".join(handles) + "  (con "
            "--api-token-file si son Business/Creator)"
        )
        _qlog(q, "pipeline", [])
        return 0
    if _ASK_RUN_RE.search(low):
        items = load_tasks()
        pending = [t for _, done, t in items if not done]
        done = [t for _, done, t in items if done]
        print(f"ROUTE:run-state [tasks: {len(pending)} pendientes, {len(done)} hechas]")
        if pending:
            print("NEXT: " + pending[0][:160])
        else:
            print("NEXT: sin pendientes (pedí objetivo o agregá con add)")
        _qlog(q, "run-state", [])
        return 0
    if _ASK_MEM_RE.search(low):
        print("ROUTE:memory [pregunta de casos/juicio] -> RAG:")
        try:
            mod = _load_rag()
            if mod is None:
                raise FileNotFoundError("scripts/rag_memoria.py")
            ranked = mod.query_ids(q, 4)
            for score, d in ranked[:4]:
                print(f"{score:.3f} [{d['src']}] {d['id']}: {d['text'][:220]}")
            _qlog(q, "memory", [d["id"] for _, d in ranked[:4]])
        except Exception as ex:
            print(f"RAG no disponible ({type(ex).__name__}: {str(ex)[:100]}). Ruta: direct.")
            _qlog(q, "memory-fallback", [])
        return 0
    print("ROUTE:direct [responder en chat; sin memoria relevante detectada]")
    _qlog(q, "direct", [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

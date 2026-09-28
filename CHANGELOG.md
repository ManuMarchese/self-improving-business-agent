# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [beta-1.2] — 2026-09-23

Del "agente que dice que aprende" a una **máquina de aprendizaje medible y a prueba
de trampa**. Metodología completa en [`docs/METODOLOGIA.md`](docs/METODOLOGIA.md).

### Agregado
- **Motor** (`motor/`, solo stdlib):
  - `llm.py`: adaptador a modelos gratis (OpenCode Zen / OpenRouter free / Atria) y
    **local vía Ollama**; caché por nonce, reintentos, respaldo por rol, mock para tests.
  - `evaluate.py`: evaluación con IC 95% por bootstrap, manifiesto de integridad
    (exit 3), modo examen sin filtraciones, probe del proveedor (exit 4), guardia de
    repo con cuarentena (exit 5), errores del proveedor fuera del puntaje.
  - `compare.py`: comparación **pareada** con regla de aceptación explícita.
  - `learn.py`: ciclo **ACE** (generador → reflector → curador → juez) con playbook de
    reglas, filtro anti-memorización y árbol de versiones.
  - `sandbox_py.py`: ejecución de código generado con timeout, filtros y chequeo de
    **tipos nativos** (anti objetos truchos).
- Tareas `eco_mock` (demo sin red) y `py_funcs` (MBPP sanitized, Apache-2.0).
- Rol `reflector` (coach remoto grande) separado del alumno (modelo chico local).
- CI: matriz Ubuntu/Windows × Python 3.11/3.12 + job que prueba el esqueleto público.
- `docs/METODOLOGIA.md`, este changelog.

### Cambiado
- `golden diff` sale con **exit 2** ante veto (antes imprimía "VETO" y salía 0).
- Registro append-only con **ancla** (`chain_heads.json`): `verify` detecta inserción,
  edición y truncado (antes no detectaba ninguno de los tres).
- Permisos de OpenCode por **lista de permitidos** (antes `bash "*": allow`).
- Escáner de secretos detecta rutas de usuario en 3 formatos; excepciones solo por
  hash exacto de línea (`doctor_allow.json`).

### Corregido (hallados auditando, ninguno lo mostraba un test)
- El shim `opencode.cmd` truncaba los prompts multilínea a la primera línea.
- El LLM del motor abría el repo (cwd y `PWD` heredado) y llegó a escribir archivos.
- El manifiesto fallaba en cualquier clon limpio por finales de línea CRLF/LF.
- El filtro anti-memorización descartaba reglas generales por subcadenas ("for").
- La guardia de repo daba falso positivo en subcarpetas (rutas relativas a git root).
- El usuario local se filtraba al esqueleto público vía `opencode.json`.

### Seguridad / limitaciones conocidas
- `sandbox_py` no es un sandbox real (corre local con filtros + timeout).
- Modelos gratis remotos pueden registrar prompts: usar solo con datos públicos.

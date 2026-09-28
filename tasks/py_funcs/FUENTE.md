# FUENTE — dataset de la tarea py_funcs

- **URL de descarga:** https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json
- **Fecha de descarga:** 2026-09-23
- **sha256:** `ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9`
- **Bytes:** 255053
- **Formato:** JSON (lista), 427 problemas hand-verified ("sanitized"), claves por fila:
  `task_id`, `prompt`, `code`, `test_list`, `test_imports`, `source_file`.
- **Licencia:** Apache License 2.0 — verificada en el repo de origen
  (`google-research/google-research`, archivo `LICENSE` en la raíz;
  `mbpp/README.md` no declara licencia propia y cita el paper
  "Program Synthesis with Large Language Models", Austin et al., 2021).
- **Splits oficiales del README de origen:** task_ids 11-510 = test,
  1-10 = few-shot, 511-600 = validation, 601-974 = train.

## Disponibilidad por rango en sanitized-mbpp.json (2026-09-23)

| rango | disponibles en el archivo |
|---|---|
| 11-510 (reserva examen) | 257 |
| 511-600 (dev pedido: 60) | **43** (554-600 con huecos; nada de 511-553) |
| 601-974 (train pedido: 80) | 120 |

El archivo queda local en `tasks/py_funcs/_fuente/` (en `.gitignore`, fuera de git).

## Decisión del supervisor (Claude, 2026-09-23) sobre el split dev

El prompt original pedía 60 problemas de dev SOLO del rango 511-600, pero el
sanitized solo trae 43 ahí. Decisión: quedarse CON sanitized-mbpp.json
(verificado a mano) y NO bajar `mbpp.jsonl`. Splits con `random.Random(42)`:

- **train** = 80 de los 120 disponibles en 601-974 (`rng.sample` sobre pool ordenado).
- **dev** = los 43 de 511-600 + 17 de 601-974 que NO quedaron en train
  (`rng.sample` sobre los 40 restantes, en el MISMO rng después del sample de
  train) → dev = 60, **disjunto** de train.
- 11-510 intactos: no se copian a train/dev (reserva del examen externo).

Reproducir: `python tasks/py_funcs/build_splits.py` (misma semilla, mismos bytes).

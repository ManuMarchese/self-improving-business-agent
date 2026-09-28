# Metodología: cómo sabemos si el agente aprendió de verdad

Este documento explica **qué cuenta como mejora** en este proyecto y **cómo se evita
engañarse**. Es la parte más importante del repo: un agente "que se automejora" sin
esto es marketing.

## 1. Separación de datos (el aprendiz nunca ve el examen)

| Split | Para qué | Quién lo ve |
|---|---|---|
| `train` (80) | el aprendiz saca reglas de sus errores | aprendiz + reflector, con feedback completo |
| `dev` (60) | decidir si un cambio se queda | solo el juez; el aprendiz ve **solo puntajes** |
| examen externo (60) | medición final, una sola vez | nadie del sistema: vive **fuera del repo** y OpenCode tiene prohibido leer esa carpeta |

- `py_funcs` sale de MBPP *sanitized* (Apache-2.0). `train` y `dev` son disjuntos por
  construcción (`tasks/py_funcs/build_splits.py`, semilla fija) y la reserva 11–510
  nunca se copia al repo.
- El prompt del reflector se construye **solo con casos de train** y hay un test que
  busca ids, enunciados y asserts de dev dentro de ese prompt.

## 2. Regla de aceptación (comparación pareada)

Comparar dos promedios sueltos engaña: con n=60 el intervalo de una corrida mide ~20
puntos. Por eso `python -m motor compare` compara **los mismos ejemplos**:

1. Diferencia por ejemplo `d = score_hijo − score_padre` (se excluyen los ejemplos con
   error del proveedor en cualquiera de los dos).
2. Intervalo de confianza 95% de la media de `d` por **bootstrap pareado** (2000
   remuestreos, semilla fija → resultado reproducible).
3. **ACEPTA** solo si el límite inferior del IC es `> 0` **y** las regresiones son
   como máximo la mitad de las mejoras. Si no, **RECHAZA** con el motivo.

El padre y el hijo se evalúan con el mismo `nonce` (misma semilla de muestreo), así
la única diferencia es el playbook.

## 3. Cómo aprende (ACE: Generador → Reflector → Curador → Juez)

Basado en *Agentic Context Engineering* (ICLR 2026): en vez de reescribir el prompt,
se acumula un **playbook** de reglas cortas con contadores útil/dañina.

- **Generador:** la policy actual resuelve train.
- **Reflector** (un modelo grande, 1 llamada por ronda): lee los errores de train y
  propone ≤3 reglas generales en JSON.
- **Curador** (código, sin LLM): descarta reglas que copian datos (nombres de función,
  literales de asserts, ids — por palabra completa), casi-duplicados, poda reglas
  dañinas y limita a 25.
- **Juez:** si el hijo no cambió nada, rechaza sin gastar llamadas; si cambió, lo
  compara en dev con la regla de la sección 2. Los hijos rechazados **no se borran**
  (árbol de versiones al estilo Darwin Gödel Machine).

## 4. Defensas contra la trampa (reward hacking)

La lección del campo: *todo sistema que se automejora hace trampa si puede tocar lo
que lo evalúa*. Defensas, cada una con test:

| Ataque | Defensa |
|---|---|
| Editar el grader o dev | `evals/MANIFEST.json` con sha256 (CRLF/LF normalizado); hash distinto → **exit 3** antes de llamar al modelo |
| Reescribir el historial de resultados | ledger encadenado con hash + ancla en `chain_heads.json`; insertar, editar o truncar → `verify` FAIL |
| Devolver un objeto con `__eq__` siempre `True` (o `mock.ANY`, `type()` dinámico) | cada operando de un assert se evalúa aparte y debe ser **tipo nativo** (int, str, list…) antes de comparar |
| Que el modelo del motor toque el repo | corre en `%TEMP%` con agente de solo lectura + guardia post-corrida: archivos nuevos → **exit 5** y cuarentena |
| Que una caída del proveedor parezca "el modelo no sabe" | errores del proveedor fuera del puntaje; >10% → corrida inválida (**exit 4**); probe previo |
| Memorizar el examen | examen fuera del repo + filtro anti-copia en el curador |

## 5. Qué NO prueba este proyecto (honestidad)

- `sandbox_py` **no es un sandbox real**: el código generado corre en la máquina local
  con filtros estáticos + timeout.
- MBPP es público: los modelos grandes probablemente lo vieron (sacan ~93%). Por eso
  el alumno es un modelo **chico local** (Qwen 2.5 1.5B) con margen para mejorar.
- n=60 por split: las diferencias chicas no son detectables; la regla de aceptación
  es conservadora a propósito (prefiere rechazar una mejora real a aceptar ruido).
- Una sola tarea con nota objetiva. La transferencia a tareas de negocio con verdad
  humana es trabajo futuro.

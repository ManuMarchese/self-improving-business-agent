# Resultados beta-1.2 (23-sep-2026)

Todo sale de `evals/ledger.jsonl` (cadena anclada: `python agent.py verify`).
Metodología en [`METODOLOGIA.md`](METODOLOGIA.md).

## Setup

- **Tarea:** `py_funcs` (MBPP sanitized, Apache-2.0). train 80 · dev 60 · examen externo 60
  (reserva 11–510, fuera del repo, nunca vista por el sistema).
- **Alumno:** `qwen2.5:1.5b` local vía Ollama (GTX 1050 2 GB), temperatura 0.2, semilla por nonce.
- **Coach (reflector):** Nemotron 3 Super 120B free (OpenRouter), respaldo Atria. 1 llamada por ronda.
- **Regla de aceptación:** IC 95% pareado de la diferencia > 0 **y** regresiones ≤ mejoras/2.

## Línea base

| Corrida | n | Media | IC 95% |
|---|---|---|---|
| v0 dev, nonce Q1 | 60 | 55.6% | 43.3–66.7 |
| v0 dev, nonce Q2 (anulada por la guardia, dato informativo) | 60 | 53.3% | 41.1–64.4 |
| v0 dev, nonce L3-dev (referencia del juez) | 59 | 57.1% | — |
| **v0 examen externo** | 60 | **70.6%** | 59.4–80.6 |

Ruido entre corridas del mismo modelo: ~2 puntos. Referencia con modelos grandes: Muse Spark
1.3 sacó 93.3% en dev (MBPP probablemente memorizado): por eso el alumno es un modelo chico.

## Experimento 1 — Playbook de reglas (ACE), 3 rondas

| Ronda | Reglas nuevas (resumen) | Padre → hijo (dev) | Mejoras / regresiones | IC 95% de d | Juez |
|---|---|---|---|---|---|
| 1 | tipo de retorno correcto; estructura de entrada; definir auxiliares | 57.1 → 56.7 | 8 / 6 | [−11.2, +12.0] | RECHAZA |
| 2 | respetar la firma exacta; plan paso a paso; verificar con casos | 57.1 → 56.1 | 4 / 4 | [−10.2, +9.0] | RECHAZA |
| 3 | verificar contra el ejemplo; tipos de entrada/salida; bordes de loops | 57.1 → 57.2 | 7 / 6 | [−8.5, +11.3] | RECHAZA |

Las reglas arreglan algunos problemas y rompen otros casi en igual cantidad. Sin juez, el
sistema habría adoptado cambios que en neto son ruido.

## Experimento 2 — Biblioteca de habilidades verificadas (skill library)

55 soluciones propias de train que pasaron todos sus tests; ante cada problema se muestran
las 2 más parecidas (Jaccard de tokens).

| Evaluación | v0 | v5 (skills) | Mejoras / regresiones | IC 95% de d | Juez |
|---|---|---|---|---|---|
| dev | 57.1 | 50.0 | 5 / 8 | [−18.1, +6.2] | RECHAZA |
| examen externo | 70.6 | 65.0 | 6 / 9 | [−17.8, +6.7] | RECHAZA |

**Dev y examen coinciden** en dirección y tamaño (−6 y −5.6 puntos): la decisión tomada en dev
generaliza a datos que el sistema nunca vio. Hipótesis: con un modelo de 1.5B los ejemplos
"parecidos" confunden más de lo que guían (copia nombres/enfoques de otro problema).

## Lectura honesta

- **Lo que está demostrado:** el sistema *mide* sin engañarse. 4 de 4 propuestas de mejora
  rechazadas con evidencia, y la decisión en dev predijo el examen externo.
- **Lo que NO está demostrado:** que aprenda. Ninguna propuesta mejoró al alumno de forma
  significativa.
- **Poder estadístico:** con n=60 solo se detectan mejoras de ~12+ puntos. Mejoras reales más
  chicas son invisibles; la regla prefiere rechazar a aceptar ruido, a propósito.

## Próximos experimentos (con hipótesis)

1. Más datos de dev (n≈200) para detectar mejoras de ~6 puntos.
2. Recuperación por *embeddings* (bge-m3 local ya instalado) en vez de Jaccard.
3. Alumno de 3–8B (llama3.2:3b / llama3.1:8b) que sí siga reglas abstractas.
4. Una tarea propia de negocio con verdad humana (el objetivo original del proyecto).

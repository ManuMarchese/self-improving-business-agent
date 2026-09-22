---
slug: YYYY-MM-DD-slug
title: <título de la corrida>
created: <fecha>
mode: supervisado / delegado / nocturno
---

# BRIEF — <título>

## Objetivo
<bajo, en 1-2 líneas: qué resultado medible se busca>

## Alcance
- Dentro: <qué SÍ entra en esta corrida>
- Fuera: <qué NO tocar>

## Criterios de salida (éxito medible)
1. <condición concreta y verificable>
2. <condición concreta y verificable>
3. Golden Set (solo si la corrida cambia reglas): `python agent.py golden rescore/diff` sin regresiones
   (baseline en `golden/results.jsonl`; veto duro si falla un caso peso 3).

## Presupuesto
- Tiempo estimado: <X> h
- Limite de tiempo real: <X> h
- Costo máximo: $<X> (0 por defecto)

## Checkpoints
- [ ] 25%: <qué debe estar listo>
- [ ] 50%: <qué debe estar listo>
- [ ] 75%: <qué debe estar listo>
- [ ] 100%: <closeout + memoria>

## Fuentes permitidas
- <webs/APIs/archivos a usar; NO fabricar datos>

## Notas
- <cualquier cosa que el operador deba saber al retomar>
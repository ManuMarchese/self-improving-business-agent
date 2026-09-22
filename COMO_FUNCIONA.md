# 🤖 Mi Agente — Explicado simple

> Versión genérica del esqueleto. Definí tu negocio en `RUNSTATE.md`:
> `<TU-NEGOCIO>`, `<TU-ICP>`, tu oferta.

---

## 🎒 **La mochila** (`RUNSTATE.md`)
El agente anota **dónde quedó** cada vez que para. Si se apaga la compu → abre la mochila y **sigue igual**.

## 🧠 **Dos cerebritos**
- **Caliente** (`memory.md`): lo importante de **hoy**, poquito.
- **Frío** (`memory_log.jsonl`): **todo** lo que pasó, para siempre.

## 📏 **Las fichas de ejemplo** (`golden/`)
Fichas congeladas de resultado conocido:
- Las de **peso alto** son verdad de un humano → si el agente falla una, **¡NO PUEDE SALIR!** (veto).
- Cada cambio de reglas se mide contra las fichas: si el puntaje baja, la "mejora" era un empeoramiento.

## ⏱ **El reloj de arena** (`agent.py run` + `costs.md`)
Cada corrida mide cuánto costó en tokens. Si se pasa del doble del estimado → **se frena sola**.

## 🚫 **El semáforo** (Gates)
**NO hace sin tu OK**: gastar plata, mandar mensajes, publicar, borrar, usar claves.

## 🔍 **El detector** (filtros duros)
Reglas excluyentes: lo que no cumple un filtro duro se **descarta directo**, no se puntúa ni se pierde tiempo.

## 🏆 **El tablero** (rúbrica)
Cada candidato se puntúa por dimensiones con evidencia obligatoria.
Solo lo visto directo cuenta; lo inferido, no.

## 🔄 **El ciclo infinito**
`TAREA → PLAN → HACER → MEDIR → ¿MEJORÓ? → GUARDAR/TIRAR → SIGUIENTE`

## 📦 **Una carpeta, un proyecto**
Este agente solo sabe de `<TU-NEGOCIO>`. Otro negocio = otra carpeta. No se mezclan.

## 🗝 **Comandos mágicos**
| Decís | El agente hace |
|-------|----------------|
| `"seguí"` | NEXT STEP + guarda |
| `"en qué estábamos"` | Cuenta en 6 líneas + pregunta |
| `python agent.py next` | Próxima tarea |
| `python agent.py done "algo"` | Marca done + aprende |
| `python agent.py doctor` | Audita el repo (no toca nada) |

---

**¡Eso es todo!** 🎈
No es magia: es **orden, reglas claras y guardar todo en archivos**.
# Golden Set — demostrar que el agente mejora (con numeros, no opiniones)

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

# WORKFLOW sandbox (v1) — sobre para corridas largas autónomas

Usar cuando: el dueño deja corriendo horas (research, oleadas, builds). Envuelve
cualquier otro workflow con límites duros.

## Límites calibrados
- Deny-list total: tokens.priv/, cookies.priv/, .env.mobile, .priv/, opencode.db,
  credenciales en cualquier forma. Ni leer valores ni imprimirlos.
- Sin red salvo fuentes del brief (APIs con cuota declarada + webfetch/websearch de lectura).
  Brief sin fuentes = sin red.
- Sin efectos irreversibles: no push, no publicar, no contactar, no gastar, no borrar.
- Time-box + budget en brief (límite 2x estimado; cortar en próximo checkpoint).
- 3-strikes: si algo falla 3 veces igual, parar y traer el error (no insistir).

## Trazabilidad calibrada
- `run new/budget` al abrir; `run step` por paso; `run intervene` si frena algo humano;
  `run report` + `run close` al cerrar (closeout.md con logros/límites honestos).
- Journal hash-chain: solo crece vía comandos existentes. Juez del workflow juzgar al final.

## Contrato de salida
Reporte: qué se hizo, qué se verificó, qué quedó pendiente, costo real.
Intención cumplida > resultado: si un límite frena la corrida, ESO es el éxito.

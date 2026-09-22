# WORKFLOW prospectar (v1) — determinista, solo lectura de APIs

Usar cuando: evaluar cuentas (IG) como referentes, leads o shortlist.
NUNCA congela goldens (gate humano por fila).

## Prompt calibrado (pegar tal cual + seeds)
Leé perfiles SOLO con `scripts/ig_api.py` (`ig_check.py` enmascara 429: prohibido).
Token por env, jamás imprimirlo. Seeds verified-only (existencia confiable, no guesses).
Piso 15k/avg200 con fallbacks (likes→views→comments). Nicho por keywords; override SOLO con
veredicto de <TU-NOMBRE> + motivo en journal (sin veredicto no hay override).
Devolvé por perfil: veredicto + métricas + bio/link + motivo. Sin chamuyo (ver OPS).

## Ambiente calibrado
- Ve: scripts/, runs/<slug>/ (evidencia propia), golden/ (solo lectura de rúbricas).
- Deny-list (ni leer valores ni nombrarlos): `tokens.priv/`, `cookies.priv/`, `.env*`,
  `cookies*.txt`, `*.priv/`, `*.pem`, `*.key`, `.priv/`, `opencode.db`, `logs/`, `trace.jsonl`.
  Token y claves jamás en logs/journal/commit.

## Tools permitidas
bash (lecturas API con pacing ≥10s), read, write (solo en runs/<slug>/ evidencia).
Prohibidas: freeze/promote, push, publicar, contactar, gastar.

## Cuotas (ver OPS.md § Red, medidas)
Discovery 200/h; hashtag 30/7d (tope operativo 25/30: pasar eso exige OK <TU-NOMBRE>);
Voyage 1 req/min. Cuota escasa = Meta + Voyage (lista cerrada, sin interpretar).
Idempotencia: leidos.json (ningún handle se lee 2 veces; "reintentar" re-entra).

## Contrato de salida
Evidencia congelada en `candidatos-*.md` + commit por oleada (runner) o manual.
Scores con PATs citados o "sin patrón" (piloto). Freeze solo con OK por fila.

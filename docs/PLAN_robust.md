# Plan — cerrar lo actual + versión robusta

## Decisiones acordadas (2026-06-07)
- **Solape de trozo = 3 páginas**: suficiente (un req ≈ 3 líneas, nunca abarca >3 págs → ningún req se parte entre trozos).
- **Backoff: NO** — API de pago, sin rate-limit que esquivar.
- **Velocidad**: paralelizar las llamadas API; **docling de 2 en 2**.
- **Sanitización XML: SÍ** (crítico — un carácter de control inválido rompe el reqif ENTERO; muy delicado).
- **Aviso si 0 reqs: SÍ** (log claro por doc; es testeo nuestro, NO una feature de calidad en run.py).
- **Orden por posición real del PDF: SÍ** (universal; sustituye el stopgap por número/array).
- **Centinela de salida: SÍ** (guardarraíl para chunks densos con cientos de reqs).

## Por qué el centinela (techo de salida)
Salida máx de Gemini ≈ 65k tokens. Un req ≈ 125 tokens → techo ~500 reqs/respuesta, pero el modelo se vuelve perezoso antes (zona de riesgo ~250-500). **DOC_B (~344 reqs en 1 trozo) cae en esa zona.** Realización determinista: pedir reqs por tandas; parar cuando una tanda vuelva **vacía** (`{"nuevos": []}`), con tope de iteraciones.

---

## FASE 1 — Cerrar y testear lo actual (visión troceada)  ← AHORA
- s4 ya trocea por páginas (50/3) con dedup.
- Test: el doc tabla-céntrico (debe subir de 114 → ~360), el doc denso (puede seguir bajo → lo arregla el centinela en Fase 2), un doc pequeño (no-regresión), + validación GOLD.
- Si el doc tabla-céntrico mejora claramente → el troceo funciona → pasamos a Fase 2.

## FASE 2 — Clon robusto (versión 3 → versión 4)
Tras testear Fase 1 y que salga bien, **clonar** y añadir los guardarraíles universales:

1. **Centinela de salida** (s3 y s4): extracción por tandas, parar en tanda vacía. Cubre chunks densos / output largo.
2. **Paralelización**: run.py corre las llamadas API en paralelo; **docling 2 a la vez** (pool con límites de concurrencia).
3. **Sanitización XML** (s6): eliminar/escapar caracteres de control inválidos en `req_id` y `texto` antes de emitir, para que un char malo no tumbe el reqif.
4. **Aviso 0 reqs**: log por doc sin requisitos.
5. **Orden por posición real** (s5 estampa `pos` = offset en el texto/PDF → s6 ordena por `pos`, no por número). Universal para docs sin numeración.
6. Overlap 3 (ya fijado).

### Estrategia
Versión actual (v3) = el motor que funciona hoy, intacto como red de seguridad.
Versión robusta (v4) = clon + los 6 guardarraíles. Se prueba aparte; si supera, sustituye.

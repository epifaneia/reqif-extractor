# v5 — Enrutado por regiones (optimización de coste de la pasada B)

## El problema (v4)

La pasada B (s4) enviaba el **PDF entero** como imagen a Gemini: ~258 tokens/página,
re-facturados en **cada tanda** del centinela y en **cada solape** de trozo.
Coste real: **~1 €/100 páginas**. Lo absurdo: el 93% de ese contenido ya lo había
leído docling gratis en la pasada A — la visión solo aportaba los IDs dentro de
figuras rasterizadas y tablas que docling mutila (~7%).

## La idea (v5)

El layout de docling **ya localiza** esas figuras y tablas (por eso sabemos que
existen). En vez de mandar el 100% de las páginas a visión, se recorta SOLO esas
regiones (5-10% del área) y cada tipo va por su ruta más barata:

```
s1  docling → md (idéntico a v4)  +  regions/<stem>/manifest.json + crops PNG
     · bbox de PictureItem/TableItem por página (pdfium renderiza el crop;
       docling pone el layout, pdfium los píxeles → md sin cambios, RAM plana)
     · page_headers: título de sección → página (para parent determinista)

s4  router (sustituye a la visión de PDF entero; json_b mismo contrato):
     s4a TABLAS   gmft (TATR sobre el texto vectorial, CPU) → markdown →
                  llamadas de TEXTO con responseSchema. 0 tokens de visión.
                  Si gmft falla → esas tablas van a la ruta de crops.
     s4b FIGURAS  crops PNG en lotes de CROP_BATCH → VLM con responseSchema.
                  parent_header = último header en página <= la del crop
                  (determinista, no lo adivina el modelo).
                  Backend por config.VLM_BACKEND:
                    gemini → clients/gemini.generate_json_with_images
                    local  → clients/vlm_local (vLLM/SGLang, guided_json)
     s4c SEGURIDAD regex de familias de IDs (utils/orphans) sobre md + tablas:
                  ID con forma conocida NO capturado → doc al fallback.
     s4d FALLBACK s4 de v4 preservado ÍNTEGRO (s4_legacy_fullpage.recover):
                  visión de PDF completo solo para el doc que lo necesite.
```

s2/s3/s5/s6 y el contrato `merged.json` quedan **intactos**. `run.py` igual.

## Por qué es seguro para el KPI (<1% de pérdida)

El riesgo del enrutado no es que el VLM alucine: es que el layout NO VEA una
figura y el requisito se pierda EN SILENCIO. Tres capas lo cubren:

1. **gmft** reconstruye las tablas desde el vector (determinista) — la fuente
   principal del 7% perdido.
2. **Crops** con visión de primera línea sobre regiones enfocadas (mejor señal
   que página entera, menos contexto que diluya).
3. **Huérfanos**: las familias de IDs ya extraídos se buscan por regex en todo
   el texto visible; cualquier ID con pinta de requisito sin capturar dispara
   el **fallback de PDF completo** (la ruta v4, que sabemos que funciona).
   Conservador a propósito: un falso positivo cuesta una pasada cara de más;
   un falso negativo costaría un requisito.

## Economía (MEDIDA, corrida 2026-07-02)

| Ruta | Coste |
|---|---|
| v4: página completa + tandas + solapes | ~1 €/100 págs |
| **v5 medido: batch de 9 docs (~600 págs)** | **$0.43 total** (904k in · 136k out, flash-lite) |
| local (L40S ~800 $/mes) | solo compensa >50M págs/mes o si compliance lo exige |

**Validación final**: cobertura **98.4%** del baseline v4 (1194/1214 ítems,
ítem a ítem por norm_id), pérdida 1.65% — dentro del objetivo ≤2% (fracaso
empresarial: 5%). Perdidos auditados por NOMBRE en el sondeo (17 + 2 + 1 en tres documentos: descartes del árbitro). Regresión reqif: 12 docs × 2
modos, XML válido y refs resueltas.

**Modelos pineados** (jul-2026): rutas baratas `gemini-3.1-flash-lite`
($0.25/$1.50 por M; no-thinking de serie; demostró criterio de árbitro:
148-286 descartes/doc con solo 20 pérdidas reales en todo el corpus);
escalado y fallback `gemini-3.1-pro-preview`. Los modelos thinking FACTURAN
los thoughts (observados 15-63k tokens/llamada en 3-flash-preview, con
respuestas vacías intermitentes); si se usa uno, `thinking_budget=0`.

**El árbitro (s4c)**: veredicto POR ID (`nuevos` | `descartados`); un ID sin
veredicto escala (flash→pro) y si sobrevive dispara el fallback. Un silencio
NO es un veredicto: una respuesta vacía deja a sus IDs sin resolver, jamás
los descarta (lección de la corrida que perdió 453 reqs por confiar en un
rescate vacío).

El backend local (GLM-OCR 0.9B / olmOCR-2 7B servidos por vLLM con XGrammar)
queda cableado en `clients/vlm_local.py` a un cambio de config:
`REQIF_VLM_BACKEND=local` + `vllm serve zai-org/GLM-OCR`.
Nota vLLM ≥0.8: pinear `xgrammar==0.1.16`.

## Validación

- `tools/survey_regions.py` — sondeo Fase 0: cruza los json_b de v4
  (congelados en `data/interim/json_b_v4_baseline/`) contra las rutas nuevas:
  cuántos ítems de la visión cara eran tabla-gmft (gratis), cuántos estaban en
  el md, cuántos solo-figura; y si hay json_b nuevo, recall directo v5 vs v4
  con desglose por ruta y lista de IDs perdidos.
- Los `routes` de cada json_b nuevo dejan trazabilidad por ítem
  (`source: table|crop|fallback`).

## Config nueva (todo overridable por env, defaults conservadores)

Ver bloque "Enrutado por regiones (v5)" en `config.py`: REGIONS_*, CROP_*,
VLM_BACKEND, FALLBACK_FULLPAGE, ORPHAN_TOLERANCE.

## Nota de entorno

`gmft` requiere `transformers<5` (el config TATR trae `dilation=None` y la
validación estricta de transformers 5.x lo rechaza). docling 2.85 soporta
4.57.x oficialmente (`docling-ibm-models: >=4.42,<6 !=5.0-5.3`) — el entorno
quedó en 4.57.6 y ambos conviven.

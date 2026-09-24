# reqif-extractor · PDF → ReqIF para Polarion

*Read this in [English](README.md).*

Convierte un PDF de especificación en un fichero ReqIF que se importa en Polarion con la jerarquía del propio documento, sus identificadores tomados verbatim, y cada ítem en la posición que tenía en la página. El modelo lee; el código decide qué es un identificador, qué es informativo y en qué orden va cada cosa. Sin datos en este repositorio.

> La IA tiene riesgos. El código los acota. La norma responde de lo que queda.

[Filosofía de diseño](#filosofía-de-diseño) · [Dónde aporta valor la IA](#dónde-aporta-valor-la-ia) · [Los cinco pasos](#los-cinco-pasos-en-este-proyecto) · [Medido](#medido) · [Demo](#demo-en-cuatro-comandos-datos-públicos) · [Limitaciones](#limitaciones) · [Seguridad y regulación](#seguridad-y-regulación)

---

## Filosofía de diseño

Cualquier aplicación con IA tiene cinco pasos. Solo el paso 4 invoca un modelo; los otros cuatro son código: reproducibles, testeables, sin red. El paso 5 nunca corrige: marca y degrada. Texto completo en [epifaneia.dev](https://epifaneia.dev).

```
 1 LIMPIEZA      2 CONTEXTO      3 GUARDARRAÍL IN    4 PROCESO        5 GUARDARRAÍL OUT
 ■ código        ■ código        ■ código            ▲ modelo         ■ código
```

## Dónde aporta valor la IA

Cualquier asistente lee un PDF de especificación y te lista los requisitos en texto. Lo que ninguno hace de forma creíble es entregarte el fichero que Polarion acepta: identificadores verbatim, la jerarquía del documento, los ítems en el orden de la página, un solo raíz XHTML por valor, todas las referencias resueltas. Ahí está el valor, y lo produce la pareja: el modelo encuentra los ítems en cualquier layout, en párrafos, filas de tabla o figuras, y el guardarraíl de salida convierte esa lista en un ReqIF que importa. Una lista en un notebook es un punto de partida. Un fichero que importa a la primera es el entregable.

---

## Los cinco pasos en este proyecto

★ marca dónde está la mejora en este proyecto: el **guardarraíl de salida**. El modelo clasifica bien; en lo que no se puede confiar es en que invente identificadores, suelte el marcador "(information only)" o reordene. Tres guardas deterministas arreglan exactamente esas tres cosas.

| Paso | Qué hace aquí | |
|---|---|---|
| 1 · Limpieza | `s1` corre docling una vez: PDF → markdown, más las regiones del propio layout (figuras y tablas, con página y bounding box). Las tablas se reconstruyen desde el texto vectorial con gmft, sin visión. Las figuras se recortan. Lo que el layout no vio lo pilla una comprobación de huérfanos: una cadena con forma de id en el texto que nada capturó manda ese documento a un fallback de página completa. | |
| 2 · Contexto | `s2` parsea los headers de forma determinista desde el markdown (número, nivel, anchor). El modelo recibirá el documento ya estructurado, y colgará cada ítem de un anchor en vez de describir dónde está. | |
| 3 · Guardarraíl de entrada | Toda llamada al modelo lleva un schema de respuesta. `s3` extrae ítems con `type ∈ {requirement, information}` y `parent_header ∈ anchors conocidos`. `s4` pide las filas de tabla como frases y prohíbe al modelo, en el schema y en el prompt, rellenar un identificador que la fila no lleve literalmente: la cadena vacía es la respuesta correcta. | |
| 4 · Proceso | Pasada A: una llamada de texto por documento sobre el markdown. Pasada B: enrutada por región, tablas como texto y figuras como recortes en lotes, `gemini` o un VLM local con el mismo contrato. Veinte veces más barato que mandar el PDF entero a visión, mismo recall. | |
| 5 · Guardarraíl de salida | `utils/id_policy`: un identificador se acepta solo si está **verbatim** en el documento; si no, se **deriva** del id de su tabla y la fila (`SWS_Com_00347.3`), o se **ancla a la página** (`TBL-P31.4`) con una forma que no puede confundirse con uno real, o se **rechaza** y se reporta. `s5c_infoguard` reconcilia `type` contra el marcador "(information only)" de la propia fuente. `s5b_positions` audita orden y jerarquía contra el PDF. `s5_merge` deduplica por id, determinista. `s6` construye el ReqIF desde una plantilla de Polarion con un solo `<xhtml:div>` raíz por valor y todas las referencias resueltas. | ★ |

## Medido

Las cifras vienen de una pasada sobre 12 documentos reales de especificación de un fabricante (confidenciales, no incluidos) y se reproducen sobre documentos públicos en la demo de abajo.

| Qué | Cifra | De dónde sale |
|---|---|---|
| Identificadores que el modelo inventó en la ruta de tablas, antes de la política | **167 de 170** filas | Numeraba filas imitando la convención del documento y colisionaba con ids reales (`…-7` frente a `…-007`) |
| Identificadores que cambiaron entre dos ejecuciones sobre el mismo PDF, antes de la política | **49 de 214** | No reproducible. Con la política: 0 |
| Ítems clasificados como requisito que la fuente marcaba "information only" | **17** en 12 documentos | Recuperados por `s5c_infoguard` contra el marcador de la fuente |
| Coste de la pasada B, enrutado por regiones frente a visión del PDF entero | **~1/20** | Mismo recall; la visión del PDF entero costaba ~1 € por 100 páginas |
| Documentos que pasan las cinco comprobaciones estructurales contra una referencia importada en Polarion | **12 / 12**, 2.695 referencias resueltas, 0 huérfanos | XML bien formado, spec types idénticos, un div raíz, referencias resueltas, prefijo de id por documento |

## Polarion

Datos públicos en un Polarion de prueba: la especificación AUTOSAR SWS COM, 465 ítems y 217 cabeceras extraídos del PDF e importados a la primera. Nada que tachar.

*El mapeo de importación: cabeceras a Heading, ítems a System Requirement, `ReqIF.Text` a Description, `ReqIF.ForeignID` a Title.*

![Mapeo de importación ReqIF en Polarion](docs/img/polarion-01-import.png)

*El documento tras el import: la jerarquía del propio PDF, cada ítem bajo su cabecera, cada título el identificador verbatim `SWS_Com_…`.*

![Documento AUTOSAR SWS COM en Polarion](docs/img/polarion-02-tree.png)

*Un requisito: identificador, texto y su cabecera padre como enlace.*

![Un requisito importado](docs/img/polarion-03-requirement.png)

## Demo en cuatro comandos (datos públicos)

Las especificaciones AUTOSAR son públicas y usan exactamente el patrón para el que se construyó este motor: identificadores entre corchetes al final de cada ítem. NIST SP 800-171 es de dominio público.

```bash
pip install -r requirements.txt
cp .env.example .env                         # GEMINI_API_KEY, o VLM_BACKEND=local
# deja AUTOSAR_SWS_COM.pdf (autosar.org) y NIST_SP_800-171r2.pdf (nist.gov) en data/inputs/
python run.py                                # s1 → s6, todos los documentos, en paralelo
python tools/crop_ids.py && python tools/sanitize_reqif.py     # variantes listas para Polarion
```

Salida: `data/outputs/reqif/v1_nested/<doc>.reqif`. Este repositorio no lleva datos; `data/` está fuera de git.

## Limitaciones

- Los ítems `type=information` siguen saliendo como work items de requisito, marcados explícitamente `(information only)` en el texto visible. Mapearlos al tipo Info nativo de Polarion está pendiente.
- La pasada de visión aún tiene ruido: un falso positivo con un id nuevo entra al fichero. `id_policy` rechaza los inventados; no puede rechazar uno con pinta real que simplemente esté mal.
- Dos backends para el modelo, los dos en uso. Cuál elegir depende del documento, no del motor:

  | | Nube (`gemini`) | VLM local (`vlm_local`) |
  |---|---|---|
  | Dónde va el dato | Sale de la máquina: markdown del documento, filas de tabla, recortes de figuras | Se queda en la máquina |
  | Hardware | Ninguno | Una GPU en la que quepa el modelo |
  | Coste | Por token; ~1/20 de la visión del PDF entero tras el enrutado por regiones | Electricidad |
  | Para qué usarlo | Especificaciones públicas o no confidenciales | Documentos que no pueden salir de la nave |
  | Por defecto hoy | Sí | `VLM_BACKEND=local` |
- `reference/gold.reqif`, un fichero que importó bien en Polarion, es opcional y no se incluye: depende de la plantilla de tu proyecto.
- Identidad, eventos y firma van como contrato con un valor local por defecto (variable de entorno, fichero JSONL, clave HMAC). Los adaptadores al Entra ID, SIEM o PKI de una empresa no están aquí ni se pueden probar aquí: ver [docs/INTEGRATION.es.md](docs/INTEGRATION.es.md).

---

## Seguridad y regulación

Los mismos cuatro epígrafes en todos los repositorios. Las filas **hoy** son lo que hace el código. Las filas **cable** son los tres cables de integración que el repositorio trae como contrato (identidad, eventos, firma) y la empresa conecta a sus sistemas: ver [docs/INTEGRATION.es.md](docs/INTEGRATION.es.md). Pruebas locales: `python tests/test_custodia.py`.

| | | |
|---|---|---|
| **Confidencialidad** | Este motor envía por defecto texto del documento y recortes de regiones a un modelo de nube (`GEMINI_MODEL`), así que es para **especificaciones no confidenciales**, o para un VLM local (`VLM_BACKEND=local`) cuando el documento no puede salir de la nave. Qué sale, cuando sale: el markdown del documento en la pasada A, filas de tabla como texto y recortes de figuras en la pasada B. `.env` y `data/` fuera de git. | **hoy** |
| | Ruta local por defecto, nube como excepción que hay que activar. Aún no hecho: hoy el defecto es nube, y el ledger registra cada llamada que sale. | **siguiente** |
| **Trazabilidad** | Cada ítem lleva su origen: página, anchor del header, procedencia del id (verbatim, derivado, anclado a página), y los cambios de `infoguard` se reportan por documento. `s5b_positions` escribe una auditoría de orden y jerarquía contra el PDF. | **hoy** |
| | **Cable 2 · eventos.** Toda llamada al modelo pasa por `custodia/ledger`: una línea JSON con actor, paso, motor, endpoint, si el dato salió, tamaño y hash. Nunca el texto. `logs/custody.jsonl` o stdout. La empresa apunta su SIEM ahí. | **cable** |
| **Prevención de fugas** | El modelo no tiene credenciales; el cliente tiene el endpoint. Los identificadores nunca se contaminan con marcadores ni prefijos que puedan filtrarse a herramientas aguas abajo: `AD-FullID` / `AD-ReqID` quedan limpios. | **hoy** |
| | **Cable 1 · identidad.** `run.py` se niega a arrancar sin un actor con nombre (`custodia/identity`, por defecto `CUSTODIA_ACTOR`); el actor se estampa en cada evento y cada firma. La empresa sustituye el proveedor por Entra ID, LDAP, Kerberos o su SSO en una llamada. Los permisos por capa sobre entradas, intermedios, modelo y salidas son los de la empresa, en su sistema de ficheros y su vault. | **cable** |
| **Humano en el bucle** | Los identificadores rechazados y los cambios de reconciliación se reportan, nunca se aplican en silencio. La variante anidada es la única recomendada para importar; la plana es opcional con sus riesgos escritos. | **hoy** |
| | **Cable 3 · firma.** Una salida es una propuesta hasta que `tools/signoff.py sign` escribe su manifiesto (hash del fichero, actor, fecha, firma) y `verify` pasa; un fichero cambiado o una clave distinta fallan. Nada que haya tocado el modelo se importa sin un manifiesto que verifique. La empresa sustituye la clave HMAC local por su PKI o por el flujo de aprobación de Polarion registrando dos funciones. | **cable** |

Marco: EU AI Act (2024/1689) · RGPD · ISO/IEC 42001 · ISO/IEC 27001 · TISAX.

---

## Estructura

```
pipeline/    s1..s6 (+ s5b positions, s5c infoguard, s4_legacy_fullpage como fallback)
clients/     gemini · vlm_local · docling_adapter        templates/  polarion_template.reqif
prompts/     requirements_extraction.txt · rescue_audit.txt
tools/       crop_ids · sanitize_reqif · survey_regions · verify_positions · signoff
utils/       id_policy · orphans · ids · md_headers · json_helpers
custodia/    identity · ledger · signoff · cli  — los tres cables de integración
docs/        ARCHITECTURE_v5.md · PLAN_robust.md · RISK_polarion.md · INTEGRATION.es.md
data/        inputs · interim · outputs — todo fuera de git
```

Daniel Martín · [epifaneia.dev](https://epifaneia.dev) · Apache-2.0

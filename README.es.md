# reqif-extractor · PDF → ReqIF para Polarion

*Read this in [English](README.md).*

Convierte un PDF de especificación en un fichero ReqIF que se importa en Polarion con la jerarquía del propio documento, sus identificadores tomados verbatim, y cada ítem en la posición que tenía en la página. El modelo lee; el código decide qué es un identificador, qué es informativo y en qué orden va cada cosa. Sin datos en este repositorio.

> La IA tiene riesgos. El código los acota. La norma responde de lo que queda.

[Filosofía de diseño](#filosofía-de-diseño) · [Dónde aporta valor la IA](#dónde-aporta-valor-la-ia) · [Los cinco pasos](#los-cinco-pasos-en-este-proyecto) · [Medido](#medido) · [Documentos públicos](#documentos-públicos) · [Demo](#demo-en-cuatro-comandos-datos-públicos) · [Limitaciones](#limitaciones) · [Seguridad y regulación](#seguridad-y-regulación)

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
| 3 · Guardarraíl de entrada | En la ruta principal todo primer intento lleva un schema de respuesta: extracción y auditoría de headers en `s3`; tablas, barrido de filas, rescate de huérfanos y recortes de figuras en `s4`. `type` es un enum `{requirement, information}`; que `parent_header ∈ anchors conocidos` lo comprueba el código tras la llamada. Un `type` fuera del enum se conserva como `requirement`, marcado `type_coerced` y contado en el resumen de la ejecución, nunca corregido en silencio. `s4` pide las filas de tabla como frases y prohíbe al modelo, en el schema y en el prompt, rellenar un identificador que la fila no lleve literalmente: la cadena vacía es la respuesta correcta. Sin schema: el reintento tras una respuesta que no es JSON (tablas de `s4` en el 2.º intento, recortes en el 3.º) y el fallback de página completa `s4_legacy_fullpage`, que solo pide JSON. El paso 5 se aplica a todos. | |
| 4 · Proceso | Pasada A: una llamada de texto por documento sobre el markdown. Pasada B: enrutada por región, tablas como texto y figuras como recortes en lotes, `gemini` o un VLM local con el mismo contrato. Medido sobre 9 documentos (~600 páginas): la pasada B costó 0,43 $, cuando la visión del PDF entero costaba ~1 € por 100 páginas, y conservó el 98,4 % de los ítems de esa referencia. | |
| 5 · Guardarraíl de salida | `utils/id_policy`: un identificador se acepta solo si está **verbatim** en el documento; si no, se **deriva** del id de su tabla y la fila (`SWS_Com_00347.3`), o se **ancla a la página** (`TBL-P31.4`) con una forma que no puede confundirse con uno real, o se **rechaza** y se reporta. `s5c_infoguard` reconcilia `type` contra el marcador "(information only)" de la propia fuente. `s5b_positions` audita orden y jerarquía contra el PDF. `s5_merge` deduplica por id, determinista. `s6` construye el ReqIF desde una plantilla de Polarion con un solo `<xhtml:div>` raíz por valor y todas las referencias resueltas. | ★ |

## Medido

Las cifras de esta tabla vienen de ejecuciones sobre documentos reales de especificación de un fabricante (confidenciales, no incluidos; procesados en la nube con autorización del cliente). La última columna dice de qué ejecución sale cada una. Las cifras medidas sobre documentos públicos están en la sección siguiente, contadas en los ficheros de salida.

| Qué | Cifra | De dónde sale |
|---|---|---|
| Identificadores que el modelo inventó en la ruta de tablas, antes de la política | **167 de 170** filas | Un documento real de 170 filas de tabla, anotado en `utils/id_policy.py` cuando se midió; no hay fichero de resultados. El modelo numeraba filas imitando la convención del documento y colisionaba con ids reales (`…-7` frente a `…-007`) |
| Identificadores que cambiaron entre dos ejecuciones sobre el mismo PDF, antes de la política | **49 de 214** | El mismo documento y la misma anotación. No reproducible. Con la política: 0 |
| Coste de la pasada B con enrutado por regiones, lote de 9 documentos (~600 páginas) | **0,43 $** | La visión del PDF entero costaba ~1 € por 100 páginas, ~6 € para esas páginas (monedas distintas, así que no se afirma un cociente exacto) |
| Ítems que conserva el enrutado por regiones frente a la referencia de visión del PDF entero | **98,4 %** (1.194 de 1.214) | Comparados id a id; los 20 que faltan (1,65 %) se auditaron uno a uno: descartes del árbitro en tres documentos |
| Documentos que pasan las cinco comprobaciones estructurales contra una referencia importada en Polarion | **12 / 12**, 2.695 referencias resueltas, 0 huérfanos | Medido con el motor anterior (v4), con Python y lxml sobre los ficheros reales (informe privado, 12 de junio de 2026). Con el motor actual: 12 documentos × 2 modos, XML válido y referencias resueltas. Comprobaciones: XML bien formado, spec types idénticos, un div raíz, referencias resueltas, prefijo de id por documento |

## Documentos públicos

Ejecución del 24 de septiembre de 2026. Contado en los ficheros `.reqif` de salida; cada identificador, comprobado por código contra el texto del PDF.

| Documento | Páginas | Requisitos | Cabeceras | Identificadores |
|---|---|---|---|---|
| AUTOSAR Specification of Communication (SWS COM), Classic Platform R21-11 | 220 | **465** | **217** | 465 de 465 copiados tal cual del PDF (351 `SWS_Com_…`, 114 `ECUC_Com_…`); ninguno derivado ni anclado a página |
| NIST SP 800-171 Rev. 2 (dominio público; retirada el 14 de mayo de 2024 y sustituida por la Rev. 3) | 114 | **382** | **18** | 382 de 382 copiados tal cual del PDF |

## Polarion

El fichero de AUTOSAR, importado en un Polarion de prueba. La importación se hizo a mano; estas capturas son su registro. Nada que tachar.

*El mapeo de importación: cabeceras a Heading, ítems a System Requirement, `ReqIF.ChapterName` (título de la cabecera) a Title, `ReqIF.Text` a Description. `ReqIF.ForeignID` lleva el id verbatim en el fichero, pero en esta prueba se dejó sin mapear.*

![Mapeo de importación ReqIF en Polarion](docs/img/polarion-01-import.png)

*El documento tras el import: la jerarquía del propio PDF, cada ítem bajo su cabecera, cada título el identificador verbatim `SWS_Com_…`.*

![Documento AUTOSAR SWS COM en Polarion](docs/img/polarion-02-tree.png)

*Un requisito: identificador, texto y su cabecera padre como enlace. `OEM REQ ID: false` es un campo propio del proyecto de prueba, no un valor del fichero: con `ReqIF.ForeignID` sin mapear conserva su valor por defecto. Mapea `ReqIF.ForeignID` a ese campo para rellenarlo.*

![Un requisito importado](docs/img/polarion-03-requirement.png)

## Demo en cuatro comandos (datos públicos)

Las especificaciones AUTOSAR son públicas y usan exactamente el patrón para el que se construyó este motor: identificadores entre corchetes al final de cada ítem. NIST SP 800-171 es de dominio público.

```bash
pip install -r requirements.txt              # pipeline completo, versiones fijadas (arrastra torch); pruebas y tools solo necesitan requirements-core.txt
cp .env.example .env                         # GEMINI_API_KEY (obligatoria); REQIF_VLM_BACKEND=local deja en local los recortes de figuras
# deja AUTOSAR_SWS_COM.pdf (autosar.org) y NIST_SP_800-171r2.pdf (nist.gov) en data/inputs/
python run.py                                # s1 → s6, todos los documentos, en paralelo
python tools/crop_ids.py && python tools/sanitize_reqif.py     # variantes listas para Polarion
```

Salida, todo bajo `data/outputs/reqif/`:

- `s6`: `v1_nested/<doc>.reqif` (la recomendada) y `v2_flat/<doc>.reqif` con `BUILD_MODE=both`, el valor por defecto; con `nested` o `flat` escribe directamente en `data/outputs/reqif/`.
- `tools/crop_ids.py`: copia a `<modo>_shortid/` con los ids de instancia recortados al código del documento (`SWSCOM_SO-R-0001`), para que un límite de longitud de Polarion no los trunque en colisiones; reescribe las referencias.
- `tools/sanitize_reqif.py`: copia `<modo>_shortid/` a `<modo>_shortid_clean/` quitando de los valores caracteres de control e invisibles, espacios raros y etiquetas HTML sueltas, nunca de los identificadores. El fichero a importar es `v1_nested_shortid_clean/<doc>.reqif`.

Este repositorio no lleva datos; `data/` está fuera de git.

## Limitaciones

- `id_policy` es algo más débil que «verbatim o nada» en dos casos que encontraron los tests: un id con forma de derivado (`….N`) pasa solo por su forma en las rutas de recortes, rescate y fallback (la ruta de tablas es segura: ignora los ids que devuelve el modelo), y «verbatim» es una búsqueda de subcadena, así que un prefijo truncado de un id real se acepta. Arreglar los dos exige volver a medir sobre el corpus real.
- Los ítems `type=information` siguen saliendo como work items de requisito, marcados explícitamente `(information only)` en el texto visible. Mapearlos al tipo Info nativo de Polarion está pendiente.
- La pasada de visión aún tiene ruido: un falso positivo con un id nuevo entra al fichero. `id_policy` rechaza los inventados; no puede rechazar uno con pinta real que simplemente esté mal.
- **Hoy el motor necesita siempre la nube.** La pasada A (markdown del documento), las filas de tabla y el arbitraje de huérfanos van a Gemini sea cual sea el backend. El VLM local sustituye una sola llamada, la de recortes de figuras de la pasada B:

  | | Nube (`gemini`) | VLM local (`vlm_local`) |
  |---|---|---|
  | Qué cubre | Todo: markdown, filas de tabla, recortes de figuras, arbitraje | Solo los recortes de figuras |
  | Dónde va el dato | Sale de la máquina | Los recortes se quedan en la máquina; el texto sigue saliendo |
  | Hardware | Ninguno | Una GPU en la que quepa el modelo |
  | Coste | Por token; pasada B medida en 0,43 $ para ~600 páginas tras el enrutado por regiones (visión del PDF entero: ~1 € por 100 páginas) | Electricidad |
  | Cómo se activa | Por defecto | `REQIF_VLM_BACKEND=local` |

  Por tanto, el motor es para **especificaciones públicas o no confidenciales**, o confidenciales solo si su dueño autoriza enviarlas a la nube, como hizo el cliente con los 12 documentos de arriba. Una ruta enteramente local (pasada A y tablas con un modelo local) no está construida.
- `reference/gold.reqif`, un fichero que importó bien en Polarion, es opcional y no se incluye: depende de la plantilla de tu proyecto.
- Identidad, eventos y firma van como contrato con un valor local por defecto (variable de entorno, fichero JSONL, clave HMAC). Los adaptadores al Entra ID, SIEM o PKI de una empresa no están aquí ni se pueden probar aquí: ver [docs/INTEGRATION.es.md](docs/INTEGRATION.es.md).

---

## Seguridad y regulación

Los mismos cuatro epígrafes en todos los repositorios. Las filas **hoy** son lo que hace el código. Las filas **cable** son los tres cables de integración que el repositorio trae como contrato (identidad, eventos, firma) y la empresa conecta a sus sistemas: ver [docs/INTEGRATION.es.md](docs/INTEGRATION.es.md). Pruebas locales, sin red y sin clave: `python tests/todas.py` (cables de custodia, política de identificadores, s3/s6/tools sobre datos sintéticos; solo necesitan `requirements-core.txt`).

| | | |
|---|---|---|
| **Confidencialidad** | Este motor envía texto del documento a un modelo de nube (`GEMINI_MODEL`), así que es **para especificaciones no confidenciales**, o confidenciales cuyo dueño autorice procesarlas en la nube. Qué sale: el markdown del documento en la pasada A, las filas de tabla como texto en la pasada B, y los recortes de figuras salvo con `REQIF_VLM_BACKEND=local`, que deja en la máquina solo los recortes. `.env` y `data/` fuera de git. | **hoy** |
| | Una ruta enteramente local (pasada A y tablas con un modelo local) y, después, local por defecto con la nube como excepción que hay que activar. Aún no hecho: hoy la nube es obligatoria, y el ledger registra cada llamada que sale. | **siguiente** |
| **Trazabilidad** | Cada ítem lleva su origen: página, anchor del header, procedencia del id (verbatim, derivado, anclado a página), y los cambios de `infoguard` se reportan por documento. `s5b_positions` escribe una auditoría de orden y jerarquía contra el PDF. Los identificadores nunca se contaminan con marcadores ni prefijos: el marcador `(information only)` va solo en el texto visible, `AD-FullID` / `AD-ReqID` quedan limpios para las herramientas que casan por ellos aguas abajo. | **hoy** |
| | **Cable 2 · eventos.** Toda llamada al modelo pasa por `custodia/ledger`: una línea JSON con actor, paso, motor, endpoint, si el dato salió, tamaño y hash. Nunca el texto. `logs/custody.jsonl` o stdout. La empresa apunta su SIEM ahí. | **cable** |
| **Prevención de fugas** | El modelo no tiene credenciales; el cliente tiene el endpoint y la clave sale del entorno o de un `.env` fuera de git, nunca del repositorio. Solo `clients/gemini` y `clients/vlm_local` envían contenido del documento por la red, y los dos pasan por el ledger. Todo lo demás es código que no envía nada. | **hoy** |
| | **Cable 1 · identidad.** `run.py` se niega a arrancar sin un actor con nombre (`custodia/identity`, por defecto `CUSTODIA_ACTOR`); el actor se estampa en cada evento y cada firma. La empresa sustituye el proveedor por Entra ID, LDAP, Kerberos o su SSO en una llamada. Los permisos por capa sobre entradas, intermedios, modelo y salidas son los de la empresa, en su sistema de ficheros y su vault. | **cable** |
| **Humano en el bucle** | Los identificadores rechazados y los cambios de reconciliación se reportan, nunca se aplican en silencio. La variante anidada es la única recomendada para importar; la plana es opcional con sus riesgos escritos. | **hoy** |
| | **Cable 3 · firma.** Una salida es una propuesta hasta que `tools/signoff.py sign` escribe su manifiesto (hash del fichero, actor, fecha, firma) y `verify` pasa; un fichero cambiado o una clave distinta fallan. Nada que haya tocado el modelo se importa sin un manifiesto que verifique. La empresa sustituye la clave HMAC local por su PKI o por el flujo de aprobación de Polarion registrando dos funciones. | **cable** |

Redactado con referencia a la EU AI Act (2024/1689), el RGPD, ISO/IEC 42001, ISO/IEC 27001 y TISAX. No se declara certificación ni conformidad: la tabla dice qué parte cubre el código y qué parte corresponde a la empresa.

---

## Estructura

```
pipeline/    s1..s6 (+ s5b positions, s5c infoguard, s4_legacy_fullpage como fallback)
clients/     gemini · vlm_local · docling_adapter        templates/  polarion_template.reqif
prompts/     requirements_extraction.txt · rescue_audit.txt
tools/       crop_ids · sanitize_reqif · survey_regions · verify_positions · signoff
utils/       id_policy · orphans · ids · md_headers · json_helpers
custodia/    identity · ledger · signoff · cli  — los tres cables de integración
tests/       todas (runner) · test_custodia · test_id_policy · test_salida — sin red, datos sintéticos
docs/        ARCHITECTURE_v5.md · PLAN_robust.md · RISK_polarion.md · INTEGRATION.es.md
data/        inputs · interim · outputs — todo fuera de git
```

Daniel Martín · [epifaneia.dev](https://epifaneia.dev) · Apache-2.0

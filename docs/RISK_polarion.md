# Riesgos de la jerarquía PLANA en Polarion

## Contexto

El único ReqIF **probado con import real** en Polarion es
`reference/gold.reqif` (un ReqIF exportado de Polarion tras un import correcto; no incluido), y es **ANIDADO** (nested): los
`SPEC-HIERARCHY` de los headers anidan sub-headers por jerarquía real del
documento, y los requisitos cuelgan de su header. Ese import salió bien.

El motor genera además una variante **PLANA** (`v2_flat`): todos los headers al
mismo nivel bajo la SPECIFICATION, con sus requisitos un nivel debajo. Esta
variante NO se ha importado nunca en Polarion. Estos son sus riesgos.

## Riesgos del modo plano

### (a) Polarion puede RENUMERAR los headings planos

Polarion deriva la numeración de outline de los headings de la **posición en el
árbol SPEC-HIERARCHY**, no de la etiqueta que traigan. Histórico real del
proyecto: con sub-headings reales, Polarion renumeraba `5.1 → 1` cuando la
posición en el árbol no coincidía con el número del documento original. En el
modo plano TODOS los headers son hermanos al nivel 1, así que un documento con
`1, 1.1, 1.2, 2, 2.1...` puede acabar mostrado como `1, 2, 3, 4, 5...` en el
Live Document. El contenido no se pierde, pero la numeración visible deja de
corresponder con la del PDF fuente, que es justo lo que el cliente coteja.

### (b) ChapterNumber como etiqueta vs numeración por posición

El reqif plano lleva el número original (p. ej. `5.1`) en el atributo con
LONG-NAME `ReqIF.ChapterNumber` — pero solo como **etiqueta de display**.
`ReqIF.ChapterNumber` es precisamente el campo que Polarion asocia a la
numeración de outline, y Polarion **podría sobrescribirlo según la posición**
del heading en el árbol al importar o al re-guardar el documento. Es decir: no
hay garantía de que la etiqueta `5.1` sobreviva en un árbol donde ese heading
es el quinto hermano de nivel 1. En el modo nested este conflicto no existe,
porque posición en el árbol y número del documento coinciden.

### (c) "Unresolved parent key: null" — NO era culpa del plano

El fallo histórico `Unresolved parent key: null` en imports anteriores se debió
a **LONG-NAMEs mal puestos en una versión vieja del generador** (atributos que
no coincidían con los nombres mágicos que el importador espera), agravado por
IDs de instancia compartidos entre documentos. Ambas causas están **ya
arregladas**: la plantilla actual replica los LONG-NAMEs del GOLD probado y los
IDs llevan prefijo por documento. Ese fallo NO es evidencia contra la jerarquía
plana en sí — pero tampoco hay evidencia a favor: el plano simplemente no se ha
probado.

## Conclusión y recomendación

**La versión SAFE es la NESTED** (`v1_nested`): es estructuralmente idéntica al
GOLD que ya se importó con éxito (misma anidación de SPEC-HIERARCHY, mismos
SPEC-TYPES byte-idénticos salvo timestamps, mismo formato de AD-Text con un
solo `<xhtml:div>`). El único delta respecto al GOLD son los prefijos de ID por
documento (necesarios y de bajo riesgo: Polarion solo exige unicidad) y más
contenido (items `type=information`).

**Recomendación: importar PRIMERO la versión nested en Polarion.** Solo si el
cliente quiere explícitamente la vista plana, probar `v2_flat` después, en un
documento desechable, y verificar (a) y (b) antes de adoptarla.

## Verificación: el motor SÍ produce la versión nested

- `config.BUILD_MODE` (env `BUILD_MODE`) acepta `"nested" | "flat" | "both"` y
  el **default es `"both"`** (`config.py`), de modo que la nested se genera
  siempre salvo que alguien fuerce `flat`.
- `pipeline/s6_build_reqif.py` implementa el modo `nested` anidando
  recursivamente por `parent_anchor` (función `build_subtree`), y en modo
  `both` escribe `data/outputs/reqif/v1_nested/` y `v2_flat/`.
- Verificado en disco para los dos documentos de prueba: existen
  `data/outputs/reqif/v1_nested/<doc>.reqif`
  XML bien formado, todas las `SPEC-OBJECT-REF` resueltas, cada `THE-VALUE`
  XHTML con un único `<xhtml:div>`, y bloque `SPEC-TYPES` idéntico al GOLD
  (normalizando `LAST-CHANGE`).

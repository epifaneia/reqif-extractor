# Contrato de integración

*Read this in [English](INTEGRATION.md).*

Este pipeline está completo como herramienta y deliberadamente incompleto como despliegue. Tres cosas que se niega a inventar, porque toda empresa ya las tiene: quién lo ejecuta, dónde va el registro, y quién firma. El repositorio trae el contrato de cada una de las tres; conectarlo a los sistemas de la empresa es trabajo de integración, por empresa, medido en días.

| Cable | En el repo hoy | Lado de la empresa | Estado |
|---|---|---|---|
| **1 · Identidad** | `custodia/identity.py`. El pipeline se niega a correr sin un actor con nombre. Proveedor por defecto: variable de entorno `CUSTODIA_ACTOR`. Sustituible con una llamada. | Proporcionar el actor desde Entra ID, LDAP, Kerberos o la cabecera del SSO. Diez líneas. | implementado / por integrar |
| **2 · Eventos** | `custodia/ledger.py`. Una línea JSON por llamada al modelo: actor, paso, motor, endpoint, si el dato salió de la máquina, tamaño de entrada, hash de entrada, resultado. Nunca el texto. Fichero `logs/custody.jsonl` o stdout. | Apuntar el agente del SIEM (Splunk, Sentinel, ELK) al fichero. Nada que escribir. | implementado / por integrar |
| **3 · Firma** | `custodia/signoff.py` y `tools/signoff.py`. Una salida es una propuesta hasta que un manifiesto la firma: hash del fichero, actor, fecha, firma HMAC con clave local. `verify` falla si el fichero cambió o la clave es distinta. | Sustituir HMAC por la PKI corporativa o el flujo de aprobación (Polarion, firma electrónica) registrando dos funciones. El manifiesto y la comprobación de hash no cambian. | implementado (HMAC) / por integrar |

Pruebas locales, sin red, sin dependencias: `python tests/test_custodia.py`.

---

## 1 · Identidad

```python
# por defecto: export CUSTODIA_ACTOR=ana.garcia  → run.py imprime "run: actor ana.garcia"
# sin ella:                                       → run.py sale con 2: el pipeline no corre de forma anónima

# adaptador de la empresa, al arrancar (run.py o un envoltorio):
from custodia import identity
identity.set_provider(lambda: obtener_subject_entra_id())   # cualquier callable que devuelva una cadena no vacía
```

El pipeline no valida credenciales: eso lo hace el sistema de la empresa. Aquí la única regla es que exista un nombre, y ese nombre se estampa en cada evento del ledger y en cada manifiesto de firma.

Mapeos: Entra ID → `subject` / `preferred_username` del token · LDAP/AD → `sAMAccountName` · Kerberos → principal · proxy inverso con SSO → cabecera `X-Forwarded-User`.

## 2 · Eventos

Toda llamada al modelo pasa por una función por cliente (`_post`), que registra un evento. Esquema de una línea de `logs/custody.jsonl`:

```json
{"ts": "2026-09-21T12:49:04", "actor": "ana.garcia", "step": "s4_evaluate",
 "engine": "ollama", "endpoint": "127.0.0.1:11434", "leaves_machine": false,
 "input_chars": 5253, "input_sha256_16": "5791a8ab6c15bee8", "output_chars": 812,
 "ok": true, "seconds": 77.3}
```

| Campo | Significado |
|---|---|
| `actor` | quién lo ejecutó (cable 1) |
| `step` | paso del pipeline que hace la llamada |
| `engine` / `endpoint` | servicio de modelo y host |
| `leaves_machine` | `true` si el endpoint no es local |
| `input_chars` / `input_sha256_16` | tamaño y hash truncado de la petición; **nunca el texto** |
| `ok` / `error` / `seconds` | resultado y duración; los errores se registran y se relanzan |

Configuración: `CUSTODIA_LEDGER=logs/custody.jsonl` (por defecto) o `CUSTODIA_LEDGER=-` para stdout. Resumen para la ficha de custodia: `python tools/signoff.py ledger`.

Lado de la empresa: una entrada de tipo fichero en Splunk, una DCR en Sentinel, Filebeat para ELK. La línea ya es JSON.

## 3 · Firma

```bash
export CUSTODIA_ACTOR=ana.garcia CUSTODIA_SIGNING_KEY=<secreto del vault>
python tools/signoff.py sign   data/outputs/reqif/X.reqif     # → X.reqif.signoff.json
python tools/signoff.py verify data/outputs/reqif/X.reqif     # sale 0 solo si está firmado y sin cambios
```

Regla: **nada que haya tocado el modelo se importa ni se envía sin un manifiesto que verifique.** El paso de import del flujo de la empresa llama a `verify` (o a `custodia.signoff.require_signoff`) antes.

Adaptador de la empresa: se conserva el manifiesto, se sustituye la firma.

```python
from custodia import signoff
signoff.set_scheme("pki-corporativa", sign_fn=lambda m: firmar_pki(m["sha256"], m["actor"], m["ts"]),
                                      verify_fn=lambda m: verificar_pki(m))
```

Mapeos: PKI corporativa (X.509) · flujo de aprobación de Polarion (la transición del flujo crea el manifiesto) · proveedor de firma electrónica · cuatro ojos: dos manifiestos, dos actores.

---

## Lo que no está a propósito

- Una base de usuarios, contraseñas, roles.
- Un servidor de logs.
- Una autoridad de certificación.

Cada una de esas cosas ya existe en la empresa, auditada, con un responsable. Una herramienta que trae las suyas es un riesgo que su TI rechazará con razón. El contrato de arriba es lo que hace que conectarse a las suyas sea cuestión de días.

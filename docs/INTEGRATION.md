# Integration contract

*Read this in [Español](INTEGRATION.es.md).*

This pipeline is complete as a tool and deliberately incomplete as a deployment. Three things it refuses to invent, because every company already has them: who is running it, where the record goes, and who signs. The repository ships the contract for each of the three; wiring it to the company's systems is integration work, per company, measured in days.

| Wire | In the repo today | Company side | State |
|---|---|---|---|
| **1 · Identity** | `custodia/identity.py`. The pipeline refuses to run without a named actor. Default provider: environment variable `CUSTODIA_ACTOR`. Replaceable with one call. | Provide the actor from Entra ID, LDAP, Kerberos or the SSO header. Ten lines. | implemented / to integrate |
| **2 · Events** | `custodia/ledger.py`. One JSON line per model call: actor, step, engine, endpoint, whether data left the machine, input size, input hash, result. Never the text. File `logs/custody.jsonl` or stdout. | Point the SIEM agent (Splunk, Sentinel, ELK) at the file. Nothing to write. | implemented / to integrate |
| **3 · Sign-off** | `custodia/signoff.py` and `tools/signoff.py`. An output is a proposal until a manifest signs it: file hash, actor, timestamp, HMAC signature with a local key. `verify` fails if the file changed or the key differs. | Replace HMAC with the corporate PKI or the approval workflow (Polarion, e-signature) by registering two functions. The manifest and the hash check stay. | implemented (HMAC) / to integrate |

Local tests, no network, no dependencies: `python tests/test_custodia.py`.

---

## 1 · Identity

```python
# default: export CUSTODIA_ACTOR=ana.garcia  → run.py prints "run: actor ana.garcia"
# without it:                                → run.py exits 2: the pipeline does not run anonymously

# company adapter, at startup (run.py or a wrapper):
from custodia import identity
identity.set_provider(lambda: get_entra_id_subject())      # any callable returning a non-empty string
```

The pipeline does not validate credentials: the company's system does. Here the only rule is that a name exists, and that name is stamped on every ledger event and on every sign-off manifest.

Mappings: Entra ID → `subject` / `preferred_username` of the token · LDAP/AD → `sAMAccountName` · Kerberos → principal · SSO reverse proxy → `X-Forwarded-User` header.

## 2 · Events

Every model call goes through one function per client (`_post`), which records an event. Schema of a line in `logs/custody.jsonl`:

```json
{"ts": "2026-09-21T12:49:04", "actor": "ana.garcia", "step": "s4_evaluate",
 "engine": "ollama", "endpoint": "127.0.0.1:11434", "leaves_machine": false,
 "input_chars": 5253, "input_sha256_16": "5791a8ab6c15bee8", "output_chars": 812,
 "ok": true, "seconds": 77.3}
```

| Field | Meaning |
|---|---|
| `actor` | who ran it (wire 1) |
| `step` | pipeline step making the call |
| `engine` / `endpoint` | model service and host |
| `leaves_machine` | `true` if the endpoint is not local |
| `input_chars` / `input_sha256_16` | size and truncated hash of the request; **never the text** |
| `ok` / `error` / `seconds` | outcome and duration; errors are recorded and re-raised |

Configuration: `CUSTODIA_LEDGER=logs/custody.jsonl` (default) or `CUSTODIA_LEDGER=-` for stdout. Summary for the custody sheet: `python tools/signoff.py ledger`.

Company side: a file-tail input in Splunk, a DCR in Sentinel, Filebeat for ELK. The line is already JSON.

## 3 · Sign-off

```bash
export CUSTODIA_ACTOR=ana.garcia CUSTODIA_SIGNING_KEY=<secret from the vault>
python tools/signoff.py sign   data/outputs/reqif/X.reqif     # → X.reqif.signoff.json
python tools/signoff.py verify data/outputs/reqif/X.reqif     # exit 0 only if signed and unchanged
```

Rule: **nothing the model touched is imported or sent without a manifest that verifies.** The import step of the company's workflow calls `verify` (or `custodia.signoff.require_signoff`) first.

Company adapter: keep the manifest, replace the signature.

```python
from custodia import signoff
signoff.set_scheme("corp-pki", sign_fn=lambda m: pki_sign(m["sha256"], m["actor"], m["ts"]),
                               verify_fn=lambda m: pki_verify(m))
```

Mappings: corporate PKI (X.509) · Polarion approval workflow (the workflow transition creates the manifest) · e-signature provider · four-eyes: two manifests, two actors.

---

## What is not here on purpose

- A user database, passwords, roles.
- A log server.
- A certificate authority.

Every one of those already exists in the company, audited, with an owner. A tool that brings its own is a liability their IT will rightly reject. The contract above is what makes connecting to theirs a matter of days.

"""identity — quién ejecuta el pipeline. Primer cable de integración.

El pipeline se niega a correr sin un actor con nombre. Por defecto el actor se
lee de la variable de entorno CUSTODIA_ACTOR; si está vacía, `current_actor()`
lanza MissingActor y la ejecución se para antes de tocar ningún dato.

La empresa sustituye el proveedor por el suyo con una sola llamada al arrancar:

    from custodia import identity
    identity.set_provider(lambda: entra_id_token_subject())   # Entra ID, LDAP, Kerberos, SSO...

El proveedor devuelve una cadena no vacía que identifique a la persona (correo
corporativo, sAMAccountName, subject del token). El pipeline no valida
credenciales: eso lo hace el sistema de la empresa; aquí solo se exige que
exista un nombre y se propaga a cada evento del ledger y a cada manifiesto de
firma.

Estado: implementado (proveedor por variable de entorno). El adaptador a cada
sistema corporativo es trabajo de integración por empresa.
"""
from __future__ import annotations

import os
from typing import Callable

ENV_VAR = "CUSTODIA_ACTOR"


class MissingActor(RuntimeError):
    """No hay actor: el pipeline no puede correr de forma anónima."""


def _env_provider() -> str:
    return os.environ.get(ENV_VAR, "").strip()


_provider: Callable[[], str] = _env_provider


def set_provider(fn: Callable[[], str]) -> None:
    """Sustituye el origen de identidad (Entra ID, LDAP, SSO...)."""
    global _provider
    _provider = fn


def current_actor() -> str:
    """Actor con nombre, o MissingActor. Nunca devuelve cadena vacía."""
    actor = (_provider() or "").strip()
    if not actor:
        raise MissingActor(
            f"Sin actor: exporta {ENV_VAR}=<usuario> o registra un proveedor con "
            "identity.set_provider(). El pipeline no corre de forma anónima.")
    return actor


def require_actor() -> str:
    """Alias explícito para el arranque de un run: falla pronto y con mensaje."""
    return current_actor()

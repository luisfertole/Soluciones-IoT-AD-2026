import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import API_KEY

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Clave requerida en las operaciones POST, PUT y DELETE.",
)


def verificar_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    """Dependencia que protege las operaciones de control y eliminación."""
    if not API_KEY:
        # Si el servidor no tiene clave configurada, se bloquea en lugar de dejar abierto.
        raise HTTPException(
            status_code=503,
            detail="Operación no disponible: la API key no está configurada en el servidor",
        )
    if not api_key or not secrets.compare_digest(
        api_key.encode("utf-8"), API_KEY.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="API key inválida o ausente",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
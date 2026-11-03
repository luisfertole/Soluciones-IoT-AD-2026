import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import API_KEY

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False,
                               description="Clave requerida en POST, PUT y DELETE.")


def api_key_valida(clave) -> bool:
    return bool(API_KEY and clave and isinstance(clave, str)
                and secrets.compare_digest(clave.encode("utf-8"), API_KEY.encode("utf-8")))


def verificar_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    if not API_KEY:
        raise HTTPException(status_code=503, detail="Operación no disponible: la API key no está configurada en el servidor")
    if not api_key_valida(api_key):
        raise HTTPException(status_code=401, detail="API key inválida o ausente",
                            headers={"WWW-Authenticate": "ApiKey"})
from pydantic import BaseModel, Field


class VehiculoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    modelo: str = Field(min_length=1, max_length=100)
    descripcion: str = Field(default="", max_length=255)
    microcontrolador: str = Field(min_length=1, max_length=50)


class VehiculoUpdate(VehiculoIn):
    activo: bool


class ComandoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=50)
    codigo: int = Field(ge=0, le=255)
    descripcion: str = Field(default="", max_length=255)


class ComandoUpdate(ComandoIn):
    activo: bool


class DemoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    descripcion: str = Field(default="", max_length=255)


class DemoUpdate(DemoIn):
    activo: bool


class MovimientoDemoIn(BaseModel):
    id_comando: int = Field(gt=0)
    orden: int = Field(ge=1, le=1000)
    duracion_ms: int = Field(gt=0, le=600_000)
    pausa_ms: int = Field(ge=0, le=600_000)
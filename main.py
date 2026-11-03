import asyncio
import logging

import pymysql
from fastapi import (Depends, FastAPI, HTTPException, Query, WebSocket,
                     WebSocketDisconnect)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.demos import demo_runner
from app.schemas import (ComandoIn, ComandoUpdate, DemoIn, DemoUpdate,
                         MovimientoDemoIn, VehiculoIn, VehiculoUpdate)
from app.security import verificar_api_key
from app.services import VehicleService
from app.websockets import manager
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from app.control import hub, router as control_router

log = logging.getLogger("iot.api")

@asynccontextmanager
async def lifespan(app):
    hub.iniciar()
    yield
    await hub.detener()

app = FastAPI(
    title="API Vehículo IoT",
    version="1.1.0",
    lifespan=lifespan,
    description=(
        "API de control de vehículos IoT. Los GET son públicos; los POST, PUT y DELETE "
        "requieren el header `X-API-Key` (botón **Authorize**). "
        "Monitoreo en tiempo real: `wss://<host>/ws/vehiculos/{id_vehiculo}`."
    ),
)

PROTEGIDO = [Depends(verificar_api_key)]
E401 = {401: {"description": "API key inválida o ausente"}}
E404 = {404: {"description": "Recurso no encontrado"}}
E409 = {409: {"description": "Conflicto: hay una DEMO en ejecución"}}


# ---------------------------------------------------------------- errores
@app.exception_handler(pymysql.MySQLError)
async def mysql_error_handler(request, exc):
    # El detalle va al log del servidor, nunca al cliente.
    log.error("Error MySQL en %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Error interno al acceder a la base de datos"})


# ---------------------------------------------------------------- helpers
def _vehiculo_o_404(id_vehiculo: int) -> dict:
    vehiculo = VehicleService.obtener_vehiculo(id_vehiculo)
    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return vehiculo


def _comando_o_404(id_vehiculo: int, id_comando: int) -> dict:
    comandos = VehicleService.obtener_comandos_vehiculo(id_vehiculo)
    comando = next((c for c in comandos if c["id_comando"] == id_comando), None)
    if not comando:
        raise HTTPException(status_code=404, detail="Comando no existe o no pertenece a este vehículo")
    return comando


def _demo_o_404(id_vehiculo: int, id_demo: int) -> dict:
    demos = VehicleService.obtener_demos(id_vehiculo)
    demo = next((d for d in demos if d["id_demo"] == id_demo), None)
    if not demo:
        raise HTTPException(status_code=404, detail="Demo no existe o no pertenece a este vehículo")
    return demo


def _sin_demo_activa(id_vehiculo: int) -> None:
    if demo_runner.en_curso(id_vehiculo):
        raise HTTPException(status_code=409, detail="Hay una DEMO en ejecución para este vehículo")


@app.get("/", include_in_schema=False)
def read_root():
    return {"status": "API operativa", "docs": "/docs"}


# ================================================================ VEHÍCULOS (5 SP)
@app.get("/vehiculos", tags=["Vehículos"], summary="Listar vehículos",
         description="SP: `sp_obtener_vehiculos`.")
def listar_vehiculos():
    return VehicleService.obtener_vehiculos()


@app.get("/vehiculos/{id_vehiculo}", tags=["Vehículos"], summary="Obtener un vehículo",
         description="SP: `sp_obtener_vehiculo`.", responses=E404)
def obtener_vehiculo(id_vehiculo: int):
    return _vehiculo_o_404(id_vehiculo)


@app.post("/vehiculos", status_code=201, tags=["Vehículos"], summary="Crear vehículo",
          description="SP: `sp_crear_vehiculo`.", dependencies=PROTEGIDO, responses=E401)
def crear_vehiculo(datos: VehiculoIn):
    filas = VehicleService.crear_vehiculo(datos.nombre, datos.modelo, datos.descripcion, datos.microcontrolador)
    return {"status": "creado", "resultado": filas}


@app.put("/vehiculos/{id_vehiculo}", tags=["Vehículos"], summary="Actualizar vehículo",
         description="SP: `sp_actualizar_vehiculo`.", dependencies=PROTEGIDO, responses={**E401, **E404})
def actualizar_vehiculo(id_vehiculo: int, datos: VehiculoUpdate):
    _vehiculo_o_404(id_vehiculo)
    VehicleService.actualizar_vehiculo(id_vehiculo, datos.nombre, datos.modelo, datos.descripcion,
                                       datos.microcontrolador, datos.activo)
    return {"status": "actualizado"}


@app.delete("/vehiculos/{id_vehiculo}", tags=["Vehículos"], summary="Eliminar vehículo",
            description="SP: `sp_eliminar_vehiculo`.", dependencies=PROTEGIDO,
            responses={**E401, **E404, **E409})
def eliminar_vehiculo(id_vehiculo: int):
    _vehiculo_o_404(id_vehiculo)
    _sin_demo_activa(id_vehiculo)
    VehicleService.eliminar_vehiculo(id_vehiculo)
    return {"status": "eliminado"}


# ================================================================ COMANDOS (4 SP)
@app.get("/vehiculos/{id_vehiculo}/comandos", tags=["Comandos"], summary="Listar comandos del vehículo",
         description="SP: `sp_obtener_comandos_vehiculo`.", responses=E404)
def listar_comandos(id_vehiculo: int):
    _vehiculo_o_404(id_vehiculo)
    return VehicleService.obtener_comandos_vehiculo(id_vehiculo)


@app.post("/vehiculos/{id_vehiculo}/comandos", status_code=201, tags=["Comandos"],
          summary="Crear comando", description="SP: `sp_crear_comando`.",
          dependencies=PROTEGIDO, responses={**E401, **E404})
def crear_comando(id_vehiculo: int, datos: ComandoIn):
    _vehiculo_o_404(id_vehiculo)
    filas = VehicleService.crear_comando(id_vehiculo, datos.nombre, datos.codigo, datos.descripcion)
    return {"status": "creado", "resultado": filas}


@app.put("/vehiculos/{id_vehiculo}/comandos/{id_comando}", tags=["Comandos"],
         summary="Actualizar comando", description="SP: `sp_actualizar_comando`.",
         dependencies=PROTEGIDO, responses={**E401, **E404})
def actualizar_comando(id_vehiculo: int, id_comando: int, datos: ComandoUpdate):
    _vehiculo_o_404(id_vehiculo)
    _comando_o_404(id_vehiculo, id_comando)
    VehicleService.actualizar_comando(id_comando, datos.nombre, datos.codigo, datos.descripcion, datos.activo)
    return {"status": "actualizado"}


@app.delete("/vehiculos/{id_vehiculo}/comandos/{id_comando}", tags=["Comandos"],
            summary="Eliminar comando", description="SP: `sp_eliminar_comando`.",
            dependencies=PROTEGIDO, responses={**E401, **E404, **E409})
def eliminar_comando(id_vehiculo: int, id_comando: int):
    _vehiculo_o_404(id_vehiculo)
    _comando_o_404(id_vehiculo, id_comando)
    _sin_demo_activa(id_vehiculo)
    VehicleService.eliminar_comando(id_comando)
    return {"status": "eliminado"}


# ================================================================ ESTADO (2 SP)
@app.get("/vehiculos/{id_vehiculo}/estado", tags=["Estado"], summary="Último estado del vehículo",
         description="SP: `sp_obtener_estado`. El estado se actualiza con `sp_actualizar_estado` "
                     "al ejecutar movimientos individuales y DEMO.", responses=E404)
def obtener_estado(id_vehiculo: int):
    _vehiculo_o_404(id_vehiculo)
    estado = VehicleService.obtener_estado(id_vehiculo)
    if not estado:
        raise HTTPException(status_code=404, detail="El vehículo aún no tiene estado registrado")
    return estado


# ================================================================ HISTORIAL (2 SP)
@app.get("/vehiculos/{id_vehiculo}/historial", tags=["Historial"], summary="Historial reciente",
         description="Máximo 10 movimientos individuales, del más reciente al más antiguo. "
                     "No incluye movimientos de DEMO. SP: `sp_obtener_historial`.", responses=E404)
def obtener_historial_vehiculo(id_vehiculo: int):
    _vehiculo_o_404(id_vehiculo)
    historial = VehicleService.obtener_historial(id_vehiculo)
    return {"id_vehiculo": id_vehiculo, "total_registros_devueltos": len(historial), "historial": historial}


@app.post("/vehiculos/{id_vehiculo}/mover", tags=["Historial"], summary="Movimiento individual",
          description="Valida vehículo y comando; en UNA transacción ejecuta `sp_actualizar_estado` y "
                      "`sp_registrar_historial`; luego notifica por WebSocket (evento `MOVIMIENTO_INDIVIDUAL`).",
          dependencies=PROTEGIDO, responses={**E401, **E404, **E409})
async def ejecutar_movimiento(id_vehiculo: int, id_comando: int = Query(gt=0)):
    _sin_demo_activa(id_vehiculo)
    vehiculo = await asyncio.to_thread(_vehiculo_o_404, id_vehiculo)
    if not vehiculo["activo"]:
        raise HTTPException(status_code=400, detail="Vehículo inactivo")
    comando = await asyncio.to_thread(_comando_o_404, id_vehiculo, id_comando)
    if not comando["activo"]:
        raise HTTPException(status_code=400, detail="Comando inactivo")

    await asyncio.to_thread(VehicleService.registrar_movimiento, id_vehiculo, id_comando, "API-POST")
    estado = await asyncio.to_thread(VehicleService.obtener_estado, id_vehiculo)

    await manager.broadcast_to_vehicle(id_vehiculo, {
        "tipo": "MOVIMIENTO_INDIVIDUAL",
        "id_vehiculo": id_vehiculo,
        "id_comando": id_comando,
        "comando": comando["nombre"],
        "codigo": comando["codigo"],
        "origen": "API-POST",
        "estado": estado,
    })
    return {"status": "success", "mensaje": f"Comando {comando['nombre']} ejecutado y registrado en historial."}


# ================================================================ DEMO (4 SP)
@app.get("/vehiculos/{id_vehiculo}/demos", tags=["DEMO"], summary="Listar DEMO del vehículo",
         description="SP: `sp_obtener_demos`.", responses=E404)
def listar_demos(id_vehiculo: int):
    _vehiculo_o_404(id_vehiculo)
    return VehicleService.obtener_demos(id_vehiculo)


@app.post("/vehiculos/{id_vehiculo}/demos", status_code=201, tags=["DEMO"], summary="Crear DEMO",
          description="SP: `sp_crear_demo`.", dependencies=PROTEGIDO, responses={**E401, **E404})
def crear_demo(id_vehiculo: int, datos: DemoIn):
    _vehiculo_o_404(id_vehiculo)
    filas = VehicleService.crear_demo(id_vehiculo, datos.nombre, datos.descripcion)
    return {"status": "creado", "resultado": filas}


@app.put("/vehiculos/{id_vehiculo}/demos/{id_demo}", tags=["DEMO"], summary="Actualizar DEMO",
         description="SP: `sp_actualizar_demo`.", dependencies=PROTEGIDO, responses={**E401, **E404, **E409})
def actualizar_demo(id_vehiculo: int, id_demo: int, datos: DemoUpdate):
    _vehiculo_o_404(id_vehiculo)
    _demo_o_404(id_vehiculo, id_demo)
    _sin_demo_activa(id_vehiculo)
    VehicleService.actualizar_demo(id_demo, datos.nombre, datos.descripcion, datos.activo)
    return {"status": "actualizado"}


@app.delete("/vehiculos/{id_vehiculo}/demos/{id_demo}", tags=["DEMO"], summary="Eliminar DEMO",
            description="SP: `sp_eliminar_demo`.", dependencies=PROTEGIDO, responses={**E401, **E404, **E409})
def eliminar_demo(id_vehiculo: int, id_demo: int):
    _vehiculo_o_404(id_vehiculo)
    _demo_o_404(id_vehiculo, id_demo)
    _sin_demo_activa(id_vehiculo)
    VehicleService.eliminar_demo(id_demo)
    return {"status": "eliminado"}


@app.post("/vehiculos/{id_vehiculo}/demos/cancelar", tags=["DEMO"], summary="Cancelar la DEMO en curso",
          description="Detiene los pasos posteriores y emite el evento WSS `DEMO_CANCELADA`.",
          dependencies=PROTEGIDO, responses={**E401, **E404})
async def cancelar_demo(id_vehiculo: int):
    if not demo_runner.cancelar(id_vehiculo):
        raise HTTPException(status_code=404, detail="No hay una DEMO en ejecución para este vehículo")
    return {"status": "cancelando", "mensaje": "Cancelación solicitada; se notificará por WebSocket"}


@app.post("/vehiculos/{id_vehiculo}/demos/{id_demo}/ejecutar", status_code=202, tags=["DEMO"],
          summary="Ejecutar DEMO en tiempo real",
          description="Transmite por WSS `DEMO_INICIADA`, N `DEMO_MOVIMIENTO` (según `sp_obtener_movimientos_demo`, "
                      "respetando orden, duración y pausa) y `DEMO_FINALIZADA`. Actualiza el último estado "
                      "(`sp_actualizar_estado`) pero NO registra historial. Solo una DEMO por vehículo.",
          dependencies=PROTEGIDO, responses={**E401, **E404, **E409})
async def ejecutar_demo(id_vehiculo: int, id_demo: int):
    # Se reserva el cupo antes de cualquier await para evitar que dos POST simultáneos pasen la validación.
    if not demo_runner.reservar(id_vehiculo):
        raise HTTPException(status_code=409, detail="Ya hay una DEMO en ejecución para este vehículo")
    try:
        vehiculo = await asyncio.to_thread(_vehiculo_o_404, id_vehiculo)
        if not vehiculo["activo"]:
            raise HTTPException(status_code=400, detail="Vehículo inactivo")
        demo = await asyncio.to_thread(_demo_o_404, id_vehiculo, id_demo)
        if not demo.get("activo", True):
            raise HTTPException(status_code=400, detail="La DEMO está inactiva")
        movimientos = await asyncio.to_thread(VehicleService.obtener_movimientos_demo, id_demo)
        if not movimientos:
            raise HTTPException(status_code=422, detail="La DEMO no tiene movimientos registrados")
        movimientos = sorted(movimientos, key=lambda m: m["orden"])
        demo_runner.iniciar(id_vehiculo, id_demo, movimientos)
    except BaseException:
        demo_runner.liberar(id_vehiculo)
        raise
    return {"status": "accepted", "id_demo": id_demo, "total_movimientos": len(movimientos),
            "mensaje": "DEMO iniciada por WebSocket. Los movimientos no generan historial."}


# ================================================================ MOVIMIENTOS DEMO (4 SP)
@app.get("/vehiculos/{id_vehiculo}/demos/{id_demo}/movimientos", tags=["Movimientos DEMO"],
         summary="Listar movimientos de una DEMO", description="SP: `sp_obtener_movimientos_demo`.",
         responses=E404)
def listar_movimientos_demo(id_vehiculo: int, id_demo: int):
    _vehiculo_o_404(id_vehiculo)
    _demo_o_404(id_vehiculo, id_demo)
    return VehicleService.obtener_movimientos_demo(id_demo)


@app.post("/vehiculos/{id_vehiculo}/demos/{id_demo}/movimientos", status_code=201,
          tags=["Movimientos DEMO"], summary="Agregar movimiento a una DEMO",
          description="SP: `sp_agregar_movimiento_demo`. El comando debe pertenecer al vehículo.",
          dependencies=PROTEGIDO, responses={**E401, **E404, **E409})
def agregar_movimiento_demo(id_vehiculo: int, id_demo: int, datos: MovimientoDemoIn):
    _vehiculo_o_404(id_vehiculo)
    _demo_o_404(id_vehiculo, id_demo)
    _comando_o_404(id_vehiculo, datos.id_comando)
    _sin_demo_activa(id_vehiculo)
    VehicleService.agregar_movimiento_demo(id_demo, datos.id_comando, datos.orden, datos.duracion_ms, datos.pausa_ms)
    return {"status": "creado"}


@app.put("/vehiculos/{id_vehiculo}/demos/{id_demo}/movimientos/{id_demo_movimiento}",
         tags=["Movimientos DEMO"], summary="Actualizar movimiento de una DEMO",
         description="SP: `sp_actualizar_movimiento_demo`.",
         dependencies=PROTEGIDO, responses={**E401, **E404, **E409})
def actualizar_movimiento_demo(id_vehiculo: int, id_demo: int, id_demo_movimiento: int, datos: MovimientoDemoIn):
    _vehiculo_o_404(id_vehiculo)
    _demo_o_404(id_vehiculo, id_demo)
    _comando_o_404(id_vehiculo, datos.id_comando)
    _sin_demo_activa(id_vehiculo)
    VehicleService.actualizar_movimiento_demo(id_demo_movimiento, datos.id_comando, datos.orden,
                                              datos.duracion_ms, datos.pausa_ms)
    return {"status": "actualizado"}


@app.delete("/vehiculos/{id_vehiculo}/demos/{id_demo}/movimientos/{id_demo_movimiento}",
            tags=["Movimientos DEMO"], summary="Eliminar movimiento de una DEMO",
            description="SP: `sp_eliminar_movimiento_demo`.",
            dependencies=PROTEGIDO, responses={**E401, **E404, **E409})
def eliminar_movimiento_demo(id_vehiculo: int, id_demo: int, id_demo_movimiento: int):
    _vehiculo_o_404(id_vehiculo)
    _demo_o_404(id_vehiculo, id_demo)
    _sin_demo_activa(id_vehiculo)
    VehicleService.eliminar_movimiento_demo(id_demo_movimiento)
    return {"status": "eliminado"}


# ================================================================ WEBSOCKET
@app.websocket("/ws/vehiculos/{id_vehiculo}")
async def websocket_endpoint(websocket: WebSocket, id_vehiculo: int):
    """Canal de monitoreo (solo lectura). Al conectar entrega `ESTADO_INICIAL`."""
    try:
        vehiculo = await asyncio.to_thread(VehicleService.obtener_vehiculo, id_vehiculo)
    except Exception:
        log.exception("Error validando vehículo %s en WebSocket", id_vehiculo)
        await websocket.close(code=1011)
        return
    if not vehiculo:
        await websocket.close(code=1008)  # política: vehículo inexistente
        return

    await manager.connect(websocket, id_vehiculo)
    try:
        try:
            estado = await asyncio.to_thread(VehicleService.obtener_estado, id_vehiculo)
            await websocket.send_json({
                "tipo": "ESTADO_INICIAL",
                "id_vehiculo": id_vehiculo,
                "estado": jsonable_encoder(estado),
            })
        except WebSocketDisconnect:
            raise
        except Exception:
            log.exception("Error al enviar estado inicial del vehículo %s", id_vehiculo)
            await websocket.send_json({"tipo": "ERROR", "mensaje": "No se pudo obtener el estado del vehículo"})

        while True:
            await websocket.receive_text()  # mantiene la conexión abierta
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, id_vehiculo)

app.include_router(control_router)
app.mount("/control", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="control")
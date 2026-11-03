import asyncio
import json
import logging
import re
import secrets
import time
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.config import ALLOWED_ORIGINS, DEVICE_TOKEN
from app.security import api_key_valida, verificar_api_key
from app.services import VehicleService
from app.websockets import manager

log = logging.getLogger("iot.control")
router = APIRouter()

MOVIMIENTOS = {"AVANZAR", "RETROCEDER", "GIRAR_IZQUIERDA", "GIRAR_DERECHA",
               "IZQUIERDA", "DERECHA", "LEVANTAR", "BAJAR"}
PULSOS = {"ENCENDER_APAGAR", "LUCES", "SONIDO"}
# Nombres de comandos en la BD que equivalen a una acción física (ajusta según tu tabla de comandos).
ALIAS = {"ENCENDER": "ENCENDER_APAGAR", "APAGAR": "ENCENDER_APAGAR", "ENCENDIDO": "ENCENDER_APAGAR"}
TIMEOUT_CONTROLADOR = 2.0   # s sin HB del navegador -> detener todo
TIMEOUT_DISPOSITIVO = 4.0   # s sin HB del ESP -> se marca desconectado


def accion_de(nombre: str) -> str:
    n = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().upper()
    n = re.sub(r"[^A-Z0-9]+", "_", n).strip("_")
    return ALIAS.get(n, n)


class ControlHub:
    """Une navegador (un controlador por vehículo) y ESP8266 (conexión saliente persistente)."""

    def __init__(self):
        self.disp: dict[int, WebSocket] = {}
        self.ctrl: dict[int, WebSocket] = {}
        self.vd: dict[int, float] = {}   # último mensaje del dispositivo
        self.vc: dict[int, float] = {}   # último mensaje del controlador
        self.activas: dict[int, set] = {}
        self._bg: set = set()
        self._vig = None

    def iniciar(self):
        self._vig = asyncio.create_task(self._vigilar())

    async def detener(self):
        if self._vig:
            self._vig.cancel()

    def conectado(self, i: int) -> bool:
        return i in self.disp

    async def _enviar(self, i: int, msg: dict) -> bool:
        ws = self.disp.get(i)
        if not ws:
            return False
        try:
            await asyncio.wait_for(ws.send_json(msg), 1.0)
            return True
        except Exception:
            await self.quitar_dispositivo(i, ws)
            return False

    async def registrar_dispositivo(self, i: int, ws: WebSocket):
        viejo = self.disp.get(i)
        self.disp[i], self.vd[i] = ws, time.monotonic()
        if viejo:
            try:
                await viejo.close(code=1012)
            except Exception:
                pass
        await manager.broadcast_to_vehicle(i, {"tipo": "DISPOSITIVO_CONECTADO", "id_vehiculo": i})

    async def quitar_dispositivo(self, i: int, ws: WebSocket):
        if self.disp.get(i) is not ws:
            return
        del self.disp[i]
        self.activas.pop(i, None)
        try:
            await ws.close()
        except Exception:
            pass
        await manager.broadcast_to_vehicle(i, {"tipo": "DISPOSITIVO_DESCONECTADO", "id_vehiculo": i})

    async def mover(self, i: int, accion: str, activo: bool) -> bool:
        a = self.activas.setdefault(i, set())
        if activo == (accion in a):
            return True  # sin repetir órdenes
        ok = await self._enviar(i, {"tipo": "SET", "accion": accion, "activo": activo})
        if ok:
            (a.add if activo else a.discard)(accion)
            if activo:
                self.registrar(i, accion)
        return ok

    async def pulso(self, i: int, accion: str) -> bool:
        ok = await self._enviar(i, {"tipo": "PULSO", "accion": accion})
        if ok:
            self.registrar(i, accion)
        return ok

    async def detener_todo(self, i: int) -> bool:
        self.activas.pop(i, None)
        return await self._enviar(i, {"tipo": "STOP_ALL"})

    async def demo_paso(self, i: int, nombre_comando: str, activo: bool):
        """Usado por DemoRunner: mueve el carro físico si hay ESP conectado y el comando es conocido."""
        accion = accion_de(nombre_comando)
        if self.conectado(i) and accion in MOVIMIENTOS | PULSOS:
            await self._enviar(i, {"tipo": "SET", "accion": accion, "activo": activo})

    # --- registro en RDS: UNA vez por pulsación (no por repetición de teclado), sin bloquear el control
    def registrar(self, i: int, accion: str):
        t = asyncio.create_task(self._registrar(i, accion))
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _registrar(self, i: int, accion: str):
        try:
            cmds = await asyncio.to_thread(VehicleService.obtener_comandos_vehiculo, i)
            c = next((c for c in cmds if c["activo"] and accion_de(c["nombre"]) == accion), None)
            if not c:
                log.warning("Sin comando en BD para la acción %s (no se registra historial)", accion)
                return
            await asyncio.to_thread(VehicleService.registrar_movimiento, i, c["id_comando"], "WEB-TECLADO")
            estado = await asyncio.to_thread(VehicleService.obtener_estado, i)
            await manager.broadcast_to_vehicle(i, {"tipo": "MOVIMIENTO_INDIVIDUAL", "id_vehiculo": i,
                                                    "comando": c["nombre"], "origen": "WEB-TECLADO", "estado": estado})
        except Exception:
            log.exception("Error registrando %s", accion)

    async def _vigilar(self):
        ultimo_hb = 0.0
        while True:
            await asyncio.sleep(0.25)
            ahora = time.monotonic()
            for i, ws in list(self.disp.items()):
                if ahora - ultimo_hb >= 0.5:
                    await self._enviar(i, {"tipo": "HB"})
                if ahora - self.vd.get(i, ahora) > TIMEOUT_DISPOSITIVO:
                    await self.quitar_dispositivo(i, ws)
            if ahora - ultimo_hb >= 0.5:
                ultimo_hb = ahora
            for i, ws in list(self.ctrl.items()):
                if ahora - self.vc.get(i, ahora) > TIMEOUT_CONTROLADOR:
                    await self.detener_todo(i)
                    try:
                        await ws.close(code=1001)
                    except Exception:
                        pass


hub = ControlHub()


async def emergencia(i: int) -> dict:
    """Prioridad máxima: cancela la DEMO y apaga todas las salidas."""
    from app.demos import demo_runner
    cancelada = demo_runner.cancelar(i)
    parado = await hub.detener_todo(i)
    await manager.broadcast_to_vehicle(i, {"tipo": "EMERGENCIA", "id_vehiculo": i})
    return {"demo_cancelada": cancelada, "dispositivo_detenido": parado}


# ------------------------------------------------------------------ REST
@router.post("/vehiculos/{id_vehiculo}/emergencia", tags=["Control"], summary="Parada de emergencia",
             description="Cancela la DEMO y ordena detener todas las salidas del ESP8266.",
             dependencies=[Depends(verificar_api_key)])
async def parada_emergencia(id_vehiculo: int):
    return await emergencia(id_vehiculo)


@router.get("/vehiculos/{id_vehiculo}/dispositivo", tags=["Control"], summary="Estado del ESP8266")
def estado_dispositivo(id_vehiculo: int):
    return {"conectado": hub.conectado(id_vehiculo)}


# ------------------------------------------------------------------ WS navegador
@router.websocket("/ws/control/{id_vehiculo}")
async def ws_control(websocket: WebSocket, id_vehiculo: int):
    from app.demos import demo_runner
    origen = websocket.headers.get("origin")
    if origen and origen not in ALLOWED_ORIGINS:
        await websocket.close(code=1008)
        return
    if not await asyncio.to_thread(VehicleService.obtener_vehiculo, id_vehiculo):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:  # autenticación en el primer mensaje: la clave nunca viaja en la URL
        auth = json.loads(await asyncio.wait_for(websocket.receive_text(), 5))
        if auth.get("tipo") != "AUTH" or not api_key_valida(auth.get("clave")):
            raise PermissionError
    except Exception:
        await websocket.send_json({"tipo": "ERROR", "mensaje": "Autenticación inválida"})
        await websocket.close(code=1008)
        return
    if id_vehiculo in hub.ctrl:
        await websocket.send_json({"tipo": "ERROR", "mensaje": "Ya hay otro controlador conectado"})
        await websocket.close(code=1008)
        return
    hub.ctrl[id_vehiculo], hub.vc[id_vehiculo] = websocket, time.monotonic()
    await websocket.send_json({"tipo": "CONTROL_LISTO", "dispositivo": hub.conectado(id_vehiculo)})
    try:
        while True:
            try:
                m = json.loads(await websocket.receive_text())
            except ValueError:
                continue
            hub.vc[id_vehiculo] = time.monotonic()
            tipo, accion = m.get("tipo"), m.get("accion")
            if tipo == "EMERGENCIA":
                await emergencia(id_vehiculo)
            elif tipo == "DETENER_TODO":
                await hub.detener_todo(id_vehiculo)
            elif tipo in ("MOVER", "PULSO"):
                err = None
                if demo_runner.en_curso(id_vehiculo):
                    err = "Hay una DEMO en ejecución"
                elif not hub.conectado(id_vehiculo):
                    err = "El ESP8266 no está conectado"
                elif tipo == "MOVER" and accion in MOVIMIENTOS:
                    await hub.mover(id_vehiculo, accion, bool(m.get("activo")))
                elif tipo == "PULSO" and accion in PULSOS:
                    await hub.pulso(id_vehiculo, accion)
                else:
                    err = "Acción no válida"
                if err:
                    await websocket.send_json({"tipo": "ERROR", "mensaje": err})
    except WebSocketDisconnect:
        pass
    finally:  # al perder el canal se detienen todos los movimientos
        if hub.ctrl.get(id_vehiculo) is websocket:
            del hub.ctrl[id_vehiculo]
            await hub.detener_todo(id_vehiculo)


# ------------------------------------------------------------------ WS ESP8266
@router.websocket("/ws/dispositivo/{id_vehiculo}")
async def ws_dispositivo(websocket: WebSocket, id_vehiculo: int):
    token = websocket.headers.get("x-device-token") or ""
    if not DEVICE_TOKEN or not secrets.compare_digest(token.encode(), DEVICE_TOKEN.encode()):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    await hub.registrar_dispositivo(id_vehiculo, websocket)
    try:
        while True:
            try:
                m = json.loads(await websocket.receive_text())
            except ValueError:
                continue
            hub.vd[id_vehiculo] = time.monotonic()
            if m.get("tipo") == "ESTADO":
                await manager.broadcast_to_vehicle(id_vehiculo, {
                    "tipo": "ESTADO_DISPOSITIVO", "r1": m.get("r1"), "r2": m.get("r2")})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.quitar_dispositivo(id_vehiculo, websocket)

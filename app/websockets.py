import logging
from typing import Dict, List

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

log = logging.getLogger("iot.ws")


class ConnectionManager:
    """Agrupa las conexiones WebSocket por vehículo."""

    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, vehicle_id: int):
        await websocket.accept()
        self.active_connections.setdefault(vehicle_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, vehicle_id: int):
        conexiones = self.active_connections.get(vehicle_id)
        if conexiones is None:
            return
        if websocket in conexiones:
            conexiones.remove(websocket)
        if not conexiones:
            del self.active_connections[vehicle_id]

    async def broadcast_to_vehicle(self, vehicle_id: int, message: dict):
        # jsonable_encoder convierte datetime, Decimal, etc. a tipos serializables.
        payload = jsonable_encoder(message)
        # Se itera sobre una copia: una conexión caída no debe romper el envío a las demás.
        for connection in list(self.active_connections.get(vehicle_id, [])):
            try:
                await connection.send_json(payload)
            except Exception:
                log.warning("Conexión WebSocket caída en vehículo %s; se elimina", vehicle_id)
                self.disconnect(connection, vehicle_id)


manager = ConnectionManager()
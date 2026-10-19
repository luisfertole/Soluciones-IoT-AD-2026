import asyncio
import logging

from app.services import VehicleService
from app.websockets import manager

log = logging.getLogger("iot.demos")


class DemoRunner:
    """Controla las DEMO en ejecución: una sola por vehículo, con cancelación."""

    def __init__(self):
        # id_vehiculo -> Task. None = cupo reservado mientras se validan los datos.
        self._tareas: dict[int, asyncio.Task | None] = {}

    def reservar(self, id_vehiculo: int) -> bool:
        if id_vehiculo in self._tareas:
            return False
        self._tareas[id_vehiculo] = None
        return True

    def liberar(self, id_vehiculo: int) -> None:
        self._tareas.pop(id_vehiculo, None)

    def en_curso(self, id_vehiculo: int) -> bool:
        return id_vehiculo in self._tareas

    def iniciar(self, id_vehiculo: int, id_demo: int, movimientos: list) -> None:
        tarea = asyncio.create_task(self._ejecutar(id_vehiculo, id_demo, movimientos))
        tarea.add_done_callback(lambda _t: self.liberar(id_vehiculo))
        self._tareas[id_vehiculo] = tarea  # referencia fuerte para que no la recolecte el GC

    def cancelar(self, id_vehiculo: int) -> bool:
        tarea = self._tareas.get(id_vehiculo)
        if tarea is None:
            return False
        tarea.cancel()
        return True

    async def _ejecutar(self, id_vehiculo: int, id_demo: int, movimientos: list) -> None:
        total = len(movimientos)
        try:
            await manager.broadcast_to_vehicle(id_vehiculo, {
                "tipo": "DEMO_INICIADA",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "total_movimientos": total,
            })
            for mov in movimientos:
                # Actualiza el último estado. NO se registra historial (regla de negocio).
                await asyncio.to_thread(
                    VehicleService.actualizar_estado, id_vehiculo, mov["id_comando"], True
                )
                await manager.broadcast_to_vehicle(id_vehiculo, {
                    "tipo": "DEMO_MOVIMIENTO",
                    "id_vehiculo": id_vehiculo,
                    "id_demo": id_demo,
                    "orden": mov["orden"],
                    "total_movimientos": total,
                    "id_comando": mov["id_comando"],
                    "comando": mov["comando"],
                    "codigo": mov["codigo"],
                    "duracion_ms": mov["duracion_ms"],
                    "pausa_ms": mov["pausa_ms"],
                })
                await asyncio.sleep((mov["duracion_ms"] + mov["pausa_ms"]) / 1000.0)

            estado = await asyncio.to_thread(VehicleService.obtener_estado, id_vehiculo)
            await manager.broadcast_to_vehicle(id_vehiculo, {
                "tipo": "DEMO_FINALIZADA",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "estado": estado,
            })
        except asyncio.CancelledError:
            await manager.broadcast_to_vehicle(id_vehiculo, {
                "tipo": "DEMO_CANCELADA",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
            })
            raise
        except Exception:
            log.exception("Error ejecutando la DEMO %s del vehículo %s", id_demo, id_vehiculo)
            await manager.broadcast_to_vehicle(id_vehiculo, {
                "tipo": "DEMO_ERROR",
                "id_vehiculo": id_vehiculo,
                "id_demo": id_demo,
                "mensaje": "La DEMO se detuvo por un error interno",
            })


demo_runner = DemoRunner()
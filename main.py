from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
import asyncio
from app.services import VehicleService
from app.websockets import manager

app = FastAPI(title="API Vehículo IoT - Entorno Dev", version="1.0.0")

@app.get("/")
def read_root():
    return {"status": "API Local Operativa en puerto 8000"}

# --- GET: Vehículos, Comandos e Historial (Límite 10) ---
@app.get("/vehiculos/{id_vehiculo}/historial")
def obtener_historial_vehiculo(id_vehiculo: int):
    vehiculo = VehicleService.obtener_vehiculo(id_vehiculo)
    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    historial = VehicleService.obtener_historial(id_vehiculo)
    return {"id_vehiculo": id_vehiculo, "total_registros_devueltos": len(historial), "historial": historial}

# --- POST: Movimiento Individual (Actualiza Estado + Historial + WebSocket) ---
@app.post("/vehiculos/{id_vehiculo}/mover")
async def ejecutar_movimiento(id_vehiculo: int, id_comando: int):
    vehiculo = VehicleService.obtener_vehiculo(id_vehiculo)
    if not vehiculo or not vehiculo['activo']:
        raise HTTPException(status_code=400, detail="Vehículo inválido o inactivo")
    
    comandos = VehicleService.obtener_comandos_vehiculo(id_vehiculo)
    comando_valido = next((c for c in comandos if c['id_comando'] == id_comando and c['activo']), None)
    
    if not comando_valido:
        raise HTTPException(status_code=404, detail="Comando no existe o no pertenece a este vehículo")

    # Transacción: Actualizar estado y registrar en historial
    VehicleService.actualizar_estado(id_vehiculo, id_comando, conectado=True)
    VehicleService.registrar_historial(id_vehiculo, id_comando, origen="API-POST")

    # Notificar vía WebSocket a los clientes conectados
    evento = {
        "tipo": "MOVIMIENTO_INDIVIDUAL",
        "id_vehiculo": id_vehiculo,
        "comando": comando_valido['nombre'],
        "codigo": comando_valido['codigo']
    }
    await manager.broadcast_to_vehicle(id_vehiculo, evento)

    return {"status": "success", "mensaje": f"Comando {comando_valido['nombre']} ejecutado y registrado en historial."}

# --- POST: Ejecutar DEMO por WebSocket (SIN HISTORIAL) ---
@app.post("/demos/{id_demo}/ejecutar")
async def ejecutar_demo(id_demo: int):
    # Obtener la demo (asumimos que existe)
    movimientos = VehicleService.obtener_movimientos_demo(id_demo)
    if not movimientos:
        raise HTTPException(status_code=404, detail="Demo sin movimientos registrados")
    
    # Asumimos que todos los movimientos pertenecen al mismo vehículo
    # Buscamos el id_vehiculo a través del primer comando o tabla demos
    # Para simplificar, consultamos la demo:
    connection = VehicleService.ejecutar_sp('sp_obtener_demos', (1,), fetch=True) # Ajusta según tu lógica
    
    # Simular ejecución asíncrona de la DEMO con tiempos (duracion_ms y pausa_ms)
    asyncio.create_task(proceso_demo_websocket(movimientos))
    
    return {"status": "accepted", "mensaje": "Demostración iniciada por WebSocket. Los movimientos no generan historial."}

async def proceso_demo_websocket(movimientos):
    for mov in movimientos:
        id_vehiculo = 1 # ID de ejemplo, ajústalo dinámicamente según tu base de datos
        id_comando = mov['id_comando']
        
        # Actualizar estado actual del vehículo durante la demo
        VehicleService.actualizar_estado(id_vehiculo, id_comando, conectado=True)
        
        # IMPORTANTE: NO se llama a sp_registrar_historial (Regla de negocio cumplida)

        # Enviar evento por WebSocket
        evento = {
            "tipo": "DEMO_MOVIMIENTO",
            "orden": mov['orden'],
            "comando": mov['comando'],
            "codigo": mov['codigo'],
            "duracion_ms": mov['duracion_ms'],
            "pausa_ms": mov['pausa_ms']
        }
        await manager.broadcast_to_vehicle(id_vehiculo, evento)

        # Esperar la duración del movimiento y la pausa configurada
        total_espera = (mov['duracion_ms'] + mov['pausa_ms']) / 1000.0
        await asyncio.sleep(total_espera)

    # Notificar fin de demo
    await manager.broadcast_to_vehicle(1, {"tipo": "DEMO_FINALIZADA"})
# --- WEBSOCKET ENDPOINT ---
@app.websocket("/ws/vehiculos/{id_vehiculo}")
async def websocket_endpoint(websocket: WebSocket, id_vehiculo: int):
    await manager.connect(websocket, id_vehiculo)
    try:
        # Intentar enviar el estado inicial de forma segura
        try:
            estado_actual = VehicleService.obtener_estado(id_vehiculo)
            await websocket.send_json({"tipo": "ESTADO_INICIAL", "estado": estado_actual})
        except Exception as e:
            print(f"Error al enviar estado inicial: {e}")
            await websocket.send_json({"tipo": "ERROR", "mensaje": str(e)})
        
        while True:
            # Mantener la conexión abierta escuchando mensajes del cliente
            data = await websocket.receive_text()
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, id_vehiculo)
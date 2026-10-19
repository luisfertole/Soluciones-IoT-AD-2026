from app.database import DatabaseConnection

class VehicleService:

    @staticmethod
    def ejecutar_sp(sp_name, args=(), fetch=True, commit=False):
        connection = DatabaseConnection.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.callproc(sp_name, args)
                result = []
                if fetch:
                    result = cursor.fetchall()
                if commit:
                    connection.commit()
                return result
        finally:
            connection.close()

    # --- VEHÍCULOS (5 SP) ---
    @staticmethod
    def crear_vehiculo(nombre, modelo, descripcion, microcontrolador):
        return VehicleService.ejecutar_sp('sp_crear_vehiculo', (nombre, modelo, descripcion, microcontrolador), fetch=True, commit=True)

    @staticmethod
    def obtener_vehiculos():
        return VehicleService.ejecutar_sp('sp_obtener_vehiculos', fetch=True)

    @staticmethod
    def obtener_vehiculo(id_vehiculo):
        res = VehicleService.ejecutar_sp('sp_obtener_vehiculo', (id_vehiculo,), fetch=True)
        return res[0] if res else None

    @staticmethod
    def actualizar_vehiculo(id_vehiculo, nombre, modelo, descripcion, microcontrolador, activo):
        return VehicleService.ejecutar_sp('sp_actualizar_vehiculo', (id_vehiculo, nombre, modelo, descripcion, microcontrolador, activo), fetch=False, commit=True)

    @staticmethod
    def eliminar_vehiculo(id_vehiculo):
        return VehicleService.ejecutar_sp('sp_eliminar_vehiculo', (id_vehiculo,), fetch=False, commit=True)

    # --- COMANDOS (4 SP) ---
    @staticmethod
    def crear_comando(id_vehiculo, nombre, codigo, descripcion):
        return VehicleService.ejecutar_sp('sp_crear_comando', (id_vehiculo, nombre, codigo, descripcion), fetch=True, commit=True)

    @staticmethod
    def obtener_comandos_vehiculo(id_vehiculo):
        return VehicleService.ejecutar_sp('sp_obtener_comandos_vehiculo', (id_vehiculo,), fetch=True)

    @staticmethod
    def actualizar_comando(id_comando, nombre, codigo, descripcion, activo):
        return VehicleService.ejecutar_sp('sp_actualizar_comando', (id_comando, nombre, codigo, descripcion, activo), fetch=False, commit=True)

    @staticmethod
    def eliminar_comando(id_comando):
        return VehicleService.ejecutar_sp('sp_eliminar_comando', (id_comando,), fetch=False, commit=True)

    # --- ESTADO (2 SP) ---
    @staticmethod
    def actualizar_estado(id_vehiculo, id_comando, conectado):
        return VehicleService.ejecutar_sp('sp_actualizar_estado', (id_vehiculo, id_comando, conectado), fetch=False, commit=True)

    @staticmethod
    def obtener_estado(id_vehiculo):
        res = VehicleService.ejecutar_sp('sp_obtener_estado', (id_vehiculo,), fetch=True)
        return res[0] if res else None

    # --- HISTORIAL (2 SP) ---
    @staticmethod
    def registrar_historial(id_vehiculo, id_comando, origen):
        return VehicleService.ejecutar_sp('sp_registrar_historial', (id_vehiculo, id_comando, origen), fetch=False, commit=True)

    @staticmethod
    def obtener_historial(id_vehiculo):
        # sp_obtener_historial trae todo ordenado DESC, limitamos los 10 más recientes en código o SQL
        historial = VehicleService.ejecutar_sp('sp_obtener_historial', (id_vehiculo,), fetch=True)
        return historial[:10]  # REGLA: COMO MÁXIMO 10 movimientos recientes

    # --- DEMO (4 SP) ---
    @staticmethod
    def crear_demo(id_vehiculo, nombre, descripcion):
        return VehicleService.ejecutar_sp('sp_crear_demo', (id_vehiculo, nombre, descripcion), fetch=True, commit=True)

    @staticmethod
    def obtener_demos(id_vehiculo):
        return VehicleService.ejecutar_sp('sp_obtener_demos', (id_vehiculo,), fetch=True)

    @staticmethod
    def actualizar_demo(id_demo, nombre, descripcion, activo):
        return VehicleService.ejecutar_sp('sp_actualizar_demo', (id_demo, nombre, descripcion, activo), fetch=False, commit=True)

    @staticmethod
    def eliminar_demo(id_demo):
        return VehicleService.ejecutar_sp('sp_eliminar_demo', (id_demo,), fetch=False, commit=True)

    # --- MOVIMIENTOS DEMO (4 SP) ---
    @staticmethod
    def agregar_movimiento_demo(id_demo, id_comando, orden, duracion_ms, pausa_ms):
        return VehicleService.ejecutar_sp('sp_agregar_movimiento_demo', (id_demo, id_comando, orden, duracion_ms, pausa_ms), fetch=False, commit=True)

    @staticmethod
    def obtener_movimientos_demo(id_demo):
        return VehicleService.ejecutar_sp('sp_obtener_movimientos_demo', (id_demo,), fetch=True)

    @staticmethod
    def actualizar_movimiento_demo(id_demo_movimiento, id_comando, orden, duracion_ms, pausa_ms):
        return VehicleService.ejecutar_sp('sp_actualizar_movimiento_demo', (id_demo_movimiento, id_comando, orden, duracion_ms, pausa_ms), fetch=False, commit=True)

    @staticmethod
    def eliminar_movimiento_demo(id_demo_movimiento):
        return VehicleService.ejecutar_sp('sp_eliminar_movimiento_demo', (id_demo_movimiento,), fetch=False, commit=True)
    # Pega estos DOS métodos dentro de la clase VehicleService (app/services.py),
# justo debajo de ejecutar_sp. El resto del archivo NO cambia.

    @staticmethod
    def ejecutar_transaccion(operaciones):
        """Ejecuta varios SP en una sola conexión y un solo commit.
        Si alguno falla, se hace rollback y no queda nada a medias."""
        connection = DatabaseConnection.get_connection()
        try:
            with connection.cursor() as cursor:
                for sp_name, args in operaciones:
                    cursor.callproc(sp_name, args)
                    while cursor.nextset():  # descarta result sets pendientes
                        pass
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # --- MOVIMIENTO INDIVIDUAL: estado + historial en UNA transacción ---
    @staticmethod
    def registrar_movimiento(id_vehiculo, id_comando, origen):
        return VehicleService.ejecutar_transaccion([
            ('sp_actualizar_estado', (id_vehiculo, id_comando, True)),
            ('sp_registrar_historial', (id_vehiculo, id_comando, origen)),
        ])
// ESP8266 + 2x 74HC595 (DS independientes, reloj y latch compartidos).
// Librerías: "WebSockets" (Markus Sattler) y "ArduinoJson" 7.x
// Crea secrets.h a partir de secrets.h.example (NO lo subas a GitHub).
#include <ESP8266WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include "secrets.h"   // WIFI_SSID, WIFI_PASS, DEVICE_TOKEN

const char*    HOST = "iot-vehiculos.duckdns.org";
const char*    RUTA = "/ws/dispositivo/1";           // id del vehículo
const uint8_t  PIN_DS1 = 14, PIN_DS2 = 2, PIN_SHCP = 13, PIN_STCP = 12;  // D5, D4, D7, D6
const bool     SALIDA_ACTIVA_ALTA = true;            // invertir si tus optoacopladores son activos en bajo
const uint32_t SEGURIDAD_MS = 2000;                  // sin mensajes del servidor -> salidas apagadas
// Duraciones de pulso: AJÚSTALAS a las de tu firmware actual.
const uint16_t PULSO_CORTO_MS = 180, PULSO_LARGO_MS = 1500, PULSO_LUCES_MS = 180, PULSO_SONIDO_MS = 180;

struct Accion { const char* nombre; uint8_t reg; uint8_t bit; };
const Accion ACCIONES[] = {
  {"AVANZAR", 1, 3}, {"RETROCEDER", 1, 2}, {"GIRAR_IZQUIERDA", 1, 1}, {"GIRAR_DERECHA", 1, 4},
  {"IZQUIERDA", 2, 0}, {"DERECHA", 2, 3}, {"LEVANTAR", 2, 1}, {"BAJAR", 2, 2},
  {"ENCENDER_APAGAR", 1, 0}, {"LUCES", 1, 7}, {"SONIDO", 1, 5}, {"DEMO", 1, 6}};

WebSocketsClient ws;
uint8_t mask[3] = {0, 0, 0};                 // mask[1] = R1, mask[2] = R2
uint32_t finPulso[3][8];
bool pulsoActivo[3][8];
uint32_t ultimoMsg = 0, ultimoHb = 0;
bool conectado = false, largo = false;

void escribir() {
  for (int8_t i = 7; i >= 0; i--) {          // primer bit enviado = Q7
    bool b1 = (mask[1] >> i) & 1, b2 = (mask[2] >> i) & 1;
    digitalWrite(PIN_DS1, b1 == SALIDA_ACTIVA_ALTA);
    digitalWrite(PIN_DS2, b2 == SALIDA_ACTIVA_ALTA);
    digitalWrite(PIN_SHCP, HIGH); delayMicroseconds(2); digitalWrite(PIN_SHCP, LOW);
  }
  digitalWrite(PIN_STCP, HIGH); delayMicroseconds(2); digitalWrite(PIN_STCP, LOW);
}

void enviarEstado() {
  if (!conectado) return;
  JsonDocument d; d["tipo"] = "ESTADO"; d["r1"] = mask[1]; d["r2"] = mask[2];
  String s; serializeJson(d, s); ws.sendTXT(s);
}

void apagarTodo() {
  mask[1] = mask[2] = 0;
  memset(pulsoActivo, 0, sizeof(pulsoActivo));
  escribir(); enviarEstado();
}

const Accion* buscar(const char* n) {
  for (auto& a : ACCIONES) if (!strcmp(a.nombre, n)) return &a;
  return nullptr;
}

void pulso(const Accion* a, uint16_t ms) {
  if (pulsoActivo[a->reg][a->bit]) return;   // sin reintentos mientras dura el pulso
  mask[a->reg] |= (1 << a->bit);
  pulsoActivo[a->reg][a->bit] = true;
  finPulso[a->reg][a->bit] = millis() + ms;
  escribir(); enviarEstado();
}

void onMensaje(uint8_t* p, size_t len) {
  JsonDocument d;
  if (deserializeJson(d, p, len)) return;
  const char* tipo = d["tipo"] | "";
  if (!strcmp(tipo, "STOP_ALL")) { apagarTodo(); return; }
  const Accion* a = buscar(d["accion"] | "");
  if (!a) return;
  if (!strcmp(tipo, "SET")) {                // mantener presionado: activa / desactiva solo su bit
    if (d["activo"].as<bool>()) mask[a->reg] |= (1 << a->bit);
    else mask[a->reg] &= ~(1 << a->bit);
    escribir(); enviarEstado();
  } else if (!strcmp(tipo, "PULSO")) {
    uint16_t ms = PULSO_LUCES_MS;
    if (!strcmp(a->nombre, "SONIDO")) ms = PULSO_SONIDO_MS;
    else if (!strcmp(a->nombre, "ENCENDER_APAGAR")) { ms = largo ? PULSO_LARGO_MS : PULSO_CORTO_MS; largo = !largo; }
    pulso(a, ms);
  }
}

void onEvento(WStype_t t, uint8_t* p, size_t len) {
  if (t == WStype_CONNECTED) {
    conectado = true; ultimoMsg = millis(); apagarTodo();   // al reconectar NO se restaura nada
    ws.sendTXT("{\"tipo\":\"HELLO\"}");
  } else if (t == WStype_DISCONNECTED) {
    conectado = false; apagarTodo();
  } else if (t == WStype_TEXT) {
    ultimoMsg = millis(); onMensaje(p, len);
  }
}

void setup() {
  pinMode(PIN_DS1, OUTPUT); pinMode(PIN_DS2, OUTPUT); pinMode(PIN_SHCP, OUTPUT); pinMode(PIN_STCP, OUTPUT);
  apagarTodo();
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(250);
  String h = String("X-Device-Token: ") + DEVICE_TOKEN;
  ws.setExtraHeaders(h.c_str());
  // TLS sin validar certificado (simple). En producción fija la CA con beginSslWithCA().
  ws.beginSSL(HOST, 443, RUTA);
  ws.onEvent(onEvento);
  ws.setReconnectInterval(2000);
  ws.enableHeartbeat(15000, 3000, 2);
}

void loop() {
  ws.loop();
  uint32_t ahora = millis();
  for (uint8_t r = 1; r <= 2; r++)           // fin de pulsos con millis(), sin delay()
    for (uint8_t b = 0; b < 8; b++)
      if (pulsoActivo[r][b] && (int32_t)(ahora - finPulso[r][b]) >= 0) {
        pulsoActivo[r][b] = false; mask[r] &= ~(1 << b); escribir(); enviarEstado();
      }
  if ((mask[1] | mask[2]) && (!conectado || ahora - ultimoMsg > SEGURIDAD_MS)) apagarTodo();   // estado seguro
  if (conectado && ahora - ultimoHb >= 1000) { ultimoHb = ahora; ws.sendTXT("{\"tipo\":\"HB\"}"); }
  if (WiFi.status() != WL_CONNECTED && (mask[1] | mask[2])) apagarTodo();
}

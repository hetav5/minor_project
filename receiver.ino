#include <SPI.h>
#include <LoRa.h>
#include <math.h>

#define SS    4   // D2
#define RST   5   // D1
#define DIO0  15  // D8

#define GRAVITY_LSB       16384.0
#define FREE_FALL_G       0.4
#define IMPACT_G          2.0
#define FALL_WINDOW_MS    1500

#define RISK_CRITICAL_DG  1.5
#define RISK_HIGH_DG      0.8
#define RISK_MEDIUM_DG    0.3

bool          fallPhase     = false;
unsigned long fallPhaseTime = 0;
float         prevTotalG    = -1.0;

// ── Helper — must be defined before calculateRisk ─────────────────────────────
String getValue(String data, String key) {
  int start = data.indexOf(key + ":");
  if (start == -1) return "0";
  start += key.length() + 1;
  int end = data.indexOf(",", start);
  if (end == -1) end = data.length();
  return data.substring(start, end);
}

// ── Risk + fall detection ─────────────────────────────────────────────────────
String calculateRisk(float temp, float pressure, int ax, int ay, int az) {
  float totalG = sqrt((float)ax*ax + (float)ay*ay + (float)az*az) / GRAVITY_LSB;
  unsigned long now = millis();

  // Fall detection
  bool fallDetected = false;
  if (!fallPhase) {
    if (totalG < FREE_FALL_G) {
      fallPhase     = true;
      fallPhaseTime = now;
    }
  } else {
    if ((now - fallPhaseTime) > FALL_WINDOW_MS) {
      fallPhase = false;
    } else if (totalG > IMPACT_G) {
      fallPhase     = false;
      fallDetected  = true;
    }
  }

  // Delta-g
  float deltaG = 0.0;
  if (prevTotalG >= 0.0) {
    deltaG = abs(totalG - prevTotalG);
  }
  prevTotalG = totalG;

  // Risk levels
  if (fallDetected)                                               return "CRITICAL";
  if (temp > 45 || pressure < 960  || pressure > 1045 || deltaG > RISK_CRITICAL_DG) return "CRITICAL";
  if (temp > 40 || pressure < 980  || pressure > 1035 || deltaG > RISK_HIGH_DG)     return "HIGH";
  if (temp > 35 || deltaG > RISK_MEDIUM_DG)                      return "MEDIUM";
  return "LOW";
}

void setup() {
  Serial.begin(74880);
  delay(1000);
  Serial.println("HELLO - ESP8266 IS ALIVE");

  pinMode(RST, OUTPUT);
  digitalWrite(RST, LOW);
  delay(10);
  digitalWrite(RST, HIGH);
  delay(2000);

  Serial.println("ESP8266 LoRa Receiver Starting...");
  LoRa.setPins(SS, RST, DIO0);

  if (!LoRa.begin(433E6)) {
    Serial.println("LoRa INIT FAILED");
    while (1);
  }

  Serial.println("LoRa Initialized OK");
  Serial.println("Waiting for packets...");
}

void loop() {
  int packetSize = LoRa.parsePacket();

  if (packetSize) {
    String data = "";
    while (LoRa.available()) {
      data += (char)LoRa.read();
    }

    float temp     = getValue(data, "T").toFloat();
    float pressure = getValue(data, "P").toFloat();
    int   ax       = getValue(data, "AX").toInt();
    int   ay       = getValue(data, "AY").toInt();
    int   az       = getValue(data, "AZ").toInt();
    float lat      = getValue(data, "LAT").toFloat();
    float lon      = getValue(data, "LON").toFloat();

    String risk = calculateRisk(temp, pressure, ax, ay, az);

    Serial.print("RAW:");
    Serial.println(data);
    Serial.print("RISK:");
    Serial.println(risk);
    Serial.print("RSSI:");
    Serial.println(LoRa.packetRssi());
    Serial.println("---");
  }
}
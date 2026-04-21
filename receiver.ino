#include <SPI.h>
#include <LoRa.h>
#include <math.h>

#define SS    4   // D2
#define RST   5   // D1
#define DIO0  15  // D8

// ── Fall detection ────────────────────────────────────────────────────────────
#define GRAVITY_LSB    16384.0
#define FREE_FALL_G    0.4
#define IMPACT_G       2.0
#define FALL_WINDOW_MS 1500

// ── Normalization bounds ──────────────────────────────────────────────────────
// Temperature: safe range 10–40°C, danger above 45°C
#define T_MIN   10.0
#define T_MAX   45.0

// Pressure: safe range 980–1040 hPa, danger outside
#define P_MIN   960.0
#define P_MAX   1050.0
#define P_SAFE_LOW  980.0
#define P_SAFE_HIGH 1040.0

// Delta-g: 0 = still, 3.0g = extreme
#define DG_MAX  3.0

// WRS weights — must sum to 1.0
#define W_TEMP  0.25
#define W_PRES  0.20
#define W_ACCEL 0.30
#define W_FALL  0.25

// ── State ─────────────────────────────────────────────────────────────────────
bool          fallPhase     = false;
unsigned long fallPhaseTime = 0;
float         prevTotalG    = -1.0;

// ─────────────────────────────────────────────────────────────────────────────
String getValue(String data, String key) {
  int start = data.indexOf(key + ":");
  if (start == -1) return "0";
  start += key.length() + 1;
  int end = data.indexOf(",", start);
  if (end == -1) end = data.length();
  return data.substring(start, end);
}

// ── Normalize a value to 0–1 within min/max ───────────────────────────────────
float normalize(float val, float minVal, float maxVal) {
  float n = (val - minVal) / (maxVal - minVal);
  if (n < 0.0) n = 0.0;
  if (n > 1.0) n = 1.0;
  return n;
}

// ── Temperature risk — increases as temp rises toward danger ──────────────────
float tempRisk(float temp) {
  return normalize(temp, T_MIN, T_MAX);
}

// ── Pressure risk — highest at extremes, lowest in safe mid-range ─────────────
float pressureRisk(float pressure) {
  if (pressure >= P_SAFE_LOW && pressure <= P_SAFE_HIGH) {
    // Inside safe band — map to 0.0–0.2 (low background risk)
    float mid = (P_SAFE_LOW + P_SAFE_HIGH) / 2.0;
    return 0.2 * (abs(pressure - mid) / ((P_SAFE_HIGH - P_SAFE_LOW) / 2.0));
  }
  // Outside safe band — map to 0.2–1.0
  if (pressure < P_SAFE_LOW) {
    return 0.2 + 0.8 * normalize(P_SAFE_LOW - pressure, 0, P_SAFE_LOW - P_MIN);
  } else {
    return 0.2 + 0.8 * normalize(pressure - P_SAFE_HIGH, 0, P_MAX - P_SAFE_HIGH);
  }
}

// ── Accel risk — based on delta-g (change between readings) ───────────────────
float accelRisk(float deltaG) {
  return normalize(deltaG, 0.0, DG_MAX);
}

// ── Fall + WRS ────────────────────────────────────────────────────────────────
String calculateWRS(float temp, float pressure, int ax, int ay, int az) {
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
      fallPhase    = false;
      fallDetected = true;
    }
  }

  // Delta-g
  float deltaG = 0.0;
  if (prevTotalG >= 0.0) {
    deltaG = abs(totalG - prevTotalG);
  }
  prevTotalG = totalG;

  // Normalize each parameter to 0–1
  float tNorm = tempRisk(temp);
  float pNorm = pressureRisk(pressure);
  float aNorm = accelRisk(deltaG);
  float fNorm = fallDetected ? 1.0 : 0.0;

  // Weighted sum → WRS (0.0 to 1.0)
  float wrs = (W_TEMP  * tNorm) +
              (W_PRES  * pNorm) +
              (W_ACCEL * aNorm) +
              (W_FALL  * fNorm);

  // Map WRS score to risk tier
  // CRITICAL if fall detected regardless of score
  if (fallDetected || wrs >= 0.75) return "CRITICAL";
  if (wrs >= 0.50)                 return "HIGH";
  if (wrs >= 0.25)                 return "MEDIUM";
  return "LOW";
}

// ─────────────────────────────────────────────────────────────────────────────
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

// ─────────────────────────────────────────────────────────────────────────────
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
    String fix     = getValue(data, "FIX");

    String risk = calculateWRS(temp, pressure, ax, ay, az);

    Serial.print("RAW:");   Serial.println(data);
    Serial.print("RISK:");  Serial.println(risk);
    Serial.print("FIX:");   Serial.println(fix);
    Serial.print("RSSI:");  Serial.println(LoRa.packetRssi());
    Serial.println("---");
  }
}
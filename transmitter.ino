#include <Wire.h>
#include <LoRa.h>
#include <Adafruit_BMP280.h>
#include <MPU6050.h>
#include <TinyGPSPlus.h>

// ── LoRa pins ─────────────────────────────────────────────────────────────────
#define SS   5
#define RST  14
#define DIO0 2

// ── GPS UART2 pins ────────────────────────────────────────────────────────────
#define GPS_RX_PIN 16
#define GPS_TX_PIN 17
#define GPS_BAUD   9600

// ── Objects ───────────────────────────────────────────────────────────────────
Adafruit_BMP280 bmp;
MPU6050 mpu;
TinyGPSPlus gps;
HardwareSerial gpsSerial(2);

// ── Fallback coords — held until NEO-6M gets a fix ───────────────────────────
float lastLat = 12.9716;
float lastLon = 77.5946;
bool  gpsFix  = false;

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(21, 22);

  // BMP280
  if (!bmp.begin(0x76)) {
    if (!bmp.begin(0x77)) {
      Serial.println("BMP280 FAIL");
      while (1);
    }
  }
  Serial.println("BMP280 OK");

  // MPU6050 — testConnection() skipped (known false-negative with this library)
  mpu.initialize();
  mpu.setSleepEnabled(false);
  Serial.println("MPU6050 OK");

  // GPS — initialized last, after other sensors are stable
  delay(500);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  Serial.println("GPS serial OK");

  // LoRa
  LoRa.setPins(SS, RST, DIO0);
  if (!LoRa.begin(433E6)) {
    Serial.println("LoRa FAIL");
    while (1);
  }
  Serial.println("LoRa OK");

  Serial.println("=============================");
  Serial.println("  ESP32 Transmitter Ready");
  Serial.println("  Waiting for GPS fix...");
  Serial.println("=============================");
}

// ─────────────────────────────────────────────────────────────────────────────
void loop() {

  // Feed every available GPS byte into TinyGPSPlus parser
  while (gpsSerial.available()) {
    gps.encode(gpsSerial.read());
  }

  // Update coords only when fix is valid and fresh (under 2 seconds old)
  if (gps.location.isValid() && gps.location.age() < 2000) {
    lastLat = gps.location.lat();
    lastLon = gps.location.lng();
    gpsFix  = true;
  } else {
    gpsFix = false;
  }

  // Read BMP280
  float temp     = bmp.readTemperature();
  float pressure = bmp.readPressure() / 100.0;

  // Read MPU6050
  int16_t ax, ay, az;
  mpu.getAcceleration(&ax, &ay, &az);

  // Build payload
  // FIX:1 = real GPS lock   FIX:0 = fallback / last known coords
  String data = "T:"    + String(temp, 1)      +
                ",P:"   + String(pressure, 1)   +
                ",AX:"  + String(ax)             +
                ",AY:"  + String(ay)             +
                ",AZ:"  + String(az)             +
                ",LAT:" + String(lastLat, 6)     +
                ",LON:" + String(lastLon, 6)     +
                ",FIX:" + String(gpsFix ? 1 : 0);

  // Debug output
  Serial.println("TX: " + data);
  Serial.print("GPS: ");
  Serial.print(gpsFix ? "FIX" : "NO FIX");
  Serial.print("  Satellites: ");
  Serial.println(gps.satellites.isValid() ? (int)gps.satellites.value() : 0);
  Serial.println("-----------------------------");

  // Transmit via LoRa
  LoRa.beginPacket();
  LoRa.print(data);
  LoRa.endPacket();

  delay(3000);
}
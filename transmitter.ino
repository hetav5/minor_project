#include <Wire.h>
#include <LoRa.h>
#include <Adafruit_BMP280.h>
#include <MPU6050.h>

#define SS   5
#define RST  14
#define DIO0 2

Adafruit_BMP280 bmp;
MPU6050 mpu;

float lat = 12.9716;
float lon = 77.5946;

void setup() {
  Serial.begin(115200);

  Wire.begin(21, 22);

  // BMP280
  if (!bmp.begin(0x76)) {
    if (!bmp.begin(0x77)) {
      Serial.println("BMP FAIL");
      while (1);
    }
  }

  // MPU
  mpu.initialize();

  // LoRa
  LoRa.setPins(SS, RST, DIO0);
  if (!LoRa.begin(433E6)) {
    Serial.println("LoRa FAIL");
    while (1);
  }

  Serial.println("🚀 ESP32 Transmitter Ready");
}

void loop() {
  float temp = bmp.readTemperature();
  float pressure = bmp.readPressure() / 100.0;

  int16_t ax, ay, az;
  mpu.getAcceleration(&ax, &ay, &az);

  lat += random(-5,6) * 0.00001;
  lon += random(-5,6) * 0.00001;

  String data = "T:" + String(temp,1) +
                ",P:" + String(pressure,1) +
                ",AX:" + String(ax) +
                ",AY:" + String(ay) +
                ",AZ:" + String(az) +
                ",LAT:" + String(lat,6) +
                ",LON:" + String(lon,6);

  Serial.println("📤 " + data);

  LoRa.beginPacket();
  LoRa.print(data);
  LoRa.endPacket();

  delay(3000);
}
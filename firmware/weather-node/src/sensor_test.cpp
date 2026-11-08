// Sensor check: prints DHT readings every 2 s. No Wi-Fi, no MQTT.
#include <Arduino.h>
#include <DHT.h>

DHT dht(D3, DHT11);  // same pin and type as main.cpp

void setup() {
  Serial.begin(115200);
  dht.begin();
}

void loop() {
  delay(2000);  // DHT needs ~2 s between reads
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {
    Serial.println("FAIL: no reading. Check SIG on D3, VCC on 3V3, GND, and DHT11 vs DHT22");
    return;
  }
  bool sane = t > -10 && t < 60 && h > 0 && h <= 100;
  Serial.printf("%s temp: %.1f C  humidity: %.1f %%\n", sane ? "OK  " : "ODD ", t, h);
}

// Compile-only check: pulls in every library from platformio.ini so we know they
// all build for the ESP32-C5 BEFORE any hardware is attached.
// It never needs to be flashed. If `pio run -e lib_check` says SUCCESS, the
// toolchain and all libraries are good.
#include <Arduino.h>
#include <WiFi.h>              // built into the ESP32 core (Task D)
#include <DHT.h>               // adafruit/DHT sensor library
#include <DHTesp.h>            // beegee-tokyo/DHT sensor library for ESPx
#include <PubSubClient.h>      // knolleary/PubSubClient (MQTT)
#include <ArduinoJson.h>       // bblanchon/ArduinoJson

// Create one of each object so the library code really gets compiled and linked.
DHT          dhtAdafruit(1, DHT11);
DHTesp       dhtEsp;
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

void setup() {
  Serial.begin(115200);
  dhtAdafruit.begin();
  dhtEsp.setup(1, DHTesp::DHT11);
  mqtt.setServer("10.42.0.1", 1883);

  JsonDocument doc;
  doc["type"] = "wx";
  doc["node"] = "N1";
  serializeJson(doc, Serial);
}

void loop() {}

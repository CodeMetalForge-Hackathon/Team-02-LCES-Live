// Node N3 (XIAO ESP32-C5): reads the Grove DHT11 and sends a heartbeat to MQTT
// sensors/N3/wx every 10 s over FireNet.
// Format: docs/LCES_Design.md §1.2, same as N2 (firmware/weather-node).
// -1 / -99 = no data. This node has no wind, flame or battery sensor yet.
//
// The sensor is read and printed over USB every 3 s even when Wi-Fi/MQTT is down,
// so you can test the sensor on its own with:  pio run -e node_n3 -t upload -t monitor
// No FireNet? Test the MQTT side over USB with tools/serial_bridge.py (see that file).
//
// Wiring (USB-C at top, XIAO straddling the breadboard gap):
//   Grove blue LED  : SIG -> D1 (GPIO0), VCC -> 3V3, GND -> GND   (status light)
//   Grove DHT11     : SIG -> D0 (GPIO1), VCC -> 3V3, GND -> GND
//
// Status LED:
//   blinking once per second  = connecting to Wi-Fi or MQTT
//   short flash every 10 s    = heartbeat delivered to the Pi
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "dht11.h"

// ---- edit these ----
const char* WIFI_SSID  = "FireNet";
const char* WIFI_PASS  = "firenet123";
const char* MQTT_HOST  = "10.42.1.1";   // Pi's IP on the FireNet hotspot
const char* NODE_ID    = "N3";
const uint32_t PUBLISH_MS = 10000;      // Pi greys a node after 3 missed heartbeats (~30 s)
const uint32_t SENSOR_MS  = 3000;       // DHT11 needs >= 1 s between reads
const uint32_t STALE_MS   = 15000;      // older readings are sent as "no data"
// --------------------

const int LED_PIN = D1;   // GPIO0
const int DHT_PIN = D0;   // GPIO1

Dht11 dht(DHT_PIN);
WiFiClient net;
PubSubClient mqtt(net);
char topic[32];
uint32_t seq = 0;

// Latest good sensor reading
float lastT = -99, lastH = -1;
uint32_t lastGoodMs = 0;
bool haveReading = false;

void toggleLed() { digitalWrite(LED_PIN, !digitalRead(LED_PIN)); }

void readSensor() {
  Dht11Reading r = dht.read();
  if (r.ok) {
    lastT = r.tempC;
    lastH = r.humidity;
    lastGoodMs = millis();
    haveReading = true;
    Serial.printf("dht: temp %.1f C  humidity %.1f %%\n", r.tempC, r.humidity);
  } else {
    Serial.printf("dht: read failed: %s  (raw %02X %02X %02X %02X %02X)\n", r.error,
                  r.raw[0], r.raw[1], r.raw[2], r.raw[3], r.raw[4]);
  }
}

void setup() {
  Serial.begin(115200);
  unsigned long start = millis();
  while (!Serial && millis() - start < 3000) delay(10);   // let the USB monitor connect

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  dht.begin();
  snprintf(topic, sizeof topic, "sensors/%s/wx", NODE_ID);
  Serial.printf("\n=== Node %s (XIAO ESP32-C5) -> %s @ %s ===\n", NODE_ID, topic, MQTT_HOST);

  delay(1500);   // DHT11 wants ~1 s after power-up before the first read
  readSensor();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);   // the ESP32 reconnects Wi-Fi on its own after this
  mqtt.setServer(MQTT_HOST, 1883);
  mqtt.setSocketTimeout(2);           // don't stall the sensor loop for long if the Pi is away
}

// Returns true when MQTT is connected. Never blocks for more than ~2 s.
bool networkReady() {
  static uint32_t lastTry = 0;
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastTry > 1000) {
      lastTry = millis();
      // status 1 = SSID not seen, 4 = connect failed (password?), 6 = disconnected/retrying
      static uint8_t n = 0;
      if (n++ % 5 == 0) Serial.printf("wifi: connecting to %s... (status %d)\n", WIFI_SSID, WiFi.status());
      toggleLed();
    }
    return false;
  }
  if (!mqtt.connected()) {
    if (millis() - lastTry < 3000) return false;
    lastTry = millis();
    Serial.printf("mqtt: connecting to %s:1883 (my ip %s, rssi %d dBm)...\n",
                  MQTT_HOST, WiFi.localIP().toString().c_str(), WiFi.RSSI());
    if (!mqtt.connect(NODE_ID)) {
      Serial.printf("mqtt: failed, state %d (-2 = broker unreachable)\n", mqtt.state());
      toggleLed();
      return false;
    }
    Serial.println("mqtt: connected");
    digitalWrite(LED_PIN, LOW);
  }
  mqtt.loop();
  return true;
}

// Builds the heartbeat and sends it over MQTT if we're online. It is ALWAYS also
// printed as a "payload <topic> <json>" line, so tools/serial_bridge.py can
// forward it to a broker over USB when there's no Wi-Fi (local testing).
void publishHeartbeat(bool online) {
  bool fresh = haveReading && millis() - lastGoodMs < STALE_MS;
  float t = fresh ? lastT : -99;   // "no data" values from the agreed format
  float h = fresh ? lastH : -1;

  // No clock on the node: the Pi stamps the receive time.
  char msg[192];
  snprintf(msg, sizeof msg,
           "{\"type\":\"wx\",\"node\":\"%s\",\"seq\":%lu,\"temp_c\":%.1f,\"rh\":%.1f,"
           "\"wind_ms\":-1,\"wind_dir\":-1,\"flame\":-1,\"batt\":-1}",
           NODE_ID, (unsigned long)seq, t, h);
  seq++;   // counts every heartbeat, so gaps on the Pi = missed heartbeats
  Serial.printf("payload %s %s\n", topic, msg);
  if (!online) {
    Serial.println("  (offline: not sent over Wi-Fi)");
    return;
  }
  bool ok = mqtt.publish(topic, msg);
  Serial.printf("  %s over Wi-Fi\n", ok ? "sent" : "FAILED to send");
  if (ok) {
    digitalWrite(LED_PIN, HIGH);   // short flash = delivered
    delay(150);
    digitalWrite(LED_PIN, LOW);
  }
}

void loop() {
  static uint32_t lastSensor = millis();
  static uint32_t lastPublish = 0;
  static bool wasOnline = false;

  // 1. Sensor: always, network or not
  if (millis() - lastSensor >= SENSOR_MS) {
    lastSensor = millis();
    readSensor();
  }

  // 2. Heartbeat every PUBLISH_MS, online or not (plus one right after connecting)
  bool online = networkReady();
  bool due = millis() - lastPublish >= PUBLISH_MS;
  if (online && !wasOnline) due = true;
  wasOnline = online;
  if (due) {
    lastPublish = millis();
    publishHeartbeat(online);
  }

  delay(10);
}

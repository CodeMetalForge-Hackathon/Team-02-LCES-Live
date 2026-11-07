// LCES weather node: reads temp/RH and publishes to the Pi over MQTT.
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ---- edit these ----
const char* WIFI_SSID  = "lces";
const char* WIFI_PASS  = "lces1234";
const char* MQTT_HOST  = "10.42.0.1";  // Pi's IP; 10.42.0.1 when the Pi runs the hotspot
const char* NODE_ID    = "N1";
const uint32_t PUBLISH_MS = 10000;     // Pi greys a node after 30 s = 3 missed publishes
// --------------------

const int LED = D4;
DHT dht(D3, DHT11);  // blue sensor = DHT11, white = DHT22
WiFiClient net;
PubSubClient mqtt(net);
char topic[48];
uint32_t lastPublish = 0;

void setup() {
  Serial.begin(115200);
  pinMode(LED, OUTPUT);
  dht.begin();
  snprintf(topic, sizeof topic, "lces/node/%s/reading", NODE_ID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);  // ESP32 reconnects Wi-Fi on its own after this
  mqtt.setServer(MQTT_HOST, 1883);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("wifi: connecting to %s...\n", WIFI_SSID);
    delay(1000);
    return;
  }
  if (!mqtt.connected()) {
    Serial.printf("mqtt: connecting to %s (my ip %s)...\n", MQTT_HOST, WiFi.localIP().toString().c_str());
    if (!mqtt.connect(NODE_ID)) { delay(2000); return; }
    Serial.println("mqtt: connected");
  }
  mqtt.loop();

  if (millis() - lastPublish < PUBLISH_MS) return;
  lastPublish = millis();

  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {  // skip; the Pi will see the node go stale
    Serial.println("sensor read failed");
    return;
  }
  // no clock on the node: the Pi stamps "ts" when it receives the message
  char msg[128];
  snprintf(msg, sizeof msg, "{\"node_id\":\"%s\",\"temp_c\":%.1f,\"rh_pct\":%.1f}", NODE_ID, t, h);
  bool ok = mqtt.publish(topic, msg);
  Serial.printf("%s %s -> %s\n", ok ? "sent" : "FAILED", topic, msg);
  if (ok) digitalWrite(LED, !digitalRead(LED));  // LED toggles on each successful send
}

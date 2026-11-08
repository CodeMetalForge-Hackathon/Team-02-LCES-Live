// Weather node (XIAO ESP32-S3 + DHT11): publishes a heartbeat to sensors/<node>/wx every 10 s.
// Format: docs/LCES_Design.md §1.2. -1 / -99 = no data (this node has no wind, flame or battery sensor).
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ---- edit these ----
const char* WIFI_SSID  = "FireNet";
const char* WIFI_PASS  = "firenet123";
const char* MQTT_HOST  = "10.42.1.1";  // Pi's IP on the FireNet hotspot
const char* NODE_ID    = "N2";
const uint32_t PUBLISH_MS = 10000;     // Pi greys a node after 30 s = 3 missed publishes
// --------------------

const int LED = D4;
DHT dht(D3, DHT11);  // blue sensor = DHT11, white = DHT22
WiFiClient net;
PubSubClient mqtt(net);
char topic[48];
uint32_t lastPublish = 0;
uint32_t seq = 0;

void setup() {
  Serial.begin(115200);
  pinMode(LED, OUTPUT);
  dht.begin();
  snprintf(topic, sizeof topic, "sensors/%s/wx", NODE_ID);
  WiFi.mode(WIFI_STA);
  int n = WiFi.scanNetworks();  // boot diagnostic: is our network visible, how strong, which security
  for (int i = 0; i < n; i++)
    if (WiFi.SSID(i) == WIFI_SSID)
      Serial.printf("wifi: saw %s rssi %d dBm, auth %d, ch %d\n", WIFI_SSID, WiFi.RSSI(i), WiFi.encryptionType(i), WiFi.channel(i));
  WiFi.begin(WIFI_SSID, WIFI_PASS);  // ESP32 reconnects Wi-Fi on its own after this
  mqtt.setServer(MQTT_HOST, 1883);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    // status 1 = SSID not seen (antenna attached?), 4 = connect failed (password?), 6 = disconnected/retrying
    Serial.printf("wifi: connecting to %s... (status %d)\n", WIFI_SSID, WiFi.status());
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
  // no clock on the node: the Pi stamps the receive time
  char msg[192];
  snprintf(msg, sizeof msg,
           "{\"type\":\"wx\",\"node\":\"%s\",\"seq\":%lu,\"temp_c\":%.1f,\"rh\":%.1f,"
           "\"wind_ms\":-1,\"wind_dir\":-1,\"flame\":-1,\"batt\":-1}",
           NODE_ID, (unsigned long)seq, t, h);
  bool ok = mqtt.publish(topic, msg);
  Serial.printf("%s %s -> %s\n", ok ? "sent" : "FAILED", topic, msg);
  if (ok) { seq++; digitalWrite(LED, !digitalRead(LED)); }  // LED toggles on each successful send
}

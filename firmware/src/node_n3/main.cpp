// Node N3 (XIAO ESP32-C5): heartbeat to MQTT sensors/N3/wx every 10 s over FireNet.
// Format: docs/LCES_Design.md §1.2, same as N2 (firmware/weather-node).
// -1 / -99 = no data. This node has no wind, flame or battery sensor yet.
//
// Wiring (USB-C at top, XIAO straddling the breadboard gap):
//   Grove blue LED  : SIG -> D1 (GPIO0), VCC -> 3V3, GND -> GND   (status light)
//   Grove DHT11     : SIG -> D0 (GPIO1), VCC -> 3V3, GND -> GND   (optional)
//
// Status LED:
//   blinking once per second  = connecting to Wi-Fi or MQTT
//   short flash every 10 s    = heartbeat delivered to the Pi
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ---- edit these ----
const char* WIFI_SSID  = "FireNet";
const char* WIFI_PASS  = "firenet123";
const char* MQTT_HOST  = "10.42.1.1";   // Pi's IP on the FireNet hotspot
const char* NODE_ID    = "N3";
const uint32_t PUBLISH_MS = 10000;      // Pi greys a node after 3 missed heartbeats (~30 s)
#define HAS_DHT 1                       // 1 = DHT11 wired to D0. If missing, temp/RH are sent as -99/-1
// --------------------

const int LED_PIN = D1;   // GPIO0
const int DHT_PIN = D0;   // GPIO1

DHT dht(DHT_PIN, DHT11);
WiFiClient net;
PubSubClient mqtt(net);
char topic[32];
uint32_t seq = 0;
uint32_t lastPublish = 0;
bool firstPublish = true;

void toggleLed() { digitalWrite(LED_PIN, !digitalRead(LED_PIN)); }

void setup() {
  Serial.begin(115200);
  unsigned long start = millis();
  while (!Serial && millis() - start < 3000) delay(10);   // let the USB monitor connect

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
#if HAS_DHT
  dht.begin();
#endif
  snprintf(topic, sizeof topic, "sensors/%s/wx", NODE_ID);
  Serial.printf("\n=== Node %s (XIAO ESP32-C5) -> %s @ %s ===\n", NODE_ID, topic, MQTT_HOST);

  // Boot diagnostic: can we see FireNet, and how strong is it?
  WiFi.mode(WIFI_STA);
  int n = WiFi.scanNetworks();
  bool seen = false;
  for (int i = 0; i < n; i++) {
    if (WiFi.SSID(i) == WIFI_SSID) {
      seen = true;
      Serial.printf("wifi: saw %s  rssi %d dBm  ch %d\n", WIFI_SSID, WiFi.RSSI(i), WiFi.channel(i));
    }
  }
  if (!seen) Serial.printf("wifi: %s NOT visible (is the Pi hotspot on? antenna attached?)\n", WIFI_SSID);

  WiFi.begin(WIFI_SSID, WIFI_PASS);   // the ESP32 reconnects Wi-Fi on its own after this
  mqtt.setServer(MQTT_HOST, 1883);
}

void loop() {
  // 1. Wi-Fi
  if (WiFi.status() != WL_CONNECTED) {
    // status 1 = SSID not seen, 4 = connect failed (password?), 6 = disconnected/retrying
    Serial.printf("wifi: connecting to %s... (status %d)\n", WIFI_SSID, WiFi.status());
    toggleLed();
    delay(1000);
    return;
  }

  // 2. MQTT
  if (!mqtt.connected()) {
    Serial.printf("mqtt: connecting to %s:1883 (my ip %s, rssi %d dBm)...\n",
                  MQTT_HOST, WiFi.localIP().toString().c_str(), WiFi.RSSI());
    if (!mqtt.connect(NODE_ID)) {
      Serial.printf("mqtt: failed, state %d (-2 = broker unreachable)\n", mqtt.state());
      toggleLed();
      delay(2000);
      return;
    }
    Serial.println("mqtt: connected");
    digitalWrite(LED_PIN, LOW);
  }
  mqtt.loop();

  // 3. Heartbeat every PUBLISH_MS (first one right after connecting)
  if (!firstPublish && millis() - lastPublish < PUBLISH_MS) return;
  firstPublish = false;
  lastPublish = millis();

  float t = -99, h = -1;   // "no data" values from the agreed format
#if HAS_DHT
  float rt = dht.readTemperature();
  float rh = dht.readHumidity();
  if (isnan(rt) || isnan(rh)) {
    Serial.println("dht: read failed, sending -99/-1 (node stays alive, sensor shows no data)");
  } else {
    t = rt;
    h = rh;
  }
#endif

  // No clock on the node: the Pi stamps the receive time.
  char msg[192];
  snprintf(msg, sizeof msg,
           "{\"type\":\"wx\",\"node\":\"%s\",\"seq\":%lu,\"temp_c\":%.1f,\"rh\":%.1f,"
           "\"wind_ms\":-1,\"wind_dir\":-1,\"flame\":-1,\"batt\":-1}",
           NODE_ID, (unsigned long)seq, t, h);
  bool ok = mqtt.publish(topic, msg);
  Serial.printf("%s %s -> %s\n", ok ? "sent" : "FAILED", topic, msg);
  if (ok) {
    seq++;
    digitalWrite(LED_PIN, HIGH);   // short flash = delivered
    delay(150);
    digitalWrite(LED_PIN, LOW);
  }
}

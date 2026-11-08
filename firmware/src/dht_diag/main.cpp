// DHT11 diagnostic for the XIAO ESP32-C5 (N3). No Wi-Fi, prints over USB.
//
// 1. At boot: checks which pin the sensor's yellow SIG wire is on (D0 or D1).
//    A DHT11 module has its own pull-up, so its pin reads HIGH even with the
//    chip's internal pull-down switched on; an empty pin reads LOW.
// 2. Then every 2.5 s it tries a read on D0 with the Adafruit DHT library
//    (the same one N3 uses) and prints the result.
// (The DHTesp fallback library was removed: it froze on the ESP32-C5.)
#include <Arduino.h>
#include <DHT.h>

const int DHT_PIN = D0;   // GPIO1, where N3 expects the DHT11

DHT dht(DHT_PIN, DHT11);

// Print a line and push it out over USB right away, so nothing is lost if we freeze.
void say(const char* s) { Serial.println(s); Serial.flush(); }

int idleLevel(int pin) {
  pinMode(pin, INPUT_PULLDOWN);
  delay(5);
  return digitalRead(pin);
}

void setup() {
  Serial.begin(115200);
  unsigned long start = millis();
  while (!Serial && millis() - start < 3000) delay(10);

  say("\n=== DHT11 diagnostic (sensor expected on D0 = GPIO1) ===");
  say("waiting 2 s for the sensor to settle after power-up...");
  delay(2000);

  say("step 1: checking idle level of D0 and D1...");
  int d0 = idleLevel(D0);
  int d1 = idleLevel(D1);
  Serial.printf("idle level: D0=%s  D1=%s\n", d0 ? "HIGH" : "LOW", d1 ? "HIGH" : "LOW");
  if (d0)      say("  -> D0 is pulled HIGH: a sensor with a pull-up (like a DHT11) is on D0. Good.");
  else if (d1) say("  -> Sensor seems to be on D1! Move the yellow wire up one row to D0.");
  else         say("  -> Nothing pulling D0 or D1 HIGH. Check yellow -> D0, red -> 3V3, black -> GND.");

  say("step 2: starting the DHT library...");
  dht.begin();
  say("step 3: reading every 2.5 s...");
}

void loop() {
  delay(2500);   // DHT11 needs at least 1-2 s between reads
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) say("[read] FAIL: no reading from D0");
  else { Serial.printf("[read] OK  temp %.1f C  humidity %.1f %%\n", t, h); Serial.flush(); }
}

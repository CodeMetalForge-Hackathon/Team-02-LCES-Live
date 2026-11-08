// DHT11 line probe for the XIAO ESP32-C5 (N3). No Wi-Fi, prints over USB.
//
// 1. Pull-up check on every free pin: an unconnected pin must read 1. A pin that
//    reads 0 here is being held LOW by whatever is wired to it.
//    The Grove DHT11 has a 10k pull-up from SIG to its VCC. If VCC gets no power,
//    that resistor drags SIG LOW, so D0=0 here almost always means "sensor unpowered".
//    (A Grove LED lights from SIG + GND alone, so a working LED doesn't prove VCC.)
// 2. DHT11 start pulse on D0, then every edge for 8 ms. A healthy DHT11 gives
//    ~83 edges (response LOW/HIGH + 40 bits x LOW/HIGH).
#include <Arduino.h>
#include "driver/gpio.h"

const gpio_num_t PIN = (gpio_num_t)D0;   // GPIO1

void startPulseAndRecord() {
  static uint16_t t[200];
  int n = 0;
  // Open-drain: pinMode() switching is too slow on the C5 and misses the reply.
  gpio_reset_pin(PIN);
  gpio_set_direction(PIN, GPIO_MODE_INPUT_OUTPUT_OD);
  gpio_set_pull_mode(PIN, GPIO_PULLUP_ONLY);
  gpio_set_level(PIN, 0); delay(20);
  gpio_set_level(PIN, 1);
  uint32_t start = micros();
  int last = gpio_get_level(PIN), first = last;
  while (micros() - start < 8000 && n < 200) {
    int v = gpio_get_level(PIN);
    if (v != last) { t[n++] = micros() - start; last = v; }
  }
  Serial.printf("start pulse: line %c then %d edges |", first ? 'H' : 'L', n);
  for (int i = 0; i < n && i < 12; i++) Serial.printf(" %u", t[i]);
  Serial.println(n >= 80 ? "   <- sensor is talking!" : "");
}

void setup() {
  Serial.begin(115200);
  unsigned long s = millis();
  while (!Serial && millis() - s < 3000) delay(10);
  delay(500);
  Serial.println("\n=== DHT11 line probe (sensor SIG on D0 = GPIO1) ===");
}

void loop() {
  const int pins[] = {D0, D1, D2, D3, D4, D5, D8, D9, D10};
  const char* names[] = {"D0", "D1", "D2", "D3", "D4", "D5", "D8", "D9", "D10"};
  Serial.print("pull-up check (1 = free):");
  for (int i = 0; i < 9; i++) { pinMode(pins[i], INPUT_PULLUP); delay(3); Serial.printf(" %s=%d", names[i], digitalRead(pins[i])); }
  Serial.println(digitalRead(D0) ? "" : "   <- D0 held LOW: sensor has no power (check red VCC wire) or SIG touches GND");
  pinMode(D0, INPUT_PULLDOWN); delay(5);
  int pd = digitalRead(D0);
  Serial.printf("D0 with pull-DOWN: %d  %s\n", pd,
                pd ? "(module's own pull-up seen: sensor board connected + powered)"
                   : "(nothing pulling up: SIG wire not reaching the sensor, or a bare sensor with no pull-up)");
  delay(1500);
  startPulseAndRecord();
  Serial.flush();
  delay(1500);
}

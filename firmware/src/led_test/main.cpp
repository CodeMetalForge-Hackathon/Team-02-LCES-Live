// N3 bring-up, step 2: blink the Grove LED Socket (blue LED) on D1 = GPIO0.
// The onboard user LED (GPIO27) blinks too, so you can tell "code not running"
// apart from "external LED wiring wrong".
//
// Expected Serial output (115200):
//   === N3 LED test: Grove LED on D1 (GPIO0) ===
//   LED ON   (blink 1)
//   LED OFF
//   LED ON   (blink 2)
//   ...
#include <Arduino.h>

#ifndef LED_TEST_PIN
#define LED_TEST_PIN D1                // default D1 = GPIO0; env led_test_d0 overrides to D0
#endif
const int LED_PIN     = LED_TEST_PIN;
const int ONBOARD_LED = LED_BUILTIN;  // GPIO27 on the XIAO C5 (may light when LOW: that's normal)

void setup() {
  Serial.begin(115200);
  unsigned long start = millis();
  while (!Serial && millis() - start < 3000) delay(10);  // give the monitor time to connect

  pinMode(LED_PIN, OUTPUT);
  pinMode(ONBOARD_LED, OUTPUT);
  Serial.println();
  Serial.printf("=== N3 LED test: Grove LED on %s ===\n", LED_PIN == D0 ? "D0 (GPIO1)" : "D1 (GPIO0)");
}

void loop() {
  static unsigned long count = 0;
  count++;

  digitalWrite(LED_PIN, HIGH);
  digitalWrite(ONBOARD_LED, HIGH);
  Serial.printf("LED ON   (blink %lu)\n", count);
  delay(500);

  digitalWrite(LED_PIN, LOW);
  digitalWrite(ONBOARD_LED, LOW);
  Serial.println("LED OFF");
  delay(500);
}

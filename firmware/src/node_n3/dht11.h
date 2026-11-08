// Minimal DHT11 driver for the XIAO ESP32-C5 (no library).
//
// Why not the Adafruit DHT library: it froze inside begin()/read() on the C5
// (USB serial stopped, no readings). This driver tells you WHICH step failed.
//
// Why open-drain via the ESP-IDF gpio driver instead of pinMode(): on the C5,
// switching pinMode(OUTPUT) -> pinMode(INPUT_PULLUP) is slow enough that the
// sensor's reply (it starts ~14 us after release) is already over before we
// start listening. In open-drain mode the pin stays an input the whole time;
// we just stop pulling LOW, so listening starts instantly.
//
// DHT11 one-wire protocol (times in microseconds):
//   host : pull LOW >= 18 ms, then release (pull-up takes the line HIGH)
//   DHT  : LOW ~80, HIGH ~80                       ("I'm here")
//   DHT  : 40 bits, each = LOW ~50 then HIGH ~26 (0) or ~70 (1)
//   bytes: RH int, RH dec, T int, T dec, checksum (= sum of first 4, low 8 bits)
#pragma once
#include <Arduino.h>
#include "driver/gpio.h"

struct Dht11Reading {
  bool ok;
  float tempC;
  float humidity;
  const char* error;   // nullptr when ok
  uint8_t raw[5];
};

class Dht11 {
 public:
  explicit Dht11(int pin) : pin_((gpio_num_t)pin) {}

  void begin() {
    gpio_reset_pin(pin_);
    gpio_set_direction(pin_, GPIO_MODE_INPUT_OUTPUT_OD);   // drive LOW or let go; always readable
    gpio_set_pull_mode(pin_, GPIO_PULLUP_ONLY);            // the Grove board also has a 10k pull-up
    gpio_set_level(pin_, 1);                               // idle line is HIGH
  }

  // DHT11 needs >= 1 s between reads (we use 2 s+ in main.cpp).
  Dht11Reading read() {
    Dht11Reading r = {false, NAN, NAN, nullptr, {0, 0, 0, 0, 0}};

    // 1. Start signal: hold the line LOW for 20 ms.
    gpio_set_level(pin_, 0);
    delay(20);

    // 2. Release the line and listen. Timing-critical from here (~5 ms total).
    uint8_t highTimes[40];
    const char* err = nullptr;
    gpio_set_level(pin_, 1);

    if (waitWhile(HIGH, 200) < 0)       err = "no response (line never went LOW after start)";
    else if (waitWhile(LOW, 150) < 0)   err = "stuck LOW in response";
    else if (waitWhile(HIGH, 150) < 0)  err = "stuck HIGH in response";
    else {
      for (int i = 0; i < 40; i++) {
        if (waitWhile(LOW, 120) < 0) { err = "timeout on bit start (LOW)"; break; }
        int hi = waitWhile(HIGH, 150);
        if (hi < 0) { err = "timeout on bit data (HIGH)"; break; }
        highTimes[i] = (uint8_t)hi;
      }
    }
    if (err) { r.error = err; return r; }

    // 3. Decode: a HIGH longer than ~48 us is a 1.
    for (int i = 0; i < 40; i++) {
      r.raw[i / 8] <<= 1;
      if (highTimes[i] > 48) r.raw[i / 8] |= 1;
    }
    uint8_t sum = r.raw[0] + r.raw[1] + r.raw[2] + r.raw[3];
    if (sum != r.raw[4]) { r.error = "checksum mismatch"; return r; }

    r.humidity = r.raw[0] + r.raw[1] * 0.1f;
    float t = r.raw[2] + (r.raw[3] & 0x7F) * 0.1f;
    if (r.raw[3] & 0x80) t = -t;   // newer DHT11s flag negative temps in bit 7
    r.tempC = t;
    r.ok = true;
    return r;
  }

 private:
  gpio_num_t pin_;

  // Waits while the line is at `level`. Returns how long it stayed there (us),
  // or -1 if it was still there after timeoutUs.
  int waitWhile(int level, uint32_t timeoutUs) {
    uint32_t start = micros();
    while (gpio_get_level(pin_) == level) {
      if (micros() - start > timeoutUs) return -1;
    }
    return (int)(micros() - start);
  }
};

// Task A smoke test: proves the PlatformIO toolchain, upload and USB serial all work.
// Expected output: a banner with chip info, then "C5 alive  uptime=Ns" every second.
#include <Arduino.h>

void setup() {
  Serial.begin(115200);

  // USB serial only exists after the PC opens the port, so wait up to 3 s for
  // the monitor to connect. Otherwise the banner is lost.
  unsigned long start = millis();
  while (!Serial && millis() - start < 3000) {
    delay(10);
  }

  Serial.println();
  Serial.println("=== XIAO ESP32-C5 alive test (PlatformIO) ===");
  Serial.printf("Chip model : %s rev %d\n", ESP.getChipModel(), ESP.getChipRevision());
  Serial.printf("CPU freq   : %lu MHz\n", (unsigned long)ESP.getCpuFreqMHz());
  Serial.printf("Flash size : %lu bytes\n", (unsigned long)ESP.getFlashChipSize());
  Serial.printf("PSRAM size : %lu bytes\n", (unsigned long)ESP.getPsramSize());
  Serial.printf("Core ver   : %s\n", ESP.getCoreVersion());
}

void loop() {
  Serial.printf("C5 alive  uptime=%lus\n", millis() / 1000);
  delay(1000);
}

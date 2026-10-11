
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

Adafruit_SSD1306 display(128, 64, &Wire, -1);

void setup() {
  Serial.begin(115200);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    while (true) {}
  }
  display.clearDisplay();
  display.display();
}

void loop() {
  if (Serial.read() != 'F') {
    return;
  }

  uint8_t *buffer = display.getBuffer();

  for (int i = 0; i < 1024; i++) {
    while (Serial.available() == 0) {
      // Wait for the rest of the frame.
    }
    buffer[i] = (uint8_t)Serial.read();
  }

  display.display();
}


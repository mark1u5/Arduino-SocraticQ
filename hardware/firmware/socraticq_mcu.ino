// SocraticQ MCU firmware for the STM32U585 side of the Arduino UNO Q.
// Default configuration uses the built-in 8x13 LED Matrix only (no external modules).
// The UNO Q uses Arduino RouterBridge for internal MCU <-> Linux communication.

#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>
#include <vector>

// Built-in 8x13 LED matrix only by default. Set to 1 only if external pots/buttons/RGB/buzzer are wired.
#ifndef SOCRATICQ_EXTERNAL_IO
#define SOCRATICQ_EXTERNAL_IO 0
#endif

#if SOCRATICQ_EXTERNAL_IO
// Configurable external wiring pins (used only when SOCRATICQ_EXTERNAL_IO == 1)
#ifndef SOCRATICQ_CATEGORY_PIN
#define SOCRATICQ_CATEGORY_PIN A0
#endif
#ifndef SOCRATICQ_CONFIDENCE_PIN
#define SOCRATICQ_CONFIDENCE_PIN A1
#endif
#ifndef SOCRATICQ_HINT_BUTTON_PIN
#define SOCRATICQ_HINT_BUTTON_PIN 2
#endif
#ifndef SOCRATICQ_SUBMIT_BUTTON_PIN
#define SOCRATICQ_SUBMIT_BUTTON_PIN 3
#endif
#ifndef SOCRATICQ_RGB_RED_PIN
#define SOCRATICQ_RGB_RED_PIN 9
#endif
#ifndef SOCRATICQ_RGB_GREEN_PIN
#define SOCRATICQ_RGB_GREEN_PIN 10
#endif
#ifndef SOCRATICQ_RGB_BLUE_PIN
#define SOCRATICQ_RGB_BLUE_PIN 11
#endif
#ifndef SOCRATICQ_BUZZER_PIN
#define SOCRATICQ_BUZZER_PIN 6
#endif

const char* DEVICE_ID = "unoq-01";
const char* PROBLEM_IDS_BY_CATEGORY[5] = {
  "CAM-2024-N4-11",
  "CAM-2024-N3-05",
  "CAM-2023-N4-17",
  "CAM-2021-N4-15",
  "CAM-2022-N4-14"
};
const char* DEMO_OPTION = "C";
#endif

// LED Matrix instance (8 rows x 13 columns, 104 pixels)
Arduino_LED_Matrix matrix;
const uint8_t FRAME_SIZE = 8 * 13;
uint8_t currentFrame[FRAME_SIZE] = {0};
bool customFrameActive = false;

// Built-in 8x13 frame bitmaps (0 = off, 7 = full brightness)
// Row-major order: index = row * 13 + col
void loadMatrixBitmap(const char* asciiMap[8]) {
  for (int r = 0; r < 8; ++r) {
    for (int c = 0; c < 13; ++c) {
      char ch = asciiMap[r][c];
      currentFrame[r * 13 + c] = (ch == '#') ? 7 : 0;
    }
  }
}

const char* BITMAP_HEART[8] = {
  "..##...##....",
  ".####.####...",
  ".#########...",
  ".#########...",
  "..#######....",
  "...#####.....",
  "....###......",
  ".....#......."
};

const char* BITMAP_THINKING[8] = {
  ".............",
  ".............",
  "..##...##....",
  ".#..#.#..#...",
  ".............",
  "..##...##....",
  ".............",
  "............."
};

const char* BITMAP_NUDGE[8] = {
  "....###......",
  "...#...#.....",
  ".......#.....",
  "......#......",
  ".....#.......",
  ".............",
  ".....#.......",
  "............."
};

const char* BITMAP_SOLVED[8] = {
  "..........#..",
  ".........#...",
  "........#....",
  ".......#.....",
  ".#....#......",
  "..#..#.......",
  "...##........",
  "............."
};

const char* BITMAP_ALERT[8] = {
  "....###......",
  "....###......",
  "....###......",
  "....###......",
  ".....#.......",
  ".............",
  ".....#.......",
  ".....#......."
};

#if SOCRATICQ_EXTERNAL_IO
uint32_t sequenceNumber = 0;
uint32_t attemptNumber = 0;
int lastCategory = -1;
int lastConfidence = -1;
int rawCategoryCandidate = -1;
int rawConfidenceCandidate = -1;
uint8_t categoryStabilityCount = 0;
uint8_t confidenceStabilityCount = 0;

bool lastHintState = HIGH;
bool lastSubmitState = HIGH;
uint32_t lastHintPress = 0;
uint32_t lastSubmitPress = 0;
const uint32_t DEBOUNCE_MS = 200;

void setRgb(int red, int green, int blue) {
  analogWrite(SOCRATICQ_RGB_RED_PIN, red);
  analogWrite(SOCRATICQ_RGB_GREEN_PIN, green);
  analogWrite(SOCRATICQ_RGB_BLUE_PIN, blue);
}

void buzz(const String& pattern) {
  if (pattern == "SUCCESS") {
    tone(SOCRATICQ_BUZZER_PIN, 880, 150);
  } else if (pattern == "SHORT_DOUBLE") {
    tone(SOCRATICQ_BUZZER_PIN, 660, 80);
    delay(100);
    tone(SOCRATICQ_BUZZER_PIN, 660, 80);
  } else if (pattern == "LONG_ALERT") {
    tone(SOCRATICQ_BUZZER_PIN, 440, 300);
  }
}
#endif

void updateMatrixState(const String& state) {
  if (state == "IDLE") {
    customFrameActive = false;
    loadMatrixBitmap(BITMAP_HEART);
    return;
  }
  if (customFrameActive) {
    return;
  }
  if (state == "THINKING") {
    loadMatrixBitmap(BITMAP_THINKING);
  } else if (state == "SOCRATIC_NUDGE") {
    loadMatrixBitmap(BITMAP_NUDGE);
  } else if (state == "SOLVED") {
    loadMatrixBitmap(BITMAP_SOLVED);
  } else if (state == "BOTTLENECK_ALERT") {
    loadMatrixBitmap(BITMAP_ALERT);
  }
}

// Allows Python side to send custom 8x13 frames directly via Bridge.call("draw", ...)
void draw(std::vector<uint8_t> newFrame) {
  size_t count = (newFrame.size() < (size_t)FRAME_SIZE) ? newFrame.size() : (size_t)FRAME_SIZE;
  for (size_t i = 0; i < count; ++i) {
    currentFrame[i] = newFrame[i];
  }
  customFrameActive = true;
}

String extractString(const String& json, const String& key) {
  String token = "\"" + key + "\":\"";
  int start = json.indexOf(token);
  if (start < 0) return "";
  start += token.length();
  int end = json.indexOf('"', start);
  return end < 0 ? "" : json.substring(start, end);
}

bool applyFeedback(String feedbackJson) {
  String state = extractString(feedbackJson, "led_state");
  updateMatrixState(state);

#if SOCRATICQ_EXTERNAL_IO
  int rgbStart = feedbackJson.indexOf("\"rgb\":[");
  if (rgbStart >= 0) {
    String rgb = feedbackJson.substring(rgbStart + 7);
    int first = rgb.indexOf(',');
    int second = rgb.indexOf(',', first + 1);
    int end = rgb.indexOf(']', second + 1);
    if (first >= 0 && second > first && end > second) {
      setRgb(rgb.substring(0, first).toInt(),
             rgb.substring(first + 1, second).toInt(),
             rgb.substring(second + 1, end).toInt());
    }
  }

  buzz(extractString(feedbackJson, "buzz_pattern"));
#endif
  Monitor.print("SocraticQ feedback [");
  Monitor.print(state);
  Monitor.print("]: ");
  Monitor.println(extractString(feedbackJson, "lcd_line1"));
  return true;
}

#if SOCRATICQ_EXTERNAL_IO
void sendEvent(const char* eventType, const char* selectedOption) {
  if (strcmp(eventType, "PROBLEM_SELECT") == 0) {
    customFrameActive = false;
  }
  int catIdx = (lastCategory < 0 || lastCategory > 4) ? 0 : lastCategory;
  const char* problemId = PROBLEM_IDS_BY_CATEGORY[catIdx];
  char payload[320];
  snprintf(payload, sizeof(payload),
           "{\"schema_v\":1,\"device_id\":\"%s\",\"seq\":%lu,\"ts_ms\":%lu,"
           "\"event_type\":\"%s\",\"problem_id\":\"%s\","
           "\"dial_category_idx\":%d,\"confidence_dial\":%d,"
           "\"selected_option\":%s,\"attempt_num\":%lu}",
           DEVICE_ID, (unsigned long)(++sequenceNumber), (unsigned long)millis(),
           eventType, problemId, catIdx,
           (lastConfidence < 0 ? 50 : lastConfidence),
           selectedOption ? (String("\"") + selectedOption + "\"").c_str() : "null",
           (unsigned long)attemptNumber);

  Bridge.notify("socraticq_event", String(payload));
  setRgb(0, 200, 255);  // Visual cyan THINKING cue on RGB
  loadMatrixBitmap(BITMAP_THINKING);
}
#endif

void setup() {
  Monitor.begin(115200);

  // Initialize LED Matrix
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
  loadMatrixBitmap(BITMAP_HEART);

#if SOCRATICQ_EXTERNAL_IO
  analogReadResolution(12);
  pinMode(SOCRATICQ_HINT_BUTTON_PIN, INPUT_PULLUP);
  pinMode(SOCRATICQ_SUBMIT_BUTTON_PIN, INPUT_PULLUP);
  pinMode(SOCRATICQ_RGB_RED_PIN, OUTPUT);
  pinMode(SOCRATICQ_RGB_GREEN_PIN, OUTPUT);
  pinMode(SOCRATICQ_RGB_BLUE_PIN, OUTPUT);
  pinMode(SOCRATICQ_BUZZER_PIN, OUTPUT);
  setRgb(0, 80, 255);  // Initial IDLE blue
#endif

  Bridge.begin();
  Bridge.provide("draw", draw);
  Bridge.provide_safe("socraticq_feedback", applyFeedback);

#if SOCRATICQ_EXTERNAL_IO
  // Baseline analog readings
  lastCategory = constrain(map(analogRead(SOCRATICQ_CATEGORY_PIN), 0, 4095, 0, 4), 0, 4);
  lastConfidence = constrain(map(analogRead(SOCRATICQ_CONFIDENCE_PIN), 0, 4095, 0, 100), 0, 100);
#endif

  Monitor.println("SocraticQ MCU ready (8x13 LED Matrix + RouterBridge)");
}

void loop() {
  // Always refresh LED matrix
  matrix.draw(currentFrame);

#if SOCRATICQ_EXTERNAL_IO
  // Read analog inputs with 5-cycle stability check to prevent floating pin noise
  int rawCat = constrain(map(analogRead(SOCRATICQ_CATEGORY_PIN), 0, 4095, 0, 4), 0, 4);
  int rawConf = constrain(map(analogRead(SOCRATICQ_CONFIDENCE_PIN), 0, 4095, 0, 100), 0, 100);

  if (rawCat != lastCategory) {
    if (rawCat == rawCategoryCandidate) {
      if (++categoryStabilityCount >= 5) {
        lastCategory = rawCat;
        categoryStabilityCount = 0;
        attemptNumber = 0;
        customFrameActive = false;
        sendEvent("PROBLEM_SELECT", nullptr);
      }
    } else {
      rawCategoryCandidate = rawCat;
      categoryStabilityCount = 0;
    }
  }

  if (abs(rawConf - lastConfidence) >= 4) {
    if (abs(rawConf - rawConfidenceCandidate) <= 1) {
      if (++confidenceStabilityCount >= 5) {
        lastConfidence = rawConf;
        confidenceStabilityCount = 0;
        sendEvent("CONFIDENCE_UPDATE", nullptr);
      }
    } else {
      rawConfidenceCandidate = rawConf;
      confidenceStabilityCount = 0;
    }
  }

  // Read digital buttons with debounce
  bool hintState = digitalRead(SOCRATICQ_HINT_BUTTON_PIN);
  bool submitState = digitalRead(SOCRATICQ_SUBMIT_BUTTON_PIN);
  uint32_t now = millis();

  if (lastHintState == HIGH && hintState == LOW && now - lastHintPress > DEBOUNCE_MS) {
    lastHintPress = now;
    sendEvent("HINT_REQUEST", nullptr);
  }
  if (lastSubmitState == HIGH && submitState == LOW && now - lastSubmitPress > DEBOUNCE_MS) {
    lastSubmitPress = now;
    ++attemptNumber;
    sendEvent("ANSWER_SUBMIT", DEMO_OPTION);
  }

  lastHintState = hintState;
  lastSubmitState = submitState;
#endif
  delay(15);
}

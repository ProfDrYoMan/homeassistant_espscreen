// What asks the internal RAM for room. On an ESP32-P4 screen ESPHome builds with CONFIG_SPIRAM_USE_CAPS_ALLOC: every
// plain malloc, new, std::string and std::vector takes internal RAM, however large, and TLS does too
// (MBEDTLS_INTERNAL_MEM_ALLOC). A reTerminal D1001 settles at about 250 KB of it free, yet its lowest since boot read
// 162 KB five minutes after a boot and 20 KB after a few hours (2026-10-24), with nothing in its log to say what took
// the rest; a Tab5 of a dev tester that restarts every few minutes read 38 KB.
//
// So the screen watches two things and keeps them where a reset leaves them (.noinit), the way loop_record keeps the
// main loop at the watchdog:
// - the lowest the internal RAM has been since boot (ESP-IDF's own counter, heap_caps_get_minimum_free_size, which
//   walks nothing), logged each time it falls another STEP, with the moment, so the log around it says what ran;
// - every allocation that found no room (heap_caps_register_failed_alloc_callback): its size, the memory it asked for,
//   the function and the task that asked, and how much internal RAM was free then. The hook runs in the task that
//   asked, with the heap short, so it only writes the record; the main loop logs it.
// The next boot hands both on: `*** INTERNAL RAM ON PREVIOUS BOOT ***` with a `Lowest:` and a `Failed:` line, logged
// with ESPHome's config dump, which every log subscriber asks for (the troubleshooting report's too: reports.py
// CRASH_LINE keeps the lines with a crash).
#pragma once
#include <cstdint>

namespace heap_watch {

constexpr uint32_t STEP = 16 * 1024;  // the lowest is logged again once it falls this much further
constexpr uint32_t EVERY_MS = 250;    // how often the main loop reads the counter

// The lowest seen, and whether it fell far enough below the last one logged to be logged again.
struct LowWater {
  uint32_t logged = 0;
  bool due(uint32_t lowest, uint32_t step) {
    if (logged != 0 && lowest + step > logged) return false;
    logged = lowest;
    return true;
  }
};

// At boot: takes the previous boot's record and starts this one's (the hook included).
void setup();
// Every turn of the main loop: the lowest and the failures since the last turn, logged.
void tick(uint32_t now);
// With the config dump: the previous boot's record, and this boot's so far.
void dump();

}  // namespace heap_watch

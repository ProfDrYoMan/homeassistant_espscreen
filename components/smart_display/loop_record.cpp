// The main loop at the task watchdog (loop_record.h says why): the watchdog's hook keeps the main loop's stack, the
// next boot logs it.
#include "loop_record.h"
#include "esphome/core/defines.h"
#ifdef USE_ESP32
#include <string>

#include "esphome/core/log.h"
#include <esp_attr.h>
#include <esp_memory_utils.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_private/freertos_debug.h>

namespace esphome {
extern TaskHandle_t loop_task_handle;  // the main loop's task, esphome/components/esp32/core.cpp
}

namespace loop_record {

namespace {

constexpr const char *TAG = "loop_record";
constexpr uint32_t MAGIC = 0x4C4F4F50;  // "LOOP"

// Kept where a reset leaves it (.noinit): written in the watchdog's interrupt, read at the next boot.
struct Record {
  uint32_t magic;
  uint32_t count;
  uint32_t running;      // the main loop was the task on its core at the time (it spun), else it waited
  uint32_t stack_words;  // how much of its stack was in use, in words
  uint32_t at[MOST];
};
__NOINIT_ATTR Record record;

bool is_code(uint32_t word) { return esp_ptr_executable(reinterpret_cast<const void *>(word)); }

}  // namespace

Record taken{};
bool had = false;

void take() {
  if (record.magic != MAGIC) return;
  taken = record;
  had = true;
  record.magic = 0;
}

void report() {
  if (!had) return;
  const uint32_t count = taken.count > static_cast<uint32_t>(MOST) ? MOST : taken.count;
  ESP_LOGE(TAG, "*** THE MAIN LOOP AT THE LAST TASK WATCHDOG *** (it %s; %u words of its stack in use; newest first)",
           taken.running ? "was running" : "waited", (unsigned) taken.stack_words);
  std::string all;
  for (uint32_t i = 0; i < count; ++i) {
    ESP_LOGE(TAG, "  LOOP%u: 0x%08X", (unsigned) i, (unsigned) taken.at[i]);
    char hex[12];
    snprintf(hex, sizeof hex, " 0x%08X", (unsigned) taken.at[i]);
    all += hex;
  }
  ESP_LOGE(TAG, "  Use: addr2line -pfiaC -e firmware.elf%s", all.c_str());
}

}  // namespace loop_record

// ESP-IDF calls this in the task watchdog's interrupt, before it prints and aborts (task_wdt.c). The main loop is the
// one task the watchdog watches on these screens, so its stack is what to keep: no logging here, an interrupt.
extern "C" void esp_task_wdt_isr_user_handler(void) {
  TaskHandle_t loop = esphome::loop_task_handle;
  if (loop == nullptr) return;
  TaskSnapshot_t snapshot;
  if (vTaskGetSnapshot(loop, &snapshot) != pdTRUE) return;
  const auto *from = reinterpret_cast<const uint32_t *>(snapshot.pxTopOfStack);
  const auto *end = reinterpret_cast<const uint32_t *>(snapshot.pxEndOfStack);
  if (from == nullptr || end == nullptr || end <= from) return;
  bool running = false;
#if CONFIG_FREERTOS_NUMBER_OF_CORES > 1
  for (BaseType_t core = 0; core < CONFIG_FREERTOS_NUMBER_OF_CORES; ++core)
    if (xTaskGetCurrentTaskHandleForCore(core) == loop) running = true;
#else
  running = xTaskGetCurrentTaskHandle() == loop;
#endif
  loop_record::record.running = running;
  loop_record::record.stack_words = static_cast<uint32_t>(end - from);
  loop_record::record.count = loop_record::scan(from, end, loop_record::record.at, loop_record::MOST, loop_record::is_code);
  loop_record::record.magic = loop_record::MAGIC;
}
#else
namespace loop_record {
void take() {}
void report() {}
}  // namespace loop_record
#endif

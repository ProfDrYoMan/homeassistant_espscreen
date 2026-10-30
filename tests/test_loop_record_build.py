"""Compile and link the real watchdog hook without ESPHome's removed loop_task_handle.

The FreeRTOS boundary is stubbed; this tests linking and the boot/ISR record path,
not ESP-IDF interrupt behavior on hardware.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LoopRecordBuildTests(unittest.TestCase):
    def test_watchdog_records_the_setup_task_without_esphome_private_symbols(self):
        compiler = shutil.which(os.environ.get('CXX', 'clang++')) or shutil.which('g++')
        if not compiler:
            self.skipTest('a C++ compiler is required')
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            headers = {
                'esphome/core/defines.h': '#define USE_ESP32\n',
                'esphome/core/log.h': '''#include <cstdio>
inline void test_log(const char *, const char *, ...) {}
#define ESP_LOGE(...) test_log(__VA_ARGS__)
''',
                'esp_attr.h': '#define __NOINIT_ATTR\n',
                'esp_memory_utils.h': '''#include <cstdint>
inline bool esp_ptr_executable(const void *p) {
  return (reinterpret_cast<uintptr_t>(p) & 0xFF000000u) == 0x48000000u;
}
''',
                'freertos/FreeRTOS.h': '''#pragma once
using TaskHandle_t = void *;
using BaseType_t = int;
constexpr int pdTRUE = 1;
''',
                'freertos/task.h': '''#pragma once
#include "FreeRTOS.h"
TaskHandle_t xTaskGetHandle(const char *);
TaskHandle_t xTaskGetCurrentTaskHandle();
TaskHandle_t xTaskGetCurrentTaskHandleForCore(BaseType_t);
int eTaskGetState(TaskHandle_t);
''',
                'esp_private/freertos_debug.h': '''#pragma once
#include <cstdint>
#include <freertos/task.h>
struct TaskSnapshot_t { uint32_t *pxTopOfStack; uint32_t *pxEndOfStack; };
int vTaskGetSnapshot(TaskHandle_t, TaskSnapshot_t *);
''',
            }
            for name, content in headers.items():
                path = tmp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            harness = tmp / 'watchdog.cpp'
            harness.write_text('''#include <cassert>
#include "''' + str(ROOT / 'components/smart_display/loop_record.cpp') + '''"
int loop_token, timer_token, other_token;
TaskHandle_t current_task = &loop_token;
uint32_t loop_stack[] = {0x48123456, 7, 0x48234567};
uint32_t timer_stack[] = {0x48345678};
TaskHandle_t xTaskGetHandle(const char *) { return &timer_token; }
TaskHandle_t xTaskGetCurrentTaskHandle() { return current_task; }
TaskHandle_t xTaskGetCurrentTaskHandleForCore(BaseType_t core) {
  return core == 1 ? &loop_token : current_task;
}
int eTaskGetState(TaskHandle_t task) { assert(task == &timer_token); return 2; }
int vTaskGetSnapshot(TaskHandle_t task, TaskSnapshot_t *snapshot) {
  assert(task == &loop_token || task == &timer_token);
  bool loop = task == &loop_token;
  snapshot->pxTopOfStack = loop ? loop_stack : timer_stack;
  snapshot->pxEndOfStack = snapshot->pxTopOfStack + (loop ? 3 : 1);
  return pdTRUE;
}
int main() {
  loop_record::take();  // Component setup runs on the main loop task.
  assert(!loop_record::had);
  current_task = &other_token;  // The watchdog interrupt need not interrupt that task.
  esp_task_wdt_isr_user_handler();
  loop_record::take();  // Simulate the next boot reading the saved record.
  assert(loop_record::had);
  assert(loop_record::taken.count == 2);
  assert(loop_record::taken.at[0] == 0x48123456);
  assert(loop_record::taken.at[1] == 0x48234567);
  assert(loop_record::taken.running == (CONFIG_FREERTOS_NUMBER_OF_CORES > 1));
  assert(loop_record::taken.timer_count == 1);
  assert(loop_record::taken.timer_at[0] == 0x48345678);
  assert(loop_record::taken.timer_state == 3);
  assert(loop_record::record.magic == 0);
  loop_record::report();
}
''')
            for cores in (1, 2):
                with self.subTest(cores=cores):
                    binary = tmp / f'watchdog-{cores}'
                    result = subprocess.run(
                        [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                         f'-DCONFIG_FREERTOS_NUMBER_OF_CORES={cores}', f'-I{tmp}',
                         str(harness), '-o', str(binary)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    subprocess.run([str(binary)], check=True)

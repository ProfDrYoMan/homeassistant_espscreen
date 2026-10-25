// The capability record (hw.h): the build's defines and the boot's findings resolved once, and written into the hello.
#include "hw.h"
#include "esphome/core/defines.h"
#include "hw_hello.h"
#include "live_view.h"
#ifdef USE_ESP32
#include <esp_heap_caps.h>
#endif

namespace hw {

namespace {

Facts facts() {
  Facts f;
#ifdef HW_SPEAKER
  f.speaker = true;
#endif
#ifdef HW_MICROPHONE
  f.microphone = true;
#endif
#ifdef HW_ECHO_CANCEL
  f.echo_cancel = true;
#endif
#ifdef HW_CAMERA
  f.camera = true;
#endif
  f.live_video = live_view::available();
#ifdef USE_ESP32
  f.psram_bytes = static_cast<uint32_t>(heap_caps_get_total_size(MALLOC_CAP_SPIRAM));
  f.internal_bytes = static_cast<uint32_t>(heap_caps_get_total_size(MALLOC_CAP_INTERNAL));
#endif
  return f;
}

}  // namespace

const Caps &caps() {
  static const Caps resolved = resolve(facts());
  return resolved;
}

void hello(JsonObject root) {
  const Caps &c = caps();
  auto caps = root["caps"].to<JsonObject>();
  caps["live"] = c.live_video;
  caps["speaker"] = c.speaker;
  caps["microphone"] = c.microphone;
  caps["echo_cancel"] = c.echo_cancel;
  caps["camera"] = c.camera;
  caps["live_tiles"] = c.live_tiles;
  caps["psram_kb"] = c.psram_bytes / 1024;
  caps["internal_kb"] = c.internal_bytes / 1024;
}

}  // namespace hw

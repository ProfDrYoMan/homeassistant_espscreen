// The PPA guard on a P4 screen (ppa_guard.h says why): every scale-rotate-mirror call of the firmware comes through
// here, ESPHome's LVGL flush of a turned glass, a stream's copy of its picture (live_view.cpp) and a plugin's camera
// alike, through the linker's wrap of ppa_do_scale_rotate_mirror (smart_display/__init__.py adds it with the other
// wraps on a P4). A call the engine takes whole goes straight on; one it would stall on goes in the pieces the plan
// gives, one after the other, each as blocking as the call was.
#include "ppa_guard.h"
#include "esphome/core/defines.h"  // USE_LIVE_VIEW: the header above has no ESPHome in it
#ifdef USE_LIVE_VIEW
#include <driver/ppa.h>
#include "esphome/core/log.h"

namespace {

constexpr const char *TAG = "ppa_guard";
constexpr uint32_t TOLD_AT_MOST = 8;  // log lines per boot about blocks that went in pieces; the rest are counted
uint32_t guarded = 0, given_up = 0;

ppa_guard::Scale as_scale(float scale) {
  // As the driver splits its float (ppa_srm.c): the whole part, and the fraction in sixteenths rounded down.
  ppa_guard::Scale s;
  s.whole = static_cast<uint32_t>(scale);
  s.frag = static_cast<uint32_t>(scale * ppa_guard::FRAG_MAX) & (ppa_guard::FRAG_MAX - 1);
  return s;
}

// The output's bits per pixel, and the width of a transaction unit for it: 32 pixels for the wide formats, else 64
// (ppa_srm.c, "Hardware bug workaround"). 0 for a format the guard does not know, which then goes as it is.
uint32_t depth_of(ppa_srm_color_mode_t cm, uint32_t &unit_w) {
  unit_w = 64;
  switch (cm) {
    case PPA_SRM_COLOR_MODE_RGB565:
      return 16;
    case PPA_SRM_COLOR_MODE_RGB888:
      unit_w = 32;
      return 24;
    case PPA_SRM_COLOR_MODE_ARGB8888:
      unit_w = 32;
      return 32;
    case PPA_SRM_COLOR_MODE_YUV420:
      return 12;
    case PPA_SRM_COLOR_MODE_YUV444:
      return 24;
    default:  // GRAY8 and whatever a later ESP-IDF adds: as it is
      return 0;
  }
}

}  // namespace

extern "C" esp_err_t __real_ppa_do_scale_rotate_mirror(ppa_client_handle_t client, const ppa_srm_oper_config_t *config);
extern "C" esp_err_t __wrap_ppa_do_scale_rotate_mirror(ppa_client_handle_t client, const ppa_srm_oper_config_t *config) {
  // A call that does not wait cannot be made to wait for its pieces, and a mirrored block lands elsewhere than the plan
  // places it: both go as they are (nothing on this screen asks for either).
  if (!config || config->mode != PPA_TRANS_MODE_BLOCKING || config->mirror_x || config->mirror_y)
    return __real_ppa_do_scale_rotate_mirror(client, config);
  ppa_guard::Block block;
  block.w = config->in.block_w;
  block.h = config->in.block_h;
  block.angle = static_cast<ppa_guard::Angle>(config->rotation_angle);
  block.sx = as_scale(config->scale_x);
  block.sy = as_scale(config->scale_y);
  block.depth = depth_of(config->out.srm_cm, block.unit_w);
  // ESPHome builds the P4 for revision 1 (CONFIG_ESP32P4_SELECTS_REV_LESS_V3), where the driver counts macro blocks of
  // 16 rows whatever the chip says (ppa_ll_srm_get_mb_size).
  block.mb = 16;
  if (block.depth == 0) return __real_ppa_do_scale_rotate_mirror(client, config);
  ppa_guard::Piece pieces[ppa_guard::MAX_PIECES];
  const int n = ppa_guard::plan(block, pieces);
  if (n == 1) return __real_ppa_do_scale_rotate_mirror(client, config);
  if (n == 0) {
    if (given_up++ == 0)
      ESP_LOGW(TAG, "a turned copy of %ux%u may hang the 2D-DMA (DIG-734) and has no safe pieces; copied as it is",
               (unsigned) block.w, (unsigned) block.h);
    return __real_ppa_do_scale_rotate_mirror(client, config);
  }
  if (guarded++ < TOLD_AT_MOST)
    ESP_LOGI(TAG, "a turned copy of %ux%u would hang the 2D-DMA (DIG-734): copied in %d pieces", (unsigned) block.w,
             (unsigned) block.h, n);
  for (int i = 0; i < n; ++i) {
    ppa_srm_oper_config_t own = *config;
    own.in.block_offset_x = config->in.block_offset_x + pieces[i].in_x;
    own.in.block_offset_y = config->in.block_offset_y + pieces[i].in_y;
    own.in.block_w = pieces[i].in_w;
    own.in.block_h = pieces[i].in_h;
    own.out.block_offset_x = config->out.block_offset_x + pieces[i].out_x;
    own.out.block_offset_y = config->out.block_offset_y + pieces[i].out_y;
    const esp_err_t err = __real_ppa_do_scale_rotate_mirror(client, &own);
    if (err != ESP_OK) return err;
  }
  return ESP_OK;
}
#endif

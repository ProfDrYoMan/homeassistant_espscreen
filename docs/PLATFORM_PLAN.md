# The core functions: a plan

What Tessera builds next on the ESP32-P4 boards, and in what order: a hardware layer the shared code talks to instead
of the chip, memory and live cameras with limits a user can read, and the ground for a doorbell you can talk back to.
Written 2026-10-25 after the P4 memory work (CHANGELOG, heap_watch, psram_task) and a review of the seams the code has
today; docs/PICTURE_ARCHITECTURE.md says how the picture path is built, this page says what changes and why. The rules
that already hold stay (AGENTS.md: behaviour lives once, a board file holds hardware and sizes only, no capability rules
by hand).

## 1. Where we stand

A few thousand screens run Tessera, a few hundred of them on a P4. The S3 boards keep their refreshed stills and stay
out of this page. Everything with video is the P4's: live camera tiles, the camera full screen with its sound, the
screen's own camera as video for Home Assistant, and a doorbell that rings on the glass. Users now ask for five live
tiles on one screen, and the doorbell should one day hear them back and open the door.

What the review found (2026-10-25, branch dev):

- **The picture path has its seam.** `picture_engine.h` (decode, blit, client) and `glass.h` (frame buffer, rooms,
  one-writer lock) are the only places the shared code meets the chip; `p4_build.py` picks the engine and the linker
  wraps, and S3 boards and the host get inline stubs for free. No `#if` on a chip name exists in `smart_display`.
- **Three chip facts leak out of the engine:** `glass::PPA_BURST_BYTES`, the PPA's sixteenths in
  `live_view::scaled()`, and the picture sizes of `live_view.h` (`MAX_PICTURE`, `TILE_PICTURE`) that come from P4
  measurements.
- **The Tab5 is a second P4.** `hardware/m5stack-tab5.yaml` does not include `esp32p4-c6.yaml`: it carries its own
  esp32 and hosted block and misses what the other P4 boards got this week (`CONFIG_ESP_HOSTED_MEMPOOL_PREFER_SPIRAM`,
  the 20 s watchdog). The Tab5 tester's restarts every few minutes are consistent with that.
- **Audio has no owner.** The camera's sound and Home Assistant's media both `start()`, `stop()` and
  `set_audio_stream_info()` on `ts_media_speaker` with nothing between them; the voice plugin, the tap sound and the
  doorbell chime share the announcement input through the mixer alone; the microphone is shared by listener count.
  Nothing says who may speak or listen when a doorbell is open.
- **Plugins cannot ask what a board can.** The API (0.18) exposes `ui::pictures()` and nothing else; `ui::live()`
  degrades to stills without a word. A board's features reach plugins only as build-time ids
  (`plugin_manifest.py`).
- **Limits are scattered.** The firmware allows seven streams a screen (`live_view::MAX_STREAMS`), the add-on eight of
  all screens together (`live_stream.MAX_STREAMS`), the editor nothing; none of the three knows the others, and a
  refused stream leaves a still without saying why.
- **The network is the ceiling, not the memory.** Espressif measures 30 Mbit/s TCP into a P4 over the C6's SDIO link
  in the open air (esp-hosted-mcu, docs/design/performance.md); a 1280 x 720 picture is about 80 KB at the add-on's
  quality, a full view at 13 pictures a second about 1 MB/s. Five tiles fit the bandwidth, but esp_hosted's own SDIO
  reader drops blocks under sustained inbound load (espressif/esp-hosted-mcu #197, open), which the 20 s watchdog now
  survives as a failed call. The add-on already paces every stream by back-pressure (the newest frame once the socket
  drained), so a slow screen gets fewer pictures, never older ones.
- **Memory is a policy, not a leak.** ESPHome builds the P4 with `CONFIG_SPIRAM_USE_CAPS_ALLOC`, so every plain
  allocation takes internal RAM and each consumer must be moved to PSRAM by hand (`roomy.h`, `psram_task.h`, the
  esp_hosted and lwIP options). Espressif's own P4 default is `SPIRAM_USE_MALLOC` with an internal threshold. That is
  the structural fix and an ESPHome-wide one.

## 2. The order

1. **The hardware layer** (section 3): one P4 core file, the chip facts back in the engine, a capability record the
   firmware, the add-on, the editor and the plugins all read, and an audio owner. Future-proof work, mostly moves.
2. **Memory and cameras with limits** (section 4): a cap on live tiles that three places share, a memory governor
   that stops a stream before the heap runs out, the soak that measures the SDIO link, and then the allocation policy.
3. **The doorbell's talk-back** (section 5): prepared, not built. The audio owner from step 1 and an uplink the add-on
   bridges are the whole preparation.

Each step lands on dev in pieces its testers can run, with a CHANGELOG line where a user would notice.

## 3. The hardware layer

### 3.1 One P4 core file

`packages/hardware/esp32p4-core.yaml` holds what every P4 shares whatever its radio wiring and panel: the chip, its
frequency and revision switch, PSRAM mode and speed, `execute_from_psram`, the watchdog, and the whole `sdkconfig_options`
block (esp_hosted mempool, PSRAM task stacks, `MEMPOOL_PREFER_SPIRAM`, thread-local pointers). `esp32p4-c6.yaml` and
`m5stack-tab5.yaml` include it and keep only their pins, SDIO frequency and LDO. A Python test asserts that every board
whose variant is esp32p4 reaches the core file, so the next P4 cannot miss it.

Done when: the Tab5 builds from the core file with no sdkconfig of its own, and `tests/test_live_view.py` (or a new
`test_boards.py`) fails on a P4 board file without it.

### 3.2 The chip facts back behind the seam

- `picture_engine::limits()` returns what today sits in `live_view.h` and `glass.h`: the burst bytes, the largest
  picture and tile picture, the scale steps (sixteenths), the largest side the decoder takes. The software engine
  returns zeros, and `live_view` and `glass` read the record instead of constants.
- `glass.h` loses `PPA_BURST_BYTES`; `glass_dsi.cpp` asks the engine.
- `PICTURE_ARCHITECTURE.md` section 2.4 (the table of every hardware fact and where it is read) is brought up to date
  (the panel wrap lives in `glass_dsi.cpp` now) and gains the rule: a chip fact is read from `picture_engine::limits()`
  or a wrap, never from a header the shared code includes.

Done when: `grep -rn "PPA\|sixteenth\|MIPI\|JPEG" components/smart_display/*.h` outside `picture_engine*.h`,
`ppa_guard.h`, `picture_jpeg.h` and `glass_dsi.cpp` finds comments only, and a test keeps it so (the way
`test_live_view.py` keeps `DIAGNOSTIC_WRAPS` to what `abort_record.cpp` defines).

### 3.3 One capability record

Today the hello sends "live" and "doorbell" when `live_view::available()` is true, `boards.json` says `camera` per
board, and plugins guess. One record replaces the three:

```
struct Caps {           // hw.h, read at boot, constant after
  bool live_video;      // picture_engine::JPEG && glass::direct()
  bool speaker, microphone, echo_cancel, camera;
  uint8_t live_tiles;   // the most live tiles this board runs at once (section 4.1)
  uint32_t psram_bytes, internal_bytes;
};
const Caps &caps();
```

Built (branch platform-core, 2026-10-25) as two halves. The build's half is `hw_build.py`: it reads the configuration
ESPHome validated and defines `HW_SPEAKER`, `HW_MICROPHONE`, `HW_ECHO_CANCEL` and `HW_CAMERA` when a component of that
kind is in it, wherever it came from (a part file, a feature, an override); it reads no id, no board name, no chip. The
boot's half is what only the running screen knows: `live_view::available()` and the two heaps. `hw::resolve()` puts
them together, pure and tested on a PC (`tests/test_hw.cpp`); `hw::caps()` resolves once on its first call.

- The hello carries the record as `caps` beside the features; "live" and "doorbell" stay for an app that reads only
  those. The add-on reads it in `page_delivery.caps_of`, with `live` from the features for an older firmware and
  `None` for a count it did not give, so an older screen is taken for what it says, never for more.
- The plugin API (0.19) gets `hw::caps()`, read-only, through `plugin_api.h`. `ui::live()` keeps degrading to stills,
  but a plugin can now say why on its own card.
- Still to do: the editor reads `caps.live_tiles` for its text and badge (section 4.1), and `boards.json` stays for
  screens that have not said hello yet.

Done when: the add-on's `live_pace`, the doorbell's feature check and the editor's live-tile count all read the hello,
and `FIRMWARE_API.md` documents `hw::caps()` with the rule that a plugin never infers hardware from a board name.

### 3.4 An audio owner

Built (branch platform-core, 2026-10-25): `audio_owner.h`, pure and host-tested (`tests/test_audio_owner.cpp`), one
owner of the loudspeaker (OUT) and the microphone (IN), the way iOS owns a device's audio session: whoever wants a
side claims it in a role, hears `lost` when a higher role takes it, and `regained` when that role gives it back, if it
asked to wait.

```
enum class Role { MEDIA, CAMERA_SOUND, VOICE, DOORBELL };   // lowest first
bool claim(Side side /* OUT, IN, BOTH */, const Claim &c);  // role, id, lost, regained
void release(Side, Role, id);
Role holder(Side); bool held(Side); bool would_grant(Side, Role);
```

The ranking as Max set it (2026-10-25), highest first: a doorbell's ring (and later its talk) takes everything and
nothing takes it; the voice assistant takes the speaker from a camera's sound and gives it back after the session; a
camera's sound takes the speaker from music, which does not start again by itself; short sounds (the tap, a plugin's
click) and Home Assistant's announcements never claim, they mix on the announcement input over whatever plays. The
same role replaces its holder (a second camera's full view takes over from the first).

- The core's own claims: `live_view.cpp` claims the speaker for a full view with sound in the role its opener gives
  (`live_view::open`'s `role`): CAMERA_SOUND for a camera someone opened (`lost` holds the sound back as a mute does,
  `regained` lets it through again), DOORBELL for the view a ring opened (`runtime_tiles` `live_begin` reads
  `doorbell::shown()`), so the ring's own camera sound plays and nothing takes the speaker from it; the ring itself
  claims nothing, its chime mixes on the announcement input. `features/audio-player.yaml` claims MEDIA through the
  media player's `on_play` (its `lost` pauses it; music that starts while a higher role holds the speaker is paused at
  once) and releases on `on_pause` and `on_idle`.
- The plugin API (0.19) offers it as `tessera::audio`. The voice plugin claims VOICE IN while its wake word listens
  (`lost` stops the wake word, `regained` starts it) and BOTH for a session, on tessera-plugins dev.
- The mixer and the half-duplex interval stay as they are: the owner decides who may start; the mixer mixes the
  announcement input over the media input as before.
- Still to do: the IN side for the doorbell's talk (section 5). Proven on the D1001 (2026-10-25): music under a ring
  (the ring takes the speaker, the music stays paused after it), music started during a ring (refused, paused within a
  second), a ring while the wake word listens (the wake word rests during the ring, and "Okay Nabu" opened a session
  right after it, 12:46).
  Found then, and kept: a camera's stream stops under every popup (LVGL draws over its room), so a voice session's
  popup ends the camera's sound with its stream, and the view asks again and claims the speaker anew after it. Whether a
  stream may go on under a popup is a question of its own, for later.
- A ring's view claims the speaker and the microphone (live_view, role DOORBELL, both sides), so the wake word rests
  during a ring and listens again after it ("doorbell took the microphone from voice", "doorbell gave the microphone
  back: voice has it again"; the voice plugin's claims, voice_assist 0.4.0). Open: the claim lives with the ring's
  stream, so a stream that breaks and opens again claims anew (the same role replaces itself, harmless), and one that
  ends while the ring's view stays (stills after three breaks) lets the wake word back under an open ring. The clean
  way is module_doorbell holding the claim for as long as the ring is up, which waits for the talk's design (section 5).

### 3.5 Allocation as a seam

`roomy.h` and `psram_task.h` are the memory layer of the shared code. The rule, written in `PICTURE_ARCHITECTURE.md`
section 8 and kept by a grep test: feature code (`live_view`, `picture_fetch`, modules, plugin host) calls `roomy` and
`psram_task`, never `heap_caps_*` or `xTaskCreate*` directly; only the two headers and the engines may. The test lists
the files allowed to say `heap_caps` so a new one fails the check until it is reviewed.

### 3.6 The screen's own camera

Screen camera's video encoder registers its own PPA client and hard-codes the burst length. It should use
`picture_engine::client()` and a `convert()` operation the engine gains for YUV input, so the one-2D-DMA-user rule and
the burst fix hold for it too. Low urgency: it works and the wraps already catch its calls. Noted so the plugin API
grows the right way.

### 3.7 Declare below, resolve above

More boards come every few months, so the layering is the product. The rule, from the bottom up, each layer knowing
only the one under it:

1. **The board file declares parts and pins.** `packages/boards/<board>.yaml` says "the microphone is an ES7210 on
   these I2S pins, the speaker an ES8311 on these, the camera an SC202CS on this I2C bus, the panel this size at this
   DPI" and nothing else: substitutions and includes, no logic, no ids of its own (AGENTS.md: a board file holds
   hardware and sizes only). Today the audio transports are chosen by hand (`TAB5_AUDIO_TRANSPORT`,
   `P4_AUDIO_TRANSPORT`, `es7210-aec.yaml`, `guition-p4-audio.yaml`); the board should name the part and the part file
   should know its transport.
2. **A part file resolves a part to ESPHome components.** `packages/hardware/parts/<part>.yaml`, one per codec, sensor
   or panel (`es7210.yaml`, `es8311.yaml`, `sc202cs.yaml`, …), turns the declaration into the components and binds them
   to the fixed ids the layer above expects: `ts_microphone`, `ts_speaker_out`, `ts_camera`, and a few facts as
   substitutions (`AUDIO_MIC_AEC: true`, `AUDIO_SAMPLE_RATE`). A part that brings echo cancellation declares it here.
3. **The feature layer builds on the ids alone.** `features/audio.yaml`, `audio-player.yaml`, `camera.yaml` never see a
   pin or a chip: they wire `ts_microphone` into the mixer, the resampler, the owner (3.4) and the plugins' ids. A board
   without a part leaves an id unbound, and the feature file is skipped by the board's includes, as `features/audio.yaml`
   already is.
4. **The firmware resolves what exists at boot** into `hw::caps()` (3.3) and the top layer says "the microphone of this
   board": `audio_owner::claim(Role::VOICE, Side::IN)`, `picture_engine::decode`, `glass::reserve`. No module and no
   plugin knows a chip name, a pin or a transport.

What this needs beyond 3.1 to 3.4: a `parts/` directory with the codecs and sensors the boards use today (five audio
parts, three camera sensors, the panel drivers), each board file rewritten to declarations only, and a Python test
that reads every board file and fails on anything but substitutions and includes. That test is a ratchet: it lists,
per top-level key a board file may still hold, the boards that hold it (`display: [tab5, wavesharep4, ...]`), fails on a
key or a board that is not listed, prints what is left when it passes, and a key leaves the list once its boards are
empty. Audio and the camera become parts first, with the Tab5 and the Waveshare set as the proof that a board builds to
the same sdkconfig and components from declarations; the panels and touch come last, because their files carry each
board's LVGL settings today and move only once those settings have a part of their own. `PROFILES.md` describes the
layering; it gains the part layer and the rule "declare below, resolve above". `ADDING_A_BOARD.md` then says: name the
parts, give the pins, done.

## 4. Memory and cameras with limits

### 4.1 A cap on live tiles that three places share

Four live tiles a screen, plus the full view (which halts the tiles anyway), on every P4 board; a fifth live tile on a
page shows the camera's still with a small "live paused" badge and a line in its settings ("This screen plays 4 cameras
live at once; the others show a picture every 15 seconds. With two cameras, each one runs more smoothly than with
four."). The four are what is on the glass: a live tile streams only while its page is open, so it is four on the page
the screen shows, and the other pages cost nothing. Two tiles run at 12 to 14 pictures a second each, four at 4 to 5
(the D1001, 2026-10-25): one PPA lays out every picture. The number lives in one place, the firmware's
`hw::caps().live_tiles`, and reaches:

- the firmware's `live_tiles_round`, which starts the first four by tile order and leaves the rest at
  `DEFAULT_PACE`;
- the add-on, which reads it from the hello and refuses a fifth stream of that screen with the 503 it already has for
  the global eight, and sets the editor's text;
- the editor, which shows the count on the page and the badge on the fifth tile as it is placed.

Why four: the soak numbers (two tiles plus the video held 175 KB internal RAM free with no failed allocation) leave room
for two more; the SDIO link carries it; and a user who reads "4" trusts the screen more than one who finds out which
fifth tile silently stopped. `MAX_STREAMS` stays seven for a page of four tiles, the full view and plugin places; it is
a ceiling, not the cap.

### 4.2 The source scales, not the screen

The add-on knows every room it serves. Two rules it gains, both in `live_pictures`/`live_stream`:

- A tile room smaller than a quarter of the panel gets its pictures at most 8 a second, the full view at the camera's
  pace. The screen already shows what it gets; the add-on simply makes fewer.
- A screen's streams together stay under a byte budget per second it states in its hello (the P4's: 1.5 MB/s, half the
  measured TCP ceiling). Over it, the add-on lowers tile pictures a second first, then their JPEG quality (QSCALE 5 to
  8), never the full view's.

Done when: five tiles on a D1001 stream within the budget and `heap_watch` logs no failed allocation over an hour.

Open, for the add-on: a camera's source that falls far behind. On 2026-10-25 at 14:23 camera.max fell up to 10 s
behind ("Live camera.max video: 1.09 s behind the camera on average, 9.88 s at most"); its two live tiles on the D1001
went quiet, and the add-on answered their next streams 503 while the source connected again ("first WebRTC frame
9.9 s"). The screen did as it should (stills at the tile's pace). Whether the add-on should cut a source that falls
that far behind and start it again, and why WebRTC took ten seconds to give its first frame, is to be seen.

### 4.3 A memory governor

`heap_watch` sees the internal low-water mark every 250 ms. Instead of logging alone, it offers one action: when the
lowest since boot falls under 48 KB, `live_tiles_round` stops the newest live tile and marks it paused with the badge;
under 32 KB it stops the next; the full view is never stopped. A tile stopped this way starts again after five minutes
with room. The thresholds are constants in `heap_watch.h` with the measurements beside them, and the CHANGELOG says a
screen now pauses a camera before it would restart.

### 4.4 The soak that measures the link

Before the release candidate goes out, on a D1001 with four live tiles, the screen's own video and the voice assistant
in use, 24 to 48 hours, counted per hour: esp_hosted drops ("task still writing Rx data to queue"), RPC timeouts,
watchdog and abort restarts, the task count and both heaps (heap_watch's five-minute line). Then the same with
`CONFIG_ESP_HOSTED_USE_MEMPOOL` back on beside `MEMPOOL_PREFER_SPIRAM`, so the pool sits in PSRAM and every packet
stops going through `heap_caps_aligned_alloc`. The configuration with the lower drop count is the release's. The three
failure paths of `psram_task` are forced once in a test build (a start without PSRAM room, a download that overflows
its stack, a stream that ends while the main loop is in a long lay-out), and the lifecycle gets a host test.

### 4.4a The PSRAM bus is shared with the panel

PSRAM is not free memory: it is one bus, and the panel reads its frame buffer out of it every refresh (glass.h, "The
panel's share of the memory bus"; `glass::misses()` counts the frames it ran dry). The camera's frames, the JPEG
decoder, the PPA, the H.264 encoder and lwIP's copies all ride the same bus, and when it is full the picture tears
before anything else fails (Max, 2026-10-25, from the encoder's strip in PSRAM). So "to PSRAM" is a choice per buffer,
not a default:

- A buffer that is read or written in a tight loop many times a second, or by a DMA that must not wait (the encoder's
  reference strip, a stream buffer's control block, a task's hot stack) stays inside the chip, as long as it is small.
- A buffer that is large and touched once per frame or less (pictures, frame copies, documents, strings, the streams'
  read buffers) goes to PSRAM.
- Every move to PSRAM is measured against `glass::misses()` under load (four live tiles and the video) as well as
  against internal RAM: a move that frees 90 KB and costs frames is not taken.

Measured on the reTerminal D1001 (2026-10-25, two live tiles and three RTSP viewers of the screen's own video, the
same scene twice): with the H.264 encoder's reference strip inside the chip the panel ran dry 0 times, a picture's
lay-out took 60 to 63 ms, the video ran at 12 to 13 pictures a second, and about 243 KB of internal RAM was free; with
the strip in PSRAM the panel ran dry 39 to 53 times a minute (blue frames over the live tiles), lay-out took 77 to
80 ms, and 344 KB was free. The strip stays inside the chip: 100 KB of internal RAM is the price of a panel without
blue frames. A smaller burst for the encoder's DMA with the strip in PSRAM (64 bytes, the rule every other 2D-DMA
client keeps; tried the same day) saves the panel (0 dry runs) and halves the video (encoding 105 to 130 ms a frame, 6
to 7 fps): the strip stays inside, and every wrap of the test is gone. A move that passes on a D1001 is measured on a
Guition 10.1 before it is anyone's default.

### 4.5 The allocation policy, after the release

A dev cycle of its own: build the P4 with `CONFIG_SPIRAM_USE_MALLOC`, `CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL` at 16 KB
and `CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL` at 64 KB, so strings, vectors, plugin documents and TLS land in PSRAM
without `roomy` asking. ESPHome hard-codes `SPIRAM_USE_CAPS_ALLOC` in its psram component, so this needs either an
upstream option or a sdkconfig override that the build verifies; the soak of 4.4 is the measure, LVGL's draw times the
risk. If it holds, `roomy` becomes a thin name for "large and read by the screen alone" rather than the only road to
PSRAM.

### 4.6 The whole field of view, binned in the sensor (later, after the sensing steps)

Max wants a screen's own camera to show all it sees, not its middle. Scaling a larger mode down on the P4 is not the
way: the PPA is one device for the video and the live tiles, and a forced pass of 1280 x 720 into 960 x 528 beside two
live tiles took 77 to 89 ms a frame, the video fell from 12-13 to 7-9 fps and the tiles from 12-14 to 6-9 pictures a
second (CAMERA.md, "What travels"; PICTURE_ARCHITECTURE.md 2.4). A sensor that bins 2 x 2 itself gives the whole field
in fewer bytes than today, so the bus, the ISP and the PPA get it for free. It is the one way that costs them nothing.

- The board file names the sensor and how it is mounted, nothing more.
- The sensor's driver (the part: the OV02C10 in the screen camera plugin, the SC202CS through esp_cam_sensor) gains a
  binned mode, the OV02C10's about 964 x 544 over the whole field. It is register work: the Linux driver has no such
  mode. Checked on the glass with a ruler for the field and a colour chart for the Bayer phase.
- The FrameHub picks the mode from what is wanted of the camera, as it picks the frame rate.
- `hw::caps()` names the modes a screen's camera has.
- One setting per screen, "Field of view: whole / sharp", whole by default once a binned mode exists; the editor shows
  the choice and nothing else.

The sender of TR-YVP7YV, who also runs a Guition 10.1 and asked for its whole field of view (via Max, 2026-10-25), is the first to try it on an OV02C10, before it is anyone's default.

**Step 0, the SC202CS, measured (2026-10-25, the home reTerminal D1001, a test build not committed).** We have no
register map of the SC202CS, only its kinship with SmartSens' SC200AI, whose binned 960 x 540 mode is in Rockchip's
driver; every SC202CS driver found (esp_cam_sensor 2.4.0, Seeed's own copy in its reTerminal-D1001 repository, Ingenic,
Sophgo, MediaTek) knows only 1600 x 1200 and crops of it, and none writes a skip or bin register. The test handed the sensor a format of its own through `VIDIOC_S_SENSOR_FMT` (`sc202cs_set_format` writes any table
it gets, and esp_video takes the size from it), so nothing of esp_cam_sensor was patched: the 1600 x 1200 RAW8 table,
an output size in 0x3208-0x320b, offsets 2 in 0x3211 and 0x3213, and our black level.

- Rows: 0x3215 = 0x31 halves them over the whole height, with the colours right (no cast); with 0x3220 = 0x01 as well
  the picture looked calmer, as if the rows were averaged rather than skipped.
- Columns: nothing found. 0x3214 = 0x31 (the column twin of 0x3215 on other sensors) changes nothing; the SC200AI's
  0x3220 = 0x17 with 0x5000 = 0x46, 0x5900 = 0xf1, 0x5901 = 0x04 gives comb edges and still the left 800 columns at
  full resolution. So the output was the left half of the field, squashed to half its height.
- What came for free: an output in whole macroblocks (800 x 592) is encoded without the PPA's cut (0.0 ms, against
  47.8 ms a frame for 800 x 600), at 12 fps, and the encoder holds 65 KB of internal RAM instead of 103 KB.

- The nearest relative with a binned mode is the SC2355 (chip id 0xeb2c, the same 1/5" 1600 x 1200 family):
  Rockchip's `drivers/media/i2c/sc2355.c` (rockchip-linux/kernel, develop-5.10) has
  `sc2355_1lane_10bit_360Mbps_800x600_30fps_regs`, 29 registers apart from its 1600 x 1200 table: 0x301f 0x0c, 0x303f
  0x82, the output size, offsets 2, 0x3215 0x31, 0x3220 0x01, 0x5000 0x46, 0x5900 0xf1, 0x5901 0x04, its PLL and MIPI
  timing (0x36eb, 0x36ed, 0x4819-0x4829), 0x3904 and the exposure. It is a monochrome sensor (Y10), so its column
  merging need not keep a Bayer pattern. On the SC202CS, with our own PLL and timing: with 0x301f 0x0c and 0x303f
  0x82 the sensor sends no frame at all (DQBUF timed out, recovery exhausted); without them, 0x3220 0x01 with the
  0x59xx block gives comb edges and the left 800 columns again. The colour SC200AI's binned mode changes 0x301f as
  well (0x01 to 0x4e), so 0x301f might have been the switch of the 0x59xx block: with 0x301f 0x4e and with 0x0c (no
  0x303f) the frames come at 12 fps and the picture is the same as without it. 0x301f is not that switch, and 0x303f
  0x82 alone stopped the frames.
- Five colour sensors with a binned mode in Rockchip's drivers (sc200ai, sc230ai, sc3336p, sc431hai, sc435hai) and the
  mono SC2355 use the same recipe (0x3211/0x3213 0x02, 0x3215 0x31, 0x3220 0x01 or 0x17, 0x5000 0x46, 0x5900 0xf1 or
  0x01, 0x5901 0x04, the output size) with a 0x301f of their own. A last test swept that block on the SC202CS while it
  streamed (the whole window written out, x 0 to 1607, y 0 to 1207; every 20 s one value, read back): 0x5901 over 04,
  05, 06, 07, 0c, 14, 24, 44, 84, 01, 02, 03, 08, 00; 0x5900 over 01, f5, f3, f0, 00; 0x5000 over 06, 56, 66, c6, 40,
  00. Every value read back as written, at 13 fps throughout: the block is there. 0x5000 bit 6 turns it on, 0x5901
  bit 2 brings the comb edges and a black strip on the left, 0x5900 0xf5 a pink cast (it works on the Bayer pattern).
  No value halves the columns: the picture stays the left 800 columns at full width.
- github.com/youkorr/esphome_esp-video names an SC202CS "800x600 with 2x2 binning" in its notes; its table is a centre
  crop (window 396..1203 x 296..903, no 0x3215, 0x3220 or 0x59xx), and its own header says the sensor has no binning.
  Nothing to build on.

For the OV02C10 (step 3, the Guition), two variants go to the tester behind the setting, both off until he confirms
them: (A) 2 x 2 subsampling with OmniVision's increments (odd 3, even 1 in 0x3814-0x3817), the whole window, 960 x 528
out of the 968 x 544 it leaves (skipped, not averaged: fine patterns may shimmer; averaging waits for the sensor's
binning bits); (B) the same youkorr repository's "sensor scaling": the whole window (0x3800-0x3807 =
0..1935 x 4..1091) with a smaller output and no binning (0x3814/0x3815 = 0x01), 0x4500 0x38 and 0x4501 0x18 where
its other tables keep 0x07 and 0x00, and its 0x3910, 0x3d85/0x3d8c/0x3d8d, 0x4033 and 0x4050/0x4051. B is unproven:
its own notes ask for validation on hardware and report watchdog time-outs at 960 x 540, and a raw sensor whose output
is smaller than its window usually crops rather than scales. The tester reports the field (a ruler or a known room),
the colours, the frame rate and whether the stream holds.

The test build is out (Screen camera 0.6.0 on tessera-plugins dev, 2026-10-25): the setting "Camera field of view"
(Sharp, Full (subsampling), Full (sensor scale); Sharp by default) asks the sensor's driver for the mode
(`tessera_camera_field_format`, the OV02C10's only), the capture task stops, hands the sensor the format and starts
again, and the video's encoder is made anew at the new size. A camera without the modes, and a mode that gives no
frame, go back to Sharp by themselves, and the setting shows it. The log says the mode, its size, and its frames a
second over its first ten seconds. What the tester sends: the field (a ruler, or a room he knows), the colours, those
frames a second, and whether the stream held.

Where it stands: the whole field on the SC202CS needs the columns halved in the sensor, and that register is not
known without SmartSens' datasheet. Halving them on the PPA instead (a full-width 1600 x 592) is the pass that cost
the video and the tiles half their pictures. The registers above are all the test wrote beyond esp_cam_sensor's
1600 x 1200 RAW8 table. The OV02C10 (the Guition) is the other road: its skipping increments are OmniVision's
(0x3814-0x3817).

## 5. The doorbell's talk-back, prepared

Not built now: Home Assistant is expected to bring a two-way audio path of its own within months, and the community
VoIP stack (a SIP endpoint inside Home Assistant) already bridges door stations. What the firmware prepares does not
depend on which one wins:

- **The owner.** Section 3.4: a ring claims BOTH, the wake word steps back, the camera's sound keeps playing. That is
  the "always hear the other side" rule.
- **Push to talk.** The doorbell view gets a hold-to-talk key. While held, the microphone's 16 kHz PCM goes up the
  camera port as one more stream (a POST of `audio/x-s16le;rate=16000` on `/camera/<token>/talk`), the speaker ducks
  the camera's sound (the mixer's `apply_ducking`), and echo cancellation is not needed. Released, the uplink closes.
- **The add-on bridges.** The screen never speaks to a doorbell. The add-on takes the PCM and hands it to the camera's
  back channel through go2rtc (Reolink takes G.711 there) or to Home Assistant's integration when it exists; the
  transcoding is ffmpeg's, which the add-on already has. Reolink first, because testers have one.
- **Unlock** is a Home Assistant action the add-on fires from the view's key, as the doorbell events already are.
- **One rule for sound that falls behind, at both ends** (2026-10-25). The add-on drops the oldest part when a screen's
  sound queue is full (`live_stream.SOUND_QUEUE`), and the screen leaves the oldest out when more than 400 ms waits for
  its speaker, down to 150 ms (live_view `SOUND_HIGH`, `SOUND_LOW`). A part that finds the screen's buffer full is left
  out whole, the safety net under it. A conversation at the door keeps the same rule.

Done when (later): a tester holds the key on a D1001 and is heard at a Reolink doorbell, with the doorbell's sound on
the glass before, during (ducked) and after.

## 6. What stays out

- Replacing ESPHome. Its board files, build server, override YAML, OTA and Home Assistant integration are why boards
  are cheap to add; the layer above (sections 3 and 4) is ours and stays ours.
- Ethernet or another radio as a way around the SDIO link. Boards will not have it; the link's budget is a rule the
  add-on keeps.
- H.264 on the screen. The P4 encodes, it does not decode; the add-on's JPEGs stay the format.
- A fifth live tile "when there is room". The cap is the feature.

## 7. Evidence to keep

Every step lands with its numbers in the commit message, as the P4 memory work did: the board, the date, internal RAM
free and lowest, pictures a second, drops an hour. Docs hold the rule that resulted, not the log (AGENTS.md).

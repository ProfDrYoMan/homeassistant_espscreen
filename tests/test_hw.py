"""The capability record's build half (components/smart_display/hw_build.py; docs/PLATFORM_PLAN.md 3.3): which HW_*
defines a configuration earns, that the component applies them, that the hello carries the record, and that the app
reads it (page_delivery.caps_of)."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'components' / 'smart_display'
spec = importlib.util.spec_from_file_location('smart_display_hw_build', COMPONENT / 'hw_build.py')
hw_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hw_build)
sys.path.insert(0, str(ROOT / 'screen_manager' / 'app'))
import page_delivery  # noqa: E402


class Defines(unittest.TestCase):
    def test_a_configuration_earns_its_defines_from_its_components(self):
        # The Tab5 as its parts build it: a speaker, a microphone, Espressif's AEC and the camera of Screen camera.
        tab5 = {'speaker': [{'platform': 'esp_audio_stack', 'id': 'ts_speaker_out'}, {'platform': 'mixer', 'id': 'ts_mixer'}],
                'microphone': [{'platform': 'esp_audio_stack', 'id': 'ts_microphone'}],
                'esp_aec': {'id': 'board_aec'}, 'esp_video_camera': [{'id': 'ts_camera'}], 'lvgl': [{}]}
        self.assertEqual(hw_build.defines(tab5), ['HW_SPEAKER', 'HW_MICROPHONE', 'HW_ECHO_CANCEL', 'HW_CAMERA'])
        # A Waveshare P4 on the plain I2S transport: no canceller, and no camera plugin built in.
        plain = {'speaker': [{'platform': 'i2s_audio', 'id': 'ts_speaker_out'}], 'microphone': [{'platform': 'i2s_audio'}]}
        self.assertEqual(hw_build.defines(plain), ['HW_SPEAKER', 'HW_MICROPHONE'])
        # A CYD: nothing of it.
        self.assertEqual(hw_build.defines({'lvgl': [{}], 'display': [{}]}), [])
        # An empty list is no component.
        self.assertEqual(hw_build.defines({'speaker': []}), [])

    def test_no_define_reads_a_name_a_chip_or_a_pin(self):
        source = (COMPONENT / 'hw_build.py').read_text()
        for word in ('esp32p4', 'ESP32P4', 'tab5', 'guition', 'waveshare', 'reterminal', 'GPIO', 'ts_speaker', 'ts_microphone'):
            self.assertNotIn(word, source.replace("'camera')", ''), f'hw_build.py must not know {word}')


class Wiring(unittest.TestCase):
    def test_the_component_applies_the_defines_and_the_hello_carries_the_record(self):
        source = (COMPONENT / '__init__.py').read_text()
        self.assertRegex(source, r'for define in hw_build\.defines\(CORE\.config\):\n\s+cg\.add_define\(define\)')
        core = (ROOT / 'packages' / 'core.yaml').read_text()
        self.assertIn('hw::hello(root);', core)
        # The record is written after the plugins' part of the hello and beside the features an older app reads.
        self.assertLess(core.index('plugin_host::hello(root);'), core.index('hw::hello(root);'))
        self.assertIn('if (live_view::available()) features.add("live");', core)
        header = (COMPONENT / 'hw.cpp').read_text()
        for key in ('"live"', '"speaker"', '"microphone"', '"echo_cancel"', '"camera"', '"live_tiles"', '"psram_kb"', '"internal_kb"'):
            self.assertIn(key, header)
        self.assertIn('#include "hw.h"', (COMPONENT / 'smart_display.h').read_text())
        self.assertIn('#include "hw.h"', (COMPONENT / 'plugin_api.h').read_text())

    def test_the_cpp_test_is_in_the_runner(self):
        self.assertTrue((ROOT / 'tests' / 'test_hw.cpp').exists())


class AppSide(unittest.TestCase):
    def test_the_app_reads_the_record_and_falls_back_on_the_features(self):
        answer = {'features': ['live', 'doorbell', 'pictures'],
                  'caps': {'live': True, 'speaker': True, 'microphone': False, 'echo_cancel': False, 'camera': True,
                           'live_tiles': 4, 'psram_kb': 32768, 'internal_kb': 768}}
        caps = page_delivery.caps_of(answer)
        self.assertEqual(caps['live'], True)
        self.assertEqual(caps['live_tiles'], 4)
        self.assertEqual(caps['speaker'], True)
        self.assertEqual(caps['microphone'], False)
        self.assertEqual(caps['psram_kb'], 32768)
        # An older firmware sends no record: live follows the feature, the counts are unknown (None), the rest False.
        old = page_delivery.caps_of({'features': ['live', 'pictures']})
        self.assertEqual(old['live'], True)
        self.assertIsNone(old['live_tiles'])
        self.assertEqual(old['speaker'], False)
        older = page_delivery.caps_of({'features': ['pictures']})
        self.assertEqual(older['live'], False)
        # Garbage is no record.
        self.assertEqual(page_delivery.caps_of({'features': ['live'], 'caps': 'yes'})['live'], True)
        self.assertEqual(page_delivery.caps_of({'caps': {'live_tiles': 'many', 'live': 1}})['live_tiles'], None)

    def test_the_senders_record_follows_its_hello(self):
        source = (ROOT / 'screen_manager' / 'app' / 'page_delivery.py').read_text()
        self.assertIn('caps = caps_of(answer)', source)
        self.assertIn('self.caps = caps', source)
        self.assertRegex(source, r'self\.caps = caps_of\(\{\}\)')
        # One line in the log when a screen's record changes, so every report has it (a reTerminal D1001 on glass,
        # 2026-10-25, where nothing showed what the app had read).
        self.assertIn('LOG.info("%s: %s", self.name or "A screen", caps_line(caps))', source)

    def test_the_record_in_one_line(self):
        caps = page_delivery.caps_of({'caps': {'live': True, 'speaker': True, 'microphone': True, 'echo_cancel': False,
                                               'camera': True, 'live_tiles': 4, 'psram_kb': 28272, 'internal_kb': 519}})
        self.assertEqual(page_delivery.caps_line(caps), 'live, 4 live tiles, speaker, microphone, camera, no echo '
                         'cancellation, 28272 KB PSRAM, 519 KB internal RAM')
        self.assertEqual(page_delivery.caps_line(page_delivery.caps_of({'features': ['pictures']})), 'no echo cancellation')

    def test_the_editor_gets_the_record(self):
        # The inventory's screen carries it (server.py, caps_of_screen) and the editor marks a live camera beyond
        # caps.live_tiles (web/src/model/live-tiles.ts, tests/live-tiles.spec.ts).
        source = (ROOT / 'screen_manager' / 'app' / 'server.py').read_text()
        self.assertIn("screen['caps'] = manager.caps_of_screen(screen)", source)
        model = (ROOT / 'web' / 'src' / 'model' / 'live-tiles.ts').read_text()
        self.assertIn('export function pausedLive(', model)

    def test_the_apps_503_is_a_safety_net(self):
        # A screen that reads its own record streams at most caps.live_tiles at once (runtime_tiles live_tiles_round), so
        # the app's 503 for a stream beyond them (live_stream.py) is never reached by such a firmware: it is the net for
        # one that does not cap, and no proof on glass can show it.
        source = (ROOT / 'screen_manager' / 'app' / 'live_stream.py').read_text()
        self.assertIn("return web.Response(status=503, text=f'This screen plays {cap} cameras live at once')", source)


if __name__ == '__main__':
    unittest.main()

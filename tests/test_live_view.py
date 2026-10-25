"""The ESP32-P4's build facts (components/smart_display/p4_build.py; docs/PICTURE_ARCHITECTURE.md, section 3.9): the
network options a P4 gets less the screen's own, which picture engine a build gets from the chip and LVGL (never a
board), and that the wraps the P4 engine and the glass need are the ones the build adds and the ones the code defines."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'components' / 'smart_display'
spec = importlib.util.spec_from_file_location('smart_display_p4_build', COMPONENT / 'p4_build.py')
p4_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p4_build)


class P4Build(unittest.TestCase):
    def test_the_component_applies_them_on_a_p4_alone(self):
        # The P4 boards build with the larger TCP window and the live view, keyed on the chip in the component.
        source = (COMPONENT / '__init__.py').read_text()
        facts = (COMPONENT / 'p4_build.py').read_text()
        self.assertIn('"CONFIG_LWIP_TCP_WND_DEFAULT": 65534', facts)
        self.assertRegex(source, r'if _esp32_p4\(\):\n\s+await _p4\(config\)')
        self.assertIn('return get_esp32_variant() == VARIANT_ESP32P4', source)
        # The engine's wraps come from the table: the panel as ESP-IDF makes it and every PPA burst shorter
        # (glass_dsi.cpp), a turned copy in pieces (ppa_guard.cpp).
        for wrap in ('esp_lcd_new_panel_dpi', 'ppa_register_client', 'ppa_do_scale_rotate_mirror'):
            self.assertIn(f'"{wrap}"', facts)
        self.assertIn('cg.add_build_flag(f"-Wl,--wrap={wrap}")', source)
        # The main loop's waits for the C6's answers (5 s each in esp_hosted) must fit the task watchdog, or a busy SDIO
        # link restarts the screen instead of failing a call (2026-10-24, the D1001, a Tab5 and a Guition 10.1).
        hardware = (ROOT / 'packages/hardware/esp32p4-core.yaml').read_text()
        self.assertRegex(hardware, r'(?m)^esp32:\n(?:  .*\n)*?  watchdog_timeout: 20s\n', 'every P4 board waits 20 s on its main loop')
        # A screen's own sdkconfig_options keep the last word.
        self.assertIn('if name not in own', facts)

    def test_the_screens_own_options_keep_the_last_word(self):
        self.assertEqual(p4_build.network_options({}), p4_build.P4_NETWORK)
        own = {'CONFIG_LWIP_TCP_WND_DEFAULT': '32768', 'CONFIG_SOMETHING_ELSE': 'y'}
        got = p4_build.network_options(own)
        self.assertNotIn('CONFIG_LWIP_TCP_WND_DEFAULT', got)
        self.assertEqual(got['CONFIG_LWIP_TCP_SND_BUF_DEFAULT'], 65534)
        self.assertTrue(got['CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP'])

    def test_the_engine_follows_the_chip_and_lvgl_not_a_board(self):
        self.assertEqual(p4_build.picture_engine('esp32p4', True), 'p4')
        self.assertEqual(p4_build.picture_engine('esp32p4', False), 'software')
        for variant in ('esp32', 'esp32s3', 'esp32c3', 'host'):
            self.assertEqual(p4_build.picture_engine(variant, True), 'software')
        self.assertEqual(p4_build.ENGINES['p4']['define'], 'USE_LIVE_VIEW')
        self.assertIsNone(p4_build.ENGINES['software']['define'])
        self.assertEqual(p4_build.ENGINES['software']['wraps'], ())

    def test_the_wraps_are_the_engines_and_the_glasss(self):
        # __init__.py adds the wraps the P4 engine names, from the table and nowhere else; glass_dsi.cpp and
        # ppa_guard.cpp define them, and no file defines one the table does not name.
        init = (COMPONENT / '__init__.py').read_text()
        self.assertIn('for wrap in engine["wraps"]:', init)
        self.assertIn('cg.add_build_flag(f"-Wl,--wrap={wrap}")', init)
        self.assertEqual(re.findall(r'-Wl,--wrap=(\w+)', init), [])
        defined = set()
        for name in ('glass_dsi.cpp', 'ppa_guard.cpp', 'live_view.cpp', 'picture_engine_p4.cpp'):
            defined |= set(re.findall(r'__wrap_(\w+)\(', (COMPONENT / name).read_text()))
        self.assertEqual(defined, set(p4_build.ENGINES['p4']['wraps']))
        # The diagnostic wraps every ESP32 gets come from their own table, and abort_record.cpp defines exactly those.
        self.assertIn('for wrap in DIAGNOSTIC_WRAPS:', init)
        diagnostic = set(re.findall(r'__wrap_(\w+)\(', (COMPONENT / 'abort_record.cpp').read_text()))
        self.assertEqual(diagnostic, set(re.search(r'DIAGNOSTIC_WRAPS = \(([^)]*)\)', init)[1].replace('"', '').replace(',', ' ').split()))
        # The define the engine builds under is the one the headers test.
        for name in ('live_view.h', 'glass.h', 'picture_engine.h'):
            self.assertIn(f"defined({p4_build.ENGINES['p4']['define']})", (COMPONENT / name).read_text(), name)


class Sockets(unittest.TestCase):
    """A P4 screen counts its streams and downloads into ESPHome's socket total (smart_display/__init__.py, _sockets):
    the count follows live_view.h's MAX_STREAMS, and the downloads are picture_fetch's three slots."""

    def test_the_count_follows_the_code(self):
        init = (COMPONENT / '__init__.py').read_text()
        streams = re.search(r'constexpr int MAX_STREAMS = (\d+);', (COMPONENT / 'live_view.h').read_text())
        self.assertIn(f'P4_STREAM_SOCKETS = {streams[1]}', init)
        slots = re.findall(r'^Slot &(\w+)\(\);', (COMPONENT / 'picture_fetch.h').read_text(), re.M)
        self.assertIn(f'PICTURE_SOCKETS = {len(slots)}', init)
        self.assertIn('FINAL_VALIDATE_SCHEMA = _sockets', init)


class ChipFactsBehindTheEngine(unittest.TestCase):
    """The P4's facts live behind the picture engine (docs/PLATFORM_PLAN.md 3.2): outside the engine's own files the
    code names no sixteenths, no PPA, no JPEG driver and no MIPI panel; it asks picture_engine (limits, fit, cut, blit)
    and glass. Comments and strings may name them."""
    ALLOWED = {'picture_engine.h', 'picture_engine_p4.cpp', 'ppa_guard.h', 'ppa_guard.cpp', 'picture_jpeg.h',
               'glass_dsi.cpp'}
    RULES = {'sixteenths': r'\bsixteenths\b', 'PPA': r'\bppa_\w+\s*\(|\bPPA_[A-Z0-9_]+',
             'JPEG driver': r'\bjpeg_(?:decoder|decode|alloc|new|del)\w*', 'MIPI': r'\bmipi\w*|\bMIPI\w*|\besp_lcd_\w+'}

    @staticmethod
    def code(text):
        text = re.sub(r'//[^\n]*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)

    def test_no_chip_fact_outside_the_engine(self):
        found = []
        for path in sorted(COMPONENT.glob('*')):
            if path.suffix not in ('.h', '.cpp') or path.name in self.ALLOWED:
                continue
            body = self.code(path.read_text())
            for name, rx in self.RULES.items():
                found += [f'{path.name}: {name} ({m.group(0)})' for m in re.finditer(rx, body)]
        self.assertEqual(found, [])



class GlassTurns(unittest.TestCase):
    """The glass is a mutex FreeRTOS gives to its highest waiter (glass.h): a still's download, at priority 1, waited
    for it behind four live tiles for up to two minutes (a reTerminal D1001, 2026-10-25, "11011 ms waiting for the
    glass, 2 ms holding it"). Every writer waits at one priority."""

    def test_a_still_waits_at_the_streams_priority(self):
        self.assertIn('constexpr unsigned WRITER_PRIORITY = 5;', (COMPONENT / 'glass.h').read_text())
        live = (COMPONENT / 'live_view.cpp').read_text()
        self.assertIn('"live_view", STACK_BYTES, s, glass::WRITER_PRIORITY, 1)', live)
        engine = (COMPONENT / 'picture_engine_p4.cpp').read_text()
        decode = engine[engine.index('uint8_t *decode('):engine.index('// ---- The PPA ----')]
        # Raised before the wait, held only around the glass, and back at its own once it let go.
        order = ['vTaskPrioritySet(nullptr, glass::WRITER_PRIORITY);', 'glass::hold();', 'glass::release();',
                 'vTaskPrioritySet(nullptr, own);']
        self.assertEqual([decode.index(step) for step in order], sorted(decode.index(step) for step in order))
        # A slow still says where its time went.
        fetch = (COMPONENT / 'picture_fetch.cpp').read_text()
        self.assertIn('waiting for the glass, %u ms holding it)', fetch)


if __name__ == '__main__':
    unittest.main()

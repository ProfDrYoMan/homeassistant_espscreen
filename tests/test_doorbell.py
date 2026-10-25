"""A doorbell's ring on the screens (docs/DOORBELL.md): an automation fires esp_screens_doorbell with the doorbell's camera
and the screens it names; a screen that draws a doorbell opens the camera live, any other gets its alert; the first that
answers closes it on the others, a ring nobody answered shows in the top bars, and the app fires one answered or missed
event per ring. The Events page's wizard puts such an automation into Home Assistant (automations.py)."""
import asyncio
import importlib.util
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'screen_manager/app'))
import automations  # noqa: E402
import doorbell  # noqa: E402

HAS_AIOHTTP = importlib.util.find_spec('aiohttp') is not None
if HAS_AIOHTTP:
    from server import Manager


class TheEvent(unittest.TestCase):
    def test_its_fields(self):
        self.assertEqual(doorbell.request({'camera': 'camera.front_door', 'title': 'Front door', 'timeout': '45'}),
                         ('camera.front_door', 'Front door', 45, []))
        self.assertEqual(doorbell.request({'camera': 'camera.front_door'}), ('camera.front_door', '', doorbell.TIMEOUT, []))
        self.assertEqual(doorbell.request({'camera': 'camera.x', 'timeout': 5})[2], doorbell.TIMEOUT_MIN)
        self.assertEqual(doorbell.request({'camera': 'camera.x', 'timeout': 9999})[2], doorbell.TIMEOUT_MAX)
        self.assertEqual(doorbell.request({'camera': 'camera.x', 'timeout': 'soon'})[3], ['timeout'])

    def test_no_camera_no_ring(self):
        for data in ({}, {'camera': 'image.front_door'}, {'camera': ['camera.x']}, None):
            self.assertIsNone(doorbell.request(data)[0], data)

    def test_its_name(self):
        self.assertEqual(doorbell.name_of('camera.front_door', 'Front door', {}), 'Front door')
        self.assertEqual(doorbell.name_of('camera.front_door', None, {'camera.front_door': {'attributes': {'friendly_name': 'Porch'}}}), 'Porch')


class Suggestions(unittest.TestCase):
    # Only an offer for the wizard: the doorbells Home Assistant knows as such, each with the camera on its device.
    def test_doorbells_home_assistant_knows(self):
        states = {'event.front_door_doorbell': {'state': '2026-10-22T10:07:52+00:00', 'attributes': {'device_class': 'doorbell', 'friendly_name': 'Front door Doorbell'}},
                  'event.button_press': {'state': 'unknown', 'attributes': {'device_class': 'button'}},
                  'binary_sensor.porch_visitor': {'state': 'off', 'attributes': {'friendly_name': 'Porch Visitor'}},
                  'binary_sensor.porch_person': {'state': 'off', 'attributes': {}},
                  'binary_sensor.g4_doorbell': {'state': 'off', 'attributes': {'friendly_name': 'G4 Doorbell'}},
                  'binary_sensor.other_visitor': {'state': 'off', 'attributes': {}},
                  'camera.front_door': {'state': 'idle'}, 'camera.porch_fluent': {'state': 'idle'}}
        registry = {'binary_sensor.porch_visitor': {'platform': 'reolink', 'translation_key': 'visitor'},
                    'binary_sensor.porch_person': {'platform': 'reolink', 'translation_key': 'person'},
                    'binary_sensor.g4_doorbell': {'platform': 'unifiprotect', 'translation_key': 'doorbell'},
                    'binary_sensor.other_visitor': {'platform': 'someone_else', 'translation_key': 'visitor'}}
        devices = {'event.front_door_doorbell': [{'entity_id': 'camera.front_door'}],
                   'binary_sensor.porch_visitor': [{'entity_id': 'camera.porch_clear', 'disabled_by': 'user'}, {'entity_id': 'camera.porch_fluent'}]}
        self.assertEqual(doorbell.suggestions(states, registry, lambda entity: devices.get(entity, [])), [
            {'trigger': 'binary_sensor.g4_doorbell', 'kind': 'on', 'camera': None, 'name': 'G4 Doorbell'},
            {'trigger': 'binary_sensor.porch_visitor', 'kind': 'on', 'camera': 'camera.porch_fluent', 'name': 'Porch Visitor'},
            {'trigger': 'event.front_door_doorbell', 'kind': 'doorbell', 'camera': 'camera.front_door', 'name': 'Front door Doorbell'}])

    def test_how_an_entity_starts_it(self):
        self.assertEqual(doorbell.trigger_kind('event.x', {'device_class': 'doorbell'}), 'doorbell')
        self.assertEqual(doorbell.trigger_kind('event.x', {}), 'event')
        self.assertEqual(doorbell.trigger_kind('binary_sensor.x'), 'on')
        self.assertEqual(doorbell.trigger_kind('input_button.x'), 'press')
        self.assertIsNone(doorbell.trigger_kind('light.x'))


class TheRings(unittest.TestCase):
    def test_answered_on_one_screen_closes_it_on_the_others_once(self):
        rings = doorbell.Doorbells()
        key = rings.start('camera.front_door', 'Front door', 45, ['a', 'b'], ['cyd'])
        message = rings.message(key)
        self.assertEqual({k: message[k] for k in ('op', 'e', 'n', 'k', 't')},
                         {'op': 'doorbell', 'e': 'camera.front_door', 'n': 'Front door', 'k': key, 't': 45})
        self.assertEqual(rings.open(), [key])
        self.assertEqual(rings.answered(key), (['a', 'b'], ['cyd']))
        self.assertEqual(rings.answered(key), ([], []), 'the second screen that answers closes nothing')
        self.assertEqual(rings.open(), [])
        self.assertFalse(rings.unanswered(key), 'an answered ring is not missed')
        self.assertEqual(rings.message(key, close=True), {'v': 1, 'op': 'doorbell', 'k': key, 'c': 1})

    def test_missed_until_seen_or_a_later_ring_is_answered(self):
        rings = doorbell.Doorbells()
        first = rings.start('camera.front_door', 'Front door', 60, ['a'])
        self.assertIsNone(rings.bar_item())
        self.assertTrue(rings.unanswered(first))
        self.assertEqual(rings.bar_item(), {'k': 'ago', 'i': 'F12E6', 'e': rings.ring(first)['at'], 'b': 1})
        rings.answered(rings.start('camera.front_door', 'Front door', 60, ['a']))
        self.assertIsNone(rings.bar_item(), 'answering the next ring of that door clears it')
        rings.unanswered(rings.start('camera.front_door', 'Front door', 60, ['a']))
        self.assertTrue(rings.seen())
        self.assertFalse(rings.seen())

    def test_the_item_goes_first_within_the_bar_limit(self):
        item = {'k': 'ago', 'i': 'F12E6', 'e': 1, 'b': 1}
        self.assertEqual(doorbell.with_missed([{'k': 'clock'}], item, 6), [item, {'k': 'clock'}])
        self.assertEqual(len(doorbell.with_missed([{'k': 'clock'}] * 6, item, 6)), 6)


class TheFirmwareReadsTheSame(unittest.TestCase):
    def test_the_op_the_report_and_the_item(self):
        receiver = (ROOT / 'components/smart_display/page_receiver.cpp').read_text()
        self.assertIn('op == "doorbell"', receiver)
        for field in ('"k"', '"c"', '"e"', '"n"', '"r"', '"t"'):
            self.assertIn(f'root[{field}]', receiver)
        self.assertIn('value["b"]', receiver)
        core = (ROOT / 'packages/core.yaml').read_text()
        self.assertIn(f'event: {doorbell.EVENT}', core)
        self.assertIn(f'features.add("{doorbell.FEATURE}")', core)
        # Standby and Back to Home wait while a ring shows (a ring after two quiet minutes closed at once without it).
        self.assertEqual(core.count('if (runtime_tiles::doorbell_holds()) return false;'), 2)
        module = (ROOT / 'components/smart_display/module_doorbell.cpp').read_text()
        for action, _ in doorbell.ENDINGS:
            self.assertIn(f'say("{action}")', module)
        clamp = re.search(r'std::max<uint32_t>\((\d+), std::min<uint32_t>\((\d+), untouched_s \? untouched_s : (\d+)\)\)', module)
        self.assertEqual(tuple(map(int, clamp.groups())), (doorbell.TIMEOUT_MIN, doorbell.TIMEOUT_MAX, doorbell.TIMEOUT))
        # No switch of its own any more: which screens get a ring is the event's `screen`.
        self.assertNotIn('ts_doorbell', (ROOT / 'packages/features/audio-player.yaml').read_text())


class TheAutomations(unittest.TestCase):
    automation = {'alias': 'Doorbell Front door', 'triggers': [{'trigger': 'doorbell.rang', 'target': {'entity_id': 'event.front_door_doorbell'}}],
                  'actions': [{'event': 'esp_screens_doorbell', 'event_data': {'camera': 'camera.front_door'}}]}

    def test_every_one_is_called_tessera(self):
        self.assertEqual(automations.checked('doorbell', self.automation)['alias'], 'Tessera: Doorbell Front door')
        self.assertEqual(automations.checked('doorbell', {**self.automation, 'alias': 'tessera:  Doorbell'})['alias'], 'Tessera: Doorbell')

    def test_only_our_events_and_our_ids(self):
        for wrong in ({**self.automation, 'actions': [{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front'}}]},
                      {**self.automation, 'actions': [{'event': 'esp_screens_show_alert'}]},
                      {**self.automation, 'triggers': []}, {**self.automation, 'alias': ''}):
            with self.assertRaises(ValueError):
                automations.checked('doorbell', wrong)
        self.assertEqual(automations.kind_of(automations.new_id('alert')), 'alert')
        self.assertIsNone(automations.kind_of('morning_lights'))
        # A button's action rides inside our event, which the app performs when the button is pressed (the alert wizard).
        buttons = {**self.automation, 'alias': 'Someone at the door', 'actions': [{'event': 'esp_screens_show_alert', 'event_data': {
            'title': 'Someone at the door', 'button_text': 'Open', 'button_color': 'green', 'action': 'lock.unlock',
            'data': {'entity_id': 'lock.front_door'}, 'button2_text': 'Not now'}}]}
        self.assertEqual(automations.checked('alert', buttons)['actions'], buttons['actions'])

    def test_the_list_by_id_and_by_name(self):
        states = {'automation.tessera_doorbell': {'state': 'on', 'attributes': {'id': 'tessera_doorbell_0a1b2c3d', 'friendly_name': 'Tessera: Doorbell'}},
                  'automation.by_hand': {'state': 'off', 'attributes': {'id': '1700000000', 'friendly_name': 'Tessera: made by hand'}},
                  'automation.other': {'state': 'on', 'attributes': {'id': '1700000001', 'friendly_name': 'Morning lights'}}}
        listed = automations.listed(states)
        self.assertEqual([(item['entity'], item['kind']) for item in listed],
                         [('automation.tessera_doorbell', 'doorbell'), ('automation.by_hand', None)])
        self.assertEqual([item['entity'] for item in automations.listed(states, 'doorbell')], ['automation.tessera_doorbell'])


class Screen:
    def __init__(self, inbox, node, features=('doorbell',), online=True):
        self.record = {'id': inbox, 'node': node, 'name': node, 'online': online, 'room': ''}
        self.sender = type('Sender', (), {'features': set(features)})()


@unittest.skipUnless(HAS_AIOHTTP, 'aiohttp')
class TheManager(unittest.TestCase):
    def manager(self, screens):
        manager = Manager.__new__(Manager)
        manager.ha = type('HA', (), {})()
        manager.ha.states = {'camera.front_door': {'state': 'idle', 'attributes': {'friendly_name': 'Front door'}}}
        manager.ha.changed = asyncio.Event()
        manager.fired = []

        async def fire(event_type, data):
            manager.fired.append((event_type, data))
        manager.ha.fire = fire
        manager.page_senders = {s.record['id']: s.sender for s in screens}
        manager.screens = lambda: [s.record for s in screens]
        manager.transport = lambda inbox, screen=None: 'v2'
        manager.page_scope = lambda inbox: {}
        manager.device_name_of = lambda entity: None
        manager.alert_cameras = {}
        manager.doorbells = doorbell.Doorbells()
        manager.sent_messages, manager.broadcasts = [], []

        async def send_auxiliary(inbox, message, action, request):
            manager.sent_messages.append((inbox, message))
            return True

        async def broadcast(event_type, data):
            manager.broadcasts.append((event_type, data))
            return {'sent': len(data.get('screen') or []), 'skipped': 0, 'failed': 0}
        manager.send_auxiliary, manager.broadcast = send_auxiliary, broadcast
        manager.doorbell_unanswered = lambda key, timeout: asyncio.sleep(0)
        return manager

    def test_a_ring_opens_the_view_on_p4s_and_the_alert_elsewhere(self):
        screens = [Screen('a', 'hall'), Screen('b', 'kitchen'), Screen('c', 'cyd', features=()), Screen('d', 'off', online=False)]
        manager = self.manager(screens)
        result = asyncio.run(manager.doorbell_show({'camera': 'camera.front_door', 'timeout': 30}))
        self.assertEqual([inbox for inbox, _ in manager.sent_messages], ['a', 'b'])
        message = manager.sent_messages[0][1]
        self.assertEqual((message['e'], message['n'], message['t']), ('camera.front_door', 'Front door', 30))
        self.assertEqual(manager.broadcasts, [('esp_screens_show_alert', {'title': 'Front door', 'icon': 'doorbell', 'camera': 'camera.front_door',
                                                                          'timeout': 30, 'screen': ['cyd', 'off']})])
        self.assertEqual((result['sent'], result['alerts']), (2, 2))
        self.assertIn('camera.front_door', manager.alert_cameras, 'the screens may open its camera')

        # The hall answers: the kitchen closes its view, the CYD its alert, and the app says so once.
        manager.sent_messages.clear()
        manager.broadcasts.clear()
        asyncio.run(manager.doorbell_report({'action': 'answered', 'key': message['k'], 'screen': 'hall'}))
        self.assertEqual(manager.sent_messages, [('b', {'v': 1, 'op': 'doorbell', 'k': message['k'], 'c': 1})])
        self.assertEqual(manager.broadcasts, [('esp_screens_dismiss_alert', {'screen': ['cyd', 'off']})])
        self.assertEqual(manager.fired, [('esp_screens_doorbell_answered', {'camera': 'camera.front_door', 'title': 'Front door', 'screen': 'hall'})])
        asyncio.run(manager.doorbell_report({'action': 'closed', 'key': message['k'], 'screen': 'kitchen'}))
        self.assertEqual(len(manager.fired), 1)

    def test_the_event_names_its_screens(self):
        manager = self.manager([Screen('a', 'hall'), Screen('b', 'kitchen')])
        asyncio.run(manager.doorbell_show({'camera': 'camera.front_door', 'title': 'Back door', 'screen': 'kitchen'}))
        self.assertEqual([(inbox, message['n']) for inbox, message in manager.sent_messages], [('b', 'Back door')])
        self.assertEqual(manager.broadcasts, [])

    def test_without_a_camera_nothing(self):
        manager = self.manager([Screen('a', 'hall')])
        self.assertEqual(asyncio.run(manager.doorbell_show({'title': 'Door'}))['sent'], 0)
        self.assertEqual(manager.sent_messages, [])

    def test_dismiss_closes_every_open_ring(self):
        manager = self.manager([Screen('a', 'hall')])
        asyncio.run(manager.doorbell_show({'camera': 'camera.front_door'}))
        key = manager.sent_messages[0][1]['k']
        manager.sent_messages.clear()
        asyncio.run(manager.doorbell_dismiss({}))
        self.assertEqual(manager.sent_messages, [('a', {'v': 1, 'op': 'doorbell', 'k': key, 'c': 1})])

    def test_a_missed_ring_is_seen_once(self):
        manager = self.manager([Screen('a', 'hall')])
        key = manager.doorbells.start('camera.front_door', 'Front door', 60, ['a'])
        manager.doorbells.unanswered(key)
        asyncio.run(manager.doorbell_report({'action': 'seen', 'key': key, 'screen': 'hall'}))
        self.assertIsNone(manager.doorbells.bar_item())
        self.assertTrue(manager.ha.changed.is_set(), 'the top bars are sent again')


if __name__ == '__main__':
    unittest.main()

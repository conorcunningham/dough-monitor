import json
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from doughs.models import Dough
from sensors.models import Reading, Sensor
from sensors.mqtt import Listener, build_client

PROBE_PAYLOAD = json.dumps({
    'sensor_id': '28-0123456789ab',
    'temperature_c': 24.5,
    'ts': '2026-10-06T08:00:00+00:00',
}).encode()


class ListenerTests(TestCase):
    def setUp(self):
        self.listener = Listener(['dough-monitor/#', 'rtl_433/+/events'])
        # It would close the test's transaction-wrapped connection.
        patcher = mock.patch('sensors.mqtt.close_old_connections')
        self.close_old_connections = patcher.start()
        self.addCleanup(patcher.stop)

    def test_subscribes_to_topics_on_connect(self):
        client = mock.Mock()

        with self.assertLogs('sensors.mqtt', 'INFO'):
            self.listener.on_connect(client, None, None, ReasonCode(PacketTypes.CONNACK, 'Success'), None)

        client.subscribe.assert_called_once_with([('dough-monitor/#', 1), ('rtl_433/+/events', 1)])

    def test_does_not_subscribe_when_connection_refused(self):
        client = mock.Mock()

        with self.assertLogs('sensors.mqtt', 'ERROR'):
            self.listener.on_connect(client, None, None, ReasonCode(PacketTypes.CONNACK, 'Not authorized'), None)

        client.subscribe.assert_not_called()

    def test_message_from_registered_sensor_is_stored(self):
        probe = Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        Dough.objects.create(dough_sensor=probe, started_at=datetime(2026, 10, 6, 7, 0, tzinfo=UTC))
        message = SimpleNamespace(topic='dough-monitor/probe/28-0123456789ab', payload=PROBE_PAYLOAD)

        self.listener.on_message(None, None, message)

        self.assertEqual(Reading.objects.get().temperature_c, 24.5)
        self.close_old_connections.assert_called_once()

    def test_unregistered_sensor_is_logged_once(self):
        with self.assertLogs('sensors.mqtt', 'INFO') as logs:
            self.listener.handle('dough-monitor/probe/28-0123456789ab', PROBE_PAYLOAD)
            self.listener.handle('dough-monitor/probe/28-0123456789ab', PROBE_PAYLOAD)

        self.assertEqual(len(logs.records), 1)
        self.assertIn("'28-0123456789ab'", logs.output[0])
        self.assertFalse(Reading.objects.exists())

    def test_idle_sensor_reading_is_dropped_quietly(self):
        Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)

        with self.assertLogs('sensors.mqtt', 'DEBUG') as logs:
            result = self.listener.handle('dough-monitor/probe/28-0123456789ab', PROBE_PAYLOAD)

        self.assertIsNone(result)
        self.assertFalse(Reading.objects.exists())
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelname, 'DEBUG')
        self.assertIn('No dough collecting', logs.output[0])

    def test_unrecognised_message_is_skipped(self):
        with self.assertLogs('sensors.mqtt', 'DEBUG') as logs:
            result = self.listener.handle('rtl_433/pi/events', b'not json')

        self.assertIsNone(result)
        self.assertIn('Skipping message', logs.output[0])

    def test_errors_are_logged_not_raised(self):
        message = SimpleNamespace(topic='dough-monitor/probe/x', payload=PROBE_PAYLOAD)

        with (
            mock.patch('sensors.mqtt.store_reading', side_effect=RuntimeError('boom')),
            self.assertLogs('sensors.mqtt', 'ERROR') as logs,
        ):
            self.listener.on_message(None, None, message)

        self.assertIn('Failed to handle message', logs.output[0])


@override_settings(MQTT_CLIENT_ID='test-client', MQTT_USERNAME='baker', MQTT_PASSWORD='secret')
class BuildClientTests(TestCase):
    def test_uses_persistent_session_and_credentials(self):
        listener = Listener(['dough-monitor/#'])

        with mock.patch('sensors.mqtt.mqtt.Client') as client_class:
            client = build_client(listener)

        client_class.assert_called_once_with(mock.ANY, client_id='test-client', clean_session=False)
        client.username_pw_set.assert_called_once_with('baker', 'secret')
        self.assertEqual(client.on_message, listener.on_message)

    @override_settings(MQTT_USERNAME='')
    def test_no_credentials_when_username_blank(self):
        with mock.patch('sensors.mqtt.mqtt.Client') as client_class:
            build_client(Listener([]))

        client_class.return_value.username_pw_set.assert_not_called()


@override_settings(MQTT_HOST='broker.test', MQTT_PORT=1884)
class MqttListenCommandTests(TestCase):
    def test_connects_and_loops(self):
        with mock.patch('sensors.management.commands.mqtt_listen.build_client') as build:
            call_command('mqtt_listen', stdout=StringIO())

        client = build.return_value
        client.connect_async.assert_called_once_with('broker.test', 1884, keepalive=60)
        client.loop_forever.assert_called_once_with(retry_first_connection=True)

    def test_ctrl_c_disconnects(self):
        out = StringIO()
        with mock.patch('sensors.management.commands.mqtt_listen.build_client') as build:
            build.return_value.loop_forever.side_effect = KeyboardInterrupt
            call_command('mqtt_listen', stdout=out)

        build.return_value.disconnect.assert_called_once()
        self.assertIn('Stopped.', out.getvalue())

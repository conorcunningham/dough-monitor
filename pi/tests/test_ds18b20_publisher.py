import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pi.ds18b20_publisher import find_probes, publish_once, read_temperature

GOOD = '72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=23125\n'
BAD_CRC = '72 01 4b 46 7f ff 0e 10 57 : crc=57 NO\n72 01 4b 46 7f ff 0e 10 57 t=23125\n'
POWER_ON_RESET = '50 05 4b 46 7f ff 0c 10 1c : crc=1c YES\n50 05 4b 46 7f ff 0c 10 1c t=85000\n'


class FakeW1Bus:
    """A temporary stand-in for /sys/bus/w1/devices."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name)

    def add_probe(self, sensor_id, w1_slave):
        probe = self.path / sensor_id
        probe.mkdir()
        (probe / 'w1_slave').write_text(w1_slave)
        return probe

    def cleanup(self):
        self._dir.cleanup()


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeW1Bus()
        self.addCleanup(self.bus.cleanup)

    def test_finds_only_ds18b20_probes(self):
        self.bus.add_probe('28-0123456789ab', GOOD)
        (self.bus.path / 'w1_bus_master1').mkdir()

        self.assertEqual([p.name for p in find_probes(self.bus.path)], ['28-0123456789ab'])

    def test_reads_temperature(self):
        probe = self.bus.add_probe('28-0123456789ab', GOOD)

        self.assertEqual(read_temperature(probe), 23.125)

    def test_rejects_bad_reads(self):
        cases = {'crc failure': BAD_CRC, 'power-on reset': POWER_ON_RESET, 'empty': ''}
        for name, contents in cases.items():
            with self.subTest(name), self.assertLogs('ds18b20_publisher', 'WARNING'):
                probe = self.bus.add_probe(f'28-{name.replace(" ", "")}', contents)
                self.assertIsNone(read_temperature(probe))

    def test_missing_file_is_logged_not_raised(self):
        probe = self.bus.path / '28-gone'

        with self.assertLogs('ds18b20_publisher', 'WARNING'):
            self.assertIsNone(read_temperature(probe))

    def test_publishes_each_good_probe(self):
        self.bus.add_probe('28-0123456789ab', GOOD)
        self.bus.add_probe('28-ba9876543210', BAD_CRC)
        client = mock.Mock()

        with self.assertLogs('ds18b20_publisher', 'WARNING'):
            published = publish_once(client, 'dough-monitor/probe', devices=self.bus.path)

        self.assertEqual(published, 1)
        topic, payload = client.publish.call_args.args
        self.assertEqual(topic, 'dough-monitor/probe/28-0123456789ab')
        self.assertEqual(client.publish.call_args.kwargs, {'qos': 1})
        data = json.loads(payload)
        self.assertEqual(data['sensor_id'], '28-0123456789ab')
        self.assertEqual(data['temperature_c'], 23.125)
        self.assertTrue(data['ts'].endswith('+00:00'))


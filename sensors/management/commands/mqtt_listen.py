from django.conf import settings
from django.core.management.base import BaseCommand

from sensors.mqtt import Listener, build_client


class Command(BaseCommand):
    help = 'Subscribe to the MQTT broker and store readings from registered sensors. Runs until stopped.'

    def handle(self, *args, **options):
        client = build_client(Listener(settings.MQTT_TOPICS))
        self.stdout.write(f'Connecting to MQTT broker at {settings.MQTT_HOST}:{settings.MQTT_PORT}...')
        client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=60)
        try:
            # Keeps retrying (with backoff) if the broker is unreachable, including at start-up.
            client.loop_forever(retry_first_connection=True)
        except KeyboardInterrupt:
            client.disconnect()
            self.stdout.write('Stopped.')

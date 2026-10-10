import logging

import paho.mqtt.client as mqtt
from django.conf import settings
from django.db import close_old_connections

from .ingest import UnrecognisedMessage, parse_payload, store_reading

logger = logging.getLogger(__name__)

QOS = 1


class Listener:
    def __init__(self, topics):
        self.topics = topics
        self._reported_unknown = set()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error('MQTT connection refused: %s', reason_code)
            return
        logger.info('Connected to MQTT broker; subscribing to %s', ', '.join(self.topics))
        # Subscribing here (not once at start-up) restores subscriptions after a reconnect.
        client.subscribe([(topic, QOS) for topic in self.topics])

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        logger.warning('Disconnected from MQTT broker: %s', reason_code)

    def on_message(self, client, userdata, message):
        # This process runs for days; don't reuse a database connection that has gone away.
        close_old_connections()
        try:
            self.handle(message.topic, message.payload)
        except Exception:
            logger.exception('Failed to handle message on %s', message.topic)

    def handle(self, topic, payload):
        try:
            parsed = parse_payload(payload)
        except UnrecognisedMessage as e:
            logger.debug('Skipping message on %s: %s', topic, e)
            return None

        reading = store_reading(parsed)
        if reading is None and parsed.hardware_id not in self._reported_unknown:
            # rtl_433 hears every 433 MHz device nearby, so unknown sensors are ignored, not added.
            self._reported_unknown.add(parsed.hardware_id)
            logger.info(
                'Ignoring unregistered sensor %r on %s (%.1f °C). Add it in the admin to record it.',
                parsed.hardware_id,
                topic,
                parsed.temperature_c,
            )
        return reading


def build_client(listener):
    # A fixed client ID with a persistent session lets the broker queue QoS 1
    # readings while this listener is offline (e.g. the laptop is asleep).
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.MQTT_CLIENT_ID,
        clean_session=False,
    )
    if settings.MQTT_USERNAME:
        client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.on_connect = listener.on_connect
    client.on_disconnect = listener.on_disconnect
    client.on_message = listener.on_message
    return client

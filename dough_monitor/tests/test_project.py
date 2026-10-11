from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class SettingsTests(SimpleTestCase):
    def test_database_is_postgres(self):
        self.assertEqual(
            settings.DATABASES['default']['ENGINE'],
            'django.db.backends.postgresql',
        )

    def test_secret_key_is_not_the_insecure_default(self):
        self.assertFalse(settings.SECRET_KEY.startswith('django-insecure-'))

    def test_trusts_the_proxy_for_https_detection(self):
        self.assertEqual(settings.SECURE_PROXY_SSL_HEADER, ('HTTP_X_FORWARDED_PROTO', 'https'))


class DatabaseSmokeTests(TestCase):
    def test_connection_is_postgres_16_or_later(self):
        self.assertGreaterEqual(connection.pg_version, 160000)

    def test_admin_login_page_renders(self):
        response = self.client.get(reverse('admin:login'))

        self.assertContains(response, 'Log in')

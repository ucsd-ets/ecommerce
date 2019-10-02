"""Production settings and globals."""
import codecs
from os import environ
from urlparse import urljoin

import yaml
# Normally you should not import ANYTHING from Django directly
# into your settings, but ImproperlyConfigured is an exception.
from django.core.exceptions import ImproperlyConfigured

from ecommerce.settings.base import *

# Protocol used for construcing absolute callback URLs
PROTOCOL = 'https'

# Enable offline compression of CSS/JS
COMPRESS_ENABLED = True
COMPRESS_OFFLINE = True

# Minify CSS
COMPRESS_CSS_FILTERS += [
    'compressor.filters.cssmin.CSSMinFilter',
]

LOGGING['handlers']['local']['level'] = 'INFO'


def get_env_setting(setting):
    """ Get the environment setting or return exception """
    try:
        return environ[setting]
    except KeyError:
        error_msg = "Set the %s env variable" % setting
        raise ImproperlyConfigured(error_msg)


# HOST CONFIGURATION
# See: https://docs.djangoproject.com/en/1.5/releases/1.5/#allowed-hosts-required-in-production
ALLOWED_HOSTS = ['*']
# END HOST CONFIGURATION

# Keep track of the names of settings that represent dicts. Instead of overriding the values in base.py,
# the values read from disk should UPDATE the pre-configured dicts.
DICT_UPDATE_KEYS = ('JWT_AUTH',)

CONFIG_FILE = get_env_setting('ECOMMERCE_CFG')
with codecs.open(CONFIG_FILE, encoding='utf-8') as f:
    config_from_yaml = yaml.load(f)

    # Remove the items that should be used to update dicts, and apply them separately rather
    # than pumping them into the local vars.
    dict_updates = {key: config_from_yaml.pop(key, None) for key in DICT_UPDATE_KEYS}

    for key, value in dict_updates.items():
        if value:
            vars()[key].update(value)

    vars().update(config_from_yaml)

DB_OVERRIDES = dict(
    PASSWORD=environ.get('DB_MIGRATION_PASS', DATABASES['default']['PASSWORD']),
    ENGINE=environ.get('DB_MIGRATION_ENGINE', DATABASES['default']['ENGINE']),
    USER=environ.get('DB_MIGRATION_USER', DATABASES['default']['USER']),
    NAME=environ.get('DB_MIGRATION_NAME', DATABASES['default']['NAME']),
    HOST=environ.get('DB_MIGRATION_HOST', DATABASES['default']['HOST']),
    PORT=environ.get('DB_MIGRATION_PORT', DATABASES['default']['PORT']),
)

for override, value in DB_OVERRIDES.iteritems():
    DATABASES['default'][override] = value

# Email configuration
EMAIL_BACKEND = config_from_yaml.get('ECOMMERCE_EMAIL_BACKEND')
EMAIL_HOST = config_from_yaml.get('ECOMMERCE_EMAIL_HOST')
EMAIL_PORT = config_from_yaml.get('ECOMMERCE_EMAIL_PORT')
EMAIL_USE_TLS = config_from_yaml.get('ECOMMERCE_EMAIL_USE_TLS')
EMAIL_HOST_USER = config_from_yaml.get('ECOMMERCE_EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = config_from_yaml.get('ECOMMERCE_EMAIL_HOST_PASSWORD')

# PAYMENT PROCESSOR OVERRIDES

authorizenet_dict = {
    'merchant_auth_name': config_from_yaml.get('AUTHORIZENET_MERCHANT_AUTH_NAME'),
    'transaction_key': config_from_yaml.get('AUTHORIZENET_TRANSACTION_KEY'),
    'redirect_url': config_from_yaml.get('AUTHORIZENET_REDIRECT_URL')
}
PAYMENT_PROCESSOR_CONFIG['edx'].update({'authorizenet': authorizenet_dict})

for __, configs in PAYMENT_PROCESSOR_CONFIG.iteritems():
    for __, config in configs.iteritems():
        config.update({
            'receipt_path': PAYMENT_PROCESSOR_RECEIPT_PATH,
            'cancel_checkout_path': PAYMENT_PROCESSOR_CANCEL_PATH,
            'error_path': PAYMENT_PROCESSOR_ERROR_PATH,
        })
# END PAYMENT PROCESSOR OVERRIDES

ENTERPRISE_API_URL = urljoin(ENTERPRISE_SERVICE_URL, 'api/v1/')

# Authorizenet payment processor set a cookie for dashboard to show pending course purchased dashoard
# notification. This cookie domain will be used to set and delete that cookie.
ECOMMERCE_COOKIE_DOMAIN = config_from_yaml.get('ECOMMERCE_COOKIE_DOMAIN')

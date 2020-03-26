"""
Tests for utils in UCSDFeatures app.
"""
import base64
import json

import ddt

from mock import patch, MagicMock, ANY
from django.conf import settings
from premailer import transform
from oscar.core.loading import get_model

from ecommerce.tests.testcases import TestCase

from ecommerce.ucsd_features.utils import send_email_notification, add_to_ga_events_cookie
from ecommerce.ucsd_features.constants import COUPONS_LIMIT_REACHED, COUPON_ASSIGNED
from ecommerce.ucsd_features.tests.fixtures import COMMUNICATION_EVENT_TYPE_FIXTURE


CommunicationEventType = get_model('customer', 'CommunicationEventType')


@ddt.ddt
class UtilsSendEmailNotificationTests(TestCase):
    def setUp(self):
        super(UtilsSendEmailNotificationTests, self).setUp()

    @patch('ecommerce.ucsd_features.utils.logger.error')
    def test_send_email_notification_with_no_email(self, mocked_logger_error):
        """
        Test that appropriate error is logged if no receiver email is provided.
        """
        expected_error_message = 'No email provided for sending the email to. Cannot send the email'

        return_value = send_email_notification(email=None, commtype_code='NOT_USED', context={})
        self.assertFalse(return_value)
        mocked_logger_error.assert_called_once_with(expected_error_message)

    @patch('ecommerce.ucsd_features.utils.CommunicationEventType.objects.get_and_render', side_effect=Exception)
    @patch('ecommerce.ucsd_features.utils.logger.error')
    def test_send_email_notification_with_invalid_commtype_code_and_exception(self, mocked_logger_error, _):
        """
        Test that appropriate error is logged if invalid commtype is provided is provided and exception is raised
        at get_and_render call.
        """
        commtype_code = 'INVALID_COMMTYPE_CODE'
        email = 'test@example.com'

        expected_error_message = ('Unable to locate a DB entry or templates for communication type [%s].'
                                  ' No notification has been sent.')

        return_value = send_email_notification(email=email, commtype_code=commtype_code, context={})
        self.assertEqual(return_value, None)
        mocked_logger_error.assert_called_once_with(expected_error_message, commtype_code)

    def test_send_email_notification_with_invalid_commtype_code_and_no_exception(self):
        """
        Test that appropriate error is logged if invalid commtype is provided is provided and no exception is raised
        at get_and_render call.
        """
        commtype_code = 'INVALID_COMMTYPE_CODE'
        email = 'test@example.com'
        expected_exception = 'Could not get some of the required values for the email'

        with self.assertRaises(Exception) as ex:
            return_value = send_email_notification(email=email, commtype_code=commtype_code, context={})
            self.assertEqual(return_value, None)

        self.assertEqual(ex.exception.message, expected_exception)

    @patch('ecommerce.ucsd_features.utils.Dispatcher.send_email_messages')
    @patch('ecommerce.ucsd_features.utils.CommunicationEventType.objects.get_and_render',
           return_value=COMMUNICATION_EVENT_TYPE_FIXTURE)
    @ddt.data(COUPONS_LIMIT_REACHED, COUPON_ASSIGNED)
    def test_send_email_notification_with_valid_commtype_code(self, commtype_code, _, mocked_dispatcher_call):
        """
        Test that `send_email_messages` is called with correct data.

        For the commtype_code used in the tests, the corresponding HTML files are placed in the theme.
        Since theme is not enabled for the tests, we will use a fixture as the return value of the
        `get_and_render` call
        """
        email = 'test@example.com'
        context = {}

        expected_messages = COMMUNICATION_EVENT_TYPE_FIXTURE
        expected_messages['html'] = transform(expected_messages.get('html'))

        return_value = send_email_notification(email=email, commtype_code=commtype_code, context=context)

        self.assertEqual(return_value, True)
        mocked_dispatcher_call.assert_called_once_with(email, expected_messages, ANY)


class UtilsGAEventsTests(TestCase):
    def setUp(self):
        super(UtilsGAEventsTests, self)
        self.event_name = 'test'
        self.event_data = {
            'key': 'value'
        }
        self.cookie_options = {
            'domain': '.test'
        }

    def test_add_to_ga_events_cookie_with_no_existing_cookie(self):
        """
        Test add_to_ga_events_cookie method when there is no existing corresponding cookie set
        """
        request = MagicMock()
        request.COOKIES.get.return_value = None

        response = MagicMock()

        expected_cookie = base64.b64encode(json.dumps({
            'events': [
                {'event_name': self.event_name, 'event_data': self.event_data}
            ]
        }))

        add_to_ga_events_cookie(request, response, self.event_name, self.event_data, **self.cookie_options)

        response.set_cookie.assert_called_once_with(
            settings.GOOGLE_ANALYTICS_EVENTS_COOKIE_NAME,
            expected_cookie,
            **self.cookie_options
        )

    def test_add_to_ga_events_cookie_with_existing_cookie(self):
        """
        Test add_to_ga_events_cookie method when there are some preexisting events in the cookie
        """
        events_data = {
            'events': [
                {'event_name': 'old_name',
                 'event_data': {}}
            ]
        }

        response = MagicMock()
        request = MagicMock()
        request.COOKIES.get.return_value = base64.b64encode(json.dumps(events_data))

        events_data['events'].append({'event_name': self.event_name,
                                      'event_data': self.event_data})
        expected_cookie = base64.b64encode(json.dumps(events_data))

        add_to_ga_events_cookie(request, response, self.event_name, self.event_data, **self.cookie_options)

        response.set_cookie.assert_called_once_with(
            settings.GOOGLE_ANALYTICS_EVENTS_COOKIE_NAME,
            expected_cookie,
            **self.cookie_options
        )

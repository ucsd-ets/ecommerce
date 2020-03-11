import base64
import json
import logging

from django.conf import settings
from oscar.core.loading import get_model, get_class
from premailer import transform


logger = logging.getLogger(__name__)
PaymentProcessorResponse = get_model('payment', 'PaymentProcessorResponse')
CommunicationEventType = get_model('customer', 'CommunicationEventType')
Dispatcher = get_class('customer.utils', 'Dispatcher')


def send_email_notification(email, commtype_code, context, site=None):
    """
        Send email to provided email
    """
    if not email:
        logger.error('No email provided for sending the email to. Cannot send the email')
        return False

    try:
        event_type = CommunicationEventType.objects.get(code=commtype_code)
    except CommunicationEventType.DoesNotExist:
        try:
            messages = CommunicationEventType.objects.get_and_render(commtype_code, context)
        except Exception:  # pylint: disable=broad-except
            logger.error('Unable to locate a DB entry or templates for communication type [%s]. '
                         'No notification has been sent.', commtype_code)
            return
    else:
        messages = event_type.get_messages(context)

    if messages.get('html'):
        messages['html'] = transform(messages.get('html'))

    if messages and messages.get('body') and messages.get('subject'):
        Dispatcher().send_email_messages(email, messages, site)
        return True

    raise Exception('Could not get some of the required values for the email')


def add_to_ga_events_cookie(request, response, event_name, event_data, **cookie_options):
    """
    Adds the provided event_data to a cookie whose name is configured through GOOGLE_ANALYTICS_EVENTS_COOKIE_NAME
    settings variable. If there is already a cookie with this name, append the event to `events` list in that cookie.
    Otherwise, make a new cookie.


    Arguments:
        request: Request object from which we can get the already set cookie
        response: Response object using which the cookie will be set
        event_name: the will be used as event action when emitting GA event from browser
        event_data: event data that will be emitted
        **cookie_options: Any other options that can be used while setting the cookie, e.g. domain of the cookie
    """
    cookie_name = settings.GOOGLE_ANALYTICS_EVENTS_COOKIE_NAME

    ga_events_cookie = request.COOKIES.get(cookie_name)

    if ga_events_cookie:
        decoded_cookie = base64.b64decode(ga_events_cookie)
        events_data = json.loads(decoded_cookie)
    else:
        events_data = {}

    events_data['events'] = events_data.get('events') or []
    events_data['events'].append({
        'event_name': event_name,
        'event_data': event_data
    })
    encoded_cookie = base64.b64encode(json.dumps(events_data))
    response.set_cookie(cookie_name, encoded_cookie, **cookie_options)

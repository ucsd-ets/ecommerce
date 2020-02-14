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

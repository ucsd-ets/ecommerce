from __future__ import unicode_literals

import logging
import waffle

from django.conf import settings
from django.db import models
from django.utils.translation import ugettext_lazy as _
from django_extensions.db.models import TimeStampedModel
from ecommerce_worker.sailthru.v1.tasks import send_course_refund_email
from oscar.apps.payment.exceptions import PaymentError
from oscar.core.loading import get_class, get_model
from oscar.core.utils import get_default_currency

from ecommerce.core.constants import COURSE_ENTITLEMENT_PRODUCT_CLASS_NAME, SEAT_PRODUCT_CLASS_NAME
from ecommerce.core.url_utils import get_lms_explore_courses_url
from ecommerce.extensions.analytics.utils import audit_log
from ecommerce.extensions.checkout.utils import format_currency, get_receipt_page_url
from ecommerce.extensions.fulfillment.api import revoke_fulfillment_for_refund
from ecommerce.extensions.order.constants import PaymentEventTypeName
from ecommerce.extensions.payment.exceptions import RefundError
from ecommerce.extensions.payment.helpers import get_processor_class_by_name
from ecommerce.extensions.refund.exceptions import InvalidStatus
from ecommerce.extensions.refund.status import REFUND, REFUND_LINE
from ecommerce.notifications.notifications import send_notification

logger = logging.getLogger(__name__)

PaymentEvent = get_model('order', 'PaymentEvent')
PaymentEventType = get_model('order', 'PaymentEventType')
post_refund = get_class('refund.signals', 'post_refund')


class StatusMixin(object):
    pipeline_setting = None

    @property
    def pipeline(self):
        # NOTE: We use the property and getattr (instead of settings.XXX) so that we can properly override the
        # settings when testing.
        return getattr(settings, self.pipeline_setting)

    def available_statuses(self):
        """Returns all possible statuses that this object can move to."""
        return self.pipeline.get(self.status, ())

    # pylint: disable=access-member-before-definition,attribute-defined-outside-init
    def set_status(self, new_status):
        """Set a new status for this object.

        If the requested status is not valid, then ``InvalidStatus`` is raised.
        """
        if new_status not in self.available_statuses():
            msg = " Transition from '{status}' to '{new_status}' is invalid for {model_name} {id}.".format(
                new_status=new_status,
                model_name=self.__class__.__name__.lower(),
                id=self.id,
                status=self.status
            )
            raise InvalidStatus(msg)

        self.status = new_status
        self.save()

    def __str__(self):
        return unicode(self.id)


class Refund(StatusMixin, TimeStampedModel):
    """Main refund model, used to represent the state of a refund."""
    order = models.ForeignKey('order.Order', related_name='refunds', verbose_name=_('Order'), on_delete=models.CASCADE)
    user = models.ForeignKey('core.User', related_name='refunds', verbose_name=_('User'), on_delete=models.CASCADE)
    total_credit_excl_tax = models.DecimalField(_('Total Credit (excl. tax)'), decimal_places=2, max_digits=12)
    currency = models.CharField(_("Currency"), max_length=12, default=get_default_currency)
    status = models.CharField(
        _('Status'),
        max_length=255,
        choices=[
            (REFUND.OPEN, REFUND.OPEN),
            (REFUND.DENIED, REFUND.DENIED),
            (REFUND.PAYMENT_REFUND_ERROR, REFUND.PAYMENT_REFUND_ERROR),
            (REFUND.PAYMENT_REFUNDED, REFUND.PAYMENT_REFUNDED),
            (REFUND.REVOCATION_ERROR, REFUND.REVOCATION_ERROR),
            (REFUND.COMPLETE, REFUND.COMPLETE),
        ]
    )

    pipeline_setting = 'OSCAR_REFUND_STATUS_PIPELINE'

    @classmethod
    def all_statuses(cls):
        """Returns all possible statuses for a refund."""
        return list(getattr(settings, cls.pipeline_setting).keys())

    @classmethod
    def create_with_lines(cls, order, lines):
        """Given an order and order lines, creates a Refund with corresponding RefundLines.

        Only creates RefundLines for unrefunded order lines. Refunds corresponding to a total
        credit of $0 are approved upon creation.

        Arguments:
            order (order.Order): The order to which the newly-created refund corresponds.
            lines (list of order.Line): Order lines to be refunded.

        Returns:
            None: If no unrefunded order lines have been provided.
            Refund: With RefundLines corresponding to each given unrefunded order line.
        """
        unrefunded_lines = [line for line in lines if not line.refund_lines.exclude(status=REFUND_LINE.DENIED).exists()]

        if unrefunded_lines:
            status = getattr(settings, 'OSCAR_INITIAL_REFUND_STATUS', REFUND.OPEN)
            total_credit_excl_tax = sum([line.line_price_excl_tax for line in unrefunded_lines])
            refund = cls.objects.create(
                order=order,
                user=order.user,
                status=status,
                total_credit_excl_tax=total_credit_excl_tax
            )

            audit_log(
                'refund_created',
                amount=total_credit_excl_tax,
                currency=refund.currency,
                order_number=order.number,
                refund_id=refund.id,
                user_id=refund.user.id
            )

            status = getattr(settings, 'OSCAR_INITIAL_REFUND_LINE_STATUS', REFUND_LINE.OPEN)
            for line in unrefunded_lines:
                RefundLine.objects.create(
                    refund=refund,
                    order_line=line,
                    line_credit_excl_tax=line.line_price_excl_tax,
                    quantity=line.quantity,
                    status=status
                )

            if total_credit_excl_tax == 0:
                refund.approve(notify_purchaser=False)

            return refund

    @property
    def num_items(self):
        """Returns the number of items in this refund."""
        num_items = 0
        for line in self.lines.all():
            num_items += line.quantity
        return num_items

    @property
    def can_approve(self):
        """Returns a boolean indicating if this Refund can be approved."""
        return self.status not in (REFUND.COMPLETE, REFUND.DENIED)

    @property
    def can_deny(self):
        """Returns a boolean indicating if this Refund can be denied."""
        return self.status == settings.OSCAR_INITIAL_REFUND_STATUS

    def _issue_credit(self):
        """Issue a credit to the purchaser via the payment processor used for the original order."""
        try:
            # NOTE: Update this if we ever support multiple payment sources for a single order.
            source = self.order.sources.first()
            processor = get_processor_class_by_name(source.source_type.name)(self.order.site)
            amount = self.total_credit_excl_tax

            refund_reference_number = processor.issue_credit(self.order.number, self.order.basket, source.reference,
                                                             amount, self.currency)
            source.refund(amount, reference=refund_reference_number)
            event_type, __ = PaymentEventType.objects.get_or_create(name=PaymentEventTypeName.REFUNDED)
            PaymentEvent.objects.create(
                event_type=event_type,
                order=self.order,
                amount=amount,
                reference=refund_reference_number,
                processor_name=processor.NAME
            )

            audit_log(
                'credit_issued',
                amount=amount,
                currency=self.currency,
                processor_name=processor.NAME,
                refund_id=self.id,
                user_id=self.user.id
            )
        except AttributeError:
            # Order has no sources, resulting in an exception when trying to access `source_type`.
            # This occurs when attempting to refund free orders.
            logger.info("No payments to credit for Refund [%d]", self.id)

    def _notify_purchaser(self):
        """ Notify the purchaser that the refund has been processed. """
        site_configuration = self.order.site.siteconfiguration
        site_code = site_configuration.partner.short_code

        if not site_configuration.send_refund_notifications:
            logger.info(
                'Refund notifications are disabled for Partner [%s]. No notification will be sent for Refund [%d]',
                site_code, self.id
            )
            return

        # NOTE (CCB): The initial version of the refund email only supports refunding a single course.
        product = self.lines.first().order_line.product
        product_class = product.get_product_class().name

        if product_class not in [SEAT_PRODUCT_CLASS_NAME, COURSE_ENTITLEMENT_PRODUCT_CLASS_NAME]:
            logger.warning(
                ('No refund notification will be sent for Refund [%d]. The notification supports product lines '
                 'of type Course, not [%s].'),
                self.id, product_class
            )
            return

        if product_class == SEAT_PRODUCT_CLASS_NAME:
            course_name = self.lines.first().order_line.product.course.name
        else:
            course_name = self.lines.first().order_line.product.title
        order_number = self.order.number
        order_url = get_receipt_page_url(site_configuration, order_number)
        amount = format_currency(self.currency, self.total_credit_excl_tax)
        if waffle.switch_is_active('sailthru_enable'):
            send_course_refund_email.delay(self.user.email, self.id, amount, course_name, order_number,
                                           order_url, site_code=site_code)
            logger.info('Course refund notification scheduled for Refund [%d].', self.id)
        else:
            send_notification(
                self.user,
                'REFUND',
                {
                    'refund_id': self.id,
                    'amount': amount,
                    'course_name': course_name,
                    'order_number': order_number,
                    'order_url': order_url,
                    'site_code': site_code,
                    'explore_courses_url': get_lms_explore_courses_url(),
                },
                self.order.site
            )
            logger.info('Course refund notification for Refund [%d] has been sent to learner %s.', self.id, self.user.email)

    def _revoke_lines(self):
        """Revoke fulfillment for the lines in this Refund."""
        if revoke_fulfillment_for_refund(self):
            self.set_status(REFUND.COMPLETE)
        else:
            logger.error('Unable to revoke fulfillment of all lines of Refund [%d].', self.id)
            self.set_status(REFUND.REVOCATION_ERROR)

    def approve(self, revoke_fulfillment=True, notify_purchaser=True):
        if self.status == REFUND.COMPLETE:
            logger.info('Refund [%d] has already been completed. No additional action is required to approve.', self.id)
            return True
        elif not self.can_approve:
            logger.warning('Refund [%d] has status set to [%s] and cannot be approved.', self.id, self.status)
            return False
        elif self.status in (REFUND.OPEN, REFUND.PAYMENT_REFUND_ERROR):
            try:
                self._issue_credit()
                self.set_status(REFUND.PAYMENT_REFUNDED)
                if notify_purchaser:
                    self._notify_purchaser()
            except (PaymentError, RefundError):
                logger.exception('Failed to issue credit for refund [%d].', self.id)
                self.set_status(REFUND.PAYMENT_REFUND_ERROR)
                return False

        if revoke_fulfillment and self.status in (REFUND.PAYMENT_REFUNDED, REFUND.REVOCATION_ERROR):
            self._revoke_lines()

        if not revoke_fulfillment and self.status == REFUND.PAYMENT_REFUNDED:
            logger.info('Skipping the revocation step for refund [%d].', self.id)
            # Mark the status complete as it does not involve the revocation.
            self.set_status(REFUND.COMPLETE)
            for refund_line in self.lines.all():
                refund_line.set_status(REFUND_LINE.COMPLETE)

        if self.status == REFUND.COMPLETE:
            post_refund.send_robust(sender=self.__class__, refund=self)
            return True

        return False

    def deny(self):
        if self.status == REFUND.DENIED:
            logger.info('Refund [%d] has already been denied. No additional action is required to deny.', self.id)
            return True

        if not self.can_deny:
            logger.warning('Refund [%d] cannot be denied. Its status is [%s].', self.id, self.status)
            return False

        self.set_status(REFUND.DENIED)

        result = True
        for line in self.lines.all():
            try:
                line.deny()
            except Exception:  # pylint: disable=broad-except
                logger.exception('Failed to deny RefundLine [%d].', line.id)
                result = False

        return result


class RefundLine(StatusMixin, TimeStampedModel):
    """A refund line, used to represent the state of a single item as part of a larger Refund."""
    refund = models.ForeignKey(
        'refund.Refund', related_name='lines', verbose_name=_('Refund'), on_delete=models.CASCADE
    )
    order_line = models.ForeignKey(
        'order.Line', related_name='refund_lines', verbose_name=_('Order Line'), on_delete=models.CASCADE
    )
    line_credit_excl_tax = models.DecimalField(_('Line Credit (excl. tax)'), decimal_places=2, max_digits=12)
    quantity = models.PositiveIntegerField(_('Quantity'), default=1)
    status = models.CharField(
        _('Status'),
        max_length=255,
        choices=[
            (REFUND_LINE.OPEN, REFUND_LINE.OPEN),
            (REFUND_LINE.REVOCATION_ERROR, REFUND_LINE.REVOCATION_ERROR),
            (REFUND_LINE.DENIED, REFUND_LINE.DENIED),
            (REFUND_LINE.COMPLETE, REFUND_LINE.COMPLETE),
        ]
    )

    pipeline_setting = 'OSCAR_REFUND_LINE_STATUS_PIPELINE'

    def deny(self):
        self.set_status(REFUND_LINE.DENIED)
        return True

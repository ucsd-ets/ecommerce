"""
Tests for the views in UCSDFeatures app.
"""
import datetime
import json

from mock import patch
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from oscar.core.loading import get_model

from ecommerce.coupons.tests.mixins import CouponMixin
from ecommerce.extensions.catalogue.tests.mixins import DiscoveryTestMixin
from ecommerce.extensions.offer.constants import OFFER_ASSIGNED
from ecommerce.tests.testcases import TestCase

from ecommerce.ucsd_features.constants import CATEGORY_GEOGRAPHY_PROMOTION_SLUG, COUPONS_LIMIT_REACHED, COUPON_ASSIGNED


Category = get_model('catalogue', 'Category')
Product = get_model('catalogue', 'Product')
OfferAssignment = get_model('offer', 'OfferAssignment')

JSON_CONTENT_TYPE = 'application/json'


class ViewsTestBaseMixin(TestCase, CouponMixin, DiscoveryTestMixin):
    """
    Base class for the views' test.
    """
    def setUp(self):
        super(ViewsTestBaseMixin, self).setUp()
        self.user = self.create_user()
        self.client.login(username=self.user.username, password=self.password)

        self.category = Category.objects.get(slug=CATEGORY_GEOGRAPHY_PROMOTION_SLUG)
        self.course, self.seat = self.create_course_and_seat(seat_type='verified')
        self.voucher_quantity_per_coupon = 5
        self.coupons = [
            self.create_coupon(
                catalog_query='*:*',
                quantity=self.voucher_quantity_per_coupon,
                title='valid coupon',
                course_seat_types='verified',
                start_datetime=datetime.datetime(2019, 1, 1),
                end_datetime=datetime.datetime(2099, 1, 1)
            ),
            self.create_coupon(
                catalog_query='*:*',
                quantity=self.voucher_quantity_per_coupon,
                title='Expired coupon',
                course_seat_types='verified',
                start_datetime=datetime.datetime(2019, 1, 1),
                end_datetime=datetime.datetime(2019, 1, 2)
            )
        ]

    def _create_voucher_assignments(self, coupons):
        for coupon in coupons:
            coupon_vouchers = coupon.coupon_vouchers.all()
            for coupon_voucher in coupon_vouchers:
                vouchers = coupon_voucher.vouchers.all()
                for voucher in vouchers:
                    OfferAssignment.objects.create(
                        user_email=self.user.email, code=voucher.code, offer=voucher.best_offer
                    )


class CourseCouponViewTestCases(ViewsTestBaseMixin):
    """
    Test cases for CourseCouponView View class.
    """
    url = reverse('ucsd_features:check_course_coupon')

    @patch('ecommerce.ucsd_features.views.logger.error')
    def test_return_404_if_no_course_key_is_provided(self, mocked_logger_error):
        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, 404)
        mocked_logger_error.assert_called_once_with('No course key provided')

    @patch('ecommerce.ucsd_features.views.logger.info')
    def test_return_400_if_no_coupons_are_available(self, mocked_logger_info):
        get_catalog_course_runs_response = {
            'results': [{
                'key': 'invlaid-course-key',
            }]
        }
        with patch(
                'ecommerce.ucsd_features.services.coupons.get_catalog_course_runs',
                return_value=get_catalog_course_runs_response
        ):
            response = self.client.post(
                self.url,
                data=json.dumps({
                    'course_key': str(self.course.id)
                }),
                content_type=JSON_CONTENT_TYPE
            )
            self.assertEqual(response.status_code, 400)

        mocked_logger_info.assert_called_once_with('No coupons found for course: {}'.format(self.course.id))

    @patch('ecommerce.ucsd_features.views.logger.info')
    def test_return_302_if_coupons_are_available(self, mocked_logger_info):
        get_catalog_course_runs_response = {
            'results': [{
                'key': self.course.id,
            }]
        }
        with patch(
                'ecommerce.ucsd_features.services.coupons.get_catalog_course_runs',
                return_value=get_catalog_course_runs_response
        ):

            response = self.client.post(self.url, data=json.dumps({
                'course_key': str(self.course.id)
            }), content_type=JSON_CONTENT_TYPE)
            self.assertEqual(response.status_code, 200)

        mocked_logger_info.assert_called_once_with(
            '{} coupon(s) found for course: {}'.format(2, self.course.id)
        )


class AssignVoucherViewTestCases(ViewsTestBaseMixin):
    url = reverse('ucsd_features:assign_voucher')

    @patch('ecommerce.ucsd_features.views.logger')
    @patch('ecommerce.ucsd_features.views.send_email_notification', return_value=True)
    def test_when_no_vouchers_are_available(self, mocked_send_email_notification, mocked_logger):
        """
        Test the flow in which there are no vouchers available.

        This test case will test the following:
            - Email to support should be sent when available vouchers count is less than
              GEOGRAPHY_DISCOUNT_MIN_VOUCHERS_LIMIT
            - Exception is logged if available vouchers count has reached zero
            - 400 response is returned

        """
        # assign all vouchers in valid coupon
        self._create_voucher_assignments([self.coupons[0]])

        get_catalog_course_runs_response = {
            'results': [{
                'key': self.course.id,
            }]
        }

        with patch(
                'ecommerce.ucsd_features.services.coupons.get_catalog_course_runs',
                return_value=get_catalog_course_runs_response
        ):
            response = self.client.post(self.url, data=json.dumps({
                'course_key': self.course.id,
                'user_email': self.user.email
            }), content_type=JSON_CONTENT_TYPE)

            # Test that 400 response is returned
            self.assertEqual(response.status_code, 400)

        # Test that email is sent to support if available vouchers are less
        # than GEOGRAPHY_DISCOUNT_MIN_VOUCHERS_LIMIT
        logged_message = ('Sent an email to support ({}) to notify that course coupons'
                          ' limit has been reached for course: {}'.format(settings.ECOMMERCE_SUPPORT_EMAILS,
                                                                          self.course.id))
        mocked_logger.info.assert_called_with(logged_message)

        coupons_link = '{}{}'.format(settings.ECOMMERCE_URL_ROOT, reverse('coupons:app', args=['']))
        mocked_send_email_notification.assert_called_with(settings.ECOMMERCE_SUPPORT_EMAILS, COUPONS_LIMIT_REACHED, {
            'coupons_link': coupons_link,
            'course_id': self.course.id
        }, self.site)

        # Test that exception is logged if available vouchers count has reached zero
        mocked_logger.exception.assert_called_with('Vouchers count for course: {} is 0'
                                                   ' therefore no more coupons will be assigned to any user'.format(
                                                       self.course.id
                                                   ))

    @override_settings(GEOGRAPHY_DISCOUNT_MIN_VOUCHERS_LIMIT=1)
    @patch('ecommerce.ucsd_features.views.logger')
    @patch('ecommerce.ucsd_features.views.send_email_notification', return_value=True)
    @patch('ecommerce.ucsd_features.views.send_notification', return_value=True)
    def test_voucher_successfully_assigned(self, mocked_send_notification, mocked_send_email, mocked_logger):
        """
        Test the flow in which there are vouchers available and one is successfully assigned.

        This test case will test the following:
            - Exception is not logged if available vouchers count has not reached zero
            - Email to support is not sent about vouchers count being below the limit
            - Email to user is sent with discount code
            - Offer status is set to OFFER_ASSIGNED
            - 200 response is returned

        """
        get_catalog_course_runs_response = {
            'results': [{
                'key': self.course.id,
            }]
        }

        with patch(
                'ecommerce.ucsd_features.services.coupons.get_catalog_course_runs',
                return_value=get_catalog_course_runs_response
        ):
            response = self.client.post(self.url, data=json.dumps({
                'course_key': self.course.id,
                'user_email': self.user.email
            }), content_type=JSON_CONTENT_TYPE)

            # Test that 200 response is returned
            self.assertEqual(response.status_code, 200)

        # Test that exception is not logged if available vouchers count has not reached zero
        mocked_logger.exception.assert_not_called()

        # Test that email to support is not sent about vouchers count being below the limit
        # send_notification_email is called for sending email to support only
        # to send notification email to user, send_notification method is used.
        mocked_send_email.assert_not_called()

        # Test that assigned voucher has status OFFER_ASSIGNED
        offer_assignments = OfferAssignment.objects.all()
        self.assertEqual(len(offer_assignments), 1)

        assigned = offer_assignments[0]
        self.assertEqual(assigned.status, OFFER_ASSIGNED)

        # Test that email to user is sent with discount code
        mocked_send_notification.assert_called_with(self.user, COUPON_ASSIGNED, {
            'user_email': self.user.email,
            'course_name': self.course.name,
            'coupon_code': assigned.code,
            'checkout_url': ''
        }, self.site)

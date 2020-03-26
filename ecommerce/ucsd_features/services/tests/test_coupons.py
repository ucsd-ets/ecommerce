"""
Test cases for CouponsService class.
"""
import datetime

from mock import patch
from oscar.core.loading import get_model

from ecommerce.extensions.catalogue.tests.mixins import DiscoveryTestMixin
from ecommerce.coupons.tests.mixins import CouponMixin
from ecommerce.tests.testcases import TestCase

from ecommerce.ucsd_features.constants import CATEGORY_GEOGRAPHY_PROMOTION_SLUG
from ecommerce.ucsd_features.services.coupons import CouponService


coupons_service = CouponService()
Category = get_model('catalogue', 'Category')
OfferAssignment = get_model('offer', 'OfferAssignment')


class CouponsServiceTestCases(DiscoveryTestMixin, CouponMixin, TestCase):
    def setUp(self):
        super(CouponsServiceTestCases, self).setUp()
        self.category = Category.objects.get(slug=CATEGORY_GEOGRAPHY_PROMOTION_SLUG)
        self.course, self.seat = self.create_course_and_seat(seat_type='verified')
        self.coupons = [
            self.create_coupon(
                catalog_query='*:*',
                title='valid coupon',
                course_seat_types='verified',
                start_datetime=datetime.datetime(2019, 1, 1),
                end_datetime=datetime.datetime(2099, 1, 1)
            ),
            self.create_coupon(
                catalog_query='*:*',
                title='Expired coupon',
                course_seat_types='verified',
                start_datetime=datetime.datetime(2019, 1, 1),
                end_datetime=datetime.datetime(2019, 1, 2)
            )
        ]

    def test_get_coupons_by_category_slug(self):
        """
        Test that `get_coupons_by_category_slug` method returns all coupons for provided category slug.
        """
        coupons = coupons_service.get_coupons_by_category_slug(CATEGORY_GEOGRAPHY_PROMOTION_SLUG)
        self.assertItemsEqual(list(coupons), self.coupons)

    def test_filter_coupons_for_course_key(self):
        """
        Test that `filter_coupons_for_course_key` method returns coupons for the provided course key.

        In this test, response from the discovery is stubbed to return results having our course key.
        """
        coupons_queryset = coupons_service.get_coupons_by_category(self.category)
        get_catalog_course_runs_response = {
            'results': [{
                'key': self.course.id,
            }]
        }
        with patch(
                'ecommerce.ucsd_features.services.coupons.get_catalog_course_runs',
                return_value=get_catalog_course_runs_response
        ):
            coupons = coupons_service.filter_coupons_for_course_key(coupons_queryset, self.course.id, self.site)
            self.assertItemsEqual(coupons, self.coupons)

    def test_get_available_vouchers(self):
        """
        Test that `get_available_vouchers` returns available vouchers that means vouchers that are:
        - not expired
        - not assigned to anyone
        """
        expected_vouchers = list(self.coupons[0].coupon_vouchers.all()[0].vouchers.all())
        assigned_voucher = expected_vouchers.pop()

        OfferAssignment.objects.create(
            user_email='test@mail.com',
            code=assigned_voucher.code,
            offer=assigned_voucher.best_offer
        )

        vouchers = coupons_service.get_available_vouchers(self.coupons)
        self.assertItemsEqual(vouchers, expected_vouchers)

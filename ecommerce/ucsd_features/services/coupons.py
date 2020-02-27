"""
Service to provide utils related to Coupons and Vouchers
"""
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import logging
import itertools

from django.db.models import F, Q
from oscar.core.loading import get_model

from ecommerce.coupons.utils import get_catalog_course_runs


logger = logging.getLogger(__name__)

Category = get_model('catalogue', 'Category')
OfferAssignment = get_model('offer', 'OfferAssignment')


class CouponService:

    def get_coupons_by_category_slug(self, category_slug, **options):
        category = Category.objects.get(slug=category_slug)
        return self.get_coupons_by_category(category, **options)

    def get_coupons_by_category(self, category, **options):
        coupons = category.product_set.filter(coupon_vouchers__isnull=False)
        if options.get('only_multi_course_coupons', False):
            # For "Single Course" coupons, there is no value set for `catalog_query`
            coupons = coupons.filter(coupon_vouchers__vouchers__offers__condition__range__catalog_query__isnull=False).distinct()
        return coupons

    def filter_coupons_for_course_key(self, coupons_products, course_key, site=None):
        coupons = []
        for coupon_product in coupons_products:
            try:
                catalog_query = (coupon_product.coupon_vouchers.first().
                                 vouchers.first().
                                 best_offer.condition.range.catalog_query)
            except (KeyError, AttributeError):
                logger.error('Could not get catalog_query for Coupon: {}'.format(coupon_product))
                continue

            response = get_catalog_course_runs(
                site=site,
                query=catalog_query
            )
            is_coupon_available_for_course = bool([
                course_obj['key'] for course_obj in response['results'] if course_obj['key'] == course_key
            ])
            if is_coupon_available_for_course:
                coupons.append(coupon_product)
        return coupons

    def get_available_vouchers(self, coupons):
        all_available_vouchers = []

        for coupon_product in coupons:
            available_vouchers = self._get_available_vouchers_in_coupon(coupon_product)

            if available_vouchers:
                all_available_vouchers.extend(available_vouchers)

            else:
                # If there is no empty voucher in current coupon. Log it.
                # Please note that this doesn't mean that there is no available voucher
                # there may be an applicable available voucher in another coupon
                logger.info(
                    'Vouchers limit for coupon: {} has been reached.'
                    ' Need to make more vouchers for the coupon'.format(coupon_product)
                )
        return all_available_vouchers

    def _get_available_vouchers_in_coupon(self, coupon):
        all_vouchers = []
        coupon_vouchers = coupon.coupon_vouchers.all()

        for coupon_voucher in coupon_vouchers:
            unassigned_vouchers = coupon_voucher.vouchers.exclude(
                offers__offerassignment__code__iexact=F('code')
            )

            if unassigned_vouchers:
                available_vouchers = [
                    x for x in unassigned_vouchers if x.is_available_to_user()[0] and x.is_active()
                ]
                all_vouchers.extend(available_vouchers)

        return all_vouchers

    def is_voucher_available_for_user(self, voucher, user):
        user_email = user.email
        user_assigned_offers = voucher.best_offer.offerassignment_set.filter(
            Q(user_email__isnull=False) & Q(code=voucher.code)
        )
        if not user_assigned_offers.exists():
            return True

        return bool(user_assigned_offers.filter(user_email=user_email))

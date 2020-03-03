"""
Service to provide utils related to Coupons and Vouchers
"""
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import logging

from django.db.models import F, Q
from django.utils import timezone
from oscar.core.loading import get_model

from ecommerce.coupons.utils import get_catalog_course_runs


logger = logging.getLogger(__name__)

Category = get_model('catalogue', 'Category')
Product = get_model('catalogue', 'Product')
OfferAssignment = get_model('offer', 'OfferAssignment')


class CouponService:

    def get_coupons_by_category_slug(self, category_slug, **options):
        category = Category.objects.get(slug=category_slug)
        return self.get_coupons_by_category(category, **options)

    def get_coupons_by_category(self, category, **options):
        coupons = category.product_set.filter(coupon_vouchers__isnull=False)
        if options.get('only_multi_course_coupons', False):
            # For "Single Course" coupons, there is no value set for `catalog_query`
            coupons = coupons.filter(
                coupon_vouchers__vouchers__offers__condition__range__catalog_query__isnull=False
            ).distinct()
        return coupons

    def filter_coupons_for_course_key(self, coupons_products, course_key, site=None):
        coupons = []
        coupons_products = coupons_products.prefetch_related(
            'coupon_vouchers__vouchers__offers__condition__range'
        )
        coupons = [
            coupon for coupon in coupons_products if
            self._is_coupon_valid_for_course(coupon, course_key, site)
        ]
        return coupons

    def _is_coupon_valid_for_course(self, coupon, course_key, site=None):
        try:
            catalog_query = (coupon.coupon_vouchers.all()[0].
                             vouchers.all()[0].
                             best_offer.condition.range.catalog_query)
        except (KeyError, AttributeError, IndexError):
            logger.error('Could not get catalog_query for Coupon: {}'.format(coupon))
            return False

        response = get_catalog_course_runs(
            site=site,
            query=catalog_query
        )
        return bool([
            course_obj.get('key') for course_obj in response['results'] if course_obj.get('key') == course_key
        ])

    def get_available_vouchers(self, coupons):
        all_vouchers = []
        now = timezone.now()

        coupon_ids = [coupon.id for coupon in coupons]
        coupons = Product.objects.filter(id__in=coupon_ids).prefetch_related(
            'coupon_vouchers__vouchers'
        )

        for coupon_product in coupons:
            coupon_vouchers = coupon_product.coupon_vouchers.all()
            for coupon_voucher in coupon_vouchers:
                current_vouchers = coupon_voucher.vouchers.exclude(
                    Q(offers__offerassignment__code__iexact=F('code')) |
                    Q(start_datetime__gt=now) |
                    Q(end_datetime__lt=now)
                )

                if current_vouchers:
                    all_vouchers += list(current_vouchers)
                else:
                    # If there is no empty voucher in current coupon. Log it.
                    # Please note that this doesn't mean that there is no available voucher
                    # there may be an applicable available voucher in another coupon
                    logger.info(
                        'Vouchers limit for coupon: {} has been reached.'
                        ' Need to make more vouchers for the coupon'.format(coupon_product)
                    )

        return all_vouchers

    def is_voucher_available_for_user(self, voucher, user):
        user_email = user.email
        user_assigned_offers = voucher.best_offer.offerassignment_set.filter(
            Q(user_email__isnull=False) & Q(code=voucher.code)
        )
        if not user_assigned_offers.exists():
            return True

        return bool(user_assigned_offers.filter(user_email=user_email))

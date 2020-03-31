"""
Service to provide utils related to Coupons and Vouchers.
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
    """
    This service class provides methods related to coupons.
    """

    def get_coupons_by_category_slug(self, category_slug, **options):
        """
        Returns all Product(s) (type: coupon) having provided category (determined by the slug)
        as one of its categories.

        Arguments:
            category_slug (str): Slug of the category
            options (dict): Extra keyword arguments

            currently supported extra keyword arguments are:
                only_multi_course_coupons (Boolean): if only Coupon Products which are of
                                                    "Multiple Course" type should be returned

        Returns:
            Queryset<Product>: queryset containing the coupon Product(s)
        """
        category = Category.objects.get(slug=category_slug)
        return self.get_coupons_by_category(category, **options)

    def get_coupons_by_category(self, category, **options):
        """
        Returns all Product(s) (type: coupon) having provided category as one of its categories.

        Arguments:
            category_slug (str): Slug of the category
            options (dict): Extra keyword arguments

            currently supported extra keyword arguments are:
                only_multi_course_coupons (Boolean): if only Coupon Products which are of
                                                    "Multiple Course" type should be returned

        Returns:
            Queryset<Product>: queryset containing the coupon Product(s)
        """
        coupons = category.product_set.filter(coupon_vouchers__isnull=False)
        if options.get('only_multi_course_coupons', False):
            # For "Single Course" coupons, there is no value set for `catalog_query`
            coupons = coupons.filter(
                coupon_vouchers__vouchers__offers__condition__range__catalog_query__isnull=False
            ).distinct()
        return coupons

    def filter_coupons_for_course_key(self, coupons_products, course_key, site=None):
        """
        Filters and returns only coupons products from the provided list that are
        applicable on course with the provided course key.

        Arguments:
            coupon_products (queryset<Product>): queryset of coupon Products
            course_key (str): id/course_key of the course
            site (Site)(optional): site object

        Returns:
            List<Product>: list containing the filtered coupon Product(s)
        """
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
        """
        Determines if the coupon is applicable on the provided course_key.

        Arguments:
            coupon (Product): coupon Product
            course_key (str): id/course_key of the course
            site (Site): site object

        Returns:
            bool
        """
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
        """
        Returns available vouchers derived from the provided coupon Products.
        A voucher is considered to be availabe if:
            - voucher is not assigned to anyone
            - voucher's start date is in the past
            - voucher's end date is in the future (voucher is not expired)

        Arguments:
            coupons (list<Product>): list of coupon Products

        Returns:
            list<Voucher>
        """
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
        """
        Determines if the provided voucher is available to be used by the user.

        Arguments:
            voucher (Voucher): voucher object
            user (User): user object

        Returns:
            bool
        """
        user_email = user.email
        user_assigned_offers = voucher.best_offer.offerassignment_set.filter(
            Q(user_email__isnull=False) & Q(code=voucher.code)
        )
        if not user_assigned_offers.exists():
            return True

        return bool(user_assigned_offers.filter(user_email=user_email))

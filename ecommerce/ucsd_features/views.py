# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import logging

from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from rest_framework.generics import GenericAPIView
from oscar.core.loading import get_model

from ecommerce.notifications.notifications import send_notification
from ecommerce.extensions.offer.constants import OFFER_ASSIGNED
from ecommerce.ucsd_features.services.coupons import CouponService
from ecommerce.ucsd_features.utils import send_email_notification

logger = logging.getLogger(__name__)

Category = get_model('catalogue', 'Category')
OfferAssignment = get_model('offer', 'OfferAssignment')
Course = get_model('courses', 'Course')
coupon_service = CouponService()


class AssignVoucherView(GenericAPIView):

    def post(self, request):
        course_key = request.data.get('course_key')
        course_sku = request.data.get('course_sku')
        user_email = request.data.get('user_email')
        site = request.site

        category = Category.objects.get(slug='geography-promotion')

        category.product_set.prefetch_related(
            'coupon_vouchers__vouchers__offers__offerassignment_set'
        )
        category.product_set.prefetch_related('coupon_vouchers__vouchers__offers__conditions__range')

        coupon_products = coupon_service.get_coupons_by_category(category, only_multi_course_coupons=True)
        filtered_coupon_products = coupon_service.filter_coupons_for_course_key(coupon_products, course_key, site)
        available_vouchers = coupon_service.get_available_vouchers(filtered_coupon_products)

        # One of the available vouchers will be assigned to the user.
        # The count should not be negative in any case
        remaining_vouchers_count = max(len(available_vouchers) - 1, 0)

        if remaining_vouchers_count < settings.GEOGRAPHY_DISCOUNT_MIN_VOUCHERS_LIMIT:
            try:
                support_email = settings.ECOMMERCE_SUPPORT_EMAIL

                logger.info('Sending email to support ({}) to notify that course coupons'
                            ' limit has been reached for course: {}. Available vouchers: {}'.format(
                                support_email,
                                course_key,
                                remaining_vouchers_count
                            ))
                coupons_link = '{}{}'.format(settings.ECOMMERCE_URL_ROOT, reverse('coupons:app', args=['']))
                is_email_sent = send_email_notification(support_email, 'COUPONS_LIMIT_REACHED', {
                    'coupons_link': coupons_link,
                    'course_id': course_key
                }, site)

                if is_email_sent:
                    logger.info('Sent an email to support ({}) to notify that course coupons'
                                ' limit has been reached for course: {}'.format(support_email, course_key))

            except Exception as ex:     # pylint: disable=broad-except
                logger.error('Failed to email to support ({}) to notify that course coupons'
                             ' limit has been reached for course: {}\nError: {}'.format(
                                 support_email, course_key, ex.message))

        if remaining_vouchers_count == 0:
            logger.exception('Vouchers count for course: {} is 0'
                             ' therefore no more coupons will be assigned to any user'.format(course_key))

        if not available_vouchers:
            return JsonResponse({}, status=400)

        available_voucher = available_vouchers[0]
        offer = OfferAssignment.objects.create(offer=available_voucher.best_offer,
                                                user_email=user_email,
                                                code=available_voucher.code)
        logger.info('Successfully assigned voucher with code: {} to user: {} for course: {}'.format(
            available_voucher.code, user_email, course_key
        ))

        try:
            course = Course.objects.get(id=course_key)
            course_name = course.name
        except Course.DoesNotExist:
            course_name = course_key

        if course_sku:
            course_checkout_url = '{}{}?sku={}'.format(
                settings.ECOMMERCE_URL_ROOT, reverse('basket:basket-add'), course_sku
            )
        else:
            course_checkout_url = ''

        try:
            send_notification(request.user, 'COUPON_ASSIGNED', {
                'user_email': user_email,
                'course_name': course_name,
                'coupon_code': available_voucher.code,
                'checkout_url': '{}{}?sku={}'.format(
                    settings.ECOMMERCE_URL_ROOT,
                    reverse('basket:basket-add'),
                    course_sku
                ) if course_sku else ''
            }, site)

            logger.info('Successfully sent an email to user: {} about assigned voucher'.format(user_email))

            offer.status = OFFER_ASSIGNED
            offer.save()

        except Exception as ex:     # pylint: disable=broad-except
            logger.error('Failed to send email to user {} with voucher code.'
                            'Error message: {}'.format(user_email, str(ex)))

        finally:
            return JsonResponse({}, status=200)  # pylint: disable=lost-exception


class CourseCouponView(GenericAPIView):
    def post(self, request):
        """
        View to check if geographic discount coupons are available for a course
        """
        course_key = request.data.get('course_key')
        if not course_key:
            logger.error('No course key provided')
            return JsonResponse({}, status=404)
        site = request.site
        category = Category.objects.get(slug='geography-promotion')
        category.product_set.prefetch_related(
            'coupon_vouchers__vouchers__offers__offerassignment_set'
        )

        coupon_products = coupon_service.get_coupons_by_category(category, only_multi_course_coupons=True)
        filtered_coupon_products = coupon_service.filter_coupons_for_course_key(coupon_products, course_key, site)

        if filtered_coupon_products:
            logger.info('{} coupon(s) found for course: {}'.format(len(filtered_coupon_products), course_key))
            return JsonResponse({
                'found': True
            }, status=302)

        else:
            logger.info('No coupons found for course: {}'.format(course_key))
            return JsonResponse({
                'found': False
            }, status=400)

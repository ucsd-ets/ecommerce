# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging

from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from oscar.core.loading import get_model
from rest_framework.views import APIView

from ecommerce.extensions.offer.constants import OFFER_ASSIGNED
from ecommerce.notifications.notifications import send_notification
from ecommerce.ucsd_features.constants import CATEGORY_GEOGRAPHY_PROMOTION_SLUG, COUPON_ASSIGNED, COUPONS_LIMIT_REACHED
from ecommerce.ucsd_features.services.coupons import CouponService
from ecommerce.ucsd_features.utils import send_email_notification

logger = logging.getLogger(__name__)

Category = get_model('catalogue', 'Category')
OfferAssignment = get_model('offer', 'OfferAssignment')
Course = get_model('courses', 'Course')
coupon_service = CouponService()


class AssignVoucherView(APIView):
    """
    View to assign voucher to a user.
    """

    def post(self, request):
        """
        This view assgins a voucher (if available) to user with the provided email.

        If the remaining vouchers count is less than the config GEOGRAPHY_DISCOUNT_MIN_VOUCHERS_LIMIT,
        an email is sent to support to notify them.

        If the voucher is successfully assigned to the user, an email is sent to the user with the coupon code.
        """
        course_key = request.data.get('course_key')
        course_sku = request.data.get('course_sku')
        user_email = request.data.get('user_email')
        support_emails = []
        site = request.site

        category = Category.objects.get(slug=CATEGORY_GEOGRAPHY_PROMOTION_SLUG)

        coupon_products = coupon_service.get_coupons_by_category(category, only_multi_course_coupons=True)
        filtered_coupon_products = coupon_service.filter_coupons_for_course_key(coupon_products, course_key, site)
        available_vouchers = coupon_service.get_available_vouchers(filtered_coupon_products)

        # One of the available vouchers will be assigned to the user.
        # The count should not be negative in any case
        remaining_vouchers_count = max(len(available_vouchers) - 1, 0)

        if remaining_vouchers_count < settings.GEOGRAPHY_DISCOUNT_MIN_VOUCHERS_LIMIT:
            try:
                support_emails = settings.ECOMMERCE_SUPPORT_EMAILS

                logger.info('Sending email to support ({}) to notify that course coupons'
                            ' limit has been reached for course: {}. Available vouchers: {}'.format(
                                support_emails,
                                course_key,
                                remaining_vouchers_count
                            ))
                coupons_link = '{}{}'.format(settings.ECOMMERCE_URL_ROOT, reverse('coupons:app', args=['']))
                is_email_sent = send_email_notification(support_emails, COUPONS_LIMIT_REACHED, {
                    'coupons_link': coupons_link,
                    'course_id': course_key
                }, site)

                if is_email_sent:
                    logger.info('Sent an email to support ({}) to notify that course coupons'
                                ' limit has been reached for course: {}'.format(support_emails, course_key))
            except AttributeError:
                logger.error('Settings has no Attribute `ECOMMERCE_SUPPORT_EMAILS` therefore unable to notify the '
                             'support about coupon exhaustion for course: {}'.format(course_key))
            except Exception as ex:     # pylint: disable=broad-except
                logger.error('Failed to email to support ({}) to notify that course coupons'
                             ' limit has been reached for course: {}\nError: {}'.format(
                                 support_emails, course_key, ex.message))

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

        try:
            send_notification(request.user, COUPON_ASSIGNED, {
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


class CourseCouponView(APIView):
    """
    View to check if a course has applicable coupons or not.
    """

    def post(self, request):
        """
        This view checks if geographic discount coupons are available for a course or not.
        """
        course_key = request.data.get('course_key')
        if not course_key:
            logger.error('No course key provided')
            return JsonResponse({}, status=404)

        site = request.site
        category = Category.objects.get(slug=CATEGORY_GEOGRAPHY_PROMOTION_SLUG)

        coupon_products = coupon_service.get_coupons_by_category(category, only_multi_course_coupons=True)
        filtered_coupon_products = coupon_service.filter_coupons_for_course_key(coupon_products, course_key, site)

        if filtered_coupon_products:
            logger.info('{} coupon(s) found for course: {}'.format(len(filtered_coupon_products), course_key))
            return JsonResponse({
                'found': True
            }, status=200)

        else:
            logger.info('No coupons found for course: {}'.format(course_key))
            return JsonResponse({
                'found': False
            }, status=400)

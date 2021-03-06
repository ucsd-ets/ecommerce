# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging

from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from oscar.core.loading import get_model
from rest_framework.permissions import IsAuthenticated
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
    authentication_classes = (JwtAuthentication,)
    permission_classes = (IsAuthenticated,)

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

                logger.info(  # pylint: disable=logging-not-lazy
                    'Sending email to support (%s) to notify that course coupons'
                    ' limit has been reached for course: %s. Available vouchers: %d' %
                    (support_emails, course_key, remaining_vouchers_count)
                )
                coupons_link = '{}{}'.format(settings.ECOMMERCE_URL_ROOT, reverse('coupons:app', args=['']))
                is_email_sent = send_email_notification(support_emails, COUPONS_LIMIT_REACHED, {
                    'coupons_link': coupons_link,
                    'course_id': course_key
                }, site)

                if is_email_sent:
                    logger.info(  # pylint: disable=logging-not-lazy
                        'Sent an email to support (%s) to notify that course coupons'
                        ' limit has been reached for course: %s' %
                        (support_emails, course_key)
                    )
            except AttributeError:
                logger.error(  # pylint: disable=logging-not-lazy
                    'Settings has no Attribute `ECOMMERCE_SUPPORT_EMAILS` therefore unable to notify the '
                    'support about coupon exhaustion for course: %s' % course_key
                )
            except Exception as ex:     # pylint: disable=broad-except
                logger.error(  # pylint: disable=logging-not-lazy
                    'Failed to email to support (%s) to notify that course coupons'
                    ' limit has been reached for course: %s\nError: %s' %
                    (support_emails, course_key, ex.message)
                )

        if remaining_vouchers_count == 0:
            logger.exception(  # pylint: disable=logging-not-lazy
                'Vouchers count for course: %s is 0 therefore no more'
                ' coupons will be assigned to any user' % course_key)

        if not available_vouchers:
            return JsonResponse({}, status=400)

        available_voucher = available_vouchers[0]
        offer = OfferAssignment.objects.create(offer=available_voucher.best_offer,
                                               user_email=user_email,
                                               code=available_voucher.code)
        logger.info(  # pylint: disable=logging-not-lazy
            'Successfully assigned voucher with code: %s to user: %s for course: %s' %
            (available_voucher.code, user_email, course_key)
        )

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

            logger.info(  # pylint: disable=logging-not-lazy
                'Successfully sent an email to user: %s about assigned voucher' % user_email
            )

            offer.status = OFFER_ASSIGNED
            offer.save()

        except Exception as ex:  # pylint: disable=broad-except
            logger.error(  # pylint: disable=logging-not-lazy
                'Failed to send email to user %s with voucher code. Error message: %s' %
                (user_email, str(ex))
            )

        finally:
            return JsonResponse({}, status=200)  # pylint: disable=lost-exception


class CourseCouponView(APIView):
    """
    View to check if a course has applicable coupons or not.
    """
    authentication_classes = (JwtAuthentication,)
    permission_classes = (IsAuthenticated,)

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
            logger.info(  # pylint: disable=logging-not-lazy
                '%d coupon(s) found for course: %s' % (len(filtered_coupon_products), course_key)
            )
            return JsonResponse({
                'found': True
            }, status=200)

        else:
            logger.info(  # pylint: disable=logging-not-lazy
                'No coupons found for course: %s' % course_key
            )
            return JsonResponse({
                'found': False
            }, status=400)

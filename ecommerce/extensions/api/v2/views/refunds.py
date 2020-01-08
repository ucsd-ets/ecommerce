"""HTTP endpoints for interacting with refunds."""
from django.contrib.auth import get_user_model
from oscar.core.loading import get_model
from rest_framework import generics, status
from rest_framework.exceptions import ParseError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from ecommerce.extensions.api import serializers
from ecommerce.extensions.api.exceptions import BadRequestException
from ecommerce.extensions.api.permissions import CanActForUser
from ecommerce.extensions.payment.exceptions import UnSettledTransaction
from ecommerce.extensions.refund.api import (
    create_refunds,
    create_refunds_for_entitlement,
    find_orders_associated_with_course
)

Order = get_model('order', 'Order')
OrderLine = get_model('order', 'Line')
Refund = get_model('refund', 'Refund')
User = get_user_model()


class RefundCreateView(generics.CreateAPIView):
    """Creates refunds.

    Given a username and course ID or an order number and a course entitlement,
    this view finds and creates a refund for each order matching the following criteria:

        * Order was placed by the User linked to username.
        * Order is in the COMPLETE state.
        * Order has at least one line item associated with the course ID or Course Entitlement.

    Note that only the line items associated with the course ID will be refunded.
    Items associated with a different course ID, or not associated with any course ID, will NOT be refunded.

    With the exception of superusers, users may only create refunds for themselves.
    Attempts to create refunds for other users will fail with HTTP 403.

    If refunds are created, a list of the refund IDs will be returned along with HTTP 201.
    If no refunds are created, HTTP 200 will be returned.
    """
    permission_classes = (IsAuthenticated, CanActForUser)

    def get_serializer(self):
        pass

    def create(self, request, *args, **kwargs):
        """
        Creates refunds, if eligible orders exist.

        This supports creating refunds for both course runs as well as course entitlements.

        Arguments:
            username (string): This is required by both types of refund

            course_run refund:
            course_id (string): The course_id for wchich to refund for the given user

            course_entitlement refund:
            order_number (string): The order for which to refund the coures entitlement
            entitlement_uuid (string): The UUID for the course entitlement for the given order to refund

        Returns:
            refunds (list): List of refunds created
        """

        course_id = request.data.get('course_id')
        username = request.data.get('username')
        order_number = request.data.get('order_number')
        entitlement_uuid = request.data.get('entitlement_uuid')

        refunds = []

        # We should always have a username value as long as CanActForUser is in place.
        if not username:  # pragma: no cover
            raise BadRequestException('No username specified.')

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise BadRequestException('User "{}" does not exist.'.format(username))

        # Try and create a refund for the passed in order
        if entitlement_uuid:
            try:
                order = user.orders.get(number=order_number)
                refunds = create_refunds_for_entitlement(order, entitlement_uuid)
            except (Order.DoesNotExist, OrderLine.DoesNotExist):
                raise BadRequestException('Order {} does not exist.'.format(order_number))
        else:
            if not course_id:
                raise BadRequestException('No course_id specified.')

            # We can only create refunds if the user has orders.
            if user.orders.exists():
                orders = find_orders_associated_with_course(user, course_id)
                refunds = create_refunds(orders, course_id)

        # Return HTTP 201 if we created refunds.
        if refunds:
            refund_ids = [refund.id for refund in refunds]
            return Response(refund_ids, status=status.HTTP_201_CREATED)

        # Return HTTP 200 if we did NOT create refunds.
        return Response([], status=status.HTTP_200_OK)


class RefundProcessView(generics.UpdateAPIView):
    """Process--approve or deny--refunds.

    This view can be used to approve, or deny, a Refund. Under normal conditions, the view returns HTTP status 200
    and a serialized Refund. In the event of an error, the view will still return a serialized Refund (to reflect any
    changed statuses); however, HTTP status will be 500.

    Only staff users are permitted to use this view.
    """
    permission_classes = (IsAuthenticated, IsAdminUser,)
    queryset = Refund.objects.all()
    serializer_class = serializers.RefundSerializer

    def update(self, request, *args, **kwargs):
        APPROVE = 'approve'
        DENY = 'deny'
        APPROVE_PAYMENT_ONLY = 'approve_payment_only'

        action = request.data.get('action', '').lower()

        if action not in (APPROVE, DENY, APPROVE_PAYMENT_ONLY):
            raise ParseError('The action [{}] is not valid.'.format(action))

        refund = self.get_object()
        result = False
        is_unsettled = False

        if action in (APPROVE, APPROVE_PAYMENT_ONLY):
            revoke_fulfillment = action == APPROVE
            try:
                result = refund.approve(revoke_fulfillment=revoke_fulfillment)
            except UnSettledTransaction:
                is_unsettled = True
        elif action == DENY:
            result = refund.deny()

        http_status = status.HTTP_200_OK if result else status.HTTP_500_INTERNAL_SERVER_ERROR
        serializer = self.get_serializer(refund)
        response_dict = serializer.data

        if is_unsettled:
            response_dict.update({'status_code': 54})

        return Response(response_dict, status=http_status)

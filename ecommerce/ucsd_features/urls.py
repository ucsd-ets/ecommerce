from django.conf.urls import url

from ecommerce.ucsd_features import views

app_name = 'ucsd_features'

urlpatterns = [
    url(r'^assign_voucher/$', views.AssignVoucherView.as_view(), name='assign_voucher'),
    url(r'^check_course_coupon/$', views.CourseCouponView.as_view(), name='check_course_coupon'),
]

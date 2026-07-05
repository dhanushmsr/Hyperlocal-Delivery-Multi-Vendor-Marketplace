from django.urls import path
from . import views

urlpatterns = [
    # 1. The New Splash Screen
    path('', views.welcome_page, name='welcome'),

    # 2. FASTX Main Storefront
    path('store/', views.customer_home, name='customer_home'),
    path('terms-and-conditions/', views.terms_and_conditions, name='terms'),
    path('privacy-policy/', views.privacy_policy, name='privacy'),

    # 3. Unified Authentication
    path('login/', views.unified_login, name='unified_login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.customer_register, name='customer_register'),
    path('forgot-password/', views.forgot_password, name='forgot_password'),

    # 4. Dashboards
    path('vendor-dashboard/', views.vendor_dashboard, name='vendor_dashboard'),
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    
    # 5. Shopping Cart & Checkout
    path('cart/', views.cart_checkout, name='cart_checkout'),
    path('add-to-cart/', views.add_to_cart, name='add_to_cart'),
    path('update-location/', views.update_location, name='update_location'),
    path('orders/', views.track_orders, name='track_orders'),

    # Order Tracking & AJAX
    path('cancel-order/', views.cancel_order, name='cancel_order'),
    path('order-status/<int:order_id>/', views.get_order_status, name='get_order_status'),

    # FASTX Wallet System
    path('wallet/', views.wallet_dashboard, name='wallet_dashboard'),
    path('wallet/add-funds/', views.add_funds_api, name='add_funds_api'),

    # Wishlist System
    path('wishlist/', views.view_wishlist, name='view_wishlist'),
    path('wishlist/toggle/', views.toggle_wishlist, name='toggle_wishlist'),

    # Settings
    path('settings/addresses/', views.saved_addresses, name='saved_addresses'),
    path('settings/', views.settings_overview, name='settings_overview'),
    path('settings/update-profile/', views.update_profile, name='update_profile'),
    path('settings/update-preferences/', views.update_preferences, name='update_preferences'),
    path('settings/change-password/', views.change_password, name='change_password'),
    
    # Admin API & Management
    path('admin-dashboard/customers/', views.manage_customers, name='manage_customers'),
    path('admin-dashboard/sellers/', views.manage_sellers, name='manage_sellers'),
    path('admin-api/toggle-user/<int:user_id>/', views.toggle_user_status, name='toggle_user_status'),
    path('admin-api/update-order-status/<int:order_id>/', views.update_order_status, name='update_order_status'),
    path('admin-api/approve-seller/<int:user_id>/', views.approve_seller, name='approve_seller'),
    path('admin-dashboard/categories/', views.manage_categories, name='manage_categories'),
    path('admin-dashboard/categories/delete/<int:category_id>/', views.delete_category, name='delete_category'),
    path('admin-api/view-doc/<str:doc_type>/<int:user_id>/', views.admin_view_document, name='admin_view_doc'),

    # Admin Promotions & Feedback
    path('admin-dashboard/promotions/', views.manage_promotions, name='manage_promotions'),
    path('admin-dashboard/promotions/delete/<int:promo_id>/', views.delete_promotion, name='delete_promotion'),
    path('admin-dashboard/feedback/', views.admin_feedback, name='admin_feedback'),
    path('admin-dashboard/feedback/clear/', views.clear_all_feedback, name='clear_all_feedback'),

    # Financials & Payouts (Admin)
    path('admin-dashboard/financials/', views.admin_financials, name='admin_financials'),
    path('admin-api/clear-payout/<int:vendor_id>/', views.clear_vendor_payout, name='clear_vendor_payout'),

    # Vendor API & Management
    path('register-seller/', views.register_seller, name='register_seller'),
    path('vendor-dashboard/history/', views.vendor_order_history, name='vendor_order_history'), 
    path('vendor-dashboard/products/', views.vendor_products, name='vendor_products'),
    path('vendor-dashboard/earnings/', views.vendor_earnings, name='vendor_earnings'),
    path('vendor-dashboard-data/', views.vendor_dashboard_data, name='vendor_dashboard_data'),
    path('vendor-api/add-product/', views.vendor_add_product, name='vendor_add_product'),
    path('vendor-api/delete-product/<int:product_id>/', views.vendor_delete_product, name='vendor_delete_product'),
    path('vendor-api/edit-product/<int:product_id>/', views.vendor_edit_product, name='vendor_edit_product'),
    path('vendor-api/clear-history/', views.vendor_clear_all_history, name='vendor_clear_all_history'),
    path('vendor-api/assign-rider/<int:order_id>/', views.vendor_assign_rider, name='vendor_assign_rider'),
    path('vendor-api/update-order-status/<int:order_id>/', views.vendor_update_order_status, name='vendor_update_order_status'),
    path('vendor-api/delete-order/<int:order_id>/', views.vendor_delete_order, name='vendor_delete_order'),
    path('vendor-api/<str:action>/<int:order_id>/', views.vendor_dispatcher, name='vendor_dispatcher'),
    path('rider-api/toggle-online/', views.rider_toggle_online, name='rider_toggle_online'),

    # Rider Dashboard & API
    path('rider/register/', views.rider_register, name='rider_register'),
    path('rider-dashboard/', views.rider_dashboard, name='rider_dashboard'),
    path('auth/rider-login/', views.rider_login, name='rider_login'),
    path('rider-dashboard/history/', views.rider_history, name='rider_history'),
    path('rider-api/accept-order/<int:order_id>/', views.accept_order, name='accept_order'),
    path('rider-api/reject-order/<int:order_id>/', views.reject_order, name='reject_order'),
    path('rider-api/update-status/<int:order_id>/', views.rider_update_status, name='rider_update_status'),
    path('rider-api/respond-assignment/<int:order_id>/', views.rider_respond_to_assignment, name='rider_respond_to_assignment'),
    path('rider-api/verify-payout/', views.rider_verify_payout_api, name='rider_verify_payout_api'),

    # Delivery Partner Management (Admin)
    path('admin-dashboard/manage-delivery-partners/', views.manage_delivery_partners, name='manage_delivery_partners'),
    path('admin-api/delivery-partner/update-status/<int:partner_id>/', views.update_partner_status, name='update_partner_status'),
    path('admin-dashboard/delivery-partner-details/<int:partner_id>/', views.delivery_partner_details, name='delivery_partner_details'),

    # Ordering & Invoices
    path('update-cart/', views.update_cart, name='update_cart'),
    path('invoice/<int:order_id>/<str:invoice_type>/', views.generate_invoice, name='generate_invoice'),
    
    # --- TWILIO SECURE OTP ENDPOINTS ---
    path('api/send-otp/', views.send_otp_api, name='send_otp_api'),
    path('api/verify-otp/', views.verify_otp_api, name='verify_otp_api'),
    
    path('order-feedback/<int:order_id>/', views.submit_feedback, name='submit_feedback'),
    path('admin-dashboard/orders/', views.admin_manage_orders, name='admin_manage_orders'),
    path('admin-dashboard/settlements/', views.admin_settlements, name='admin_settlements'),
    path('admin-dashboard/settlements/clear/', views.clear_settlement, name='clear_settlement'),
    path('admin-dashboard/financials/clear/', views.clear_payouts, name='clear_payouts'),
    path('api/account/delete/initiate/', views.initiate_account_deletion, name='initiate_account_deletion'),
    path('api/account/delete/confirm/', views.confirm_account_deletion, name='confirm_account_deletion'),
    path('payment/verify/', views.payment_verification, name='payment_verification'),
    path('product-tag/<str:tag_name>/', views.product_tag_view, name='product_tag_view'),
    path('admin-panel/payments/', views.admin_payment_history, name='admin_payment_history'),
    path('admin-panel/payments/clear/', views.admin_clear_payment_history, name='admin_clear_payment_history'),
    path('admin-panel/coupons/', views.manage_coupons, name='manage_coupons'),
    path('api/validate-coupon/', views.validate_coupon_api, name='validate_coupon_api'),
    path('admin-api/wallet/search/', views.admin_search_wallet, name='admin_search_wallet'),
    path('admin-api/wallet/update/<int:user_id>/', views.admin_update_wallet, name='admin_update_wallet'),

    
]
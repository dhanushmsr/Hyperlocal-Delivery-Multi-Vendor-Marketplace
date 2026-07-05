import os
import json
import time
import traceback
import random
import re
import requests
import razorpay
from decimal import Decimal
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.http import JsonResponse, FileResponse, HttpResponseForbidden
from django.contrib.auth.forms import PasswordChangeForm
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.db.models import Sum, Count, F, Q
from django.utils import timezone
from django.conf import settings
from twilio.rest import Client

# Import your database models
from .models import Order, OrderItem, Category, Product, Wallet, WalletTransaction, Wishlist, Address, Vendor, RiderProfile, UserSettings, CustomerProfile, Promotion, Feedback, Coupon, CouponUsage

User = get_user_model() 

# ==========================================
# 0. WELCOME / SPLASH SCREEN
# ==========================================
def welcome_page(request):
    if request.user.is_authenticated:
        return route_user_by_role(request.user)
    
    promotions = Promotion.objects.filter(is_active=True)[:4]
    categories = Category.objects.all()
    
    context = {
        'promotions': promotions,
        'categories': categories
    }
    return render(request, 'customer/welcome.html', context)

def route_user_by_role(user):
    if user.is_superuser:
        return redirect('admin_dashboard')
    elif getattr(user, 'is_vendor', False): 
        return redirect('vendor_dashboard')
    elif hasattr(user, 'customer_profile') and user.customer_profile.phone_number:
        return redirect('customer_home')
    elif hasattr(user, 'rider_profile'): 
        return redirect('rider_dashboard')
    else:
        return redirect('customer_home')

# ==========================================
# 1. AUTHENTICATION & REGISTRATION
# ==========================================
def unified_login(request):
    if request.method == 'POST':
        login_id = request.POST.get('login_id', '').strip()
        password = request.POST.get('password', '').strip()

        if '@' in login_id:
            if not password:
                messages.error(request, "Password is required for Email login.")
                return redirect('unified_login')

            user = authenticate(request, username=login_id, password=password)
            
            if user is not None:
                if user.is_superuser or getattr(user, 'is_staff', False):
                    login(request, user)
                    return redirect('admin_dashboard')
                elif getattr(user, 'is_vendor', False):
                    if not getattr(user, 'is_approved_vendor', False):
                        messages.error(request, "Login denied: Your seller account is pending admin approval.")
                        return redirect('unified_login')
                    login(request, user)
                    return redirect('vendor_dashboard')
                else:
                    messages.error(request, "Customers must log in using Phone Number & OTP.")
                    return redirect('unified_login')
            else:
                messages.error(request, "Invalid Email or Password.")
                return redirect('unified_login')

        else:
            if not request.session.get('otp_verified', False):
                messages.error(request, "Please request and verify your OTP to login.")
                return redirect('unified_login')

            verified_phone = request.session.get('verified_phone', '')
            if not verified_phone:
                messages.error(request, "Session expired. Please verify your OTP again.")
                return redirect('unified_login')

            clean_phone = verified_phone[-10:] if len(verified_phone) >= 10 else verified_phone
            user = None
            
            vendor = Vendor.objects.filter(phone_number__endswith=clean_phone).first()
            if vendor and vendor.user:
                user = vendor.user
            else:
                customer = CustomerProfile.objects.filter(phone_number__endswith=clean_phone).first()
                if customer and customer.user:
                    user = customer.user

            if user:
                is_vendor = getattr(user, 'is_vendor', False)
                if is_vendor and not getattr(user, 'is_approved_vendor', False):
                    messages.error(request, "Login denied: Your seller account is pending admin approval.")
                    return redirect('unified_login')
                    
                login(request, user)
                request.session.pop('otp_verified', None)
                request.session.pop('verified_phone', None)
                request.session.modified = True
                return redirect('vendor_dashboard') if is_vendor else redirect('customer_home')

            else:
                if RiderProfile.objects.filter(phone_number__endswith=clean_phone).exists():
                    messages.error(request, "This account belongs to a Delivery Partner. Please use the Rider App.")
                else:
                    messages.error(request, "No account found with this number. Please register first.")
                return redirect('unified_login')

    return render(request, 'auth/unified_login.html')

def rider_login(request):
    if request.method == 'POST':
        if request.session.get('otp_verified', False):
            verified_phone = request.session.get('verified_phone')
            
            rider = RiderProfile.objects.filter(phone_number=verified_phone).first()
            
            if not rider:
                messages.error(request, "No Delivery Partner found with this mobile number. Please register first.")
                return redirect('rider_login')
                
            user = rider.user
            if not user.is_active:
                messages.error(request, "Your account has been blocked. Contact Admin.")
                return redirect('rider_login')
                
            if not rider.is_approved:
                messages.error(request, "Your account is pending Admin approval.")
                return redirect('rider_login')
                
            login(request, user)
            request.session.pop('otp_verified', None)
            request.session.pop('verified_phone', None)
            request.session.modified = True
            
            return redirect('rider_dashboard')
        else:
            messages.error(request, "Security Error: Please verify your OTP to login.")
            return redirect('rider_login')
            
    return render(request, 'auth/rider_login.html')

@transaction.atomic
def customer_register(request):
    if request.user.is_authenticated:
        return route_user_by_role(request.user)

    if request.method == 'POST':
        if not request.session.get('otp_verified', False):
            messages.error(request, "Security Error: You must verify your phone number with an OTP before registering.")
            return redirect('customer_register')

        verified_phone = request.session.get('verified_phone')
        email = request.POST.get('email')

        if Vendor.objects.filter(phone_number=verified_phone).exists():
            messages.error(request, "Seller accounts cannot be used as customer accounts. Please use a different number.")
            return redirect('customer_register')

        if CustomerProfile.objects.filter(phone_number=verified_phone).exists():
            messages.error(request, "A customer account with this phone number already exists. Please login instead.")
            return redirect('customer_register')

        try:
            existing_rider = RiderProfile.objects.filter(phone_number=verified_phone).first()
            
            if existing_rider:
                user = existing_rider.user
            else:
                if User.objects.filter(email=email).exists():
                    messages.error(request, "An account with this email already exists.")
                    return redirect('customer_register')

                password = request.POST.get('password')
                confirm_password = request.POST.get('confirm_password')

                if password != confirm_password:
                    messages.error(request, "Passwords do not match.")
                    return redirect('customer_register')

                user = User.objects.create_user(
                    username=email, 
                    email=email, 
                    password=password, 
                    first_name=request.POST.get('first_name'),
                    last_name=request.POST.get('last_name')
                )
            
            profile = getattr(user, 'customer_profile', None)
            if not profile:
                profile = CustomerProfile.objects.create(user=user, phone_number=verified_phone)
            else:
                profile.phone_number = verified_phone
                
            profile.address = request.POST.get('address')
            profile.city = request.POST.get('city')
            profile.pincode = request.POST.get('pincode')
            if request.FILES.get('profile_picture'):
                profile.profile_picture = request.FILES.get('profile_picture')
            profile.save()

            Address.objects.create(
                user=user, title='Home', street=profile.address,
                city=profile.city, pincode=profile.pincode, is_default=True
            )
            
            login(request, user)
            request.session.pop('otp_verified', None)
            request.session.pop('verified_phone', None)
            request.session.modified = True

            messages.success(request, f"Welcome to FASTX! Your account is ready.")
            return redirect('customer_home')
            
        except Exception as e:
            messages.error(request, f"Registration failed: {str(e)}")
            return redirect('customer_register')

    return render(request, 'auth/register.html')

from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import transaction
from django.views.decorators.http import require_http_methods

@require_http_methods(["GET", "POST"])
@transaction.atomic
def rider_register(request):
    if request.method == 'POST':
        # 1. Enforce OTP Verification
        if not request.session.get('otp_verified', False):
            messages.error(request, "Security Error: You must verify your phone number with an OTP before registering.")
            return redirect('rider_register')

        verified_phone = request.session.get('verified_phone')
        email = request.POST.get('email')
        age = int(request.POST.get('age', 0))
        
        # 2. Age Validation
        if age < 18:
            messages.error(request, "Must be 18+ to join the fleet.")
            return redirect('rider_register')
            
        try:
            # 3. Cross-Profile Collision Checks
            if Vendor.objects.filter(phone_number=verified_phone).exists():
                messages.error(request, "Seller accounts cannot be used to register as a Delivery Partner. Please use a different phone number.")
                return redirect('rider_register')

            if RiderProfile.objects.filter(phone_number=verified_phone).exists():
                messages.error(request, "You are already registered as a Delivery Partner. Please login.")
                return redirect('rider_login')

            # 4. Handle Existing Customers vs New Users
            existing_customer = CustomerProfile.objects.filter(phone_number=verified_phone).first()
            
            if existing_customer:
                user = existing_customer.user
            else:
                if User.objects.filter(email=email).exists():
                    messages.error(request, "An account with this email already exists.")
                    return redirect('rider_register')
                    
                password = request.POST.get('password')
                user = User.objects.create_user(
                    username=email, 
                    email=email, 
                    password=password, 
                    first_name=request.POST.get('first_name'), 
                    last_name=request.POST.get('last_name')
                )

            # 5. Create Rider Profile with Comprehensive Banking Details
            RiderProfile.objects.create(
                user=user, 
                phone_number=verified_phone, 
                age=age, 
                vehicle_type=request.POST.get('vehicle_type'), 
                license_number=request.POST.get('license_number'),
                license_photo=request.FILES.get('license_photo'), 
                profile_photo=request.FILES.get('profile_photo'), 
                bank_account_name=request.POST.get('bank_account_name', ''), 
                bank_account_number=request.POST.get('account_number', ''), # Matches the HTML input name
                bank_name=request.POST.get('bank_name', ''),
                ifsc_code=request.POST.get('ifsc_code', ''),
                passbook_photo=request.FILES.get('passbook_photo'),
                accepted_terms=True, 
                accepted_traffic_policy=True
            )
            
            # 6. Clean Up Session
            request.session.pop('otp_verified', None)
            request.session.pop('verified_phone', None)
            request.session.modified = True
            
            messages.success(request, "Rider Application submitted! Awaiting Admin approval.")
            return redirect('customer_home')
            
        except Exception as e:
            messages.error(request, f"Registration failed: {str(e)}")
            return redirect('rider_register')

    return render(request, 'auth/rider_register.html')
@transaction.atomic
def register_seller(request):
    if request.method == 'POST':
        if not request.session.get('otp_verified', False):
            messages.error(request, "Security Error: You must verify your phone number with an OTP before registering.")
            return render(request, 'auth/register_seller.html')

        phone_number = request.session.get('verified_phone')
        email = request.POST.get('email')
        
        if CustomerProfile.objects.filter(phone_number=phone_number).exists() or \
           RiderProfile.objects.filter(phone_number=phone_number).exists() or \
           Vendor.objects.filter(phone_number=phone_number).exists():
            messages.error(request, "This phone number is already in use by another account. Sellers must use a completely unique mobile number.")
            return render(request, 'auth/register_seller.html')

        if User.objects.filter(email=email).exists():
            messages.error(request, "An account with this email already exists.")
            return render(request, 'auth/register_seller.html')

        try:
            password = request.POST.get('password')
            business_name = request.POST.get('business_name')
            
            user = User.objects.create_user(
                username=email, email=email, password=password, 
                first_name=request.POST.get('first_name', ''), last_name=request.POST.get('last_name', '')
            )
            user.is_vendor = True
            user.is_approved_vendor = False 
            user.save()
            
            Vendor.objects.create(
                user=user, 
                business_name=business_name, 
                gst_number=request.POST.get('gst_number', ''), 
                phone_number=phone_number, 
                address=request.POST.get('address'),
                bank_account_name=request.POST.get('bank_account_name', ''), 
                bank_name=request.POST.get('bank_name', ''), 
                ifsc_code=request.POST.get('ifsc_code', ''), 
                account_number=request.POST.get('account_number', ''), 
                profile_photo=request.FILES.get('profile_photo'), 
                aadhaar_card=request.FILES.get('aadhaar_card'), 
                passbook_photo=request.FILES.get('passbook_photo')
            )

            request.session.pop('otp_verified', None)
            request.session.pop('verified_phone', None)
            messages.success(request, "Seller Application submitted successfully! Please wait for Admin approval.")
            return redirect('unified_login')
            
        except Exception as e:
            messages.error(request, f"Submission error: {str(e)}")
            return render(request, 'auth/register_seller.html')
            
    return render(request, 'auth/register_seller.html')

def logout_view(request):
    logout(request)
    request.session.flush() 
    return redirect('unified_login')


# ==========================================
# 2. CUSTOMER STOREFRONT
# ==========================================
def customer_home(request):
    categories = Category.objects.prefetch_related('items').all()
    promotions = Promotion.objects.filter(is_active=True)
    return render(request, 'customer/home.html', {
        'categories': categories,
        'promotions': promotions
    })

# ==========================================
# 3. HIGHLY FUNCTIONAL UNIFIED CHECKOUT
# ==========================================
def add_to_cart(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            product_id = str(data.get('id'))
            price = float(data.get('price'))
            name = data.get('name')
            image_url = data.get('image')
            requested_qty = int(data.get('quantity', 1))

            product = Product.objects.get(id=product_id)
            if 'cart' not in request.session: request.session['cart'] = {}
            cart = request.session['cart']
            
            current_cart_qty = cart.get(product_id, {}).get('quantity', 0)
            new_intended_qty = current_cart_qty + requested_qty

            if new_intended_qty > product.stock:
                available_left = product.stock - current_cart_qty
                msg = f"Cannot add {requested_qty}. Only {available_left} more available." if available_left > 0 else "You already have all available stock in your cart."
                return JsonResponse({'status': 'error', 'message': msg})

            if product_id in cart:
                cart[product_id]['quantity'] = new_intended_qty
                cart[product_id]['total'] = cart[product_id]['quantity'] * price
            else:
                cart[product_id] = {'name': name, 'price': price, 'quantity': requested_qty, 'total': price * requested_qty, 'image': image_url, 'category': product.category.name if product.category else 'Store Item'}

            request.session['cart'] = cart
            request.session.modified = True 
            return JsonResponse({'status': 'success', 'cart_count': sum(item['quantity'] for item in cart.values())})
        except Product.DoesNotExist: return JsonResponse({'status': 'error', 'message': 'Product is no longer available.'})
        except Exception as e: return JsonResponse({'status': 'error', 'message': 'Server error: ' + str(e)})
    return JsonResponse({'status': 'error', 'message': 'Invalid request.'})

@csrf_exempt
@require_POST
def update_cart(request):
    try: 
        data = json.loads(request.body)
    except json.JSONDecodeError: 
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON data'}, status=400)

    product_id, action = data.get('product_id'), data.get('action') 
    
    try: 
        product = Product.objects.get(id=product_id)
    except (Product.DoesNotExist, ValueError): 
        return JsonResponse({'status': 'error', 'message': 'Product not found'}, status=404)
        
    cart = request.session.get('cart', {})
    product_id_str = str(product_id) 
    
    if product_id_str in cart:
        if action == 'increase': 
            if cart[product_id_str]['quantity'] < product.stock:
                cart[product_id_str]['quantity'] += 1
            else:
                return JsonResponse({'status': 'error', 'message': f'Max stock reached. Only {product.stock} available.'})
                
        elif action == 'decrease':
            cart[product_id_str]['quantity'] -= 1
            if cart[product_id_str]['quantity'] <= 0: 
                cart.pop(product_id_str)
                
    if product_id_str in cart:
        cart[product_id_str]['total'] = cart[product_id_str]['quantity'] * cart[product_id_str]['price']
            
    request.session['cart'] = cart
    request.session.modified = True
    
    return JsonResponse({'status': 'success', 'cart': cart})

def update_location(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        request.session['user_location'] = {'address': data.get('address'), 'pincode': data.get('pincode'), 'city': data.get('city')}
        request.session.modified = True
        return JsonResponse({'status': 'success'})

@login_required
@transaction.atomic
def cart_checkout(request):
    cart = request.session.get('cart', {})
    cart_items = cart.values()
    
    subtotal = Decimal('0.00')
    for item in cart_items:
        subtotal += Decimal(str(item['price'])) * int(item['quantity'])

    gst_tax = (subtotal * Decimal('0.05')).quantize(Decimal('0.01'))
    
    # NEW LOGIC: Delivery Fee is 40 Rs if under 150, 10 Rs if over 150
    delivery_fee = Decimal('40.00') if subtotal < Decimal('150.00') else Decimal('10.00')
    
    handling_charge = Decimal('5.00') if subtotal > Decimal('0.00') else Decimal('0.00')
    initial_grand_total = subtotal + gst_tax + delivery_fee + handling_charge
    
    if request.method == 'POST':
        payment_method = request.POST.get('payment_method')
        address_id = request.POST.get('address_id')
        delivery_address_text = request.POST.get('delivery_address_text', 'Address not provided')
        use_wallet = request.POST.get('use_wallet') == 'true'
        coupon_code = request.POST.get('coupon_code', '').strip().upper()

        discount_amount = Decimal('0.00')
        applied_coupon_obj = None
        
        if coupon_code:
            applied_coupon_obj = Coupon.objects.filter(code=coupon_code, is_active=True).first()
            if applied_coupon_obj and not CouponUsage.objects.filter(user=request.user, coupon=applied_coupon_obj).exists():
                is_valid_category = True
                if applied_coupon_obj.category_restriction:
                    is_valid_category = any(item.get('category') == applied_coupon_obj.category_restriction.name for item in cart_items)
                if is_valid_category and subtotal >= applied_coupon_obj.min_order_value:
                    discount_amount = applied_coupon_obj.discount_amount
            else:
                applied_coupon_obj = None 
                
        grand_total = max(Decimal('0.00'), initial_grand_total - discount_amount)

        address = None
        if address_id and address_id.isdigit():
            address = Address.objects.filter(id=address_id, user=request.user).first()
            if address:
                delivery_address_text = f"{address.flat_no}, {address.street}, {address.city} - {address.pincode}"

        wallet_applied_amount = Decimal('0.00')
        user_wallet = None
        if use_wallet:
            try:
                user_wallet = Wallet.objects.select_for_update().get(user=request.user)
                wallet_applied_amount = min(user_wallet.balance, grand_total)
                grand_total -= wallet_applied_amount
            except Wallet.DoesNotExist:
                pass

        vendor_orders = {}
        for product_id, details in cart.items():
            product = Product.objects.select_for_update().get(id=int(product_id))
            vendor_profile = Vendor.objects.get(user=product.vendor)
            
            if vendor_profile.id not in vendor_orders:
                vendor_orders[vendor_profile.id] = {'vendor': vendor_profile, 'subtotal': Decimal('0.00'), 'items': []}
            
            item_cost = Decimal(str(details['price'])) * int(details['quantity'])
            vendor_orders[vendor_profile.id]['subtotal'] += item_cost
            vendor_orders[vendor_profile.id]['items'].append({'product': product, 'quantity': details['quantity'], 'price': details['price']})

        parent_order_id = f"FX-{int(time.time())}"
        created_orders = []

        for v_id, v_data in vendor_orders.items():
            v_sub = v_data['subtotal']
            ratio = v_sub / subtotal
            
            v_tax = (gst_tax * ratio).quantize(Decimal('0.01'))
            v_delivery = (delivery_fee * ratio).quantize(Decimal('0.01'))
            v_handling = (handling_charge * ratio).quantize(Decimal('0.01'))
            v_wallet = (wallet_applied_amount * ratio).quantize(Decimal('0.01'))
            v_discount = (discount_amount * ratio).quantize(Decimal('0.01'))
            
            # NEW LOGIC: Admin Platform Fee is 30%
            v_platform_fee = (v_sub * Decimal('0.30')).quantize(Decimal('0.01'))
            
            calc_total = v_sub + v_tax + v_delivery + v_handling - v_wallet - v_discount
            v_grand_total = max(Decimal('0.00'), calc_total).quantize(Decimal('0.01'))
            
            combined_delivery_fee = v_delivery + v_handling
            
            order = Order.objects.create(
                order_id=f"{parent_order_id}-{v_id}", 
                user=request.user, vendor=v_data['vendor'],
                subtotal=v_sub, tax_amount=v_tax, delivery_fee=combined_delivery_fee,
                wallet_amount_used=v_wallet, coupon_code=coupon_code if discount_amount > 0 else None,
                discount_amount=v_discount, platform_fee=v_platform_fee, total_amount=v_grand_total,
                delivery_address=delivery_address_text, payment_method=payment_method,
                status='Pending_Payment' if payment_method == 'Online' and grand_total > 0 else 'Confirmed'
            )
            created_orders.append(order)

            for item_data in v_data['items']:
                OrderItem.objects.create(
                    order=order, product=item_data['product'], product_name=item_data['product'].name,
                    price=item_data['price'], quantity=item_data['quantity']
                )
                if item_data['product'].stock >= int(item_data['quantity']):
                    item_data['product'].stock -= int(item_data['quantity'])
                    item_data['product'].save()

        if user_wallet and wallet_applied_amount > 0:
            user_wallet.balance -= wallet_applied_amount
            user_wallet.save()
            WalletTransaction.objects.create(wallet=user_wallet, amount=wallet_applied_amount, transaction_type='DEBIT', description=f'Used for Order {parent_order_id}')

        if applied_coupon_obj:
            CouponUsage.objects.create(user=request.user, coupon=applied_coupon_obj)

        if payment_method == 'Online' and grand_total > 0:
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            payment_data = {"amount": int(grand_total * 100), "currency": "INR", "receipt": parent_order_id, "payment_capture": 1}
            razorpay_order = client.order.create(data=payment_data)
            
            for o in created_orders:
                o.razorpay_order_id = razorpay_order['id']
                o.save()
            
            return JsonResponse({
                'status': 'razorpay_init',
                'razorpay_order_id': razorpay_order['id'],
                'amount': payment_data['amount'],
                'key': settings.RAZORPAY_KEY_ID,
                'user_name': request.user.first_name,
                'user_email': request.user.email,
                'user_phone': request.user.customer_profile.phone_number if hasattr(request.user, 'customer_profile') else ""
            })
        else:
            request.session['cart'] = {}
            request.session.modified = True
            return JsonResponse({'status': 'success', 'order_id': parent_order_id})

    coupon_code_get = request.GET.get('coupon', '').strip().upper()
    discount_get = Decimal('0.00')
    
    if coupon_code_get:
        get_coupon_obj = Coupon.objects.filter(code=coupon_code_get, is_active=True).first()
        if get_coupon_obj and not CouponUsage.objects.filter(user=request.user, coupon=get_coupon_obj).exists():
            is_valid_category = True
            if get_coupon_obj.category_restriction:
                is_valid_category = any(item.get('category') == get_coupon_obj.category_restriction.name for item in cart_items)
            
            if is_valid_category and subtotal >= get_coupon_obj.min_order_value:
                discount_get = get_coupon_obj.discount_amount

    grand_total_get = max(Decimal('0.00'), initial_grand_total - discount_get)
    
    context = {
        'cart_items': cart_items,
        'item_subtotal': subtotal,
        'gst_tax': gst_tax,
        'delivery_fee': delivery_fee,
        'handling_charge': handling_charge,
        'discount_amount': discount_get,
        'grand_total': grand_total_get,
        'coupon_code': coupon_code_get,
        'coupon_applied': discount_get > 0,
        'saved_addresses': Address.objects.filter(user=request.user),
        'available_coupons': Coupon.objects.filter(is_active=True).order_by('-discount_amount')
    }
    return render(request, 'customer/cart.html', context)


@csrf_exempt
def payment_verification(request):
    if request.method == "POST":
        data = request.POST
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        
        try:
            client.utility.verify_payment_signature({
                'razorpay_order_id': data.get('razorpay_order_id'),
                'razorpay_payment_id': data.get('razorpay_payment_id'),
                'razorpay_signature': data.get('razorpay_signature')
            })
            
            orders = Order.objects.filter(razorpay_order_id=data.get('razorpay_order_id'))
            for order in orders:
                order.razorpay_payment_id = data.get('razorpay_payment_id')
                order.razorpay_signature = data.get('razorpay_signature')
                order.is_paid = True
                order.status = 'Confirmed'
                order.save()
            
            if 'cart' in request.session:
                del request.session['cart']
                
            return render(request, 'customer/payment_success.html', {'order': orders.first()})
            
        except razorpay.errors.SignatureVerificationError:
            orders = Order.objects.filter(razorpay_order_id=data.get('razorpay_order_id'))
            orders.update(status='Cancelled')
            return render(request, 'customer/payment_failed.html', {'order': orders.first() if orders.exists() else None})


# ==========================================
# 4. ORDER TRACKING & INVOICES
# ==========================================
@login_required
def track_orders(request):
    active_orders = Order.objects.filter(user=request.user).exclude(status__in=['Delivered', 'Cancelled', 'Pending_Payment']).order_by('-created_at')
    past_orders = Order.objects.filter(user=request.user, status__in=['Delivered', 'Cancelled']).order_by('-created_at')
    return render(request, 'customer/orders.html', {'active_orders': active_orders, 'past_orders': past_orders})

@login_required
def cancel_order(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        try:
            order = Order.objects.get(id=data['order_id'], user=request.user)
            if order.status == 'Confirmed':
                order.status = 'Cancelled'
                order.save()
                return JsonResponse({'status': 'success', 'message': 'Order cancelled successfully.'})
            else:
                return JsonResponse({'status': 'error', 'message': 'Order is already being processed and cannot be cancelled.'})
        except Order.DoesNotExist: return JsonResponse({'status': 'error', 'message': 'Order not found.'})

@login_required
def get_order_status(request, order_id):
    try:
        order = Order.objects.get(id=order_id, user=request.user)
        return JsonResponse({'status': 'success', 'order_status': order.status})
    except Order.DoesNotExist: return JsonResponse({'status': 'error'})

@login_required
def generate_invoice(request, order_id, invoice_type='product'):
    base_order = get_object_or_404(Order, id=order_id)
    is_customer = request.user == base_order.user
    is_vendor = getattr(request.user, 'is_vendor', False) and hasattr(request.user, 'vendor_profile')
    is_admin = request.user.is_superuser
    admin_vendor_filter = request.GET.get('vendor_id')
    
    if not (is_customer or is_vendor or is_admin): return HttpResponseForbidden("Unauthorized to view this bill.")

    display_items = []
    context = {'order': base_order, 'invoice_type': invoice_type, 'company_branding': "Powered by OXRO Private Ltd", 'fastx_gst': "OXRO_GSTIN_PLACEHOLDER"}

    if (is_vendor and not is_admin) or (is_admin and admin_vendor_filter) or invoice_type == 'commission':
        target_vendor = get_object_or_404(Vendor, id=admin_vendor_filter) if is_admin and admin_vendor_filter else base_order.vendor
        target_user = target_vendor.user if target_vendor else None
            
        for item in base_order.items.all():
            if item.product and item.product.vendor == target_user:
                item.line_total = item.price * item.quantity
                display_items.append(item)
        
        context.update({'display_items': display_items, 'subtotal': base_order.subtotal, 'tax': base_order.tax_amount, 'discount': base_order.discount_amount if hasattr(base_order, 'discount_amount') else Decimal('0.00'), 'coupon_code': base_order.coupon_code if hasattr(base_order, 'coupon_code') else None, 'seller_gst': target_vendor.gst_number if target_vendor else "Unregistered", 'billed_to': target_vendor.business_name if target_vendor else "Seller"})
        
        if invoice_type == 'product':
            context['bill_title'] = "TAX INVOICE - GOODS"
            context['billed_by'] = target_vendor.business_name if target_vendor else "FASTX Seller"
            context['grand_total'] = (base_order.subtotal + base_order.tax_amount) - context['discount']
        elif invoice_type == 'delivery':
            context['bill_title'] = "TAX INVOICE - DELIVERY SERVICES"
            context['sac_code'] = "996813"
            context['billed_by'] = "FASTX (OXRO Private Ltd)"
            context['delivery_fee'] = base_order.delivery_fee
            context['grand_total'] = base_order.delivery_fee
        elif invoice_type == 'commission':
            context['bill_title'] = "TAX INVOICE - MARKETPLACE SERVICES"
            context['sac_code'] = "998399"
            context['billed_by'] = "FASTX (OXRO Private Ltd)"
            # Set to 30% dynamically
            context['platform_fee'] = base_order.platform_fee if hasattr(base_order, 'platform_fee') else (base_order.subtotal * Decimal('0.30'))
            context['grand_total'] = context['platform_fee']
    else:
        base_id_prefix = base_order.order_id.rsplit('-', 1)[0] if '-' in base_order.order_id else base_order.order_id
        related_orders = Order.objects.filter(order_id__startswith=base_id_prefix, user=base_order.user)
        
        global_subtotal, global_tax, global_delivery, global_wallet, global_discount = Decimal('0.00'), Decimal('0.00'), Decimal('0.00'), Decimal('0.00'), Decimal('0.00')
        for ro in related_orders:
            global_subtotal += ro.subtotal
            global_tax += ro.tax_amount
            global_delivery += ro.delivery_fee
            global_wallet += ro.wallet_amount_used
            if hasattr(ro, 'discount_amount'): global_discount += ro.discount_amount
            for item in ro.items.all():
                item.line_total = item.price * item.quantity
                display_items.append(item)
        
        context.update({'display_items': display_items, 'subtotal': global_subtotal, 'tax': global_tax, 'delivery_fee': global_delivery, 'wallet_applied': global_wallet, 'discount': global_discount, 'coupon_code': base_order.coupon_code if hasattr(base_order, 'coupon_code') else None, 'seller_gst': base_order.vendor.gst_number if base_order.vendor else "Unregistered"})
        
        if invoice_type == 'product':
            context['bill_title'] = "TAX INVOICE - GOODS"
            context['billed_by'] = base_order.vendor.business_name if base_order.vendor else "FASTX Sellers"
            context['grand_total'] = (global_subtotal + global_tax) - global_discount
        elif invoice_type == 'delivery':
            context['bill_title'] = "TAX INVOICE - DELIVERY SERVICES"
            context['sac_code'] = "996813"
            context['billed_by'] = "FASTX (OXRO Private Ltd)"
            context['grand_total'] = global_delivery

    return render(request, 'customer/invoice.html', context)

# ==========================================
# 5. FASTX WALLET & WISHLIST
# ==========================================
@login_required
def wallet_dashboard(request):
    wallet, created = Wallet.objects.get_or_create(user=request.user)
    transactions = wallet.transactions.all().order_by('-timestamp')[:15]
    return render(request, 'customer/wallet.html', {'wallet': wallet, 'transactions': transactions})

@login_required
def add_funds_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            amount = float(data.get('amount', 0))
            if amount <= 0: return JsonResponse({'status': 'error', 'message': 'Invalid top-up amount.'})

            wallet = request.user.wallet
            wallet.balance += Decimal(str(amount))
            wallet.save()
            WalletTransaction.objects.create(wallet=wallet, amount=amount, transaction_type='CREDIT', description='Funds added via Unified Payments (UPI)', reference_id=f"TXN-{int(time.time())}")
            return JsonResponse({'status': 'success', 'new_balance': round(wallet.balance, 2), 'message': f'₹{amount} added securely to your FASTX Wallet.'})
        except Exception as e: return JsonResponse({'status': 'error', 'message': str(e)})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'})

@login_required
@csrf_exempt
def admin_update_wallet(request, user_id):
    """Allows admin to manually Add or Remove funds from a customer's wallet."""
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': 'Unauthorized. Admin access required.'})

    if request.method == 'POST':
        try:
            raw_body = request.body.decode('utf-8')
            data = {}
            if raw_body:
                data = json.loads(raw_body)
            else:
                data = request.POST
                
            raw_amount = data.get('amount')
            action = data.get('action')
            
            if raw_amount is None:
                return JsonResponse({'success': False, 'message': f'CRITICAL: No amount received. Payload was: {raw_body} | POST data: {request.POST}'})

            try:
                amount = Decimal(str(raw_amount))
            except Exception as parse_err:
                return JsonResponse({'success': False, 'message': f'Failed to read number. You sent: {raw_amount}. Error: {str(parse_err)}'})
                
            if amount <= Decimal('0.00'):
                return JsonResponse({'success': False, 'message': f'Amount must be greater than zero. Read: {amount}'})
                
            wallet, created = Wallet.objects.get_or_create(user__id=user_id)
            
            if action == 'add':
                wallet.balance += amount
                WalletTransaction.objects.create(wallet=wallet, amount=amount, transaction_type='CREDIT', description=f"Admin Adjustment: Added by {request.user.username}")
            elif action == 'remove':
                if wallet.balance < amount:
                    return JsonResponse({'success': False, 'message': f'Insufficient funds. Current balance is ₹{wallet.balance}.'})
                wallet.balance -= amount
                WalletTransaction.objects.create(wallet=wallet, amount=amount, transaction_type='DEBIT', description=f"Admin Adjustment: Removed by {request.user.username}")
            else:
                return JsonResponse({'success': False, 'message': f'Invalid action type: {action}'})
                
            wallet.save()
            return JsonResponse({'success': True, 'new_balance': str(wallet.balance), 'message': f'Transaction successful! New balance is ₹{wallet.balance}'})
            
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': f'JSON parsing failed. Raw data: {raw_body}'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Server Crash: {repr(e)}'})
            
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


@login_required
def admin_search_wallet(request):
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': 'Unauthorized. Admin access required.'})

    q = request.GET.get('q', '').strip()
    user = None
    
    if q.isdigit():
        user = User.objects.filter(id=q).first()
        if not user:
            clean_phone = q[-10:]
            customer = CustomerProfile.objects.filter(phone_number__endswith=clean_phone).first()
            if customer: 
                user = customer.user
                
    if not user:
        return JsonResponse({'success': False, 'message': 'No customer found with that ID or Phone Number.'})
        
    wallet, _ = Wallet.objects.get_or_create(user=user)
    
    return JsonResponse({
        'success': True,
        'user_id': user.id,
        'name': user.get_full_name() or user.username,
        'phone': user.customer_profile.phone_number if hasattr(user, 'customer_profile') else 'No Phone Listed',
        'balance': str(wallet.balance)
    })

@login_required
def view_wishlist(request):
    return render(request, 'customer/wishlist.html', {'wishlist_items': Wishlist.objects.filter(user=request.user).select_related('product')})

@login_required
def toggle_wishlist(request):
    if request.method == 'POST':
        try:
            product = Product.objects.get(id=json.loads(request.body).get('product_id'))
            wishlist_entry = Wishlist.objects.filter(user=request.user, product=product)
            if wishlist_entry.exists():
                wishlist_entry.delete(); action = 'removed'
            else:
                Wishlist.objects.create(user=request.user, product=product); action = 'added'
            return JsonResponse({'status': 'success', 'action': action})
        except Product.DoesNotExist: return JsonResponse({'status': 'error', 'message': 'Product not found.'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})

# ==========================================
# 6. USER SETTINGS
# ==========================================
@login_required
def saved_addresses(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        flat_no = request.POST.get('flat_no')
        street = request.POST.get('street')
        city = request.POST.get('city')
        pincode = request.POST.get('pincode')
        make_default = request.POST.get('is_default') == 'on'

        if make_default: Address.objects.filter(user=request.user).update(is_default=False)
        Address.objects.create(user=request.user, title=title, flat_no=flat_no, street=street, city=city, pincode=pincode, is_default=make_default)
        messages.success(request, f"{title} address saved successfully!")
        return redirect('saved_addresses')

    return render(request, 'customer/settings_addresses.html', {'addresses': Address.objects.filter(user=request.user)})

@login_required
def settings_overview(request): return render(request, 'customer/settings.html')

@login_required
def update_profile(request):
    if request.method == 'POST':
        user = request.user
        
        # 1. Identify the user's current profile securely
        profile = None
        if getattr(user, 'is_vendor', False) and hasattr(user, 'vendor_profile'):
            profile = user.vendor_profile
        elif hasattr(user, 'rider_profile'):
            profile = user.rider_profile
        elif hasattr(user, 'customer_profile'):
            profile = user.customer_profile

        # 2. Update Personal Info (ONLY if they exist in the submitted form)
        if 'first_name' in request.POST:
            user.first_name = request.POST.get('first_name')
        if 'last_name' in request.POST:
            user.last_name = request.POST.get('last_name')
        if 'email' in request.POST:
            user.email = request.POST.get('email')

        # 3. Secure Phone Number Update Logic
        if 'phone' in request.POST:
            new_phone = request.POST.get('phone')
            current_phone = profile.phone_number if profile else None
            
            if new_phone and new_phone != current_phone:
                if request.session.get('otp_verified') and request.session.get('verified_phone') == new_phone:
                    # Ensure the new number isn't already used
                    if CustomerProfile.objects.filter(phone_number=new_phone).exists() or \
                       Vendor.objects.filter(phone_number=new_phone).exists() or \
                       RiderProfile.objects.filter(phone_number=new_phone).exists():
                        messages.error(request, "This phone number is already registered to another account.")
                        return redirect('settings_overview')
                    
                    profile.phone_number = new_phone
                    request.session.pop('otp_verified', None)
                    request.session.pop('verified_phone', None)
                    request.session.modified = True
                else:
                    messages.error(request, "Security Alert: You must verify your new phone number with an OTP before saving.")
                    return redirect('settings_overview')

        # 4. Update Profile Photo
        if 'profile_photo' in request.FILES and profile:
            if hasattr(profile, 'profile_photo'):
                profile.profile_photo = request.FILES.get('profile_photo')
            elif hasattr(profile, 'profile_picture'):
                profile.profile_picture = request.FILES.get('profile_photo')

        # 5. BANKING UPDATES (ONLY processes if the Banking Form was submitted)
        if profile:
            if 'bank_account_name' in request.POST: 
                profile.bank_account_name = request.POST.get('bank_account_name')
            if 'bank_name' in request.POST: 
                profile.bank_name = request.POST.get('bank_name')
            if 'ifsc_code' in request.POST: 
                profile.ifsc_code = request.POST.get('ifsc_code')
            
            # Map account number dynamically based on Seller vs Rider
            if 'account_number' in request.POST:
                if hasattr(profile, 'account_number'):
                    profile.account_number = request.POST.get('account_number')
                elif hasattr(profile, 'bank_account_number'):
                    profile.bank_account_number = request.POST.get('account_number')

            if 'passbook_photo' in request.FILES and hasattr(profile, 'passbook_photo'):
                profile.passbook_photo = request.FILES.get('passbook_photo')

        # Save all changes to database
        user.save()
        if profile: 
            profile.save()

        messages.success(request, "Account settings updated successfully.")
    return redirect('settings_overview')

@login_required
def change_password(request):
    if request.method == 'POST':
        if not request.session.get('otp_verified', False):
            messages.error(request, "Security Error: Please verify your phone number with an OTP to change your password.")
            return redirect('settings_overview')

        verified_phone = request.session.get('verified_phone')
        user_phone = None
        if getattr(request.user, 'is_vendor', False) and hasattr(request.user, 'vendor_profile'): 
            user_phone = request.user.vendor_profile.phone_number
        elif hasattr(request.user, 'rider_profile'): 
            user_phone = request.user.rider_profile.phone_number
        elif hasattr(request.user, 'customer_profile'): 
            user_phone = request.user.customer_profile.phone_number
        
        if verified_phone != user_phone:
            messages.error(request, "OTP verification failed. Phone number mismatch.")
            return redirect('settings_overview')

        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            
            request.session.pop('otp_verified', None)
            request.session.pop('verified_phone', None)
            request.session.modified = True
            
            messages.success(request, "Password updated securely!")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{error}")
                    
    return redirect('settings_overview')

@login_required
def update_preferences(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        settings_prof = request.user.settings_profile
        key = data.get('key')
        if hasattr(settings_prof, key):
            setattr(settings_prof, key, not getattr(settings_prof, key))
            settings_prof.save()
            return JsonResponse({'status': 'success', 'value': getattr(settings_prof, key)})
    return JsonResponse({'status': 'error'})


# ==========================================
# 7. ADMIN DASHBOARD & MANAGEMENT 
# ==========================================
@staff_member_required
def admin_dashboard(request):
    all_orders = Order.objects.all()
    context = {
        'total_orders': all_orders.count(),
        'completed_orders': all_orders.filter(status='Delivered').count(),
        'cancelled_orders': all_orders.filter(status='Cancelled').count(),
        'active_riders': RiderProfile.objects.filter(is_approved=True, user__is_active=True).count(),
        'active_sellers': User.objects.filter(is_vendor=True, is_approved_vendor=True, is_active=True).count(),
        'total_revenue': all_orders.filter(status='Delivered').aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00'),
    }
    return render(request, 'admin/admin_dashboard.html', context)
    
@staff_member_required
def admin_financials(request):
    financial_data, rider_data = [], []
    for seller in User.objects.filter(is_vendor=True):
        pending_orders = Order.objects.filter(vendor__user=seller, status='Delivered', is_payout_cleared=False)
        if pending_orders.count() > 0:
            cod_total = pending_orders.filter(payment_method__icontains='COD').aggregate(Sum('subtotal'))['subtotal__sum'] or Decimal('0.00')
            online_total = pending_orders.filter(payment_method__icontains='Online').aggregate(Sum('subtotal'))['subtotal__sum'] or Decimal('0.00')
            revenue = cod_total + online_total
            
            total_commission = pending_orders.aggregate(Sum('platform_fee'))['platform_fee__sum'] or (revenue * Decimal('0.30'))
            payout_due = sum((o.subtotal + o.tax_amount) - (o.discount_amount + o.platform_fee) for o in pending_orders)

            financial_data.append({
                'seller_name': seller.vendor_profile.business_name if hasattr(seller, 'vendor_profile') else seller.get_full_name(),
                'seller_id': seller.id, 'total_orders': pending_orders.count(), 'cod_count': pending_orders.filter(payment_method__icontains='COD').count(),
                'cod_total': cod_total, 'online_total': online_total, 'commission': total_commission, 'payout_due': payout_due, 'orders': pending_orders
            })

    for rider in User.objects.filter(rider_profile__isnull=False):
        pending_trips = Order.objects.filter(rider=rider, status='Delivered', is_rider_cleared=False)
        if pending_trips.count() > 0:
            rider_data.append({
                'rider_name': rider.get_full_name(), 'rider_id': rider.id, 'total_trips': pending_trips.count(),
                'payout_due': pending_trips.aggregate(Sum('delivery_fee'))['delivery_fee__sum'] or Decimal('0.00'), 'orders': pending_trips
            })

    return render(request, 'admin/manage_financials.html', {'financial_data': financial_data, 'rider_data': rider_data})

@staff_member_required
@require_POST
def clear_payouts(request):
    target_type, target_id = request.POST.get('target_type'), request.POST.get('target_id')
    if target_type == 'vendor':
        Order.objects.filter(vendor__user__id=target_id, status='Delivered', is_payout_cleared=False).update(is_payout_cleared=True)
        messages.success(request, "Seller payout cleared successfully. Ledger reset to zero.")
    elif target_type == 'rider':
        Order.objects.filter(rider__id=target_id, status='Delivered', is_rider_cleared=False).update(is_rider_cleared=True)
        messages.success(request, "Rider payout cleared successfully. Ledger reset to zero.")
    return redirect('admin_financials')

@staff_member_required
@require_POST
def clear_vendor_payout(request, vendor_id):
    try:
        cleared_count = Order.objects.filter(vendor__user__id=vendor_id, status='Delivered', is_payout_cleared=False).update(is_payout_cleared=True)
        return JsonResponse({'success': True, 'message': f'Successfully cleared {cleared_count} orders.'})
    except Exception as e: return JsonResponse({'success': False, 'error': str(e)})

@staff_member_required
def manage_customers(request): 
    users_list = User.objects.filter(
        is_superuser=False, 
        is_vendor=False
    ).select_related(
        'customer_profile'
    ).annotate(
        total_orders=Count('order'), 
        total_spent=Sum('order__total_amount')
    ).order_by('-date_joined')
    
    context = {
        'users_list': users_list, 
        'user_type': 'Customer'
    }
    
    return render(request, 'admin/manage_customers.html', context)

@staff_member_required
def manage_sellers(request): 
    # Use select_related to securely fetch all the extended banking and document fields 
    # from the Vendor profile, preventing the template from defaulting to "Not Provided".
    users_list = User.objects.filter(
        is_vendor=True
    ).select_related(
        'vendor_profile'
    ).annotate(
        total_sales=Count('products')
    ).order_by('-date_joined')
    
    context = {
        'users_list': users_list, 
        'user_type': 'Seller'
    }
    
    return render(request, 'admin/manage_sellers.html', context)

@staff_member_required
def toggle_user_status(request, user_id):
    if request.method == 'POST':
        try:
            target_user = User.objects.get(id=user_id)
            if target_user == request.user: return JsonResponse({'success': False, 'error': 'You cannot block your own account.'})
            target_user.is_active = json.loads(request.body).get('is_active', True)
            target_user.save()
            return JsonResponse({'success': True, 'status': "Active" if target_user.is_active else "Blocked"})
        except Exception as e: return JsonResponse({'success': False, 'error': str(e)})

@staff_member_required
@csrf_exempt 
def update_order_status(request, order_id):
    if request.method == 'POST':
        try:
            new_status = json.loads(request.body).get('status')
            order = Order.objects.get(id=order_id)
            if new_status == 'Delivered': order.delivered_at = timezone.now()
            elif new_status == 'Out_for_Delivery': order.delivery_started_at = timezone.now()
            order.status = new_status
            order.save()
            return JsonResponse({'success': True, 'message': 'Status updated successfully'})
        except Order.DoesNotExist: return JsonResponse({'success': False, 'error': 'Order not found'}, status=404)
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)

@staff_member_required
def manage_categories(request):
    if request.method == 'POST':
        action = request.POST.get('action', 'add')
        cat_id = request.POST.get('category_id')
        name = request.POST.get('name')
        icon = request.POST.get('icon', 'bi bi-basket')
        image_url = request.POST.get('image_url', '')
        image = request.FILES.get('image')

        if action == 'edit' and cat_id:
            category = get_object_or_404(Category, id=cat_id)
            if name:
                category.name = name
                category.icon = icon
                category.image_url = image_url
                if image:
                    category.image = image
                category.save()
                messages.success(request, f"Category '{name}' updated successfully!")
            else:
                messages.error(request, "Category name cannot be empty.")
                
        elif action == 'add':
            if name:
                Category.objects.create(
                    name=name, 
                    icon=icon,
                    image_url=image_url,
                    image=image
                )
                messages.success(request, f"Category '{name}' added successfully!")
            else: 
                messages.error(request, "Category name cannot be empty.")
                
        return redirect('manage_categories')
        
    return render(request, 'admin/manage_categories.html', {
        'categories': Category.objects.all().order_by('name')
    })

@staff_member_required
def delete_category(request, category_id):
    if request.method == 'POST':
        category = get_object_or_404(Category, id=category_id)
        category_name, _ = category.name, category.delete()
        messages.success(request, f"Category '{category_name}' has been deleted.")
    return redirect('manage_categories')

# ==========================================
# 8. VENDOR PORTAL LOGIC
# ==========================================
@staff_member_required
@require_POST
def approve_seller(request, user_id):
    user = get_object_or_404(User, id=user_id, is_vendor=True)
    user.is_approved_vendor = True; user.save()
    return JsonResponse({'success': True, 'message': f"Seller {user.get_full_name()} has been approved!"})

@login_required
def vendor_dashboard(request):
    if not getattr(request.user, 'is_vendor', False) and not request.user.is_superuser: 
        return redirect('customer_home')
        
    Order.objects.filter(vendor__user=request.user, status='Packed', rider__isnull=False, assignment_status='Pending', assignment_start_time__lt=timezone.now() - timedelta(seconds=60)).update(rider=None, assignment_status='Rejected', assignment_start_time=None)

    my_products = Product.objects.filter(vendor=request.user)
    recent_orders = Order.objects.filter(vendor__user=request.user).exclude(status__in=['Delivered', 'Cancelled', 'Pending_Payment']).distinct().order_by('-created_at')
    today_sales = Order.objects.filter(vendor__user=request.user, status='Delivered', created_at__date=timezone.now().date()).aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')

    # UPDATED: Fetch only delivery partners who are currently ONLINE
    active_partners = User.objects.filter(is_superuser=False, is_vendor=False, rider_profile__is_online=True)[:10]

    stats = {
        "today_sales": today_sales, 
        "active_orders": recent_orders.count(), 
        "pending_shipments": recent_orders.filter(status__in=['Confirmed', 'Packed']).count(), 
        "top_product": my_products.first().name if my_products.exists() else "No products yet"
    }
    
    return render(request, 'vendor/dashboard.html', {
        'stats': stats, 
        'recent_orders': recent_orders, 
        'my_products': my_products, 
        'categories': Category.objects.all(), 
        'delivery_partners': active_partners
    })

@login_required
@require_POST
def vendor_add_product(request):
    if getattr(request.user, 'is_vendor', False):
        try:
            Product.objects.create(vendor=request.user, category=Category.objects.get(id=request.POST.get('category_id')), name=request.POST.get('name'), price=request.POST.get('price'), stock=request.POST.get('stock'), image_url=request.POST.get('image_url') or "", image=request.FILES.get('image_file'), is_available=request.POST.get('is_available') == 'on')
            messages.success(request, f"'{request.POST.get('name')}' added successfully!")
        except Exception as e: messages.error(request, f"Failed to add product: {str(e)}")
    return redirect('vendor_products')

@login_required
def vendor_order_history(request):
    if not getattr(request.user, 'is_vendor', False): return redirect('customer_home')
    orders = Order.objects.filter(vendor__user=request.user).order_by('-created_at')
    
    date_from, date_to, status_filter = request.GET.get('date_from'), request.GET.get('date_to'), request.GET.get('status')
    if date_from: orders = orders.filter(created_at__date__gte=date_from)
    if date_to: orders = orders.filter(created_at__date__lte=date_to)
    if status_filter: orders = orders.filter(status=status_filter)
    else: orders = orders.filter(status__in=['Delivered', 'Cancelled'])
    
    for order in orders: order.gross = order.subtotal; order.tax = order.tax_amount; order.net = order.subtotal
    return render(request, 'vendor/order_history.html', {'orders': orders})

@login_required
@require_POST
def vendor_delete_order(request, order_id):
    if not getattr(request.user, 'is_vendor', False): return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)
    get_object_or_404(Order, id=order_id, vendor__user=request.user).delete() 
    return JsonResponse({'success': True})

@login_required
@require_POST
def vendor_clear_all_history(request):
    if not getattr(request.user, 'is_vendor', False): return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)
    Order.objects.filter(vendor__user=request.user).delete()
    return JsonResponse({'success': True})

@login_required
def vendor_products(request):
    if not getattr(request.user, 'is_vendor', False): return redirect('customer_home')
    return render(request, 'vendor/products.html', {'my_products': Product.objects.filter(vendor=request.user).order_by('-id'), 'categories': Category.objects.all()})

@login_required
@require_POST
def vendor_delete_product(request, product_id):
    if getattr(request.user, 'is_vendor', False):
        product = get_object_or_404(Product, id=product_id, vendor=request.user)
        product.delete()
        messages.success(request, f"{product.name} deleted successfully.")
    return redirect('vendor_products')

@login_required
@require_POST
def vendor_edit_product(request, product_id):
    if getattr(request.user, 'is_vendor', False):
        try:
            product = get_object_or_404(Product, id=product_id, vendor=request.user)
            product.name, product.category, product.price, product.stock, product.is_available = request.POST.get('name'), Category.objects.get(id=request.POST.get('category_id')), request.POST.get('price'), request.POST.get('stock'), request.POST.get('is_available') == 'on'
            if request.POST.get('image_url'): product.image_url = request.POST.get('image_url')
            if request.FILES.get('image_file'): product.image = request.FILES.get('image_file') 
            product.save()
            messages.success(request, f"'{product.name}' was successfully updated!")
        except Exception as e: messages.error(request, f"Failed to update product: {str(e)}")
    return redirect('vendor_products')
    
@login_required
def vendor_earnings(request):
    if not getattr(request.user, 'is_vendor', False): return redirect('customer_home')
    delivered_orders = Order.objects.filter(vendor__user=request.user, status='Delivered')
    gross_income = delivered_orders.aggregate(Sum('subtotal'))['subtotal__sum'] or Decimal('0.00')
    tax_deduction = delivered_orders.aggregate(Sum('tax_amount'))['tax_amount__sum'] or Decimal('0.00')
    
    payout_verified = request.session.get('payout_verified_status', False)
    payout_pending = Decimal('0.00') if payout_verified else (gross_income - tax_deduction)

    top_products = Product.objects.filter(vendor=request.user).annotate(total_sold=Count('orderitem')).order_by('-total_sold')[:5]
    associated_riders = User.objects.filter(assigned_deliveries__vendor__user=request.user, assigned_deliveries__status='Delivered').annotate(vendor_delivery_count=Count('assigned_deliveries', filter=Q(assigned_deliveries__vendor__user=request.user))).distinct()[:5]

    today = timezone.now()
    chart_labels, chart_data = [], []
    for i in range(6, -1, -1):
        target_day = today - timedelta(days=i)
        daily_sales = delivered_orders.filter(created_at__date=target_day.date()).aggregate(Sum('subtotal'))['subtotal__sum'] or 0
        chart_labels.append(target_day.strftime('%b %d'))
        chart_data.append(float(daily_sales))

    context = {'gross_income': round(gross_income, 2), 'tax_deduction': round(tax_deduction, 2), 'delivery_partner_fees': 0.00, 'payout_pending': round(payout_pending, 2), 'total_orders': delivered_orders.count(), 'chart_labels': chart_labels, 'chart_data': chart_data, 'top_products': top_products, 'associated_riders': associated_riders, 'payout_verified': payout_verified}
    return render(request, 'vendor/earnings.html', context)

@login_required
@require_POST
def verify_payout_api(request):
    request.session['payout_verified_status'] = True
    return JsonResponse({'success': True})

@login_required
@require_POST
def vendor_assign_rider(request, order_id):
    try:
        data = json.loads(request.body)
        order = Order.objects.get(id=order_id, vendor__user=request.user)
        rider_user = User.objects.get(id=data.get('rider_id'))
        order.rider = rider_user
        if hasattr(order, 'assigned_rider'): order.assigned_rider = rider_user
        order.rejected_by.clear() 
        order.assignment_start_time = timezone.now() 
        order.assignment_status = 'Pending' 
        order.save()
        return JsonResponse({'success': True})
    except Exception as e: return JsonResponse({'success': False, 'error': str(e)})

@login_required
@require_POST
def vendor_update_order_status(request, order_id):
    try:
        order = Order.objects.get(id=order_id, vendor__user=request.user)
        order.status = json.loads(request.body).get('status')
        order.save()
        return JsonResponse({'success': True})
    except Exception as e: return JsonResponse({'success': False, 'error': str(e)})

@login_required
def vendor_dispatcher(request, action, order_id):
    actions = {'delete': vendor_delete_order, 'update-status': vendor_update_order_status, 'assign-rider': vendor_assign_rider, 'clear-history': vendor_clear_all_history}
    return actions.get(action)(request, order_id) if action in actions else JsonResponse({'success': False, 'error': 'Action not found'}, status=404)

# ==========================================
# 9. RIDER PORTAL
# ==========================================
@login_required
def rider_dashboard(request):
    if not hasattr(request.user, 'rider_profile'): 
        return redirect('customer_home')
    
    # Always show active deliveries they've already accepted/been assigned
    active_deliveries = Order.objects.filter(
        rider=request.user, 
        status__in=['Packed', 'Dispatched', 'Out_for_Delivery']
    ).select_related('vendor').order_by('created_at')
    
    # NEW LOGIC: Only fetch the Open Pool if the rider is online!
    available_orders = []
    if request.user.rider_profile.is_online:
        available_orders = Order.objects.filter(rider=None, status='Confirmed').exclude(rejected_by=request.user)
        
    completed_count = Order.objects.filter(rider=request.user, status='Delivered').count()
    
    return render(request, 'rider/dashboard.html', {
        'active_deliveries': active_deliveries, 
        'available_orders': available_orders, 
        'completed_count': completed_count, 
        'rider': request.user.rider_profile
    })

@login_required
@require_POST
def rider_toggle_online(request):
    """API endpoint for Rider to toggle their Active/Offline status."""
    if not hasattr(request.user, 'rider_profile'): 
        return JsonResponse({'success': False, 'error': 'Unauthorized access.'})
    
    try:
        data = json.loads(request.body)
        is_online = data.get('is_online', False)
        
        profile = request.user.rider_profile
        profile.is_online = is_online
        profile.save()
        
        return JsonResponse({'success': True, 'is_online': profile.is_online})
    except Exception as e: 
        return JsonResponse({'success': False, 'error': str(e)})

@staff_member_required
def manage_delivery_partners(request):
    # Fetch all riders, join their User data, and dynamically count their successful deliveries
    all_riders = RiderProfile.objects.select_related('user').annotate(
        total_deliveries=Count('user__assigned_deliveries', filter=Q(user__assigned_deliveries__status='Delivered'))
    ).order_by('-id')
    
    context = {
        'delivery_partners': all_riders, 
        'total_partners_count': all_riders.count(), 
        'pending_approval_count': all_riders.filter(is_approved=False).count(), 
        'active_partners_count': all_riders.filter(is_approved=True, user__is_active=True).count(), 
        'blocked_partners_count': all_riders.filter(user__is_active=False).count()
    }
    return render(request, 'admin/manage_delivery_partners.html', context)

@staff_member_required
@require_POST
def update_partner_status(request, partner_id):
    try:
        data, partner = json.loads(request.body), get_object_or_404(RiderProfile, id=partner_id)
        if data.get('action') == 'Approve': partner.is_approved, partner.user.is_active, status = True, True, "Active"
        elif data.get('action') == 'Block': partner.user.is_active, status = False, "Blocked"
        elif data.get('action') == 'Unblock': partner.user.is_active, status = True, "Active"
        elif data.get('action') == 'Reject': partner.delete(); return JsonResponse({'success': True, 'new_status': 'Rejected'})
        partner.save(); partner.user.save()
        return JsonResponse({'success': True, 'new_status': status})
    except Exception as e: return JsonResponse({'success': False, 'error': str(e)}, status=400)

@staff_member_required
def delivery_partner_details(request, partner_id):
    partner = get_object_or_404(RiderProfile, id=partner_id)
    return render(request, 'admin/partner_details.html', {'partner': partner, 'deliveries': Order.objects.filter(rider=partner.user).order_by('-created_at')})

@login_required
@require_POST
def accept_order(request, order_id):
    if not hasattr(request.user, 'rider_profile'): return redirect('customer_home')
    order = get_object_or_404(Order, id=order_id)
    if order.rider is not None: return redirect('rider_dashboard')
    order.rider, order.status = request.user, 'Packed'
    order.save()
    return redirect('rider_dashboard')

@login_required
@require_POST
def reject_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    order.rejected_by.add(request.user)
    order.rider = None 
    order.save()
    return redirect('rider_dashboard')

@login_required
def rider_history(request):
    if not hasattr(request.user, 'rider_profile'): return redirect('customer_home')
    successful_orders = Order.objects.filter(rider=request.user, status='Delivered').order_by('-created_at')
    total_earnings = successful_orders.aggregate(Sum('delivery_fee'))['delivery_fee__sum'] or Decimal('0.00')
    payout_pending = Decimal('0.00') if request.session.get('rider_payout_verified', False) else total_earnings
    return render(request, 'rider/history.html', {'history': Order.objects.filter(rider=request.user, status__in=['Delivered', 'Not_Reachable']).order_by('-created_at'), 'total_orders': successful_orders.count(), 'total_earnings': round(total_earnings, 2), 'payout_pending': round(payout_pending, 2)})

@login_required
@require_POST
def rider_verify_payout_api(request):
    if hasattr(request.user, 'rider_profile'):
        request.session['rider_payout_verified'] = True
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Unauthorized'})
    
@login_required
@require_POST
def rider_update_status(request, order_id):
    if not hasattr(request.user, 'rider_profile'): return JsonResponse({'success': False, 'error': 'Unauthorized access.'})
    try:
        data = json.loads(request.body)
        order = Order.objects.get(id=order_id, rider=request.user)
        if data.get('status') == 'Delivered': order.delivered_at = timezone.now()
        elif data.get('status') == 'Out_for_Delivery': order.delivery_started_at = timezone.now()
        order.status = data.get('status')
        order.save()
        return JsonResponse({'success': True})
    except Exception as e: return JsonResponse({'success': False, 'error': str(e)})
        
@staff_member_required
def admin_view_document(request, doc_type, user_id):
    if doc_type == 'vendor_aadhar': file_field = get_object_or_404(Vendor, user_id=user_id).aadhaar_card
    elif doc_type == 'vendor_passbook': file_field = get_object_or_404(Vendor, user_id=user_id).passbook_photo
    elif doc_type == 'rider_license': file_field = get_object_or_404(RiderProfile, user_id=user_id).license_photo
    elif doc_type == 'rider_profile': file_field = get_object_or_404(RiderProfile, user_id=user_id).profile_photo
    elif doc_type == 'rider_passbook': file_field = get_object_or_404(RiderProfile, user_id=user_id).passbook_photo
    else: return HttpResponseForbidden("Invalid document type.")
    if not file_field: return HttpResponseForbidden("Document not found.")
    return FileResponse(file_field.open('rb'))
    
def check_assignment_timeout(request, order_id):
    order = Order.objects.get(id=order_id)
    if order.assignment_status == 'Pending' and order.assigned_at and (timezone.now() - order.assigned_at).total_seconds() > 60:
        order.assignment_status, order.rider = 'Rejected', None
        order.save()
        return JsonResponse({'status': 'expired', 'message': 'Assignment timed out.'})
    return JsonResponse({'status': 'active'})
    
@login_required
def check_order_timeout(request, order_id):
    try:
        order = Order.objects.get(id=order_id)
        if order.assignment_status == 'Pending' and order.assignment_start_time and timezone.now() > order.assignment_start_time + timedelta(seconds=60):
            order.assignment_status, order.rider = 'Rejected', None
            order.save()
            return JsonResponse({'status': 'Rejected'})
        return JsonResponse({'status': order.assignment_status})
    except Order.DoesNotExist: return JsonResponse({'error': 'Not found'}, status=404)
    
@login_required
@require_POST
def rider_respond_to_assignment(request, order_id):
    try:
        data, order = json.loads(request.body), Order.objects.get(id=order_id, rider=request.user)
        if data.get('action') == 'Accepted': order.assignment_status = 'Accepted'
        elif data.get('action') == 'Rejected':
            order.assignment_status = 'Rejected'
            order.rejected_by.add(request.user)
            order.rider = None
        else: return JsonResponse({'success': False, 'error': 'Invalid action provided.'})
        order.save()
        return JsonResponse({'success': True})
    except Exception as e: return JsonResponse({'success': False, 'error': str(e)})
        
@login_required
def vendor_dashboard_data(request):
    if not getattr(request.user, 'is_vendor', False): return JsonResponse({'error': 'Unauthorized'}, status=403)
    return render(request, 'vendor/dashboard_table_rows.html', {'recent_orders': Order.objects.filter(vendor__user=request.user).order_by('-created_at')})
    
def is_valid_password(password):
    if len(password) < 8: return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", password): return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password): return False, "Password must contain at least one special character."
    return True, "Valid"

def forgot_password(request):
    if request.method == 'POST':
        if not request.session.get('otp_verified', False):
            messages.error(request, "Security Error: Please verify your phone number with an OTP first.")
            return redirect('forgot_password')

        verified_phone = request.session.get('verified_phone')
        new_password = request.POST.get('new_password')
        
        user = find_user_by_phone(verified_phone)
        
        if user:
            is_valid, msg = is_valid_password(new_password)
            if not is_valid:
                messages.error(request, msg)
                return redirect('forgot_password')
                
            user.set_password(new_password)
            user.save()
            
            request.session.pop('otp_verified', None)
            request.session.pop('verified_phone', None)
            request.session.modified = True
            
            messages.success(request, "Your password has been successfully reset! You can now log in.")
            return redirect('unified_login')
        else:
            messages.error(request, "No account found with this registered mobile number.")
            return redirect('forgot_password')

    return render(request, 'auth/forgot_password.html')
    
@staff_member_required
def manage_promotions(request):
    promotions = Promotion.objects.all().order_by('-created_at')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            if promotions.count() >= 4: messages.error(request, "Maximum of 4 banners allowed.")
            else:
                Promotion.objects.create(title=request.POST.get('title'), description=request.POST.get('description'), media=request.FILES.get('media'))
                messages.success(request, "Banner added successfully!")
        elif action == 'edit':
            promo = get_object_or_404(Promotion, id=request.POST.get('promo_id'))
            promo.title, promo.description = request.POST.get('title'), request.POST.get('description')
            if request.FILES.get('media'): promo.media = request.FILES.get('media')
            promo.save()
            messages.success(request, "Banner updated successfully!")
        return redirect('manage_promotions')
    return render(request, 'admin/manage_promotions.html', {'promotions': promotions})

# ==========================================
# 10. TWILIO SECURE OTP INTEGRATION
# ==========================================
@csrf_exempt
@require_POST
def send_otp_api(request): 
    try:
        data = json.loads(request.body)
        phone = data.get('phone', '').strip()
        action = data.get('action', 'login').replace('_', ' ').title() 
        
        if not phone: 
            return JsonResponse({'success': False, 'error': 'Phone number is required.'})

        otp = str(random.randint(100000, 999999))
        
        request.session['auth_otp'] = otp
        request.session['auth_phone'] = phone
        request.session.modified = True
        
        message_body = f"FASTX {action} Verification: Your OTP is {otp}. Valid for 5 minutes. Do not share this code."
        
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=message_body,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=phone if phone.startswith('+') else f"+91{phone}"
        )
        
        return JsonResponse({'success': True, 'message': f'OTP sent for {action}.'})

    except Exception as e: 
        print(f"Twilio Error: {str(e)}") 
        return JsonResponse({'success': False, 'error': f'Failed to send OTP: {str(e)}'})

@csrf_exempt
@require_POST
def verify_otp_api(request):
    try:
        data = json.loads(request.body)
        user_otp, phone = data.get('otp', '').strip(), data.get('phone', '').strip()
        
        if phone.startswith('+91'): phone = phone.replace('+91', '')
        elif phone.startswith('91') and len(phone) > 10: phone = phone[2:]
        
        session_otp, session_phone = request.session.get('auth_otp'), request.session.get('auth_phone')
        
        if not user_otp or not session_otp: return JsonResponse({'success': False, 'error': 'No active OTP session found.'})
        if phone != session_phone: return JsonResponse({'success': False, 'error': 'Phone number mismatch.'})
            
        if user_otp == session_otp:
            request.session['otp_verified'] = True
            request.session['verified_phone'] = phone
            del request.session['auth_otp'] 
            request.session.modified = True
            return JsonResponse({'success': True, 'message': 'Phone verified successfully.'})
        else: return JsonResponse({'success': False, 'error': 'Invalid OTP code. Try again.'})
    except Exception as e: return JsonResponse({'success': False, 'error': str(e)})
        
@staff_member_required
def delete_promotion(request, promo_id):
    get_object_or_404(Promotion, id=promo_id).delete()
    return redirect('manage_promotions')

@staff_member_required
def admin_feedback(request):
    return render(request, 'admin/admin_feedback.html', {'feedbacks': Feedback.objects.select_related('user', 'rider').all().order_by('-created_at')})

@staff_member_required
def clear_all_feedback(request):
    Feedback.objects.all().delete()
    messages.success(request, "All feedback has been cleared.")
    return redirect('admin_feedback')
    
@login_required
def submit_feedback(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    if Feedback.objects.filter(order_id=order.order_id).exists():
        messages.info(request, "You have already submitted feedback for this order.")
        return redirect('track_orders')

    if request.method == 'POST':
        Feedback.objects.create(user=request.user, order_id=order.order_id, rider=order.rider, rating=int(request.POST.get('rating', 5)), rider_rating=int(request.POST.get('rider_rating', 5)), comment=request.POST.get('comment', ''))
        messages.success(request, "Thank you! Your feedback has been recorded.")
        return redirect('track_orders')
    return render(request, 'customer/submit_feedback.html', {'order': order})
    
@staff_member_required
def admin_manage_orders(request):
    orders = Order.objects.select_related('user', 'vendor').all().order_by('-created_at')
    vendor_id, status_filter = request.GET.get('vendor_id'), request.GET.get('status')
    
    if vendor_id: orders = orders.filter(vendor__id=vendor_id)
    if status_filter:
        if status_filter == 'active': orders = orders.exclude(status__in=['Delivered', 'Cancelled'])
        elif status_filter == 'past': orders = orders.filter(status__in=['Delivered', 'Cancelled'])
        else: orders = orders.filter(status=status_filter)
            
    return render(request, 'admin/manage_orders.html', {'orders': orders, 'vendors': Vendor.objects.all().order_by('business_name'), 'selected_vendor': int(vendor_id) if vendor_id else '', 'selected_status': status_filter or ''})
    
@staff_member_required
def admin_settlements(request):
    vendor_payouts, rider_payouts = [], []
    for vendor in Vendor.objects.all():
        unsettled_orders = Order.objects.filter(vendor=vendor, status='Delivered', vendor_settled=False)
        if unsettled_orders.exists():
            vendor_payouts.append({'vendor': vendor, 'total_due': sum((o.subtotal + o.tax_amount) - (o.discount_amount + o.platform_fee) for o in unsettled_orders), 'order_count': unsettled_orders.count(), 'orders': unsettled_orders})

    for rider in User.objects.filter(is_rider=True): 
        unsettled_trips = Order.objects.filter(rider=rider, status='Delivered', rider_settled=False)
        if unsettled_trips.exists():
            rider_payouts.append({'rider': rider, 'total_due': sum(o.delivery_fee for o in unsettled_trips), 'trip_count': unsettled_trips.count(), 'orders': unsettled_trips})

    return render(request, 'admin/admin_settlements.html', {'vendor_payouts': vendor_payouts, 'rider_payouts': rider_payouts})

@staff_member_required
@require_POST
def clear_settlement(request):
    target_type, target_id = request.POST.get('target_type'), request.POST.get('target_id')
    if target_type == 'vendor':
        Order.objects.filter(vendor__id=target_id, status='Delivered', vendor_settled=False).update(vendor_settled=True)
        messages.success(request, "Seller payout cleared successfully. Counter reset to zero.")
    elif target_type == 'rider':
        Order.objects.filter(rider__id=target_id, status='Delivered', rider_settled=False).update(rider_settled=True)
        messages.success(request, "Rider payout cleared successfully. Counter reset to zero.")
    return redirect('admin_settlements')

def terms_and_conditions(request): return render(request, 'legal/terms.html')
def privacy_policy(request): return render(request, 'legal/privacy.html')
    
@login_required
@require_POST
def initiate_account_deletion(request):
    try:
        otp = str(random.randint(100000, 999999))
        request.session['delete_account_otp'] = otp
        request.session['delete_account_reason'] = json.loads(request.body).get('reason', 'No reason provided')
        
        phone_number = None
        if getattr(request.user, 'is_vendor', False) and hasattr(request.user, 'vendor_profile'): phone_number = request.user.vendor_profile.phone_number
        elif hasattr(request.user, 'rider_profile'): phone_number = request.user.rider_profile.phone_number
        elif hasattr(request.user, 'customer_profile'): phone_number = request.user.customer_profile.phone_number
            
        if not phone_number: return JsonResponse({'status': 'error', 'message': 'No registered phone number found to send OTP.'})

        Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN).messages.create(
            body=f"FASTX Security Alert: Your OTP to permanently delete your account is {otp}. Do NOT share this code with anyone.",
            from_=settings.TWILIO_PHONE_NUMBER,
            to=phone_number if phone_number.startswith('+') else f"+91{phone_number}"
        )
        return JsonResponse({'status': 'success', 'message': 'OTP sent via SMS', 'phone': f"******{phone_number[-4:]}"})
    except Exception as e: return JsonResponse({'status': 'error', 'message': f"SMS Gateway Error: {str(e)}"})

@login_required
@require_POST
def confirm_account_deletion(request):
    try:
        provided_otp = json.loads(request.body).get('otp', '')
        stored_otp = request.session.get('delete_account_otp')
        if not stored_otp or provided_otp != stored_otp: return JsonResponse({'status': 'error', 'message': 'Invalid or expired OTP.'})
            
        user = request.user
        logout(request) 
        user.delete()   
        if 'delete_account_otp' in request.session: del request.session['delete_account_otp']
        return JsonResponse({'status': 'success', 'message': 'Account deleted successfully.'})
    except Exception as e: return JsonResponse({'status': 'error', 'message': str(e)})
    
def product_tag_view(request, tag_name):
    """Catches product tag links and redirects safely."""
    messages.info(request, f"Browsing products tagged with: {tag_name.title()}")
    return redirect('customer_home')
    
@staff_member_required
def admin_payment_history(request):
    """
    Centralized master ledger showing all transactions (Online, Wallet, and COD)
    with real-time financial aggregate summaries.
    """
    # Fetch all orders that have passed the payment initialization phase
    all_transactions = Order.objects.exclude(status='Pending_Payment').select_related('user', 'vendor').order_by('-created_at')
    
    # Filter options if needed
    payment_type = request.GET.get('payment_type')
    if payment_type == 'Online':
        all_transactions = all_transactions.filter(payment_method='Online')
    elif payment_type == 'COD':
        all_transactions = all_transactions.filter(payment_method='COD')

    # Live aggregate math calculations
    online_total = all_transactions.filter(payment_method='Online', is_paid=True).aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')
    
    # FIXED: Changed 'wallet_amount__sum' to 'wallet_amount_used__sum' to match the database field exactly
    wallet_total = all_transactions.aggregate(Sum('wallet_amount_used'))['wallet_amount_used__sum'] or Decimal('0.00')
    
    cod_total = all_transactions.filter(payment_method='COD', status='Delivered').aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')
    
    grand_revenue_total = online_total + wallet_total + cod_total

    context = {
        'transactions': all_transactions,
        'online_total': online_total,
        'wallet_total': wallet_total,
        'cod_total': cod_total,
        'grand_revenue_total': grand_revenue_total,
        'selected_type': payment_type or 'All'
    }
    return render(request, 'admin/payment_history.html', context)

@staff_member_required
@require_POST
def admin_clear_payment_history(request):
    """
    Clears out the payment tracking history by deleting or marking records 
    to reset the ledger metrics back to zero.
    """
    # To preserve user account data but clear the history logs safely:
    Order.objects.exclude(status__in=['Pending', 'Packed', 'Dispatched', 'Out_for_Delivery']).delete()
    messages.success(request, "Master payment history ledger has been cleared and reset to zero successfully.")
    return redirect('admin_payment_history')
    
# Add this import at the top if not already there
from .models import Coupon, CouponUsage

@staff_member_required
def manage_coupons(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add':
            cat_id = request.POST.get('category_id')
            Coupon.objects.create(
                code=request.POST.get('code').upper(),
                description=request.POST.get('description'),
                discount_amount=Decimal(request.POST.get('discount_amount', 0)),
                min_order_value=Decimal(request.POST.get('min_order_value', 0)),
                category_restriction=Category.objects.get(id=cat_id) if cat_id else None,
                is_active=request.POST.get('is_active') == 'on'
            )
            messages.success(request, "Coupon created successfully!")
            
        elif action == 'edit':
            coupon = get_object_or_404(Coupon, id=request.POST.get('coupon_id'))
            cat_id = request.POST.get('category_id')
            coupon.code = request.POST.get('code').upper()
            coupon.description = request.POST.get('description')
            coupon.discount_amount = Decimal(request.POST.get('discount_amount', 0))
            coupon.min_order_value = Decimal(request.POST.get('min_order_value', 0))
            coupon.category_restriction = Category.objects.get(id=cat_id) if cat_id else None
            coupon.is_active = request.POST.get('is_active') == 'on'
            coupon.save()
            messages.success(request, "Coupon updated successfully!")
            
        elif action == 'delete':
            Coupon.objects.filter(id=request.POST.get('coupon_id')).delete()
            messages.success(request, "Coupon deleted.")
            
        return redirect('manage_coupons')

    context = {
        'coupons': Coupon.objects.all().order_by('-created_at'),
        'categories': Category.objects.all()
    }
    return render(request, 'admin/manage_coupons.html', context)


@login_required
@require_POST
def validate_coupon_api(request):
    """Real-time validation for the frontend cart."""
    try:
        data = json.loads(request.body)
        code = data.get('code', '').strip().upper()
        subtotal = Decimal(str(data.get('subtotal', 0)))
        
        coupon = Coupon.objects.filter(code=code, is_active=True).first()
        if not coupon:
            return JsonResponse({'success': False, 'message': 'Invalid or inactive coupon code.'})
            
        # Check One-Time Usage
        if CouponUsage.objects.filter(user=request.user, coupon=coupon).exists():
            return JsonResponse({'success': False, 'message': 'You have already used this coupon.'})
            
        # Check Minimum Order Value
        if subtotal < coupon.min_order_value:
            return JsonResponse({'success': False, 'message': f'Minimum cart subtotal of ₹{coupon.min_order_value} required to use this.'})
            
        # Check Category Restriction
        if coupon.category_restriction:
            cart = request.session.get('cart', {})
            has_valid_item = any(item.get('category') == coupon.category_restriction.name for item in cart.values())
            if not has_valid_item:
                return JsonResponse({'success': False, 'message': f'This coupon is only valid for items in the {coupon.category_restriction.name} category.'})

        return JsonResponse({
            'success': True, 
            'discount_amount': float(coupon.discount_amount), 
            'message': 'Coupon applied successfully!'
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': 'An error occurred validating the coupon.'})
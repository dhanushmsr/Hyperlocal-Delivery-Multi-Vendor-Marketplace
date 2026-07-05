import os
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.dispatch import receiver
from django.db.models.signals import post_save, post_delete
from django.conf import settings

# ==========================================
# 1. CUSTOM USER MODEL
# ==========================================
class User(AbstractUser):
    email = models.EmailField(unique=True) 
    is_vendor = models.BooleanField(default=False)
    is_approved_vendor = models.BooleanField(
        default=False, 
        help_text="Designates whether the admin has approved this seller."
    )
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

# ==========================================
# 2. E-COMMERCE MODELS
# ==========================================
class Category(models.Model):
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=50, default="bi bi-basket", help_text="Bootstrap icon class")
    image = models.ImageField(upload_to='category_images/', blank=True, null=True)
    image_url = models.URLField(max_length=500, blank=True, null=True, help_text="Optional external image URL")

    def __str__(self):
        return self.name

class Product(models.Model):
    category = models.ForeignKey(Category, related_name='items', on_delete=models.CASCADE)
    vendor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='products', null=True) 
    hsn_code = models.CharField(max_length=20, default="0000", help_text="Harmonized System of Nomenclature code for GST")
    
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    
    # DUAL IMAGE SETUP
    image_url = models.URLField(max_length=500, blank=True, null=True, default="https://images.unsplash.com/photo-1542838132-92c53300491e?w=150&q=80")
    image = models.ImageField(upload_to='product_images/', blank=True, null=True)
    
    is_available = models.BooleanField(default=True)
    stock = models.IntegerField(default=50)

    def __str__(self):
        return self.name
    
class Order(models.Model):
    STATUS_CHOICES = (
        ('Confirmed', 'Order Confirmed'),
        ('Packed', 'Packed'),
        ('Dispatched', 'Dispatched'),
        ('Out_for_Delivery', 'Out for Delivery'),
        ('Delivered', 'Delivered'),
        ('Cancelled', 'Cancelled'),
    )
    
    ASSIGNMENT_STATUS_CHOICES = (
        ('Pending', 'Pending'),
        ('Accepted', 'Accepted'),
        ('Rejected', 'Rejected'),
    )
    
    # Core Order Details
    is_payout_cleared = models.BooleanField(default=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    vendor = models.ForeignKey('Vendor', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    order_id = models.CharField(max_length=20, unique=True)
    delivery_address = models.TextField(blank=True, null=True, help_text="The exact address used for this order.")
    
    # --- Bill Breakdown Fields ---
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    wallet_amount_used = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2) # Grand Total
    vendor_settled = models.BooleanField(default=False, help_text="True if the seller has been paid for this order")
    rider_settled = models.BooleanField(default=False, help_text="True if the rider has been paid for this delivery")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Confirmed')
    payment_method = models.CharField(max_length=50, default='Online')
    created_at = models.DateTimeField(auto_now_add=True)
    coupon_code = models.CharField(max_length=50, blank=True, null=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Track the platform commission explicitly for the Seller Settlement Invoice
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # --- Rider Dashboard Tracking Relations ---
    rider = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='assigned_deliveries'
    )
    assignment_start_time = models.DateTimeField(
        null=True, 
        blank=True, 
        help_text="Timestamp of when the assignment request was made to the rider."
    )
    assignment_status = models.CharField(
        max_length=20,
        choices=ASSIGNMENT_STATUS_CHOICES,
        default='Pending',
        help_text="Current status of the assignment request to the rider."
    )

    # Delivery History & Analytics
    distance_km = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    delivery_started_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    
    # Metrics and Ratings
    rider_rating = models.IntegerField(default=0, help_text="Rating from 1-5") 
    rejected_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='rejected_orders', blank=True)
    is_rider_cleared = models.BooleanField(default=False)  # NEW field for riders
    
    razorpay_order_id = models.CharField(max_length=100, null=True, blank=True)
    razorpay_payment_id = models.CharField(max_length=100, null=True, blank=True)
    razorpay_signature = models.CharField(max_length=255, null=True, blank=True)
    is_paid = models.BooleanField(default=False)

    def __str__(self):
        return self.order_id

    @property
    def duration_minutes(self):
        """Returns the total delivery duration rounded to 1 decimal place."""
        if self.delivery_started_at and self.delivered_at:
            delta = self.delivered_at - self.delivery_started_at
            return round(delta.total_seconds() / 60, 1)
        return 0
    
class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True) 
    product_name = models.CharField(max_length=200) 
    quantity = models.IntegerField(default=1)
    price = models.DecimalField(max_digits=8, decimal_places=2) 

    def __str__(self):
        return f"{self.quantity}x {self.product_name}"

# ==========================================
# 3. USER PROFILES & DEPENDENCIES
# ==========================================

class CustomerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='customer_profile')
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    profile_picture = models.ImageField(upload_to='customers/profiles/', blank=True, null=True)
    
    # Primary Delivery Details
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, default='Bengaluru')
    pincode = models.CharField(max_length=6, blank=True, null=True)

    def __str__(self):
        return f"Customer: {self.user.first_name}"

class Vendor(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='vendor_profile')
    business_name = models.CharField(max_length=255, unique=True)
    gst_number = models.CharField(max_length=15, blank=True, null=True, help_text="15-digit GSTIN number")
    phone_number = models.CharField(max_length=15)
    address = models.TextField()
    registered_at = models.DateTimeField(auto_now_add=True)
    profile_photo = models.ImageField(upload_to='vendors/profiles/', blank=True, null=True)
    aadhaar_card = models.FileField(upload_to='vendors/documents/', blank=True, null=True) # PDF or Image
    
    # BANKING DETAILS
    bank_account_name = models.CharField(max_length=255, blank=True, null=True)
    account_number = models.CharField(max_length=50, blank=True, null=True)
    bank_name = models.CharField(max_length=150, blank=True, null=True)
    ifsc_code = models.CharField(max_length=20, blank=True, null=True)
    passbook_photo = models.ImageField(upload_to='vendors/documents/', blank=True, null=True)

    def __str__(self):
        return f"{self.business_name} (Owner: {self.user.username})"

class RiderProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rider_profile')
    phone_number = models.CharField(max_length=15)
    age = models.IntegerField()
    vehicle_type = models.CharField(max_length=20, choices=[('Bike', 'Bike'), ('Scooter', 'Scooter')])
    license_number = models.CharField(max_length=50)
    license_photo = models.ImageField(upload_to='riders/licenses/')
    profile_photo = models.ImageField(upload_to='riders/profiles/')
    is_online = models.BooleanField(default=False)
    
    # BANKING DETAILS
    bank_account_name = models.CharField(max_length=255, blank=True, null=True)
    bank_account_number = models.CharField(max_length=50, blank=True, null=True)
    bank_name = models.CharField(max_length=150, blank=True, null=True)
    ifsc_code = models.CharField(max_length=20, blank=True, null=True)
    passbook_photo = models.ImageField(upload_to='riders/documents/', blank=True, null=True)

    accepted_terms = models.BooleanField(default=False)
    accepted_traffic_policy = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=False) 

    def __str__(self):
        return f"{self.user.first_name} - {self.vehicle_type}"

class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    fastx_coins = models.IntegerField(default=0, help_text="Loyalty points")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s Wallet - ₹{self.balance}"

class WalletTransaction(models.Model):
    TRANSACTION_TYPES = (
        ('CREDIT', 'Credit (Money Added)'),
        ('DEBIT', 'Debit (Money Spent)'),
    )

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    description = models.CharField(max_length=255)
    reference_id = models.CharField(max_length=100, blank=True, null=True, help_text="Order ID or Payment Gateway TXN ID")
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        sign = "+" if self.transaction_type == 'CREDIT' else "-"
        return f"{sign}₹{self.amount} | {self.description}"

class Wishlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wishlist_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product')

    def __str__(self):
        return f"{self.user.username} saved {self.product.name}"
    
class Address(models.Model):
    ADDRESS_TYPES = (
        ('Home', 'Home'),
        ('Work', 'Work'),
        ('Other', 'Other'),
    )
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='addresses')
    title = models.CharField(max_length=20, choices=ADDRESS_TYPES, default='Home')
    flat_no = models.CharField(max_length=100, help_text="House/Flat/Block No.")
    street = models.CharField(max_length=255, help_text="Street or Area")
    city = models.CharField(max_length=100, default='Bengaluru')
    pincode = models.CharField(max_length=6)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Addresses"
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.title} ({self.pincode})"
    
class UserSettings(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='settings_profile')
    
    order_updates = models.BooleanField(default=True, help_text="Receive SMS/Email for order status")
    promotional_alerts = models.BooleanField(default=False, help_text="Receive discounts and offers")
    whatsapp_alerts = models.BooleanField(default=True, help_text="Get delivery tracking on WhatsApp")
    dark_mode = models.BooleanField(default=False)
    language = models.CharField(max_length=50, default='English')
    two_factor_auth = models.BooleanField(default=False)

    def __str__(self):
        return f"Settings for {self.user.username}"

class Promotion(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True) # Optional description
    media = models.FileField(upload_to='promotions/')       # FileField allows BOTH Images and Videos
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at'] # Ensures new ones are first

    def is_video(self):
        """Helper for the HTML template to know if it should render an <img> or <video> tag"""
        if self.media:
            return self.media.name.lower().endswith(('.mp4', '.mov', '.avi', '.webm'))
        return False

class Feedback(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    order_id = models.CharField(max_length=50)
    
    # New Fields for Rider Tracking
    rider = models.ForeignKey(User, related_name='received_feedbacks', on_delete=models.SET_NULL, null=True, blank=True)
    rating = models.IntegerField(default=5)       # Platform/Order Rating
    rider_rating = models.IntegerField(default=5) # Rider Specific Rating
    
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Feedback for {self.order_id} - {self.rating} Stars"
        
class Coupon(models.Model):
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True, null=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    category_restriction = models.ForeignKey('Category', on_delete=models.SET_NULL, null=True, blank=True, help_text="Leave blank to apply to all categories")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.code

class CouponUsage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE)
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'coupon') # Ensures a user can only use a specific coupon once

# ==========================================
# 4. SIGNALS (Optimized)
# ==========================================
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_dependencies(sender, instance, created, **kwargs):
    if created:
        # Only create profiles if the user is not a superuser, 
        # or handle superuser profiles separately if needed.
        if not instance.is_superuser:
            Wallet.objects.get_or_create(user=instance)
            UserSettings.objects.get_or_create(user=instance)
            CustomerProfile.objects.get_or_create(user=instance)

# ==========================================
# PROMOTION SIGNALS
# ==========================================
@receiver(post_save, sender=Promotion)
def limit_promotions(sender, instance, created, **kwargs):
    """Your logic: Keeps only the latest 4 promotions active."""
    if created:
        promotions = Promotion.objects.all()
        if promotions.count() > 4:
            to_delete = promotions[4:]
            for promo in to_delete:
                promo.delete() # This triggers the post_delete signal below!

@receiver(post_delete, sender=Promotion)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    CRITICAL SERVER SAVER:
    Deletes the actual image/video file from your hosting server 
    when the Promotion record is deleted by your limit_promotions logic.
    """
    if instance.media:
        if os.path.isfile(instance.media.path):
            os.remove(instance.media.path)
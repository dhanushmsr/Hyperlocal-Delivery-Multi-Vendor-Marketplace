from django.contrib import admin
from .models import User, Vendor, Category, Product, Order, OrderItem, Wallet, WalletTransaction

# 1. User & Vendor Profiles
admin.site.register(User)
admin.site.register(Vendor)

# 2. Store Models
admin.site.register(Category)
admin.site.register(Product)

# 3. Order Models
admin.site.register(Order)
admin.site.register(OrderItem)

# 4. Financial Models
admin.site.register(Wallet)
admin.site.register(WalletTransaction)
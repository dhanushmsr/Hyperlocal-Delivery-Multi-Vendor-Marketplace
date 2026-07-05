# Hyperlocal-Delivery-Multi-Vendor-Marketplace
Balancing my B.E. in Computer Science with hands-on software development has pushed me to tackle complex architectural challenges. Today, I’m excited to share my latest full-stack Python project: FASTX, a complete Hyperlocal Quick Commerce platform. 


# FASTX: Hyperlocal Quick Commerce Platform 🛒🛵

FASTX is a comprehensive, multi-vendor 10-minute delivery ecosystem built with Python and Django. It synchronizes four distinct user roles—Customers, Vendors, Delivery Partners, and Administrators—into a single, real-time platform.

## 🚀 Features

### 1. Customer Storefront
* **Unified OTP Authentication:** Secure, passwordless login via Twilio API.
* **Dynamic Checkout:** Real-time calculation of platform fees, dynamic delivery charges based on cart distance/value, and GST.
* **FASTX Wallet & Coupons:** Integrated digital wallet and complex coupon validation engine (category-specific and min-value constraints).
* **Payment Gateway:** Razorpay integration with secure signature verification.

### 2. Vendor (Seller) Dashboard
* **Live Fulfillment Queue:** AJAX-powered dashboard to accept, pack, and dispatch incoming orders with a 60-second countdown timer.
* **Inventory Management:** Full CRUD operations for product listings and category mapping.
* **Automated Ledgers:** Live calculation of gross sales, tax deductions, and pending payouts.

### 3. Delivery Partner Portal
* **Availability Toggle:** "I am Active" state management to receive live pings.
* **Smart Assignment:** Accepts direct vendor assignments or picks up from an open unassigned pool.
* **Live Routing:** One-click Google Maps integration to navigate from Vendor pickup to Customer drop-off.

### 4. Admin Management Center
* **KYC & Onboarding:** Secure document viewer to approve/reject Vendor and Rider applications.
* **Financial Master Ledger:** Tracks platform commission (30% cut), overall revenue, and payout clearances.

## 🛠️ Tech Stack
* **Backend:** Django (Python)
* **Database:** SQL (SQLite / PostgreSQL)
* **Frontend:** HTML5, CSS3, Bootstrap 5, Vanilla JavaScript (AJAX/Fetch API)
* **Third-Party APIs:** Razorpay, Twilio, Nominatim (OpenStreetMap Geocoding)

## 📸 Application Flow
*(Insert the images/screenshots described below here)*

## 👨‍💻 Author
**Dhanush M**
* Aspiring Software Developer
* Connect with me on [LinkedIn](Link)

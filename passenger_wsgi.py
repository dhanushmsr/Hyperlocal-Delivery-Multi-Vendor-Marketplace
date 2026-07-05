import os
import sys

# 1. Add your project directory to the system path
sys.path.insert(0, os.path.dirname(__file__))

# 2. Point to your specific settings file
# IMPORTANT: 'ecom' must match the folder name where your settings.py is located
os.environ['DJANGO_SETTINGS_MODULE'] = 'ecom.settings'

# 3. Load the Django WSGI application
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
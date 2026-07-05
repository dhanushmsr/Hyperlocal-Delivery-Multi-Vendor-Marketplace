from django import template

register = template.Library()

@register.filter(name='has_rider_profile')
def has_rider_profile(user):
    return hasattr(user, 'rider_profile')
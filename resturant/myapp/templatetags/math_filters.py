from django import template
from decimal import Decimal
from django.core.exceptions import ValidationError

register = template.Library()

@register.filter
def multiply(value, arg):
    try:
        return Decimal(str(value)) * Decimal(str(arg))
    except (ValueError, TypeError, ValidationError):
        return Decimal('0.00')
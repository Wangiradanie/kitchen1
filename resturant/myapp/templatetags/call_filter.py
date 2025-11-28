# myapp/templatetags/call_filter.py
from django import template

register = template.Library()

@register.filter
def call(obj, method_name):
    """Call a method by name"""
    method = getattr(obj, method_name, None)
    if method and callable(method):
        return method()
    return None
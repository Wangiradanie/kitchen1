from allauth.account.adapter import DefaultAccountAdapter
from importlib import import_module

class CustomAccountAdapter(DefaultAccountAdapter):
    def authenticate(self, request, **credentials):
        user = super().authenticate(request, **credentials)
        if user and not user.is_approved and not user.is_superuser:
            return None  # Prevent unapproved users from logging in
        return user

    def get_signup_form_class(self, request):
        # Dynamically import StaffSignupForm to avoid circular imports
        module = import_module('auth_app.forms')
        return getattr(module, 'StaffSignupForm')
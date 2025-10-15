from django import forms

class StaffSignupForm(forms.Form):
    # optionally, add your custom signup fields here

    def signup(self, request, user):
        user.is_staff = True
        user.is_approved = False  # Make sure your User model has this field
        user.save()
        return user

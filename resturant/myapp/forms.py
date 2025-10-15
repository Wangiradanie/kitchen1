from django import forms
from .models import Recipe, Order, OrderItem, Requisition, InventoryItem, Table, MenuItem
from decimal import Decimal

class RequisitionForm(forms.ModelForm):
    class Meta:
        model = Requisition
        fields = ['item_name', 'units', 'quantity', 'unit_price', 'reason', 'comments']
        widgets = {
            'item_name': forms.TextInput(attrs={'placeholder': 'Enter item name', 'class': 'form-control', 'required': True}),
            'units': forms.TextInput(attrs={'placeholder': 'Enter units (e.g., kg, liters)', 'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01', 'placeholder': 'Enter quantity', 'class': 'form-control', 'required': True}),
            'unit_price': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01', 'placeholder': 'Enter unit price in UGX', 'class': 'form-control', 'required': True}),
            'reason': forms.TextInput(attrs={'placeholder': 'Enter reason for purchase', 'class': 'form-control'}),
            'comments': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Additional comments', 'class': 'form-control'}),
        }
        labels = {
            'item_name': 'Item Name',
            'units': 'Units',
            'quantity': 'Quantity',
            'unit_price': 'Unit Price (UGX)',
            'reason': 'Reason for Purchase',
            'comments': 'Comments',
        }
        help_texts = {
            'unit_price': 'Enter the price per unit in UGX.',
        }

    def clean(self):
        cleaned_data = super().clean()
        quantity = cleaned_data.get('quantity')
        unit_price = cleaned_data.get('unit_price')
        if quantity and unit_price:
            cleaned_data['total_price'] = quantity * unit_price
        else:
            raise forms.ValidationError("Both quantity and unit price are required to calculate total price.")
        print("Cleaned data:", cleaned_data)  # Debug
        return cleaned_data

class RecipeForm(forms.ModelForm):
    class Meta:
        model = Recipe
        fields = ['name', 'category', 'description', 'profit_percentage']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Enter recipe name'}),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Enter recipe description'}),
            'profit_percentage': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'Enter profit % for UGX selling price'}),
        }
        labels = {
            'name': 'Recipe Name',
            'category': 'Category',
            'description': 'Description',
            'profit_percentage': 'Profit Percentage (%) for UGX Price',
        }
        help_texts = {
            'profit_percentage': 'Enter the profit percentage to calculate the selling price in UGX (e.g., 20 for 20% profit).',
        }

    def clean_profit_percentage(self):
        profit_percentage = self.cleaned_data.get('profit_percentage')
        if profit_percentage < 0:
            raise forms.ValidationError("Profit percentage cannot be negative.")
        return profit_percentage

class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['customer', 'table']
        widgets = {
            'customer': forms.TextInput(attrs={'placeholder': 'Enter customer name'}),
            'table': forms.Select(attrs={'class': 'form-control'}),
        }
        labels = {
            'customer': 'Customer Name',
            'table': 'Table',
        }

class OrderItemForm(forms.ModelForm):
    class Meta:
        model = OrderItem
        fields = ['menu_item', 'quantity']
        widgets = {
            'menu_item': forms.Select(attrs={'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'min': '1', 'placeholder': 'Enter quantity'}),
        }
        labels = {
            'menu_item': 'Menu Item',
            'quantity': 'Quantity',
        }

class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ['name', 'units', 'quantity', 'unit_price']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Enter item name'}),
            'units': forms.TextInput(attrs={'placeholder': 'Enter unit (e.g., kg, liters)'}),
            'quantity': forms.NumberInput(attrs={'min': '0', 'step': '0.01', 'placeholder': 'Enter quantity'}),
            'unit_price': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'Enter unit price in UGX'}),
        }
        labels = {
            'name': 'Item Name',
            'units': 'Units',
            'quantity': 'Quantity',
            'unit_price': 'Unit Price (UGX)',
        }
        help_texts = {
            'unit_price': 'Enter the price per unit in UGX.',
        }

class UseItemForm(forms.Form):
    item = forms.ModelChoiceField(
        queryset=InventoryItem.objects.all(),
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='Inventory Item'
    )
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0.01,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter quantity to use', 'step': '0.01'}),
        label='Quantity to Use'
    )
    reason = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter reason for usage'}),
        label='Reason'
    )

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get('item')
        quantity = cleaned_data.get('quantity')
        if item and quantity and quantity > item.quantity:
            raise forms.ValidationError(f'Cannot use {quantity} units. Only {item.quantity} available!')
        return cleaned_data
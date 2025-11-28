from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum, F
from django.db.models.functions import Coalesce
from decimal import Decimal
from django.db import transaction 
from django.db import transaction, IntegrityError
import time
from django.contrib.auth import get_user_model
from django.db.models import Max


# ----------------------------------------------------------------------
#  USER
# ----------------------------------------------------------------------
class CustomUser(AbstractUser):
    ROLE_CHOICES = [
        ('staff', 'Staff'),
        ('operations_manager', 'Operations Manager'),
        ('finance', 'Finance'),
        ('director', 'Director'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    is_approved = models.BooleanField(default=False, help_text="Admin must approve to activate")

    def save(self, *args, **kwargs):
        self.is_active = self.is_approved
        super().save(*args, **kwargs)
    
    def has_approval_right(self, field):
    
        mapping = {
            'operations_manager': 'operations_manager',
            'finance': 'finance',
            'director': 'director',
                    }
        required_role = mapping.get(field)
        if not required_role:
            return False
        return self.role == required_role

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


# ----------------------------------------------------------------------
#  INVENTORY
# ----------------------------------------------------------------------
class InventoryItem(models.Model):
    name = models.CharField(max_length=100, unique=True)
    units = models.CharField(max_length=50)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.units})"


class InventoryHistory(models.Model):
    CHANGE_TYPES = [
        ('Added', 'Added'),
        ('Used', 'Used'),
        ('Adjusted', 'Adjusted'),
    ]
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    units = models.CharField(max_length=50)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField()
    change_type = models.CharField(max_length=20, choices=CHANGE_TYPES)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.change_type} {self.quantity} {self.units} of {self.item.name}"


# ----------------------------------------------------------------------
#  TABLE
# ----------------------------------------------------------------------
class DTable(models.Model):
    name = models.CharField(max_length=50, unique=True)
    is_occupied = models.BooleanField(default=False)

    def __str__(self):
        return self.name


# ----------------------------------------------------------------------
#  RECIPE
# ----------------------------------------------------------------------
class Recipe(models.Model):
    CATEGORY_CHOICES = [
        ('Starter', 'Starter'),
        ('Main Course', 'Main Course'),
        ('Dessert', 'Dessert'),
        ('Break Fast', 'Break Fast'),
    ]
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    description = models.TextField(blank=True, null=True)
    profit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=20.00)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)

    def __str__(self):
        return self.name

    def update_cost_and_price(self):
        self.total_cost = sum(
            Decimal(str(ing.quantity)) * ing.unit_price
            for ing in self.ingredients.all()
        ) or Decimal('0.00')
        profit_multiplier = Decimal('1.0') + (self.profit_percentage / Decimal('100.0'))
        self.selling_price = self.total_cost * profit_multiplier
        self.save(update_fields=['total_cost', 'selling_price'])


class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='ingredients')
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    def save(self, *args, **kwargs):
        if not self.unit_price:
            self.unit_price = self.inventory_item.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity} {self.inventory_item.units} of {self.inventory_item.name}"


# ----------------------------------------------------------------------
#  MENU ITEM
# ----------------------------------------------------------------------
class MenuItem(models.Model):
    CATEGORY_CHOICES = [
        ('Starters', 'Starters'),
        ('Main Course', 'Main Course'),
        ('Desserts', 'Desserts'),
        ('Break Fast', 'Break Fast'),
    ]
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    recipe = models.ForeignKey(Recipe, on_delete=models.SET_NULL, null=True, blank=True, related_name='menu_items')

    def __str__(self):
        return self.name


class MenuItemIngredient(models.Model):
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    quantity_needed = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity_needed} {self.inventory_item.units} of {self.inventory_item.name}"


# ----------------------------------------------------------------------
#  ORDER & ORDER ITEM
# ----------------------------------------------------------------------
class Order(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Started', 'Started'),
        ('Ready', 'Ready'),
        ('Canceled', 'Canceled'),
    ]
    order_number = models.CharField(max_length=20, unique=True)
    customer = models.CharField(max_length=100, blank=True, null=True)
    table = models.ForeignKey(DTable, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    timestamp = models.DateTimeField(auto_now_add=True)
    start_time = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def save(self, *args, **kwargs):
        if not self.order_number:
            last = Order.objects.order_by('-id').first()
            if last and last.order_number and last.order_number.startswith('ORD-'):
                try:
                    num = int(last.order_number[4:]) + 1
                    self.order_number = f"ORD-{num:04d}"
                except:
                    self.order_number = "ORD-0001"
            else:
                self.order_number = "ORD-0001"
        super().save(*args, **kwargs)

    def time_taken(self):
        if self.start_time and self.completed_at:
            delta = self.completed_at - self.start_time
            total_seconds = int(delta.total_seconds())
            h, rem = divmod(total_seconds, 3600)
            m, s = divmod(rem, 60)
            return f"{h:02d}:{m:02d}:{s:02d}"
        return "N/A"

    def cogs(self):
        total = InventoryHistory.objects.filter(
            reason__contains=f"order {self.order_number}",
            change_type='Used'
        ).aggregate(
            total=Coalesce(Sum(F('quantity') * F('unit_price')), Decimal('0.00'))
        )['total']
        return total

    def profit(self):
        return self.total_price - self.cogs()

    def original_cogs(self):
        total = Decimal('0.00')
        for item in self.items.all():
            if item.menu_item.recipe:
                total += sum(
                    ing.quantity * ing.unit_price
                    for ing in item.menu_item.recipe.ingredients.all()
                ) * item.quantity
        return total.quantize(Decimal('0.01'))

    def __str__(self):
        return f"Order {self.order_number} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.menu_item and self.quantity:
            self.total_price = self.menu_item.price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.name}"

    class Meta:
        ordering = ['-created_at']


class Requisition(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
    ]

    requisition_number = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='requisitions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    operations_manager_approval = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    finance_approval = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    director_approval = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    total_price = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    is_archived = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="Pending")

    def save(self, *args, **kwargs):
        if not self.requisition_number:
            next_num = RequisitionCounter.get_next_number()
            self.requisition_number = f"REQ-{next_num:04d}"
        super().save(*args, **kwargs)

    def overall_status(self):
        approvals = [self.operations_manager_approval, self.finance_approval, self.director_approval]
        if all(a == 'Approved' for a in approvals):
            return 'Fully Approved'
        if any(a == 'Rejected' for a in approvals):
            return 'Rejected'
        return 'Pending'

    def __str__(self):
        return self.requisition_number


# models.py (continued)
class RequisitionHistory(models.Model):
    requisition = models.ForeignKey('Requisition', on_delete=models.CASCADE, related_name='history')
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    action = models.CharField(max_length=20, choices=[
        ('approve', 'Approved'),
        ('reject', 'Rejected'),
        ('submit', 'Submitted')
    ])
    field = models.CharField(max_length=20, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user} {self.action} {self.field or ''} on {self.timestamp}"
    
class RequisitionItem(models.Model):
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name='items')
    item_name = models.CharField(max_length=100)
    units = models.CharField(max_length=50, blank=True, null=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=15, decimal_places=2, editable=False)

    def save(self, *args, **kwargs):
        self.total_price = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.item_name} x {self.quantity}"
    
class RequisitionCounter(models.Model):
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'requisition_counter'

    @classmethod
    def get_next_number(cls):
        with transaction.atomic():
            counter = cls.objects.select_for_update().first()
            if not counter:
                counter = cls.objects.create(last_number=0)
            counter.last_number += 1
            counter.save(update_fields=['last_number'])
            return counter.last_number
# ----------------------------------------------------------------------
#  SIGNALS
# ----------------------------------------------------------------------
@receiver(post_save, sender=Recipe)
def create_or_update_menu_item_for_recipe(sender, instance, **kwargs):
    if not instance.ingredients.exists():
        return
    category_map = {
        'Starter': 'Starters', 'Main Course': 'Main Course',
        'Dessert': 'Desserts', 'Break Fast': 'Break Fast'
    }
    menu_category = category_map.get(instance.category, 'Main Course')
    selling_price = instance.selling_price if instance.selling_price > 0 else Decimal('0.01')

    menu_item, _ = MenuItem.objects.update_or_create(
        recipe=instance,
        defaults={'name': instance.name, 'category': menu_category, 'price': selling_price}
    )
    for ing in instance.ingredients.all():
        MenuItemIngredient.objects.update_or_create(
            menu_item=menu_item,
            inventory_item=ing.inventory_item,
            defaults={'quantity_needed': ing.quantity}
        )


@receiver([post_save, post_delete], sender=OrderItem)
def update_order_total(sender, instance, **kwargs):
    if not instance.order_id:
        return
    total = OrderItem.objects.filter(order=instance.order).aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')
    Order.objects.filter(pk=instance.order.pk).update(total_price=total)
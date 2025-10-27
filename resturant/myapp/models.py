from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
from decimal import Decimal

class CustomUser(AbstractUser):
    is_manager = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=False)  # Admin approval required

    def save(self,*args, **kwargs):
# Ensure user is only active if approved
       if not self.is_approved:
          self.is_active = False
       else:
          self.is_active = True
    
       super().save(*args,**kwargs)



    def __str__(self):
        return self.username

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

class Table(models.Model):
    name = models.CharField(max_length=50, unique=True)
    is_occupied = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class Recipe(models.Model):
    CATEGORY_CHOICES = [
        ('Starter', 'Starter'),
        ('Main Course', 'Main Course'),
        ('Dessert', 'Dessert'),
    ]
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    description = models.TextField(blank=True, null=True)
    profit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=20.00, help_text="Profit percentage for selling price")
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)

    def __str__(self):
        return self.name

    def update_cost_and_price(self):
        print(f"Updating cost and price for Recipe: {self.name}, ID: {self.id}")
        self.total_cost = sum(
            Decimal(str(ingredient.quantity)) * ingredient.unit_price
            for ingredient in self.ingredients.all()
        ) or Decimal('0.00')
        profit_multiplier = Decimal('1.0') + (self.profit_percentage / Decimal('100.0'))
        self.selling_price = self.total_cost * profit_multiplier
        self.save()

class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='ingredients')
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price per unit in UGX")

    def save(self, *args, **kwargs):
        if not self.unit_price:
            self.unit_price = self.inventory_item.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity} {self.inventory_item.units} of {self.inventory_item.name} for {self.recipe.name}"

class MenuItem(models.Model):
    CATEGORY_CHOICES = [
        ('Starters', 'Starters'),
        ('Main Course', 'Main Course'),
        ('Desserts', 'Desserts'),
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
        return f"{self.quantity_needed} {self.inventory_item.units} of {self.inventory_item.name} for {self.menu_item.name}"

class Order(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Started', 'Started'),
        ('Ready', 'Ready'),
        ('Canceled', 'Canceled'),
    ]
    order_number = models.CharField(max_length=20, unique=True)
    customer = models.CharField(max_length=100, blank=True, null=True)
    table = models.ForeignKey(Table, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    timestamp = models.DateTimeField(auto_now_add=True)
    start_time = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def save(self, *args, **kwargs):
        if not self.order_number:
            last_order = Order.objects.order_by('-id').first()
            if last_order and last_order.order_number and last_order.order_number.startswith('ORD-'):
                try:
                    last_number = int(last_order.order_number[4:])
                    new_number = last_number + 1
                    self.order_number = f"ORD-{new_number:04d}"
                except (ValueError, IndexError):
                    self.order_number = "ORD-0001"
            else:
                self.order_number = "ORD-0001"
        super().save(*args, **kwargs)

    def time_taken(self):
        if self.start_time and self.completed_at:
            delta = self.completed_at - self.start_time
            total_seconds = int(delta.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return "N/A"

    def __str__(self):
        return f"Order {self.order_number} ({self.status})"

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    total_price = models.DecimalField(max_digits=10, decimal_places=2, editable=False)

    def save(self, *args, **kwargs):
        self.total_price = self.menu_item.price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity} x {self.menu_item.name} for Order {self.order.order_number}"

class Requisition(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
    ]
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    item_name = models.CharField(max_length=100)
    units = models.CharField(max_length=50, blank=True, null=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price per unit in UGX")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, editable=False)
    reason = models.TextField(blank=True, null=True)
    comments = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    admin_approval = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    manager_approval = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    director_approval = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')

    def save(self, *args, **kwargs):
        self.total_price = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Requisition for {self.item_name} by {self.user.username}"

@receiver(post_save, sender=Recipe)
def create_or_update_menu_item_for_recipe(sender, instance, created, **kwargs):
    print(f"Signal triggered for Recipe: {instance.name}, ID: {instance.id}")
    if not instance.ingredients.exists():
        print("No ingredients, skipping MenuItem creation")
        return
    category_map = {
        'Starter': 'Starters',
        'Main Course': 'Main Course',
        'Dessert': 'Desserts',
        'Breakfast': 'Main Course',
        'Lunch': 'Main Course',
        'Dinner': 'Main Course'
    }
    menu_item_category = category_map.get(instance.category, 'Main Course')
    selling_price = instance.selling_price if instance.selling_price > 0 else Decimal('0.01')
    menu_item, _ = MenuItem.objects.update_or_create(
        recipe=instance,
        defaults={
            'name': instance.name,
            'category': menu_item_category,
            'price': selling_price
        }
    )
    MenuItemIngredient.objects.filter(menu_item=menu_item).delete()
    for ingredient in instance.ingredients.all():
        MenuItemIngredient.objects.update_or_create(
            menu_item=menu_item,
            inventory_item=ingredient.inventory_item,
            defaults={'quantity_needed': ingredient.quantity}
        )
    print(f"MenuItem created/updated: {menu_item.name}")
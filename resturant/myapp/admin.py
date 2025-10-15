from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .models import (
    CustomUser, InventoryItem, InventoryHistory, Table, Recipe,
    RecipeIngredient, MenuItem, MenuItemIngredient, Order, OrderItem, Requisition
)

# Inline Classes
class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1
    fields = ['inventory_item', 'quantity', 'unit_price']
    autocomplete_fields = ['inventory_item']

class MenuItemIngredientInline(admin.TabularInline):
    model = MenuItemIngredient
    extra = 1
    fields = ['inventory_item', 'quantity_needed']
    autocomplete_fields = ['inventory_item']

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    fields = ['menu_item', 'quantity', 'total_price']
    autocomplete_fields = ['menu_item']
    readonly_fields = ['total_price']

# Admin Classes
@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'email', 'is_staff', 'is_manager', 'is_active']
    list_filter = ['is_staff', 'is_manager', 'is_active']
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_manager', 'is_superuser', 'groups', 'user_permissions')
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2', 'is_staff', 'is_manager'),
        }),
    )
    search_fields = ['username', 'email']
    ordering = ['username']

@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'units', 'quantity', 'unit_price', 'created_at']
    list_filter = ['units', 'created_at']
    search_fields = ['name']
    ordering = ['name']

    def get_readonly_fields(self, request, obj=None):
        if obj:  # Editing an existing object
            return ['created_at']
        return []

@admin.register(InventoryHistory)
class InventoryHistoryAdmin(admin.ModelAdmin):
    list_display = ['item', 'change_type', 'quantity', 'units', 'unit_price', 'reason', 'timestamp']
    list_filter = ['change_type', 'timestamp']
    search_fields = ['item__name', 'reason']
    ordering = ['-timestamp']

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ['item', 'quantity', 'units', 'unit_price', 'reason', 'change_type', 'timestamp']
        return []

@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_occupied']
    list_filter = ['is_occupied']
    search_fields = ['name']
    ordering = ['name']

@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'total_cost', 'selling_price', 'created_at']
    list_filter = ['category', 'created_at']
    search_fields = ['name']
    ordering = ['-created_at']
    inlines = [RecipeIngredientInline]

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ['total_cost', 'selling_price', 'created_at']
        return ['total_cost', 'selling_price']

@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'recipe']
    list_filter = ['category']
    search_fields = ['name']
    ordering = ['name']
    inlines = [MenuItemIngredientInline]

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ['price']
        return []

@admin.register(MenuItemIngredient)
class MenuItemIngredientAdmin(admin.ModelAdmin):
    list_display = ['menu_item', 'inventory_item', 'quantity_needed']
    search_fields = ['menu_item__name', 'inventory_item__name']
    ordering = ['menu_item__name']

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'customer', 'table', 'status', 'total_price', 'timestamp', 'time_taken']
    list_filter = ['status', 'timestamp']
    search_fields = ['order_number', 'customer']
    ordering = ['-timestamp']
    inlines = [OrderItemInline]

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ['order_number', 'total_price', 'timestamp', 'time_taken']
        return ['order_number', 'total_price', 'time_taken']

    def time_taken(self, obj):
        return obj.time_taken()
    time_taken.short_description = 'Time Taken'

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'menu_item', 'quantity', 'total_price']
    list_filter = ['order__status']
    search_fields = ['menu_item__name']
    ordering = ['order__order_number']

@admin.register(Requisition)
class RequisitionAdmin(admin.ModelAdmin):
    list_display = ['item_name', 'quantity', 'total_price', 'admin_approval', 'manager_approval', 'director_approval', 'created_at']
    list_filter = ['admin_approval', 'manager_approval', 'director_approval', 'created_at']
    search_fields = ['item_name', 'description']
    ordering = ['-created_at']

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ['user', 'item_name', 'quantity', 'total_price', 'description', 'created_at']
        return []

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.has_perm('myapp.can_manage_requisitions'):
            return qs
        return qs.filter(user=request.user)

@receiver(post_migrate)
def create_groups_and_permissions(sender, **kwargs):
    if sender.name != "myapp":
        return

    content_types = {
        'order': ContentType.objects.get_for_model(Order),
        'inventory': ContentType.objects.get_for_model(InventoryItem),
        'requisition': ContentType.objects.get_for_model(Requisition),
    }

    perms = {
        'can_manage_orders': ('Can manage orders', content_types['order']),
        'can_manage_inventory': ('Can manage inventory', content_types['inventory']),
        'can_manage_requisitions': ('Can manage requisitions', content_types['requisition']),
    }

    for codename, (name, ct) in perms.items():
        Permission.objects.get_or_create(codename=codename, name=name, content_type=ct)

    manager_group, _ = Group.objects.get_or_create(name='Manager')
    director_group, _ = Group.objects.get_or_create(name='Director')

    requisition_perm = Permission.objects.get(codename='can_manage_requisitions')
    manager_group.permissions.add(requisition_perm)
    director_group.permissions.add(requisition_perm)
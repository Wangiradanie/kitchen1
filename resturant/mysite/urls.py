from django.contrib import admin
from django.urls import path, include
from myapp import views

urlpatterns = [
    path('admin/', admin.site.urls),

    # Home / POS
    path('', views.pos_view, name='home'),
    path('pos/', views.pos_view, name='pos'),

    # Orders
    path('orders/', views.orders_view, name='orders'),

    # Inventory
    path('inventory/', views.inventory_view, name='inventory'),
    path('inventory/history/', views.inventory_history_view, name='inventory_history'),
    path('get-inventory/<int:item_id>/', views.get_inventory_item, name='get_inventory_item'),  # ADD THIS

    # Recipes
    path('recipes/', views.recipes_view, name='recipes'),
    path('recipes/data/', views.recipes_data, name='recipes_data'),
    path('add_recipe_ingredients/', views.add_recipe_ingredients, name='add_recipe_ingredients'),
    path('update_menu_item/', views.update_menu_item, name='update_menu_item'),
    path('delete_menu_item/', views.delete_menu_item, name='delete_menu_item'),

    # Requisitions
    path('requisitions/', views.requisitions_view, name='requisitions'),
    path('requisitions/<int:requisition_id>/action/', views.requisition_action, name='requisition_action'),
    path('requisition/<int:requisition_id>/pdf/', views.requisition_pdf, name='requisition_pdf'),
    path('requisition/add_item/', views.requisition_add_item, name='requisition_add_item'),
    path('requisitions/submit/', views.requisition_submit, name='requisition_submit'),


    # Table AJAX updates
    path('table/update/', views.update_table_status, name='update_table_status'),

    path('dashboard/', views.dashboard_view, name='dashboard'),

    # User accounts
    path('accounts/', include('allauth.urls')),
    
]
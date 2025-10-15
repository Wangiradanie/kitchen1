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

    # Reports
    path('reports/', views.reports_view, name='reports'),
    path('reports/data/', views.reports_data, name='reports_data'),
    path('reports/pdf/', views.generate_pdf_report, name='generate_pdf_report'),  # Added for PDF

    # Inventory
    path('inventory/', views.inventory_view, name='inventory'),
    path('inventory/history/', views.inventory_history_view, name='inventory_history'),

    # Recipes
    path('recipes/', views.recipes_view, name='recipes'),
    path('recipes/data/', views.recipes_data, name='recipes_data'),  # Added for AJAX
    path('add_recipe_ingredients/', views.add_recipe_ingredients, name='add_recipe_ingredients'),
    path('update_menu_item/', views.update_menu_item, name='update_menu_item'),
    path('delete_menu_item/', views.delete_menu_item, name='delete_menu_item'),

    # Requisitions
    path('requisitions/', views.requisitions_view, name='requisitions'),
    path('requisitions/<int:requisition_id>/action/', views.requisition_action, name='requisition_action'),

    # Table AJAX updates
    path('table/update/', views.update_table_status, name='update_table_status'),

    # User accounts (login, logout, signup)
    path('accounts/', include('allauth.urls')),
]
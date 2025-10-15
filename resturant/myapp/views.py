from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import datetime, timedelta
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse
from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField
from django.db import transaction
from .models import (
    InventoryItem, Table, MenuItem, Order, OrderItem, Requisition,
    InventoryHistory, MenuItemIngredient, Recipe, RecipeIngredient,
)
from .forms import (
    InventoryItemForm, UseItemForm, OrderForm, OrderItemForm,
    RequisitionForm, RecipeForm
)
import json
from decimal import Decimal
import pytz
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table as ReportLabTable, TableStyle, Paragraph
from reportlab.lib.units import inch
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend

# ------------------- INVENTORY -------------------
@login_required
def inventory_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    items = InventoryItem.objects.all().order_by('name')
    total_cost = items.aggregate(
        total=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField()))
    )['total'] or Decimal('0.00')
    form = InventoryItemForm()
    use_form = UseItemForm()

    if request.method == 'POST':
        if 'add-update' in request.POST:
            form = InventoryItemForm(request.POST)
            if form.is_valid():
                item, created = InventoryItem.objects.get_or_create(
                    name=form.cleaned_data['name'],
                    defaults={
                        'units': form.cleaned_data['units'],
                        'quantity': form.cleaned_data['quantity'],
                        'unit_price': form.cleaned_data['unit_price']
                    }
                )
                if not created:
                    if item.quantity != form.cleaned_data['quantity'] or item.unit_price != form.cleaned_data['unit_price']:
                        InventoryHistory.objects.create(
                            item=item,
                            units=item.units,
                            quantity=form.cleaned_data['quantity'],
                            unit_price=form.cleaned_data['unit_price'],
                            reason='Inventory adjusted',
                            change_type='Adjusted'
                        )
                    item.units = form.cleaned_data['units']
                    item.quantity = form.cleaned_data['quantity']
                    item.unit_price = form.cleaned_data['unit_price']
                    item.save()
                else:
                    InventoryHistory.objects.create(
                        item=item,
                        units=item.units,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                        reason='New item added',
                        change_type='Added'
                    )
                messages.success(request, f'{item.name} updated successfully.')
                return redirect('inventory')
            else:
                messages.error(request, f'Invalid inventory form data: {form.errors.as_text()}')

        elif 'use-item' in request.POST:
            use_form = UseItemForm(request.POST)
            if use_form.is_valid():
                item = use_form.cleaned_data['item']
                quantity = use_form.cleaned_data['quantity']
                reason = use_form.cleaned_data['reason']
                if quantity > item.quantity:
                    messages.error(request, f'Cannot use {quantity} units. Only {item.quantity} available!')
                elif quantity <= 0:
                    messages.error(request, 'Quantity must be greater than zero.')
                else:
                    item.quantity -= quantity
                    item.save()
                    InventoryHistory.objects.create(
                        item=item,
                        units=item.units,
                        quantity=quantity,
                        unit_price=item.unit_price,
                        reason=reason,
                        change_type='Used'
                    )
                    messages.success(request, f'{quantity} units of {item.name} used.')
                    return redirect('inventory')
            else:
                messages.error(request, f'Invalid usage form data: {use_form.errors.as_text()}')

    return render(request, 'inventory.html', {
        'items': items,
        'total_cost': total_cost,
        'form': form,
        'use_form': use_form,
    })

# ------------------- INVENTORY HISTORY -------------------
@login_required
def inventory_history_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    history = InventoryHistory.objects.select_related('item').all().order_by('-timestamp')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if start_date:
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            if end_date:
                end_date = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            else:
                end_date = start_date + timedelta(days=1)
            history = history.filter(timestamp__range=[start_date, end_date])
        except ValueError:
            messages.error(request, 'Invalid date format.')

    return render(request, 'inventory_history.html', {
        'history': history,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
        'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
    })

# ------------------- RECIPES -------------------
@login_required
def recipes_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    recipes = Recipe.objects.prefetch_related('ingredients__inventory_item', 'menu_items').all().order_by('-created_at')
    start_date = request.GET.get('start_date')
    period = request.GET.get('period', 'weekly')

    if start_date:
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            end_date = start_date + timedelta(days=6 if period == 'weekly' else 30)
            recipes = recipes.filter(created_at__range=[start_date, end_date])
        except ValueError:
            messages.error(request, 'Invalid date format.')

    inventory_items = [
        {
            'id': item.id,
            'name': item.name,
            'quantity': float(item.quantity),
            'units': item.units,
            'unit_price': float(item.unit_price)
        }
        for item in InventoryItem.objects.all().order_by('name')
    ]
    menu_items = MenuItem.objects.select_related('recipe').all()
    form = RecipeForm()

    if request.method == 'POST':
        if 'add_recipe' in request.POST:
            print("POST data:", dict(request.POST))  # Debug POST data
            form = RecipeForm(request.POST)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        recipe = form.save(commit=False)
                        recipe.total_cost = Decimal('0.00')
                        recipe.selling_price = Decimal('0.00')
                        recipe.save()
                        print(f"Saved recipe ID: {recipe.id}")  # Debug ID
                        inventory_ids = request.POST.getlist('inventory_item[]')
                        quantities = request.POST.getlist('quantity[]')

                        if not inventory_ids or not quantities or len(inventory_ids) != len(quantities):
                            messages.error(request, 'Please provide matching inventory items and quantities.')
                            return redirect('recipes')

                        for i in range(len(inventory_ids)):
                            try:
                                inv_id = int(inventory_ids[i])
                                quantity = Decimal(quantities[i])
                                if quantity <= 0:
                                    messages.error(request, f'Quantity for ingredient {i + 1} must be greater than zero.')
                                    return redirect('recipes')
                                inventory_item = InventoryItem.objects.get(id=inv_id)
                                if quantity > inventory_item.quantity:
                                    messages.error(request, f'Insufficient {inventory_item.name}: {quantity} requested, {inventory_item.quantity} available.')
                                    return redirect('recipes')
                                inventory_item.quantity -= quantity
                                inventory_item.save()
                                InventoryHistory.objects.create(
                                    item=inventory_item,
                                    units=inventory_item.units,
                                    quantity=quantity,
                                    unit_price=inventory_item.unit_price,
                                    reason=f'Used for recipe {recipe.name}',
                                    change_type='Used'
                                )
                                RecipeIngredient.objects.create(
                                    recipe=recipe,
                                    inventory_item=inventory_item,
                                    quantity=quantity,
                                    unit_price=inventory_item.unit_price
                                )
                            except (ValueError, InventoryItem.DoesNotExist) as e:
                                messages.error(request, f'Invalid data for ingredient {i + 1}: {str(e)}')
                                return redirect('recipes')

                        recipe.update_cost_and_price()
                        messages.success(request, f'Recipe "{recipe.name}" added successfully.')
                        return redirect('recipes')
                except Exception as e:
                    messages.error(request, f'Error saving recipe: {str(e)}')
                    print(f"Error in transaction: {str(e)}")
            else:
                messages.error(request, f'Invalid recipe form data: {form.errors.as_text()}')
                print(f"Form errors: {form.errors.as_text()}")
        elif 'delete_recipe' in request.POST:
            recipe_id = request.POST.get('recipe_id')
            try:
                recipe = Recipe.objects.get(id=recipe_id)
                recipe.delete()
                messages.success(request, f'Recipe "{recipe.name}" deleted successfully.')
            except Recipe.DoesNotExist:
                messages.error(request, 'Recipe not found.')
            return redirect('recipes')
        else:
            messages.error(request, 'Invalid POST request.')

    return render(request, 'recipes.html', {
        'recipes': recipes,
        'inventory_items': json.dumps(inventory_items),
        'menu_items': menu_items,
        'form': form,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
        'period': period,
    })

# ------------------- RECIPES DATA -------------------
@login_required
def recipes_data(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    recipes = Recipe.objects.prefetch_related('ingredients__inventory_item').all().order_by('-created_at')
    start_date = request.GET.get('start_date')
    period = request.GET.get('period', 'weekly')

    if start_date:
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            end_date = start_date + timedelta(days=6 if period == 'weekly' else 30)
            recipes = recipes.filter(created_at__range=[start_date, end_date])
        except ValueError:
            pass

    data = []
    for recipe in recipes:
        ingredients = [
            {
                'inventory_item_name': ingredient.inventory_item.name,
                'quantity': float(ingredient.quantity),
                'units': ingredient.inventory_item.units,
                'unit_price': float(ingredient.unit_price)
            }
            for ingredient in recipe.ingredients.all()
        ]
        data.append({
            'id': recipe.id,
            'name': recipe.name,
            'category': recipe.category,
            'created_at': recipe.created_at.astimezone(pytz.timezone('Africa/Nairobi')).strftime('%Y-%m-%d %H:%M:%S'),
            'ingredients': ingredients,
            'total_cost': float(recipe.total_cost),
            'selling_price': float(recipe.selling_price),
        })
    return JsonResponse(data, safe=False)

# ------------------- ADD RECIPE INGREDIENTS -------------------
@login_required
def add_recipe_ingredients(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    if request.method == 'POST':
        recipe_id = request.POST.get('recipe_id')
        try:
            recipe = Recipe.objects.get(id=recipe_id)
            inventory_ids = request.POST.getlist('inventory_item[]')
            quantities = request.POST.getlist('quantity[]')

            if len(inventory_ids) != len(quantities):
                messages.error(request, 'Mismatched ingredient data.')
                return redirect('recipes')

            with transaction.atomic():
                for i in range(len(inventory_ids)):
                    try:
                        inv_id = int(inventory_ids[i])
                        quantity = Decimal(quantities[i])
                        if quantity <= 0:
                            messages.error(request, f'Quantity for ingredient {i + 1} must be greater than zero.')
                            return redirect('recipes')
                        inventory_item = InventoryItem.objects.get(id=inv_id)
                        if quantity > inventory_item.quantity:
                            messages.error(request, f'Insufficient {inventory_item.name}: {quantity} requested, {inventory_item.quantity} available.')
                            return redirect('recipes')
                        inventory_item.quantity -= quantity
                        inventory_item.save()
                        InventoryHistory.objects.create(
                            item=inventory_item,
                            units=inventory_item.units,
                            quantity=quantity,
                            unit_price=inventory_item.unit_price,
                            reason=f'Used for recipe {recipe.name}',
                            change_type='Used'
                        )
                        RecipeIngredient.objects.create(
                            recipe=recipe,
                            inventory_item=inventory_item,
                            quantity=quantity,
                            unit_price=inventory_item.unit_price
                        )
                    except (ValueError, InventoryItem.DoesNotExist) as e:
                        messages.error(request, f'Invalid data for ingredient {i + 1}: {str(e)}')
                        return redirect('recipes')
                
                recipe.update_cost_and_price()
                messages.success(request, f'Ingredients added to recipe "{recipe.name}".')
                return redirect('recipes')
        except Recipe.DoesNotExist:
            messages.error(request, 'Recipe not found.')
            return redirect('recipes')
    else:
        recipes = Recipe.objects.all()
        inventory_items = [
            {
                'id': item.id,
                'name': item.name,
                'quantity': float(item.quantity),
                'units': item.units,
                'unit_price': float(item.unit_price)
            }
            for item in InventoryItem.objects.all().order_by('name')
        ]
        return render(request, 'add_recipe_ingredients.html', {
            'recipes': recipes,
            'inventory_items': json.dumps(inventory_items),
        })

# ------------------- POS -------------------
@login_required
def pos_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    tables = Table.objects.all()
    try:
        menu_items = MenuItem.objects.select_related('recipe').all()
        for item in menu_items:
            print(f"MenuItem: {item.name}, Category: {item.category}, Price: {item.price}, Recipe: {item.recipe.name if item.recipe else 'None'}")
    except Exception as e:
        messages.error(request, f'Error fetching menu items: {str(e)}')
        menu_items = []
    inventory_items = InventoryItem.objects.all()
    order_form = OrderForm()
    order_item_form = OrderItemForm()

    if request.method == 'POST':
        if 'submit-order' in request.POST:
            order_form = OrderForm(request.POST)
            items_data = json.loads(request.POST.get('order_items', '[]'))
            print(f"Order form data: {request.POST}")
            print(f"Order items data: {items_data}")
            if order_form.is_valid() and items_data:
                with transaction.atomic():
                    order = order_form.save(commit=False)
                    order.status = 'Pending'
                    order.save()
                    total_price = Decimal('0.00')
                    for item in items_data:
                        try:
                            menu_item = MenuItem.objects.get(id=item['id'])
                            quantity = int(item.get('quantity', 0))
                            if quantity <= 0:
                                messages.error(request, f'Invalid quantity for {menu_item.name}.')
                                return redirect('pos')
                            order_item = OrderItem(
                                order=order,
                                menu_item=menu_item,
                                quantity=quantity
                            )
                            order_item.save()
                            total_price += order_item.total_price
                            if menu_item.recipe:
                                for ingredient in menu_item.recipe.ingredients.all():
                                    inventory_item = ingredient.inventory_item
                                    needed = ingredient.quantity * quantity
                                    if inventory_item.quantity < needed:
                                        messages.error(request, f'Insufficient {inventory_item.name} for {menu_item.name}')
                                        return redirect('pos')
                                    inventory_item.quantity -= needed
                                    inventory_item.save()
                                    InventoryHistory.objects.create(
                                        item=inventory_item,
                                        units=inventory_item.units,
                                        quantity=needed,
                                        unit_price=inventory_item.unit_price,
                                        reason=f'Used for {menu_item.name} in order {order.order_number}',
                                        change_type='Used'
                                    )
                            for ingredient in menu_item.menuitemingredient_set.all():
                                inventory_item = ingredient.inventory_item
                                needed = ingredient.quantity_needed * quantity
                                if inventory_item.quantity < needed:
                                    messages.error(request, f'Insufficient {inventory_item.name} for {menu_item.name}')
                                    return redirect('pos')
                                inventory_item.quantity -= needed
                                inventory_item.save()
                                InventoryHistory.objects.create(
                                    item=inventory_item,
                                    units=inventory_item.units,
                                    quantity=needed,
                                    unit_price=inventory_item.unit_price,
                                    reason=f'Used for {menu_item.name} in order {order.order_number}',
                                    change_type='Used'
                                )
                        except MenuItem.DoesNotExist:
                            messages.error(request, f'Menu item ID {item["id"]} not found.')
                            return redirect('pos')
                        except (ValueError, KeyError) as e:
                            messages.error(request, f'Invalid item data: {str(e)}')
                            return redirect('pos')
                    order.total_price = total_price
                    order.save()
                    if order.table:
                        table = order.table
                        table.is_occupied = True
                        table.save()
                    messages.success(request, f'Order {order.order_number} submitted successfully!')
                    return redirect('pos')
            else:
                messages.error(request, f'Invalid order data: {order_form.errors or "No items provided"}')
    return render(request, 'pos.html', {
        'tables': tables,
        'menu_items': menu_items,
        'inventory_items': inventory_items,
        'order_form': order_form,
        'order_item_form': order_item_form
    })

# ------------------- ORDERS -------------------
@login_required
def orders_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    prevailing_orders = Order.objects.filter(status__in=['Pending', 'Started']).order_by('-timestamp')
    history_orders = Order.objects.all().order_by('-timestamp')
    start_date = request.GET.get('start_date')
    period = request.GET.get('period', 'weekly')

    if request.method == 'POST':
        order_id = request.POST.get('order_id')
        action = request.POST.get('action')
        try:
            order = Order.objects.get(id=order_id)
            if action == 'start' and order.status == 'Pending':
                order.status = 'Started'
                order.start_time = timezone.now()
            elif action == 'ready' and order.status == 'Started':
                order.status = 'Ready'
                order.completed_at = timezone.now()
                if order.table:
                    order.table.is_occupied = False
                    order.table.save()
            elif action == 'cancel' and order.status in ['Pending', 'Started']:
                order.status = 'Canceled'
                order.completed_at = timezone.now()
                if order.table:
                    order.table.is_occupied = False
                    order.table.save()
            order.save()
            messages.success(request, f'Order {order.order_number} updated.')
        except Order.DoesNotExist:
            messages.error(request, 'Order not found.')
        return redirect('orders')

    if start_date:
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            end_date = start_date + timedelta(days=6 if period == 'weekly' else 30)
            history_orders = history_orders.filter(timestamp__range=[start_date, end_date])
        except ValueError:
            messages.error(request, 'Invalid date format.')

    return render(request, 'orders.html', {
        'prevailing_orders': prevailing_orders,
        'history_orders': history_orders,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
        'period': period,
    })

# ------------------- REQUISITIONS -------------------
@login_required
def requisitions_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    start_date = request.GET.get('start_date', timezone.now().strftime('%Y-%m-%d'))
    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
    except ValueError:
        start_date = timezone.now()
    end_date = start_date + timedelta(days=30)

    if request.user.has_perm('myapp.can_manage_requisitions'):
        requisitions = Requisition.objects.filter(created_at__range=[start_date, end_date])
    else:
        requisitions = Requisition.objects.filter(user=request.user, created_at__range=[start_date, end_date])

    grand_total = requisitions.aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')

    if request.method == 'POST':
        print("POST data:", dict(request.POST))  # Debug POST data
        form = RequisitionForm(request.POST)
        if form.is_valid():
            requisition = form.save(commit=False)
            requisition.user = request.user
            requisition.total_price = form.cleaned_data['quantity'] * form.cleaned_data['unit_price']
            requisition.save()
            messages.success(request, 'Requisition submitted.')
            return redirect('requisitions')
        else:
            messages.error(request, f'Invalid form data: {form.errors.as_text()}')
            print(f"Form errors: {form.errors.as_text()}")  # Debug form errors
    else:
        form = RequisitionForm()

    return render(request, 'requisitions.html', {
        'requisitions': requisitions,
        'form': form,
        'start_date': start_date.strftime('%Y-%m-%d'),
        'grand_total': grand_total,
        'is_manager': request.user.has_perm('myapp.can_manage_requisitions'),
    })

# ------------------- REQUISITION ACTION -------------------
@login_required
@require_POST
def requisition_action(request, requisition_id):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    try:
        requisition = Requisition.objects.get(id=requisition_id)
        if not request.user.has_perm('myapp.can_manage_requisitions'):
            messages.error(request, 'You do not have permission to perform this action.')
            return redirect('requisitions')

        action = request.POST.get('action')
        if action not in ['approve', 'reject']:
            messages.error(request, 'Invalid action.')
            return redirect('requisitions')

        if requisition.admin_approval == 'Pending':
            requisition.admin_approval = 'Approved' if action == 'approve' else 'Rejected'
        elif requisition.manager_approval == 'Pending' and request.user.is_manager:
            requisition.manager_approval = 'Approved' if action == 'approve' else 'Rejected'
        elif requisition.director_approval == 'Pending' and request.user.is_manager:
            requisition.director_approval = 'Approved' if action == 'approve' else 'Rejected'
        else:
            messages.error(request, 'No pending actions available.')
            return redirect('requisitions')

        requisition.save()
        messages.success(request, f'Requisition {requisition.id} {action}d.')
        return redirect('requisitions')
    except Requisition.DoesNotExist:
        messages.error(request, 'Requisition not found.')
        return redirect('requisitions')

# ------------------- REPORTS -------------------
@login_required
def reports_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    start_date = request.GET.get('start_date')
    report_type = request.GET.get('report_type', 'daily')
    orders = Order.objects.filter(status='Ready')

    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone()) if start_date else None
    except ValueError:
        start_date = None

    if start_date:
        if report_type == 'daily':
            end_date = start_date + timedelta(days=1)
        elif report_type == 'weekly':
            end_date = start_date + timedelta(days=7)
        else:
            end_date = start_date + timedelta(days=30)
        orders = orders.filter(timestamp__range=[start_date, end_date])

    total_sales = orders.aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')
    inventory_expense = InventoryHistory.objects.filter(
        change_type='Used',
        timestamp__range=[start_date, end_date] if start_date else [timezone.now() - timedelta(days=30), timezone.now()]
    ).aggregate(
        total=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField()))
    )['total'] or Decimal('0.00')
    net_profit = total_sales - inventory_expense
    total_orders = orders.count()

    popular_items = OrderItem.objects.filter(
        order__status='Ready',
        order__timestamp__range=[start_date, end_date] if start_date else [timezone.now() - timedelta(days=30), timezone.now()]
    ).values('menu_item__name').annotate(total_quantity=Sum('quantity')).order_by('-total_quantity')[:5]

    ingredient_usage = InventoryHistory.objects.filter(
        change_type='Used',
        timestamp__range=[start_date, end_date] if start_date else [timezone.now() - timedelta(days=30), timezone.now()]
    ).values('item__name').annotate(total_used=Sum('quantity')).order_by('-total_used')[:5]

    return render(request, 'reports.html', {
        'orders': orders,
        'total_sales': total_sales,
        'inventory_expense': inventory_expense,
        'net_profit': net_profit,
        'total_orders': total_orders,
        'popular_items': popular_items,
        'ingredient_usage': ingredient_usage,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
        'report_type': report_type,
    })

# ------------------- REPORTS DATA (JSON) -------------------
@login_required
def reports_data(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    report_type = request.GET.get('report_type', 'daily')
    start_date = request.GET.get('start_date')
    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone()) if start_date else timezone.now() - timedelta(days=30)
    except ValueError:
        start_date = timezone.now() - timedelta(days=30)

    if report_type == 'daily':
        days = 1
        end_date = start_date + timedelta(days=1)
    elif report_type == 'weekly':
        days = 7
        end_date = start_date + timedelta(days=7)
    else:
        days = 30
        end_date = start_date + timedelta(days=30)

    orders = Order.objects.filter(status='Ready', timestamp__range=[start_date, end_date])
    total_sales = orders.aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')
    inventory_expense = InventoryHistory.objects.filter(
        change_type='Used',
        timestamp__range=[start_date, end_date]
    ).aggregate(
        total=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField()))
    )['total'] or Decimal('0.00')
    net_profit = total_sales - inventory_expense
    total_orders = orders.count()

    revenue_dates = [(start_date + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
    daily_sales = []
    daily_expenses = []
    for i in range(days):
        day_start = start_date + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_sales = orders.filter(timestamp__range=[day_start, day_end]).aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')
        day_expenses = InventoryHistory.objects.filter(
            change_type='Used',
            timestamp__range=[day_start, day_end]
        ).aggregate(
            total=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField()))
        )['total'] or Decimal('0.00')
        daily_sales.append(float(day_sales))
        daily_expenses.append(float(day_expenses))

    data = {
        'orders': [
            {
                'order_number': order.order_number,
                'customer': order.customer or '',
                'table__name': order.table.name if order.table else '',
                'status': order.status,
                'timestamp': order.timestamp.astimezone(pytz.timezone('Africa/Nairobi')).strftime('%Y-%m-%d %H:%M:%S'),
                'total_price': float(order.total_price),
                'time_taken': order.time_taken() if hasattr(order, 'time_taken') and callable(getattr(order, 'time_taken')) else None
            } for order in orders
        ],
        'total_sales': float(total_sales or 0),
        'inventory_expense': float(inventory_expense or 0),
        'net_profit': float(net_profit or 0),
        'total_orders': int(total_orders or 0),
        'revenue_dates': revenue_dates,
        'daily_sales': daily_sales,
        'daily_expenses': daily_expenses,
    }
    return JsonResponse(data, safe=False)

# ------------------- GENERATE PDF REPORT -------------------
@login_required
def generate_pdf_report(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    start_date = request.GET.get('start_date')
    report_type = request.GET.get('report_type', 'daily')
    orders = Order.objects.filter(status='Ready')

    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone()) if start_date else None
    except ValueError:
        start_date = None

    if start_date:
        if report_type == 'daily':
            days = 1
            end_date = start_date + timedelta(days=1)
        elif report_type == 'weekly':
            days = 7
            end_date = start_date + timedelta(days=7)
        else:
            days = 30
            end_date = start_date + timedelta(days=30)
        orders = orders.filter(timestamp__range=[start_date, end_date])
    else:
        days = 30
        start_date = timezone.now() - timedelta(days=30)
        end_date = timezone.now()
        orders = orders.filter(timestamp__range=[start_date, end_date])

    total_sales = orders.aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')
    inventory_expense = InventoryHistory.objects.filter(
        change_type='Used',
        timestamp__range=[start_date, end_date]
    ).aggregate(
        total=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField()))
    )['total'] or Decimal('0.00')
    net_profit = total_sales - inventory_expense
    total_orders = orders.count()

    revenue_dates = [(start_date + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
    daily_sales = []
    daily_expenses = []
    for i in range(days):
        day_start = start_date + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_sales = orders.filter(timestamp__range=[day_start, day_end]).aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')
        day_expenses = InventoryHistory.objects.filter(
            change_type='Used',
            timestamp__range=[day_start, day_end]
        ).aggregate(
            total=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField()))
        )['total'] or Decimal('0.00')
        daily_sales.append(float(day_sales))
        daily_expenses.append(float(day_expenses))

    response = HttpResponse(content_type='application/pdf')
    filename = f'report_{start_date.strftime("%Y%m%d") if start_date else "latest"}_{report_type}.pdf'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    doc = SimpleDocTemplate(response, pagesize=letter)
    story = []

    styles = getSampleStyleSheet()
    title = Paragraph("Residence256 Hotel Financial Report", styles['Title'])
    story.append(title)

    data = [
        ['Metric', 'Value (UGX)'],
        ['Total Sales', f'{total_sales:,.2f}'],
        ['Inventory Expense', f'{inventory_expense:,.2f}'],
        ['Net Profit', f'{net_profit:,.2f}'],
        ['Total Orders', str(total_orders)],
    ]
    table = ReportLabTable(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 14),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(table)

    story.append(Paragraph("<br/><br/>Revenue vs Expenses", styles['Heading2']))
    drawing = Drawing(400, 200)
    lp = LinePlot()
    lp.x = 50
    lp.y = 50
    lp.height = 125
    lp.width = 300
    lp.data = [
        list(zip(range(len(revenue_dates)), daily_sales)),
        list(zip(range(len(revenue_dates)), daily_expenses))
    ]
    lp.lines[0].strokeColor = colors.blue
    lp.lines[1].strokeColor = colors.red
    lp.xValueAxis.valueMin = 0
    lp.xValueAxis.valueMax = len(revenue_dates) - 1
    lp.xValueAxis.valueSteps = list(range(len(revenue_dates)))
    lp.xValueAxis.labels.angle = 45
    lp.xValueAxis.labels.boxAnchor = 'ne'
    lp.xValueAxis.labels.fontSize = 8
    lp.xValueAxis.labelTextFormat = lambda x: revenue_dates[int(x)] if 0 <= int(x) < len(revenue_dates) else ''
    lp.yValueAxis.valueMin = 0
    lp.yValueAxis.valueMax = max(max(daily_sales, default=0), max(daily_expenses, default=0)) * 1.2 or 1000
    lp.yValueAxis.labelTextFormat = lambda x: f'{x:,.0f}'
    drawing.add(lp)
    legend = Legend()
    legend.x = 350
    legend.y = 150
    legend.fontSize = 8
    legend.alignment = 'right'
    legend.colorNamePairs = [(colors.blue, 'Revenue'), (colors.red, 'Expenses')]
    drawing.add(legend)
    story.append(drawing)

    story.append(Paragraph("<br/><br/>Order Details", styles['Heading2']))
    order_data = [['Order Number', 'Customer', 'Table', 'Status', 'Timestamp', 'Total Price (UGX)']]
    for order in orders:
        order_data.append([
            order.order_number,
            order.customer or '-',
            order.table.name if order.table else '-',
            order.status,
            order.timestamp.astimezone(pytz.timezone('Africa/Nairobi')).strftime('%Y-%m-%d %H:%M:%S'),
            f'{order.total_price:,.2f}'
        ])
    order_table = ReportLabTable(order_data)
    order_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(order_table)

    doc.build(story)
    return response

# ------------------- TABLE STATUS UPDATE (AJAX) -------------------
@login_required
@require_POST
def update_table_status(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    table_id = request.POST.get('table_id')
    is_occupied = request.POST.get('is_occupied') == 'true'

    try:
        table = Table.objects.get(id=table_id)
        table.is_occupied = is_occupied
        table.save()
        return JsonResponse({'status': 'success', 'table_id': table_id, 'is_occupied': table.is_occupied})
    except Table.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Table not found'})

# ------------------- UPDATE MENU ITEM (AJAX) -------------------
@login_required
@require_POST
def update_menu_item(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    try:
        menu_item_id = request.POST.get('menu_item_id')
        name = request.POST.get('name')
        category = request.POST.get('category')
        price = request.POST.get('price')

        menu_item = MenuItem.objects.get(id=menu_item_id)
        menu_item.name = name
        menu_item.category = category
        menu_item.price = Decimal(price) if price else (menu_item.recipe.selling_price if menu_item.recipe else Decimal('0.00'))
        menu_item.save()

        return JsonResponse({'status': 'success', 'message': f'Menu item "{menu_item.name}" updated.'})
    except MenuItem.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Menu item not found.'})
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Invalid price.'})

# ------------------- DELETE MENU ITEM (AJAX) -------------------
@login_required
@require_POST
def delete_menu_item(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    try:
        menu_item_id = request.POST.get('menu_item_id')
        menu_item = MenuItem.objects.get(id=menu_item_id)
        name = menu_item.name
        menu_item.delete()
        return JsonResponse({'status': 'success', 'message': f'Menu item "{name}" deleted.'})
    except MenuItem.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Menu item not found.'})

# ------------------- GET INVENTORY ITEMS (AJAX) -------------------
@login_required
def get_inventory_items(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    inventory_items = [
        {
            'id': item.id,
            'name': item.name,
            'quantity': float(item.quantity),
            'units': item.units,
            'unit_price': float(item.unit_price)
        }
        for item in InventoryItem.objects.all().order_by('name')
    ]
    return JsonResponse(inventory_items, safe=False)
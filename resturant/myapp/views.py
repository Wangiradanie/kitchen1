from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import datetime, timedelta
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse
from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField
from django.db import transaction
from django.db.models.functions import Coalesce
from .models import (
    InventoryItem, DTable, MenuItem, Order, OrderItem, Requisition,
    InventoryHistory, MenuItemIngredient, Recipe, RecipeIngredient,
)
from .forms import (
    InventoryItemForm, UseItemForm, OrderForm, OrderItemForm,
    RecipeForm,RequisitionItemForm
)

import json
from decimal import Decimal
import pytz
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate as Paragraph
from reportlab.lib.units import inch
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend
import pandas as pd  # NEW: for advanced reports

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from reportlab.platypus import Spacer
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import TableStyle 
from .models import Requisition, RequisitionHistory, RequisitionItem
from reportlab.lib import colors
from reportlab.platypus import Table as ReportLabTable
from django.db.models.functions import ExtractMonth, ExtractYear, TruncMonth
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, TableStyle, Paragraph, Spacer






# ------------------- POS (100% FIXED: Saves items + total) -------------------
@login_required
def pos_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    tables = DTable.objects.all()

    try:
        menu_items = MenuItem.objects.select_related('recipe').all()
    except Exception as e:
        messages.error(request, f'Error fetching menu items: {str(e)}')
        menu_items = []

    inventory_items = InventoryItem.objects.all()
    order_form = OrderForm()
    order_item_form = OrderItemForm()

    if request.method == 'POST' and 'submit-order' in request.POST:
        order_form = OrderForm(request.POST)
        try:
            items_data = json.loads(request.POST.get('order_items', '[]'))
        except Exception:
            items_data = []

        # Quick debug logging (leave/remove as desired)
        print(f"Order form data: {request.POST}")
        print(f"Order items data: {items_data}")

        # Validate form + items present
        if not order_form.is_valid():
            messages.error(request, f'Invalid order form: {order_form.errors.as_text()}')
            return redirect('pos')
        if not items_data:
            messages.error(request, 'No items provided.')
            return redirect('pos')

        # === PHASE 1: VALIDATE ITEMS & INVENTORY (ROBUST NAME + ID LOOKUP) ===
        validated_items = []
        try:
            for it in items_data:
                quantity = int(it.get('quantity', 0))
                if quantity <= 0:
                    raise ValueError(f"Quantity must be greater than 0, got {quantity}")

                menu_item = None

                # 1. Try by ID
                menu_id = it.get('id') or it.get('menu_item_id')
                if menu_id:
                    menu_item = MenuItem.objects.filter(pk=menu_id).first()

                # 2. Try by name (case-insensitive, strip whitespace)
                if not menu_item:
                    name = it.get('name', '').strip()
                    if name:
                        menu_item = MenuItem.objects.filter(name__iexact=name).first()

                if not menu_item:
                    raise ValueError(f"Menu item not found: '{it.get('name') or it.get('id')}'")

                # 3. Validate inventory: Recipe ingredients
                if menu_item.recipe:
                    for ing in menu_item.recipe.ingredients.all():
                        needed = ing.quantity * Decimal(quantity)
                        if ing.inventory_item.quantity < needed:
                            raise ValueError(
                                f"Not enough {ing.inventory_item.name}: "
                                f"{needed} needed, only {ing.inventory_item.quantity} available"
                            )

                # 4. Validate inventory: Direct MenuItem ingredients
                for ing in menu_item.menuitemingredient_set.all():
                    needed = ing.quantity_needed * Decimal(quantity)
                    if ing.inventory_item.quantity < needed:
                        raise ValueError(
                            f"Not enough {ing.inventory_item.name}: "
                            f"{needed} needed, only {ing.inventory_item.quantity} available"
                        )

                validated_items.append((menu_item, quantity))

        except Exception as e:
            messages.error(request, f"Order validation failed: {str(e)}")
            print(f"[ORDER VALIDATION ERROR] {e}")
            return redirect('pos')

        # === PHASE 2: SAVE ORDER & DEDUCT INVENTORY ===
        try:
            with transaction.atomic():
                order = order_form.save(commit=False)
                order.status = 'Pending'
                order.total_price = Decimal('0.00')
                order.save()  # Save to get order.id

                # Create OrderItems (total_price calculated in OrderItem.save())
                for menu_item, quantity in validated_items:
                    OrderItem.objects.create(
                        order=order,
                        menu_item=menu_item,
                        quantity=quantity
                    )

                # Recompute total from OrderItems (signal will also do this)
                total = OrderItem.objects.filter(order=order).aggregate(
                    t=Sum('total_price')
                )['t'] or Decimal('0.00')
                order.total_price = total
                order.save(update_fields=['total_price'])

                # Deduct inventory
                for menu_item, quantity in validated_items:
                    # Recipe ingredients
                    if menu_item.recipe:
                        for ing in menu_item.recipe.ingredients.all():
                            needed = ing.quantity * Decimal(quantity)
                            inv = ing.inventory_item
                            inv.quantity -= needed
                            inv.save()
                            InventoryHistory.objects.create(
                                item=inv,
                                units=inv.units,
                                quantity=needed,
                                unit_price=inv.unit_price,
                                reason=f'Used for {menu_item.name} in order {order.order_number}',
                                change_type='Used'
                            )
                    # Direct ingredients
                    for ing in menu_item.menuitemingredient_set.all():
                        needed = ing.quantity_needed * Decimal(quantity)
                        inv = ing.inventory_item
                        inv.quantity -= needed
                        inv.save()
                        InventoryHistory.objects.create(
                            item=inv,
                            units=inv.units,
                            quantity=needed,
                            unit_price=inv.unit_price,
                            reason=f'Used for {menu_item.name} in order {order.order_number}',
                            change_type='Used'
                        )

                # Mark table as occupied
                if order.table:
                    order.table.is_occupied = True
                    order.table.save()

            messages.success(request, f'Order {order.order_number} submitted successfully!')
            return redirect('pos')

        except Exception as e:
            messages.error(request, f'Error saving order: {str(e)}')
            print(f"[ORDER SAVE ERROR] {e}")
            return redirect('pos')

    return render(request, 'pos.html', {
        'tables': tables,
        'menu_items': menu_items,
        'inventory_items': inventory_items,
        'order_form': order_form,
        'order_item_form': order_item_form
    })


# ------------------- INVENTORY -------------------
# ------------------- INVENTORY (RESTOCK + PRICE UPDATE) -------------------
@login_required
def inventory_view(request):
    

    timezone.activate(pytz.timezone('Africa/Nairobi'))
    items = InventoryItem.objects.all().order_by('name')
    total_cost = items.aggregate(
        total=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField()))
    )['total'] or Decimal('0.00')

    form = InventoryItemForm()

    # === EXPORT CURRENT STOCK ===
    export = request.GET.get('export')
    if export in ['csv', 'excel']:
        data = []
        for i in items:
            data.append({
                'Name': i.name,
                'Quantity': float(i.quantity),
                'Units': i.units,
                'Unit Price': float(i.unit_price),
                'Total Value': float(i.quantity * i.unit_price)
            })
        df = pd.DataFrame(data)

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            if export == 'excel' else 'text/csv'
        )
        filename = f"inventory_current.{export}"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        if export == 'excel':
            with pd.ExcelWriter(response, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
        else:
            df.to_csv(response, index=False)
        return response

    # === HISTORY DATA (for History Tab) ===
    history = InventoryHistory.objects.select_related('item').all().order_by('-timestamp')
    start = request.GET.get('start_date')
    end = request.GET.get('end_date')
    item_id = request.GET.get('item')

    if start:
        history = history.filter(timestamp__date__gte=start)
    if end:
        history = history.filter(timestamp__date__lte=end)
    if item_id:
        history = history.filter(item_id=item_id)

    hist_data = []
    for h in history:
        hist_data.append({
            'Date': h.timestamp.strftime('%Y-%m-%d %H:%M'),
            'Item': h.item.name,
            'Qty': float(h.quantity),
            'Units': h.units,
            'Price': float(h.unit_price),
            'Value': float(h.quantity * h.unit_price),
            'Type': h.change_type,
            'Reason': h.reason or 'Manual'
        })
    expected_cols = ['Date', 'Item', 'Qty', 'Units', 'Price', 'Value', 'Type', 'Reason']
    if hist_data:
         hist_df = pd.DataFrame(hist_data)[expected_cols]
    else:
         hist_df = pd.DataFrame(columns=expected_cols)
    # hist_df = pd.DataFrame(hist_data)
    # hist_df = hist_df[['Date', 'Item', 'Qty', 'Units', 'Price', 'Value', 'Type', 'Reason']]

    # === FORM HANDLING ===
    if request.method == 'POST':
        if 'add-new' in request.POST:
            form = InventoryItemForm(request.POST)
            if form.is_valid():
                name = form.cleaned_data['name'].strip()
                if InventoryItem.objects.filter(name__iexact=name).exists():
                    messages.error(request, f'Item "{name}" already exists. Use Restock.')
                else:
                    item = form.save()
                    InventoryHistory.objects.create(
                        item=item, units=item.units, quantity=item.quantity,
                        unit_price=item.unit_price, reason='New item added', change_type='Added'
                    )
                    messages.success(request, f'New item "{item.name}" added.')
                return redirect('inventory')

        elif 'restock-item' in request.POST:
            item_id = request.POST.get('restock-item')
            try:
                item = InventoryItem.objects.get(id=item_id)
                old_qty = item.quantity
                old_price = item.unit_price

                item.quantity = Decimal(request.POST.get('quantity'))
                item.unit_price = Decimal(request.POST.get('unit_price'))
                item.save()

                change_qty = abs(item.quantity - old_qty)
                change_type = 'Added' if item.quantity > old_qty else 'Adjusted'
                InventoryHistory.objects.create(
                    item=item, units=item.units, quantity=change_qty,
                    unit_price=item.unit_price, reason='Restock', change_type=change_type
                )
                messages.success(request, f'{item.name} restocked.')
            except Exception as e:
                messages.error(request, f'Error: {str(e)}')
            return redirect('inventory')

    return render(request, 'inventory.html', {
        'items': items, 'total_cost': total_cost, 'form': form,
        'history_html': hist_df.to_html(classes='table table-sm table-bordered', index=False),
        'start': start, 'end': end, 'item_id': item_id
    })
# ------------------- GET INVENTORY ITEM (AJAX) -------------------
@login_required
def get_inventory_item(request, item_id):
    try:
        item = InventoryItem.objects.get(id=item_id)
        return JsonResponse({
            'name': item.name,
            'quantity': float(item.quantity),
            'unit_price': float(item.unit_price),
            'units': item.units
        })
    except InventoryItem.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)


# ------------------- INVENTORY HISTORY -------------------
@login_required
def inventory_history_view(request):
    import pandas as pd
    from django.http import HttpResponse

    history = InventoryHistory.objects.select_related('item').all().order_by('-timestamp')
    items = InventoryItem.objects.all()

    start = request.GET.get('start_date')
    end = request.GET.get('end_date')
    item_id = request.GET.get('item')
    export = request.GET.get('export')

    if start:
        history = history.filter(timestamp__date__gte=start)
    if end:
        history = history.filter(timestamp__date__lte=end)
    if item_id:
        history = history.filter(item_id=item_id)

    # === PANDAS TABLE ===
    data = []
    for h in history:
        data.append({
            'Date': h.timestamp.strftime('%Y-%m-%d %H:%M'),
            'Item': h.item.name,
            'Qty': float(h.quantity),
            'Units': h.units,
            'Price': float(h.unit_price),
            'Value': float(h.quantity * h.unit_price),
            'Type': h.change_type,
            'Reason': h.reason or 'Manual'
        })
    df = pd.DataFrame(data)
    df = df[['Date', 'Item', 'Qty', 'Units', 'Price', 'Value', 'Type', 'Reason']]

    if export in ['csv', 'excel']:
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            if export == 'excel' else 'text/csv'
        )
        response['Content-Disposition'] = f'attachment; filename="inventory_history.{export}"'
        with pd.ExcelWriter(response, engine='openpyxl') if export == 'excel' else open(response, 'w') as f:
            if export == 'excel':
                df.to_excel(f, index=False)
            else:
                df.to_csv(f, index=False)
        return response

    return render(request, 'inventory_history.html', {
        'history_html': df.to_html(classes='table table-sm table-bordered', index=False),
        'items': items,
        'start': start, 'end': end, 'item_id': item_id
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
            form = RecipeForm(request.POST)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        recipe = form.save(commit=False)
                        recipe.total_cost = Decimal('0.00')
                        recipe.selling_price = Decimal('0.00')
                        recipe.save()
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
            else:
                messages.error(request, f'Invalid recipe form data: {form.errors.as_text()}')
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


# ------------------- ORDERS VIEW -------------------
@login_required
def orders_view(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    prevailing_orders = Order.objects.filter(status__in=['Pending', 'Started']).prefetch_related('items__menu_item').order_by('-timestamp')
    history_orders = Order.objects.prefetch_related('items__menu_item').all().order_by('-timestamp')
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
    draft = None
    draft_items = []

    # Find active draft: not submitted, not archived, belongs to user
    active_draft = Requisition.objects.filter(
        user=request.user,
        is_archived=False
    ).exclude(
        history__action='submit'
    ).first()

    if active_draft:
        draft = active_draft
        draft_items = draft.items.all()
        request.session['requisition_draft'] = draft.id
    else:
        # Only trust session if the draft hasn't been submitted
        draft_id = request.session.get('requisition_draft')
        if draft_id:
            try:
                sess_draft = Requisition.objects.get(
                    id=draft_id,
                    user=request.user,
                    is_archived=False
                )
                if not sess_draft.history.filter(action='submit').exists():
                    draft = sess_draft
                    draft_items = draft.items.all()
                else:
                    # Submitted — clear session
                    request.session.pop('requisition_draft', None)
            except Requisition.DoesNotExist:
                request.session.pop('requisition_draft', None)
    # draft = None
    # draft_items = []
    # existing_draft = Requisition.objects.filter(
    #     user=request.user,
    #     operations_manager_approval='Pending',
    #     finance_approval='Pending',
    #     director_approval='Pending',
    #     is_archived=False
    # ).first()

    # if existing_draft:
    #     draft = existing_draft
    #     draft_items = draft.items.all()
    #     request.session['requisition_draft'] = draft.id

    # else:
    #     # fallback: if no "existing_draft" by approval flags, try session-stored draft id
    #     draft_id = request.session.get('requisition_draft')
    #     if draft_id:
    #         sess_draft = Requisition.objects.filter(id=draft_id, user=request.user, is_archived=False).first()
    #         if sess_draft and not sess_draft.history.filter(action='submit').exists():
    #             draft = sess_draft
    #             draft_items = draft.items.all()
    

    if request.method == 'POST' and 'add-item' in request.POST:
        if draft:
            messages.error(request, "Finish current draft first.")
            return redirect('requisitions')

        form = RequisitionItemForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    draft = Requisition.objects.create(user=request.user)
                    request.session['requisition_draft'] = draft.id
                    item = form.save(commit=False)
                    item.requisition = draft
                    item.save()
                    draft.total_price = draft.items.aggregate(total=Sum('total_price'))['total'] or 0
                    draft.save(update_fields=['total_price'])
            except Exception:
                messages.error(request, "Failed to create draft.")
                return redirect('requisitions')
            messages.success(request, f"Draft {draft.requisition_number} created.")
            return redirect('requisitions')
        else:
            messages.error(request, "Invalid item.")
    else:
        form = RequisitionItemForm()

    # if request.method == 'POST' and 'submit' in request.POST:
    #     if draft and draft.items.exists():
    #         request.session.pop('requisition_draft', None)
    #         RequisitionHistory.objects.create(
    #             requisition=draft, user=request.user, action='submit'
    #         )
    #         messages.success(request, f"{draft.requisition_number} submitted.")
    #     else:
    #         messages.error(request, "Add items first.")
    #     return redirect('requisitions')

    pending = Requisition.objects.filter(is_archived=False).prefetch_related('history__user', 'items')
    approved = Requisition.objects.filter(
        is_archived=True,
        operations_manager_approval='Approved',
        finance_approval='Approved',
        director_approval='Approved'
    ).prefetch_related('history__user', 'items')
    rejected = Requisition.objects.filter(is_archived=True).exclude(
        operations_manager_approval='Approved',
        finance_approval='Approved',
        director_approval='Approved'
    ).prefetch_related('history__user', 'items')

    context = {
        'form': form,
        'draft': draft,
        'draft_items': draft_items,
        'pending_requisitions': pending,
        'approved_requisitions': approved,
        'rejected_requisitions': rejected,
        'approval_fields': {
            'operations_manager_approval': 'Ops Mgr',
            'finance_approval': 'Finance',
            'director_approval': 'Director'
        }
    }
    return render(request, 'requisitions.html', context)


#
# @login_required
# @require_POST
# def requisition_add_item(request):
#     form = RequisitionItemForm(request.POST)
#     if form.is_valid():
#         draft_id = request.session.get('requisition_draft')
#         if not draft_id:
#             draft = Requisition.objects.create(user=request.user)
#             request.session['requisition_draft'] = draft.id
#         else:
#             draft = Requisition.objects.get(id=draft_id)
#         item = form.save(commit=False)
#         item.requisition = draft
#         item.save()
#         draft.total_price = sum(i.total_price for i in draft.items.all())
#         draft.save()
#     return redirect('requisitions')
# 
@login_required
@require_POST
def requisition_add_item(request):
    form = RequisitionItemForm(request.POST)

    if form.is_valid():
        try:
            with transaction.atomic():
                draft_id = request.session.get('requisition_draft')
                if draft_id:
                    draft = Requisition.objects.filter(id=draft_id, user=request.user, is_archived=False).first()
                else:
                    draft = None

                if not draft:
                    draft = Requisition.objects.create(user=request.user)
                    request.session['requisition_draft'] = draft.id
                    request.session.modified = True

                item = form.save(commit=False)
                item.requisition = draft
                item.save()

                draft.total_price = draft.items.aggregate(total=Sum('total_price'))['total'] or 0
                draft.save(update_fields=['total_price'])
        except Exception as e:
            print("[REQUISITION SAVE ERROR]", e)
            messages.error(request, "Failed to add item (server error).")
            return redirect('requisitions')
        messages.success(request, "Item added to requisition.")
        return redirect('requisitions')
    else:
        # debug output — will appear in runserver console
        print("[REQUISITION FORM ERRORS]", form.errors.as_json())
        # surface readable errors to user
        err_text = "; ".join(f"{f}: {', '.join(errs)}" for f, errs in form.errors.items()) if form.errors else "Invalid input."
        messages.error(request, f"Failed to add item: {err_text}")
        return redirect('requisitions')


# @login_required
# @require_POST
# def requisition_add_item(request):
#     form = RequisitionItemForm(request.POST)
#     if form.is_valid():
#         # Get or create draft
#         draft_id = request.session.get('requisition_draft')
#         if draft_id:
#             draft = Requisition.objects.get(id=draft_id)
#         else:
#             draft = Requisition.objects.create(user=request.user)
#             request.session['requisition_draft'] = draft.id
#             request.session.modified = True  # Ensure session saves

#         # Save item
#         item = form.save(commit=False)
#         item.requisition = draft
#         item.save()

#         # Update total
#         draft.total_price = sum(i.total_price for i in draft.items.all())
#         draft.save()

#     else:
#         messages.error(request, "Failed to add item. Please check the form.")

#     return redirect('requisitions')


# @login_required
# @require_POST
# def requisition_submit(request):
#     draft_id = request.session.get('requisition_draft')
#     if draft_id:
#         try:
#             draft = Requisition.objects.get(id=draft_id, user=request.user)
#             if draft.items.exists():
#                 del request.session['requisition_draft']
#                 messages.success(request, f'Requisition {draft.requisition_number} submitted.')
#             else:
#                 messages.error(request, 'No items.')
#         except:
#             pass
#     return redirect('requisitions')

# @login_required
# @require_POST
# def requisition_submit(request):
#     draft_id = request.session.get('requisition_draft')
#     if not draft_id:
#         messages.error(request, "No draft to submit.")
#         return redirect('requisitions')

#     try:
#         with transaction.atomic():
#             draft = Requisition.objects.select_for_update().get(id=draft_id, user=request.user, is_archived=False)
#             if not draft.items.exists():
#                 messages.error(request, "Add items first.")
#                 return redirect('requisitions')

#             # record submission and optionally mark archived
#             RequisitionHistory.objects.create(requisition=draft, user=request.user, action='submit')
#             draft.is_archived = True   # optional: if you want submitted requisitions moved out of active list
#             draft.save(update_fields=['is_archived'])

#             request.session.pop('requisition_draft', None)
#             messages.success(request, f"Requisition {draft.requisition_number} submitted.")
#     except Requisition.DoesNotExist:
#         messages.error(request, "Draft not found or already submitted.")
#     except Exception as e:
#         print("[REQUISITION SUBMIT ERROR]", e)
#         messages.error(request, "Error submitting requisition.")
#     return redirect('requisitions')

@login_required
@require_POST
def requisition_submit(request):
    draft_id = request.session.get('requisition_draft')
    if not draft_id:
        messages.error(request, "No draft to submit.")
        return redirect('requisitions')

    try:
        with transaction.atomic():
            draft = Requisition.objects.select_for_update().get(id=draft_id, user=request.user, is_archived=False)
            if not draft.items.exists():
                messages.error(request, "Add items first.")
                return redirect('requisitions')

            # record submission (do NOT archive here so requisition moves to pending approvals)
            RequisitionHistory.objects.create(requisition=draft, user=request.user, action='submit')

            # clear session so UI shows new empty draft area
            request.session.pop('requisition_draft', None)
            messages.success(request, f"Requisition {draft.requisition_number} submitted.")
    except Requisition.DoesNotExist:
        messages.error(request, "Draft not found or already submitted.")
    except Exception as e:
        print("[REQUISITION SUBMIT ERROR]", e)
        messages.error(request, "Error submitting requisition.")
    return redirect('requisitions')



@login_required
@require_POST
def requisition_action(request, requisition_id):
    user = request.user
    field = request.POST.get('field')
    action = request.POST.get('action')

    try:
        req = Requisition.objects.get(id=requisition_id)
    except Requisition.DoesNotExist:
        messages.error(request, 'Requisition not found.')
        return redirect('requisitions')

    if not user.has_approval_right(field):
        messages.error(request, f"You cannot {action} as {field.replace('_', ' ').title()}.")
        return redirect('requisitions')

    field_map = {
        'operations_manager': 'operations_manager_approval',
        'finance': 'finance_approval',
        'director': 'director_approval',
    }
    status_field = field_map[field]
    current = getattr(req, status_field)

    if current != 'Pending':
        messages.warning(request, f"Already {current.lower()}.")
        return redirect('requisitions')

    new_status = 'Approved' if action == 'approve' else 'Rejected'
    setattr(req, status_field, new_status)
    req.save(update_fields=[status_field])

    RequisitionHistory.objects.create(
        requisition=req, user=user, action=action, field=field
    )

    if req.overall_status() == 'Fully Approved':
        req.is_archived = True
        req.save(update_fields=['is_archived'])
        messages.success(request, f"{req.requisition_number} archived.")
    else:
        messages.success(request, f"{field.replace('_', ' ').title()}: {action}d.")

    return redirect('requisitions')

@login_required
def requisition_pdf(request, requisition_id):
    req = get_object_or_404(Requisition, id=requisition_id)

    buffer = BytesIO()
    
    # Create the PDF object
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=18)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph(f"<font size=18>Requisition {req.requisition_number}</font>", styles["Title"]))
    elements.append(Spacer(1, 12))

    # Info
    elements.append(Paragraph(f"<b>User:</b> {req.user}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Created:</b> {req.created_at.strftime('%B %d, %Y %I:%M %p')}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Total:</b> UGX {req.total_price:,}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # Items Table
    data = [['Item', 'Qty', 'Unit Price', 'Total']]
    for item in req.items.all():
        data.append([
            item.item_name,
            str(item.quantity),
            f"UGX {item.unit_price:,}",
            f"UGX {item.total_price:,}"
        ])

    table = ReportLabTable(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(table)

    # Build PDF
    doc.build(elements)

    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="requisition_{req.requisition_number}.pdf"'
    return response
# ------------------- TABLE STATUS UPDATE (AJAX) -------------------
@login_required
@require_POST
def update_table_status(request):
    timezone.activate(pytz.timezone('Africa/Nairobi'))
    table_id = request.POST.get('table_id')
    is_occupied = request.POST.get('is_occupied') == 'true'

    try:
        table = DTable.objects.get(id=table_id)
        table.is_occupied = is_occupied
        table.save()
        return JsonResponse({'status': 'success', 'table_id': table_id, 'is_occupied': table.is_occupied})
    except DTable.DoesNotExist:
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



@login_required
def dashboard_view(request):
    today = datetime.today()
    current_year = today.year
    current_month = today.month
    current_month_name = today.strftime('%B')

    # ---------- CURRENT MONTH ----------
    current_orders = Order.objects.filter(
        status='Ready',
        timestamp__year=current_year,
        timestamp__month=current_month
    ).count()

    current_revenue = Order.objects.filter(
        status='Ready',
        timestamp__year=current_year,
        timestamp__month=current_month
    ).aggregate(total=Sum('total_price'))['total'] or Decimal('0.00')

    current_expense = InventoryHistory.objects.filter(
        change_type='Added',
        # reason__contains='order',
        timestamp__year=current_year,
        timestamp__month=current_month
    ).aggregate(
        total=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField()))
    )['total'] or Decimal('0.00')

    current_profit = current_revenue - current_expense

    # ---------- LAST 3 YEARS ----------
    years = [current_year - 2, current_year - 1, current_year]

    revenue_data = {y: [0.0] * 12 for y in years}
    orders_data  = {y: [0]   * 12 for y in years}
    expense_data = {y: [0.0] * 12 for y in years}

    # Revenue & Orders
    sales = (
        Order.objects.filter(status='Ready', timestamp__year__in=years)
        .annotate(year=ExtractYear('timestamp'), month=ExtractMonth('timestamp'))
        .values('year', 'month')
        .annotate(revenue=Sum('total_price'), count=Count('id'))
        .order_by('year', 'month')
    )
    for s in sales:
        y, m = s['year'], s['month'] - 1
        revenue_data[y][m] = float(s['revenue'] or 0)
        orders_data[y][m]  = s['count']

    # Expense
    expenses = (
        InventoryHistory.objects.filter(
            change_type='Added',
            # reason__contains='order',
            timestamp__year__in=years
        )
        .annotate(year=ExtractYear('timestamp'), month=ExtractMonth('timestamp'))
        .values('year', 'month')
        .annotate(cost=Sum(ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField())))
        .order_by('year', 'month')
    )
    for e in expenses:
        y, m = e['year'], e['month'] - 1
        expense_data[y][m] = float(e['cost'] or 0)

    # ---------- TOP 5 MENU ITEMS (current year) ----------
    top_items = (
        OrderItem.objects.filter(
            order__status='Ready',
            order__timestamp__year=current_year
        )
        .values('menu_item__name')
        .annotate(total_qty=Sum('quantity'), total_sales=Sum('total_price'))
        .order_by('-total_sales')[:5]
    )
    top_items_list = [
        {
            'name': i['menu_item__name'],
            'quantity': i['total_qty'],
            'sales': float(i['total_sales'] or 0)
        }
        for i in top_items
    ]

    # ---------- CONTEXT ----------
    context = {
        'current_year': current_year,
        'current_month': current_month_name,
        'current_revenue': current_revenue,
        'current_orders': current_orders,
        'current_expense': current_expense,
        'current_profit': current_profit,
        'years': json.dumps(years),
        'revenue_data': json.dumps(revenue_data),
        'orders_data': json.dumps(orders_data),
        'expense_data': json.dumps(expense_data),
        'top_items': top_items_list,
    }

    return render(request, 'dashboard.html', context)
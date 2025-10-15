# D:\python\kitchen\project2\mysite\myapp\management\commands\seed_data.py
from django.core.management.base import BaseCommand
from myapp.models import InventoryItem, Table, MenuItem

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        InventoryItem.objects.bulk_create([
            InventoryItem(name="Rice", quantity=50),
            InventoryItem(name="Beef", quantity=20),
            InventoryItem(name="Matoke", quantity=30),
            InventoryItem(name="Vegetables", quantity=15),
            InventoryItem(name="Fish", quantity=0),
        ])
        Table.objects.bulk_create([
            Table(name="Table 1", is_occupied=True),
            Table(name="Table 2"),
            Table(name="Table 3"),
            Table(name="Table 4", is_occupied=True),
            Table(name="Table 5"),
            Table(name="Table 6"),
            Table(name="Table 7"),
            Table(name="Table 8", is_occupied=True),
            Table(name="Table 9"),
            Table(name="Table 10"),
            Table(name="Room 101", is_occupied=True),
            Table(name="Room 102"),
            Table(name="Room 103"),
            Table(name="Room 104", is_occupied=True),
            Table(name="Room 105"),
        ])
        MenuItem.objects.bulk_create([
            MenuItem(name="Vegetable Samosas", price=5000, category="Starters"),
            MenuItem(name="Matoke with Beef", price=15000, category="Main Course"),
            MenuItem(name="Mango Sorbet", price=6000, category="Desserts"),
        ])
        self.stdout.write(self.style.SUCCESS('Data seeded successfully!'))
#!/usr/bin/env bash
set -o errexit  # Exit on any error
pip install --upgrade pip  # Optional: Update pip
pip install -r requirements.txt  # Install dependencies from requirements.txt
python manage.py collectstatic --no-input  # Collect static files for Django
python manage.py migrate  # Apply database migrations
#!/usr/bin/env bash
# Render build step: install, collect static files, and apply migrations to the production database (Neon).
set -o errexit
pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

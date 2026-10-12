#!/bin/sh
# Start serwisu WWW: migracje bazy, potem gunicorn
set -e
python manage.py migrate --noinput
exec gunicorn sprawdzarka.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120 --access-logfile -

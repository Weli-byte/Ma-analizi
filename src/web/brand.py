"""The app's name and the owner's contact details, in one place (ADR 0060). Defaults are the owner's; each can be
overridden by an environment variable (e.g. when the app moves to a company)."""

import os

APP_NAME = os.environ.get("FORECAST_APP_NAME", "Welliddia")
OWNER = os.environ.get("FORECAST_OWNER", "Veli Parlak")
CONTACT_EMAIL = os.environ.get("FORECAST_CONTACT_EMAIL", "veliparlak84@gmail.com")
COUNTRY = os.environ.get("FORECAST_COUNTRY", "Türkiye")

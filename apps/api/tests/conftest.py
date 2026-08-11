"""Shared pytest configuration.

Sets safe default environment variables before any test module can import
app.main, which bakes Settings into module-level FastAPI/middleware
construction at import time. Using setdefault (not overwrite) means a
developer's real .env values still win if already set.
"""

import os

os.environ.setdefault("GITHUB_APP_ID", "900000099")
os.environ.setdefault("GITHUB_APP_SLUG", "buglens-test-app")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-not-for-prod")

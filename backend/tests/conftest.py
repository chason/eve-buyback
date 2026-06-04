import os

# Configure SSO + session before the app (and its cached settings) import.
os.environ.setdefault("BUYBACK_ENVIRONMENT", "development")
os.environ.setdefault("BUYBACK_EVE_CLIENT_ID", "test-client-id")
os.environ.setdefault("BUYBACK_EVE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("BUYBACK_SESSION_SECRET", "test-session-secret")

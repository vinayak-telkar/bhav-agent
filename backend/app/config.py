"""
Single hardcoded demo user — v1 has no auth/multi-user support (tech spec §7).
Centralized here so routes, the scheduler, and ingest/seed_data.py all agree
on the same id rather than each defining their own.
"""
import os

DEMO_USER_ID = os.environ.get("DEMO_USER_ID", "demo-user-0001")

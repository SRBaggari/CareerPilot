"""Job discovery: search job sources through provider adapters, then import a posting into
CareerPilot, where the existing analysis and matching pipeline takes over unchanged.

Only sources that permit automated access (official APIs, published feeds) can be added.
There is no scraping, and nothing bypasses authentication, CAPTCHAs, robots rules or other
access controls: see ``access.py``.
"""

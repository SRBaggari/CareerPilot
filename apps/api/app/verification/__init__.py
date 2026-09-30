"""The claim verification engine.

Independent of any generator: it takes a generated document, extracts its claims, retrieves
the candidate's own evidence, compares each claim with that evidence and with the stored
profile, and decides SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONTRADICTED. Only
SUPPORTED claims are approved; nothing is ever upgraded silently.
"""

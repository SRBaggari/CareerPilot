"""Site adapters: the application sites CareerPilot knows how to fill.

A site without an adapter is unsupported: CareerPilot stops and explains, and never
guesses at an unknown form. To support a real provider (e.g. a job board's hosted
application form), add an adapter that recognises its form by structure, and test it
against a recorded copy of that form before enabling it.
"""

from app.automation.adapters.base import (
    DetectedForm,
    FormField,
    SiteAdapter,
    Unsupported,
    adapter_for,
)

__all__ = ["DetectedForm", "FormField", "SiteAdapter", "Unsupported", "adapter_for"]

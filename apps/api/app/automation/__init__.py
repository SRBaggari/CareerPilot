"""Browser-assisted applications (Playwright), built so nothing is ever submitted without
the candidate's explicit confirmation.

Two phases, each with its own browser session:

1. **Prepare**: open the application page, stop at any access control (CAPTCHA, login,
   refusal), detect the supported fields, fill them from the approved application, read
   back exactly what the form now contains, and close. Every non-GET request is blocked
   in this phase, so nothing can be submitted even by accident.
2. **Submit**: only after the candidate confirms the review (by its hash). The page is
   opened and filled again, the form must match the reviewed content exactly, and only
   then is the submit button pressed.

Unsupported sites stop with an explanation. Every action is recorded in an audit log.
"""

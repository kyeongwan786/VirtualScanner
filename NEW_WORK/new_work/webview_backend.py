from __future__ import annotations

import sys


def hide_backend_application(root) -> None:
    """Keep the internal Tk worker out of the user's app switcher/taskbar."""
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
        except Exception:
            # The HTML window remains usable if AppKit is unavailable.
            pass
    elif sys.platform == "win32":
        try:
            root.attributes("-toolwindow", True)
        except Exception:
            pass

    root.withdraw()

"""Entry point for the Kick Stream Recorder application.

Configure root logging, then launch the CustomTkinter :class:`~src.gui.app.App`.
Run from the project root with::

    python -m src.main
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> None:
    """Create the main window and enter the Tk event loop."""
    from .gui.app import App

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

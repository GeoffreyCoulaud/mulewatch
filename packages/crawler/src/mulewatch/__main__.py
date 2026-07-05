"""Package shim: makes ``python -m mulewatch`` work (spec §2/§4/§9.4).

``python -m mulewatch`` runs the PACKAGE's ``__main__`` (this file), not the
``composition`` subpackage's. We re-export ``main`` (the real entry point, in
``composition.__main__``) and call it under ``__name__ == "__main__"``.
"""

from mulewatch.composition.__main__ import main

__all__ = ["main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""``MergeError``: usage or merge error (clear message for the CLI, never bare).

The merge is a standalone operator tool (merge spec §2): it does not depend on the
repositories' error contract. ``MergeError`` is its own exception (``ValueError`` style:
a readable message that ``__main__`` prints on ``stderr`` with a non-zero exit code).
"""


class MergeError(Exception):
    """Invalid usage or a copy that fails (fail-fast, clear message for the operator)."""

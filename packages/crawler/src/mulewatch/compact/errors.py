"""`CompactError`: usage or compaction error (clear message for the CLI, never bare).

Standalone operator tool (compaction spec §6), independent of the repositories' error
contract — like MergeError. `__main__` prints it on stderr with a non-zero exit code.
"""


class CompactError(Exception):
    """Invalid usage or a compaction that fails (fail-fast, clear message for the operator)."""

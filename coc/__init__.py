"""KEENEYE — a chain-of-custody tracker for digital evidence.

The package is deliberately layered so that every front-end (the CLI in
:mod:`coc.cli`, the JSON API in :mod:`coc.api`) sits on top of
:mod:`coc.service`.  Nothing else is allowed to write to the database, which
is what guarantees that no state change can happen without a matching entry in
the append-only custody log.
"""

__all__ = ["TOOL_NAME", "__version__"]

TOOL_NAME = "KEENEYE"
__version__ = "0.1.0"

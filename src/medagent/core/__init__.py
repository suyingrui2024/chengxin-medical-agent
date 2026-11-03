"""Shared vocabulary and low-level primitives: domain types, the event store, text
matching, phase planning, measurement kinds, state transitions, turn context,
presentation and the decision wait. Everything above may import this package; it
imports nothing above itself."""

#: A kernel package: other packages depend on it, and it promises no names
#: of its own.  Callers import the module they need
#: (``from medagent.core.domain import CaseState``).  The empty surface is
#: declared rather than left unwritten, so "deliberately empty" and "nobody
#: got round to it" stay different states.
__all__: list[str] = []

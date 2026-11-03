"""Safety and evidence guards: assertion evidence, direction guards, red flags,
baseline projection and self-checks. These decide what may be asserted and what
must be withheld."""

#: This package is organised by submodule: callers import from the module they
#: need (``from medagent.guards.triage import TriageService``), and the package
#: itself promises no names.  The empty surface is declared rather than left
#: unwritten, so "deliberately empty" and "nobody got round to it" are
#: different states.
__all__: list[str] = []

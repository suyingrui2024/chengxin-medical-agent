"""First-party capability packages. Manifests never import arbitrary code."""

#: This package is organised by submodule: callers import from the module they
#: need (``from medagent.extensions.triage import TriageService``), and the package
#: itself promises no names.  The empty surface is declared rather than left
#: unwritten, so "deliberately empty" and "nobody got round to it" are
#: different states.
__all__: list[str] = []

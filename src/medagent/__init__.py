"""Auditable outpatient-style consultation agent."""

#: The single source of the version string.  package.json and
#: pyproject.toml cannot import this, so scripts/lint/version.py checks
#: them against this value; the policy version stamped into every
#: agent.started event is derived from it at runtime, so an audit record
#: can never claim a policy the code did not run.
__version__ = "0.11.17"

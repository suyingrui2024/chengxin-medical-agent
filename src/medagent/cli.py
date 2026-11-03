"""The command-line entry points.

Each subcommand is one operation a human runs by hand -- starting the
server, inspecting content -- and forwards to the same functions the
desktop shell calls, so the two entry points cannot drift apart.
"""

from .server import main


if __name__ == "__main__":
    main()


"""Phoenix for agentcraft2: the viewer for the traces of agents, on the developer's machine only.

``python -m agentcraft2_phoenix`` runs Phoenix, the open-source trace viewer, with the
settings and the page that suit a developer's own machine:

* it listens on loopback only, and needs no login and no key;
* it sends nothing anywhere: no usage telemetry, no web fonts, no calls from the page to
  other sites, and no model providers;
* its page is named AgentCraft2 Traces and shows traces, sessions, datasets and experiments,
  not the playground, the prompt hub or the vendor's links.

Users start it with ``ac2 services start``; this package is what that command installs.
"""

__all__ = ["PINNED_VERSION"]

PINNED_VERSION = "20.21.1"
"""The release of ``arize-phoenix`` this package was written for and tested with."""

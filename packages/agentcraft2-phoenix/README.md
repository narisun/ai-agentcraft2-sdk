# agentcraft2-phoenix

Phoenix for agentcraft2: the viewer for the traces of agents, running on the developer's
machine only. You do not install this package yourself. `ac2 services start phoenix`
installs it, with the Phoenix release it was made for, into the workspace's own
`.agentcraft2/services/phoenix` folder, and starts it.

It differs from stock Phoenix in four ways:

- **Local only.** It listens on `127.0.0.1`. It needs no login and no key, and an agent's
  traces go to it over loopback HTTP and nowhere else.
- **Sends nothing out.** Usage telemetry, the tracking pixel and web fonts are off, the page
  may connect only to its own server (so it no longer asks GitHub for the number of stars),
  and no model provider is allowed.
- **Named AgentCraft2 Traces,** with the playground, the prompt hub, evaluators, settings and
  every link to the maker's sites hidden. Traces, sessions, datasets and experiments remain.
- **Pinned.** It runs only with the Phoenix release in `PINNED_VERSION` and refuses to start,
  or to rewrite the page, with any other.

```text
python -m agentcraft2_phoenix --data-dir DIR [--port 6006]
```

The settings Phoenix runs with are decided in `agentcraft2_phoenix/config/environment.py`, the
lifecycle changes in `server.py`, and the page in `page.py`. When you move to another Phoenix
release, change `PINNED_VERSION` and the `viewer` extra together, run the tests, and look at the
page in a browser: the rewrite tells you at startup if the page no longer has a text it expects.

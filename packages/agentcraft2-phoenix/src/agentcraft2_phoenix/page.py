"""The page Phoenix serves, renamed and trimmed for a developer who uses it for traces.

Phoenix has no setting for its name, its menu or the links to its maker. Its web page is built
from one template, ``index.html``, that the server fills in for every address. This module
rewrites that template as it is loaded:

* the title and the descriptions say AgentCraft2 Traces, and the preview image, a picture
  fetched from another site, is removed;
* a style hides the playground, the prompt hub, evaluators, settings, and every link to the
  maker's sites, including the "Star on GitHub" button;
* a script sends a visit to a hidden page to the list of projects;
* a content security policy lets the page connect to its own server and nowhere else, which
  also stops the browser asking GitHub for the number of stars.

Hiding is a convenience, not a lock: the providers that the playground would use are switched
off in the server's environment. The rewrite is written for one release of Phoenix. If a text
it expects is not there, it refuses to start rather than serve a page it did not check.
"""

from __future__ import annotations

from typing import Any

import jinja2

from agentcraft2_phoenix import PINNED_VERSION

__all__ = ["TITLE", "BrandedLoader", "BrandingError", "brand", "install"]

TITLE = "AgentCraft2 Traces"
INDEX = "index.html"

_DESCRIPTION = "Traces of your AgentCraft2 agents"
_REPLACEMENTS = (
    ("<title>Phoenix</title>", f"<title>{TITLE}</title>"),
    ('<meta name="title" content="Arize Phoenix" />', f'<meta name="title" content="{TITLE}" />'),
    (
        '<meta property="og:title" content="Arize Phoenix" />',
        f'<meta property="og:title" content="{TITLE}" />',
    ),
    (
        '<meta name="description" content="AI Observability & Evaluation" />',
        f'<meta name="description" content="{_DESCRIPTION}" />',
    ),
    (
        '<meta property="og:description" content="AI Observability & Evaluation" />',
        f'<meta property="og:description" content="{_DESCRIPTION}" />',
    ),
    (
        '<meta property="og:image" content="https://raw.githubusercontent.com/Arize-ai/'
        'phoenix-assets/main/images/socal/social-preview-horizontal.jpg" />',
        "",
    ),
)

_HIDDEN_PAGES = (
    "playground",
    "prompts",
    "evaluators",
    "settings",
    "dashboards",
    "chat",
    "apis",
)
_HIDDEN_LINKS = (
    *(f'a[href$="/{page}"]' for page in _HIDDEN_PAGES),
    *(f'a[href*="/{page}/"]' for page in _HIDDEN_PAGES),
    *(f'a[href*="/{page}?"]' for page in _HIDDEN_PAGES),
    'a[href^="https://github.com/Arize-ai"]',
    'a[href^="https://arize.com"]',
    'a[href^="https://docs.arize.com"]',
    'a[href^="https://join.slack.com"]',
    'a[href^="https://discord"]',
    'a[href^="https://www.linkedin.com"]',
    'a[href^="https://twitter.com"]',
    'a[href^="https://x.com"]',
)

_POLICY = "connect-src 'self'; object-src 'none'; base-uri 'self'"
_HIDE_RULE = ", ".join(_HIDDEN_LINKS)
_HIDDEN_PATTERN = "|".join(_HIDDEN_PAGES)
_NAME_RULES = f"""
      nav a[href="/"] > svg {{ display: none !important; }}
      nav[data-expanded="false"] a[href="/"] > svg {{ display: block !important; }}
      nav a[href="/"] .brand-text-wrap {{ width: auto !important; }}
      nav a[href="/"] .brand-text-wrap > * {{ display: none !important; }}
      nav a[href="/"] .brand-text-wrap::after {{
        content: "{TITLE}"; font-weight: 600; font-size: 15px; white-space: nowrap;
      }}"""
_ADDITIONS = f"""
    <meta http-equiv="Content-Security-Policy" content="{_POLICY}" />
    <style>
      {_HIDE_RULE} {{ display: none !important; }}{_NAME_RULES}
    </style>
    <script>
      (function () {{
        if (/^\\/({_HIDDEN_PATTERN})(\\/|$)/.test(location.pathname)) {{
          location.replace("/projects");
        }}
        // The page sets its own title as you move about, and may replace the whole title
        // element; watch the head, and keep the name at the end.
        var suffix = " - {TITLE}";
        function name() {{
          var title = document.title;
          if (title !== "{TITLE}" && title.slice(-suffix.length) !== suffix) {{
            document.title = title + suffix;
          }}
        }}
        name();
        new MutationObserver(name).observe(document.head, {{
          childList: true, characterData: true, subtree: true
        }});
      }})();
    </script>
  </head>"""


class BrandingError(RuntimeError):
    """The page is not the one this module was written for."""


def brand(source: str) -> str:
    """Return the template ``index.html`` with the name, the menu and the policy applied.

    Raises:
        BrandingError: If a text this module replaces is missing or occurs more than once.
    """
    changed = source
    for old, new in (*_REPLACEMENTS, ("</head>", _ADDITIONS)):
        found = changed.count(old)
        if found != 1:
            raise BrandingError(
                f"Phoenix's page has {found} copies of {old[:70]!r}, and one was expected. "
                f"The page rewrite was written for Phoenix {PINNED_VERSION}; "
                "install that release, or update agentcraft2_phoenix/page.py for the new one"
            )
        changed = changed.replace(old, new)
    return changed


class BrandedLoader(jinja2.BaseLoader):
    """A template loader that applies :func:`brand` to the page and leaves the rest alone."""

    def __init__(self, inner: jinja2.BaseLoader) -> None:
        self.inner = inner

    def get_source(
        self, environment: jinja2.Environment, template: str
    ) -> tuple[str, str | None, Any]:
        """Load ``template`` from the loader this one wraps, branding the page."""
        source, filename, uptodate = self.inner.get_source(environment, template)
        return (brand(source) if template == INDEX else source), filename, uptodate


def install(app_module: Any) -> None:
    """Make the Phoenix server render the branded page.

    Args:
        app_module: ``phoenix.server.app``, which holds the ``templates`` the page is made from.
    """
    environment = app_module.templates.env
    environment.loader = BrandedLoader(environment.loader)
    environment.cache.clear()
    # Fail now, at startup, and not when the first browser asks.
    environment.loader.get_source(environment, INDEX)

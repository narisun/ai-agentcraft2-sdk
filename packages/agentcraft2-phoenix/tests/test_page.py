"""The page is renamed and trimmed, and refuses to be rewritten when it is not the expected one."""

from typing import Any

import jinja2
import pytest

from agentcraft2_phoenix import PINNED_VERSION, page

# Only the lines the rewrite looks at, in the form Phoenix's template has them.
IMAGE = (
    "https://raw.githubusercontent.com/Arize-ai/phoenix-assets/main/images/socal/"
    "social-preview-horizontal.jpg"
)
PAGE = """<!DOCTYPE html>
<html>
  <head>
    <title>Phoenix</title>
    <meta name="title" content="Arize Phoenix" />
    <meta name="description" content="AI Observability & Evaluation" />
    <meta property="og:title" content="Arize Phoenix" />
    <meta property="og:description" content="AI Observability & Evaluation" />
    <meta property="og:image" content="IMAGE" />
    <script src="{{basename}}/modernizr.js"></script>
  </head>
  <body><div id="root"></div></body>
</html>
""".replace("IMAGE", IMAGE)


def test_the_name_and_descriptions_are_replaced() -> None:
    branded = page.brand(PAGE)
    assert "<title>AgentCraft2 Traces</title>" in branded
    assert 'name="title" content="AgentCraft2 Traces"' in branded
    assert 'property="og:title" content="AgentCraft2 Traces"' in branded
    assert branded.count("Traces of your AgentCraft2 agents") == 2
    assert "<title>Phoenix</title>" not in branded
    assert "Arize Phoenix" not in branded


def test_the_preview_image_from_another_site_is_removed() -> None:
    assert "raw.githubusercontent.com" not in page.brand(PAGE)


def test_the_page_may_connect_only_to_its_own_server() -> None:
    branded = page.brand(PAGE)
    assert branded.count("Content-Security-Policy") == 1
    assert "connect-src 'self'" in branded
    assert "object-src 'none'" in branded


def test_the_playground_prompts_evaluators_settings_and_the_makers_links_are_hidden() -> None:
    branded = page.brand(PAGE)
    for selector in (
        'a[href$="/playground"]',
        'a[href*="/playground?"]',
        'a[href$="/prompts"]',
        'a[href$="/evaluators"]',
        'a[href$="/settings"]',
        'a[href*="/settings/"]',
        'a[href^="https://github.com/Arize-ai"]',
        'a[href^="https://arize.com"]',
    ):
        assert selector in branded
    assert "display: none !important" in branded


def test_the_assistant_chat_the_api_pages_and_the_dashboards_are_hidden_too() -> None:
    branded = page.brand(PAGE)
    for selector in ('a[href$="/chat"]', 'a[href*="/apis/"]', 'a[href$="/dashboards"]'):
        assert selector in branded


def test_the_sidebar_shows_the_name_in_place_of_the_makers_wordmark() -> None:
    branded = page.brand(PAGE)
    assert 'nav a[href="/"] .brand-text-wrap::after' in branded
    assert 'content: "AgentCraft2 Traces"' in branded
    assert 'nav a[href="/"] > svg { display: none !important; }' in branded


def test_the_tab_title_keeps_the_name_as_the_page_changes_its_title() -> None:
    branded = page.brand(PAGE)
    assert 'var suffix = " - AgentCraft2 Traces";' in branded
    assert "new MutationObserver(name).observe(document.head" in branded


def test_traces_sessions_datasets_and_experiments_are_not_hidden() -> None:
    branded = page.brand(PAGE)
    for shown in ("projects", "sessions", "datasets", "experiments", "traces"):
        assert f'a[href$="/{shown}"]' not in branded


def test_a_visit_to_a_hidden_page_goes_to_the_projects() -> None:
    branded = page.brand(PAGE)
    assert 'location.replace("/projects")' in branded
    assert r"/^\/(playground|prompts|evaluators|settings|dashboards|chat|apis)(\/|$)/" in branded


@pytest.mark.parametrize(
    "missing",
    [
        "<title>Phoenix</title>",
        '<meta name="title" content="Arize Phoenix" />',
        '<meta name="description" content="AI Observability & Evaluation" />',
        "</head>",
    ],
)
def test_a_page_without_an_expected_text_is_refused_with_the_release_it_was_written_for(
    missing: str,
) -> None:
    with pytest.raises(page.BrandingError) as caught:
        page.brand(PAGE.replace(missing, ""))
    assert PINNED_VERSION in str(caught.value)
    assert "0 copies" in str(caught.value)


def test_a_page_with_a_text_twice_is_refused() -> None:
    with pytest.raises(page.BrandingError, match="2 copies"):
        page.brand(PAGE.replace("</head>", "</head></head>"))


def test_the_loader_rewrites_the_page_and_nothing_else() -> None:
    environment = jinja2.Environment(
        loader=page.BrandedLoader(jinja2.DictLoader({"index.html": PAGE, "other.html": "Phoenix"})),
        autoescape=True,
    )
    assert "AgentCraft2 Traces" in environment.get_template("index.html").render(basename="")
    assert environment.get_template("other.html").render() == "Phoenix"


class _Templates:
    def __init__(self, source: str) -> None:
        self.env = jinja2.Environment(
            loader=jinja2.DictLoader({"index.html": source}), autoescape=True
        )


class _App:
    def __init__(self, source: str) -> None:
        self.templates = _Templates(source)


def test_installing_makes_the_server_render_the_branded_page() -> None:
    app: Any = _App(PAGE)
    app.templates.env.get_template("index.html")  # cached before, as after a first request
    page.install(app)
    assert "AgentCraft2 Traces" in app.templates.env.get_template("index.html").render(basename="")


def test_installing_fails_at_startup_when_the_page_is_not_the_expected_one() -> None:
    with pytest.raises(page.BrandingError):
        page.install(_App("<html><head></head></html>"))

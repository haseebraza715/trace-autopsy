"""Shared test configuration.

Every test in the suite must be deterministic and fully offline: no LLM
calls and no sentence-transformer model loads (which would download weights
from HuggingFace on a machine without a warm cache).
"""

from __future__ import annotations

import warnings

import pytest

from agent_autopsy.preanalysis.patterns import PatternDetector
from agent_autopsy.utils.config import Config, get_config, set_config

# langgraph's import chain pulls in both `langchain_core` and `langchain`,
# each of which registers `default` warning filters for its deprecation
# categories. `warn_deprecated(pending=True)` emits with the *pending*
# category (CPython filters on the Warning instance's class), and the second
# registration lands on top of anything pytest's filterwarnings applied,
# surfacing an import-time LangChainPendingDeprecationWarning. Pre-import the
# chain here with the second registration neutralized and the category
# suppressed so the warning can never escape into collection or test output.
try:
    import langchain_core  # noqa: F401  (ensures deprecation module is registered)
    from langchain_core._api import deprecation as _lc_deprecation

    _lc_deprecation.surface_langchain_deprecation_warnings = lambda: None
    warnings.filterwarnings(
        "ignore", category=_lc_deprecation.LangChainPendingDeprecationWarning
    )
    from langgraph.graph import END, StateGraph  # noqa: F401
    from langgraph.graph.message import add_messages  # noqa: F401
except ImportError:  # langchain/langgraph are optional (dev/llm extras)
    pass


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real .env out of the suite.

    Config.from_env and Config.__post_init__ call load_dotenv, so a populated
    .env in the repo root would override the defaults these tests assert on.
    Tests that need env vars set them explicitly via monkeypatch."""
    try:
        import agent_autopsy.utils.config as config_module

        monkeypatch.setattr(config_module, "load_dotenv", lambda *a, **k: False)
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _offline_test_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline + isolated global config for every test.

    - Snaps the global config so cross-test mutation leaks stop.
    - Forces the lexical goal-drift path so embedding models are never loaded
      (and therefore never downloaded) during the suite.
    """
    monkeypatch.setattr(
        PatternDetector,
        "_get_embedding_model",
        classmethod(
            lambda cls, name: (_ for _ in ()).throw(ImportError("offline test guard"))
        ),
    )

    prev_config = get_config()
    prev_skip = prev_config.skip_embeddings
    set_config(Config.from_env())
    get_config().skip_embeddings = True
    yield
    prev_config.skip_embeddings = prev_skip
    set_config(prev_config)

"""Keep AppTest's simulated client on the page selected by native redirects."""

import pytest
from streamlit.testing.v1 import AppTest

from ai_event_planning_platform.navigation import CURRENT_ROUTE_KEY


@pytest.fixture(autouse=True)
def follow_native_page_redirects(monkeypatch):
    # This installed AppTest retains its initial _page_hash after st.switch_page.
    # A browser sends the newly selected hash on its next widget event. Emulate
    # that client behavior here; production routing/authentication stays intact.
    original = AppTest._run

    def run(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if CURRENT_ROUTE_KEY in self.session_state:
            route = self.session_state[CURRENT_ROUTE_KEY]
            path = "" if route == "login" else route
            for page_hash, info in self._registered_pages.items():
                if info.get("url_pathname") == path:
                    self._page_hash = page_hash
                    break
        return result

    monkeypatch.setattr(AppTest, "_run", run)

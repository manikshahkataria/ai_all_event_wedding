"""Read-only/public-page browser QA on the existing server; never deletes data."""

from urllib.parse import urlparse
import sys

from playwright.sync_api import expect, sync_playwright
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page()
            registered_routes = set()
            registered_titles = {}
            dashboard_routes = {"wedding_dashboard", "budget_dashboard", "wedding_timeline", "wedding_invitations"}

            def studio_loaded():
                return (dashboard_routes <= registered_routes
                        and registered_titles.get("wedding_invitations") == "Wedding Invitation Studio")

            def observe(payload):
                if isinstance(payload, bytes):
                    message = ForwardMsg.FromString(payload)
                    if message.HasField("navigation"):
                        registered_routes.update(p.url_pathname for p in message.navigation.app_pages)
                        registered_titles.update((p.url_pathname, p.page_name) for p in message.navigation.app_pages)

            page.on("websocket", lambda socket: socket.on("framereceived", observe))
            page.goto("http://localhost:8505")
            expect(page.get_by_role("heading", name="Log in", exact=True)).to_be_visible()
            if "--wait-for-source-refresh" in sys.argv and not studio_loaded():
                print("Waiting for source refresh in this anonymous QA session…", flush=True)
                for attempt in range(45):
                    if studio_loaded():
                        break
                    if attempt % 5 == 0:
                        page.keyboard.press("r")
                    page.wait_for_timeout(1000)
            assert studio_loaded(), "The running server has not loaded the Invitation Studio and dashboard routes."
            page.get_by_role("button", name="Sign up", exact=True).click()
            expect(page.get_by_role("heading", name="Create your account", exact=True)).to_be_visible()
            expect(page).to_have_url("http://localhost:8505/signup")
            page.go_back()
            expect(page.get_by_role("heading", name="Log in", exact=True)).to_be_visible()
            assert urlparse(page.url).path in {"", "/"}
            page.go_forward()
            expect(page.get_by_role("heading", name="Create your account", exact=True)).to_be_visible()
            expect(page).to_have_url("http://localhost:8505/signup")
            # Fresh protected URLs have no session tokens and must show no project data.
            protected = browser.new_page()
            for route in ("smart_wedding_budget", *sorted(dashboard_routes)):
                protected.goto("http://localhost:8505/" + route)
                expect(protected.get_by_role("heading", name="Log in", exact=True)).to_be_visible()
                expect(protected.get_by_role("heading", name="Wedding at a Glance", exact=True)).to_have_count(0)
                expect(protected.get_by_role("heading", name="Wedding Invitation Studio", exact=True)).to_have_count(0)
            print("Browser PASS on localhost:8505: Invitation Studio and dashboard routes registered, native Sign up navigation, Back, Forward, and unauthenticated overview/budget/calendar/studio/editor guards.")
            print("Scope: public routes and access guards; authenticated studio editing, uploads, downloads and persistence are covered with mocked AppTest/SDK tests and still need live-account verification. No accounts or projects modified.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()

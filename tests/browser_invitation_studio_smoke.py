"""Actual Edge interactions with the existing localhost frontend and a mocked backend.

No server is started, no authentication code is changed, and no real account,
Groq service or database is accessed. Only this QA browser's websocket and media
requests are intercepted. Production Python UI executes through AppTest; the real
Streamlit frontend renders its messages and sends actual browser widget events.
"""

from contextlib import ExitStack
import base64
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path
import re
import sys
import logging
from time import monotonic
from unittest.mock import Mock, patch
from urllib.parse import urlparse
from uuid import uuid4
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.local_script_runner import LocalScriptRunner

from ai_event_planning_platform.auth import AuthSessionTokens, store_auth_session
from ai_event_planning_platform.invitation_state import ACTIVE_CARD_KEY, WORKSPACE_KEY, STAGE_KEY
from ai_event_planning_platform.wedding_profile import WeddingDateStatus, WeddingLocationPreference, WeddingProfile
from ai_event_planning_platform.wedding_project_state import restore_project_workspace
from ai_event_planning_platform.wedding_projects import WeddingProjectRepository, profile_to_json
from test_invitations import ready_card
from test_wedding_projects import FakeClient, OWNER_ONE


URL = "http://localhost:8505"
REVIEW = "I reviewed all names, event details, tentative labels and text rendering"


class BrowserAppTestBridge:
    """Small Streamlit-protocol adapter, scoped to this manual browser QA script."""

    def __init__(self, app, template):
        self.app = app
        self.template = template
        self.messages = []
        self.media = {}
        self.runs = 0
        self.errors = []

    def capture_messages(self, original, runner):
        messages = original(runner)
        self.messages = list(messages)
        return messages

    def capture_media(self, original, storage, *args, **kwargs):
        file_id = original(storage, *args, **kwargs)
        self.media[file_id] = storage._files_by_id[file_id]
        return file_id

    def websocket(self, socket):
        # Intentionally do not connect_to_server(): mock events never reach the
        # existing server, so its authentication and account sessions are intact.
        socket.on_message(lambda payload: self.on_message(socket, payload))

    def on_message(self, socket, payload):
        message = BackMsg.FromString(payload)
        if not message.HasField("rerun_script"):
            return
        client = message.rerun_script
        if client.page_script_hash in self.app._registered_pages:
            self.app._page_hash = client.page_script_hash
        self.app._run(client.widget_states if self.runs else None, timeout=30)
        active_route = self.app.session_state["app_navigation_current"] if "app_navigation_current" in self.app.session_state else None
        active_path = "" if active_route == "login" else active_route
        for page_hash, info in self.app._registered_pages.items():
            if info.get("url_pathname") == active_path:
                self.app._page_hash = page_hash
                break
        self.runs += 1
        if self.app.exception:
            self.errors.extend(e.message for e in self.app.exception)

        # AppTest does not emit the network lifecycle messages. Reuse only the
        # anonymous server's public config and frontend version handshake.
        start = ForwardMsg()
        start.CopyFrom(self.template)
        start.new_session.script_run_id = str(uuid4())
        start.new_session.page_script_hash = self.app._page_hash
        start.new_session.initialize.session_id = "mock-invitation-browser-qa"
        start.new_session.initialize.session_status.script_is_running = True
        socket.send(start.SerializeToString())
        for forward in self.messages:
            outgoing = ForwardMsg()
            outgoing.CopyFrom(forward)
            if (outgoing.HasField("delta") and outgoing.delta.HasField("new_element")
                    and outgoing.delta.new_element.HasField("download_button")):
                button = outgoing.delta.new_element.download_button
                file_id = urlparse(button.url).path.rsplit("/", 1)[-1].split(".", 1)[0]
                item = self.media[file_id]
                # Chromium's native downloads bypass this fixture's HTTP routing.
                # Supply the exact production download payload as a QA data URL.
                button.url = f"data:{item.mimetype};base64," + base64.b64encode(item.content).decode("ascii")
            socket.send(outgoing.SerializeToString())
        finished = ForwardMsg()
        finished.script_finished = ForwardMsg.FINISHED_SUCCESSFULLY
        socket.send(finished.SerializeToString())
        idle = ForwardMsg()
        idle.session_status_changed.script_is_running = False
        socket.send(idle.SerializeToString())

    def fulfill_media(self, route):
        filename = urlparse(route.request.url).path.rsplit("/", 1)[-1]
        item = self.media.get(filename.split(".", 1)[0])
        assert item is not None, "Browser requested a missing mocked media file."
        headers = {"Content-Type": item.mimetype}
        if item.filename:
            headers["Content-Disposition"] = f'attachment; filename="{item.filename}"'
        route.fulfill(status=200, headers=headers, body=item.content)

    def healthy(self):
        assert not self.errors, self.errors
        assert not self.app.exception


def capture_public_handshake(browser):
    """Read only the anonymous login UI; never submit account credentials."""
    messages = []
    page = browser.new_page()

    def observe(payload):
        if isinstance(payload, bytes):
            message = ForwardMsg.FromString(payload)
            if message.HasField("new_session"):
                messages.append(message)

    page.on("websocket", lambda socket: socket.on("framereceived", observe))
    page.goto(URL)
    expect(page.get_by_role("heading", name="Log in", exact=True)).to_be_visible()
    page.close()
    assert messages, "No frontend handshake received from existing localhost:8505."
    return messages[-1]


def current(bridge):
    return bridge.app.session_state[WORKSPACE_KEY][bridge.app.session_state[ACTIVE_CARD_KEY]]


def wait_for_rerun(page, bridge, before):
    deadline = monotonic() + 40
    while bridge.runs <= before:
        assert monotonic() < deadline, "Browser widget event did not complete a rerun."
        page.wait_for_timeout(50)
    bridge.healthy()


def click(page, bridge, label):
    before = bridge.runs
    page.get_by_role("button", name=label, exact=True).click()
    wait_for_rerun(page, bridge, before)


def advance(page, bridge, count=1):
    for _ in range(count):
        label = "Continue to save & download" if bridge.app.session_state[STAGE_KEY] == 3 else "Continue"
        click(page, bridge, label)


def fill(page, bridge, label, value):
    before = bridge.runs
    widget = page.get_by_role("textbox", name=label, exact=True)
    if widget.input_value() == value:
        return
    widget.fill(value)
    widget.press("Tab")
    wait_for_rerun(page, bridge, before)


def check(page, bridge, label):
    widget = page.get_by_role("checkbox", name=label, exact=True)
    if not widget.is_checked():
        before = bridge.runs
        widget.focus()
        widget.press("Space")
        wait_for_rerun(page, bridge, before)
        expect(widget).to_be_checked()


def download_and_compare(page, bridge, label):
    image = page.get_by_role("img", name=re.compile(r"invitation preview, page 1$"))
    expect(image).to_be_visible()
    expected_url = bridge.app.get("image")[0].proto.imgs[0].url
    expect(image).to_have_attribute("src", re.compile(re.escape(expected_url) + r"$"))
    filename = urlparse(image.get_attribute("src")).path.rsplit("/", 1)[-1]
    preview = bridge.media[filename.split(".", 1)[0]].content
    with page.expect_download() as received:
        page.get_by_role("button", name=label, exact=True).click()
    downloaded = received.value
    assert downloaded.failure() is None
    data = Path(downloaded.path()).read_bytes()
    if "ZIP" in label:
        with ZipFile(BytesIO(data)) as archive:
            assert len(archive.namelist()) > 1
            pages = tuple(archive.read(name) for name in sorted(archive.namelist()))
    else:
        pages = (data,)
    assert pages[0].startswith(b"\x89PNG\r\n\x1a\n"), (
        f"Browser download is not PNG: scheme={urlparse(downloaded.url).scheme}, "
        f"expected={bridge.app.get('download_button')[0].proto.url}, bytes={len(pages[0])}, prefix={pages[0][:24]!r}")
    with Image.open(BytesIO(preview)) as preview_png, Image.open(BytesIO(pages[0])) as downloaded_png:
        diagnostic = f"preview={preview_png.format}/{preview_png.size}/{len(preview)} bytes; download={downloaded_png.format}/{downloaded_png.size}/{len(pages[0])} bytes"
    assert pages[0] == preview, "Downloaded PNG differs from the visible preview: " + diagnostic
    for raw in pages:
        with Image.open(BytesIO(raw)) as png:
            assert png.format == "PNG" and png.size == (1080, 1920)
            png.verify()
    bridge.healthy()
    return len(pages)


def open_project(page, bridge, row):
    click(page, bridge, "My Wedding Projects")
    project = page.locator(f'.st-key-continue_wedding_project_{row["project_id"]}').get_by_role("button")
    before = bridge.runs
    project.click()
    wait_for_rerun(page, bridge, before)
    click(page, bridge, "Invitations")
    expect(page.get_by_role("heading", name="Wedding Invitation Studio", exact=True)).to_be_visible()


def dashboard_smoke(page, bridge, row, ai):
    """Exercise the compact dashboard through actual frontend widget events."""
    expect(page.get_by_role("heading", name="Priya & Rahul's Wedding", exact=True)).to_be_visible()
    expect(page.get_by_text("18 November 2027 (fixed date) · Pune, Maharashtra", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Your Next Step", exact=True)).to_be_visible()
    assert len([button for button in bridge.app.button if button.proto.type == "primary"]) == 1
    assert not bridge.app.get("page_link"), "Dashboard duplicated its feature navigation."
    for label in ("Wedding summary", "Planning alerts"):
        assert not next(item for item in bridge.app.expander if item.label == label).proto.expanded
    expect(page.get_by_text("Guest count", exact=True)).not_to_be_visible()
    page.get_by_text("Wedding summary", exact=True).click()
    expect(page.get_by_text("Guest count", exact=True)).to_be_visible()
    page.get_by_text("Wedding summary", exact=True).click()
    expect(page.get_by_text("Guest count", exact=True)).not_to_be_visible()
    for label, route in (
        ("Wedding Details", "step_by_step"),
        ("Budget Dashboard", "budget_dashboard"),
        ("Calendar & Timeline", "wedding_timeline"),
        ("Invitations", "wedding_invitations"),
    ):
        expect(page.get_by_role("button", name=label, exact=True)).to_have_count(1)
        click(page, bridge, label)
        assert bridge.app.session_state["page"] == route
        assert bridge.app.session_state["active_wedding_project_id"] == row["project_id"]
        click(page, bridge, "Back")
        expect(page.get_by_role("heading", name="Your Next Step", exact=True)).to_be_visible()
        assert bridge.app.session_state["page"] == "wedding_dashboard"
    click(page, bridge, "Review Smart Budget")
    assert bridge.app.session_state["page"] == "smart_wedding_budget"
    click(page, bridge, "Back")
    expect(page.get_by_role("button", name="Invitations", exact=True)).to_be_visible()
    ai.assert_not_called()
    bridge.healthy()
    print("Browser PASS on localhost:8505: compact saved dashboard header, four unique feature destinations and Back, one deterministic next action, collapsed summaries and summary disclosure; no Groq calls.")
    print("Scope: existing Edge frontend; QA-only mocked authentication, project repository and backend. No live-account or database verification; no server started or real project modified.")


def main():
    logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
    dashboard = "--dashboard" in sys.argv
    client = FakeClient()
    repo = WeddingProjectRepository(client, OWNER_ONE)
    profile, ceremonies, _ = ready_card()
    if dashboard:
        profile = replace(profile, wedding_date_status=WeddingDateStatus.FIXED_DATE,
                          wedding_date=date(2027, 11, 18), location_preference=WeddingLocationPreference.LOCAL,
                          guest_count=80)
    row = repo.create_wedding_project("Browser QA complete invitation", profile, ceremonies)
    minimal = repo.create_wedding_project("Browser QA missing details", WeddingProfile(bride_name="Esha", groom_name="Vishal"), [])
    many = []
    for i in range(12):
        ceremony_id = str(uuid4())
        many.append(replace(ceremonies[0], ceremony_id=ceremony_id, name=f"Family celebration {i + 1}",
                            is_custom=True, plan=replace(ceremonies[0].plan, ceremony_id=ceremony_id)))
    long = repo.create_wedding_project("Browser QA long invitation", profile, many)
    app = AppTest.from_file("../app.py", default_timeout=30)
    store_auth_session(app.session_state, AuthSessionTokens("mock-access", "mock-refresh"))
    initial_state = {}
    restore_project_workspace(initial_state, row)
    for key, value in initial_state.items():
        app.session_state[key] = value
    initial_route = "wedding_dashboard" if dashboard else "wedding_invitations"
    app.session_state["page"] = initial_route
    ai = Mock(side_effect=AssertionError("Browser QA must not call Groq."))

    with sync_playwright() as p, ExitStack() as stack:
        browser = p.chromium.launch(channel="msedge", headless=True)
        stack.callback(browser.close)
        template = capture_public_handshake(browser)
        bridge = BrowserAppTestBridge(app, template)
        for target in (
            "ai_event_planning_platform.home.project_repository_from_state",
            "ai_event_planning_platform.wedding_project_state.project_repository_from_state",
            "ai_event_planning_platform.invitation_state.project_repository_from_state",
        ):
            stack.enter_context(patch(target, lambda state: repo))
        stack.enter_context(patch("ai_event_planning_platform.home.restore_authenticated_session", lambda a, r: AuthSessionTokens(a, r)))
        stack.enter_context(patch("ai_event_planning_platform.invitation_wording.suggest_wording", ai))
        stack.enter_context(patch("ai_event_planning_platform.budget_intelligence.interpret_budget_needs", ai))
        stack.enter_context(patch("ai_event_planning_platform.timeline_intelligence.interpret_timeline", ai))
        original_messages = LocalScriptRunner.forward_msgs
        original_media = MemoryMediaFileStorage.load_and_get_id
        stack.enter_context(patch.object(LocalScriptRunner, "forward_msgs", lambda runner: bridge.capture_messages(original_messages, runner)))
        stack.enter_context(patch.object(MemoryMediaFileStorage, "load_and_get_id", lambda storage, *a, **kw: bridge.capture_media(original_media, storage, *a, **kw)))
        app.run()
        app._page_hash = next(h for h, info in app._registered_pages.items() if info.get("url_pathname") == initial_route)
        context = browser.new_context(accept_downloads=True)
        context.route_web_socket("**/_stcore/stream", bridge.websocket)
        context.route("**/mock/media/**", bridge.fulfill_media)
        page = context.new_page()
        page.goto(URL + "/" + initial_route)
        if dashboard:
            dashboard_smoke(page, bridge, row, ai)
            return
        expect(page.get_by_role("heading", name="Wedding Invitation Studio", exact=True)).to_be_visible()
        print("Browser QA: Studio loaded with isolated mocked backend.", flush=True)

        click(page, bridge, "Traditional Royal")
        advance(page, bridge)
        fill(page, bridge, "Bride's name", "Invitation-only Priya")
        fill(page, bridge, "Invitation message", "Our custom browser-tested invitation message.")
        click(page, bridge, "Back a step")
        click(page, bridge, "Elegant Floral")
        advance(page, bridge)
        expect(page.get_by_role("textbox", name="Bride's name", exact=True)).to_have_value("Invitation-only Priya")
        expect(page.get_by_role("textbox", name="Invitation message", exact=True)).to_have_value("Our custom browser-tested invitation message.")
        advance(page, bridge, 3)
        check(page, bridge, REVIEW)
        expect(page.get_by_role("button", name="Download PNG", exact=True)).to_be_enabled()
        assert download_and_compare(page, bridge, "Download PNG") == 1
        print("Browser QA: edited card downloaded; PNG equals the visible preview.", flush=True)
        click(page, bridge, "Save invitation draft")
        expect(page.get_by_text("Invitation draft saved and read back from Supabase. Photo pixels remain session-only.", exact=True)).to_be_visible()
        check(page, bridge, REVIEW)
        click(page, bridge, "Load latest saved details for comparison")
        expect(page.get_by_role("checkbox", name=REVIEW, exact=True)).not_to_be_checked()
        assert current(bridge).wording == "Our custom browser-tested invitation message."
        assert current(bridge).overrides["bride_name"] == "Invitation-only Priya"

        # A separate saved source update, in the in-memory fake repository only.
        changed = replace(profile, groom_name="Vishal", wedding_date_status=WeddingDateStatus.FIXED_DATE,
                          wedding_date=date(2027, 11, 18), city="Manali")
        client.rows[0]["wedding_profile"].update(profile_to_json(changed))
        click(page, bridge, "Load latest saved details for comparison")
        click(page, bridge, "Back a step")
        click(page, bridge, "Edit invitation details")
        expect(page.get_by_role("textbox", name="Groom's name", exact=True)).to_have_value("Vishal")
        expect(page.get_by_role("textbox", name="Bride's name", exact=True)).to_have_value("Invitation-only Priya")

        open_project(page, bridge, minimal)
        advance(page, bridge, 4)
        expect(page.get_by_text("Complete or confirm these invitation details to download your card.", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Download PNG", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Save invitation draft", exact=True)).to_be_enabled()
        click(page, bridge, "Save invitation draft")
        click(page, bridge, "Back a step")
        page.get_by_text("Design & announcement options", exact=True).click()
        check(page, bridge, "Save the Date / announcement-style card")
        check(page, bridge, "Use 'To be announced' for unresolved dates, times and venues")
        advance(page, bridge)
        check(page, bridge, REVIEW)
        assert download_and_compare(page, bridge, "Download PNG") == 1

        open_project(page, bridge, long)
        advance(page, bridge, 4)
        check(page, bridge, REVIEW)
        zip_pages = download_and_compare(page, bridge, "Download all PNG pages (ZIP)")
        ai.assert_not_called()
        bridge.healthy()
        print(f"Browser PASS on localhost:8505: actual Edge editing/reruns, template-switch draft retention, review then load-latest regression, source refresh with override preservation, incomplete draft save/checklist, explicit TBA, matching 1080x1920 PNG and {zip_pages}-page ZIP downloads.")
        print("Scope: existing localhost frontend; QA-only intercepted websocket/media with mocked AppTest backend, authentication and repository. No live-account, Supabase or Groq verification; no real accounts/projects modified and no duplicate server started.")


if __name__ == "__main__":
    main()

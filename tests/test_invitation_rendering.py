from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from ai_event_planning_platform.invitations import TEMPLATES, new_invitation, resolve_invitation
from ai_event_planning_platform.invitation_rendering import SIZE, render_invitation, validate_photo
from test_invitations import ready_card


def photo_bytes(size=(400, 300), fmt="PNG"):
    image = Image.new("RGB", size, "#1240D0")
    target = BytesIO()
    image.save(target, format=fmt)
    return target.getvalue()


@pytest.mark.parametrize("template", tuple(TEMPLATES))
@pytest.mark.parametrize("kind", ("combined", "bride_family", "groom_family", "ceremony"))
def test_every_template_and_invitation_type_exports_real_readable_png(template, kind):
    p, cs, card = ready_card(kind)
    card = replace(card, template_id=template)
    result = render_invitation(card, resolve_invitation(card, p, cs))
    assert result.pages and all(page.startswith(b"\x89PNG\r\n\x1a\n") for page in result.pages)
    assert all(Image.open(BytesIO(page)).size == SIZE for page in result.pages)
    assert all(0 <= x1 <= x2 <= 1080 and 100 <= y1 <= y2 < 1740 for x1, y1, x2, y2 in result.text_bounds)


def test_designs_are_distinct_and_color_choices_change_pixels():
    p, cs, card = ready_card()
    signatures = {render_invitation(replace(card, template_id=tid), resolve_invitation(card, p, cs)).pages[0] for tid in TEMPLATES}
    assert len(signatures) == 4
    assert render_invitation(card, resolve_invitation(card, p, cs)).pages != render_invitation(replace(card, palette="alternate"), resolve_invitation(card, p, cs)).pages


@pytest.mark.parametrize("kind", ("combined", "bride_family", "groom_family", "ceremony"))
def test_overall_wedding_date_is_labelled_without_replacing_ceremony_dates(monkeypatch, kind):
    from datetime import date
    import ai_event_planning_platform.invitation_rendering as rendering
    from ai_event_planning_platform.wedding_profile import WeddingDateStatus
    p, cs, card = ready_card(kind)
    p = replace(p, wedding_date_status=WeddingDateStatus.FIXED_DATE, wedding_date=date(2027, 11, 18))
    captured = []
    wrap = rendering._wrap
    def capture(value, *args):
        captured.append(value)
        return wrap(value, *args)
    monkeypatch.setattr(rendering, "_wrap", capture)
    resolved = resolve_invitation(card, p, cs)
    rendered = render_invitation(card, resolved)
    assert rendered.pages
    assert ("Wedding date: 18 November 2027" in captured) == (kind != "ceremony")
    assert resolved["events"][0]["date"] == cs[0].plan.ceremony_date.isoformat()


def test_long_names_and_long_combined_itinerary_paginate_without_overflow():
    p, cs, card = ready_card()
    cs = [replace(cs[0], ceremony_id=f"event-{i}", name="Family celebration " + str(i),
        plan=replace(cs[0].plan, ceremony_id=f"event-{i}", venue_notes="A long venue name with a detailed address " * 8)) for i in range(15)]
    card = replace(new_invitation(cs), overrides={"bride_name": "W" * 120, "groom_name": "A long family name " * 6}, wording="A meaningful message with many good wishes. " * 22)
    result = render_invitation(card, resolve_invitation(card, p, cs))
    assert len(result.pages) > 2
    assert all(100 <= x1 <= x2 <= 980 and 100 <= y1 <= y2 < 1740 for x1, y1, x2, y2 in result.text_bounds)


def test_missing_information_is_preview_only_until_tba_is_accepted():
    p, cs, card = ready_card()
    card = replace(new_invitation(), overrides={"bride_name": "Priya", "groom_name": "Rahul"})
    before = render_invitation(card, resolve_invitation(card, p, []))
    announced = replace(card, announcement=True, accept_tba=True)
    after = render_invitation(announced, resolve_invitation(announced, p, []))
    assert before.pages != after.pages


def test_unicode_has_explicit_font_and_shaping_warnings():
    p, cs, card = ready_card()
    card = replace(card, overrides={"bride_name": "प्रिया", "groom_name": "राहुल"})
    result = render_invitation(card, resolve_invitation(card, p, cs))
    assert result.pages and any("script" in warning for warning in result.warnings)


@pytest.mark.parametrize("fmt,filename", [("JPEG", "portrait.jpg"), ("JPEG", "portrait.JPEG"), ("PNG", "portrait.png")])
def test_valid_photos_are_normalized_without_remote_services(fmt, filename):
    normalized = validate_photo(photo_bytes(fmt=fmt), filename)
    image = Image.open(BytesIO(normalized))
    assert image.format == "PNG" and image.size == (400, 300)
    assert "exif" not in image.info


@pytest.mark.parametrize("data,filename", [(b"not an image", "photo.jpg"), (b"x" * (8*1024*1024+1), "photo.png"),
    (photo_bytes(), "photo.jpg"), (photo_bytes(), "photo.gif"), (photo_bytes((63, 100)), "tiny.png"),
    (photo_bytes((8001, 64)), "wide.png")], ids=("invalid-content", "too-large", "content-extension-mismatch", "unsupported-type", "too-small", "too-wide"))
def test_invalid_photo_contents_extensions_and_resource_limits(data, filename):
    with pytest.raises(ValueError):
        validate_photo(data, filename)


def test_photo_framing_original_and_cover_produce_different_real_image_placement():
    p, cs, card = ready_card()
    photo = validate_photo(photo_bytes((200, 600)), "portrait.png")
    resolved = resolve_invitation(card, p, cs)
    blank = render_invitation(card, resolved)
    contained = render_invitation(card, resolved, photo)
    covered = render_invitation(replace(card, photo_fit="cover"), resolved, photo)
    assert contained.pages != blank.pages and covered.pages != contained.pages
    colors = Image.open(BytesIO(covered.pages[0])).getcolors(maxcolors=1080*1920)
    assert next(count for count, rgb in colors if rgb == (18, 64, 208)) > 10000


def test_photo_decompression_bomb_is_rejected(monkeypatch):
    raw = photo_bytes()
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError):
        validate_photo(raw, "photo.png")

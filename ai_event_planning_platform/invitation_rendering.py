"""Original code-drawn invitation designs and privacy-preserving local PNG rendering."""

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import math
from pathlib import Path
import unicodedata
import warnings

from PIL import Image, ImageDraw, ImageFont, ImageOps, features

from ai_event_planning_platform.invitations import date_label, missing_details, validate_invitation

SIZE = (1080, 1920)
MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000
PALETTES = {
    "royal": (("#4A1227", "#FFF1D0", "#D2AE68", "#7D364C"), ("#123B38", "#FFF4D7", "#D5B76F", "#32635A")),
    "floral": (("#FFF8EE", "#50443D", "#B68578", "#78937A"), ("#F5F0FA", "#42364B", "#AA81A2", "#77918B")),
    "minimal": (("#F5F4F0", "#183C43", "#568C85", "#E2E8DF"), ("#F7EFEA", "#4C3037", "#A57872", "#EDDDD6")),
    "festive": (("#FFF1B5", "#185A48", "#C74171", "#F2AE32"), ("#FCE4E9", "#3E5342", "#B77E20", "#D5898D")),
}


def validate_photo(data, filename):
    """Validate before decoding, bound resources, orient and strip EXIF/GPS metadata."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_PHOTO_BYTES:
        raise ValueError("Choose a JPG or PNG photo of at most 8 MB.")
    extension = Path(filename).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("Only JPG, JPEG and PNG photos are supported.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                expected = "PNG" if extension == ".png" else "JPEG"
                if probe.format != expected or getattr(probe, "is_animated", False):
                    raise ValueError
                w, h = probe.size
                if min(w, h) < 64 or max(w, h) > 8000 or w * h > MAX_PHOTO_PIXELS:
                    raise ValueError
                probe.verify()
            with Image.open(BytesIO(data)) as source:
                source.load()
                photo = ImageOps.exif_transpose(source).convert("RGBA")
                photo.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                flattened = Image.new("RGB", photo.size, "white")
                flattened.paste(photo, mask=photo.getchannel("A"))
                target = BytesIO()
                flattened.save(target, format="PNG")
                return target.getvalue()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError("This file is not a valid supported photo. Use a still JPG/PNG, 64–8000 px per side and at most 20 megapixels.") from None


@lru_cache(maxsize=64)
def _font(size, serif=False, unicode=False):
    candidates = (["C:/Windows/Fonts/Nirmala.ttc"] if unicode else []) + (
        ["C:/Windows/Fonts/georgia.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"] if serif else
        ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default(size=size)


def _flower(draw, x, y, radius, petal, center):
    for i in range(6):
        angle = math.pi * i / 3
        px, py = x + math.cos(angle) * radius, y + math.sin(angle) * radius
        draw.ellipse((px - radius * .7, py - radius * .7, px + radius * .7, py + radius * .7), fill=petal)
    draw.ellipse((x - radius * .45, y - radius * .45, x + radius * .45, y + radius * .45), fill=center)


def _canvas(template, palette, page_number):
    bg, ink, accent, secondary = PALETTES[template][palette == "alternate"]
    canvas = Image.new("RGB", SIZE, bg)
    draw = ImageDraw.Draw(canvas)
    if template == "royal":
        for inset, width in ((38, 4), (55, 2), (78, 1)):
            draw.rounded_rectangle((inset, inset, 1080-inset, 1920-inset), radius=42, outline=accent, width=width)
        for x in (90, 990):
            for y in (105, 1815):
                for radius in (15, 30, 45):
                    draw.ellipse((x-radius, y-radius, x+radius, y+radius), outline=accent, width=2)
                draw.polygon(((x, y-55), (x+12, y), (x, y+55), (x-12, y)), fill=accent)
        draw.line((380, 135, 700, 135), fill=accent, width=3)
    elif template == "floral":
        draw.rounded_rectangle((46, 48, 1034, 1872), radius=190, outline=accent, width=2)
        for x, y, direction in ((90, 110, 1), (990, 1810, -1)):
            for offset in range(0, 170, 32):
                lx, ly = x + direction * offset * .45, y + direction * offset
                draw.ellipse((lx-12, ly-32, lx+14, ly+28), fill=secondary)
            _flower(draw, x, y, 29, accent, bg)
            _flower(draw, x+direction*55, y+direction*55, 20, "#E6CDBF", accent)
    elif template == "minimal":
        draw.rectangle((0, 0, 22, 1920), fill=ink)
        draw.ellipse((810, -145, 1210, 255), fill=secondary)
        draw.arc((-120, 1590, 290, 2010), 0, 360, fill=accent, width=3)
        draw.line((140, 110, 940, 110), fill=accent, width=3)
        draw.line((140, 1800, 940, 1800), fill=accent, width=3)
    else:
        draw.rectangle((35, 35, 1045, 1885), outline=ink, width=4)
        for x in range(70, 1040, 105):
            draw.line((x, 38, x, 95 + (x % 3)*15), fill=ink, width=3)
            _flower(draw, x, 110+(x%3)*15, 14, secondary, accent)
        for x in (70, 1010):
            for y in range(220, 1760, 100):
                draw.ellipse((x-7, y-16, x+7, y+16), fill=secondary)
        draw.line((260, 1820, 820, 1820), fill=accent, width=4)
    small = _font(24)
    draw.text((540, 1840), f"{page_number}", fill=accent, font=small, anchor="mt")
    return canvas, ink, accent


def _clusters(text):
    groups = []
    for char in text:
        if groups and (unicodedata.combining(char) or char == "\u200d" or groups[-1].endswith("\u200d")):
            groups[-1] += char
        else:
            groups.append(char)
    return groups


def _wrap(text, font, width):
    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            candidate = (line + " " + word).strip()
            if font.getlength(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = ""
            for group in _clusters(word):
                if line and font.getlength(line + group) > width:
                    lines.append(line)
                    line = ""
                line += group
        lines.append(line)
    return lines


@dataclass(frozen=True)
class RenderedInvitation:
    pages: tuple[bytes, ...]
    warnings: tuple[str, ...]
    text_bounds: tuple[tuple[int, int, int, int], ...]


def render_invitation(card, resolved, photo=None):
    validate_invitation(card)
    all_text = " ".join([card.wording, *resolved["general"].values(), *card.family.values(),
                         *(str(v) for e in resolved["events"] for v in e.values())])
    notices = []
    non_latin = any(ord(c) > 0x024F and c not in "–—’‘“”·" for c in all_text)
    if non_latin:
        notices.append("Review every name and script in the PNG preview. Font coverage varies by installation.")
        if not features.check_feature("raqm"):
            notices.append("Complex-script shaping is unavailable in this Pillow build. Indic/Arabic text may not join correctly; review carefully or use a transliteration before export.")
    unresolved = any(blocking for _, _, blocking in missing_details(card, resolved))
    pages, bounds = [], []
    image, ink, accent = _canvas(card.template_id, card.palette, 1)
    content = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    page_start = 0
    y = 175
    serif = card.template_id != "minimal"

    def finish():
        offset = max(0, (1710-y)//2)
        image.paste(content, (0, offset), content)
        for index in range(page_start, len(bounds)):
            x1, y1, x2, y2 = bounds[index]
            bounds[index] = (x1, y1+offset, x2, y2+offset)
        if unresolved:
            draw = ImageDraw.Draw(image)
            draw.text((540, 1770), "DRAFT PREVIEW · DETAILS NEED REVIEW", font=_font(25), fill=accent, anchor="mt")
        target = BytesIO()
        image.save(target, format="PNG")
        pages.append(target.getvalue())

    def new_page():
        nonlocal image, ink, accent, y, content, page_start
        finish()
        image, ink, accent = _canvas(card.template_id, card.palette, len(pages)+1)
        content = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        page_start = len(bounds)
        y = 175
        ImageDraw.Draw(content).text((540, y), "CELEBRATION · CONTINUED", font=_font(25), fill=accent, anchor="mt")
        y += 65

    def text(value, size=35, color=None, gap=20, heading=False):
        nonlocal y
        if not value.strip():
            return
        face = _font(size, serif=serif and heading, unicode=any(ord(c) > 0x024F for c in value))
        for line in _wrap(value, face, 780):
            height = max(size + 16, face.getbbox(line or "Ag")[3] - face.getbbox(line or "Ag")[1] + 14)
            if y + height > 1710:
                new_page()
            draw = ImageDraw.Draw(content)
            box = draw.textbbox((540, y), line, font=face, anchor="mt")
            draw.text((540, y), line, font=face, fill=color or ink, anchor="mt")
            bounds.append(box)
            y += height
        y += gap

    general = resolved["general"]
    text("SAVE THE DATE" if card.announcement else "YOU ARE INVITED", 27, accent, gap=28)
    text(general["bride_name"] or "Bride's name (missing)", 68, heading=True, gap=4)
    text("&", 42, accent, gap=3)
    text(general["groom_name"] or "Groom's name (missing)", 68, heading=True, gap=30)
    if photo:
        if y + 370 > 1710:
            new_page()
        with Image.open(BytesIO(photo)) as source:
            source = source.convert("RGB")
            portrait = ImageOps.fit(source, (520, 340), method=Image.Resampling.LANCZOS) if card.photo_fit == "cover" else ImageOps.pad(source, (520, 340), method=Image.Resampling.LANCZOS, color=PALETTES[card.template_id][card.palette == "alternate"][0])
        content.paste(portrait, (280, y))
        ImageDraw.Draw(content).rectangle((275, y-5, 805, y+345), outline=accent, width=3)
        y += 375
    if card.invitation_type in {"bride_family", "groom_family"}:
        side = "Bride's family" if card.invitation_type == "bride_family" else "Groom's family"
        text(side.upper(), 26, accent, gap=12)
        text(card.family.get("hosts", "") or "Hosting details (missing)", 38, heading=True)
        for key, label in (("parents", "Parents"), ("relatives", "Family")):
            if card.family.get(key):
                text(label + ": " + card.family[key], 32)
        text(card.family.get("message", ""), 33)
    text(card.wording, 35, gap=30)
    if card.selected_ceremony_ids and card.invitation_type != "ceremony":
        wedding_date = date_label(general.get("date", ""), general.get("certainty", "undecided"))
        if wedding_date:
            text("Wedding date: " + wedding_date, 32, accent, gap=24)
    for event in resolved["events"]:
        text(event["name"], 43, accent, heading=True, gap=12)
        missing = "To be announced" if card.announcement and card.accept_tba else "Not provided (draft)"
        text(date_label(event["date"], event["certainty"]) or "Date: " + missing, 34, gap=8)
        timing = event["time"] or ("Preferred timing: " + event["preferred_timing"] if event.get("preferred_timing") else "Time: " + missing)
        text(timing, 33, gap=8)
        text(event["venue"] or "Venue: " + missing, 34, gap=8)
        text(event.get("details", ""), 31)
        y += 22
    text(general.get("location", ""), 31, gap=12)
    if card.selected_ceremony_ids:
        text(general.get("details", ""), 31)
    finish()
    return RenderedInvitation(tuple(pages), tuple(notices), tuple(bounds))

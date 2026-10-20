"""QR code helpers: read a QR code from a camera photo, and make QR images / printable ID cards."""
import io

import cv2
import numpy as np
import qrcode
import db
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def decode_qr(image_bytes):
    """Return the text inside the first QR code found in the photo, or None."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    det = cv2.QRCodeDetector()
    for candidate in (img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)):
        text, _, _ = det.detectAndDecode(candidate)
        if text:
            return text.strip()
    # try a sharpened / upscaled version for small or blurry codes
    big = cv2.resize(img, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
    text, _, _ = det.detectAndDecode(big)
    return text.strip() if text else None


def qr_png(text, box=8):
    img = qrcode.make(text, box_size=box, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _trips(s):
    from db import TRIPS
    t = [f"T{no}{'↑' if p == 'PICKUP' else '↓'}" for no, tr in TRIPS.items() for b, g, p in tr["movements"]
         if b == s.get("batch") and g == s.get("grp")]
    return "Trips: " + ", ".join(f"{x[:-1]} {'pickup' if x[-1] == '↑' else 'drop-off'}" for x in t)


def id_cards_pdf(students, school_name):
    """A4 sheet of student ID cards (2 x 5 per page) with QR codes, ready to print and laminate."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    cw, ch = 90 * mm, 52 * mm
    mx, my = (W - 2 * cw) / 3, 12 * mm
    gap_y = (H - 2 * my - 5 * ch) / 4
    for i, s in enumerate(students):
        k = i % 10
        if i and k == 0:
            c.showPage()
        col, row = k % 2, k // 2
        x = mx + col * (cw + mx)
        y = H - my - (row + 1) * ch - row * gap_y
        c.setStrokeColorRGB(0.75, 0.75, 0.75)
        c.roundRect(x, y, cw, ch, 4 * mm)
        c.setFillColorRGB(0.11, 0.33, 0.55)
        c.roundRect(x, y + ch - 10 * mm, cw, 10 * mm, 4 * mm, fill=1, stroke=0)
        c.rect(x, y + ch - 10 * mm, cw, 5 * mm, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x + 4 * mm, y + ch - 6.5 * mm, school_name[:48])
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(x + 4 * mm, y + ch - 17 * mm, s["name"][:28])
        c.setFont("Helvetica", 8.5)
        lines = [f"ID: {s['code']}   {s.get('class_name') or ''}{s.get('section') or ''}",
                 f"{db.mlabel(s.get('batch'), s.get('grp'))} · Bus {s.get('bus_no') or ''}", (f"Stop: {(s.get('stop_name') or '')[:30]}" if not s.get("drop_stop_name") or s.get("drop_stop_name") == s.get("stop_name")
                  else f"Pick: {(s.get('stop_name') or '').replace('Route ', 'R')} · Drop: {s['drop_stop_name'].replace('Route ', 'R')}"),
                 _trips(s)]
        for j, line in enumerate(lines):
            c.drawString(x + 4 * mm, y + ch - (23 + 5 * j) * mm, line)
        from reportlab.graphics.barcode import code128      # DPS ID cards carry a 1-D barcode (Code 128)
        bc = code128.Code128(s["badge"], barHeight=7 * mm, barWidth=0.36 * mm, humanReadable=True)
        bc.drawOn(c, x + cw - bc.width - 3 * mm, y + 3.5 * mm)
        c.setFont("Helvetica", 6.5)
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.drawString(x + 4 * mm, y + 3 * mm, f"Card {s['badge'][:20]} · bus and gate scans")
        c.setFillColorRGB(0, 0, 0)
    c.save()
    return buf.getvalue()


def rear_tags_pdf(buses, school_name):
    """A4 sheet of rear-door tags (2 x 4 per page): a large QR + the bus number, to fix inside the rear door of each bus.
    Crews scan it during the pre/post-trip check and security scans it during the after-trip inspection."""
    import security_ext as SX
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    cw, ch = 90 * mm, 66 * mm
    mx = (W - 2 * cw) / 3
    my = (H - 4 * ch) / 5
    for i, b in enumerate(buses):
        k = i % 8
        if i and k == 0:
            c.showPage()
        col, row = k % 2, k // 2
        x = mx + col * (cw + mx)
        y = H - (row + 1) * (ch + my)
        code = SX.rear_tag_code(b["bus_no"])
        c.setStrokeColorRGB(0.11, 0.15, 0.35)
        c.setLineWidth(1.2)
        c.roundRect(x, y, cw, ch, 4 * mm)
        c.setFillColorRGB(0.11, 0.15, 0.35)
        c.rect(x, y + ch - 11 * mm, cw, 11 * mm, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x + 5 * mm, y + ch - 7.5 * mm, "REAR DOOR — SCAN HERE")
        c.setFont("Helvetica", 7.5)
        c.drawRightString(x + cw - 5 * mm, y + ch - 7.5 * mm, school_name[:38])
        c.drawImage(ImageReader(io.BytesIO(qr_png(code, 10))), x + 4 * mm, y + 5 * mm, 46 * mm, 46 * mm)
        c.setFillColorRGB(0.1, 0.12, 0.2)
        c.setFont("Helvetica-Bold", 22)
        c.drawString(x + 53 * mm, y + 36 * mm, b["bus_no"])
        c.setFont("Helvetica", 8.5)
        for j, line in enumerate(["Crew: scan during the", "pre- and post-trip check.", "Security: scan during the", "after-trip inspection.",
                                  f"Code {code}"]):
            c.drawString(x + 53 * mm, y + (27 - 4.6 * j) * mm, line)
    c.save()
    return buf.getvalue()

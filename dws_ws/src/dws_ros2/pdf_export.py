"""
pdf_export.py
==============
ReportLab-based PDF report generation for the DWS export features.

This module is intentionally isolated from api_server.py: it contains
only report-layout logic and takes plain dicts (the same shape produced
by api_server._row_to_transaction()) plus filesystem directories as
input. It performs no database access and no FastAPI imports, so it can
be unit-tested or reused independently of the HTTP layer.

Two entry points:
    build_transaction_pdf(row, capture_dir, pointcloud_dir) -> bytes
        One-page report for a single transaction.

    build_summary_pdf(rows, capture_dir, pointcloud_dir) -> bytes
        Multi-page report, ~one page per transaction, with page numbers.

Both return raw PDF bytes ready to hand to a StreamingResponse.
"""

import io
import os
import time

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle


# ---------------------------------------------------------------------------
# Shared layout constants
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

INK        = HexColor("#1d1d1f")
INK_SOFT   = HexColor("#6e6e73")
RULE       = HexColor("#d0d0d5")
BLUE       = HexColor("#0071e3")
SNAPSHOT_BOX_W = PAGE_W - 2 * MARGIN
SNAPSHOT_BOX_H = 100 * mm

LABEL_STYLE = ParagraphStyle(
    "label", fontName="Helvetica", fontSize=7.5, leading=9,
    textColor=INK_SOFT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_basename(url_or_path):
    """
    Stored image_url / pointcloud_url values are treated elsewhere in the
    system (api_server.serve_capture / serve_pointcloud) as bare
    filenames — those routes reject any '/' in the path param. Mirror
    that contract here: take only the basename, regardless of whether
    the stored value is a bare filename, a relative path, or a full URL.
    """
    if not url_or_path:
        return None
    # Strip any URL query/fragment defensively, then take the basename.
    cleaned = url_or_path.split("?", 1)[0].split("#", 1)[0]
    return os.path.basename(cleaned) or None


def _fmt_num(v, decimals=1, suffix=""):
    if v is None:
        return "--"
    try:
        return f"{float(v):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def _draw_header(c, row, generated_at_str):
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(MARGIN, PAGE_H - MARGIN - 4, "DELHIVERY DWS REPORT")

    c.setFont("Helvetica", 8)
    c.setFillColor(INK_SOFT)
    c.drawRightString(
        PAGE_W - MARGIN, PAGE_H - MARGIN - 4,
        f"Generated {generated_at_str}"
    )

    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(MARGIN, PAGE_H - MARGIN - 12, PAGE_W - MARGIN, PAGE_H - MARGIN - 12)

    y = PAGE_H - MARGIN - 26
    c.setFont("Helvetica", 9)
    c.setFillColor(INK)

    tx_id = row.get("id")
    awb = row.get("awb") or "--"
    created_at = row.get("created_at") or "--"
    station = row.get("station_id") or "--"
    operator = row.get("operator") or "--"

    left_lines = [
        f"Transaction ID : DWS{str(tx_id).zfill(6) if tx_id is not None else '------'}",
        f"Barcode (AWB)  : {awb}",
    ]
    right_lines = [
        f"Timestamp  : {created_at}",
        f"Station ID : {station}",
        f"Operator ID: {operator}",
    ]

    ly = y
    for line in left_lines:
        c.drawString(MARGIN, ly, line)
        ly -= 12

    ry = y
    for line in right_lines:
        c.drawString(PAGE_W / 2, ry, line)
        ry -= 12

    return min(ly, ry) - 8


def _draw_measurements(c, row, top_y):
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(INK)
    c.drawString(MARGIN, top_y, "MEASUREMENTS")
    c.setStrokeColor(RULE)
    c.line(MARGIN, top_y - 3, PAGE_W - MARGIN, top_y - 3)

    fields = [
        ("Length",  _fmt_num(row.get("length"), 1, " mm")),
        ("Breadth", _fmt_num(row.get("width"), 1, " mm")),
        ("Height",  _fmt_num(row.get("height"), 1, " mm")),
        ("Weight",  _fmt_num(row.get("weight"), 2, " kg")),
    ]

    col_w = (PAGE_W - 2 * MARGIN) / 4
    row_h = 26
    y = top_y - 16
    for i, (label, value) in enumerate(fields):
        col = i % 4
        line_row = i // 4
        x = MARGIN + col * col_w
        yy = y - line_row * row_h
        c.setFont("Helvetica", 7)
        c.setFillColor(INK_SOFT)
        c.drawString(x, yy, label.upper())
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(INK)
        c.drawString(x, yy - 13, value)

    n_rows = (len(fields) + 3) // 4
    return y - n_rows * row_h - 6


def _resolve_capture_path(image_url, capture_dir):
    """
    Resolve a stored image_url to an existing file on disk.

    SnapshotManager.save() writes images under dated subdirectories
    (capture_dir/YYYY/MM/DD/filename.jpg) and stores that full absolute
    path as image_url. Reducing it to a bare basename (as the point
    cloud link does, to match serve_capture's URL contract) silently
    drops the YYYY/MM/DD segment, so the file is never found even
    though it exists. Try, in order: the stored path as-is (normal
    case — same machine), the same path rebased onto the current
    capture_dir (handles a moved/restored capture_dir while keeping
    the YYYY/MM/DD layout), then a bare-filename lookup for legacy
    flat captures.
    """
    if not image_url:
        return None
    if os.path.isfile(image_url):
        return image_url

    cap_dir = os.path.normpath(capture_dir)
    norm_url = os.path.normpath(image_url)

    marker = os.sep + os.path.basename(cap_dir) + os.sep
    idx = norm_url.find(marker)
    if idx != -1:
        candidate = os.path.join(cap_dir, norm_url[idx + len(marker):])
        if os.path.isfile(candidate):
            return candidate

    candidate = os.path.join(cap_dir, os.path.basename(norm_url))
    if os.path.isfile(candidate):
        return candidate

    return None


def _draw_snapshot(c, row, top_y, capture_dir):
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(INK)
    c.drawString(MARGIN, top_y, "PARCEL SNAPSHOT")
    c.setStrokeColor(RULE)
    c.line(MARGIN, top_y - 3, PAGE_W - MARGIN, top_y - 3)

    box_top = top_y - 10
    box_bottom = box_top - SNAPSHOT_BOX_H

    img_path = _resolve_capture_path(row.get("image_url"), capture_dir)

    if img_path:
        try:
            img = ImageReader(img_path)
            iw, ih = img.getSize()
            scale = min(SNAPSHOT_BOX_W / iw, SNAPSHOT_BOX_H / ih)
            draw_w, draw_h = iw * scale, ih * scale
            x = MARGIN + (SNAPSHOT_BOX_W - draw_w) / 2
            y = box_bottom + (SNAPSHOT_BOX_H - draw_h) / 2
            c.drawImage(
                img, x, y, width=draw_w, height=draw_h,
                preserveAspectRatio=True, mask="auto",
            )
        except Exception:
            c.setFont("Helvetica-Oblique", 9)
            c.setFillColor(INK_SOFT)
            c.drawCentredString(
                PAGE_W / 2, (box_top + box_bottom) / 2,
                "Snapshot could not be loaded."
            )
    else:
        c.setFont("Helvetica-Oblique", 9)
        c.setFillColor(INK_SOFT)
        c.drawCentredString(
            PAGE_W / 2, (box_top + box_bottom) / 2,
            "No snapshot available."
        )

    return box_bottom - 10


def _draw_pointcloud(c, row, top_y, pointcloud_dir, base_url=None):
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(INK)
    c.drawString(MARGIN, top_y, "POINT CLOUD")
    c.setStrokeColor(RULE)
    c.line(MARGIN, top_y - 3, PAGE_W - MARGIN, top_y - 3)

    filename = _safe_basename(row.get("pointcloud_url"))
    y = top_y - 16
    c.setFont("Helvetica", 9)

    if filename:
        pc_path = os.path.join(pointcloud_dir, filename)
        exists = os.path.isfile(pc_path)
        if base_url:
            link_url = f"{base_url}/pointclouds/{filename}"
            c.setFillColor(BLUE)
            c.drawString(MARGIN, y, filename)
            text_w = c.stringWidth(filename, "Helvetica", 9)
            c.linkURL(
                link_url,
                (MARGIN, y - 2, MARGIN + text_w, y + 9),
                relative=0,
            )
        else:
            c.setFillColor(INK)
            c.drawString(MARGIN, y, filename)
        if not exists:
            c.setFont("Helvetica-Oblique", 7.5)
            c.setFillColor(INK_SOFT)
            c.drawString(MARGIN, y - 11, "(file not found on disk)")
            y -= 11
    else:
        c.setFillColor(INK_SOFT)
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(MARGIN, y, "No point cloud captured.")

    return y - 14


def _draw_footer(c, page_num=None, total_pages=None):
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(MARGIN, MARGIN + 10, PAGE_W - MARGIN, MARGIN + 10)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(INK_SOFT)
    c.drawString(MARGIN, MARGIN, "Generated by DWS")
    if page_num is not None and total_pages is not None:
        c.drawRightString(
            PAGE_W - MARGIN, MARGIN, f"Page {page_num} of {total_pages}"
        )


def _render_transaction_page(c, row, capture_dir, pointcloud_dir,
                              generated_at_str, base_url=None,
                              page_num=None, total_pages=None):
    """Draw one full transaction report onto the current canvas page."""
    y = _draw_header(c, row, generated_at_str)
    y = _draw_measurements(c, row, y)
    y = _draw_snapshot(c, row, y, capture_dir)
    _draw_pointcloud(c, row, y, pointcloud_dir, base_url=base_url)
    _draw_footer(c, page_num=page_num, total_pages=total_pages)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def build_transaction_pdf(row, capture_dir, pointcloud_dir, base_url=None):
    """
    Build a single-page PDF report for one transaction.

    row: dict shaped like api_server._row_to_transaction()'s return value
         (must include at least: id, awb, length, width, height, weight,
         vol_weight, charge_weight, confidence, station_id, operator,
         created_at, image_url, pointcloud_url).
    capture_dir / pointcloud_dir: absolute directories to resolve
         image_url / pointcloud_url basenames against (pass
         dws_ros2.paths.CAPTURE_DIR / POINTCLOUD_DIR).
    base_url: optional public base URL (e.g. http://host:8000) used to
         make the point-cloud filename a clickable link in the PDF.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    generated_at_str = time.strftime("%d %b %Y %H:%M")
    _render_transaction_page(
        c, row, capture_dir, pointcloud_dir, generated_at_str,
        base_url=base_url, page_num=1, total_pages=1,
    )
    c.showPage()
    c.save()
    return buf.getvalue()


def build_summary_pdf(rows, capture_dir, pointcloud_dir, base_url=None):
    """
    Build a multi-page PDF, one page per transaction, with page numbers.

    rows: list of dicts shaped like _row_to_transaction()'s return value.
    Raises ValueError if rows is empty — caller decides how to surface
    that (api_server.py raises HTTPException 404, matching the existing
    /export/csv behaviour for an empty result set).
    """
    if not rows:
        raise ValueError("build_summary_pdf() called with no rows")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    generated_at_str = time.strftime("%d %b %Y %H:%M")
    total = len(rows)

    for i, row in enumerate(rows, start=1):
        _render_transaction_page(
            c, row, capture_dir, pointcloud_dir, generated_at_str,
            base_url=base_url, page_num=i, total_pages=total,
        )
        c.showPage()

    c.save()
    return buf.getvalue()

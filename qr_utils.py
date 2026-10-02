"""
qr_utils.py
Generates a QR code for each computer. The QR encodes a full URL to that
computer's public status page (/qr/<computer_id>), so scanning it with any
phone camera opens the page directly - no app required.
"""

import os
import qrcode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_FOLDER = os.path.join(BASE_DIR, "static", "qrcodes")


def generate_qr(computer_id, base_url, box_size=8):
    """Create a QR code that links to this computer's public status page.

    box_size controls the physical size of the generated image (bigger box_size
    = larger PNG). Settings > QR Codes lets the admin choose Small/Medium/Large,
    which maps to a box_size via database.QR_SIZE_OPTIONS - the URL encoded
    inside the QR code is exactly the same regardless of size.
    """
    os.makedirs(QR_FOLDER, exist_ok=True)
    target_url = f"{base_url.rstrip('/')}/qr/{computer_id}"

    qr = qrcode.QRCode(border=2, box_size=box_size)
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0B2E17", back_color="white")

    filename = f"{computer_id}.png"
    path = os.path.join(QR_FOLDER, filename)
    img.save(path)
    return f"qrcodes/{filename}"  # relative path under static/

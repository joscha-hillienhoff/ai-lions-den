"""Join QR code generation, cached per URL."""

from __future__ import annotations

import base64
from io import BytesIO

import qrcode
from qrcode.constants import ERROR_CORRECT_M

_CACHE: dict[str, str] = {}


def join_qr_data_url(url: str) -> str:
    cached = _CACHE.get(url)
    if cached:
        return cached

    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=1)
    code.add_data(url)
    code.make(fit=True)
    modules = code.modules_count
    code.box_size = max(1, 720 // (modules + 2))
    image = code.make_image(fill_color="#0a0c13", back_color="#f7f3e8")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    _CACHE[url] = data_url
    return data_url

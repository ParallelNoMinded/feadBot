from io import BytesIO

import qrcode


def generate_qr_png_bytes(url: str) -> bytes:
    img = qrcode.make(url)
    buff = BytesIO()
    img.save(buff, format="PNG")
    return buff.getvalue()

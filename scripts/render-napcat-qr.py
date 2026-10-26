"""Encode only the new instance's login URL locally; never fetch that URL."""
from pathlib import Path
from urllib.parse import urlsplit
import qrcode
from qrcode.image.pure import PyPNGImage

root = Path(__file__).resolve().parents[1]
source = root / ".state" / "napcat-qr-url.txt"
output = root / ".state" / "napcat-login.png"
try:
    value = source.read_text(encoding="utf-8-sig").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(value) > 4096:
        raise ValueError("Unexpected login QR content")
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(value)
    qr.make(fit=True)
    with output.open("wb") as stream:
        qr.make_image(image_factory=PyPNGImage).save(stream)
    (root / ".state" / "napcat-login.html").write_text('''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>汐 · 独立测试 QQ 登录</title>
<style>body{font:18px/1.7 system-ui;max-width:680px;margin:40px auto;text-align:center;color:#173747}img{max-width:90%;image-rendering:pixelated}</style>
<h1>汐 · 独立测试 QQ 登录</h1>
<p>此二维码由本机新建的 qq-character-napcat 实例提供。</p>
<p>请使用专门给汐准备的测试 QQ 扫码并确认，勿使用现有 AstrBot 的账号或主号。</p>
<img src="napcat-login.png" alt="独立测试 QQ 登录二维码">
<p>二维码失效时请回到 Codex 告诉我刷新。请勿分享本页。</p></html>''', encoding="utf-8")
    print(str(output))
finally:
    source.unlink(missing_ok=True)

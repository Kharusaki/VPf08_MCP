"""Инструмент «QR-код»: сохраняет PNG в workspace/."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

import qrcode

from ..config import SETTINGS

logger = logging.getLogger(__name__)

MAX_QR_TEXT = 2_000


def generate_qr(text: str, filename: str | None = None) -> str:
    """Сгенерировать QR-код и вернуть путь относительно workspace/."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Не указан текст для QR-кода.")
    if len(text) > MAX_QR_TEXT:
        raise ValueError(f"Слишком длинный текст для QR-кода (максимум {MAX_QR_TEXT} символов).")

    workspace = SETTINGS.workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)

    if filename:
        # Берём только имя файла, чтобы нельзя было выйти из workspace/
        name = Path(filename).name
        if not name.lower().endswith(".png"):
            name += ".png"
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"qr_{stamp}_{uuid.uuid4().hex[:6]}.png"

    target = (workspace / name).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError("Имя файла не должно выходить за пределы workspace/.") from exc

    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    image.save(target)

    logger.info("Создан QR-код: %s", target)
    return str(target.relative_to(workspace.resolve()))

"""Materialize quoted image references under alicedev's plugin data root."""

from __future__ import annotations

import base64
import binascii
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Sequence
from urllib.parse import unquote, urlparse

import aiohttp

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_DEFAULT_MIME = "image/png"
_MIME_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
_DATA_URI = re.compile(r"^data:(?P<mime>image/[a-z0-9.+-]+)?;base64,(?P<data>.*)$", re.I | re.S)


class ImageDownloadError(RuntimeError):
    """A quoted image could not be safely downloaded or copied."""


def _extension(mime_type: str, source: str = "") -> str:
    normalized = mime_type.split(";", 1)[0].strip().lower()
    if normalized in _MIME_EXTENSIONS:
        return _MIME_EXTENSIONS[normalized]
    suffix = Path(urlparse(source).path).suffix.lower()
    if suffix in {".gif", ".jpeg", ".jpg", ".png", ".webp", ".bmp"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".png"


def _content_type_for_path(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed if guessed and guessed.startswith("image/") else _DEFAULT_MIME


def _ensure_image_bytes(data: bytes, mime_type: str) -> None:
    if not data:
        raise ImageDownloadError("image response was empty")
    # Reject obvious non-image responses before they are persisted. Content-type
    # remains authoritative for formats without a short, stable magic header.
    if not mime_type.lower().startswith("image/"):
        raise ImageDownloadError(f"unsupported image content type: {mime_type}")
    signatures = (
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff",
        b"GIF87a",
        b"GIF89a",
        b"RIFF",
        b"BM",
    )
    if not data.startswith(signatures):
        raise ImageDownloadError("image response has no recognized image signature")


def _new_destination(root: Path, mime_type: str, source: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{uuid.uuid4().hex}{_extension(mime_type, source)}"


def _decode_data_uri(reference: str) -> tuple[bytes, str] | None:
    match = _DATA_URI.match(reference.strip())
    if not match:
        return None
    try:
        data = base64.b64decode(match.group("data"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ImageDownloadError("invalid base64 image data URI") from exc
    return data, (match.group("mime") or _DEFAULT_MIME).lower()


def _local_path(reference: str) -> Path | None:
    parsed = urlparse(reference)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme:
        return None
    path = Path(reference).expanduser()
    return path if path.is_file() else None


async def _fetch_http(reference: str, max_bytes: int) -> tuple[bytes, str]:
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(reference, allow_redirects=True) as response:
                response.raise_for_status()
                mime_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if not mime_type.startswith("image/"):
                    raise ImageDownloadError(
                        f"unsupported image content type: {mime_type or 'missing'}"
                    )
                length = response.headers.get("Content-Length")
                try:
                    declared_length = int(length) if length else None
                except ValueError as exc:
                    raise ImageDownloadError("invalid image content length") from exc
                if declared_length is not None and declared_length > max_bytes:
                    raise ImageDownloadError("image exceeds size limit")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ImageDownloadError("image exceeds size limit")
                    chunks.append(chunk)
                return b"".join(chunks), mime_type
    except aiohttp.ClientError as exc:
        raise ImageDownloadError(f"image download failed: {reference}") from exc


async def download_quoted_images(
    references: Sequence[str], destination: Path, *, max_bytes: int = _MAX_IMAGE_BYTES
) -> list[Path]:
    """Download/copy quoted image references into ``destination``.

    Returned paths are newly-created files under ``destination`` and can be
    serialized in the ``images`` JSON column. Data-URI conversion at card render
    time keeps the t2i sidecar independent of the plugin container filesystem.
    """

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    root = Path(destination).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stored: list[Path] = []
    for reference in references:
        if not reference or not reference.strip():
            continue
        ref = reference.strip()
        decoded = _decode_data_uri(ref)
        source_path = _local_path(ref) if decoded is None else None
        if decoded is not None:
            data, mime_type = decoded
        elif source_path is not None:
            try:
                if source_path.stat().st_size > max_bytes:
                    raise ImageDownloadError("local image exceeds size limit")
                data = source_path.read_bytes()
            except OSError as exc:
                raise ImageDownloadError(f"cannot read quoted image: {source_path}") from exc
            mime_type = _content_type_for_path(source_path)
        elif urlparse(ref).scheme in {"http", "https"}:
            data, mime_type = await _fetch_http(ref, max_bytes)
        else:
            raise ImageDownloadError(f"unsupported image reference: {ref}")
        if len(data) > max_bytes:
            raise ImageDownloadError("image exceeds size limit")
        _ensure_image_bytes(data, mime_type)
        output = _new_destination(root, mime_type, ref)
        try:
            output.write_bytes(data)
        except OSError as exc:
            raise ImageDownloadError(f"cannot write quoted image: {output}") from exc
        stored.append(output)
    return stored


def image_data_uri(path: Path) -> str:
    """Encode a stored image for use in a t2i HTML template."""

    image_path = Path(path)
    mime_type = _content_type_for_path(image_path)
    if not mime_type.startswith("image/"):
        raise ImageDownloadError(f"unsupported stored image type: {image_path}")
    try:
        payload = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise ImageDownloadError(f"cannot read stored image: {image_path}") from exc
    return f"data:{mime_type};base64,{payload}"

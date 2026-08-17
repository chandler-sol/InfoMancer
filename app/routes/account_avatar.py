from __future__ import annotations

import html
import os
import tempfile
import zlib
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import auth as auth_module
from .context import RouteContext


# Keep the built-in choices lightweight and portable. The same symbolic language is
# rendered by the profile picker and by the small account avatar used throughout UI.
PROFILE_ICON_SYMBOLS = {
    "film": "◆",
    "television": "▣",
    "star": "★",
    "library": "▤",
    "disc": "◎",
    "camera": "▰",
    "headphones": "∩",
    "folder": "▱",
    "server": "≡",
    "heart": "♥",
    "clapperboard": "▥",
}
PROFILE_ICON_CHOICES = {"initials", "custom", *PROFILE_ICON_SYMBOLS}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_EDGE = 256


# AuthService deliberately validates profile_icon against its shared PROFILE_ICONS
# set. Extend that shared object during router import so existing profile/create-user
# flows accept the richer icon vocabulary without duplicating account update logic.
auth_module.PROFILE_ICONS.update(PROFILE_ICON_CHOICES)


def _profile_symbol(user) -> str:
    icon = user.profile_icon if hasattr(user, "profile_icon") else user["profile_icon"]
    if icon in PROFILE_ICON_SYMBOLS:
        return PROFILE_ICON_SYMBOLS[icon]
    name = user.display_name if hasattr(user, "display_name") else user["display_name"]
    return (str(name).strip()[:1] or "?").upper()


# AuthUser.symbol resolves this module-global function at access time, so installing
# the extended symbol renderer keeps legacy templates and admin surfaces consistent.
auth_module.profile_symbol = _profile_symbol


def _avatar_directory(settings) -> Path:
    directory = Path(settings.database).resolve().parent / "profile-avatars"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory


def _avatar_path(settings, user_id: int) -> Path:
    return _avatar_directory(settings) / f"{int(user_id)}.png"


def _validate_canvas_png(payload: bytes) -> str:
    """Accept only the small, browser-reencoded PNG produced by profile.js."""
    if not payload or len(payload) > MAX_AVATAR_BYTES:
        return "The processed profile image must be no larger than 2 MB."
    if not payload.startswith(PNG_SIGNATURE):
        return "The processed profile image is not a valid PNG."

    position = len(PNG_SIGNATURE)
    seen_ihdr = False
    seen_iend = False
    idat_parts: list[bytes] = []
    color_type = -1
    width = height = 0

    while position + 12 <= len(payload):
        length = int.from_bytes(payload[position:position + 4], "big")
        chunk_type = payload[position + 4:position + 8]
        data_start = position + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if length > MAX_AVATAR_BYTES or crc_end > len(payload):
            return "The processed profile image has an invalid PNG structure."
        chunk_data = payload[data_start:data_end]
        expected_crc = int.from_bytes(payload[data_end:crc_end], "big")
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(chunk_data, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            return "The processed profile image failed its integrity check."

        if chunk_type == b"IHDR":
            if seen_ihdr or length != 13 or position != len(PNG_SIGNATURE):
                return "The processed profile image has an invalid PNG header."
            seen_ihdr = True
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
            compression, filtering, interlace = chunk_data[10:13]
            if (
                width != AVATAR_EDGE or height != AVATAR_EDGE
                or bit_depth != 8 or color_type not in {2, 6}
                or compression != 0 or filtering != 0 or interlace != 0
            ):
                return "Profile images must be reprocessed to a 256 × 256 RGB/RGBA PNG."
        elif chunk_type == b"IDAT":
            if not seen_ihdr or seen_iend:
                return "The processed profile image has invalid image data."
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if length != 0 or not seen_ihdr or seen_iend:
                return "The processed profile image has an invalid ending."
            seen_iend = True
            position = crc_end
            break
        elif chunk_type and 65 <= chunk_type[0] <= 90:
            # Reject unknown critical chunks. Browser canvas PNGs do not need them.
            return "The processed profile image contains an unsupported PNG feature."

        position = crc_end

    if not seen_ihdr or not seen_iend or position != len(payload) or not idat_parts:
        return "The processed profile image is incomplete."

    channels = 4 if color_type == 6 else 3
    expected_length = height * (1 + width * channels)
    try:
        decoded = zlib.decompress(b"".join(idat_parts))
    except zlib.error:
        return "The processed profile image could not be decoded."
    if len(decoded) != expected_length:
        return "The processed profile image contains an unexpected amount of pixel data."
    return ""


def _svg_avatar(user) -> str:
    symbol = _profile_symbol(user)
    safe_symbol = html.escape(symbol)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
        '<rect width="256" height="256" rx="128" fill="#b9f542"/>'
        f'<text x="128" y="139" text-anchor="middle" dominant-baseline="middle" '
        'font-family="Inter,Segoe UI,sans-serif" font-size="112" font-weight="800" fill="#0b1009">'
        f'{safe_symbol}</text></svg>'
    )


async def _read_small_body(request: Request) -> tuple[bytes, str]:
    length_text = request.headers.get("content-length", "").strip()
    if length_text.isdigit() and int(length_text) > MAX_AVATAR_BYTES:
        return b"", "The processed profile image must be no larger than 2 MB."
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_AVATAR_BYTES:
            return b"", "The processed profile image must be no larger than 2 MB."
        chunks.append(chunk)
    return b"".join(chunks), ""


def build_router(ctx: RouteContext):
    router = APIRouter()
    settings = ctx.live("settings")

    @router.get("/account/avatar/current")
    def current_profile_avatar(request: Request):
        user = getattr(request.state, "user", None)
        if not user or int(getattr(user, "id", 0) or 0) <= 0:
            return Response(status_code=404)
        custom_path = _avatar_path(settings, user.id)
        preview = request.query_params.get("preview") == "1"
        if custom_path.is_file() and (preview or user.profile_icon == "custom"):
            return FileResponse(
                custom_path,
                media_type="image/png",
                headers={"Cache-Control": "private, no-store"},
            )
        return Response(
            _svg_avatar(user),
            media_type="image/svg+xml",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/account/profile/avatar")
    async def upload_profile_avatar(request: Request):
        user = getattr(request.state, "user", None)
        if not user or int(getattr(user, "id", 0) or 0) <= 0:
            return JSONResponse({"error": "Custom profile images require an account."}, status_code=400)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if content_type != "image/png":
            return JSONResponse(
                {"error": "InfoMancer only stores the sanitized PNG produced by the profile editor."},
                status_code=415,
            )
        payload, read_error = await _read_small_body(request)
        if read_error:
            return JSONResponse({"error": read_error}, status_code=413)
        error = _validate_canvas_png(payload)
        if error:
            return JSONResponse({"error": error}, status_code=400)

        target = _avatar_path(settings, user.id)
        temporary = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=f".{user.id}-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, target)
            temporary = ""
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

        return JSONResponse({
            "ok": True,
            "avatar_url": f"/account/avatar/current?preview=1&v={target.stat().st_mtime_ns}",
        })

    return router, {
        "current_profile_avatar": current_profile_avatar,
        "upload_profile_avatar": upload_profile_avatar,
    }

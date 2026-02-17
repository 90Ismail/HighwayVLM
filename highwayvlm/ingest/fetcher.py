import re
from urllib.parse import urljoin, urlparse

import requests

from highwayvlm.settings import (
    FRAMES_DIR,
    get_image_url_regex,
    get_camera_metadata_url_template,
    get_snapshot_url_template,
    get_request_timeout_seconds,
)


def _extension_from_content_type(content_type):
    content_type = (content_type or "").lower()
    if "png" in content_type:
        return "png"
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "gif" in content_type:
        return "gif"
    return "jpg"


def _is_viewer_url(url):
    if not url:
        return False
    lowered = url.lower()
    return "list/cameras" in lowered or "#media/camera" in lowered or "/media/camera/" in lowered


def _build_snapshot_url(camera):
    url = camera.get("snapshot_url")
    if url:
        return url
    template = get_snapshot_url_template()
    if not template:
        raise ValueError("snapshot_url missing; set SNAPSHOT_URL_TEMPLATE or camera snapshot_url")
    camera_id = camera.get("camera_id")
    if not camera_id:
        raise ValueError("camera_id is required to build snapshot URL")
    return template.format(camera_id=camera_id)


def _looks_like_image_url(value, key_hint=None):
    if not value:
        return False
    lowered = value.lower()
    if re.search(r"\.(?:jpg|jpeg|png|gif)(?:\?|$)", lowered):
        return not _is_viewer_url(lowered)
    if not (lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("/")):
        return False
    if _is_viewer_url(lowered):
        return False
    if key_hint and ("image" in key_hint or "snapshot" in key_hint):
        return True
    return "image" in lowered or "snapshot" in lowered


def _base_origin(url):
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _extract_image_url_from_payload(payload, base_url):
    if payload is None:
        return None
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(value, str):
                    key_lower = key.lower()
                    if _looks_like_image_url(value, key_lower):
                        return urljoin(base_url, value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            for value in item:
                if isinstance(value, str):
                    if _looks_like_image_url(value):
                        return urljoin(base_url, value)
                else:
                    stack.append(value)
    return None


def _fetch_metadata_image_url(camera):
    template = get_camera_metadata_url_template()
    if not template:
        return None
    camera_id = camera.get("camera_id")
    if not camera_id:
        return None
    url = template.format(camera_id=camera_id)
    response = requests.get(
        url,
        timeout=get_request_timeout_seconds(),
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except Exception:
        return None
    return _extract_image_url_from_payload(payload, url)


def _fetch_public_camera_metadata_url(camera, base_url):
    camera_id = camera.get("camera_id")
    origin = _base_origin(base_url)
    if not camera_id or not origin:
        return None
    candidates = [
        f"{origin}/api/v2/cameras/{camera_id}",
        f"{origin}/api/v1/cameras/{camera_id}",
        f"{origin}/api/cameras/{camera_id}",
        f"{origin}/api/v2/cameras?ids={camera_id}",
        f"{origin}/api/v1/cameras?ids={camera_id}",
        f"{origin}/api/cameras?ids={camera_id}",
        f"{origin}/api/v2/cameras?camera_ids={camera_id}",
        f"{origin}/api/v1/cameras?camera_ids={camera_id}",
        f"{origin}/api/cameras?camera_ids={camera_id}",
        f"{origin}/api/cameras?cameraId={camera_id}",
    ]
    for url in candidates:
        try:
            response = requests.get(url, timeout=get_request_timeout_seconds())
            response.raise_for_status()
        except Exception:
            continue
        try:
            payload = response.json()
        except Exception:
            continue
        image_url = _extract_image_url_from_payload(payload, url)
        if image_url:
            return image_url
    return None


def _extract_image_url_from_html(html, base_url):
    if not html:
        return None
    custom = get_image_url_regex()
    patterns = []
    if custom:
        patterns.append(re.compile(custom, re.IGNORECASE | re.DOTALL))
    patterns.extend(
        [
            re.compile(
                r'"(?:imageUrl|snapshotUrl|snapshot_url|image_url)"\s*:\s*"([^"]+)"',
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:imageUrl|snapshotUrl|snapshot_url|image_url)\s*=\s*\"([^\"]+)\"",
                re.IGNORECASE,
            ),
            re.compile(r"src=['\"]([^'\"]+\.(?:jpg|jpeg|png))['\"]", re.IGNORECASE),
            re.compile(r"https?://[^\"'\s>]+?\.(?:jpg|jpeg|png)", re.IGNORECASE),
        ]
    )
    for pattern in patterns:
        match = pattern.search(html)
        if not match:
            continue
        candidate = match.group(1) if match.lastindex else match.group(0)
        return urljoin(base_url, candidate)
    return None


def fetch_snapshot_bytes(camera):
    url = _build_snapshot_url(camera)
    response = requests.get(
        url,
        timeout=get_request_timeout_seconds(),
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type")
    content_type_lower = (content_type or "").lower()
    if content_type_lower.startswith("image/"):
        return response.content, content_type
    if "application/json" in content_type_lower or "text/json" in content_type_lower:
        try:
            payload = response.json()
        except Exception:
            payload = None
        image_url = _extract_image_url_from_payload(payload, url)
        if not image_url:
            raise ValueError("snapshot_metadata_missing_image_url")
        image_response = requests.get(
            image_url,
            timeout=get_request_timeout_seconds(),
        )
        image_response.raise_for_status()
        image_type = image_response.headers.get("Content-Type")
        if image_type and not image_type.lower().startswith("image/"):
            raise ValueError(f"snapshot_not_image: content_type={image_type}")
        return image_response.content, image_type
    if _is_viewer_url(url) or (content_type and "text/html" in content_type.lower()):
        image_url = _extract_image_url_from_html(response.text, url)
        if not image_url:
            image_url = _fetch_metadata_image_url(camera)
        if not image_url:
            image_url = _fetch_public_camera_metadata_url(camera, url)
        if not image_url:
            raise ValueError(
                "snapshot_url must be a direct image URL or metadata template; set SNAPSHOT_URL_TEMPLATE, "
                "CAMERA_METADATA_URL_TEMPLATE, or IMAGE_URL_REGEX"
            )
        image_response = requests.get(
            image_url,
            timeout=get_request_timeout_seconds(),
        )
        image_response.raise_for_status()
        image_type = image_response.headers.get("Content-Type")
        if image_type and not image_type.lower().startswith("image/"):
            raise ValueError(f"snapshot_not_image: content_type={image_type}")
        return image_response.content, image_type
    if content_type and not content_type.lower().startswith("image/"):
        raise ValueError(f"snapshot_not_image: content_type={content_type}")
    return response.content, content_type


def save_snapshot(camera_id, image_bytes, content_type, captured_at):
    ext = _extension_from_content_type(content_type)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    path = FRAMES_DIR / f"{camera_id}_{captured_at}.{ext}"
    path.write_bytes(image_bytes)
    return path

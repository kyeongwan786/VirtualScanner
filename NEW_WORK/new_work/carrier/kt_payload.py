from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass


class KtPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class KtImagePayload:
    image_key: str
    size_key: str
    image_bytes: bytes
    declared_size: int


def parse_kt_image_fragment(text: str) -> KtImagePayload:
    """Parse the observed comma-prefixed KT image property fragments.

    The two supplied files are not standalone JSON documents. This parser
    extracts only their known image and byte-count properties and validates
    that the declared count matches the decoded payload.
    """
    if not isinstance(text, str):
        raise KtPayloadError("KT 이미지 조각은 문자열이어야 합니다.")

    image_matches = list(
        re.finditer(r'"(imageData|image)"\s*:\s*"([^"\r\n]*)"', text)
    )
    if len(image_matches) != 1:
        raise KtPayloadError("image 또는 imageData 필드를 정확히 하나 찾지 못했습니다.")

    image_match = image_matches[0]
    image_key, encoded = image_match.groups()
    size_key = "imageDataSize" if image_key == "imageData" else "imageSize"
    size_matches = list(
        re.finditer(rf'"{re.escape(size_key)}"\s*:\s*(\d+)', text)
    )
    if len(size_matches) != 1:
        raise KtPayloadError(f"{size_key} 필드를 정확히 하나 찾지 못했습니다.")

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KtPayloadError(f"{image_key}가 올바른 Base64가 아닙니다.") from exc
    declared_size = int(size_matches[0].group(1))
    if declared_size != len(image_bytes):
        raise KtPayloadError(
            f"{size_key}={declared_size}, 디코딩된 바이트={len(image_bytes)}"
        )
    return KtImagePayload(
        image_key=image_key,
        size_key=size_key,
        image_bytes=image_bytes,
        declared_size=declared_size,
    )

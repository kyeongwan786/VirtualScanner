from __future__ import annotations

import base64
from collections.abc import Mapping
from urllib.parse import quote_plus


SK_REPORT_TEXT_KEYS = (
    "kaitIdType",
    "recogSubCd",
    "idType",
    "ctzCorpNum",
    "custNm",
    "isueDt",
    "drvLicenIsueNum",
    "isuePlc",
    "nationClCd",
    "addr",
    "mskPos",
)
SK_REPORT_KEYS = (*SK_REPORT_TEXT_KEYS, "base64DATA", "idCrop")
SK_VETERAN_REPORT_KEYS = (
    *SK_REPORT_TEXT_KEYS[:3],
    "wlfNum",
    *SK_REPORT_TEXT_KEYS[3:],
    "base64DATA",
    "idCrop",
)


class SkReportInputError(ValueError):
    pass


def render_sk_report(
    fields: Mapping[str, str],
    full_image_jpeg: bytes,
    id_crop_jpeg: bytes | None,
) -> bytes:
    """Render com_sk's ordered SK key/value report format.

    This function only serializes caller-provided, reviewed values and image
    bytes. It does not query/update a database, validate identity, or crop an
    image; those behaviors require separate evidence and integration.
    """
    missing = [key for key in SK_REPORT_TEXT_KEYS if key not in fields]
    if missing:
        raise SkReportInputError(f"필수 SK 필드가 없습니다: {', '.join(missing)}")
    invalid = [key for key in SK_REPORT_TEXT_KEYS if not isinstance(fields[key], str)]
    if invalid:
        raise SkReportInputError(
            f"SK 필드는 문자열이어야 합니다(None과 숫자는 빈 문자열로 바꾸지 않음): "
            f"{', '.join(invalid)}"
        )
    if not isinstance(full_image_jpeg, bytes) or not full_image_jpeg:
        raise SkReportInputError("base64DATA에 넣을 이미지 바이트가 비어 있습니다.")
    values = {key: fields[key] for key in SK_REPORT_TEXT_KEYS}
    if {values["kaitIdType"], values["recogSubCd"]} & {"1301", "1303"}:
        values["addr"] = ""
    for field in ("ctzCorpNum", "isueDt"):
        values[field] = "".join(
            char for char in values[field] if char.isdecimal()
        )
    values["wlfNum"] = "".join(
        char for char in fields.get("veteranNum", "") if char.isdecimal()
    )
    values["drvLicenIsueNum"] = "".join(
        char for char in values["drvLicenIsueNum"] if char.isdecimal()
    )
    values["base64DATA"] = base64.b64encode(full_image_jpeg).decode("ascii")
    text_keys = list(SK_REPORT_TEXT_KEYS)
    if fields["kaitIdType"] == "1302":
        text_keys.insert(3, "wlfNum")
    lines = [f"{key}={quote_plus(values[key])}" for key in text_keys]
    # Source includes idCrop only for resident/driver report codes, and only
    # when the crop file exists and can be encoded.
    if fields["kaitIdType"] in {"1001", "1101", "1102"} and id_crop_jpeg:
        lines.append(f"base64DATA={quote_plus(values['base64DATA'])}")
        lines.append(f"idCrop={quote_plus(base64.b64encode(id_crop_jpeg).decode('ascii'))}")
    else:
        lines.append(f"base64DATA={quote_plus(values['base64DATA'])}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")

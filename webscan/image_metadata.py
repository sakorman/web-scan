"""Keep a usable 35 mm focal-length prior across JPEG normalization."""

import math

from PIL import Image

EXIF_IFD = 34665
FOCAL_LENGTH_35MM = 41989


def focal_length_exif(image):
    """Return a fresh EXIF block containing only a valid equivalent focal length."""
    try:
        source = image.getexif()
        value = source.get_ifd(EXIF_IFD).get(
            FOCAL_LENGTH_35MM, source.get(FOCAL_LENGTH_35MM)
        )
        if isinstance(value, (str, bytes, bool)):
            return b""
        focal = float(value)
        # This EXIF field is an unsigned SHORT; zero means unknown.
        if (
            not math.isfinite(focal)
            or not focal.is_integer()
            or not 1 <= focal <= 65535
        ):
            return b""
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        ZeroDivisionError,
        OverflowError,
    ):
        return b""
    result = Image.Exif()
    result[EXIF_IFD] = {FOCAL_LENGTH_35MM: int(focal)}
    return result.tobytes()

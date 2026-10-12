"""Preserve capture order even when parallel uploads arrive out of order."""

import json
import re


def ordered_frames(rows):
    frames = [dict(row) for row in rows]
    groups = {}
    order = []
    for arrival, frame in enumerate(frames):
        try:
            metadata = json.loads(frame["metadata"])
        except (ValueError, TypeError):
            metadata = {}
        batch = metadata.get("upload_batch")
        index = metadata.get("capture_index")
        valid = (
            isinstance(batch, str)
            and re.fullmatch("[a-f0-9]{32}", batch)
            and type(index) is int
            and 0 <= index < 200
        )
        if valid:
            first = groups.setdefault(batch, arrival)
            order.append((first, index, arrival))
        else:
            order.append((arrival, 0, arrival))
    return [frames[key[2]] for key in sorted(order)]

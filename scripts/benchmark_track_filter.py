"""Experimental COLMAP seed filter; writes a separate model, never edits its source.

Reject entire points with multiple observations in one image. Preserve the exact
bytes and order of retained points and camera poses to isolate the intervention.
This is an experiment, not a default preprocessing or outlier classification.
"""
import argparse
import json
from pathlib import Path
import shutil
import struct


def take(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError('Truncated COLMAP binary')
    return data[offset:offset+size]


def point_records(data):
    count, = struct.unpack('<Q', take(data, 0, 8))
    offset = 8
    seen = set()
    for _ in range(count):
        header = take(data, offset, 51)
        pid, = struct.unpack_from('<Q', header)
        if pid in seen:
            raise ValueError('Duplicate point ID')
        seen.add(pid)
        length, = struct.unpack_from('<Q', header, 43)
        record = take(data, offset, 51 + 8*length)
        track = list(struct.iter_unpack('<II', record[51:]))
        yield pid, track, record
        offset += len(record)
    if offset != len(data):
        raise ValueError('Trailing COLMAP point bytes')


def filter_points(data):
    retained, rejected = [], []
    for pid, track, record in point_records(data):
        images = [iid for iid, _ in track]
        if len(images) != len(set(images)):
            rejected.append(pid)
        else:
            retained.append(record)
    return struct.pack('<Q', len(retained)) + b''.join(retained), rejected


def clear_associations(data, rejected):
    output = bytearray(data)
    count, = struct.unpack('<Q', take(data, 0, 8))
    offset = 8
    changed = 0
    rejected = set(rejected)
    for _ in range(count):
        take(data, offset, 64)  # image ID, quaternion, translation, camera ID
        offset += 64
        end = data.find(b'\0', offset)
        if end < 0:
            raise ValueError('Unterminated COLMAP image name')
        offset = end + 1
        length, = struct.unpack('<Q', take(data, offset, 8))
        offset += 8
        take(data, offset, 24*length)
        for i in range(length):
            position = offset + 24*i + 16
            pid, = struct.unpack_from('<q', data, position)
            if pid in rejected:
                struct.pack_into('<q', output, position, -1)
                changed += 1
        offset += 24*length
    if offset != len(data):
        raise ValueError('Trailing COLMAP image bytes')
    return bytes(output), changed


def prepare(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError('Choose a new destination outside the source model')
    points, rejected = filter_points((source/'points3D.bin').read_bytes())
    images, changed = clear_associations((source/'images.bin').read_bytes(), rejected)
    shutil.copytree(source, destination)
    (destination/'points3D.bin').write_bytes(points)
    (destination/'images.bin').write_bytes(images)
    kept, = struct.unpack_from('<Q', points)
    return dict(input_points=kept+len(rejected), retained_points=kept,
                removed_point_ids=rejected, cleared_observations=changed,
                retained_point_bytes_and_order_preserved=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(prepare(args.source, args.destination)))


if __name__ == '__main__':
    main()

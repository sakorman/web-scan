"""Offline spatial grouping of RGB-selected depth candidates; never inject points."""
import argparse
import itertools
import time
from pathlib import Path

import numpy as np

from scripts.benchmark_depth_overlap import checked_map, read
from scripts.benchmark_depth_prior import digest, save


def group_samples(world, z, radius, reverse=False):
    """Assign to a fixed leader, not transitive connected components."""
    world, z = np.asarray(world, dtype=float), np.asarray(z, dtype=float)
    n = len(world)
    if (world.shape != (n, 3) or z.shape != (n,) or not np.isfinite(world).all()
            or not np.isfinite(z).all() or np.any(z <= 0) or not 0 < radius < 1):
        raise ValueError('Invalid points, depths or radius')
    ids = np.empty(n, dtype=int)
    leaders = np.empty(n, dtype=int)
    size = 0
    order = range(n-1, -1, -1) if reverse else range(n)
    for i in order:
        refs = leaders[:size]
        distances = np.linalg.norm(world[refs] - world[i], axis=1)
        eligible = distances <= radius * np.minimum(z[refs], z[i])
        if eligible.any():
            group = int(np.argmin(np.where(eligible, distances, np.inf)))
        else:
            group = size
            leaders[size] = i
            size += 1
        ids[i] = group
    return ids, leaders[:size]


def diverse_three(center, cameras, degrees=1.):
    """Require three source centers with all three ray pairs separated."""
    if len(cameras) < 3:
        return False
    rays = np.asarray(cameras) - center
    norm = np.linalg.norm(rays, axis=1)
    if not np.isfinite(rays).all() or np.any(norm <= 0):
        return False
    rays /= norm[:, None]
    cosine = np.clip(rays @ rays.T, -1., 1.)
    enough = cosine <= np.cos(np.deg2rad(degrees))
    return any(enough[a, b] and enough[a, c] and enough[b, c]
               for a, b, c in itertools.combinations(range(len(cameras)), 3))


def summarize(world, z, source, centers, ids, leaders, sensor_world=None, sensor_z=None, sensor_distance=None):
    records = []
    for group, leader in enumerate(leaders):
        members = np.flatnonzero(ids == group)
        # Each source photo gets one nearest observation, chosen without sensor data.
        representatives = []
        for view in np.unique(source[members]):
            same = members[source[members] == view]
            distances = np.linalg.norm(world[same] - world[leader], axis=1)
            representatives.append(same[np.argmin(distances)])
        representatives = np.asarray(representatives, dtype=int)
        diverse = diverse_three(world[leader], centers[source[representatives]])
        record = {'leader': int(leader), 'samples': len(members), 'source_views': len(representatives),
                  'diverse_three': bool(diverse)}
        if sensor_world is not None:
            measurements = sensor_world[representatives]
            depths = sensor_z[representatives]
            valid = np.isfinite(measurements).all(axis=1) & np.isfinite(depths) & (depths > 0)
            good = np.zeros(len(valid), dtype=bool)
            good[valid] = (np.linalg.norm(measurements[valid] - world[leader], axis=1)
                          / depths[valid]) <= .01
            good &= sensor_distance[representatives] > .01
            record.update(sensor_views=int(valid.sum()), sensor_good_views=int(good.sum()),
                          sensor_diverse_three=bool(diverse_three(world[leader], centers[source[representatives[good]]])))
        records.append(record)
    diverse_rows = [r for r in records if r['diverse_three']]
    summary = {'samples': len(world), 'groups': len(leaders),
               'groups_by_views': {str(k): sum(r['source_views'] == k for r in records)
                                   for k in sorted({r['source_views'] for r in records})},
               'three_views': sum(r['source_views'] >= 3 for r in records),
               'diverse_three': len(diverse_rows)}
    if sensor_world is not None:
        summary.update(diverse_with_three_sensor_views=sum(r['sensor_views'] >= 3 for r in diverse_rows),
                       diverse_sensor_confirmed=sum(r['sensor_diverse_three'] for r in diverse_rows))
    return summary, records


def run(args):
    start = time.monotonic()
    manifest = read(args.samples / 'samples.json')
    rows = sorted(manifest['views'], key=lambda r: r['name'])
    if (len({r['name'] for r in rows}) != len(rows)
            or len({r['image_sha256'] for r in rows}) != len(rows)):
        raise ValueError('Repeated source photograph')
    args.output.mkdir(parents=True, exist_ok=False)
    chunks, sources, centers, sensor_modes = [], [], [], []
    for i, row in enumerate(rows):
        values = checked_map(args.samples, row)
        count = len(values['z'])
        if (values['world'].shape != (count, 3)
                or any(values[k].shape != (count,) for k in
                       ('grid_index', 'image_distance', 'space_distance', 'conflict'))
                or values['conflict'].dtype != bool
                or len(np.unique(values['grid_index'])) != count):
            raise ValueError('Invalid sample shapes or pixel identities')
        if (not np.isfinite(values['image_distance']).all()
                or not np.isfinite(values['space_distance']).all()):
            raise ValueError('Invalid distance fields')
        if 'sensor_world' in values and (values['sensor_world'].shape != (count, 3)
                or values['sensor_z'].shape != (count,) or values['sensor_distance'].shape != (count,)):
            raise ValueError('Invalid sensor sample shapes')
        keep = ((values['image_distance'] > .01) & (values['space_distance'] > .01)
                & ~values['conflict'])
        order = np.argsort(values['grid_index'][keep], kind='stable')
        chunks.append({key: v[keep][order] for key, v in values.items()})
        sources.append(np.full(keep.sum(), i, dtype=int))
        centers.append(row['center'])
        sensor_modes.append('sensor_world' in values)
    if not rows or len(set(sensor_modes)) != 1:
        raise ValueError('Empty or mixed sensor domains')
    values = {key: np.concatenate([c[key] for c in chunks]) for key in chunks[0]}
    expected = manifest['overlap']['pooled']['0.01']['both_far_no_front_conflict']
    if len(values['z']) != expected:
        raise ValueError('Candidate counts differ from overlap baseline')
    sources, centers = np.concatenate(sources), np.asarray(centers)
    if centers.shape != (len(rows), 3) or not np.isfinite(centers).all():
        raise ValueError('Invalid camera centers')
    output = {'samples_sha256': digest(args.samples/'samples.json'),
              'source_views': [r['name'] for r in rows], 'variants': []}
    for radius in (.005, .01, .02):
        for reverse in (False, True):
            ids, leaders = group_samples(values['world'], values['z'], radius, reverse)
            summary, records = summarize(values['world'], values['z'], sources, centers, ids, leaders,
                                         values.get('sensor_world'), values.get('sensor_z'), values.get('sensor_distance'))
            name = f"{radius}-{'reverse' if reverse else 'forward'}"
            path = args.output / (name + '.npz')
            np.savez_compressed(path, ids=ids, leaders=leaders, source=sources,
                                grid_index=values['grid_index'])
            output['variants'].append(dict(radius=radius, order=name.split('-')[1], summary=summary,
                                           map=path.name, map_sha256=digest(path), groups=records))
    output['seconds'] = time.monotonic() - start
    save(args.output/'groups.json', output)
    print([(r['radius'], r['order'], r['summary']) for r in output['variants']], flush=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())

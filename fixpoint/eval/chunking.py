"""Chunk planning and disk accounting for large grading runs.

The binding constraint on grading Lite-300 locally is not compute — it is
Docker image traffic. Every instance has its own ~3.5GB image, but images
share layers: one base for everything, one env layer per (repo, version), and
only a thin instance layer is unique. The 217 gradable Lite instances span
just 57 (repo, version) env images, so ordering chunks by that key means each
env layer downloads once instead of once per chunk it straddles.

Pruning strategy follows from the same math: dropping images after EVERY chunk
(the first implementation) re-downloads shared layers constantly. Instead we
prune only when the image store crosses a threshold — set below Docker
Desktop's VM capacity, which is a separate, smaller disk than the host (the VM
filling up is what killed the first grading attempt with a misleading
"no matching manifest" error).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

# Docker Desktop's VM disk is ~100GB here; images crossing this threshold
# trigger a prune. Headroom matters: a chunk in flight can add ~15GB.
DEFAULT_PRUNE_THRESHOLD_GB = 55.0

_SIZE_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B)$", re.IGNORECASE)
_UNIT_GB = {"B": 1e-9, "KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}


def parse_docker_size(text: str) -> float:
    """'57.25GB' / '663MB' / '24.58kB' -> gigabytes. Unparseable -> 0.0
    (a failed reading must never block grading; worst case we prune late)."""
    m = _SIZE_RE.match(text.strip())
    if not m:
        return 0.0
    value, unit = float(m.group(1)), m.group(2).upper()
    return value * _UNIT_GB.get(unit, 0.0)


def plan_chunks(instance_ids: Sequence[str],
                meta: Mapping[str, tuple[str, str]],
                max_chunk: int = 15) -> list[list[str]]:
    """Group instances by (repo, version) so env layers download once.

    Deterministic: groups ordered by (repo, version), ids sorted within a
    group, oversized groups split at max_chunk. Instances missing from `meta`
    are collected into a final catch-all chunk rather than dropped — an id we
    cannot classify still deserves grading.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    unknown: list[str] = []
    for iid in instance_ids:
        if iid in meta:
            groups.setdefault(meta[iid], []).append(iid)
        else:
            unknown.append(iid)

    chunks: list[list[str]] = []
    for key in sorted(groups):
        ids = sorted(groups[key])
        chunks.extend(ids[i:i + max_chunk] for i in range(0, len(ids), max_chunk))
    if unknown:
        chunks.extend(sorted(unknown)[i:i + max_chunk] for i in range(0, len(unknown), max_chunk))
    return chunks

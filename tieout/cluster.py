"""One-dimensional clustering and dominance statistics.

Shared by the learning engine, which clusters observations to find conventions,
and by the layout rules, which cluster shape edges so a row of five cards yields
one alignment finding rather than ten.

Kept as a standalone module rather than living under ``learn/`` so the rules can
use it without importing the learning engine, which would invert the intended
dependency direction.

Everything here is deterministic. Same input, same clusters, same order, every
run, on every machine -- which is a requirement, not a nicety: a QA tool that
reports different findings on the same file is worse than no tool.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Cluster:
    """A group of values judged to be the same value within a tolerance."""

    #: Weighted mean of the members.
    centre: float
    #: The value carrying the most weight, which is what gets emitted as the
    #: expected value. A mean of 872 and 840 is 856, a position nothing occupies.
    mode: float
    members: tuple[float, ...]
    weight: float
    support: int

    @property
    def spread(self) -> float:
        if len(self.members) < 2:
            return 0.0
        return max(self.members) - min(self.members)

    @property
    def stdev(self) -> float:
        if len(self.members) < 2:
            return 0.0
        return statistics.stdev(self.members)


@dataclass
class Dominance:
    """The outcome of asking "is there a dominant value here?"."""

    #: Clusters ordered by descending weight.
    clusters: tuple[Cluster, ...]
    total_weight: float
    total_support: int

    @property
    def top(self) -> Cluster | None:
        return self.clusters[0] if self.clusters else None

    @property
    def top_share(self) -> float:
        if not self.clusters or self.total_weight <= 0:
            return 0.0
        return self.clusters[0].weight / self.total_weight

    @property
    def distinct(self) -> int:
        return len(self.clusters)

    def share_of(self, index: int) -> float:
        if index >= len(self.clusters) or self.total_weight <= 0:
            return 0.0
        return self.clusters[index].weight / self.total_weight

    @property
    def entropy(self) -> float:
        """Shannon entropy in bits over the cluster weights.

        Used to separate "several real conventions" from "no convention at all".
        A key with twelve equally-weighted values is noise, whatever its top
        share happens to be.
        """
        if self.total_weight <= 0:
            return 0.0
        total = 0.0
        for cluster in self.clusters:
            share = cluster.weight / self.total_weight
            if share > 0:
                total -= share * math.log2(share)
        return total


def cluster_values(
    values: Iterable[float],
    tolerance: float,
    weights: Sequence[float] | None = None,
) -> list[Cluster]:
    """Cluster scalars whose difference is within ``tolerance``.

    Single-linkage over sorted values: a value joins the current cluster when it
    is within tolerance of the cluster's current centre, not merely of its
    nearest neighbour. Chaining on nearest-neighbour would let a run of shapes
    each 2pt from the last collapse into one cluster spanning 40pt, which would
    then be emitted as a grid line nothing sits on.

    A tolerance of zero means exact grouping, which is what font sizes need.
    """
    items = list(values)
    if not items:
        return []
    if weights is None:
        weights = [1.0] * len(items)
    if len(weights) != len(items):
        raise ValueError("weights must be the same length as values")

    ordered = sorted(zip(items, weights, strict=True), key=lambda pair: pair[0])

    groups: list[list[tuple[float, float]]] = [[ordered[0]]]
    for value, weight in ordered[1:]:
        current = groups[-1]
        centre = sum(v * w for v, w in current) / max(
            1e-12, sum(w for _, w in current)
        )
        if abs(value - centre) <= tolerance:
            current.append((value, weight))
        else:
            groups.append([(value, weight)])

    clusters: list[Cluster] = []
    for group in groups:
        members = tuple(v for v, _ in group)
        total_weight = sum(w for _, w in group)
        centre = (
            sum(v * w for v, w in group) / total_weight
            if total_weight > 0
            else members[0]
        )
        clusters.append(
            Cluster(
                centre=centre,
                mode=_weighted_mode(group),
                members=members,
                weight=total_weight,
                support=len(members),
            )
        )
    clusters.sort(key=lambda c: (-c.weight, c.centre))
    return clusters


def _weighted_mode(group: Sequence[tuple[float, float]]) -> float:
    """The individual value carrying the most weight, ties broken by value."""
    totals: dict[float, float] = {}
    for value, weight in group:
        totals[value] = totals.get(value, 0.0) + weight
    return max(sorted(totals), key=lambda value: totals[value])


def dominance(
    values: Iterable[float],
    tolerance: float,
    weights: Sequence[float] | None = None,
) -> Dominance:
    """Cluster and summarise, ready for classification."""
    clusters = cluster_values(values, tolerance, weights)
    return Dominance(
        clusters=tuple(clusters),
        total_weight=sum(c.weight for c in clusters),
        total_support=sum(c.support for c in clusters),
    )


@dataclass
class CategoricalDominance:
    """Dominance over non-numeric values, which cannot be clustered."""

    counts: dict[str, float] = field(default_factory=dict)
    supports: dict[str, int] = field(default_factory=dict)

    @property
    def total_weight(self) -> float:
        return sum(self.counts.values())

    @property
    def total_support(self) -> int:
        return sum(self.supports.values())

    @property
    def ordered(self) -> list[tuple[str, float]]:
        return sorted(self.counts.items(), key=lambda item: (-item[1], item[0]))

    @property
    def top(self) -> tuple[str, float] | None:
        ordered = self.ordered
        return ordered[0] if ordered else None

    @property
    def top_share(self) -> float:
        top = self.top
        if top is None or self.total_weight <= 0:
            return 0.0
        return top[1] / self.total_weight

    @property
    def distinct(self) -> int:
        return len(self.counts)

    def share_of(self, key: str) -> float:
        if self.total_weight <= 0:
            return 0.0
        return self.counts.get(key, 0.0) / self.total_weight

    @property
    def entropy(self) -> float:
        if self.total_weight <= 0:
            return 0.0
        total = 0.0
        for weight in self.counts.values():
            share = weight / self.total_weight
            if share > 0:
                total -= share * math.log2(share)
        return total


def categorical_dominance(
    items: Iterable[tuple[str, float]],
) -> CategoricalDominance:
    """Tally ``(value, weight)`` pairs."""
    result = CategoricalDominance()
    for value, weight in items:
        result.counts[value] = result.counts.get(value, 0.0) + weight
        result.supports[value] = result.supports.get(value, 0) + 1
    return result


def group_by(
    items: Iterable[T], key: Callable[[T], str]
) -> dict[str, list[T]]:
    """Stable grouping helper, so derivers do not each reimplement it."""
    out: dict[str, list[T]] = {}
    for item in items:
        out.setdefault(key(item), []).append(item)
    return out


def derive_tolerance(
    spread: float, *, floor: float, factor: float = 1.5, ceiling: float | None = None
) -> float:
    """Turn an observed spread into a rule tolerance.

    ``max(observed_spread * 1.5, floor)`` per section 8.2. A client with sloppy
    but acceptable logo placement gets a looser rule automatically rather than
    forty false positives, and a client with millimetre discipline gets a tight
    one.
    """
    value = max(spread * factor, floor)
    if ceiling is not None:
        value = min(value, ceiling)
    return round(value, 2)


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Linear-interpolated percentile. ``fraction`` is 0..1.

    Written out rather than taken from ``statistics.quantiles`` because that
    function's behaviour on very short sequences is not what margin derivation
    needs: a two-slide archetype must still yield a number.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def floor_to(value: float, unit: float) -> float:
    """Round down to the nearest ``unit``. Margins are rounded down so the
    derived margin is never tighter than the tightest thing observed."""
    if unit <= 0:
        return value
    return math.floor(value / unit) * unit


def round_to(value: float, unit: float) -> float:
    if unit <= 0:
        return value
    return round(value / unit) * unit


def coverage_share(
    lines: Sequence[float], half_width: float, extent: float
) -> float:
    """The share of ``[0, extent]`` within ``half_width`` of some line.

    This is how a set of learned positions is asked whether it still carries
    information. A grid line is only worth checking against if being near it
    distinguishes a shape from one placed at random; once the bands around the
    lines cover most of the canvas, "this edge just misses a grid line" is true
    of almost anywhere a shape could be put, and the rule that reads it is
    reporting coincidence.

    Overlapping bands are merged rather than summed, and bands are clipped to
    the canvas, so the result is a real share between 0 and 1 rather than a
    count dressed up as one.
    """
    if extent <= 0 or half_width <= 0 or not lines:
        return 0.0
    bands = sorted(
        (max(0.0, line - half_width), min(extent, line + half_width))
        for line in lines
    )
    covered = 0.0
    start, end = bands[0]
    for low, high in bands[1:]:
        if low > end:
            covered += max(0.0, end - start)
            start, end = low, high
        else:
            end = max(end, high)
    covered += max(0.0, end - start)
    return min(1.0, covered / extent)

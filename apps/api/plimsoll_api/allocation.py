"""How many generators, and how many virtual users on each.

JMeter allocates an OS thread per virtual user, so `max_vus_per_generator` is a
real ceiling rather than a formality, and the arithmetic here decides whether a
run is honest about the load it produced.
"""

from __future__ import annotations

import math


class CapacityError(Exception):
    """The pool cannot supply what the test asks for."""


def allocate(*, total_users: int, max_generators: int, max_vus_per_generator: int) -> list[int]:
    if total_users < 1:
        raise CapacityError("A run needs at least one virtual user.")
    if max_generators < 1 or max_vus_per_generator < 1:
        raise CapacityError("The pool declares no capacity.")

    ceiling = max_generators * max_vus_per_generator
    if total_users > ceiling:
        raise CapacityError(
            f"The test asks for {total_users} virtual users; the pool can supply {ceiling}."
        )

    generators = math.ceil(total_users / max_vus_per_generator)
    base, remainder = divmod(total_users, generators)
    # The remainder is handed out one user at a time to the earliest generators,
    # so the allocation sums to exactly what was asked for. Rounding each share
    # independently is how a 1,000-user test quietly becomes a 998-user test.
    return [base + (1 if index < remainder else 0) for index in range(generators)]

"""Virtual users across generators. The totals must reconcile exactly."""

import pytest

from plimsoll_api.allocation import CapacityError, allocate


def test_an_even_split() -> None:
    assert allocate(total_users=100, max_generators=4, max_vus_per_generator=50) == [50, 50]


def test_the_remainder_goes_to_the_earliest_generators() -> None:
    # 10 users over 3 generators is 4, 3, 3 -- never 3, 3, 3 with one lost.
    assert allocate(total_users=10, max_generators=3, max_vus_per_generator=4) == [4, 3, 3]


def test_every_allocation_sums_to_the_request() -> None:
    for total in range(1, 200):
        allocation = allocate(total_users=total, max_generators=50, max_vus_per_generator=7)
        assert sum(allocation) == total


def test_no_generator_exceeds_its_ceiling() -> None:
    allocation = allocate(total_users=1000, max_generators=50, max_vus_per_generator=300)
    assert max(allocation) <= 300


def test_one_generator_is_enough_for_a_small_test() -> None:
    assert allocate(total_users=5, max_generators=10, max_vus_per_generator=500) == [5]


def test_a_request_beyond_the_pool_is_refused() -> None:
    with pytest.raises(CapacityError) as raised:
        allocate(total_users=10_000, max_generators=2, max_vus_per_generator=500)
    assert "1000" in str(raised.value)


def test_zero_users_is_refused() -> None:
    with pytest.raises(CapacityError):
        allocate(total_users=0, max_generators=2, max_vus_per_generator=500)

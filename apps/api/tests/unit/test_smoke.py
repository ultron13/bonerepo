def test_packages_are_importable() -> None:
    import plimsoll_api
    import plimsoll_contracts

    assert plimsoll_api.__name__ == "plimsoll_api"
    assert plimsoll_contracts.__name__ == "plimsoll_contracts"

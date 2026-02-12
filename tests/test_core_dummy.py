from core.placeholders import DomainPlaceholder


def test_domain_placeholder_default_name() -> None:
    placeholder = DomainPlaceholder()
    assert placeholder.name == "x-aout-core"

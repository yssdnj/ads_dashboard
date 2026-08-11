import pytest

from ads_funnel.tests.market_test_database import validate_test_database_name


def test_test_database_guard_accepts_only_narrow_generated_names():
    assert (
        validate_test_database_name("ads_funnel_market_test_a1b2c3d4")
        == "ads_funnel_market_test_a1b2c3d4"
    )


@pytest.mark.parametrize(
    "name",
    [
        "ads_funnel_market",
        "ads_funnel_market_test_",
        "ads_funnel_market_test_bad-name",
        "other_test_a1b2c3d4",
        "",
    ],
)
def test_test_database_guard_rejects_operational_or_ambiguous_names(name):
    with pytest.raises(ValueError, match="disposable Market Monitor test database"):
        validate_test_database_name(name)

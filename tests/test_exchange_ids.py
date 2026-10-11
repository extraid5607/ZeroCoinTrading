"""Every venue we use by default must exist in the installed CCXT (catches renamed exchange ids)."""
import pytest

from app.config import Settings
from app.exchange import ccxt_class


@pytest.mark.parametrize("venue", Settings().venues)
def test_default_venues_exist_in_ccxt(venue):
    cls = ccxt_class(venue)
    ex = cls({"enableRateLimit": True})
    assert "spot" in ex.has or ex.has.get("fetchOHLCV")
    assert ex.has["fetchOHLCV"]


def test_unknown_venue_is_a_clear_error():
    with pytest.raises(ValueError):
        ccxt_class("nosuchexchange")

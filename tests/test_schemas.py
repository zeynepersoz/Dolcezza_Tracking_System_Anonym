# -*- coding: utf-8 -*-
"""GLS Irlanda (Eircode) / Hollanda posta kodu ve etiket girdi dogrulama testleri."""
import pytest
from pydantic import ValidationError

from gls_api.schemas import (
    AddressIn,
    LabelCreateIn,
    PostalCodeError,
    validate_postal_code,
    validate_tracking_no,
)


@pytest.mark.parametrize("code", ["D02 XY45", "d02xy45", "T12A123", "A65F4E2"])
def test_eircode_accepts_valid(code):
    assert validate_postal_code("IE", code)


@pytest.mark.parametrize("code", ["", "12345", "D02", "D02-XY45"])
def test_eircode_rejects_invalid(code):
    with pytest.raises(PostalCodeError):
        validate_postal_code("IE", code)


@pytest.mark.parametrize("code", ["1234AB", "1234 AB", "9999zz"])
def test_nl_postal_accepts_valid(code):
    assert validate_postal_code("NL", code)


@pytest.mark.parametrize("code", ["", "AB1234", "12345", "1234A"])
def test_nl_postal_rejects_invalid(code):
    with pytest.raises(PostalCodeError):
        validate_postal_code("NL", code)


def test_unknown_country_is_lenient():
    assert validate_postal_code("TR", "34000") == "34000"


@pytest.mark.parametrize("country,code", [
    ("FR", "75008"),
    ("IT", "00144"),
    ("ES", "28001"),
    ("GR", "10432"),
    ("GR", "104 32"),
    ("SE", "11122"),
    ("SE", "111 22"),
    ("PT", "1000-001"),
])
def test_extra_country_postal_accepts_valid(country, code):
    assert validate_postal_code(country, code)


@pytest.mark.parametrize("country,code", [
    ("FR", "7500"),
    ("FR", "750080"),
    ("FR", "ABCDE"),
    ("IT", "0014"),
    ("ES", "2800A"),
    ("GR", "1043"),
    ("GR", "10432 1"),
    ("SE", "1112"),
    ("PT", "1000001"),
    ("PT", "1000-01"),
])
def test_extra_country_postal_rejects_invalid(country, code):
    with pytest.raises(PostalCodeError):
        validate_postal_code(country, code)


def test_address_in_normalizes_and_validates():
    addr = AddressIn(name=" Acme ", country="ie", postal_code="d02xy45")
    assert addr.country == "IE"
    assert addr.postal_code == "D02XY45"


def test_address_in_rejects_blank_name():
    with pytest.raises(ValidationError):
        AddressIn(name="   ")


def test_address_in_rejects_bad_email():
    with pytest.raises(ValidationError):
        AddressIn(name="Acme", email="not-an-email")


def test_label_create_in_rejects_unknown_channel():
    with pytest.raises(ValidationError):
        LabelCreateIn(channel="FR", shipper_id=1, consignee_id=2)


def test_label_create_in_rejects_out_of_range_weight():
    with pytest.raises(ValidationError):
        LabelCreateIn(channel="NL", shipper_id=1, consignee_id=2, weights=[12.0, 0])


@pytest.mark.parametrize("no", ["21569761233", "22562472349"])
def test_ie_tracking_no_accepts_11_digit(no):
    assert validate_tracking_no("IE", no)


@pytest.mark.parametrize("no", ["ABC12345678", "1234567890", "123456789012"])
def test_ie_tracking_no_rejects_wrong_shape(no):
    with pytest.raises(ValueError):
        validate_tracking_no("IE", no)


def test_nl_tracking_no_accepts_14_digit():
    assert validate_tracking_no("NL", "35000001406746")


@pytest.mark.parametrize("no", ["3500000140674", "350000014067466", "35000001A06746"])
def test_nl_tracking_no_rejects_wrong_shape(no):
    with pytest.raises(ValueError):
        validate_tracking_no("NL", no)

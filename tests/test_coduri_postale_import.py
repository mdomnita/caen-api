from scripts.init_coduri_postale_db import _parse_numar_entries


def test_empty_returns_single_empty_entry():
    entries = _parse_numar_entries(None)
    assert entries == [{
        "numar_raw": None, "numar_tip": None, "numar_min": None,
        "numar_max": None, "numar_open_ended": 0, "numar_paritate": None,
    }]


def test_single_closed_range_odd():
    entries = _parse_numar_entries("nr. 1-21")
    assert entries == [{
        "numar_raw": "nr. 1-21", "numar_tip": "nr", "numar_min": 1,
        "numar_max": 21, "numar_open_ended": 0, "numar_paritate": "impar",
    }]


def test_single_closed_range_even():
    entries = _parse_numar_entries("nr. 2-24")
    assert entries[0]["numar_min"] == 2
    assert entries[0]["numar_max"] == 24
    assert entries[0]["numar_paritate"] == "par"


def test_single_open_ended_range():
    entries = _parse_numar_entries("nr. 21-T")
    assert entries == [{
        "numar_raw": "nr. 21-T", "numar_tip": "nr", "numar_min": 21,
        "numar_max": None, "numar_open_ended": 1, "numar_paritate": "impar",
    }]


def test_bl_entries_left_as_is_not_split():
    for raw in ["bl. II, IV, VI", "bl. XIII", "bl. 4, 20, 38, 44, 60, 80, 90"]:
        entries = _parse_numar_entries(raw)
        assert entries == [{
            "numar_raw": raw, "numar_tip": "bl", "numar_min": None,
            "numar_max": None, "numar_open_ended": 0, "numar_paritate": None,
        }]


def test_semicolon_splits_into_two_closed_ranges():
    entries = _parse_numar_entries("nr. 23-49; 2-90")
    assert entries == [
        {"numar_raw": "nr. 23-49", "numar_tip": "nr", "numar_min": 23,
         "numar_max": 49, "numar_open_ended": 0, "numar_paritate": "impar"},
        {"numar_raw": "2-90", "numar_tip": "nr", "numar_min": 2,
         "numar_max": 90, "numar_open_ended": 0, "numar_paritate": "par"},
    ]


def test_semicolon_splits_second_closed_range_example():
    entries = _parse_numar_entries("nr. 51-103; 92-160")
    assert entries == [
        {"numar_raw": "nr. 51-103", "numar_tip": "nr", "numar_min": 51,
         "numar_max": 103, "numar_open_ended": 0, "numar_paritate": "impar"},
        {"numar_raw": "92-160", "numar_tip": "nr", "numar_min": 92,
         "numar_max": 160, "numar_open_ended": 0, "numar_paritate": "par"},
    ]


def test_semicolon_splits_into_two_open_ended_ranges():
    entries = _parse_numar_entries("nr. 105-T; 162-T")
    assert entries == [
        {"numar_raw": "nr. 105-T", "numar_tip": "nr", "numar_min": 105,
         "numar_max": None, "numar_open_ended": 1, "numar_paritate": "impar"},
        {"numar_raw": "162-T", "numar_tip": "nr", "numar_min": 162,
         "numar_max": None, "numar_open_ended": 1, "numar_paritate": "par"},
    ]


def test_mixed_parity_range_has_no_paritate():
    entries = _parse_numar_entries("nr. 1-4")
    assert entries[0]["numar_min"] == 1
    assert entries[0]["numar_max"] == 4
    assert entries[0]["numar_paritate"] is None


def test_letter_suffix_left_unparsed():
    entries = _parse_numar_entries("nr. 15A-31")
    assert entries == [{
        "numar_raw": "nr. 15A-31", "numar_tip": "nr", "numar_min": None,
        "numar_max": None, "numar_open_ended": 0, "numar_paritate": None,
    }]


def test_multi_token_group_left_unparsed():
    entries = _parse_numar_entries("nr. 1, 3, 5")
    assert entries == [{
        "numar_raw": "nr. 1, 3, 5", "numar_tip": "nr", "numar_min": None,
        "numar_max": None, "numar_open_ended": 0, "numar_paritate": None,
    }]

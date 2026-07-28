from scripts import init_zile_libere_db


def test_read_rows_handles_utf8_bom(tmp_path):
    csv_path = tmp_path / "zile.csv"
    csv_path.write_text(
        "\ufeffdata,zi_saptamana,denumire_sarbatoare,temei_art_139_codul_muncii,cade_in_weekend,observatii,sursa_legala,sursa_calendar_2026,sursa_verificare_suplimentara\n"
        "2026-01-01,joi,Anul Nou,1 și 2 ianuarie,Nu,,https://legislatie.just.ro,https://timeanddate.com,https://zilelibere.com\n",
        encoding="utf-8",
    )

    rows = init_zile_libere_db._read_rows(csv_path)

    assert rows == [
        (
            "2026-01-01",
            "joi",
            "Anul Nou",
            "1 și 2 ianuarie",
            0,
            None,
            "https://legislatie.just.ro",
            "https://timeanddate.com",
            "https://zilelibere.com",
        )
    ]
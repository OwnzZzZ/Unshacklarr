import json
import re
from pathlib import Path

I18N = Path(__file__).resolve().parents[1] / "unshacklarr" / "static" / "i18n"
HOLE = re.compile(r"\{\d+\}")


def test_translations_fit_the_page():
    """Each language translates texts the page has, with no {n} the English lacks (it would show as is)."""
    english = json.loads((I18N / "en.json").read_text())
    for path in I18N.glob("*.json"):
        if path.name == "en.json":
            continue
        for key, text in json.loads(path.read_text()).items():
            assert key in english, f"{path.name}: {key!r} is not on the page (run tools/i18n/extract.py?)"
            assert isinstance(text, str) and text.strip(), f"{path.name}: {key!r} is empty"
            assert set(HOLE.findall(text)) <= set(HOLE.findall(key)), f"{path.name}: {key!r} has a {{n}} of its own"


def test_the_server_translates_a_notification(tmp_path, monkeypatch):
    from unshacklarr import i18n
    (tmp_path / "fr.json").write_text(json.dumps({"Downloaded: {0}": "Téléchargé: {0}", "Imported by Sonarr.": "Importé par Sonarr.",
                                                  "{0} failed": "{0} en échec", "3 parts": "3 parties"}))
    monkeypatch.setattr(i18n, "FOLDER", tmp_path)
    i18n.catalog.cache_clear()
    assert i18n.tr_lines("Downloaded: Silo S02E03\nImported by Sonarr.", "fr") == "Téléchargé: Silo S02E03\nImporté par Sonarr."
    assert i18n.tr("3 parts failed", "fr") == "3 parties en échec"  # what fills {0} is translated too
    assert i18n.tr("Something else", "fr") == "Something else" and i18n.tr("Downloaded: x", "en") == "Downloaded: x"
    i18n.catalog.cache_clear()


def test_a_short_template_does_not_swallow_a_sentence(tmp_path, monkeypatch):
    from unshacklarr import i18n
    (tmp_path / "fr.json").write_text(json.dumps({"{0} ago": "il y a {0}", "{0} of {1}": "{0} sur {1}"}))
    monkeypatch.setattr(i18n, "FOLDER", tmp_path)
    i18n.catalog.cache_clear()
    assert i18n.tr("5 min ago", "fr") == "il y a 5 min" and i18n.tr("3 of 6", "fr") == "3 sur 6"
    long = "A download of this episode already waits in the downloads folder"
    assert i18n.tr(long, "fr") == long and i18n.tr("Device certificate revoked (0x8004C065), tested 59 min ago", "fr").endswith("ago")
    i18n.catalog.cache_clear()


def test_a_hostile_line_cannot_make_translation_slow(tmp_path, monkeypatch):
    import time
    from unshacklarr import i18n
    (tmp_path / "fr.json").write_text(json.dumps({"Seen {0} on {1} at {2} for {3} in {4} by {5}.": "Vu {0} sur {1} à {2} pour {3} dans {4} par {5}."}))
    monkeypatch.setattr(i18n, "FOLDER", tmp_path)
    i18n.catalog.cache_clear()
    line = "Seen " + " on at for in by" * 10_000 + " x" * 50_000  # every piece there, many times, but not the final "."
    start = time.monotonic()
    assert i18n.tr(line, "fr") == line
    assert time.monotonic() - start < 1  # a regular expression took minutes on this
    assert i18n.tr("Seen it on M6 at 9 for me in Paris by bus.", "fr") == "Vu it sur M6 à 9 pour me dans Paris par bus."
    i18n.catalog.cache_clear()

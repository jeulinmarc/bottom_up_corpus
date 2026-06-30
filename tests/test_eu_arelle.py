import builtins
import pytest
from bottom_up_corpus.eu import arelle_esef


def test_oim_from_esef_zip_raises_clear_error_when_arelle_missing(monkeypatch, tmp_path):
    # Simulate Arelle not installed: make `import arelle...` fail inside the function.
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name.startswith("arelle"):
            raise ImportError("no arelle")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    z = tmp_path / "x.zip"; z.write_bytes(b"PK\x03\x04")
    with pytest.raises(ImportError, match="eu-financials"):
        arelle_esef.oim_from_esef_zip(str(z))

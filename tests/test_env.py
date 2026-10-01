from clusterwatch.env import load_dotenv


def test_load_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("CW_A", raising=False)
    monkeypatch.delenv("CW_B", raising=False)
    monkeypatch.setenv("CW_C", "deja")
    f = tmp_path / ".env"
    f.write_text('# commentaire\nCW_A=abc\nexport CW_B="x=y"\nCW_C=nouveau\nCW_D=\n', encoding="utf-8")
    assert load_dotenv(f) == ["CW_A", "CW_B"]
    import os
    assert os.environ["CW_A"] == "abc" and os.environ["CW_B"] == "x=y" and os.environ["CW_C"] == "deja"
    assert load_dotenv(tmp_path / "absent") == []

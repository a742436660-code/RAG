import pytest

from app.core.errors import AppError
from app.services.storage import secure_filename, validate_extension, validate_mime


def test_secure_filename():
    assert secure_filename("../../bad name.txt") == "bad_name.txt"
    assert secure_filename("") == "upload"


def test_extension_validation():
    assert validate_extension("a.pdf") == ".pdf"
    with pytest.raises(AppError):
        validate_extension("a.exe")


def test_mime_validation():
    assert validate_mime("text/plain", ".txt") == "text/plain"
    with pytest.raises(AppError):
        validate_mime("application/x-msdownload", ".txt")

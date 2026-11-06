from datetime import datetime, timezone
from unittest import mock

import pytest
import requests

from unshacklarr import offsite


def test_a_backup_is_encrypted_and_opens_with_its_passphrase_only():
    sealed = offsite.encrypt("series: {}\n", "correct horse")
    assert offsite.is_encrypted(sealed) and "series" not in sealed
    assert offsite.decrypt(sealed, "correct horse") == "series: {}\n"
    with pytest.raises(ValueError):
        offsite.decrypt(sealed, "wrong")


def test_s3_is_signed_as_aws_signs_it():
    # the signature checked against botocore's own S3SigV4Auth for the same request and time
    seen = {}
    ok = mock.Mock(status_code=200, raise_for_status=lambda: None)
    with mock.patch.object(requests, "put", lambda url, data, timeout, headers: seen.update(url=url, headers=headers) or ok):
        offsite.s3_put("https://s3.eu-west-3.amazonaws.com", "my-bucket/unshacklarr", "eu-west-3", "AKIDEXAMPLE",
                       "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "unshacklarr-2026-10-08-1200 a.yaml.enc", b"hello",
                       datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc))
    assert seen["url"] == "https://s3.eu-west-3.amazonaws.com/my-bucket/unshacklarr/unshacklarr-2026-10-08-1200%20a.yaml.enc"
    assert seen["headers"]["Authorization"].endswith("Signature=f7e72728e18db900ab7a32433b4e18c36cf68b714f8ad8c3192b55ce466ffa4d")


def test_nothing_leaves_without_a_passphrase():
    with pytest.raises(ValueError):
        offsite.send({"backup_remote": "webdav", "backup_remote_url": "https://dav.example"}, "b.yaml", "series: {}")

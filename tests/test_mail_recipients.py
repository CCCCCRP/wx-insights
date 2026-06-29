from worker.config import parse_email_list
from worker.mail.recipients import resolve_recipients


def test_parse_email_list_dedup_and_trim():
    assert parse_email_list(" a@x.com , b@y.com ; a@x.com ") == ["a@x.com", "b@y.com"]


def test_resolve_recipients_explicit():
    assert resolve_recipients(["x@y.com"], ["a@b.com"]) == ["x@y.com"]


def test_resolve_recipients_fallback():
    assert resolve_recipients(None, ["a@b.com", "c@d.com"]) == ["a@b.com", "c@d.com"]


def test_resolve_recipients_empty_explicit():
    assert resolve_recipients([], ["a@b.com"]) == []

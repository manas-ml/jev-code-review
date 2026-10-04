from datetime import UTC, datetime

from examples.notify import digest, queue, schedule, unsubscribe, webhooks


def test_urgent_always_sends():
    assert schedule.should_send(True, schedule.QuietHours(), datetime(2026, 1, 1, 23, tzinfo=UTC))


def test_daytime_sends():
    assert schedule.should_send(False, schedule.QuietHours(), datetime(2026, 1, 1, 12, tzinfo=UTC))


def test_blocks_localhost():
    import pytest

    with pytest.raises(ValueError):
        webhooks.validate_url("http://localhost/hook")


def test_retry_succeeds():
    assert queue.deliver_with_retry(lambda: 200, base_delay=0)


def test_unsubscribe_roundtrip():
    class Cur:
        def execute(self, *a):
            self.args = a

    email = "a@b.com"
    assert unsubscribe.handle_unsubscribe(Cur(), email, unsubscribe.unsubscribe_token(email))


def test_digest_groups():
    notes = [digest.Note(1, "a", "x"), digest.Note(1, "a", "y"), digest.Note(1, "b", "z")]
    assert digest.build_digest(notes) == {"a": ["x", "y"], "b": ["z"]}

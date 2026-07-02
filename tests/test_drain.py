from logscope.cluster.drain import (
    WILDCARD,
    Drain,
    merge_tokens,
    seq_distance,
)

# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_seq_distance_wildcards_match_anything():
    assert seq_distance(["a", WILDCARD, "c"], ["a", "x", "c"]) == 1.0
    assert seq_distance(["a", "b", "c"], ["a", "x", "c"]) == 2 / 3
    assert seq_distance(["a", "b"], ["a", "b", "c"]) == 0.0  # length mismatch


def test_merge_only_generalizes():
    merged = merge_tokens(["a", "b", "c"], ["a", "x", "c"])
    assert merged == ["a", WILDCARD, "c"]
    # Merging again with a third value keeps the wildcard (never re-specializes).
    merged2 = merge_tokens(merged, ["a", "y", "c"])
    assert merged2 == ["a", WILDCARD, "c"]


# --------------------------------------------------------------------------- #
# The headline behavior: collapse noise, keep distinct messages distinct
# --------------------------------------------------------------------------- #


def test_varying_ids_collapse_to_one_template():
    d = Drain()
    lines = [
        "Failed to connect to db-7 after 3 retries (request_id=a1b2)",
        "Failed to connect to db-3 after 3 retries (request_id=c4d5)",
        "Failed to connect to db-9 after 3 retries (request_id=e6f7)",
    ]
    for line in lines:
        d.add_message(line)
    assert len(d.templates) == 1
    template = d.templates[0]
    assert template.count == 3
    assert WILDCARD in template.tokens  # the varying db id / request id generalized


def test_distinct_messages_stay_separate():
    d = Drain()
    d.add_message("connection established to host")
    d.add_message("connection established to host")  # identical -> one template
    d.add_message("disk almost full on /var")        # genuinely different
    d.add_message("payment processed for order 42")  # number masked -> distinct
    # Three distinct templates; the repeated line has count 2.
    assert len(d.templates) == 3
    counts = sorted(t.count for t in d.templates)
    assert counts == [1, 1, 2]


def test_unmasked_prefix_variable_splits_a_known_drain_limitation():
    """Documents a real Drain weakness: an unmasked variable in the leading
    tokens (which the tree branches on) causes over-splitting. The standard
    mitigations are masking (numbers/ids/ips, which we do) and tuning `depth`.
    A varying *name* in the prefix is not maskable, so it splits."""
    d = Drain()
    d.add_message("user alice logged in")
    d.add_message("user bob logged in")
    # Both share length and the "user" prefix but branch apart on the name token.
    assert len(d.templates) == 2


def test_different_lengths_are_different_templates():
    d = Drain()
    d.add_message("connection refused")
    d.add_message("connection refused by peer")  # different token count
    assert len(d.templates) == 2


def test_numbers_and_ips_are_masked():
    d = Drain()
    d.add_message("request from 10.0.0.1 took 234 ms")
    d.add_message("request from 10.0.0.2 took 999 ms")
    assert len(d.templates) == 1
    assert d.templates[0].count == 2


def test_templates_ranked_by_count():
    d = Drain()
    for _ in range(5):
        d.add_message("frequent error code 500")
    d.add_message("rare warning")
    ranked = d.templates
    assert ranked[0].count == 5   # most frequent first
    assert ranked[-1].count == 1


def test_returns_the_matched_template():
    d = Drain()
    t1 = d.add_message("worker 1 started")
    t2 = d.add_message("worker 2 started")
    assert t1.id == t2.id  # same template object/id
    assert t2.count == 2

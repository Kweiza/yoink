import policy


def test_decide_no_conflict_allows():
    d = policy.decide(mode="block", conflicting_owners=[])
    assert d.should_block is False
    assert d.should_warn is False


def test_decide_advisory_conflict_warns_but_allows():
    d = policy.decide(mode="advisory", conflicting_owners=[{"login": "alice"}])
    assert d.should_block is False
    assert d.should_warn is True


def test_decide_block_conflict_blocks_and_warns():
    d = policy.decide(mode="block", conflicting_owners=[{"login": "alice"}])
    assert d.should_block is True
    assert d.should_warn is True


def test_decide_unknown_mode_defaults_to_advisory_fail_open():
    d = policy.decide(mode="strict", conflicting_owners=[{"login": "alice"}])
    # Spec §2 fail-open: yoink errors never block; unknown mode is an error.
    assert d.should_block is False


def test_block_paths_phase4_stub_never_matches_in_phase3():
    # Phase 3 is not allowed to consult block_paths yet.
    assert policy.is_phase4_block_path("migrations/0001.sql") is False

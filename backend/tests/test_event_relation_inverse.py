from app.skills.atomic.event_relation import _are_inverse_chains, _valid_other_note


def test_explicit_inverse_accepts_gender_specific_parent_child_relations():
    assert _are_inverse_chains(["F"], ["D"])
    assert _are_inverse_chains(["M"], ["S"])
    assert _are_inverse_chains(["W", "F"], ["D", "H"])


def test_explicit_inverse_rejects_guessed_or_wrong_direction():
    assert not _are_inverse_chains(["F"], ["F"])
    assert not _are_inverse_chains(["M"], ["M"])
    assert not _are_inverse_chains(["W", "F"], ["H", "S"])


def test_other_relation_requires_short_directional_note():
    assert _valid_other_note("被其诛杀")
    assert not _valid_other_note(None)
    assert not _valid_other_note("")
    assert not _valid_other_note("超出十五个汉字的其他关系方向说明文字")

from app.consensus import agree, f1, iou, match, pair_f1, positives, split_for_video

NEG = {"not_louse"}


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0.0
    assert abs(iou((0, 0, 10, 10), (5, 0, 10, 10)) - 1 / 3) < 1e-9


def test_match_requires_same_class_and_iou():
    a = [((0, 0, 10, 10), "louse"), ((50, 50, 10, 10), "fly")]
    b = [((1, 1, 10, 10), "louse"), ((50, 50, 10, 10), "louse")]  # la segunda cambia de clase
    assert match(a, b) == (1, 1, 1)
    assert match(a, a) == (2, 0, 0)
    assert match([], []) == (0, 0, 0)


def test_match_is_one_to_one():
    a = [((0, 0, 10, 10), "louse")]
    b = [((0, 0, 10, 10), "louse"), ((1, 1, 10, 10), "louse")]
    assert match(a, b) == (1, 1, 0)  # una caja de A no puede emparejar con dos de B


def test_negative_boxes_are_background():
    a = [((0, 0, 10, 10), "louse"), ((30, 30, 10, 10), "not_louse")]
    b = [((0, 0, 10, 10), "louse")]
    assert positives(a, NEG) == [((0, 0, 10, 10), "louse")]
    assert agree(a, b, NEG)  # la caja «ni uno» de más no genera desacuerdo
    assert agree([], [((5, 5, 8, 8), "not_louse")], NEG)  # ambos sin piojos


def test_disagreement_cases():
    a = [((0, 0, 10, 10), "louse")]
    assert not agree(a, [], NEG)  # uno ve un piojo y el otro nada
    assert not agree(a, [((0, 0, 10, 10), "fly")], NEG)  # mismo sitio, otra clase
    assert not agree(a, [((100, 100, 10, 10), "louse")], NEG)  # otro sitio


def test_f1_values():
    assert f1(0, 0, 0) == 1.0
    assert f1(1, 1, 1) == 0.5
    a = [((0, 0, 10, 10), "louse"), ((50, 50, 10, 10), "louse")]
    assert pair_f1(a, a[:1], NEG) == 2 / 3


def test_split_is_deterministic_per_video_and_roughly_proportional():
    assert split_for_video("VID_a", "s") == split_for_video("VID_a", "s")
    names = [f"VID_{i}" for i in range(2000)]
    counts = {"train": 0, "val": 0, "test": 0}
    for n in names:
        counts[split_for_video(n, "semilla")] += 1
    assert abs(counts["train"] / 2000 - 0.7) < 0.04 and abs(counts["val"] / 2000 - 0.15) < 0.03
    assert any(split_for_video(n, "a") != split_for_video(n, "b") for n in names[:50])  # otra semilla reparte distinto

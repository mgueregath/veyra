from app.combine import combine, combine_and, combine_or
from app.evaluation import Pred


def P(x, y=0, w=10, h=10, s=0.9, lab="louse"):
    return Pred((x, y, w, h), lab, s)


def test_or_is_the_union_and_merges_overlapping_boxes():
    a, b = [P(0, s=0.9), P(100, s=0.5)], [P(2, s=0.7), P(300, s=0.6)]
    out = combine_or([a, b], thr=0.3)
    assert len(out) == 3  # la de 0 y la de 2 se funden; 100 y 300 quedan aparte
    first = out[0]
    assert first.score == 0.9 and first.bbox[0] in (0, 1) and sorted(p.bbox[0] for p in out)[1:] == [100, 300]
    assert combine_or([[], []]) == [] and len(combine_or([a, []])) == 2


def test_and_keeps_only_what_every_source_confirms():
    a, b, c = [P(0), P(100)], [P(1), P(500)], [P(0), P(100)]
    out = combine_and([a, b], thr=0.3)
    assert [p.bbox[0] for p in out] == [0] or [p.bbox[0] for p in out] == [1] or len(out) == 1
    assert len(combine_and([a, b, c])) == 1  # la de 100 no la confirma b; la de 0 sí la confirman las tres
    assert combine_and([a, []]) == [] and combine_and([]) == []
    assert combine_and([a]) == a  # una sola fuente: tal cual


def test_and_uses_each_box_only_once():
    a = [P(0, s=0.9), P(2, s=0.8)]  # dos cajas casi iguales de la misma fuente
    b = [P(1, s=0.7)]  # una sola caja de la otra
    assert len(combine_and([a, b], thr=0.3)) == 1


def test_scores_can_be_max_mean_or_min():
    a, b = [P(0, s=0.9)], [P(0, s=0.5)]
    assert combine("or", [a, b], score="max")[0].score == 0.9
    assert combine("or", [a, b], score="min")[0].score == 0.5
    assert abs(combine("and", [a, b])[0].score - 0.7) < 1e-9  # AND usa la media por defecto
    assert combine("and", [a, b], score="min")[0].score == 0.5


def test_classes_must_be_compatible_unless_one_has_none():
    model = [P(0, lab="louse", s=0.9)]
    fly = [P(0, lab="fly", s=0.8)]
    blob = [P(0, lab="", s=5.0)]  # el detector de apariencia no distingue clases
    assert combine("and", [model, fly]) == []  # clases distintas: no coinciden
    got = combine("and", [model, blob])
    assert len(got) == 1 and got[0].label == "louse"  # la clase la aporta el modelo
    assert len(combine("or", [model, fly])) == 2 and len(combine("or", [model, blob])) == 1


def test_unknown_operation_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        combine("xor", [[P(0)]])

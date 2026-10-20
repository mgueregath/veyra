import math

from app.evaluation import Pred, average_precision, evaluate, prf

G = {1: [((0, 0, 10, 10), "louse"), ((50, 50, 10, 10), "louse")], 2: []}


def test_average_precision_known_value():
    # 2 reales; predicciones por puntaje: acierta, falla, acierta -> AP = 0.5*1 + 0.5*(2/3)
    assert abs(average_precision([True, False, True], 2) - (0.5 + 0.5 * 2 / 3)) < 1e-9
    assert average_precision([True, True], 2) == 1.0
    assert average_precision([False, False], 2) == 0.0
    assert math.isnan(average_precision([True], 0))


def test_prf():
    assert prf(2, 1, 1)["f1"] == 2 * (2 / 3) * (2 / 3) / (4 / 3)
    assert prf(0, 0, 0)["f1"] == 0.0


def test_perfect_detector():
    preds = {1: [Pred((0, 0, 10, 10), "louse", 0.9), Pred((50, 50, 10, 10), "louse", 0.8)], 2: []}
    r = evaluate(G, preds, ["louse"], {1: "A", 2: "B"}, score_thr=0.5)
    assert r["map"] == 1.0 and r["micro"]["precision"] == 1.0 and r["micro"]["recall"] == 1.0 and r["count_mae"] == 0.0
    assert r["by_video"] == [
        {"video": "A", "frames": 1, "gt": 2, "pred": 2},
        {"video": "B", "frames": 1, "gt": 0, "pred": 0},
    ]
    assert r["examples"]["false_positives"] == [] and r["examples"]["false_negatives"] == []


def test_false_positives_on_empty_images_and_misses():
    preds = {
        1: [Pred((0, 0, 10, 10), "louse", 0.9)],
        2: [Pred((5, 5, 10, 10), "louse", 0.95)],
    }  # un acierto, un FP en la imagen vacía
    r = evaluate(G, preds, ["louse"], score_thr=0.5)
    assert (r["micro"]["tp"], r["micro"]["fp"], r["micro"]["fn"]) == (1, 1, 1)
    assert r["examples"]["false_positives"][0]["frame_id"] == 2 and r["examples"]["false_negatives"][0]["bbox"] == [
        50,
        50,
        10,
        10,
    ]


def test_wrong_class_is_error_for_both():
    preds = {1: [Pred((0, 0, 10, 10), "fly", 0.9)]}
    r = evaluate({1: [((0, 0, 10, 10), "louse")]}, preds, ["louse", "fly"], score_thr=0.5)
    assert r["per_class"]["louse"]["fn"] == 1 and r["per_class"]["fly"]["fp"] == 1 and r["micro"]["tp"] == 0


def test_iou_threshold_and_one_to_one():
    preds = {1: [Pred((5, 0, 10, 10), "louse", 0.9)]}  # IoU = 1/3
    gt = {1: [((0, 0, 10, 10), "louse")]}
    assert evaluate(gt, preds, ["louse"], score_thr=0.1, iou_thr=0.5)["micro"]["tp"] == 0
    assert evaluate(gt, preds, ["louse"], score_thr=0.1, iou_thr=0.3)["micro"]["tp"] == 1
    dup = {
        1: [Pred((0, 0, 10, 10), "louse", 0.9), Pred((0, 0, 10, 10), "louse", 0.8)]
    }  # dos predicciones sobre una sola caja real
    r = evaluate(gt, dup, ["louse"], score_thr=0.1)
    assert (r["micro"]["tp"], r["micro"]["fp"]) == (1, 1)


def test_best_f1_threshold_is_reported_and_chosen():
    preds = {
        1: [Pred((0, 0, 10, 10), "louse", 0.9), Pred((50, 50, 10, 10), "louse", 0.6)],
        2: [Pred((0, 0, 5, 5), "louse", 0.3)],
    }
    r = evaluate(G, preds, ["louse"])
    assert r["threshold_choice"] == "mejor F1 en este conjunto" and r["score_threshold"] == 0.6
    assert r["micro"]["f1"] == 1.0
    assert evaluate(G, preds, ["louse"], score_thr=0.8)["threshold_choice"] == "fijo"


def test_no_predictions_and_no_ground_truth():
    r = evaluate(G, {}, ["louse"], score_thr=0.5)
    assert r["map"] == 0.0 and r["micro"]["recall"] == 0.0 and r["micro"]["fn"] == 2
    r2 = evaluate({1: []}, {1: [Pred((0, 0, 5, 5), "louse", 0.9)]}, ["louse"], score_thr=0.5)
    assert r2["map"] is None and r2["micro"]["fp"] == 1  # sin cajas reales no hay AP, pero sí falsos positivos

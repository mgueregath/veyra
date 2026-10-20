#!/usr/bin/env python3
"""Mide la velocidad de inferencia sobre un video real: PyTorch (fp32 y fp16) y, si se pide, ONNX Runtime.

Uso: python3 workers/benchmark.py --weights modelo.pt --video v.mp4 --imgsz 1280 [--onnx]
Imprime una tabla y una línea JSON (para guardarla junto al modelo). Solo mide el detector (sin lectura ni dibujo).
"""

import argparse
import json
import os
import sys
import time

import cv2

os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")  # pesos locales de confianza (ver train_yolo.py)


def frames(video, n):
    cap, out = cv2.VideoCapture(video), []
    while len(out) < n:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
    cap.release()
    return out


def bench(fn, imgs, warm=5):
    for f in imgs[:warm]:
        fn(f)
    t = []
    for f in imgs:
        t0 = time.perf_counter()
        fn(f)
        t.append((time.perf_counter() - t0) * 1000)
    t.sort()
    return {
        "fps": round(1000 / (sum(t) / len(t)), 1),
        "ms_mean": round(sum(t) / len(t), 1),
        "ms_p95": round(t[int(0.95 * (len(t) - 1))], 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--device", default="0")
    ap.add_argument("--onnx", action="store_true")
    a = ap.parse_args()
    from ultralytics import YOLO

    imgs = frames(a.video, a.frames)
    res = {
        "weights": a.weights,
        "imgsz": a.imgsz,
        "frames": len(imgs),
        "frame_size": list(imgs[0].shape[1::-1]),
        "results": {},
    }
    m = YOLO(a.weights)
    for half in (False, True):
        res["results"]["pytorch_" + ("fp16" if half else "fp32")] = bench(
            lambda f, half=half: m.predict(f, imgsz=a.imgsz, device=a.device, half=half, verbose=False), imgs
        )
    if a.onnx:
        try:
            path = m.export(format="onnx", imgsz=a.imgsz, half=False, simplify=False, opset=12, device=a.device)
            import onnxruntime as ort

            prov = ort.get_available_providers()
            om = YOLO(path, task="detect")
            res["onnx_file"] = str(path)
            res["onnx_providers"] = prov
            res["results"]["onnx_ultralytics"] = bench(
                lambda f: om.predict(f, imgsz=a.imgsz, device=a.device, verbose=False), imgs
            )
        except Exception as e:  # el motivo se informa: no se oculta
            res["onnx_error"] = f"{type(e).__name__}: {str(e)[:300]}"
    print(f"{'motor':22} {'fps':>7} {'ms medio':>9} {'ms p95':>8}")
    for k, v in res["results"].items():
        print(f"{k:22} {v['fps']:7.1f} {v['ms_mean']:9.1f} {v['ms_p95']:8.1f}")
    if "onnx_error" in res:
        print("ONNX no disponible:", res["onnx_error"])
    print("JSON " + json.dumps(res))


if __name__ == "__main__":
    sys.exit(main())

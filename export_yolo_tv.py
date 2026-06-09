from ultralytics import YOLO

print("🔹 Loading YOLOv8n...")
model = YOLO("yolov8n.pt")

print("🔹 Exporting to OpenVINO (FP32 for max compatibility)...")
model.export(format="openvino", imgsz=1280, half=False)
print("✅ Done. New folder: yolov8n_openvino_model/")
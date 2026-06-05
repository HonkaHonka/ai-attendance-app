from openvino import Core
core = Core()
print("Devices:", core.available_devices)

model = core.read_model("D:/ai-attendance-app/yolov8n_openvino_model/yolov8n.xml")
try:
    compiled = core.compile_model(model, "GPU")
    print("✅ GPU compilation works! Model runs on Intel Iris Xe.")
except Exception as e:
    print(f"❌ GPU compilation failed: {e}")
    print("Driver issue — GPU visible but cannot compile.")
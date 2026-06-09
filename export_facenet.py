import torch
from facenet_pytorch import InceptionResnetV1

print("🔹 Loading FaceNet for export...")
model = InceptionResnetV1(pretrained="vggface2").eval()

# Dummy input: MTCNN outputs [3, 160, 160], we unsqueeze to [1, 3, 160, 160]
dummy = torch.randn(1, 3, 160, 160)

print("🔹 Exporting to ONNX...")
torch.onnx.export(
    model,
    dummy,
    "facenet.onnx",
    input_names=["input"],
    output_names=["output"],
    dynamic_axes={"input": {0: "batch_size"}, "output": {0: "batch_size"}},
    opset_version=11
)

print("🔹 Converting ONNX to OpenVINO IR...")
from openvino import convert_model, save_model
ov_model = convert_model("facenet.onnx")
save_model(ov_model, "facenet_openvino.xml")

print("✅ FaceNet exported to facenet_openvino.xml / .bin")
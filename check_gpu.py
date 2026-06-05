from openvino.runtime import Core

core = Core()
devices = core.available_devices
print("OpenVINO available devices:", devices)

if 'GPU' in devices or 'GPU.0' in devices:
    print("✅ Intel GPU is visible to OpenVINO")
else:
    print("❌ Intel GPU is NOT visible to OpenVINO")
    print("The TV is missing Intel GPU drivers or the OpenVINO GPU plugin.")
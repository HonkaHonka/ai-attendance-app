# save as check_ov.py
import openvino
print("OpenVINO version:", openvino.__version__)

from openvino import Core
core = Core()
devices = core.available_devices
print("Devices:", devices)

for d in devices:
    print(f"  {d}")
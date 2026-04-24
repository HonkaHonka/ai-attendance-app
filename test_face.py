import cv2
from ultralytics import YOLO

model = YOLO("yolov8n.pt")

cap = cv2.VideoCapture(0)

while True:
    ret, frame = cap.read()
    if not ret:
        break

    results = model.track(frame, conf=0.4, persist=True, verbose=False)

    if results[0].boxes is not None:
        for i in range(len(results[0].boxes)):
            cls = int(results[0].boxes.cls[i])
            if cls != 0:  # not person
                continue

            x1,y1,x2,y2 = map(int, results[0].boxes.xyxy[i])
            # face approx = upper 40% of person box
            fx1, fy1 = x1, y1
            fx2, fy2 = x2, y1 + int((y2-y1)*0.4)

            cv2.rectangle(frame,(fx1,fy1),(fx2,fy2),(0,255,0),2)

    cv2.imshow("PERSON → FACE ROI", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()

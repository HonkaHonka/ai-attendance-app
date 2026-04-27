import cv2
cap = cv2.VideoCapture(0)
# Try to open the camera's property dialog
cap.set(cv2.CAP_PROP_SETTINGS, 1)
while True:
    ret, frame = cap.read()
    if not ret:
        break
    cv2.imshow('Camera Feed', frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break
cap.release()
cv2.destroyAllWindows()
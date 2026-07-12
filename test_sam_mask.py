import numpy as np
from ultralytics import SAM
import cv2

# Create a dummy image: a black image with a white square in the middle
image = np.zeros((100, 100, 3), dtype=np.uint8)
cv2.rectangle(image, (30, 30), (70, 70), (255, 255, 255), -1)

# Initialize Mobile SAM
model = SAM('mobile_sam.pt')

try:
    x, y = 50, 50
    results = model(image, points=[[x, y]], labels=[1], verbose=False)
    masks = results[0].masks
    mask_np = (masks.data[0].cpu().numpy() * 255).astype(np.uint8)
    
    contours, _ = cv2.findContours(mask_np, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print("Number of contours:", len(contours))
    for idx, c in enumerate(contours):
        dist = cv2.pointPolygonTest(c, (x, y), False)
        print(f"Contour {idx} area:", cv2.contourArea(c))
        print(f"Contour {idx} pointPolygonTest with ({x}, {y}):", dist)
        
except Exception as e:
    print("Error:", e)

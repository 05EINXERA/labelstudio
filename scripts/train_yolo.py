import argparse
import os
import sys

try:
    from ultralytics import YOLO
except ImportError:
    print("Error: The 'ultralytics' package is not installed.")
    print("Please install it by running: pip install ultralytics")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Train a YOLOv8 model using an exported dataset from the annotation app.")
    parser.add_argument(
        "--data", 
        type=str, 
        required=True, 
        help="Path to the extracted YOLO export folder (must contain a .yaml file, e.g., dataset.yaml or data.yaml)"
    )
    parser.add_argument(
        "--model", 
        type=str, 
        default="yolov8n.pt", 
        help="Base YOLOv8 model to start training from (default: yolov8n.pt). Options: yolov8n.pt, yolov8m.pt, yolov8l.pt, yolov8x.pt"
    )
    parser.add_argument(
        "--epochs", 
        type=int, 
        default=100, 
        help="Number of epochs to train for (default: 100)"
    )
    parser.add_argument(
        "--imgsz", 
        type=int, 
        default=640, 
        help="Target image size for training (default: 640)"
    )
    parser.add_argument(
        "--batch", 
        type=int, 
        default=16, 
        help="Batch size (default: 16). Lower this if you run out of GPU memory."
    )
    
    args = parser.parse_args()
    
    # Check if the data path exists
    if not os.path.exists(args.data):
        print(f"Error: The provided data path '{args.data}' does not exist.")
        sys.exit(1)
        
    # Find the YAML file in the data directory
    yaml_file = None
    if os.path.isfile(args.data) and args.data.endswith('.yaml'):
        yaml_file = args.data
    elif os.path.isdir(args.data):
        for f in os.listdir(args.data):
            if f.endswith('.yaml'):
                yaml_file = os.path.join(args.data, f)
                break
                
    if not yaml_file:
        print(f"Error: Could not find a .yaml configuration file in '{args.data}'.")
        print("Please provide the exact path to the .yaml file or the folder containing it.")
        sys.exit(1)
        
    print(f"--- Starting YOLOv8 Training ---")
    print(f"Model:   {args.model}")
    print(f"Dataset: {yaml_file}")
    print(f"Epochs:  {args.epochs}")
    print(f"Image Size: {args.imgsz}")
    print(f"Batch Size: {args.batch}")
    print("--------------------------------")
    
    # Initialize the model
    model = YOLO(args.model)
    
    # Train the model
    results = model.train(
        data=yaml_file,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project="training_runs",
        name="custom_yolo"
    )
    
    print("\n=== Training Complete ===")
    print("Your new trained weights are located in:")
    print("  training_runs/custom_yolo/weights/best.pt")
    print("\nTo use this model in the annotation app, copy the 'best.pt' file into the 'models/' folder of your labelstudio repository.")

if __name__ == "__main__":
    main()

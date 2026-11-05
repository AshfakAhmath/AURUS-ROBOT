import os
import sys
import time

# Add src folder to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.hardware.sensors import ProximitySensor

def main():
    print("=========================================================")
    print("           AURUS ULTRASONIC SENSORS CHECKER              ")
    print("=========================================================")
    
    try:
        sensor = ProximitySensor()
    except Exception as e:
        print(f"Error initializing ProximitySensor: {e}")
        sys.exit(1)
        
    print(f"Running mode: {'SIMULATION' if sensor.is_simulation else 'PHYSICAL PI HARDWARE'}")
    print("Press Ctrl+C to stop reading.\n")
    
    try:
        while True:
            # Read all 5 sensors
            readings = sensor.read_all()
            
            # Print readings formatted
            print(f"FL: {readings['fl']:>6.1f} cm | "
                  f"F: {readings['f']:>6.1f} cm | "
                  f"FR: {readings['fr']:>6.1f} cm | "
                  f"RL: {readings['rl']:>6.1f} cm | "
                  f"RR: {readings['rr']:>6.1f} cm", end='\r')
            
            # Flush stdout
            sys.stdout.flush()
            time.sleep(0.3)
            
    except KeyboardInterrupt:
        print("\n\nSensor check stopped by user.")
        if not sensor.is_simulation:
            import RPi.GPIO as GPIO
            GPIO.cleanup()
            print("GPIO cleaned up.")

if __name__ == "__main__":
    main()

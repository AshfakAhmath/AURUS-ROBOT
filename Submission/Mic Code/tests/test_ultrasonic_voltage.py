import time

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Error: RPi.GPIO module not found. Are you running this on a Raspberry Pi?")
    exit(1)

# Default pin configuration for testing (Front Center Sensor)
# Change these if you are testing a different sensor
TRIG_PIN = 20

def main():
    GPIO.setmode(GPIO.BCM)
    
    # We only need to control the Trigger pin.
    # The Echo pin is left disconnected from the Pi during this test.
    GPIO.setup(TRIG_PIN, GPIO.OUT, initial=GPIO.LOW)
    
    print("=========================================================")
    print("          ULTRASONIC SENSOR VOLTAGE TEST SCRIPT          ")
    print("=========================================================")
    print(f"Using TRIG_PIN = {TRIG_PIN} (BCM)")
    print("INSTRUCTIONS:")
    print("1. Connect the sensor's VCC to 5V and GND to GND.")
    print(f"2. Connect the sensor's TRIG pin to Raspberry Pi GPIO {TRIG_PIN}.")
    print("3. DO NOT connect the sensor's ECHO pin to the Raspberry Pi yet!")
    print("4. Connect the ECHO pin to your voltage divider.")
    print("5. Connect your multimeter to the OUTPUT of your voltage divider and GND.")
    print("   (Set multimeter to measure DC Voltage)")
    print("6. Point the sensor at an object far away (e.g., ceiling or a wall 2m away).")
    print("   This makes the echo pulses longer, increasing the average voltage on the multimeter.")
    print("=========================================================\n")
    
    input("Press ENTER to start pulsing the trigger pin...")
    
    print("\nPulsing TRIG pin... Press Ctrl+C to stop.")
    print("Check your multimeter. The voltage should NOT exceed 3.3V.")
    print("If it is close to 5V, your voltage divider is wired incorrectly!\n")

    try:
        while True:
            # Send a 10 microsecond pulse to trigger the sensor
            GPIO.output(TRIG_PIN, GPIO.HIGH)
            time.sleep(0.00001)
            GPIO.output(TRIG_PIN, GPIO.LOW)
            
            # Fire the sensor rapidly (every 20ms) to give a steady reading on the multimeter
            time.sleep(0.02)
            
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up.")

if __name__ == "__main__":
    main()

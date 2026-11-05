class FollowController:
    """
    Translates vision data (face bounding boxes) into motor commands.
    Ensures safe following by checking ultrasonic sensor data.
    """
    def __init__(self):
        self.active = False
        
        # Target normalized face width representing approx 1 meter distance
        self.target_width = 0.25 
        
        # Deadzones to prevent jitter
        self.center_x = 0.5
        self.yaw_deadzone = 0.1  # 0.4 to 0.6 is centered
        self.dist_deadzone = 0.05

    def set_active(self, state):
        self.active = state

    def calculate_motor_command(self, faces, sensor_data):
        """
        Returns the required motor action string based on face position.
        """
        if not self.active:
            return "stop"
            
        if not faces:
            return "stop" # Stop if user is lost
            
        # Safety Stop: Avoid crashing into obstacles
        f_dist = sensor_data.get('f', 100)
        fl_dist = sensor_data.get('fl', 100)
        fr_dist = sensor_data.get('fr', 100)
        
        if f_dist < 20 or fl_dist < 15 or fr_dist < 15:
            return "stop"
            
        # Get the largest face (assume it's the primary user)
        largest_face = max(faces, key=lambda f: f["box"][2])
        x, y, w, h = largest_face["box"]
        
        # Calculate center of the face
        face_center_x = x + (w / 2)
        
        # Proportional control logic
        if face_center_x < (self.center_x - self.yaw_deadzone):
            return "spin_left"
        elif face_center_x > (self.center_x + self.yaw_deadzone):
            return "spin_right"
        elif w < (self.target_width - self.dist_deadzone):
            return "forward"
        elif w > (self.target_width + self.dist_deadzone):
            return "backward"
            
        return "stop"

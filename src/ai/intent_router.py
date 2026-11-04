import re

class IntentRouter:
    """
    Classifies voice commands into local actions or passes them to the Gemini brain.
    Ensures < 1 second response for basic mobility and system commands.
    """
    
    def __init__(self):
        # Basic command patterns mapped to their internal action identifiers
        self.movement_patterns = {
            r"\b(move\s+forward|go\s+forward|forward)\b": "forward",
            r"\b(move\s+backward|go\s+backward|backward|back\s+up)\b": "backward",
            r"\b(turn\s+left|go\s+left|left)\b": "spin_left",  # Actually we have 'spin' or 'strafe_left' in BRAIN_ACTIONS
            r"\b(turn\s+right|go\s+right|right)\b": "spin_right",
            r"\b(strafe\s+left|slide\s+left)\b": "strafe_left",
            r"\b(strafe\s+right|slide\s+right)\b": "strafe_right",
            r"\b(stop|halt|freeze)\b": "stop",
            r"\b(spin|rotate|twirl)\b": "spin",
            r"\b(wiggle|dance|shiver|nod)\b": lambda m: m.group(1) # map to itself
        }
        
        self.mode_patterns = {
            r"\b(explore|autonomous\s+mode|auto\s+mode)\b": "mode_autonomous",
            r"\b(manual\s+mode|stop\s+exploring)\b": "mode_manual"
        }
        
        self.report_patterns = {
            r"\b(summarize\s+today|daily\s+report|how\s+was\s+today)\b": "report_daily"
        }

    def route(self, text):
        """
        Parses text and returns an intent dictionary.
        Returns:
            {"type": "local_movement", "action": action_name, "original": text}
            {"type": "local_mode", "mode": mode_name, "original": text}
            {"type": "conversation", "original": text}
        """
        text_lower = text.lower().strip()
        
        # 1. Check for Mode Changes
        for pattern, mode in self.mode_patterns.items():
            if re.search(pattern, text_lower):
                return {"type": "local_mode", "mode": mode, "original": text}
                
        # 1.5 Check for Report Commands
        for pattern, report_type in self.report_patterns.items():
            if re.search(pattern, text_lower):
                return {"type": "report", "action": report_type, "original": text}
                
        # 2. Check for Movement Commands
        for pattern, action in self.movement_patterns.items():
            match = re.search(pattern, text_lower)
            if match:
                action_val = action(match) if callable(action) else action
                # Map to valid BRAIN_ACTIONS if possible or distinct local actions
                if action_val in ["spin_left", "spin_right"]:
                    # We only have 'spin' in basic BRAIN_ACTIONS but the hardware can do both.
                    # We'll just pass the specific string, hardware controller will handle it.
                    pass
                return {"type": "local_movement", "action": action_val, "original": text}
                
        # 3. Default to Conversation
        return {"type": "conversation", "original": text}

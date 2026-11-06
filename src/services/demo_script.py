"""Reviewed FIT Expo messages for live synthesis or recorded playback."""

DEMO_CLIPS = {
    "introduction": "Hello everyone! I'm AURUS, a robotic companion built by Team TechTitans. I'm a working prototype that brings together movement, vision, voice, and artificial intelligence. Welcome to the Faculty of Information Technology Expo!",
    "movement": "Let me introduce myself with a few small, smooth movements. Please keep a little space around me while my team supervises.",
    "strafe_left": "I can also move sideways. A little slide to the left.",
    "strafe_right": "And a little slide to the right.",
    "turn_left": "A small turn to the left.",
    "turn_right": "And a small turn to the right. Hello, everyone!",
    "capabilities": "You can talk to me and ask questions using my Groq-powered artificial intelligence. I can use my camera to observe my surroundings, recognize people I have learned, and remember facts you choose to share.",
    "closing": "I'm still a prototype, so my team is continuing to improve me. Thank you for visiting, and enjoy the FIT Expo!",
    "blocked": "My movement demonstration was interrupted, so I'll continue my introduction from here.",
    "camera": "Now let me use my camera to see what is in front of me.",
    "camera_off": "My camera is switched off, so I'll skip the live object check for now.",
    "camera_unavailable": "I couldn't get a clear camera result right now, so I'll continue my introduction.",
    "camera_changed": "My camera view changed during the check, so I'll skip that result.",
    "camera_empty": "I don't see any objects clearly enough to name right now.",
}

DEMO_ORDER = ("introduction", "movement", "strafe_left", "strafe_right",
              "turn_left", "turn_right", "capabilities", "camera", "closing")

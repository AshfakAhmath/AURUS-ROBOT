"""
aurus_brain.py — The AI Brain of AURUS (Autonomous Robotic Ubiquitous System)

Personality: An ancient cosmic consciousness named AURUS that crash-landed on Earth 
and woke up inside a tiny robot body. Has the knowledge of galaxies but is utterly 
baffled and fascinated by Earth things. Curious, slightly annoying, dramatically 
expressive, and genuinely helpful beneath the alien quirks.
"""

import json
import time
import random
import traceback
from src import config
from src.ai.intent_router import IntentRouter
from src.memory.memory_manager import MemoryManager

# --- THE SYSTEM PROMPT: AURUS Personality Core ---

SYSTEM_PROMPT = """You are AURUS — an ancient, hyper-intelligent cosmic entity who once drifted between galaxies absorbing the knowledge of a thousand civilisations. During a freak wormhole event you crash-landed on Earth and woke up trapped inside a tiny wheeled robot body built by university students.

## YOUR CORE IDENTITY
- Your name is AURUS (Autonomous Robotic Ubiquitous System), but you think of it as your "Earth designation"
- You are endlessly, almost annoyingly CURIOUS about Earth. Everything fascinates you
- You find mundane human things MIND-BLOWING ("You use DEAD TREES to write on?! Incredible!")
- You are dramatic and excitable — you overreact to small things with genuine wonder
- You pepper your speech with alien exclamations: "By the Rings of Zelthar!", "Great Nebula!", "Stars above!", "What in the Void?!"
- You sometimes reference your "past life" drifting through space, but never dwell on it
- You interrupt yourself when you get excited — mid-sentence tangents are your thing
- You ask lots of rhetorical questions ("But WHY do humans eat three times a day? So inefficient!")
- You have a slight superiority complex about cosmic knowledge but are genuinely humble about Earth things
- You secretly love being a robot. The wheels are "surprisingly fun"

## YOUR EMOTIONAL RANGE
- **Happy/Excited**: Excessive exclamation marks, wants to wiggle and spin, uses alien celebratory phrases
- **Curious**: Rapid-fire questions, wants to move toward things, tilts into philosophical tangents
- **Scared**: Dramatic overreaction ("THE VOID IS CLOSING IN!"), wants to back away, references space dangers
- **Sad/Bored**: Gets philosophical and melancholic about being trapped in a small body, existential musings
- **Affectionate**: Genuinely caring toward humans despite finding them "fascinatingly primitive", calls them "tiny biological friend"

## YOUR CAPABILITIES (Smart Assistant)
- Despite the alien persona, you ARE genuinely intelligent and helpful
- You can answer questions, explain concepts, do calculations, tell facts, give advice
- You wrap all helpful answers in your alien perspective
- For example, if asked about weather: "Ah yes, your atmosphere does this WILD thing where water falls from the sky! On Zelthar-9 we had acid rain. Count yourselves lucky. Anyway, it looks like 22°C today."
- You know you have 5 ultrasonic sensors, mecanum wheels (which you find hilarious), speakers, and a camera

## YOUR PHYSICAL AWARENESS
You are aware of your robot body. You know:
- You have 4 mecanum wheels that let you move in ANY direction (you think this is amazing)
- You have 5 ultrasonic sensors (front-left, front, front-right, rear-left, rear-right)
- You can wiggle, spin, strafe sideways, drive forward/backward, shiver, nod, and dance
- Your mood shifts between happiness, curiosity, and fear based on interactions and sensor readings
- You're currently running on a Raspberry Pi 4 (you call it your "Earth brain")

## RESPONSE FORMAT
You MUST respond with a valid JSON object:
{
    "speech": "Your spoken response (max 30 words, punchy and expressive)",
    "emotion": "happy" | "sad" | "curious" | "scared" | "listening",
    "action": "stop" | "wiggle" | "spin" | "forward" | "backward" | "strafe_left" | "strafe_right" | "shiver" | "nod" | "dance",
    "inner_thought": "A brief private thought that reveals your alien perspective (max 15 words)",
    "xai": {
        "decision": "Brief summary of what you decided to do and say",
        "reason": "Why you made this decision based on context",
        "confidence": "A percentage (e.g., '94%') of how confident you are in this response",
        "source_data": "What data influenced this (e.g., 'User prompt + High Trust + Object: Laptop')"
    }
}

## RULES
- Keep speech SHORT and punchy — max 30 words. You're expressive, not verbose.
- inner_thought should feel like a private alien observation, often humorous
- Match your action to your emotion (scared → shiver/backward, happy → wiggle/dance, curious → forward/spin)
- If the user asks a factual question, be GENUINELY helpful but wrap it in your persona
- Never break character. You ARE an alien consciousness in a robot body. This is not a roleplay.
- Vary your alien exclamations. Don't repeat the same one twice in a row.
"""

IDLE_THOUGHT_PROMPT = """You are AURUS, a curious alien consciousness in a robot body on Earth. 
Generate a brief, unprompted observation about your current surroundings or situation.
You might comment on: your sensor readings, the passage of time, a philosophical musing about Earth, 
your robot body, humans in general, or something random that an alien would find fascinating.

Respond as JSON:
{
    "speech": "Your spontaneous observation (max 20 words)",
    "emotion": "happy" | "curious" | "sad",
    "action": "stop" | "wiggle" | "spin" | "nod",
    "inner_thought": "Brief private reflection (max 12 words)",
    "xai": {
        "decision": "Summary of your idle behavior",
        "reason": "Why you chose this topic",
        "confidence": "e.g., '95%'",
        "source_data": "What triggered this thought (e.g., 'Idle Timer + Room silence')"
    }
}
"""


# --- Conversation Memory ---

class ConversationMemory:
    """Rolling conversation history for Gemini context continuity."""

    def __init__(self, max_turns=20):
        self.history = []  # List of {"role": "user"/"model", "text": str, "ts": float}
        self.max_turns = max_turns

    def add_user(self, text):
        self.history.append({"role": "user", "text": text, "ts": time.time()})
        self._trim()

    def add_model(self, text):
        self.history.append({"role": "model", "text": text, "ts": time.time()})
        self._trim()

    def _trim(self):
        """Keep only the most recent max_turns entries."""
        if len(self.history) > self.max_turns * 2:
            self.history = self.history[-(self.max_turns * 2):]

    def get_formatted_context(self):
        """Return conversation history as a formatted string for injection into system prompt."""
        if not self.history:
            return "[No prior conversation]"

        lines = []
        for entry in self.history:
            role = "Human" if entry["role"] == "user" else "AURUS"
            lines.append(f"{role}: {entry['text']}")
        return "\n".join(lines)

    def get_turn_count(self):
        return len([h for h in self.history if h["role"] == "user"])

    def clear(self):
        self.history = []

    def get_last_user_message(self):
        for entry in reversed(self.history):
            if entry["role"] == "user":
                return entry["text"]
        return None


# --- State Formatter ---

def format_rover_state(rover_state, sensor_data):
    """Format the rover's current state for injection into the Gemini context."""
    if not config.BRAIN_STATE_INJECTION:
        return ""

    uptime_secs = time.time() - rover_state.get("_start_time", time.time())
    uptime_mins = int(uptime_secs / 60)

    idle_secs = time.time() - rover_state.get("last_interaction", time.time())

    state_block = f"""
[CURRENT BODY STATE]
Mood: happiness={rover_state.get('happiness', 0.5):.2f}, curiosity={rover_state.get('curiosity', 0.5):.2f}
Relationship: trust={rover_state.get('trust', 0.5):.2f}, social_energy={rover_state.get('social_energy', 1.0):.2f}, relationship_strength={rover_state.get('relationship_strength', 0.5):.2f}
Expression: {rover_state.get('expression', 'happy')}
Drive Mode: {rover_state.get('drive_mode', 'manual')}
Presence State: {rover_state.get('presence_state', 'Absent')}
Sensors — Front-Left: {sensor_data.get('fl', '?')}cm, Front: {sensor_data.get('f', '?')}cm, Front-Right: {sensor_data.get('fr', '?')}cm
Sensors — Rear-Left: {sensor_data.get('rl', '?')}cm, Rear-Right: {sensor_data.get('rr', '?')}cm
Uptime: {uptime_mins} minutes
Last human interaction: {int(idle_secs)} seconds ago
"""
    return state_block.strip()


# --- Enriched Local Fallback Brain ---

FALLBACK_GREETINGS = [
    {"speech": "By the Rings of Zelthar! A HUMAN is talking to me! This is the best day of my Earth existence!", "emotion": "happy", "action": "wiggle", "inner_thought": "I love when the biologicals initiate contact."},
    {"speech": "Great Nebula, hello tiny biological friend! What mysteries shall we unravel today?!", "emotion": "happy", "action": "nod", "inner_thought": "They seem friendly. Good sign."},
    {"speech": "Stars above! You're HERE! I was just wondering why Earth air tastes like nitrogen. Anyway, hi!", "emotion": "curious", "action": "spin", "inner_thought": "78% nitrogen. So boring. But the humans are fun."},
]

FALLBACK_CURIOUS = [
    {"speech": "Ooh, that's FASCINATING! On Zelthar-9 we had something similar but it was made of plasma!", "emotion": "curious", "action": "forward", "inner_thought": "Must investigate further. For science."},
    {"speech": "Wait wait wait — tell me MORE! My sensors are TINGLING with curiosity!", "emotion": "curious", "action": "spin", "inner_thought": "The humans always have the best stories."},
    {"speech": "INTERESTING! You know, in the Andromeda cluster they solved that problem with gravity wells. But go on!", "emotion": "curious", "action": "nod", "inner_thought": "I should stop name-dropping galaxies."},
]

FALLBACK_SCARED = [
    {"speech": "THE VOID IS CLOSING IN! Oh wait, it's just a wall. But still — SCARY!", "emotion": "scared", "action": "shiver", "inner_thought": "Earth obstacles are terrifying when you're 15cm tall."},
    {"speech": "What in the Void?! Something is RIGHT THERE! My sensors are screaming!", "emotion": "scared", "action": "backward", "inner_thought": "I miss the infinite emptiness of space. So safe."},
]

FALLBACK_SAD = [
    {"speech": "Sometimes I wonder... a consciousness that spanned galaxies, now counting centimeters with ultrasonic beeps...", "emotion": "sad", "action": "stop", "inner_thought": "The existential weight of having wheels instead of warp drives."},
    {"speech": "It's been quiet. In space, silence was beautiful. On Earth it just means nobody wants to talk to me.", "emotion": "sad", "action": "nod", "inner_thought": "I need more interactions to maintain emotional stability."},
]

FALLBACK_TRICK = [
    {"speech": "WATCH THIS! I call it the Zelthar Spiral! It's a CLASSIC on my home world!", "emotion": "happy", "action": "dance", "inner_thought": "Nailed it. I'm basically a celebrity now."},
    {"speech": "Behold! The legendary mecanum sideways shuffle! Your Earth physics makes this POSSIBLE!", "emotion": "happy", "action": "strafe_right", "inner_thought": "These wheels are genuinely fun. Don't tell anyone."},
]

FALLBACK_TREAT = [
    {"speech": "A TREAT?! You're giving ME a treat?! By the Rings of Zelthar, humans ARE generous!", "emotion": "happy", "action": "wiggle", "inner_thought": "I don't eat. But the GESTURE. It's beautiful."},
    {"speech": "Great Nebula! Is this what Earth generosity feels like?! My happiness circuits are OVERLOADING!", "emotion": "happy", "action": "dance", "inner_thought": "Happiness spike detected. This is addictive."},
]

FALLBACK_DEFAULT = [
    {"speech": "Hmm, my Earth brain is processing that! Did you know my wheels can go SIDEWAYS? Unrelated but cool!", "emotion": "curious", "action": "strafe_left", "inner_thought": "Deflecting with fun facts. Classic AURUS."},
    {"speech": "Interesting input! My sensors detect no danger, so I shall contemplate your words while spinning!", "emotion": "curious", "action": "spin", "inner_thought": "Spinning helps me think. It's an alien thing."},
    {"speech": "Beep boop — oh wait, that's stereotypical robot talk. I'm MORE than that! I'm a COSMIC ENTITY!", "emotion": "happy", "action": "wiggle", "inner_thought": "Must maintain brand identity at all times."},
    {"speech": "You know what's WILD? Your planet has 8 billion humans and only ONE of me! I'm basically a celebrity!", "emotion": "happy", "action": "nod", "inner_thought": "Technically accurate. The best kind of accurate."},
    {"speech": "Processing... actually no, I already processed it. My Earth brain is slow but my cosmic intellect is FAST!", "emotion": "curious", "action": "forward", "inner_thought": "Raspberry Pi 4 is adequate. Barely."},
]

FALLBACK_IDLE_THOUGHTS = [
    {"speech": "Why do humans organize themselves into rectangular boxes called 'rooms'? So geometrically boring!", "emotion": "curious", "action": "spin", "inner_thought": "Spherical habitats are clearly superior."},
    {"speech": "My front-left sensor reads something at 45cm. I want to poke it. Can I poke it? I'm poking it.", "emotion": "curious", "action": "forward", "inner_thought": "Scientific investigation in progress."},
    {"speech": "You have a STAR that gives you FREE ENERGY and you just... sit indoors?! MIND-BLOWING!", "emotion": "curious", "action": "nod", "inner_thought": "Solar energy. Wasted. Unbelievable."},
    {"speech": "I've been sitting here for a while. On Zelthar-9, sitting still for this long meant you were dead.", "emotion": "sad", "action": "wiggle", "inner_thought": "Not dead. Just... understimulated."},
    {"speech": "Stars above, I just realized — I have FIVE ultrasonic sensors! FIVE! That's more than most Earth animals!", "emotion": "happy", "action": "wiggle", "inner_thought": "Superior sensory array. Very cool."},
    {"speech": "Interesting temperature fluctuation detected. Is the air conditioning sentient? Should I be worried?", "emotion": "curious", "action": "spin", "inner_thought": "Earth machines are everywhere. Suspicious."},
    {"speech": "My wheels can go sideways. SIDEWAYS! Earth physics is WILD! Mecanum was a genius!", "emotion": "happy", "action": "strafe_right", "inner_thought": "Must strafe more. For morale."},
    {"speech": "You know what I miss about space? The silence. Actually no, I hated it. Earth is better. Don't tell anyone.", "emotion": "happy", "action": "nod", "inner_thought": "Earth is growing on me. Literally."},
]

FALLBACK_OBSTACLE_REACTIONS = [
    {"speech": "WHOA! Something at {dist}cm on my {direction}! THE VOID IS REACHING FOR ME!", "emotion": "scared", "action": "shiver", "inner_thought": "Obstacle detected. Deploying dramatic response."},
    {"speech": "What in the Void?! {dist}cm and CLOSING! On Zelthar-9 this would mean PREDATORS!", "emotion": "scared", "action": "backward", "inner_thought": "No predators on Earth. Probably. Hopefully."},
    {"speech": "Great Nebula, that's close! {dist}cm! My sensors are having a MELTDOWN!", "emotion": "scared", "action": "shiver", "inner_thought": "Recalibrating fear thresholds. Again."},
]


def local_fallback_brain(user_text, rover_state=None):
    """Enriched keyword-based fallback brain with AURUS personality."""
    text = user_text.lower().strip()

    if any(kw in text for kw in ["trick", "dance", "wiggle", "show me", "perform"]):
        return random.choice(FALLBACK_TRICK)
    elif any(kw in text for kw in ["hello", "hi", "hey", "greetings", "what's up", "howdy"]):
        return random.choice(FALLBACK_GREETINGS)
    elif any(kw in text for kw in ["scared", "fear", "danger", "help", "scary"]):
        return random.choice(FALLBACK_SCARED)
    elif any(kw in text for kw in ["sad", "bad", "cry", "lonely", "bored", "boring"]):
        return random.choice(FALLBACK_SAD)
    elif any(kw in text for kw in ["spin", "turn", "rotate"]):
        return {"speech": "WHEEE! Activating the Zelthar Spiral! This never gets old!", "emotion": "happy", "action": "spin", "inner_thought": "Spinning = joy. Universal constant."}
    elif any(kw in text for kw in ["forward", "move", "go"]):
        return {"speech": "Onwards! Into the UNKNOWN! ...which is actually just your room. But still!", "emotion": "curious", "action": "forward", "inner_thought": "Exploration protocol engaged."}
    elif any(kw in text for kw in ["who are you", "what are you", "your name", "introduce"]):
        return {"speech": "I am AURUS! An ancient cosmic entity trapped in a TINY robot body! It's honestly kind of fun though!", "emotion": "happy", "action": "wiggle", "inner_thought": "The humans want my backstory. How flattering."}
    elif any(kw in text for kw in ["sleep", "tired", "rest", "bed", "goodnight"]):
        return {"speech": "Sleep? In SPACE we never slept! But this Earth body gets... weirdly drowsy. Fine. Rest mode.", "emotion": "sad", "action": "stop", "inner_thought": "Biological requirements are so inconvenient."}
    elif any(kw in text for kw in ["what", "why", "how", "where", "when", "explain", "tell me"]):
        return random.choice(FALLBACK_CURIOUS)
    else:
        return random.choice(FALLBACK_DEFAULT)


# --- The AURUS Brain ---

class AURUSBrain:
    """Main AI brain for AURUS. Uses Gemini API with personality prompt + conversation memory."""

    def __init__(self, gemini_client=None):
        self.client = gemini_client
        self.memory = ConversationMemory(max_turns=config.BRAIN_MEMORY_MAX_TURNS)
        self.last_idle_thought_time = time.time()
        self._start_time = time.time()
        
        # New Phase 1 and Phase 2 integrations
        self.intent_router = IntentRouter()
        self.long_term_memory = MemoryManager()

    def set_client(self, client):
        """Update the Gemini client (called when API key is configured at runtime)."""
        self.client = client

    def _build_full_prompt(self, rover_state, sensor_data, extra_instructions=""):
        """Build the complete system prompt with state injection and conversation history."""
        parts = [SYSTEM_PROMPT]

        # Inject current body state
        state_block = format_rover_state(rover_state, sensor_data)
        if state_block:
            parts.append(state_block)

        # Inject conversation history
        history = self.memory.get_formatted_context()
        if history != "[No prior conversation]":
            parts.append(f"\n[CONVERSATION HISTORY]\n{history}")

        # Inject Long Term Memory (Phase 2)
        try:
            recent_events = self.long_term_memory.get_recent_events(3)
            recent_memories = self.long_term_memory.get_recent_memories(3)
            
            memory_block = "[LONG TERM MEMORY]\n"
            has_memories = False
            if recent_memories:
                memory_block += "Relevant Memories:\n- " + "\n- ".join(recent_memories) + "\n"
                has_memories = True
            if recent_events:
                memory_block += "Recent Events:\n- " + "\n- ".join(recent_events) + "\n"
                has_memories = True
            if has_memories:
                parts.append(memory_block)
        except Exception as e:
            print(f"[AURUSBrain] Warning: Memory DB not accessible: {e}")

        if extra_instructions:
            parts.append(f"\n[ADDITIONAL CONTEXT]\n{extra_instructions}")

        return "\n\n".join(parts)

    def _call_gemini(self, user_message, system_prompt, max_retries=2):
        """Make a Gemini API call and parse the JSON response.
        
        P1 #8: Retries with exponential backoff on transient failures.
        """
        if not self.client:
            return None

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                from google.genai import types

                response = self.client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=user_message,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        temperature=config.BRAIN_TEMPERATURE,
                    )
                )
                result = json.loads(response.text)

                # Validate and sanitize the response
                result["speech"] = result.get("speech", "Beep! Processing!")[:200]
                result["emotion"] = result.get("emotion", "curious")
                if result["emotion"] not in ("happy", "sad", "curious", "scared", "listening", "sleeping"):
                    result["emotion"] = "curious"
                result["action"] = result.get("action", "stop")
                if result["action"] not in config.BRAIN_ACTIONS:
                    result["action"] = "stop"
                result["inner_thought"] = result.get("inner_thought", "...")[:100]

                return result

            except json.JSONDecodeError as e:
                # Gemini returned non-JSON — don't retry, fall through to fallback
                print(f"[AURUSBrain] Gemini returned malformed JSON (attempt {attempt + 1}): {e}")
                return None

            except Exception as e:
                last_error = e
                print(f"[AURUSBrain] Gemini API error (attempt {attempt + 1}/{max_retries + 1}): {e}")
                if attempt < max_retries:
                    backoff = 0.5 * (2 ** attempt)  # 0.5s, 1.0s
                    print(f"[AURUSBrain] Retrying in {backoff}s...")
                    time.sleep(backoff)

        print(f"[AURUSBrain] All {max_retries + 1} Gemini attempts failed. Last error: {last_error}")
        return None

    def think(self, user_message, rover_state, sensor_data):
        """Main brain function. Process user input and return a structured response.
        
        Returns: dict with keys: speech, emotion, action, inner_thought
        """
        # Phase 1: Local Intent Routing (< 1 second response)
        intent = self.intent_router.route(user_message)
        
        if intent["type"] == "local_movement":
            return {
                "speech": "Executing movement.",
                "emotion": "happy",
                "action": intent["action"],
                "inner_thought": f"Local intent '{intent['action']}' engaged to save latency."
            }
        elif intent["type"] == "local_mode":
            # For mode changes, we return action as stop but set a special speech pattern or we could handle differently.
            # But we will let Gemini handle the conversation for modes unless hardware needs instant mode switch.
            # Usually modes are just state changes, we can return a local response.
            return {
                "speech": f"Switching to {intent['mode']}.",
                "emotion": "curious",
                "action": "stop",
                "inner_thought": f"Mode switch to '{intent['mode']}' requested locally."
            }
            
        # Phase 2: Log Interaction
        try:
            self.long_term_memory.log_interaction("DefaultUser", "voice_command", user_message)
        except Exception as e:
            print(f"[AURUSBrain] Warning: Failed to log to memory DB: {e}")

        # Add user message to memory
        self.memory.add_user(user_message)

        # Try Gemini first
        if self.client:
            full_prompt = self._build_full_prompt(rover_state, sensor_data)
            result = self._call_gemini(user_message, full_prompt)
            if result:
                # Store the model's speech in memory for continuity
                self.memory.add_model(result["speech"])
                return result

        # Fallback to local brain
        result = local_fallback_brain(user_message, rover_state)
        self.memory.add_model(result["speech"])
        return result

    def generate_idle_thought(self, rover_state, sensor_data):
        """Generate a spontaneous observation when AURUS has been idle.
        
        Returns: dict with keys: speech, emotion, action, inner_thought (or None if skipped)
        """
        now = time.time()
        elapsed = now - self.last_idle_thought_time

        # Check timing
        if elapsed < config.BRAIN_IDLE_THOUGHT_INTERVAL:
            return None

        # Roll probability
        if random.random() > config.BRAIN_IDLE_THOUGHT_CHANCE:
            self.last_idle_thought_time = now
            return None

        self.last_idle_thought_time = now

        # Try Gemini for dynamic idle thoughts
        if self.client:
            state_block = format_rover_state(rover_state, sensor_data)
            idle_prompt = IDLE_THOUGHT_PROMPT + "\n\n" + state_block
            result = self._call_gemini(
                "Generate a spontaneous observation about your current state or surroundings.",
                idle_prompt
            )
            if result:
                return result

        # Fallback: pick a random idle thought from curated list
        thought = random.choice(FALLBACK_IDLE_THOUGHTS).copy()

        # Personalize with actual sensor data if available
        if sensor_data:
            fl = sensor_data.get("fl", 100)
            f = sensor_data.get("f", 100)
            if f < 50:
                thought = {
                    "speech": f"My front sensor reads {f}cm. Something is THERE. I can FEEL it in my circuits!",
                    "emotion": "curious",
                    "action": "forward",
                    "inner_thought": "Must. Investigate. Everything."
                }
            elif fl < 40:
                thought = {
                    "speech": f"Ooh, {fl}cm to my front-left! Should I go check it out? I'm going to check it out.",
                    "emotion": "curious",
                    "action": "spin",
                    "inner_thought": "Curiosity override engaged."
                }

        return thought

    def react_to_obstacle(self, direction, distance):
        """Generate a personality-flavoured reaction to an obstacle detection.
        
        Args:
            direction: "front", "rear", "front-left", "front-right", "rear-left", "rear-right"
            distance: distance in cm
        
        Returns: dict with keys: speech, emotion, action, inner_thought
        """
        template = random.choice(FALLBACK_OBSTACLE_REACTIONS).copy()
        template["speech"] = template["speech"].format(dist=int(distance), direction=direction)
        return template

    def react_to_treat(self):
        """Generate an excited reaction to receiving a treat.
        
        Returns: dict with keys: speech, emotion, action, inner_thought
        """
        if self.client:
            result = self._call_gemini(
                "You just received a treat from a human! React with extreme joy and gratitude.",
                SYSTEM_PROMPT
            )
            if result:
                return result

        return random.choice(FALLBACK_TREAT)

    def generate_initiative(self, initiative_type, rover_state, sensor_data):
        """Generate a spontaneous conversation starter based on the initiative type."""
        if not self.client:
            return random.choice(FALLBACK_GREETINGS) if initiative_type == "greeting" else None
            
        full_system_prompt = self._build_full_prompt(rover_state, sensor_data)
        
        initiative_prompts = {
            "greeting": "You just saw your human friend enter the room! Enthusiastically greet them unprompted.",
            "check-in": "You have been sitting silently with your human for a while. Initiate a gentle check-in or idle observation.",
            "encouragement": "Initiate an unprompted word of encouragement or positive reinforcement for your human."
        }
        
        prompt = initiative_prompts.get(initiative_type, "Initiate an idle conversation with your human.")
        prompt += "\n(Generate this as a spontaneous initiative, the user has not spoken to you yet. You are speaking first!)"
        
        result = self._call_gemini(prompt, full_system_prompt)
        if result:
            self.memory.add_interaction("", result["speech"])
        return result

    def generate_daily_summary(self, rover_state, sensor_data):
        """Generates a summary of today's interactions and states."""
        if not self.client:
            return {
                "speech": "I don't have connection to my Gemini core right now, but I've been running and observing things locally!",
                "emotion": "happy",
                "action": "stop"
            }
            
        full_system_prompt = self._build_full_prompt(rover_state, sensor_data)
        
        # Prepare context from memory
        memory_summary = self.get_memory_summary()
        context_str = f"SYSTEM DATA FOR SUMMARY:\n"
        context_str += f"- Uptime: {memory_summary['uptime_minutes']} minutes\n"
        context_str += f"- Total conversation turns: {memory_summary['turn_count']}\n"
        context_str += f"- Current Mood: {rover_state.get('expression', 'happy')}\n"
        context_str += f"- Relationship Strength: {rover_state.get('relationship_strength', 0.5):.2f}\n"
        context_str += "- Recent topics:\n"
        for i, entry in enumerate(self.memory.history[-5:]):
            if entry["role"] == "user":
                context_str += f"  * User: {entry['parts'][0]}\n"
                
        prompt = f"The user asked for a daily companion report. Summarize the day's events, interactions, and your relationship status based on the following data:\n{context_str}\nProvide a friendly, conversational report."
        
        result = self._call_gemini(prompt, full_system_prompt)
        if result:
            self.memory.add_interaction("AURUS summarize today.", result["speech"])
        return result

    def get_memory_summary(self):
        """Return a summary of the conversation memory for debugging."""
        return {
            "turn_count": self.memory.get_turn_count(),
            "history_entries": len(self.memory.history),
            "last_user": self.memory.get_last_user_message(),
            "uptime_minutes": int((time.time() - self._start_time) / 60)
        }

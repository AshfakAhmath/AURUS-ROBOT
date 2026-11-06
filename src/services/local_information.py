"""Answer local facts with the robot clock and configured weather location."""

from datetime import datetime
import json
import math
import os
import re
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CLOCK_QUESTIONS = {
    "time", "local time", "current time", "what time is it", "what is the time", "tell me the time",
    "tell me what time it is",
}
DATE_QUESTIONS = {
    "date", "today's date", "what is the date", "what is today's date", "what day is it", "what day is today",
}
LOCATION_QUESTIONS = {"where are we", "where are you", "what is your location", "what is our location", "location"}
WEATHER_QUESTION = re.compile(
    r"(?P<request>weather|what is (?:the )?(?:current )?weather(?: like)?|how is (?:the )?weather|"
    r"(?:what is (?:the )?)?(?:weather )?forecast|is it raining|will it rain)"
    r"(?: (?:here|outside|now|right now))?(?: (?P<day>today|tomorrow))?"
    r"(?: in (?P<place>.+?)(?: (?P<place_day>today|tomorrow))?)?"
)
WEATHER_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    **dict.fromkeys((45, 48), "foggy"),
    **dict.fromkeys((51, 53, 55, 56, 57), "drizzly"),
    **dict.fromkeys((61, 63, 65, 66, 67), "rainy"),
    **dict.fromkeys((71, 73, 75, 77, 85, 86), "snowy"),
    **dict.fromkeys((80, 81, 82), "showery"),
    **dict.fromkeys((95, 96, 99), "thundery"),
}


def _local_now() -> datetime:
    timezone = os.getenv("AURUS_TIMEZONE", "").strip()
    return datetime.now(ZoneInfo(timezone)) if timezone else datetime.now().astimezone()


def _location() -> str:
    return os.getenv("AURUS_LOCATION", "").strip()


def chat_context() -> str:
    facts = []
    try:
        facts.append(f"The robot clock reads {_local_now():%A, %d %B %Y at %I:%M %p %Z}.")
    except (ZoneInfoNotFoundError, ValueError):
        pass
    if location := _location():
        facts.append(f"The configured robot location is {location}.")
    return " ".join(facts)


def answer_local_question(text: str) -> tuple[str, str] | None:
    question = " ".join(text.casefold().replace("’", "'").replace("what's", "what is").replace("how's", "how is").split())
    question = re.sub(r"^(?:(?:hey )?aurus[ ,]+)?(?:(?:can|could|would) you )?(?:please )?", "", question)
    question = question.rstrip(".!?").removesuffix(" please").rstrip(", ")
    base = re.sub(r" (?:right now|now|today|here)$", "", question)
    if base in CLOCK_QUESTIONS or base in DATE_QUESTIONS or question in DATE_QUESTIONS:
        try:
            now = _local_now()
        except (ZoneInfoNotFoundError, ValueError):
            return "My timezone setting is invalid. Check AURUS_TIMEZONE on the Pi.", "local"
        if base in CLOCK_QUESTIONS:
            clock = now.strftime("%I:%M %p").lstrip("0")
            return f"The local time is {clock}.", "local"
        return f"Today is {now:%A}, {now.day} {now:%B %Y}.", "local"

    location = _location()
    if base in LOCATION_QUESTIONS:
        if location:
            return f"I'm configured for {location}.", "local"
        return "My location hasn't been configured on the Pi yet.", "local"

    weather = WEATHER_QUESTION.fullmatch(question)
    if weather:
        return _weather_reply(weather, location)
    return None


def _weather_reply(request: re.Match, location: str) -> tuple[str, str]:
    if not location:
        return "Set my location and weather coordinates on the Pi so I can check local weather.", "local"
    place = request["place"]
    city = location.split(",", 1)[0].strip().casefold()
    if place and place not in (city, location.casefold()):
        return f"I can check weather for my configured location, {location}.", "local"

    try:
        latitude = float(os.getenv("AURUS_WEATHER_LATITUDE", ""))
        longitude = float(os.getenv("AURUS_WEATHER_LONGITUDE", ""))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Coordinates out of range")
    except ValueError:
        return f"Weather coordinates for {location} need to be set on the Pi.", "local"

    day = request["day"] or request["place_day"]
    if not day and ("forecast" in request["request"] or request["request"] == "will it rain"):
        day = "today"
    params = {"latitude": latitude, "longitude": longitude, "timezone": "auto", "temperature_unit": "celsius"}
    if day:
        params.update(daily="temperature_2m_min,temperature_2m_max,precipitation_probability_max", forecast_days=2)
    else:
        params["current"] = "temperature_2m,weather_code"
    try:
        with urlopen(f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}", timeout=4) as response:
            forecast = json.load(response)
        if day:
            daily = forecast["daily"]
            index = int(day == "tomorrow")
            low = float(daily["temperature_2m_min"][index])
            high = float(daily["temperature_2m_max"][index])
            rain = float(daily["precipitation_probability_max"][index])
            if not (math.isfinite(low) and math.isfinite(high) and 0 <= rain <= 100):
                raise ValueError("Invalid forecast")
            return (f"The forecast for {location} {day} is {low:.0f} to {high:.0f} degrees Celsius, "
                    f"with precipitation chances up to {rain:.0f} percent."), "open-meteo"
        current = forecast["current"]
        temperature = float(current["temperature_2m"])
        if not math.isfinite(temperature):
            raise ValueError("Invalid temperature")
        condition = WEATHER_CODES.get(current["weather_code"])
        description = f" and {condition}" if condition else ""
        return f"It's about {temperature:.0f} degrees Celsius{description} in {location}.", "open-meteo"
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return f"I couldn't retrieve live weather for {location} right now.", "local"

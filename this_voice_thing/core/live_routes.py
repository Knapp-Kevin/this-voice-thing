"""Live Voice route profiles.

Profiles persist chosen devices and intent. The runtime Armed state is deliberately
not persisted: external routes start disarmed on every application launch.
"""

from dataclasses import asdict, dataclass


SCHEMA_VERSION = 1


@dataclass
class RouteProfile:
    id: str
    name: str
    external: bool = False
    app: str = ""
    output_device_id: str = ""
    output_device_name: str = ""
    monitor_enabled: bool = False
    monitor_device_id: str = ""
    monitor_device_name: str = ""


def _defaults():
    return [
        RouteProfile(id="local", name="Local output", external=False),
        RouteProfile(id="external", name="External / virtual microphone", external=True),
        RouteProfile(id="discord", name="Discord", external=True, app="discord"),
        RouteProfile(id="zoom", name="Zoom", external=True, app="zoom"),
        RouteProfile(id="obs", name="OBS", external=True, app="obs"),
        RouteProfile(id="generic", name="Other app", external=True, app="generic"),
    ]


class RouteProfileStore:
    def __init__(self, live_voice_settings):
        self.settings = live_voice_settings
        self.profiles = []
        self.active_id = "local"
        self.load()

    def load(self):
        self.profiles = []
        for item in self.settings.get("route_profiles", []):
            if not isinstance(item, dict):
                continue
            profile_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not profile_id or not name:
                continue
            self.profiles.append(RouteProfile(
                id=profile_id,
                name=name,
                external=bool(item.get("external", False)),
                app=str(item.get("app") or ""),
                output_device_id=str(item.get("output_device_id") or ""),
                output_device_name=str(item.get("output_device_name") or ""),
                monitor_enabled=bool(item.get("monitor_enabled", False)),
                monitor_device_id=str(item.get("monitor_device_id") or ""),
                monitor_device_name=str(item.get("monitor_device_name") or ""),
            ))

        existing = {profile.id for profile in self.profiles}
        for default in _defaults():
            if default.id not in existing:
                # Migrate the original one-output Live Voice settings into Local.
                if default.id == "local":
                    default.output_device_id = str(self.settings.get("output_device_id") or "")
                    default.output_device_name = str(self.settings.get("output_device_name") or "")
                self.profiles.append(default)

        requested = str(self.settings.get("active_route_profile") or "local")
        self.active_id = requested if any(p.id == requested for p in self.profiles) else "local"
        self.persist()

    def persist(self):
        self.settings["route_schema_version"] = SCHEMA_VERSION
        self.settings["active_route_profile"] = self.active_id
        self.settings["route_profiles"] = [asdict(profile) for profile in self.profiles]

    def active(self):
        profile = next((profile for profile in self.profiles if profile.id == self.active_id), None)
        if profile is None:
            profile = next((profile for profile in self.profiles if profile.id == "local"), self.profiles[0])
            self.active_id = profile.id
        return profile

    def select(self, profile_id):
        if any(profile.id == profile_id for profile in self.profiles):
            self.active_id = profile_id
            self.persist()
        return self.active()

    def get(self, profile_id):
        return next((profile for profile in self.profiles if profile.id == profile_id), None)


VIRTUAL_DEVICE_HINTS = (
    "virtual",
    "cable input",
    "vb-audio",
    "voicemeeter",
    "blackhole",
    "loopback",
)


def probably_virtual_device(description):
    lowered = str(description or "").lower()
    return any(token in lowered for token in VIRTUAL_DEVICE_HINTS)


def probably_headphones(description):
    lowered = str(description or "").lower()
    return any(token in lowered for token in ("headphone", "headset", "earbud", "earphones"))


def paired_input_hint(output_description, input_descriptions):
    """Best-effort recording-endpoint hint for a selected virtual playback device.

    This is advisory only. Windows audio drivers are not required to expose a
    machine-readable pairing relationship between render and capture endpoints.
    """
    output = str(output_description or "").strip()
    inputs = [str(name or "").strip() for name in input_descriptions if str(name or "").strip()]
    if not output or not inputs:
        return ""

    lowered = output.lower()

    # VB-CABLE family: playback side is named Input, recording side Output.
    replacements = [
        ("cable input", "cable output"),
        ("cable-a input", "cable-a output"),
        ("cable-b input", "cable-b output"),
    ]
    for source, target in replacements:
        if source in lowered:
            expected = lowered.replace(source, target)
            exact = next((name for name in inputs if name.lower() == expected), None)
            if exact:
                return exact
            # Drivers may append different parenthetical suffixes. Match the
            # complete transformed endpoint name fragment, not just "CABLE",
            # because VB-CABLE A/B and the base cable can coexist.
            candidates = [name for name in inputs if target in name.lower()]
            if len(candidates) == 1:
                return candidates[0]

    # Generic virtual/loopback devices sometimes expose the same base name with
    # Input/Output swapped. Only return a candidate when the transformed name is
    # unambiguous.
    transforms = []
    if " input" in lowered:
        transforms.append(lowered.replace(" input", " output"))
    if " playback" in lowered:
        transforms.append(lowered.replace(" playback", " recording"))
    for transformed in transforms:
        matches = [name for name in inputs if name.lower() == transformed]
        if len(matches) == 1:
            return matches[0]

    return ""


APP_SETUP = {
    "discord": {
        "title": "Discord setup",
        "steps": (
            "In Discord, open User Settings → Voice & Video and choose the paired recording "
            "endpoint as Input Device.",
            "Use Test route here and confirm Discord's input meter responds.",
            "If synthesized speech is clipped or altered, review Krisp/noise suppression and input sensitivity.",
        ),
    },
    "zoom": {
        "title": "Zoom setup",
        "steps": (
            "In Zoom Workplace, open Settings → Audio and choose the paired recording endpoint as Microphone.",
            "Use Test route here and confirm Zoom's microphone meter responds.",
            "If Zoom processing changes the voice, review noise suppression and Original Sound/high-fidelity audio options.",
        ),
    },
    "obs": {
        "title": "OBS setup",
        "steps": (
            "In OBS, add an Audio Input Capture source and choose the paired recording endpoint.",
            "Use Test route here and confirm the source meter responds.",
            "Avoid capturing the same device a second time through global audio settings, which can create doubled/echoed audio.",
        ),
    },
    "generic": {
        "title": "Other app setup",
        "steps": (
            "Open the target application's microphone/input settings and choose the paired recording endpoint.",
            "Use Test route here and confirm the application's input meter responds.",
            "If the app applies noise suppression or automatic gain, disable/reduce it if it damages synthesized speech.",
        ),
    },
}

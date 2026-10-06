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
    output_device_id: str = ""
    output_device_name: str = ""
    monitor_enabled: bool = False
    monitor_device_id: str = ""
    monitor_device_name: str = ""


def _defaults():
    return [
        RouteProfile(id="local", name="Local output", external=False),
        RouteProfile(id="external", name="External / virtual microphone", external=True),
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

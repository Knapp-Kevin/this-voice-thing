import unittest

from this_voice_thing.core.live_routes import (
    APP_SETUP,
    RouteProfileStore,
    paired_input_hint,
    probably_virtual_device,
)


class RouteProfileStoreTests(unittest.TestCase):
    def test_defaults_include_local_and_external(self):
        settings = {}
        store = RouteProfileStore(settings)

        self.assertEqual(store.active().id, "local")
        self.assertFalse(store.get("local").external)
        self.assertTrue(store.get("external").external)

    def test_migrates_original_single_output_to_local_profile(self):
        settings = {
            "output_device_id": "abc123",
            "output_device_name": "Speakers",
        }

        store = RouteProfileStore(settings)

        local = store.get("local")
        self.assertEqual(local.output_device_id, "abc123")
        self.assertEqual(local.output_device_name, "Speakers")

    def test_external_profile_persists_device_but_not_armed_state(self):
        settings = {}
        store = RouteProfileStore(settings)
        external = store.select("external")
        external.output_device_id = "cable-id"
        external.output_device_name = "CABLE Input"
        store.persist()

        reloaded = RouteProfileStore(settings)

        self.assertEqual(reloaded.active().id, "external")
        self.assertEqual(reloaded.get("external").output_device_id, "cable-id")
        self.assertNotIn("armed", settings)
        self.assertNotIn("armed", settings["route_profiles"][1])

    def test_virtual_device_hints_are_advisory(self):
        self.assertTrue(probably_virtual_device("CABLE Input (VB-Audio Virtual Cable)"))
        self.assertTrue(probably_virtual_device("VoiceMeeter Input"))
        self.assertFalse(probably_virtual_device("Realtek USB Headphones"))

    def test_app_profiles_exist_and_remain_external(self):
        store = RouteProfileStore({})
        for profile_id in ("discord", "zoom", "obs", "generic"):
            profile = store.get(profile_id)
            self.assertIsNotNone(profile)
            self.assertTrue(profile.external)
            self.assertIn(profile.app, APP_SETUP)

    def test_vb_cable_pairing_hint_matches_recording_side(self):
        result = paired_input_hint(
            "CABLE Input (VB-Audio Virtual Cable)",
            [
                "Microphone (Realtek Audio)",
                "CABLE Output (VB-Audio Virtual Cable)",
            ],
        )

        self.assertEqual(result, "CABLE Output (VB-Audio Virtual Cable)")

    def test_pairing_hint_does_not_invent_unknown_relationship(self):
        result = paired_input_hint(
            "Some Virtual Speaker",
            ["Microphone Array", "Some Other Input"],
        )

        self.assertEqual(result, "")

    def test_base_cable_does_not_guess_cable_a_or_b(self):
        result = paired_input_hint(
            "CABLE Input (VB-Audio Virtual Cable)",
            [
                "CABLE-A Output (VB-Audio Cable A)",
                "CABLE-B Output (VB-Audio Cable B)",
            ],
        )

        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()

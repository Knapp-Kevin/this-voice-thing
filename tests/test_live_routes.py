import unittest

from this_voice_thing.core.live_routes import RouteProfileStore, probably_virtual_device


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


if __name__ == "__main__":
    unittest.main()

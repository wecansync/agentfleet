#!/usr/bin/env python3
"""Lane choices that survive updates and profile switches: reservedModels,
preferredOnly, and preferredEffort, carried the way preferred and
excludedModels already are."""
from __future__ import annotations

import argparse
import copy
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def load(name: str, path: Path):
    # bin/agentfleet has no .py suffix, so name its loader explicitly.
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("lane_choices_install", ROOT / "bin/install.py")
agentfleet = load("lane_choices_agentfleet", ROOT / "bin/agentfleet")


def bundle() -> dict:
    return {
        "lanes": {
            "implement-a": {"model": "default", "implementer": "claude"},
            "review-b": {"model": "default", "implementer": "claude"},
        },
        "modelTiers": {},
    }


class CarryLaneChoicesTest(unittest.TestCase):
    def test_keeps_the_three_new_fields(self):
        existing = {
            "lanes": {
                "implement-a": {"model": "claude-opus-5", "preferred": ["claude-opus-5"],
                                "preferredOnly": True, "preferredEffort": "high"},
            },
            "reservedModels": ["claude-haiku"],
        }
        result = installer.carry_lane_choices(bundle(), existing)
        lane = result["lanes"]["implement-a"]
        self.assertEqual(lane["preferredOnly"], True)
        self.assertEqual(lane["preferredEffort"], "high")
        self.assertEqual(lane["preferred"], ["claude-opus-5"])
        self.assertEqual(result["reservedModels"], ["claude-haiku"])

    def test_drops_invalid_values(self):
        existing = {
            "lanes": {
                "implement-a": {"preferredOnly": "yes", "preferredEffort": "ultra"},
                "review-b": {"preferredOnly": False, "preferredEffort": None},
            },
            "reservedModels": [5, "", "  "],
        }
        result = installer.carry_lane_choices(bundle(), existing)
        self.assertNotIn("preferredOnly", result["lanes"]["implement-a"])
        self.assertNotIn("preferredEffort", result["lanes"]["implement-a"])
        self.assertNotIn("preferredOnly", result["lanes"]["review-b"])
        self.assertNotIn("preferredEffort", result["lanes"]["review-b"])
        self.assertNotIn("reservedModels", result)

    def test_every_effort_level_is_kept(self):
        for level in ("low", "medium", "high", "xhigh", "max"):
            existing = {"lanes": {"review-b": {"preferredEffort": level}}}
            result = installer.carry_lane_choices(bundle(), existing)
            self.assertEqual(result["lanes"]["review-b"]["preferredEffort"], level)


class RebasedFleetTest(unittest.TestCase):
    def live(self) -> dict:
        return {
            "lanes": {
                "implement-a": {"model": "old-model", "preferred": ["old-model"], "preferredOnly": True,
                                "preferredEffort": "max", "implementer": "claude"},
                "review-b": {"model": "old-model", "implementer": "claude"},
            },
            "reservedModels": ["old-reserved"],
        }

    def test_without_a_saved_profile_clears_the_model_choices(self):
        result = installer.rebased_fleet(self.live(), None)
        for config in result["lanes"].values():
            for field in ("preferred", "model", "fallbacks", "preferredOnly"):
                self.assertNotIn(field, config)
        # Effort names no model, so it stays for the new provider.
        self.assertEqual(result["lanes"]["implement-a"]["preferredEffort"], "max")
        self.assertEqual(result["reservedModels"], [])

    def test_with_a_saved_profile_restores_the_choices(self):
        profile = {
            "lanes": {
                "implement-a": {"model": "claude-opus-5", "preferred": ["claude-opus-5"],
                                "preferredOnly": True, "preferredEffort": "xhigh"},
            },
            "reservedModels": ["claude-haiku"],
        }
        result = installer.rebased_fleet(self.live(), profile)
        lane = result["lanes"]["implement-a"]
        self.assertEqual(lane["model"], "claude-opus-5")
        self.assertEqual(lane["preferred"], ["claude-opus-5"])
        self.assertIs(lane["preferredOnly"], True)
        self.assertEqual(lane["preferredEffort"], "xhigh")
        self.assertNotIn("preferredOnly", result["lanes"]["review-b"])
        self.assertEqual(result["reservedModels"], ["claude-haiku"])


class AgentfleetProfileRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.TemporaryDirectory(prefix="agentfleet lane choices ")
        self.addCleanup(self.raw.cleanup)
        self.home = Path(self.raw.name) / "home"
        self.config = Path(self.raw.name) / "config"
        self.home.mkdir()
        self.config.mkdir()
        self.paths = agentfleet.Paths(argparse.Namespace(home=str(self.home), config_home=str(self.config), prefix=None))
        self.models = ["claude-opus-5", "claude-haiku"]
        settings = {"model": "claude-opus-5", "modelPicker": {"options": [{"model": m, "label": m, "description": ""} for m in self.models]}}
        self.write(self.paths.settings, settings)
        self.fleet = {
            "lanes": {
                "implement-a": {"model": "claude-opus-5", "preferred": ["claude-opus-5"],
                                "preferredOnly": True, "preferredEffort": "high", "implementer": "claude"},
                "review-b": {"model": "claude-haiku", "implementer": "claude"},
            },
            "modelTiers": {},
            "reservedModels": ["claude-haiku"],
        }
        self.write(self.paths.fleet, self.fleet)

    @staticmethod
    def write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")

    @staticmethod
    def read(path: Path) -> dict:
        return json.loads(path.read_text())

    def test_save_then_use_keeps_reserved_models_and_lane_fields(self):
        settings = self.read(self.paths.settings)
        profile, token = agentfleet.snapshot(self.paths, settings, self.fleet, "work")
        self.assertEqual(profile["reservedModels"], ["claude-haiku"])
        self.assertEqual(profile["lanes"]["implement-a"]["preferredOnly"], True)
        self.assertEqual(profile["lanes"]["implement-a"]["preferredEffort"], "high")
        self.assertNotIn("preferredOnly", profile["lanes"]["review-b"])
        agentfleet.save_profile(self.paths, profile, token)

        # Another profile wipes the choices, then "work" is switched back to.
        cleared = copy.deepcopy(self.fleet)
        cleared["reservedModels"] = []
        cleared["lanes"]["implement-a"] = {"model": "claude-haiku", "implementer": "claude"}
        self.write(self.paths.fleet, cleared)

        loaded, _ = agentfleet.load_profile(self.paths, "work")
        agentfleet.apply_profile(self.paths, loaded, None)
        fleet = self.read(self.paths.fleet)
        self.assertEqual(fleet["reservedModels"], ["claude-haiku"])
        lane = fleet["lanes"]["implement-a"]
        self.assertIs(lane["preferredOnly"], True)
        self.assertEqual(lane["preferredEffort"], "high")
        self.assertEqual(lane["preferred"], ["claude-opus-5"])
        self.assertNotIn("preferredOnly", fleet["lanes"]["review-b"])

    def test_invalid_saved_values_are_not_restored(self):
        profile = {
            "format": agentfleet.PROFILE_FORMAT, "name": "bad", "kind": "native",
            "settings": {}, "env": {}, "modelTiers": {}, "excludedModels": [],
            "reservedModels": ["claude-haiku", 7],
            "lanes": {"implement-a": {"model": "claude-opus-5", "preferredOnly": "yes", "preferredEffort": "ultra"}},
        }
        agentfleet.apply_profile(self.paths, profile, None)
        lane = self.read(self.paths.fleet)["lanes"]["implement-a"]
        self.assertNotIn("preferredOnly", lane)
        self.assertNotIn("preferredEffort", lane)
        self.assertEqual(self.read(self.paths.fleet)["reservedModels"], ["claude-haiku"])

    def test_profile_saved_before_reservations_clears_them(self):
        """A 2.7 profile has no reservedModels; switching to it must not keep the current provider's."""
        profile = {
            "format": agentfleet.PROFILE_FORMAT, "name": "old", "kind": "native",
            "settings": {}, "env": {}, "modelTiers": {}, "excludedModels": [],
            "lanes": {"implement-a": {"model": "claude-opus-5"}},
        }
        agentfleet.apply_profile(self.paths, profile, None)
        self.assertNotIn("reservedModels", self.read(self.paths.fleet))


if __name__ == "__main__":
    unittest.main()

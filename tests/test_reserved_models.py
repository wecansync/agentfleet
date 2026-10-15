#!/usr/bin/env python3
"""Focused tests for reservedModels, preferredOnly, and bare preferred names."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS))
import provider_catalog


def import_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


OPUS = "claude-opus-5.5[1m]"
SOL = "codex-sol[1m]"
ASTRA = "codex-astra[1m]"
LUNA = "codex-luna[1m]"
NOVA = "codex-nova[1m]"


def rows(*ids: str) -> list[dict]:
    return [{"id": model.split("[")[0], "context_length": 1_000_000} for model in ids]


def lane(**extra) -> dict:
    return {"implementer": "claude", "tier": "deep", **extra}


class ReservedModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reconcile = import_script("reserved_fleet_reconcile", SCRIPTS / "fleet-reconcile.py")
        cls.policy = provider_catalog.generic_policy()

    def resolve(self, fleet: dict, *ids: str) -> dict:
        resolved, _, _, _ = self.reconcile.resolve_fleet(copy.deepcopy(fleet), {}, rows(*ids), self.policy)
        return resolved

    def used(self, config: dict) -> list[str]:
        return [config["model"], *config.get("fallbacks", [])]

    def deep_fleet(self, **top) -> dict:
        fleet = {
            "version": "delegate-fleet.v1",
            "lanes": {"plan": lane(), "review": lane(), "implement-deep": lane()},
        }
        fleet.update(top)
        return fleet

    def test_reserved_model_never_ranked_into_deep_lanes(self):
        resolved = self.resolve(self.deep_fleet(reservedModels=["codex-astra"]), OPUS, SOL, ASTRA)
        for name, config in resolved["lanes"].items():
            self.assertNotIn(ASTRA, self.used(config), name)
            self.assertTrue(config["model"] in (OPUS, SOL), name)

    def test_unreserved_control_can_rank_the_model(self):
        resolved = self.resolve(self.deep_fleet(), OPUS, SOL, ASTRA)
        self.assertTrue(any(ASTRA in self.used(config) for config in resolved["lanes"].values()))

    def test_reserved_model_leaves_a_stale_own_chain(self):
        fleet = self.deep_fleet(reservedModels=["codex-astra"])
        fleet["lanes"]["plan"].update(model="gone-model[1m]", fallbacks=[ASTRA, SOL])
        resolved = self.resolve(fleet, OPUS, SOL, ASTRA)
        self.assertEqual(resolved["lanes"]["plan"]["model"], SOL)
        self.assertNotIn(ASTRA, resolved["lanes"]["plan"]["fallbacks"])

    def test_reserved_current_model_is_replaced(self):
        fleet = self.deep_fleet(reservedModels=["codex-astra"])
        fleet["lanes"]["plan"].update(model=ASTRA)
        resolved = self.resolve(fleet, OPUS, SOL, ASTRA)
        self.assertNotIn(ASTRA, self.used(resolved["lanes"]["plan"]))

    def test_preferred_lane_may_use_reserved_model(self):
        fleet = self.deep_fleet(reservedModels=["codex-astra"])
        fleet["lanes"]["review"].update(preferred=["codex-astra", "codex-sol"])
        resolved = self.resolve(fleet, OPUS, SOL, ASTRA)
        review = resolved["lanes"]["review"]
        self.assertEqual(review["model"], ASTRA)
        self.assertEqual(review["fallbacks"][0], SOL)
        for other in ("plan", "implement-deep"):
            self.assertNotIn(ASTRA, self.used(resolved["lanes"][other]))

    def test_preferred_reserved_model_works_as_fallback(self):
        fleet = self.deep_fleet(reservedModels=["codex-astra"])
        fleet["lanes"]["review"].update(preferred=["codex-sol", "codex-astra"])
        review = self.resolve(fleet, OPUS, SOL, ASTRA)["lanes"]["review"]
        self.assertEqual(review["model"], SOL)
        self.assertEqual(review["fallbacks"][0], ASTRA)

    def test_preferred_only_gives_exactly_the_live_preferred_fallbacks(self):
        fleet = self.deep_fleet()
        fleet["lanes"]["review"].update(
            preferred=["codex-sol", "codex-vanished", "codex-astra"], preferredOnly=True,
        )
        review = self.resolve(fleet, OPUS, SOL, ASTRA)["lanes"]["review"]
        self.assertEqual(review["model"], SOL)
        self.assertEqual(review["fallbacks"], [ASTRA])

    def test_preferred_only_fallbacks_are_capped(self):
        names = [f"codex-m{i}" for i in range(12)]
        fleet = self.deep_fleet()
        fleet["lanes"]["review"].update(preferred=names, preferredOnly=True)
        review = self.resolve(fleet, *[f"{n}[1m]" for n in names], OPUS)["lanes"]["review"]
        self.assertEqual(len(review["fallbacks"]), provider_catalog.MAX_FALLBACK_CANDIDATES - 1)
        self.assertEqual(review["fallbacks"], [f"{n}[1m]" for n in names[1:provider_catalog.MAX_FALLBACK_CANDIDATES]])

    def test_without_preferred_only_ranked_filler_still_follows(self):
        fleet = self.deep_fleet()
        fleet["lanes"]["review"].update(preferred=["codex-sol"])
        review = self.resolve(fleet, OPUS, SOL, ASTRA)["lanes"]["review"]
        self.assertEqual(review["model"], SOL)
        self.assertEqual(set(review["fallbacks"]), {OPUS, ASTRA})

    def test_preferred_only_without_live_entry_ranks_normally_minus_reserved(self):
        fleet = self.deep_fleet(reservedModels=["codex-astra"])
        fleet["lanes"]["review"].update(preferred=["codex-vanished"], preferredOnly=True)
        review = self.resolve(fleet, OPUS, SOL, ASTRA)["lanes"]["review"]
        self.assertIn(review["model"], (OPUS, SOL))
        self.assertNotIn(ASTRA, self.used(review))
        self.assertEqual(set(self.used(review)), {OPUS, SOL})

    def test_bare_preferred_names_resolve_to_catalog_ids(self):
        fleet = self.deep_fleet()
        fleet["lanes"]["review"].update(preferred=["codex-luna", "codex-sol"], preferredOnly=True)
        resolved = self.resolve(fleet, OPUS, SOL, LUNA)
        review = resolved["lanes"]["review"]
        self.assertEqual(review["model"], LUNA)
        self.assertEqual(review["fallbacks"], [SOL])
        self.assertEqual(review["preferred"], ["codex-luna", "codex-sol"], "the stored list is left as written")

    def test_exact_preferred_match_wins_over_suffix_match(self):
        fleet = self.deep_fleet()
        fleet["lanes"]["review"].update(preferred=["codex-luna"], preferredOnly=True)
        resolved = self.resolve(fleet, "codex-luna", NOVA)
        catalog = {"codex-luna": {}, "codex-luna[1m]": {}}
        self.assertEqual(self.reconcile.resolve_catalog_name("codex-luna", catalog), "codex-luna")
        self.assertEqual(self.reconcile.resolve_catalog_name("codex-luna[1m]", catalog), "codex-luna[1m]")
        self.assertEqual(resolved["lanes"]["review"]["model"], "codex-luna[1m]")

    def test_excluded_beats_reserved_even_when_preferred(self):
        fleet = self.deep_fleet(reservedModels=["codex-astra"], excludedModels=["codex-astra"])
        fleet["lanes"]["review"].update(preferred=["codex-astra", "codex-sol"])
        resolved = self.resolve(fleet, OPUS, SOL, ASTRA)
        for name, config in resolved["lanes"].items():
            self.assertNotIn(ASTRA, self.used(config), name)

    def test_alt_lane_still_avoids_its_sibling(self):
        fleet = self.deep_fleet(reservedModels=["codex-astra"])
        fleet["lanes"]["plan-alt"] = lane(altOf="plan")
        resolved = self.resolve(fleet, OPUS, SOL, ASTRA)
        self.assertNotEqual(resolved["lanes"]["plan-alt"]["model"], resolved["lanes"]["plan"]["model"])
        self.assertNotIn(ASTRA, self.used(resolved["lanes"]["plan-alt"]))

    def test_second_reconcile_pass_changes_nothing(self):
        fleet = self.deep_fleet(reservedModels=["codex-astra"])
        fleet["lanes"]["review"].update(preferred=["codex-astra", "codex-luna"])
        fleet["lanes"]["implement-deep"].update(preferred=["codex-sol", "codex-luna"], preferredOnly=True)
        fleet["lanes"]["plan-alt"] = lane(altOf="plan")
        ids = (OPUS, SOL, ASTRA, LUNA)
        first = self.resolve(fleet, *ids)
        second = self.resolve(first, *ids)
        first.pop("_reconcile"), second.pop("_reconcile")
        self.assertEqual(first, second)

    def test_reserved_models_helper_mirrors_excluded_models(self):
        fleet = {"reservedModels": ["codex-astra[1m]", " codex-sol ", 3, ""]}
        self.assertEqual(provider_catalog.reserved_models(fleet), {"codex-astra", "codex-sol"})
        self.assertEqual(provider_catalog.reserved_models({}), set())
        self.assertEqual(provider_catalog.reserved_models(None), set())


if __name__ == "__main__":
    unittest.main()

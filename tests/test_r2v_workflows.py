"""Contract tests for the standalone, local direct-panel R2V workflows."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "config" / "workflows"
VARIANTS = ("turbo4", "baseline20")


def read(variant, kind):
    return json.loads((WORKFLOWS / f"minimax_h3_panel_r2v_{variant}.{kind}.json").read_text())


class TestPanelR2VWorkflows(unittest.TestCase):
    def test_reference_has_pixel_and_text_conditioning(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                graph = read(variant, "api")
                ref = graph["136"]
                self.assertEqual(ref["class_type"], "MiniMaxH3ReferenceToVideo")
                self.assertEqual(ref["inputs"]["vae"], ["119", 0])
                self.assertEqual(ref["inputs"]["clip"], ["128", 0])
                self.assertEqual(ref["inputs"]["ref_images.ref_image_0"], ["137", 0])
                self.assertEqual(graph["137"]["class_type"], "LoadImage")
                self.assertIn("<Picture 1>", ref["inputs"]["prompt"])
                self.assertEqual(ref["inputs"]["ref_image_size"], "match")
                self.assertNotIn("first_frame", ref["inputs"])
                self.assertFalse(any("ImageToVideo" in n["class_type"] for n in graph.values()))
                self.assertFalse(any("AddGuide" in n["class_type"] for n in graph.values()))

    def test_checkpoint_and_sampler_match_variant(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                graph = read(variant, "api")
                self.assertEqual(graph["127"]["inputs"]["unet_name"],
                                 "minimax_h3_ref2va_pruned_int8_convrot.safetensors")
                model = ["145", 0] if variant == "turbo4" else ["127", 0]
                self.assertEqual(graph["124"]["inputs"]["model"], model)
                self.assertEqual(graph["126"]["inputs"]["model"], model)
                self.assertEqual(graph["124"]["inputs"]["steps"],
                                 4 if variant == "turbo4" else 20)
                self.assertEqual(graph["124"]["inputs"]["scheduler"], "simple")
                self.assertEqual(graph["123"]["inputs"]["sampler_name"], "res_multistep")
                if variant == "turbo4":
                    lora = graph["145"]["inputs"]
                    self.assertEqual(lora["model"], ["127", 0])
                    self.assertEqual(lora["strength_model"], 1.0)
                    self.assertEqual(lora["lora_name"],
                                     "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors")
                else:
                    self.assertFalse(any("Lora" in n["class_type"] for n in graph.values()))

    def test_valid_canvas_duration_and_fixed_seed(self):
        for variant in VARIANTS:
            graph = read(variant, "api")
            inputs = graph["136"]["inputs"]
            self.assertEqual(inputs["length"], 124)
            self.assertEqual(inputs["length"] % 17, 5)
            self.assertEqual(inputs["width"] % 32, 0)
            self.assertEqual(inputs["height"] % 32, 0)
            self.assertEqual(graph["130"]["inputs"]["fps"], 24)
            self.assertEqual(graph["129"]["inputs"]["noise_seed"], 42)
            ui = read(variant, "ui")
            noise = next(n for n in ui["nodes"] if n["id"] == 129)
            self.assertEqual(noise["widgets_values"], [42, "fixed"])

    def test_latent_decode_and_no_generated_audio_in_delivery(self):
        for variant in VARIANTS:
            graph = read(variant, "api")
            self.assertEqual(graph["125"]["inputs"]["latent_image"], ["136", 1])
            self.assertEqual(graph["126"]["inputs"]["conditioning"], ["136", 0])
            self.assertEqual(graph["122"]["inputs"]["samples"], ["125", 0])
            self.assertEqual(graph["122"]["inputs"]["vae"], ["119", 0])
            self.assertEqual(graph["130"]["inputs"]["images"], ["122", 0])
            self.assertNotIn("audio", graph["130"]["inputs"])
            self.assertFalse(any(n["class_type"] == "VAEDecodeAudio" for n in graph.values()))

    def test_gui_and_api_have_identical_effective_inputs(self):
        for variant in VARIANTS:
            graph = read(variant, "api")
            ui = read(variant, "ui")
            nodes = {n["id"]: n for n in ui["nodes"] if n["type"] != "MarkdownNote"}
            links = {edge[0]: edge for edge in ui["links"]}
            self.assertEqual(set(nodes), {int(key) for key in graph})
            used = set()
            for key, api_node in graph.items():
                node = nodes[int(key)]
                self.assertEqual(node["type"], api_node["class_type"])
                sockets = {i["name"]: i for i in node.get("inputs", [])}
                for name, value in api_node["inputs"].items():
                    if isinstance(value, list):
                        link_id = sockets[name]["link"]
                        edge = links[link_id]
                        self.assertEqual([str(edge[1]), edge[2]], value)
                        self.assertEqual(edge[3], int(key))
                        self.assertEqual(node["inputs"][edge[4]]["name"], name)
                        source = nodes[edge[1]]["outputs"][edge[2]]
                        self.assertEqual(source["type"], sockets[name]["type"])
                        self.assertIn(link_id, source["links"])
                        used.add(link_id)
                    else:
                        self.assertEqual(node["widgets_values_named"][name], value)
                        self.assertIn(value, node["widgets_values"])
            self.assertEqual(used, set(links))

    def test_graph_is_acyclic_and_every_node_feeds_saved_video(self):
        for variant in VARIANTS:
            graph = read(variant, "api")
            visiting, visited = set(), set()

            def visit(key):
                self.assertNotIn(key, visiting)
                if key in visited:
                    return
                visiting.add(key)
                for value in graph[key]["inputs"].values():
                    if isinstance(value, list):
                        self.assertIn(value[0], graph)
                        visit(value[0])
                visiting.remove(key)
                visited.add(key)

            visit("92")
            self.assertEqual(visited, set(graph))

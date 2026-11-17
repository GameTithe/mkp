import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import app


ROOT = Path(__file__).resolve().parents[1]


class FrameScopeTest(unittest.TestCase):
    def test_sample_counts_priority_and_filter(self):
        result = app.analyze(app.load_input(ROOT / "data" / "frames.json"))

        self.assertEqual(
            result["meta"],
            {
                "total_rows": 74,
                "accepted_frames": 54,
                "ignored_warmup": 18,
                "invalid_rows": 1,
                "duplicate_rows": 1,
            },
        )
        self.assertEqual([item["scenario"] for item in result["items"]], ["전투", "로비", "마을"])
        self.assertEqual([item["status"] for item in result["items"]], ["regressed", "stable", "stable"])
        self.assertEqual(result["items"][0]["bottleneck"], "gpu")
        self.assertEqual(app.analyze(app.load_input(ROOT / "data" / "frames.json"), "전투", "regressed")["items"], [result["items"][0]])

    def test_exact_regression_boundary_and_insufficient(self):
        frames = []
        for build, duration in (("baseline", 20), ("candidate", 22)):
            for frame in range(8):
                frames.append(
                    {"build": build, "scenario": "경계", "frame": frame, "cpu_ms": duration, "gpu_ms": duration}
                )
        result = app.analyze({"schema_version": 1, "frames": frames})
        self.assertEqual(result["items"][0]["status"], "regressed")
        self.assertAlmostEqual(result["items"][0]["delta_percent"], 10.0)

        result = app.analyze({"schema_version": 1, "frames": frames[:7]})
        self.assertEqual(result["items"][0]["status"], "insufficient")
        self.assertEqual(result["items"][0]["bottleneck"], "unknown")

    def test_bom_duplicate_and_invalid_row(self):
        document = {
            "schema_version": 1,
            "frames": [
                {"build": "baseline", "scenario": "s", "frame": 3, "cpu_ms": 1, "gpu_ms": 2},
                {"build": "baseline", "scenario": "s", "frame": 3, "cpu_ms": 9, "gpu_ms": 9},
                {"build": "candidate", "scenario": "s", "frame": 3, "cpu_ms": "bad", "gpu_ms": 2},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8-sig")
            result = app.analyze(app.load_input(path))

        self.assertEqual(result["meta"]["accepted_frames"], 1)
        self.assertEqual(result["meta"]["duplicate_rows"], 1)
        self.assertEqual(result["meta"]["invalid_rows"], 1)
        self.assertEqual([warning["code"] for warning in result["warnings"]], ["DUPLICATE_FRAME", "INVALID_ROW"])

    def test_cli_errors_are_json_without_traceback(self):
        process = subprocess.run(
            [sys.executable, str(ROOT / "app.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["error"]["code"], "INVALID_ARGS")
        self.assertEqual(process.stderr, "")
        self.assertNotIn(str(ROOT), process.stdout)

    def test_malformed_and_missing_inputs_fail_safely(self):
        self.assertEqual(
            app.invalid_fields(
                {"build": "baseline", "scenario": "s", "frame": 3, "cpu_ms": 10**400, "gpu_ms": 1}
            ),
            ["cpu_ms"],
        )
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            paths_and_codes = ((malformed, "INVALID_INPUT"), (Path(directory) / "missing.json", "IO_ERROR"))
            for path, code in paths_and_codes:
                with self.subTest(code=code):
                    process = subprocess.run(
                        [sys.executable, str(ROOT / "app.py"), "--input", str(path), "--json"],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        check=False,
                    )
                    self.assertEqual(process.returncode, 2)
                    self.assertEqual(json.loads(process.stdout)["error"]["code"], code)
                    self.assertEqual(process.stderr, "")
                    self.assertNotIn(str(path), process.stdout)


if __name__ == "__main__":
    unittest.main()

"""Offline visual contracts; no devices, credentials, or Git fixture commits."""
import copy
import hashlib
import importlib.util
import json
import shutil
import struct
import subprocess
import sys
import types
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="optional FFmpeg unavailable; actual conversion is unverified on this platform")


def visual():
    path = ROOT / "tools/visual_evidence.py"
    assert path.exists(), "visual evidence helper has not been implemented"
    spec = importlib.util.spec_from_file_location("visual_evidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def png(rgb=(255, 0, 0), width=4, height=2):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress((b"\0" + bytes(rgb) * width) * height)) +
            chunk(b"IEND", b""))


def contract(count=5):
    return {"required": True, "min_frames": count,
            "sample_fractions": [i / (count - 1) for i in range(count)],
            "tolerance_fraction": 0.05, "require_display": False}


def sample():
    mode = {"width": 4, "height": 2, "fourcc": "PNG"}
    scope = {"mode": mode, "layer": "consumption", "consumer": "sdk",
             "scenario": "steady"}
    bundle = {"run_id": "run-1", "candidate_id": "a" * 64,
              "contract_sha256": "sha256:" + "c" * 64,
              "window": {"monotonic_start": 10.0, "monotonic_end": 30.0}}
    group = {"id": "set-1", "requirement_id": "display", **scope,
             "display": False, "window": copy.deepcopy(bundle["window"]), "frames": []}
    items, files, results = {}, {}, []
    for i, when in enumerate([10.0, 15.0, 20.0, 25.0, 30.0]):
        item_id, frame_id = "raw-" + str(i), "frame-" + str(i)
        data = png()  # A static scene at distinct real sample positions is legitimate.
        metadata = {**copy.deepcopy(scope), "sequence": i, "monotonic": when,
                    "timestamp": when, "clock_domain": "host_monotonic",
                    "layout": copy.deepcopy(mode)}
        items[item_id] = {"id": item_id, "kind": "frame_set", "requirement_id": "display",
                          "path": item_id + ".png", "sha256": digest(data), "bytes": len(data),
                          "source_ids": [], "complete": True, "metadata": metadata}
        files[item_id] = data
        group["frames"].append({"id": frame_id, "source_item_id": item_id,
                                "preview_item_id": item_id, "sequence": i,
                                "monotonic": when, "timestamp": when,
                                "clock_domain": "host_monotonic",
                                "review_refs": [{"review_id": "review-1"}]})
        results.append({"frame_id": frame_id, "frame_set_id": "set-1",
                        "input_sha256": digest(data), "source_sha256": digest(data),
                        "channel": "native", "provider": "trusted-host", "model": "fixture-model",
                        "prompt_version": "basic-display-v1", "conclusion": "Visible red test field.",
                        "outcome": "pass", "limitations": ["synthetic fixture"]})
    bundle["visual"] = {"schema_version": 1, "frame_sets": [group]}
    bundle["items"] = list(items.values())
    receipt = {"review_id": "review-1", "run_id": bundle["run_id"],
               "candidate_id": bundle["candidate_id"], "outcome": "pass",
               "contract_digest": bundle["contract_sha256"],
               "cell": {key: scope[key] for key in ("layer", "consumer", "scenario")},
               "channels": ["native"], "frame_results": results}
    return bundle, items, files, receipt


def check(module, parts, config=None, trusted=True):
    bundle, items, files, receipt = parts
    return module.validate_visual(bundle, config or contract(), items=items,
                                  read_item=files.__getitem__, run=bundle,
                                  review_validator=(lambda ref: receipt) if trusted else None)


def test_v01_missing_visual_single_frame_and_untrusted_claim_cannot_pass():
    module = visual()
    parts = sample()
    with pytest.raises(ValueError, match="trusted|review"):
        check(module, parts, trusted=False)
    parts[0]["visual"]["frame_sets"][0]["frames"] = parts[0]["visual"]["frame_sets"][0]["frames"][:1]
    with pytest.raises(ValueError, match="frame|sample"):
        check(module, parts)
    parts[0]["visual"] = {"viewed": True}
    with pytest.raises(ValueError):
        check(module, parts)


def test_v02_complete_static_samples_and_trusted_receipt_pass():
    report = check(visual(), sample())
    assert report["outcome"] == "pass"
    assert report["frame_count"] == 5
    assert report["claim_ceiling"] == "basic_visual_display"
    assert any("IQ" in item for item in report["limitations"])


@pytest.mark.parametrize("field,value", [("width", 1920), ("height", 1080), ("fourcc", "YUYV"),
                                        ("consumer", "application"), ("scenario", "another-profile")])
def test_frozen_requirement_rejects_coherently_relabelled_visual_set(field, value):
    bundle, items, files, receipt = sample()
    requirement = {"id": "display", "layer": "consumption", "consumer": "sdk", "scenario": "steady",
                   "expected_mode": {"width": 4, "height": 2, "fourcc": "PNG"}}
    if field in requirement["expected_mode"]:
        requirement["expected_mode"][field] = value
    else:
        requirement[field] = value
    with pytest.raises(ValueError, match="frozen|requirement|mode|scope"):
        visual().validate_visual(bundle, contract(), items=items, read_item=files.__getitem__, run=bundle,
                                 review_validator=lambda ref: receipt, requirements={"display": requirement})


def test_frozen_visual_mode_is_explicit_and_not_inferred_from_scenario_text():
    bundle, items, files, receipt = sample()
    requirement = {"id": "display", "layer": "consumption", "consumer": "sdk", "scenario": "steady",
                   "expected_mode": {"width": 4, "height": 2, "fourcc": "PNG"}}
    result = visual().validate_visual(bundle, contract(), items=items, read_item=files.__getitem__, run=bundle,
                                    review_validator=lambda ref: receipt, requirements={"display": requirement})
    assert result["frame_count"] == 5


@pytest.mark.parametrize("field,value", [("contract_digest", "sha256:" + "b" * 64),
                                        ("cell", {"layer": "other"})])
def test_strict_visual_receipt_cannot_be_borrowed_from_another_contract_or_cell(field, value):
    bundle, items, files, receipt = sample()
    receipt[field] = value
    requirement = {"id": "display", "layer": "consumption", "consumer": "sdk", "scenario": "steady"}
    with pytest.raises(ValueError, match="contract|cell|scope"):
        visual().validate_visual(bundle, contract(), items=items, read_item=files.__getitem__, run=bundle,
                                 review_validator=lambda ref: receipt, requirements={"display": requirement})


@pytest.mark.parametrize("mutation", ["consecutive", "duplicate_source", "duplicate_time", "duplicate_sequence", "short_window"])
def test_v03_rejects_false_window_coverage(mutation):
    parts = sample()
    group = parts[0]["visual"]["frame_sets"][0]
    if mutation == "consecutive":
        for i, frame in enumerate(group["frames"]):
            frame["monotonic"] = 10.0 + i * 0.01
            parts[1][frame["source_item_id"]]["metadata"]["monotonic"] = frame["monotonic"]
    elif mutation == "duplicate_source":
        group["frames"][1]["source_item_id"] = "raw-0"
    elif mutation == "duplicate_time":
        group["frames"][1]["monotonic"] = 10.0
    elif mutation == "duplicate_sequence":
        group["frames"][1]["sequence"] = 0
    else:
        group["window"]["monotonic_end"] = 10.1
    with pytest.raises(ValueError):
        check(visual(), parts)


def test_contract_drives_two_frames_and_matching_is_injective():
    parts = sample()
    group = parts[0]["visual"]["frame_sets"][0]
    group["frames"] = [group["frames"][0], group["frames"][-1]]
    for key in list(parts[1]):
        if key not in {"raw-0", "raw-4"}:
            del parts[1][key]
    assert check(visual(), parts, contract(2))["frame_count"] == 2
    config = contract(2)
    config["sample_fractions"] = [0, 0.01, 1]
    with pytest.raises(ValueError, match="sample|fraction"):
        check(visual(), parts, config)


@pytest.mark.parametrize("mutation", ["wrong_input_hash", "wrong_source_hash", "wrong_run", "wrong_candidate", "missing_frame", "no_conclusion", "failed", "unknown_model", "disagreement"])
def test_v07_forged_or_incomplete_receipts_are_rejected(mutation):
    parts = sample()
    receipt = parts[3]
    if mutation == "wrong_input_hash":
        receipt["frame_results"][0]["input_sha256"] = "sha256:" + "b" * 64
    elif mutation == "wrong_source_hash":
        receipt["frame_results"][0]["source_sha256"] = "sha256:" + "b" * 64
    elif mutation == "wrong_run":
        receipt["run_id"] = "different-run"
    elif mutation == "wrong_candidate":
        receipt["candidate_id"] = "b" * 64
    elif mutation == "missing_frame":
        receipt["frame_results"].pop()
    elif mutation == "no_conclusion":
        receipt["frame_results"][0]["conclusion"] = ""
    elif mutation == "failed":
        receipt["frame_results"][0]["outcome"] = "fail"
    elif mutation == "unknown_model":
        receipt["frame_results"][0]["model"] = "unknown"
    else:
        receipt["disagreement"] = "unresolved"
    with pytest.raises(ValueError):
        check(visual(), parts)


@pytest.mark.parametrize("mutation", ["consumer", "mode", "orphan", "corrupt", "hash", "source_time", "display"])
def test_v08_rejects_substitution_or_unassociated_evidence(mutation):
    parts = sample()
    config = contract()
    if mutation == "consumer":
        parts[1]["raw-0"]["metadata"]["consumer"] = "driver"
    elif mutation == "mode":
        parts[1]["raw-0"]["metadata"]["mode"]["width"] = 8
    elif mutation == "orphan":
        extra = copy.deepcopy(parts[1]["raw-0"])
        extra["id"] = "unused"
        parts[1]["unused"] = extra
    elif mutation == "corrupt":
        parts[2]["raw-0"] = b"not an image"
        parts[1]["raw-0"]["sha256"] = digest(parts[2]["raw-0"])
    elif mutation == "hash":
        parts[2]["raw-0"] = png((0, 0, 255))
    elif mutation == "source_time":
        parts[1]["raw-0"]["metadata"]["monotonic"] = 12.0
    else:
        config["require_display"] = True
    with pytest.raises(ValueError):
        check(visual(), parts, config)


def test_v09_static_pixels_do_not_prove_dynamic_or_iq():
    parts = sample()
    group = parts[0]["visual"]["frame_sets"][0]
    group["claims"] = ["not_frozen"]
    with pytest.raises(ValueError, match="controlled|dynamic"):
        check(visual(), parts)
    group["claims"] = ["color_accuracy"]
    with pytest.raises(ValueError, match="claim|IQ"):
        check(visual(), parts)


def route(mode="native", limit=4):
    import media_router
    return media_router.route_media(mode, "supported", "available", "general", "normal",
                                    media_router._safe_profile({"provider": "fixture",
                                    "model": "fixture", "declared_capabilities": ["visual_reasoning"],
                                    "max_images_per_message": limit}), limit)


def execution_frames(tmp_path):
    frames = []
    for i in range(5):
        path = tmp_path / (str(i) + ".png")
        path.write_bytes(png())
        frames.append({"frame_id": "frame-" + str(i), "frame_set_id": "set-1",
                       "preview_path": str(path), "input_sha256": digest(png()),
                       "source_sha256": digest(png())})
    return frames


def adapter(request):
    return {"status": "success", "frame_results": [
        {"frame_id": frame["frame_id"], "frame_set_id": frame["frame_set_id"],
         "input_sha256": frame["input_sha256"], "source_sha256": frame["source_sha256"],
         "channel": request["channel"], "provider": "fixture", "model": "fixture-model",
         "prompt_version": request["prompt_version"], "conclusion": "Visible synthetic red field.",
         "outcome": "pass", "limitations": ["fixture"]} for frame in request["frames"]]}


def execute(module, tmp_path, callback=adapter, selected=None):
    return module.execute_visual_review(execution_frames(tmp_path), selected or route(),
                                        run={"run_id": "run-1", "candidate_id": "a" * 64},
                                        prompt_version="basic-display-v1",
                                        adapters={"native": callback, "bridge": callback})


def test_v06_real_adapter_execution_is_serial_and_respects_four_image_limit(tmp_path):
    seen = []
    def record(request):
        seen.append((request["channel"], len(request["frames"])))
        return adapter(request)
    report = execute(visual(), tmp_path, record)
    assert seen == [("native", 4), ("native", 1)]
    assert report["outcome"] == "pass"
    assert len(report["frame_results"]) == 5
    assert len(report["batches"]) == 2


@pytest.mark.parametrize("failure", ["exception", "missing", "hash", "text_only", "no_adapter", "dual_disagreement", "explicit_fail"])
def test_v05_v06_v07_failures_keep_results_without_passing(tmp_path, failure):
    def broken(request):
        result = adapter(request)
        if failure == "exception" and request["batch_index"] == 1:
            raise RuntimeError("fixture error containing secret=do-not-echo")
        if failure == "missing" and request["batch_index"] == 1:
            result["frame_results"] = []
        if failure == "hash":
            result["frame_results"][0]["input_sha256"] = "sha256:" + "b" * 64
        if failure == "dual_disagreement" and request["channel"] == "bridge":
            result["frame_results"][0]["outcome"] = "fail"
            result["frame_results"][0]["conclusion"] = "Black test field is not the expected image."
        if failure == "explicit_fail":
            result["frame_results"][0]["outcome"] = "fail"
            result["frame_results"][0]["conclusion"] = "Visible tearing in frame."
        return result
    module = visual()
    selected = route("dual") if failure == "dual_disagreement" else route()
    if failure == "text_only":
        selected = route("text_only")
    if failure == "no_adapter":
        report = module.execute_visual_review(execution_frames(tmp_path), selected,
                    run={"run_id": "run-1", "candidate_id": "a" * 64},
                    prompt_version="basic-display-v1", adapters={})
    else:
        report = execute(module, tmp_path, broken, selected)
    assert report["outcome"] != "pass"
    assert "do-not-echo" not in json.dumps(report)
    if failure == "explicit_fail":
        assert report["outcome"] == "fail"
    if failure == "exception":
        assert len(report["frame_results"]) == 4
        assert len(report["batches"]) == 2


@pytest.mark.parametrize("later_failure", ["next_batch", "same_batch"])
def test_observed_failure_is_not_downgraded_when_other_frames_are_unavailable(tmp_path, later_failure):
    def inspect(request):
        if later_failure == "next_batch" and request["batch_index"] == 1:
            raise RuntimeError("provider unavailable")
        result = adapter(request)
        if request["batch_index"] == 0:
            result["frame_results"][0].update(outcome="fail", conclusion="Visible corrupt frame.")
            if later_failure == "same_batch":
                result["frame_results"][1]["input_sha256"] = "sha256:" + "b" * 64
        return result
    report = execute(visual(), tmp_path, inspect)
    assert report["outcome"] == "fail"
    assert any(row["outcome"] == "fail" for row in report["frame_results"])
    assert any(batch["status"] != "success" for batch in report["batches"])


def raw_bars(fourcc):
    # Limited-range BT.601: red pair, then blue pair, with offset and row padding.
    if fourcc == "UYVY":
        active = bytes([90, 81, 240, 81, 240, 41, 110, 41])
    else:
        active = bytes([81, 90, 81, 240, 41, 240, 41, 110])
    data = b"HEAD" + active + b"PAD!" + active
    layout = {"width": 4, "height": 2, "fourcc": fourcc,
              "planes": [{"stride": 12, "offset": 4, "bytesused": 20}],
              "colors": {"matrix": "bt601", "range": "limited"}}
    return data, layout


@requires_ffmpeg
@pytest.mark.parametrize("fourcc", ["UYVY", "YUYV"])
def test_v04_real_ffmpeg_decodes_noncompact_color_bars(tmp_path, fourcc):
    assert shutil.which("ffmpeg"), "required FFmpeg offline test dependency unavailable"
    module = visual()
    data, layout = raw_bars(fourcc)
    source, output = tmp_path / "frame.raw", tmp_path / "preview.png"
    source.write_bytes(data)
    report = module.prepare_preview(source, layout, output)
    pixels = subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-i", str(output),
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                            capture_output=True, check=True, timeout=10).stdout
    assert len(pixels) == 24
    assert pixels[0] > 230 and pixels[1] < 20 and pixels[2] < 20
    assert pixels[6] < 20 and pixels[7] < 20 and pixels[8] > 230
    assert pixels[:12] == pixels[12:]
    assert source.read_bytes() == data
    assert report["source_sha256"] == digest(data)
    assert report["preview_sha256"] == digest(output.read_bytes())
    assert report["converter"]["version"]
    assert ("uyvy422" if fourcc == "UYVY" else "yuyv422") in report["converter"]["argv"]


@pytest.mark.parametrize("mutation", ["short", "stride", "offset", "bytesused", "odd", "format", "planes", "colors"])
def test_v04_v08_invalid_raw_layout_never_writes_preview(tmp_path, mutation):
    data, layout = raw_bars("UYVY")
    if mutation == "short":
        data = data[:-1]
    elif mutation in {"stride", "offset", "bytesused"}:
        layout["planes"][0][mutation] = {"stride": 7, "offset": 50, "bytesused": 19}[mutation]
    elif mutation == "odd":
        layout["width"] = 3
    elif mutation == "format":
        layout["fourcc"] = "NV12"
    elif mutation == "planes":
        layout["planes"].append(dict(layout["planes"][0]))
    else:
        layout["colors"] = {}
    source, output = tmp_path / "frame.raw", tmp_path / "preview.png"
    source.write_bytes(data)
    with pytest.raises(ValueError):
        visual().prepare_preview(source, layout, output)
    assert not output.exists()


def test_preview_does_not_overwrite_existing_file_or_original(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(png())
    with pytest.raises(ValueError):
        visual().prepare_preview(source, {"width": 4, "height": 2, "fourcc": "PNG"}, source)
    assert source.read_bytes() == png()


def test_png_direct_preview_is_traceable_and_bounded(tmp_path):
    source, output = tmp_path / "source.png", tmp_path / "preview.png"
    source.write_bytes(png())
    report = visual().prepare_preview(source, {"width": 4, "height": 2, "fourcc": "PNG"}, output)
    assert report["source_sha256"] == report["preview_sha256"]
    assert output.read_bytes() == png()
    with pytest.raises(ValueError):
        visual().prepare_preview(source, {"width": 4, "height": 2, "fourcc": "PNG"},
                                 tmp_path / "too-big.png", max_bytes=1)


def test_initial_run_binding_allows_missing_run_id_but_not_another_candidate():
    module = visual()
    bundle, items, files, receipt = sample()
    report = module.validate_visual(bundle, contract(), items=items, read_item=files.__getitem__,
                                    run={"candidate_id": bundle["candidate_id"]},
                                    review_validator=lambda ref: receipt)
    assert report["outcome"] == "pass"


def bridge_script(tmp_path, *, content=None, qualified=True, wrong_hash=False):
    script = tmp_path / "bridge_fixture.py"
    body = json.dumps(content or {"outcome": "pass", "conclusion": "Visible synthetic field.",
                                  "limitations": ["offline provider fixture"]})
    script.write_text("import hashlib,json,sys\n"
        "path=sys.argv[sys.argv.index('--analyze-evidence')+1]\n"
        "profile=sys.argv[sys.argv.index('--prompt-profile')+1]\n"
        "data=open(path,'rb').read()\n"
        "print(json.dumps({'status':'success','channel':'bridge','provider':'fixture',"
        "'model':'fixture-model','input_sha256':" +
        ("'sha256:'+'b'*64" if wrong_hash else "'sha256:'+hashlib.sha256(data).hexdigest()") +
        ",'prompt_profile':profile,'timestamp':'2026-10-15T00:00:00Z',"
        "'provider_profile':{'quality_profile':{'evaluation_status':" + repr("verified" if qualified else "unverified") +
        "}},'result':'<think>untrusted provider analysis</think>\\n```json\\n'+" + repr(body) + "+'\\n```'}))\n",
        encoding="utf-8")
    return script


def test_bridge_cli_actually_executes_provider_and_preserves_raw_result(tmp_path):
    module = visual()
    callback = module.BridgeCLIAdapter(interpreter=sys.executable, server_path=bridge_script(tmp_path))
    report = execute(module, tmp_path, callback, route("bridge"))
    assert report["outcome"] == "pass"
    assert len(report["provider_results"]) == 5
    assert "<think>" in report["provider_results"][0]["result"]
    assert report["frame_results"][0]["provider_timestamp"] == "2026-10-15T00:00:00Z"


@pytest.mark.parametrize("failure", ["unverified", "wrong_hash", "ambiguous_json", "missing_outcome"])
def test_bridge_transport_success_is_not_visual_pass(tmp_path, failure):
    module = visual()
    content = {"conclusion": "Visible field.", "limitations": []} if failure == "missing_outcome" else None
    script = bridge_script(tmp_path, content=content, qualified=failure != "unverified",
                           wrong_hash=failure == "wrong_hash")
    if failure == "ambiguous_json":
        text = script.read_text(encoding="utf-8")
        script.write_text(text.replace("+'\\n```'", "+'\\n```\\n```json\\n{}\\n```'"), encoding="utf-8")
    report = execute(module, tmp_path, module.BridgeCLIAdapter(interpreter=sys.executable, server_path=script), route("bridge"))
    assert report["outcome"] == "inconclusive"


@pytest.mark.parametrize("value", [None, [], "fake", {"schema_version": 1, "frame_sets": [None]}])
def test_malformed_visual_payload_raises_value_error(value):
    parts = sample()
    parts[0]["visual"] = value
    with pytest.raises(ValueError):
        check(visual(), parts)


def test_labeled_overview_links_every_original_without_combining_consumers(tmp_path):
    frames = execution_frames(tmp_path)
    for i, frame in enumerate(frames):
        frame.update(monotonic=10 + i, sequence=i, consumer="sdk", scenario="steady")
    path = tmp_path / "overview.html"
    visual().prepare_overview(frames, path)
    text = path.read_text(encoding="utf-8")
    assert text.count("<figure>") == 5
    assert all(frame["frame_id"] in text and Path(frame["preview_path"]).as_uri() in text for frame in frames)
    assert "sdk" in text and "sequence=4" in text


def test_custom_sampling_fractions_are_taken_from_contract():
    parts = sample()
    config = contract(2)
    config["sample_fractions"] = [0.2, 0.8]
    group = parts[0]["visual"]["frame_sets"][0]
    group["frames"] = [group["frames"][0], group["frames"][-1]]
    for frame, when in zip(group["frames"], [14.0, 26.0]):
        frame["monotonic"] = when
        parts[1][frame["source_item_id"]]["metadata"]["monotonic"] = when
    for key in list(parts[1]):
        if key not in {"raw-0", "raw-4"}:
            del parts[1][key]
    assert check(visual(), parts, config)["outcome"] == "pass"


@pytest.mark.parametrize("mutation", ["frame", "frames", "metadata", "reference", "receipt"])
def test_malformed_nested_payloads_fail_closed_with_value_error(mutation):
    parts = sample()
    group = parts[0]["visual"]["frame_sets"][0]
    if mutation == "frame":
        group["frames"][0] = None
    elif mutation == "frames":
        group["frames"] = "five frames"
    elif mutation == "metadata":
        parts[1]["raw-0"]["metadata"] = []
    elif mutation == "reference":
        group["frames"][0]["review_refs"] = [None]
    else:
        parts = (*parts[:3], {"review_id": "review-1", "channels": [None]})
    with pytest.raises(ValueError):
        check(visual(), parts)


def test_actual_display_requires_screenshot_or_recording_not_a_flag():
    parts = sample()
    config = contract()
    config["require_display"] = True
    group = parts[0]["visual"]["frame_sets"][0]
    group.update(display=True, layer="operation_view")
    for item in parts[1].values():
        item["metadata"]["layer"] = "operation_view"
    with pytest.raises(ValueError, match="screenshot|recording|display"):
        check(visual(), parts, config)
    for item in parts[1].values():
        item["metadata"]["capture_method"] = "screenshot"
    assert check(visual(), parts, config)["outcome"] == "pass"


def test_bridge_failed_response_is_retained_for_audit(tmp_path):
    module = visual()
    callback = module.BridgeCLIAdapter(interpreter=sys.executable,
                                       server_path=bridge_script(tmp_path, wrong_hash=True))
    report = execute(module, tmp_path, callback, route("bridge"))
    assert report["outcome"] == "inconclusive"
    assert len(report["provider_results"]) == 5


@requires_ffmpeg
def test_v04_wrong_byte_order_fails_known_reference_colors(tmp_path):
    data, layout = raw_bars("UYVY")
    layout["fourcc"] = "YUYV"  # Metadata itself cannot magically correct actual bytes.
    source, output = tmp_path / "frame.raw", tmp_path / "wrong.png"
    source.write_bytes(data)
    visual().prepare_preview(source, layout, output)
    pixels = subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-i", str(output),
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                            capture_output=True, check=True, timeout=10).stdout
    assert not (pixels[0] > 230 and pixels[1] < 20 and pixels[2] < 20)


def test_missing_ffmpeg_blocks_raw_conversion_without_affecting_original(tmp_path, monkeypatch):
    module = visual()
    data, layout = raw_bars("UYVY")
    source, output = tmp_path / "frame.raw", tmp_path / "preview.png"
    source.write_bytes(data)
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    with pytest.raises(module.VisualEvidenceError, match="FFmpeg unavailable"):
        module.prepare_preview(source, layout, output)
    assert not output.exists()
    assert source.read_bytes() == data


@requires_ffmpeg
def test_v09_unknown_colors_require_explicit_documented_decode_assumptions(tmp_path):
    data, layout = raw_bars("UYVY")
    layout["colors"] = {"matrix": "unknown", "range": "unknown"}
    layout["decode_assumptions"] = {"matrix": "bt601", "range": "limited"}
    source, output = tmp_path / "raw.frame", tmp_path / "preview.png"
    source.write_bytes(data)
    report = visual().prepare_preview(source, layout, output)
    assert report["layout"]["colors"]["matrix"] == "unknown"
    assert report["decode_assumptions"] == {"matrix": "bt601", "range": "limited"}
    assert any("assumption" in value.lower() and "IQ" in value for value in report["limitations"])
    assert "scale=in_color_matrix=bt601:in_range=limited:out_range=full" in report["converter"]["argv"]


def test_known_color_metadata_cannot_be_overridden_by_assumptions(tmp_path):
    data, layout = raw_bars("UYVY")
    layout["decode_assumptions"] = {"matrix": "bt709", "range": "full"}
    source = tmp_path / "raw.frame"
    source.write_bytes(data)
    with pytest.raises(ValueError, match="assumption|override"):
        visual().prepare_preview(source, layout, tmp_path / "preview.png")


def packed_sample(tmp_path):
    module = visual()
    bundle, old_items, files, receipt = sample()
    frames = bundle["visual"]["frame_sets"][0]["frames"]
    group = bundle["visual"]["frame_sets"][0]
    group["frames"] = [frames[0], frames[-1]]
    group["mode"] = {"width": 4, "height": 2, "fourcc": "UYVY"}
    items, results = {}, []
    raw, layout = raw_bars("UYVY")
    for i, frame in enumerate(group["frames"]):
        source_id, preview_id = frame["source_item_id"], "preview-" + str(i)
        source = copy.deepcopy(old_items[source_id])
        source.update(path=source_id + ".raw", sha256=digest(raw), bytes=len(raw))
        source["metadata"]["mode"] = copy.deepcopy(group["mode"])
        source["metadata"]["layout"] = copy.deepcopy(layout)
        raw_path, preview_path = tmp_path / source["path"], tmp_path / (preview_id + ".png")
        raw_path.write_bytes(raw)
        conversion = module.prepare_preview(raw_path, layout, preview_path)
        image = preview_path.read_bytes()
        preview = copy.deepcopy(source)
        preview.update(id=preview_id, path=preview_path.name, sha256=digest(image), bytes=len(image), source_ids=[source_id])
        preview["metadata"]["conversion"] = conversion
        items[source_id], items[preview_id] = source, preview
        files[source_id], files[preview_id] = raw, image
        frame["preview_item_id"] = preview_id
        row = copy.deepcopy(receipt["frame_results"][0 if i == 0 else -1])
        row.update(input_sha256=digest(image), source_sha256=digest(raw))
        results.append(row)
    receipt["frame_results"] = results
    return bundle, items, files, receipt


@requires_ffmpeg
def test_v08_packed_preview_pixels_are_recomputed_from_raw(tmp_path):
    parts = packed_sample(tmp_path)
    assert check(visual(), parts, contract(2))["outcome"] == "pass"
    frame = parts[0]["visual"]["frame_sets"][0]["frames"][0]
    preview = parts[1][frame["preview_item_id"]]
    wrong = png((0, 0, 255))
    preview["sha256"] = digest(wrong)
    preview["metadata"]["conversion"]["preview_sha256"] = digest(wrong)
    parts[2][preview["id"]] = wrong
    parts[3]["frame_results"][0]["input_sha256"] = digest(wrong)
    with pytest.raises(ValueError, match="pixels|conversion"):
        check(visual(), parts, contract(2))


@requires_ffmpeg
def test_dynamic_receipt_and_controlled_change_cannot_prove_update_from_static_pixels():
    parts = sample()
    group = parts[0]["visual"]["frame_sets"][0]
    group["claims"] = ["not_frozen"]
    group["controlled_change"] = {"item_id": "change", "frame_ids": ["frame-0", "frame-4"],
                                  "description": "Controlled target moves between positions."}
    parts[1]["change"] = {"id": "change", "kind": "log", "sha256": digest(b"move")}
    parts[2]["change"] = b"move"
    for row in parts[3]["frame_results"]:
        row.update(controlled_change_item_id="change", dynamic_observation="Target moved.")
    with pytest.raises(ValueError, match="dynamic|changed|static"):
        check(visual(), parts)


def test_same_source_path_under_different_ids_is_not_a_new_sample():
    parts = sample()
    parts[1]["raw-1"]["path"] = parts[1]["raw-0"]["path"]
    with pytest.raises(ValueError, match="source|path"):
        check(visual(), parts)


@requires_ffmpeg
def test_controlled_changed_pixels_and_bound_observations_support_dynamic_scope():
    parts = sample()
    group = parts[0]["visual"]["frame_sets"][0]
    group["claims"] = ["dynamic_update"]
    group["controlled_change"] = {"item_id": "change", "frame_ids": ["frame-0", "frame-4"],
                                  "description": "Controlled red target changes to blue."}
    parts[1]["change"] = {"id": "change", "kind": "log", "sha256": digest(b"move")}
    parts[2]["change"] = b"move"
    changed = png((0, 0, 255))
    parts[2]["raw-4"] = changed
    parts[1]["raw-4"]["sha256"] = digest(changed)
    parts[3]["frame_results"][-1].update(input_sha256=digest(changed), source_sha256=digest(changed))
    for row in parts[3]["frame_results"]:
        row.update(controlled_change_item_id="change", dynamic_observation="Target color changes.")
    assert check(visual(), parts)["outcome"] == "pass"


@requires_ffmpeg
def test_jpeg_direct_input_and_lossless_derived_preview_are_supported(tmp_path):
    module = visual()
    source, jpeg, output = tmp_path / "source.png", tmp_path / "frame.jpg", tmp_path / "preview.png"
    source.write_bytes(png())
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-i", str(source), "-frames:v", "1", str(jpeg)],
                   check=True, timeout=10, stdin=subprocess.DEVNULL)
    report = module.prepare_preview(jpeg, {"width": 4, "height": 2, "fourcc": "JPEG"}, output)
    assert report["source_sha256"] == digest(jpeg.read_bytes())
    assert report["preview_sha256"] == digest(output.read_bytes())
    assert output.read_bytes().startswith(b"\x89PNG")


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf")])
def test_invalid_sample_budgets_are_rejected(value):
    config = contract()
    config["min_frames"] = value
    with pytest.raises(ValueError):
        check(visual(), sample(), config)


def test_duplicate_json_keys_cannot_hide_a_provider_failure(tmp_path):
    module = visual()
    # Duplicate members are ambiguous even when json.loads would use the final pass.
    with pytest.raises(ValueError):
        module._provider_json('{"outcome":"fail","outcome":"pass"}')


def test_partial_bridge_route_executes_but_cannot_exceed_candidate_ceiling(tmp_path):
    selected = route("bridge")
    selected.update(status="partial", bridge_qualification="unknown")
    calls = []
    def observe(request):
        calls.append(request["batch_index"])
        return adapter(request)
    report = execute(visual(), tmp_path, observe, selected)
    assert calls == [0, 1]
    assert len(report["frame_results"]) == 5
    assert report["outcome"] == "inconclusive"


def test_control_plane_spec_loading_needs_no_tools_python_path(tmp_path):
    bundle, items, files, receipt = sample()
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"bundle": bundle, "items": items,
        "files": {key: value.hex() for key, value in files.items()},
        "receipt": receipt, "contract": contract()}), encoding="utf-8")
    script = ("import importlib.util,json,sys;"
              "spec=importlib.util.spec_from_file_location('isolated_visual',sys.argv[1]);"
              "mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);"
              "data=json.load(open(sys.argv[2],encoding='utf-8'));"
              "read=lambda key:bytes.fromhex(data['files'][key]);"
              "report=mod.validate_visual(data['bundle'],data['contract'],items=data['items'],"
              "read_item=read,run=data['bundle'],review_validator=lambda ref:data['receipt']);"
              "assert report['outcome']=='pass';print(report['frame_count'])")
    result = subprocess.run([sys.executable, "-I", "-c", script,
                             str(ROOT / "tools/visual_evidence.py"), str(payload)],
                            cwd=tmp_path, capture_output=True, text=True, check=True, timeout=10)
    assert result.stdout.strip() == "5"


@pytest.mark.parametrize("where", ["number", "timestamp", "window"])
def test_unrepresentable_finite_integer_is_a_structured_error(where):
    module = visual()
    if where == "number":
        with pytest.raises(ValueError):
            module._number(10 ** 400, "huge")
        return
    parts = sample()
    if where == "timestamp":
        parts[0]["visual"]["frame_sets"][0]["frames"][0]["timestamp"] = 10 ** 400
        parts[1]["raw-0"]["metadata"]["timestamp"] = 10 ** 400
    else:
        parts[0]["window"]["monotonic_end"] = 10 ** 400
    with pytest.raises(ValueError):
        check(module, parts)


@pytest.mark.parametrize("target", ["file", "parent", "output_parent"])
def test_windows_reparse_attribute_is_rejected_even_when_not_a_symlink(tmp_path, monkeypatch, target):
    source_dir, output_dir = tmp_path / "source", tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    source = source_dir / "frame.png"
    source.write_bytes(png())
    blocked = source if target == "file" else source_dir if target == "parent" else output_dir
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == blocked:
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            values["st_file_attributes"] = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT, including junctions.
            return types.SimpleNamespace(**values)
        return info
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError, match="reparse|symlink"):
        visual().prepare_preview(source, {"width": 4, "height": 2, "fourcc": "PNG"}, output_dir / "preview.png")

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - tests execute trusted local tooling only
import tempfile
from pathlib import Path

from tests.conftest import REPO_ROOT


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def _first_boot() -> str:
    return _read("rootfs/usr/local/bin/gbrain-first-boot")


def _http() -> str:
    return _read("rootfs/etc/services.d/gbrain-http/run")


def _worker() -> str:
    return _read("rootfs/etc/services.d/gbrain-worker/run")


def _autopilot() -> str:
    return _read("rootfs/etc/services.d/gbrain-autopilot/run")


def _dream_svc() -> str:
    return _read("rootfs/etc/services.d/gbrain-dream/run")


def _doctor_svc() -> str:
    return _read("rootfs/etc/services.d/gbrain-doctor/run")


def _xml() -> str:
    return _read("gbrain-aio.xml")


def _rootfs_blob() -> str:
    parts = [
        _first_boot(),
        _http(),
        _worker(),
        _autopilot(),
        _dream_svc(),
        _doctor_svc(),
        _read("rootfs/usr/local/bin/gbrain-enqueue-first-sync"),
        _read("rootfs/usr/local/bin/gbrain-dream-once"),
        _read("rootfs/usr/local/bin/gbrain-doctor-once"),
        _read("rootfs/usr/local/bin/gbrain-push-after-cycle"),
        _read("rootfs/usr/local/bin/gbrain-merge-file-config"),
        _read("rootfs/usr/local/lib/gbrain-aio-lib.sh"),
        _read("gbrain-aio.xml"),
        _read("compose.yaml"),
        _read(".env.example"),
    ]
    return "\n".join(parts)


def test_first_boot_uses_spec_init_and_schema() -> None:
    script = _first_boot()
    assert "gbrain init" in script  # nosec B101
    assert "--embedding-model ollama:embeddinggemma" in script  # nosec B101
    assert "--embedding-dimensions 768" in script  # nosec B101
    assert "--non-interactive" in script  # nosec B101
    assert "--skip-embed-check" in script  # nosec B101
    assert "git init" in script  # nosec B101
    assert "uid 99" in script  # nosec B101
    assert "safe.directory" in script  # nosec B101
    assert "sources add" in script  # nosec B101
    assert "sources federate" in script  # nosec B101
    assert "schema use gbrain-everything" in script  # nosec B101
    assert "gbrain-merge-file-config" in script  # nosec B101
    assert "--force" not in script  # nosec B101


def test_http_does_not_block_on_sync() -> None:
    http = _http()
    assert "gbrain-first-boot" in http  # nosec B101
    assert "first-boot failed; starting serve" in http  # nosec B101
    assert "gbrain-enqueue-first-sync &" in http  # nosec B101
    assert "gbrain serve --http" in http  # nosec B101
    assert "jobs submit sync" not in http  # nosec B101


def test_enqueue_is_source_scoped_after_health() -> None:
    enqueue = _read("rootfs/usr/local/bin/gbrain-enqueue-first-sync")
    assert "jobs submit sync" in enqueue  # nosec B101
    assert "sourceId" in enqueue  # nosec B101
    assert "127.0.0.1:3131/health" in enqueue  # nosec B101


def test_worker_supervisor_unchanged() -> None:
    worker = _worker()
    assert "gbrain jobs supervisor --nice 10" in worker  # nosec B101
    assert "wait_config" in worker  # nosec B101
    assert "autopilot" not in worker  # nosec B101


def test_autopilot_is_s6_longrun_30m_no_worker() -> None:
    auto = _autopilot()
    assert "--repo" in auto  # nosec B101
    assert "--interval 1800" in auto  # nosec B101
    assert "--no-worker" in auto  # nosec B101
    assert "--install" not in auto  # nosec B101
    assert "gbrain-push-after-cycle" in auto  # nosec B101


def test_dream_and_doctor_are_in_container_timers() -> None:
    dream = _dream_svc()
    doctor = _doctor_svc()
    assert "gbrain-sleep-until 02:00" in dream  # nosec B101
    assert "gbrain-dream-once" in dream  # nosec B101
    assert "gbrain-sleep-until monday 06:00" in doctor  # nosec B101
    assert "gbrain-doctor-once" in doctor  # nosec B101
    assert "gbrain dream --dir" in _read(
        "rootfs/usr/local/bin/gbrain-dream-once"
    )  # nosec B101
    assert "cycle_already_running" in _read(
        "rootfs/usr/local/bin/gbrain-dream-once"
    )  # nosec B101
    assert "gbrain doctor --json" in _read(
        "rootfs/usr/local/bin/gbrain-doctor-once"
    )  # nosec B101
    assert "last-doctor.json" in _read(
        "rootfs/usr/local/bin/gbrain-doctor-once"
    )  # nosec B101
    assert "--remediate --max-usd" in _read(
        "rootfs/usr/local/bin/gbrain-doctor-once"
    )  # nosec B101


def test_no_force_push_and_token_stays_out_of_remote() -> None:
    push = _read("rootfs/usr/local/bin/gbrain-push-after-cycle")
    assert "push --force" not in push  # nosec B101
    assert "git push --force" not in _rootfs_blob()  # nosec B101
    assert "http.extraHeader=Authorization: Bearer" in push  # nosec B101
    assert "remote set-url origin" in push  # nosec B101
    assert "BRAIN_GIT_PUSH_URL" in push  # nosec B101
    assert "autopilot-cycle" in push  # nosec B101


def test_no_hermes_jsonl_and_no_crontab_install() -> None:
    blob = _rootfs_blob()
    assert "session_corpus" not in blob  # nosec B101
    assert "autopilot --install" not in blob  # nosec B101
    assert ".jsonl" not in _xml()  # nosec B101
    assert "hermes" not in _xml().lower()  # nosec B101


def test_xml_has_optional_fields_empty_by_default() -> None:
    xml = _xml()
    for name in (
        "BRAIN_GIT_PUSH_URL",
        "BRAIN_GIT_PUSH_TOKEN",
        "DOCTOR_REMEDIATE_MAX_USD",
        "SKILLOPT_ENABLED",
        "NIGHTLY_QUALITY_PROBE",
        "PARSER_PROBE_ENABLED",
    ):
        assert f'Target="{name}"' in xml  # nosec B101
        assert f'Target="{name}" Default=""' in xml  # nosec B101
    assert 'Target="CHAT_MODEL"' in xml  # nosec B101
    assert "deepseek-v4-flash:cloud" in xml  # nosec B101
    assert 'Target="BRAIN_GIT_PUSH_TOKEN"' in xml and 'Mask="true"' in xml  # nosec B101


def test_stay_off_flags_are_not_enabled() -> None:
    blob = _rootfs_blob()
    assert "dream.auto_think" not in blob  # nosec B101
    assert "unified_multimodal" not in blob  # nosec B101
    assert "tokenmax" not in blob  # nosec B101
    assert "reranker.enabled" not in blob  # nosec B101


def test_compose_and_env_example_include_optional_fields() -> None:
    compose = _read("compose.yaml")
    env = _read(".env.example")
    for name in (
        "CHAT_MODEL",
        "BRAIN_GIT_PUSH_URL",
        "BRAIN_GIT_PUSH_TOKEN",
        "DOCTOR_REMEDIATE_MAX_USD",
        "SKILLOPT_ENABLED",
        "NIGHTLY_QUALITY_PROBE",
        "PARSER_PROBE_ENABLED",
    ):
        assert name in compose  # nosec B101
        assert name in env  # nosec B101


def _run_merge(env: dict[str, str], existing: dict | None = None) -> tuple[dict, dict]:
    bun = shutil.which("bun")
    if bun is None:
        return {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        if existing is not None:
            path.write_text(json.dumps(existing))
        merged_env = os.environ.copy()
        merged_env.update(env)
        merged_env["CONFIG_PATH"] = str(path)
        result = subprocess.run(  # nosec B603
            [bun, str(REPO_ROOT / "rootfs/usr/local/bin/gbrain-merge-file-config")],
            check=True,
            text=True,
            capture_output=True,
            env=merged_env,
        )
        cfg = json.loads(path.read_text())
        flags = json.loads(result.stdout)
        return cfg, flags


def test_file_plane_keyless_install_has_no_chat_model() -> None:
    if shutil.which("bun") is None:
        merge = _read("rootfs/usr/local/bin/gbrain-merge-file-config")
        assert (
            "together:${chatModelName}" in merge or "together:" in merge
        )  # nosec B101
        assert "ollamaBase" in merge  # nosec B101
        return
    cfg, flags = _run_merge(
        {
            "SOURCE_PATH": "/my-brain",
            "OLLAMA_BASE_URL": "",
            "APPLY_CHAT_GATED": "0",
        }
    )
    assert cfg["embedding_model"] == "ollama:embeddinggemma"  # nosec B101
    assert cfg["embedding_dimensions"] == 768  # nosec B101
    assert cfg["sync"]["repo_path"] == "/my-brain"  # nosec B101
    assert cfg["mcp"]["publish_advisor"] is True  # nosec B101
    assert cfg["mcp"]["publish_skills"] is True  # nosec B101
    assert cfg["mcp"]["skills_dir"] == "/opt/gbrain/skills"  # nosec B101
    assert "chat_model" not in cfg  # nosec B101
    assert "dream.drift.enabled" not in flags  # nosec B101
    assert "cycle.skillopt.enabled" not in flags  # nosec B101
    assert flags["mcp.publish_advisor"] == "true"  # nosec B101
    assert flags["mcp.skills_dir"] == "/opt/gbrain/skills"  # nosec B101


def test_file_plane_chat_and_optional_toggles() -> None:
    if shutil.which("bun") is None:
        return
    cfg, flags = _run_merge(
        {
            "SOURCE_PATH": "/my-brain",
            "OLLAMA_BASE_URL": "http://example.invalid:11434",
            "CHAT_MODEL": "deepseek-v4-flash:cloud",
            "SKILLOPT_ENABLED": "1",
            "NIGHTLY_QUALITY_PROBE": "true",
            "PARSER_PROBE_ENABLED": "on",
        }
    )
    assert cfg["chat_model"] == "together:deepseek-v4-flash:cloud"  # nosec B101
    assert cfg["expansion_model"] == "together:deepseek-v4-flash:cloud"  # nosec B101
    assert (
        cfg["provider_base_urls"]["together"] == "http://example.invalid:11434/v1"
    )  # nosec B101
    assert cfg["dream"]["drift"]["enabled"] is True  # nosec B101
    assert cfg["cycle"]["skillopt"]["enabled"] is True  # nosec B101
    assert cfg["autopilot"]["nightly_quality_probe"]["enabled"] is True  # nosec B101
    assert (
        cfg["autopilot"]["conversation_parser_probe"]["enabled"] is True
    )  # nosec B101
    assert flags["dream.drift.enabled"] == "true"  # nosec B101
    assert flags["cycle.skillopt.enabled"] == "true"  # nosec B101

"""XML <-> bootstrap <-> runtime.env parity contract tests.

The Unraid XML <Config Type="Variable"> surface is the trigger surface.
Every var the setup chain reads must be reachable from the XML, and every
XML variable must be consumed (or passed through) by the chain.

XML parsing here uses stdlib ElementTree on OUR OWN repo file (not untrusted
input), so defusedxml is unnecessary; noted to satisfy the lint rule.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405 B406 B314  # own repo file, no external entities
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
XML = APP / "gbrain-aio.xml"
BOOTSTRAP = APP / "rootfs/etc/cont-init.d/01-bootstrap.sh"
LIB = APP / "rootfs/usr/local/lib/gbrain-aio-lib.sh"
SERVICES = APP / "rootfs/etc/services.d"


def xml_vars() -> dict[str, bool]:
    tree = ET.parse(XML)  # nosec B406 B314  # own repo file, trusted source
    out: dict[str, bool] = {}
    for el in tree.iter("Config"):
        if el.get("Type") == "Variable":
            out[el.get("Target", "")] = el.get("Required") == "true"
    return out


def _bootstrap_env_reads() -> set[str]:
    s = BOOTSTRAP.read_text()
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)(?::-[^}]*)?-?\}", s))


def _runtime_env_keys() -> set[str]:
    s = BOOTSTRAP.read_text()
    "runtime.env" + chr(34) + " <<EOF" if False else None
    # heredoc line: cat >"..."/runtime.env" <<EOF (redirect target may be quoted/derived)
    idx = s.find("runtime.env")
    m = None
    if idx != -1:
        heredoc_start = s.find("<<EOF", idx)
        if heredoc_start != -1:
            body_start = heredoc_start + len("<<EOF\n")
            body_end = s.find("\nEOF\n", body_start)
            if body_end != -1:
                heredoc = s[body_start:body_end]
                m = re.findall(r"^([A-Z][A-Z0-9_]*)=", heredoc, re.M)
                return set(m)
    return set()


def _s6_env_reads() -> set[str]:
    reads: set[str] = set()
    for run in SERVICES.rglob("run"):
        s = run.read_text()
        reads |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)(?::-[^}]*)?-?\}", s))
    return reads


def test_required_xml_vars_have_no_default() -> None:
    tree = ET.parse(XML)  # nosec B406 B314  # own repo file, trusted source
    for el in tree.iter("Config"):
        if el.get("Required") == "true" and el.get("Type") == "Variable":
            assert not (  # nosec B101 # test assertions
                el.text or ""
            ).strip(), f"required var {el.get('Target')} must have empty default"


def test_bootstrap_required_vars_exposed_in_xml() -> None:
    xml_targets = set(xml_vars())
    for var in ("POSTGRES_PASSWORD", "GBRAIN_LAN_BIND", "GBRAIN_ADMIN_BOOTSTRAP_TOKEN"):
        assert (
            var in xml_targets
        ), f"{var} must be settable from the XML"  # nosec B101 # test assertions


def test_no_homelab_ip_defaults_in_rootfs() -> None:
    for path in [BOOTSTRAP, *SERVICES.rglob("run"), LIB]:
        s = path.read_text()
        assert (
            "192.168.1." not in s
        ), f"homelab IP default leaked into {path}"  # nosec B101 # test assertions


def test_every_xml_variable_target_is_consumed_by_chain() -> None:
    chain_reads = set(_bootstrap_env_reads())
    chain_reads |= set(
        re.findall(r"\$\{([A-Z][A-Z0-9_]+)(?::-[^}]*)?-?\}", (LIB.read_text()))
    )
    for run in SERVICES.rglob("run"):
        chain_reads |= set(
            re.findall(r"\$\{([A-Z][A-Z0-9_]+)(?::-[^}]*)?-?\}", run.read_text())
        )
    consumed_by_first_boot = set(
        re.findall(
            r"\$\{([A-Z][A-Z0-9_]+)(?::-[^}]*)?-?\}",
            (APP / "rootfs/usr/local/bin/gbrain-first-boot").read_text(),
        )
    )
    consumed_by_merge = set(
        re.findall(
            r"process\.env\.([A-Z][A-Z0-9_]+)",
            (APP / "rootfs/usr/local/bin/gbrain-merge-file-config").read_text(),
        )
    )
    # Every other helper under rootfs/usr/local/bin (dream-once, doctor-once,
    # push-after-cycle, ...) also consumes XML-triggered vars.
    for helper in (APP / "rootfs/usr/local/bin").iterdir():
        consumed_by_merge |= set(
            re.findall(r"\$\{([A-Z][A-Z0-9_]+)(?::-[^}]*)?-?\}", helper.read_text())
        )
    # Vars the gbrain runtime itself consumes via container env passthrough
    # (sourced by with-contenv, never by a setup script).
    runtime_consumed = {
        "GBRAIN_ADMIN_BOOTSTRAP_TOKEN",
        "POSTGRES_PASSWORD",
        "BRAIN_GIT_PUSH_TOKEN",
        "TOGETHER_API_KEY",
        # Vendor keys are read by the gbrain gateway directly from the
        # container env (with-contenv passthrough), never by setup scripts.
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
        "VOYAGE_API_KEY",
    }

    known = chain_reads | consumed_by_first_boot | consumed_by_merge | runtime_consumed
    for target, _required in xml_vars().items():
        assert (
            target in known
        ), f"XML var {target} is not consumed by any setup script"  # nosec B101 # test assertions


def test_only_key_required_providers_have_xml_key_fields() -> None:
    tree = ET.parse(XML)  # nosec B406 B314  # own repo file, trusted source
    key_targets = {
        el.get("Target")
        for el in tree.iter("Config")
        if el.get("Type") == "Variable" and "API_KEY" in (el.get("Target") or "")
    }
    assert (
        "OLLAMA_API_KEY" not in key_targets
    ), "ollama needs no key; do not expose"  # nosec B101 # test assertions
    assert (
        "TOGETHER_API_KEY" not in key_targets
    ), "together key is script-managed"  # nosec B101 # test assertions
    expected = {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "VOYAGE_API_KEY",
    }
    assert (
        key_targets == expected
    ), f"key fields mismatch: {key_targets ^ expected}"  # nosec B101 # test assertions


def test_runtime_env_heredoc_matches_xml_surface() -> None:
    runtime_keys = _runtime_env_keys()
    xml_targets = set(xml_vars())
    # Every runtime.env key must be an XML var, a fixed internal, or a
    # bootstrap-internal (DATABASE_URL/encoded values, derived tailnet names).
    internal = {
        "DATABASE_URL",
        "GBRAIN_DATABASE_URL",
        "GBRAIN_HOME",
        "GBRAIN_HTTP_PORT",
        "GBRAIN_HTTP_BIND",
        "TS_SOCKET",
        "OLLAMA_API_KEY",
        "PGDATA",
        "CERT_DIR",
        "AIO_APPDATA",
        "TOGETHER_API_KEY",
        "TS_DERIVED_DNS_NAME",
        "TS_DERIVED_IP",
    }
    for key in runtime_keys:
        assert (  # nosec B101 # test assertions
            key in xml_targets or key in internal
        ), f"runtime.env key {key} has no XML trigger"


def test_merge_file_config_reads_all_xml_model_vars() -> None:
    s = (APP / "rootfs/usr/local/bin/gbrain-merge-file-config").read_text()
    for var in (
        "CHAT_PROVIDER",
        "CHAT_MODEL",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIMENSIONS",
        "SCHEMA_PACK",
        "GBRAIN_EXTRA_CONFIG",
    ):
        assert (
            f"process.env.{var}" in s
        ), f"merge engine does not read {var}"  # nosec B101 # test assertions


def test_s6_scripts_read_schedule_vars() -> None:
    dream = (SERVICES / "gbrain-dream/run").read_text()
    doctor = (SERVICES / "gbrain-doctor/run").read_text()
    autopilot = (SERVICES / "gbrain-autopilot/run").read_text()
    assert "dream_at" in dream and "DREAM_AT" in (
        LIB.read_text()
    )  # nosec B101 # test assertions
    assert (
        "doctor_day" in doctor and "doctor_at" in doctor
    )  # nosec B101 # test assertions
    assert "AUTOPILOT_INTERVAL" in autopilot  # nosec B101 # test assertions


def test_first_boot_marks_doctor_and_backfill() -> None:
    s = (APP / "rootfs/usr/local/bin/gbrain-first-boot").read_text()
    assert "need-first-doctor" in s  # nosec B101 # test assertions
    assert "need-graph-backfill" in s  # nosec B101 # test assertions


def test_graph_backfill_is_idempotent_and_source_scoped() -> None:
    s = (APP / "rootfs/usr/local/bin/gbrain-graph-backfill").read_text()
    assert "extract links --source db --source-id" in s  # nosec B101 # test assertions
    assert (
        "extract timeline --source db --source-id" in s
    )  # nosec B101 # test assertions
    assert "graph-backfill.done" in s  # nosec B101 # test assertions


def test_post_helper_waits_for_first_sync_job() -> None:
    s = (APP / "rootfs/usr/local/bin/gbrain-first-sync-post").read_text()
    assert "jobs get" in s  # nosec B101 # test assertions
    assert "first-sync.job" in s  # nosec B101 # test assertions
    # backfill must come after the sync wait, not before
    assert s.index("first-sync.job") < s.index(
        "gbrain-graph-backfill"
    )  # nosec B101 # test assertions


def test_http_launches_post_helper_detached() -> None:
    s = (SERVICES / "gbrain-http/run").read_text()
    assert "gbrain-first-sync-post &" in s  # nosec B101 # test assertions
    # still must not block HTTP on indexing
    assert s.index("gbrain serve --http") > s.index(
        "gbrain-first-sync-post &"
    )  # nosec B101 # test assertions


def test_single_appdata_root_field_present() -> None:
    tree = ET.parse(XML)  # nosec B406,B314  # own repo file, trusted source
    targets = {el.get("Target") for el in tree.iter("Config") if el.get("Type") == "Path"}
    assert "/data/aio" in targets, "single appdata root must be exposed"
    assert "/data/postgres" not in targets and "/var/lib/gbrain" not in targets and "/config/caddy" not in targets


def test_bootstrap_derives_paths_from_aio_appdata() -> None:
    s = BOOTSTRAP.read_text()
    assert "AIO_APPDATA" in s
    assert "POSTGRES_DATA=" in s and "GBRAIN_HOME_DIR=" in s and "CADDY_CERTS=" in s
    # adoption migration present (legacy -> single root)
    assert "migrating gbrain-home" in s


def test_runtime_env_carries_derived_paths() -> None:
    keys = _runtime_env_keys()
    assert {"GBRAIN_HOME", "PGDATA", "CERT_DIR"} <= keys

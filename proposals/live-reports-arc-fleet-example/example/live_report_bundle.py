#!/usr/bin/env python3
"""
live_report_bundle.py - export / verify / install Azure SRE Agent Live Report definitions.

A Live Report is defined by:
  * its saved HTML source (layout, CSS, JS, CSP with the raw {REPORT_NONCE} placeholder)
  * name, description
  * allowedTools (the connector tools the HTML may call at view time)

"Download HTML" in the portal produces a sanitized, rendered, static snapshot. That
snapshot cannot call tools and is NOT an installable definition. This script works
on the saved source instead, which is what the agent's GetReport tool checks out to
tmp/livereport-<id>.html. It packages that source byte-for-byte into a portable
bundle (*.livereport.json), so the rendering layout is identical after install.

Bundle format: schemaVersion "sreagent.livereport/v1" (print it with `schema`).

Commands
--------
export   Build bundles from checked-out report HTML plus metadata.
         Run it where the HTML lives (normally the source agent's workspace, after
         the agent has run ListReports/GetReport).
           python3 live_report_bundle.py export --meta reports-meta.json --out bundles/
           python3 live_report_bundle.py export --html tmp/livereport-X.html \
               --name "My report" --description "..." --allowed-tool <tool> --out bundles/

verify   Check bundle integrity and installability (sha256, nonce placeholders,
         CSP, every callTool name is in allowedTools, SRI on CDN scripts).
           python3 live_report_bundle.py verify bundles/*.livereport.json

bindings Show environment-specific values (subscription, workspace, resource
         group, GUIDs) detected in a bundle. These can be rebound at install time.
           python3 live_report_bundle.py bindings bundles/x.livereport.json

prepare  Target side (run in the target agent's workspace, or locally). Verify,
         apply --set rebinds, write ready-to-save HTML plus install-plan.json. The
         agent then calls SaveReport once per plan entry.
           python3 live_report_bundle.py prepare bundles/x.livereport.json \
               --set subscription=<sub> --set workspace=<ws-guid> --set resourceGroup=<rg> \
               --out-dir tmp/livereport-install

install  Remote, from an operator workstation (az login). Asks the TARGET agent,
         through the documented data-plane threads API, to run `prepare` on a bundle
         that sits in a code repository connected to that agent (codeRefs/<repo>/...),
         then SaveReport each entry. Polls the thread and reports pending approvals.
         Tool-access approval is left to a human.
           python3 live_report_bundle.py install \
               --agent-id /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.App/agents/<name> \
               --repo-path codeRefs/<repo>/live-reports \
               --bundle x.livereport.json --set workspace=<guid> [--dry-run]

clean    Sanitize bundles for source control (e.g. GitHub). Bound values (subscription,
         workspace, resource group, IDs) become required __PLACEHOLDER__s that
         `prepare --set` must fill in. Values listed in --map (host names, other RGs,
         names in text) are replaced by pseudonyms, so the layout and demo data still
         render. Fails if any original value survives (leak scan) or if the inline
         JavaScript no longer parses (node --check, when node is available).
           python3 live_report_bundle.py clean bundles/*.livereport.json \
               --map redaction-map.txt --out cleaned/      # names get ' - Cleaned'

schema   Print the JSON Schema of the bundle format.

Only the Python 3.8+ standard library is used. `install` also needs the Azure CLI.
"""
import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "sreagent.livereport/v1"
NONCE = "{REPORT_NONCE}"
ARM_API = "2025-05-01-preview"
DATA_PLANE_AUDIENCE = "https://azuresre.dev"

GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# key: 'value' pairs that are typically environment-specific in callTool args / config blocks
KEY_RE = re.compile(
    r"""(['"]?)(subscriptionId|subscription|workspaceId|workspace|resource-group|resourceGroup|"""
    r"""clusterUrl|cluster|database)\1\s*:\s*(['"])([^'"\n<>{}]{1,200})\3""")
RG_PATH_RE = re.compile(r"/resourceGroups/([A-Za-z0-9._()\-]{1,90})(?=[/'\"\s,]|$)", re.IGNORECASE)
# const SUB = '...'; const WS = '...'; const RESOURCE_GROUP = '...'
CONST_RE = re.compile(r"""\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(['"])([^'"\n<>{}]{1,200})\2""")


def _const_binding_name(ident):
    u = ident.upper()
    if "SUB" in u:
        return "subscription"
    if u in ("WS", "LAW") or "WORKSPACE" in u:
        return "workspace"
    if u == "RG" or "RESOURCE_GROUP" in u or "RESOURCEGROUP" in u:
        return "resourceGroup"
    if "CLUSTER" in u:
        return "cluster"
    return None
CALLTOOL_RE = re.compile(r"""callTool\(\s*(['"])([^'"]+)\1""")
CONST_TOOL_RE = re.compile(r"""\bconst\s+[A-Z_]*TOOL[A-Z_]*\s*=\s*(['"])([^'"]+)\1""")
SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"][^>]*>", re.IGNORECASE)
LINK_CSS_RE = re.compile(r"<link\b[^>]*rel\s*=\s*['\"]stylesheet['\"][^>]*>", re.IGNORECASE)
CSP_RE = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*['\"]Content-Security-Policy['\"]", re.IGNORECASE)
KEY_NAME = {"subscriptionId": "subscription", "subscription": "subscription", "workspaceId": "workspace",
            "workspace": "workspace", "resource-group": "resourceGroup", "resourceGroup": "resourceGroup",
            "clusterUrl": "cluster", "cluster": "cluster", "database": "database"}

BUNDLE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "sreagent.livereport/v1",
    "title": "Azure SRE Agent Live Report bundle",
    "type": "object",
    "required": ["schemaVersion", "kind", "metadata", "spec"],
    "properties": {
        "schemaVersion": {"const": SCHEMA_VERSION},
        "kind": {"const": "LiveReport"},
        "metadata": {
            "type": "object",
            "required": ["name", "description", "exportedAt"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "sourceReportId": {"type": ["string", "null"]},
                "sourceVersion": {"type": ["integer", "null"]},
                "sourceAgent": {"type": ["string", "null"]},
                "exportedAt": {"type": "string"},
                "exportedBy": {"type": "string"}
            }
        },
        "spec": {
            "type": "object",
            "required": ["allowedTools", "html", "htmlSha256", "htmlBytes"],
            "properties": {
                "allowedTools": {"type": "array", "items": {"type": "string"}},
                "html": {"type": "string", "description": "Saved report source, byte-exact, with raw {REPORT_NONCE} placeholders"},
                "htmlSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "htmlBytes": {"type": "integer"}
            }
        },
        "bindings": {
            "type": "array",
            "items": {"type": "object", "required": ["name", "value", "occurrences"],
                      "properties": {"name": {"type": "string"}, "value": {"type": "string"},
                                     "occurrences": {"type": "integer"}, "detectedFrom": {"type": "string"}}}
        },
        "requirements": {"type": "object"},
        "warnings": {"type": "array", "items": {"type": "string"}}
    }
}


# ----------------------------------------------------------------------------- helpers
def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_html(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8")  # strict: refuse non-UTF-8 rather than silently altering bytes


def _write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:  # binary: no newline translation, byte-exact
        f.write(text.encode("utf-8"))


def _slug(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s[:80] or "report"


def _die(msg, code=2):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


def tools_called(html):
    names = {m.group(2) for m in CALLTOOL_RE.finditer(html)}
    names |= {m.group(2) for m in CONST_TOOL_RE.finditer(html)}
    return sorted(names)


def detect_bindings(html):
    """Find environment-specific literals that a target environment will likely need to change."""
    found = {}  # value -> (name, detectedFrom)
    counters = {}

    def add(value, base, origin):
        if value in found or not value.strip():
            return
        counters[base] = counters.get(base, 0) + 1
        name = base if counters[base] == 1 else "%s_%d" % (base, counters[base])
        found[value] = (name, origin)

    for m in KEY_RE.finditer(html):
        add(m.group(4), KEY_NAME[m.group(2)], "key '%s'" % m.group(2))
    for m in CONST_RE.finditer(html):
        base = _const_binding_name(m.group(1))
        if base:
            add(m.group(3), base, "const %s" % m.group(1))
    for m in RG_PATH_RE.finditer(html):
        add(m.group(1), "resourceGroup", "ARM resource ID path")
    for m in GUID_RE.finditer(html):
        g = m.group(0)
        if not any(g.lower() == v.lower() for v in found):
            add(g, "guid", "GUID literal")
    out = []
    for value, (name, origin) in found.items():
        flags = re.IGNORECASE if GUID_RE.fullmatch(value) else 0
        n = len(re.findall(re.escape(value), html, flags))
        out.append({"name": name, "value": value, "occurrences": n, "detectedFrom": origin})
    return sorted(out, key=lambda b: (-b["occurrences"], b["name"]))


def check_html(html, allowed_tools):
    """Return (errors, warnings) for installability and rendering fidelity."""
    errors, warnings = [], []
    if NONCE not in html:
        errors.append("No {REPORT_NONCE} placeholder found. This looks like a rendered/downloaded copy, "
                      "not the saved report source. Export with GetReport's htmlPath instead.")
    if re.search(r"""nonce\s*=\s*['"](?!\{REPORT_NONCE\})[A-Za-z0-9+/=_-]{8,}['"]""", html):
        errors.append("Concrete nonce values found. Runtime-substituted HTML will not render after install.")
    if not CSP_RE.search(html):
        warnings.append("No Content-Security-Policy meta tag found.")
    called = tools_called(html)
    missing = [t for t in called if t not in allowed_tools]
    if missing:
        errors.append("Tools called by the HTML but not in allowedTools (blocked at view time): " + ", ".join(missing))
    unused = [t for t in allowed_tools if t not in called and t not in html]
    if unused:
        warnings.append("allowedTools entries never referenced in the HTML: " + ", ".join(unused))
    for tag in SCRIPT_SRC_RE.finditer(html):
        if "integrity=" not in tag.group(0):
            warnings.append("CDN script without SRI integrity: " + tag.group(1))
    for tag in LINK_CSS_RE.finditer(html):
        t = tag.group(0)
        if "cdn" in t and "integrity=" not in t:
            warnings.append("CDN stylesheet without SRI integrity: " + t[:120])
    if re.search(r"""\son[a-z]+\s*=\s*["']""", re.sub(r"<script[\s\S]*?</script>", "", html), re.IGNORECASE):
        warnings.append("Inline event handler attributes (onclick=...) are blocked by the nonce CSP and will not fire.")
    return errors, warnings


def build_bundle(html, name, description, allowed_tools, source_id=None, source_version=None, source_agent=None):
    errors, warnings = check_html(html, allowed_tools)
    bindings = detect_bindings(html)
    if bindings:
        warnings.append("Environment-specific values detected (%d). Review with `bindings` and rebind at install "
                        "with --set name=value." % len(bindings))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "LiveReport",
        "metadata": {
            "name": name, "description": description,
            "sourceReportId": source_id, "sourceVersion": source_version, "sourceAgent": source_agent,
            "exportedAt": _now(), "exportedBy": "live_report_bundle.py " + TOOL_VERSION,
        },
        "spec": {
            "allowedTools": list(allowed_tools),
            "html": html,
            "htmlSha256": _sha(html),
            "htmlBytes": len(html.encode("utf-8")),
        },
        "bindings": bindings,
        "requirements": {
            "toolsCalled": tools_called(html),
            "nonceTokens": html.count(NONCE),
            "cdnScripts": [m.group(1) for m in SCRIPT_SRC_RE.finditer(html)],
        },
        "warnings": warnings,
    }, errors


def load_bundle(path):
    with open(path, "rb") as f:
        b = json.loads(f.read().decode("utf-8"))
    return b


def validate_bundle(b):
    """Structural + integrity validation. Returns (errors, warnings)."""
    errors, warnings = [], []
    if b.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("schemaVersion must be %s (got %r)" % (SCHEMA_VERSION, b.get("schemaVersion")))
    if b.get("kind") != "LiveReport":
        errors.append("kind must be LiveReport")
    md, spec = b.get("metadata") or {}, b.get("spec") or {}
    if not md.get("name"):
        errors.append("metadata.name is required")
    for k in ("allowedTools", "html", "htmlSha256", "htmlBytes"):
        if k not in spec:
            errors.append("spec.%s is required" % k)
    if errors:
        return errors, warnings
    if _sha(spec["html"]) != spec["htmlSha256"]:
        errors.append("htmlSha256 mismatch: the HTML was altered after export")
    if len(spec["html"].encode("utf-8")) != spec["htmlBytes"]:
        errors.append("htmlBytes mismatch")
    e2, w2 = check_html(spec["html"], spec["allowedTools"])
    return errors + e2, warnings + w2


def apply_bindings(html, bindings, sets):
    """Replace each bound source value with the new value. Returns (html, report)."""
    by_name = {b["name"]: b for b in bindings}
    report = []
    for name, new in sets.items():
        if name in by_name:
            old = by_name[name]["value"]
        elif name.startswith("literal:"):
            old = name[len("literal:"):]
        else:
            _die("Unknown binding '%s'. Known: %s (or use literal:<old>=<new>)" % (name, ", ".join(by_name) or "none"))
        flags = re.IGNORECASE if GUID_RE.fullmatch(old) else 0
        html, n = re.subn(re.escape(old), lambda _m: new, html, flags=flags)
        report.append({"name": name, "from": old, "to": new, "replaced": n})
    return html, report


def _parse_sets(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            _die("--set expects name=value, got %r" % p)
        k, v = p.split("=", 1)
        out[k.strip()] = v
    return out


# ----------------------------------------------------------------------------- commands
def cmd_export(a):
    items = []
    if a.meta:
        with open(a.meta, "rb") as f:
            meta = json.loads(f.read().decode("utf-8"))
        if isinstance(meta, dict):
            meta = [meta.get("report", meta)]
        for m in meta:
            m = m.get("report", m)
            items.append(dict(html=m["htmlPath"], name=m["name"], description=m.get("description", ""),
                              tools=m.get("allowedTools") or [], id=m.get("id"),
                              version=m.get("currentVersionNumber")))
    elif a.html:
        if not a.name:
            _die("--name is required with --html")
        items.append(dict(html=a.html, name=a.name, description=a.description or "", tools=a.allowed_tool or [],
                          id=a.source_id, version=a.source_version))
    else:
        _die("give --meta FILE or --html FILE")

    base = os.path.dirname(os.path.abspath(a.meta)) if a.meta else os.getcwd()
    os.makedirs(a.out, exist_ok=True)
    failed = 0
    for it in items:
        path = it["html"]
        if not os.path.isabs(path) and not os.path.exists(path):
            cand = [os.path.join(a.html_root or "", path), os.path.join(base, path)]
            path = next((c for c in cand if os.path.exists(c)), path)
        html = _read_html(path)
        bundle, errors = build_bundle(html, it["name"], it["description"], it["tools"], it["id"], it["version"],
                                      a.source_agent)
        out = os.path.join(a.out, _slug(it["name"]) + ".livereport.json")
        status = "OK"
        if errors:
            status = "BLOCKED"
            failed += 1
            for e in errors:
                print("  error: " + e)
            if not a.force:
                print("%-8s %s (not written; use --force to write anyway)" % (status, it["name"]))
                continue
        _write_text(out, json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
        print("%-8s %s -> %s  (%d bytes html, sha256 %s, %d bindings, %d warnings)" % (
            status, it["name"], out, bundle["spec"]["htmlBytes"], bundle["spec"]["htmlSha256"][:12],
            len(bundle["bindings"]), len(bundle["warnings"])))
    return 1 if failed and not a.force else 0


def _expand(paths):
    out = []
    for p in paths:
        out.extend(sorted(glob.glob(p)) or [p])
    return out


def cmd_verify(a):
    bad = 0
    for p in _expand(a.bundles):
        b = load_bundle(p)
        errors, warnings = validate_bundle(b)
        name = (b.get("metadata") or {}).get("name", "?")
        print("%-5s %s  [%s]" % ("FAIL" if errors else "PASS", p, name))
        for e in errors:
            print("   error:   " + e)
        for w in warnings:
            print("   warning: " + w)
        bad += bool(errors)
    return 1 if bad else 0


def cmd_bindings(a):
    for p in _expand(a.bundles):
        b = load_bundle(p)
        print("%s  [%s]" % (p, b["metadata"]["name"]))
        if not b.get("bindings"):
            print("   (none detected)")
        for x in b.get("bindings", []):
            print("   %-16s %-40s x%-3d %s" % (x["name"], x["value"], x["occurrences"], x.get("detectedFrom", "")))
    return 0


def cmd_prepare(a):
    sets = _parse_sets(a.set)
    os.makedirs(a.out_dir, exist_ok=True)
    plan, bad = [], 0
    for p in _expand(a.bundles):
        b = load_bundle(p)
        errors, warnings = validate_bundle(b)
        if errors:
            bad += 1
            print("FAIL %s" % p)
            for e in errors:
                print("   error: " + e)
            continue
        missing_req = [x["name"] for x in b.get("bindings", []) if x.get("required") and x["name"] not in sets]
        if missing_req and not a.allow_placeholders:
            bad += 1
            print("FAIL %s: required values not set: %s (use --set name=value, or --allow-placeholders)" % (
                p, ", ".join("%s (%s)" % (n, next(x["value"] for x in b["bindings"] if x["name"] == n))
                             for n in missing_req)))
            continue
        html, rebinds = apply_bindings(b["spec"]["html"], b.get("bindings", []), sets)
        # re-check after rebinding (a rebind must not break nonce/tool policy)
        e2, _ = check_html(html, b["spec"]["allowedTools"])
        if e2:
            bad += 1
            print("FAIL %s after rebinding: %s" % (p, "; ".join(e2)))
            continue
        base_name = b["metadata"]["name"]
        if a.drop_suffix and base_name.endswith(a.drop_suffix):
            base_name = base_name[: -len(a.drop_suffix)]
        name = (a.name_prefix or "") + base_name + (a.name_suffix or "")
        html_path = os.path.join(a.out_dir, _slug(name) + ".html")
        _write_text(html_path, html)
        unbound = [x["name"] for x in b.get("bindings", []) if x["name"] not in sets]
        entry = {
            "bundle": p,
            "htmlPath": html_path,
            "htmlSha256": _sha(html),
            "name": name,
            "description": b["metadata"].get("description", ""),
            "allowedTools": b["spec"]["allowedTools"],
            "reportId": a.report_id if (a.report_id and len(_expand(a.bundles)) == 1) else None,
            "rebinds": rebinds,
            "unboundValuesKeptFromSource": unbound,
            "sourceReportId": b["metadata"].get("sourceReportId"),
            "sourceVersion": b["metadata"].get("sourceVersion"),
        }
        plan.append(entry)
        print("READY %s -> %s" % (name, html_path))
        for r in rebinds:
            print("   rebind %-14s %s -> %s (%d replaced)" % (r["name"], r["from"], r["to"], r["replaced"]))
        if unbound:
            print("   kept source values for: " + ", ".join(unbound))
    plan_path = os.path.join(a.out_dir, "install-plan.json")
    _write_text(plan_path, json.dumps({"generatedAt": _now(), "reports": plan}, indent=2, ensure_ascii=False) + "\n")
    print("plan: " + plan_path)
    print("Next (in the target SRE Agent): for each plan entry call SaveReport with htmlPath, name, description, "
          "allowedTools (and reportId to add a new version instead of creating a report). Do not edit the HTML.")
    return 1 if bad else 0


# ----------------------------------------------------------------------------- clean (sanitize for source control)
PKG_AT_RE = re.compile(r"^[A-Za-z0-9._-]+@\d")  # chart.js@4.4.7 is not an email


def _placeholder(name):
    base = re.sub(r"_\d+$", "", name)
    special = {"subscription": "SUBSCRIPTION_ID", "workspace": "WORKSPACE", "resourceGroup": "RESOURCE_GROUP",
               "cluster": "CLUSTER", "database": "DATABASE", "guid": "GUID"}
    p = special.get(base, re.sub(r"(?<!^)([A-Z])", r"_\1", base).upper())
    suffix = name[len(base):]
    return "__%s%s__" % (p, suffix.upper())


def _load_map(path):
    """Redaction map: JSON object {original: replacement} or lines 'original => replacement'."""
    if not path:
        return {}
    with open(path, "rb") as f:
        txt = f.read().decode("utf-8")
    if txt.lstrip().startswith("{"):
        return json.loads(txt)
    out = {}
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" not in line:
            _die("map line needs 'original => replacement': %r" % line)
        k, v = line.split("=>", 1)
        out[k.strip()] = v.strip()
    return out


def _bounded(value):
    # whole-token match, so 'SRV1' does not hit inside 'srv10'
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(value) + r"(?![A-Za-z0-9_-])", re.IGNORECASE)


def _js_syntax_errors(html):
    import shutil
    import tempfile
    node = shutil.which("node")
    if not node:
        return None  # not checked
    errs = []
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE)
    for i, js in enumerate(blocks):
        if not js.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as t:
            t.write(js)
            p = t.name
        try:
            r = subprocess.run([node, "--check", p], capture_output=True, text=True)
            if r.returncode != 0:
                errs.append("inline script %d: %s" % (i + 1, (r.stderr or "").strip().splitlines()[-1:]))
        finally:
            os.unlink(p)
    return errs


def clean_bundle(b, user_map, name_suffix, overrides=None):
    html = b["spec"]["html"]
    meta = dict(b["metadata"])
    ov = (overrides or {}).get(meta["name"], {})
    if ov.get("name"):
        meta["name"] = ov["name"]
    if ov.get("description"):
        meta["description"] = ov["description"]
    rules = []  # (original, replacement, kind)
    seen = set()
    placeholders = {}
    for x in b.get("bindings", []):
        if x["value"].lower() in seen:
            continue
        ph = _placeholder(x["name"])
        placeholders[x["name"]] = ph
        rules.append((x["value"], ph, "binding"))
        seen.add(x["value"].lower())
        if GUID_RE.fullmatch(x["value"]):  # also catch display-truncated GUIDs like 1a2b3c4d-...-0123456789ab
            g = x["value"]
            rules.append((g[:8] + "-\u2026-" + g[-12:], ph, "binding"))
            rules.append((g[:8] + "-...-" + g[-12:], ph, "binding"))
    for k, v in user_map.items():
        if k.lower() not in seen:
            rules.append((k, v, "map"))
            seen.add(k.lower())
    rules.sort(key=lambda r: -len(r[0]))  # longest first

    counts = {}

    def apply(text):
        for orig, rep, kind in rules:
            text, n = _bounded(orig).subn(lambda _m: rep, text)
            counts[(orig, kind)] = counts.get((orig, kind), 0) + n
        return text

    new_html = apply(html)
    new_name = apply(meta["name"]) + (name_suffix or "")
    new_desc = apply(meta.get("description", ""))

    # leak scan: originals (any case, as substrings) + GUID fragments + stray GUIDs/emails
    deny = {orig for orig, _r, _k in rules}
    for x in b.get("bindings", []):
        if GUID_RE.fullmatch(x["value"]):
            deny |= {x["value"][:8], x["value"][-12:]}
    leaks = []
    blob = "\n".join([new_html, new_name, new_desc])
    for d in sorted(deny, key=len, reverse=True):
        for m in re.finditer(re.escape(d), blob, re.IGNORECASE):
            leaks.append("'%s' near: %s" % (d, blob[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")))
            break
    for g in set(GUID_RE.findall(blob)):
        leaks.append("unmapped GUID: " + g)
    for e in set(re.findall(r"[\w.+-]+@[\w-]+\.[A-Za-z][\w.-]*", blob)):
        if not PKG_AT_RE.match(e):
            leaks.append("email-like value: " + e)

    js_errs = _js_syntax_errors(new_html)
    allowed = b["spec"]["allowedTools"]
    errors, warnings = check_html(new_html, allowed)
    if js_errs:
        errors += ["JavaScript syntax error after cleaning: " + e for e in js_errs]
    ph_bindings = []
    for name, ph in placeholders.items():
        n = new_html.count(ph)
        if n:
            ph_bindings.append({"name": name, "value": ph, "occurrences": n, "required": True,
                                "detectedFrom": "clean placeholder"})
    cleaned = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "LiveReport",
        "metadata": {
            "name": new_name, "description": new_desc,
            "sourceReportId": None, "sourceVersion": None, "sourceAgent": None,
            "exportedAt": _now(), "exportedBy": "live_report_bundle.py %s (clean)" % TOOL_VERSION,
            "cleaned": {
                "placeholders": sorted(placeholders.values()),
                "pseudonymizedValues": sum(1 for (o, k), n in counts.items() if k == "map" and n),
                "replacements": sum(counts.values()),
                "leakScan": "pass" if not leaks else "FAIL",
                "jsSyntaxCheck": "skipped (node not found)" if js_errs is None else ("pass" if not js_errs else "FAIL"),
            },
        },
        "spec": {"allowedTools": list(allowed), "html": new_html, "htmlSha256": _sha(new_html),
                 "htmlBytes": len(new_html.encode("utf-8"))},
        "bindings": ph_bindings,
        "requirements": {"toolsCalled": tools_called(new_html), "nonceTokens": new_html.count(NONCE),
                         "cdnScripts": [m.group(1) for m in SCRIPT_SRC_RE.finditer(new_html)],
                         "mustSet": [x["name"] for x in ph_bindings]},
        "warnings": warnings + ["Embedded demo data is pseudonymized; it describes no real environment."],
    }
    return cleaned, errors, leaks, counts


def cmd_clean(a):
    user_map = _load_map(a.map)
    overrides = {}
    if a.overrides:
        with open(a.overrides, "rb") as f:
            overrides = json.loads(f.read().decode("utf-8"))
    os.makedirs(a.out, exist_ok=True)
    bad = 0
    for p in _expand(a.bundles):
        b = load_bundle(p)
        errs0, _ = validate_bundle(b)
        if errs0:
            bad += 1
            print("FAIL %s (source bundle invalid): %s" % (p, "; ".join(errs0)))
            continue
        cleaned, errors, leaks, counts = clean_bundle(b, user_map, a.name_suffix, overrides)
        out = os.path.join(a.out, _slug(cleaned["metadata"]["name"]) + ".livereport.json")
        ok = not errors and not leaks
        print("%s %s -> %s" % ("CLEAN " if ok else "FAIL  ", cleaned["metadata"]["name"], out))
        print("   placeholders: %s | pseudonymized values: %d | replacements: %d | js: %s" % (
            ", ".join(cleaned["metadata"]["cleaned"]["placeholders"]) or "none",
            cleaned["metadata"]["cleaned"]["pseudonymizedValues"], cleaned["metadata"]["cleaned"]["replacements"],
            cleaned["metadata"]["cleaned"]["jsSyntaxCheck"]))
        for e in errors:
            print("   error: " + e)
        for l in leaks:
            print("   leak:  " + l)
        if ok or a.force:
            _write_text(out, json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n")
        if not ok:
            bad += 1
    return 1 if bad else 0


# ----------------------------------------------------------------------------- remote install
def _az(args):
    exe = "az.cmd" if os.name == "nt" else "az"
    r = subprocess.run([exe] + args, capture_output=True, text=True)
    if r.returncode != 0:
        _die("az %s failed: %s" % (" ".join(args[:3]), r.stderr.strip()[:400]))
    return r.stdout.strip()


def _http(method, url, token, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            txt = resp.read().decode("utf-8")
            return resp.status, (json.loads(txt) if txt.strip() else {}), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:600]}, dict(e.headers or {})


def _install_prompt(a, sets):
    script = a.script_path or (a.repo_path.rstrip("/") + "/live_report_bundle.py")
    bundles = " ".join(a.repo_path.rstrip("/") + "/" + x for x in a.bundle)
    set_args = " ".join("--set %s=%s" % (k, v) for k, v in sets.items())
    rid = (" --report-id " + a.report_id) if a.report_id else ""
    suffix = (' --name-suffix "%s"' % a.name_suffix) if a.name_suffix else ""
    if a.name_prefix:
        suffix += ' --name-prefix "%s"' % a.name_prefix
    if a.drop_suffix:
        suffix += ' --drop-suffix "%s"' % a.drop_suffix
    return (
        "Install Live Report bundle(s) exactly as exported. Do not redesign or edit the HTML.\n"
        "1. Run: python3 %s prepare %s --out-dir tmp/livereport-install %s%s%s\n"
        "   If it reports FAIL, stop and reply with the error output.\n"
        "2. For each entry in tmp/livereport-install/install-plan.json call SaveReport with htmlPath, name, "
        "description and allowedTools exactly as listed (pass reportId only if it is not null). "
        "Use the file as-is; do not regenerate it.\n"
        "3. Do not run the tools to probe; the HTML was already validated at export.\n"
        "4. Reply with one line per report: INSTALLED <name> | <reportId> | v<version>, or FAILED <name> | <reason>."
    ) % (script, bundles, set_args, rid, suffix)


def cmd_install(a):
    sets = _parse_sets(a.set)
    prompt = _install_prompt(a, sets)
    if a.dry_run:
        print("DRY RUN - no calls made.\n")
        print("1. GET  https://management.azure.com%s?api-version=%s  -> properties.agentEndpoint" % (a.agent_id, ARM_API))
        print("2. token: az account get-access-token --resource %s" % DATA_PLANE_AUDIENCE)
        print("3. POST {agentEndpoint}/api/v1/threads  body.startMessage.text =\n")
        print(prompt)
        print("\n4. poll GET {agentEndpoint}/api/v1/threads/{id}/messages until state in Completed/Idle/PendingApproval/Failed")
        print("5. GET  {agentEndpoint}/api/v1/approvals/{id}  -> approve the tool-access card in the portal")
        return 0

    endpoint = a.endpoint or _az(["rest", "-m", "GET", "--url",
                                  "https://management.azure.com%s?api-version=%s" % (a.agent_id, ARM_API),
                                  "--query", "properties.agentEndpoint", "-o", "tsv"])
    endpoint = endpoint.rstrip("/")
    token = _az(["account", "get-access-token", "--resource", DATA_PLANE_AUDIENCE, "--query", "accessToken", "-o", "tsv"])

    status, body, headers = _http("POST", endpoint + "/api/v1/threads", token, {"startMessage": {"text": prompt}})
    if status == 401:
        _die("401 from data plane: token audience must be %s and you need thread-write access on the agent" % DATA_PLANE_AUDIENCE)
    if status not in (200, 201):
        _die("create thread failed: HTTP %s %s" % (status, body))
    thread_id = body.get("id") or (headers.get("Location", "").rstrip("/").split("/")[-1])
    print("thread: %s" % thread_id)

    deadline = time.time() + a.timeout
    state, msgs = None, {}
    while time.time() < deadline:
        time.sleep(10)
        st, msgs, _ = _http("GET", "%s/api/v1/threads/%s/messages" % (endpoint, thread_id), token)
        if st == 200:
            state = msgs.get("state") if isinstance(msgs, dict) else None
            print("  state: %s" % state)
            if state in ("Completed", "Idle", "PendingApproval", "Failed", "Waiting"):
                break
    if state == "PendingApproval":
        _, appr, _ = _http("GET", "%s/api/v1/approvals/%s" % (endpoint, thread_id), token)
        print("Pending approvals (approve the tool-access card in the SRE Agent portal, thread %s):" % thread_id)
        print(json.dumps(appr, indent=2)[:2000])
    # print the agent's last reply text, whatever the message shape
    texts = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("text", "content") and isinstance(v, str):
                    texts.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(msgs)
    lines = [l for t in texts for l in t.splitlines() if l.startswith(("INSTALLED", "FAILED"))]
    print("\n".join(lines) if lines else ("last reply: " + (texts[-1][:1500] if texts else "(none)")))
    print("final state: %s" % state)
    return 0 if state in ("Completed", "Idle", "PendingApproval", "Waiting") else 1


def cmd_schema(_a):
    print(json.dumps(BUNDLE_SCHEMA, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export/verify/install Azure SRE Agent Live Report definitions.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)

    e = sp.add_parser("export", help="build bundles from checked-out report HTML")
    e.add_argument("--meta", help="JSON from GetReport/ListReports: object or array with id,name,description,"
                                  "currentVersionNumber,allowedTools,htmlPath")
    e.add_argument("--html-root", help="directory that htmlPath values are relative to (default: cwd, then meta dir)")
    e.add_argument("--html", help="single report HTML (saved source)")
    e.add_argument("--name")
    e.add_argument("--description")
    e.add_argument("--allowed-tool", action="append")
    e.add_argument("--source-id")
    e.add_argument("--source-version", type=int)
    e.add_argument("--source-agent", help="source agent ARM resource ID (recorded in metadata)")
    e.add_argument("--out", required=True)
    e.add_argument("--force", action="store_true", help="write bundles even if blocking errors are found")
    e.set_defaults(fn=cmd_export)

    v = sp.add_parser("verify", help="validate bundles")
    v.add_argument("bundles", nargs="+")
    v.set_defaults(fn=cmd_verify)

    b = sp.add_parser("bindings", help="list environment-specific values in bundles")
    b.add_argument("bundles", nargs="+")
    b.set_defaults(fn=cmd_bindings)

    p = sp.add_parser("prepare", help="verify, rebind and write ready-to-save HTML + install-plan.json")
    p.add_argument("bundles", nargs="+")
    p.add_argument("--set", action="append", help="name=value rebind (see `bindings`), or literal:<old>=<new>")
    p.add_argument("--out-dir", default="tmp/livereport-install")
    p.add_argument("--drop-suffix", help="remove this text from the end of report names, e.g. ' - Cleaned'")
    p.add_argument("--name-prefix", help="prepend to report names, e.g. 'Test - '")
    p.add_argument("--name-suffix", help="append to report names, e.g. ' (Contoso)'")
    p.add_argument("--report-id", help="existing report ID to update (single bundle only)")
    p.add_argument("--allow-placeholders", action="store_true",
                   help="install even if required __PLACEHOLDER__ values were not set (live calls will fail)")
    p.set_defaults(fn=cmd_prepare)

    c = sp.add_parser("clean", help="sanitize bundles for source control: placeholders + pseudonyms + leak scan")
    c.add_argument("bundles", nargs="+")
    c.add_argument("--map", help="redaction map (JSON object or 'original => replacement' lines). "
                                 "Keep this file OUT of source control: it lists the real values.")
    c.add_argument("--overrides", help="JSON {\"<source report name>\": {\"name\": ..., \"description\": ...}} to rename "
                                       "or re-describe reports. Keep it local: keys are the original names.")
    c.add_argument("--name-suffix", default=" - Cleaned", help="appended to report names (default ' - Cleaned')")
    c.add_argument("--out", required=True)
    c.add_argument("--force", action="store_true", help="write even if the leak scan fails")
    c.set_defaults(fn=cmd_clean)

    i = sp.add_parser("install", help="ask a target agent (via data-plane API) to install bundles from a connected repo")
    i.add_argument("--agent-id", required=True, help="target agent ARM resource ID")
    i.add_argument("--endpoint", help="agent data-plane endpoint (skips the ARM lookup)")
    i.add_argument("--repo-path", required=True, help="bundle folder as seen by the target agent, e.g. codeRefs/<repo>/live-reports")
    i.add_argument("--script-path", help="path of this script as seen by the agent (default: <repo-path>/live_report_bundle.py)")
    i.add_argument("--bundle", action="append", required=True, help="bundle file name inside --repo-path (repeatable)")
    i.add_argument("--set", action="append")
    i.add_argument("--drop-suffix")
    i.add_argument("--name-prefix")
    i.add_argument("--name-suffix")
    i.add_argument("--report-id")
    i.add_argument("--timeout", type=int, default=900)
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(fn=cmd_install)

    s = sp.add_parser("schema", help="print bundle JSON Schema")
    s.set_defaults(fn=cmd_schema)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

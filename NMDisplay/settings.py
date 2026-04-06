import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS = {
    "my_node_id": None,

    "telemetry_lists": {
        "enabled_lists": [
            "last_heard",
            "nearby",
            "big_sig",
            "most_pkts"
        ],
        "limits": {
            "last_heard": 5,
            "nearby":     5,
            "big_sig":    5,
            "most_pkts":  5,
            "msgs_sent":  5,
            "msgs_rcvd":  5,
            "dead_end":   5
        }
    },

    "timeline": {
        "time_scale_hours": 6,
        "y_axis_mode": "auto",
        "y_axis_max": 30,
        "message_accent": True
    },

    "map": {
        "zoom": 11,
        "center_mode": "activity_centroid",
        "node_labels": "short_name"
    },

    "display": {
        "theme": "color",
        "poll_interval_seconds": 10
    }
}

VALID_LISTS = {"last_heard", "nearby", "big_sig", "most_pkts", "msgs_sent", "msgs_rcvd", "dead_end"}


def load() -> dict:
    """Load config.json, filling in any missing keys from DEFAULTS."""
    if not os.path.exists(CONFIG_PATH):
        return _deep_copy(DEFAULTS)
    with open(CONFIG_PATH, "r") as f:
        user = json.load(f)
    return _deep_merge(DEFAULTS, user)


def save(config: dict) -> None:
    """Write config to config.json atomically."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def validate_and_merge(current: dict, patch: dict) -> tuple[dict, list[str]]:
    """
    Merge patch into current config, validate constraints.
    Returns (merged_config, list_of_errors).
    """
    merged = _deep_merge(current, patch)
    errors = []

    tl = merged.get("timeline", {})
    if tl.get("time_scale_hours") not in (1, 6, 24):
        errors.append("timeline.time_scale_hours must be 1, 6, or 24")
    if tl.get("y_axis_mode") not in ("auto", "fixed"):
        errors.append("timeline.y_axis_mode must be 'auto' or 'fixed'")

    limits = merged.get("telemetry_lists", {}).get("limits", {})
    for key, val in limits.items():
        if not isinstance(val, int) or not (1 <= val <= 5):
            errors.append(f"telemetry_lists.limits.{key} must be integer 1–5")

    enabled = merged.get("telemetry_lists", {}).get("enabled_lists", [])
    for item in enabled:
        if item not in VALID_LISTS:
            errors.append(f"Unknown list id: {item}")

    m = merged.get("map", {})
    if not isinstance(m.get("zoom"), int) or not (0 <= m["zoom"] <= 18):
        errors.append("map.zoom must be integer 0–18")
    if m.get("center_mode") not in ("my_node", "activity_centroid"):
        errors.append("map.center_mode must be 'my_node' or 'activity_centroid'")
    if m.get("node_labels") not in ("short_name", "dots_only"):
        errors.append("map.node_labels must be 'short_name' or 'dots_only'")

    d = merged.get("display", {})
    if d.get("theme") not in ("color", "eink"):
        errors.append("display.theme must be 'color' or 'eink'")
    poll = d.get("poll_interval_seconds")
    if not isinstance(poll, int) or not (5 <= poll <= 300):
        errors.append("display.poll_interval_seconds must be integer 5–300")

    return merged, errors


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _deep_copy(d):
    return json.loads(json.dumps(d))

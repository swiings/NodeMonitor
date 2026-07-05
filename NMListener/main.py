"""
main.py — Entry point for the Meshtastic Node Monitor data collector.

Connection type (TCP / Serial / BLE) and DB path come from NMData/active.json,
written by NMDisplay's session manager. Falls back to hardcoded TCP defaults
when active.json is absent (backwards-compatible standalone operation).

Usage:
    python main.py
"""

import logging
import os
import sqlite3
import time

import config
import listener
from connection import make_interface, summary as conn_summary, FatalConnectionError
from db.repository import Repository
from db.schema import create_tables
from store.counters import MessageCounter
from store.node_store import NodeStore
from store.timeline import PacketTimeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("main")


def _init_db() -> Repository:
    db_path = os.path.abspath(config.DB_PATH)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    create_tables(conn)
    conn.close()
    log.info("Database ready: %s", db_path)
    return Repository(db_path)


def _log_device_config(iface) -> None:
    """Log Meshtastic device configuration at startup for debugging."""
    import json as json_module
    try:
        myinfo = iface.myInfo
        
        # Build a summary dict
        config_summary = {
            "my_node_num": myinfo.my_node_num if myinfo else None,
            "hw_model": myinfo.hw_model if myinfo else None,
            "firmware_version": myinfo.firmware_version if myinfo else None,
            "device_state_version": myinfo.device_state_version if myinfo else None,
        }
        
        # Try to get radio config via getConfig() method (works across interface types)
        try:
            radio_config = iface.getConfig("lora")
            if radio_config:
                config_summary["lora_region"] = radio_config.get("region")
                config_summary["lora_modem_preset"] = radio_config.get("modemPreset")
        except Exception:
            pass
        
        # Try to get telemetry config
        try:
            tel_config = iface.getConfig("telemetry")
            if tel_config:
                config_summary["telemetry_config"] = tel_config
        except Exception:
            pass
        
        # Convert to JSON and log each line
        config_json = json_module.dumps(config_summary, indent=2)
        for line in config_json.split('\n'):
            if line.strip():
                log.info("DEVICE_CONFIG: %s", line)
    except Exception as e:
        log.warning("Could not log device config: %s", e)


def main() -> None:
    repo        = _init_db()
    node_store  = NodeStore()
    timeline    = PacketTimeline()
    msg_counter = MessageCounter()

    delay = config.RECONNECT_DELAY_INITIAL
    iface = None

    print(f"\nMeshtastic Node Monitor — NMListener")
    print(f"Connection : {conn_summary(config.CONNECTION)}")
    print(f"Database   : {os.path.abspath(config.DB_PATH)}")
    print(f"Seed nodes : {'yes' if config.SEED_ON_CONNECT else 'no'}\n")

    while True:
        try:
            iface = make_interface(config.CONNECTION)
            _log_device_config(iface)
            listener.register(
                iface, repo, node_store, timeline, msg_counter,
                seed_on_connect=config.SEED_ON_CONNECT,
            )
            delay = config.RECONNECT_DELAY_INITIAL
            log.info("Listening — %d node(s) known so far", node_store.node_count())

            while not listener.connection_lost_event.wait(timeout=1):
                pass

        except KeyboardInterrupt:
            print("\nShutting down.")
            break

        except FatalConnectionError as exc:
            log.error("Fatal: %s", exc)
            log.error("Fix the session config in NMDisplay and restart the listener.")
            break

        except Exception as exc:
            log.error("Connection error: %s", exc)

        finally:
            listener.unregister()
            if iface is not None:
                try:
                    iface.close()
                except Exception:
                    pass
                iface = None

        log.info("Reconnecting in %ds ...", delay)
        time.sleep(delay)
        delay = min(delay * 2, config.RECONNECT_DELAY_MAX)

    repo.close()


if __name__ == "__main__":
    main()

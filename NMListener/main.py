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
        
        # Log raw myInfo object using protobuf's MessageToDict
        if myinfo:
            log.info("DEVICE_CONFIG: === myInfo ===")
            try:
                from google.protobuf.json_format import MessageToDict
                myinfo_dict = MessageToDict(myinfo, preserving_proto_field_name=True)
                myinfo_json = json_module.dumps(myinfo_dict, indent=2, default=str)
                for line in myinfo_json.split('\n'):
                    if line.strip():
                        log.info("DEVICE_CONFIG: %s", line)
            except Exception as e:
                # Fallback: just iterate over the message fields
                log.info("DEVICE_CONFIG: myInfo (fields):")
                try:
                    for field, value in myinfo.ListFields():
                        log.info("DEVICE_CONFIG:   %s: %s", field.name, value)
                except Exception as e2:
                    log.info("DEVICE_CONFIG: Could not list fields: %s", e2)
        
        # Try to get and log all config sections
        config_sections = ["lora", "telemetry", "device", "position", "power", "network", "display"]
        for section in config_sections:
            try:
                config_data = iface.getConfig(section)
                if config_data:
                    log.info("DEVICE_CONFIG: === %s ===", section)
                    try:
                        from google.protobuf.json_format import MessageToDict
                        config_dict = MessageToDict(config_data, preserving_proto_field_name=True)
                        config_json = json_module.dumps(config_dict, indent=2, default=str)
                    except:
                        config_json = json_module.dumps(config_data, indent=2, default=str)
                    for line in config_json.split('\n'):
                        if line.strip():
                            log.info("DEVICE_CONFIG: %s", line)
            except Exception as e:
                pass  # Section may not exist
        
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

import sqlite3


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA foreign_keys=ON;

        -- One row per mesh node; upserted on NODEINFO or POSITION packets
        CREATE TABLE IF NOT EXISTS nodes (
            node_id             INTEGER PRIMARY KEY,
            short_name          TEXT,
            long_name           TEXT,
            hardware_model      TEXT,
            first_seen          INTEGER NOT NULL,
            last_heard          INTEGER NOT NULL,
            device_last_heard   INTEGER,   -- lastHeard from the Meshtastic node's own DB (may be days old)
            seeded              INTEGER NOT NULL DEFAULT 0,  -- 1 = present in node list at connect time
            lat                 REAL,
            lon                 REAL,
            altitude_m          REAL,
            source              TEXT,
            sats                INTEGER,
            rssi                INTEGER,
            snr                 REAL,
            battery_level       INTEGER,
            voltage             REAL,
            hop_limit           INTEGER,
            packet_source       TEXT,
            role                TEXT,
            street              TEXT,
            city                TEXT,
            state               TEXT,
            last_motion_ts      INTEGER,
            motion_eval_ts      INTEGER,
            motion_score        REAL
        );

        CREATE INDEX IF NOT EXISTS idx_nodes_last_heard ON nodes(last_heard);

        -- Every received packet, all portnums
        CREATE TABLE IF NOT EXISTS packets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rx_time         INTEGER NOT NULL,
            from_node_id    INTEGER NOT NULL,
            to_node_id      INTEGER,
            portnum         TEXT NOT NULL,
            portnum_int     INTEGER,
            hop_limit       INTEGER,
            hop_start       INTEGER,
            rx_rssi         INTEGER,
            rx_snr          REAL,
            rx_lat          REAL,
            rx_lon          REAL,
            payload_raw     BLOB,
            payload_json    TEXT,
            FOREIGN KEY (from_node_id) REFERENCES nodes(node_id)
        );

        CREATE INDEX IF NOT EXISTS idx_packets_rx_time      ON packets(rx_time);
        CREATE INDEX IF NOT EXISTS idx_packets_from_node_id ON packets(from_node_id);
        CREATE INDEX IF NOT EXISTS idx_packets_portnum      ON packets(portnum);

        -- Text messages extracted from TEXT_MESSAGE_APP packets
        CREATE TABLE IF NOT EXISTS text_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id       INTEGER NOT NULL,
            rx_time         INTEGER NOT NULL,
            from_node_id    INTEGER NOT NULL,
            to_node_id      INTEGER,
            message_text    TEXT NOT NULL,
            FOREIGN KEY (packet_id) REFERENCES packets(id)
        );

        CREATE INDEX IF NOT EXISTS idx_text_messages_rx_time      ON text_messages(rx_time);
        CREATE INDEX IF NOT EXISTS idx_text_messages_from_node_id ON text_messages(from_node_id);

        -- Device and environment sensor data from TELEMETRY_APP packets
        CREATE TABLE IF NOT EXISTS telemetry (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id           INTEGER NOT NULL,
            rx_time             INTEGER NOT NULL,
            node_id             INTEGER NOT NULL,
            battery_level       INTEGER,
            voltage             REAL,
            channel_util        REAL,
            air_util_tx         REAL,
            temperature         REAL,
            relative_humidity   REAL,
            barometric_pressure REAL,
            gas_resistance      REAL,   -- BME680 VOC gas resistance (raw, from environmentMetrics)
            FOREIGN KEY (packet_id) REFERENCES packets(id),
            FOREIGN KEY (node_id)   REFERENCES nodes(node_id)
        );

        CREATE INDEX IF NOT EXISTS idx_telemetry_rx_time ON telemetry(rx_time);
        CREATE INDEX IF NOT EXISTS idx_telemetry_node_id ON telemetry(node_id);

        -- Every GPS fix from POSITION_APP, preserves full history
        CREATE TABLE IF NOT EXISTS positions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id       INTEGER NOT NULL,
            rx_time         INTEGER NOT NULL,
            node_id         INTEGER NOT NULL,
            lat             REAL NOT NULL,
            lon             REAL NOT NULL,
            altitude_m      REAL,
            source          TEXT,
            sats            INTEGER,
            precision_bits  INTEGER,
            FOREIGN KEY (packet_id) REFERENCES packets(id),
            FOREIGN KEY (node_id)   REFERENCES nodes(node_id)
        );

        CREATE INDEX IF NOT EXISTS idx_positions_node_id ON positions(node_id);
        CREATE INDEX IF NOT EXISTS idx_positions_rx_time ON positions(rx_time);

        -- Saved map tracks (derived from node position history)
        CREATE TABLE IF NOT EXISTS tracks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id         INTEGER NOT NULL,
            created_ts      INTEGER NOT NULL,
            start_ts        INTEGER,
            end_ts          INTEGER,
            point_count     INTEGER NOT NULL DEFAULT 0,
            color           TEXT,
            is_selected     INTEGER NOT NULL DEFAULT 1,
            path_json       TEXT NOT NULL,
            snapped_path_json TEXT,
            snap_provider   TEXT,
            FOREIGN KEY (node_id) REFERENCES nodes(node_id)
        );

        CREATE INDEX IF NOT EXISTS idx_tracks_created_ts ON tracks(created_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_tracks_node_id ON tracks(node_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_selected ON tracks(is_selected);

        -- Pre-aggregated per-minute packet counts for histogram display
        CREATE TABLE IF NOT EXISTS packet_timeline (
            minute_ts   INTEGER NOT NULL,
            portnum     TEXT NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (minute_ts, portnum)
        );

        CREATE INDEX IF NOT EXISTS idx_packet_timeline_minute ON packet_timeline(minute_ts);

        -- Cumulative per-node message send/receive counts
        CREATE TABLE IF NOT EXISTS message_counts (
            node_id         INTEGER PRIMARY KEY,
            sent_count      INTEGER NOT NULL DEFAULT 0,
            received_count  INTEGER NOT NULL DEFAULT 0,
            last_updated    INTEGER NOT NULL,
            FOREIGN KEY (node_id) REFERENCES nodes(node_id)
        );
    """)
    conn.commit()
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after initial schema creation."""
    # Backward-compatible schema migration for existing session DBs.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(packets)").fetchall()}
    if "hop_start" not in cols:
        conn.execute("ALTER TABLE packets ADD COLUMN hop_start INTEGER")
        conn.commit()

    new_columns = [
        ("nodes", "seeded",            "INTEGER NOT NULL DEFAULT 0"),
        ("nodes", "device_last_heard", "INTEGER"),
        ("nodes", "role",              "TEXT"),
        ("nodes", "street",            "TEXT"),
        ("nodes", "city",              "TEXT"),
        ("nodes", "state",             "TEXT"),
        ("nodes", "source",            "TEXT"),
        ("nodes", "sats",              "INTEGER"),
        ("nodes", "packet_source",     "TEXT"),
        ("nodes", "last_motion_ts",   "INTEGER"),
        ("nodes", "motion_eval_ts",   "INTEGER"),
        ("nodes", "motion_score",     "REAL"),
        ("telemetry", "gas_resistance", "REAL"),
        ("positions", "source",        "TEXT"),
        ("positions", "sats",          "INTEGER"),
        ("tracks", "snapped_path_json", "TEXT"),
        ("tracks", "snap_provider",     "TEXT"),
    ]
    for table, col, definition in new_columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

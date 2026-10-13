NodeMonitor README (work in progress)

Main NodeMonitor Page 
<img width="1177" height="989" alt="Screenshot 2026-08-12 at 6 43 57 PM" src="https://github.com/user-attachments/assets/ed1eb064-d431-441b-84ad-a1145aa0383d" />
Full Screen Map with Convex Hull and rare Airplane node in view
<img width="1182" height="665" alt="B25B3245-73D5-42CF-982A-61D68501510D_1_105_c" src="https://github.com/user-attachments/assets/1b32bbc2-6c34-4330-a5b4-3f481a6ff07e" />

Traffic Detail card displayed 
<img width="1078" height="728" alt="C816E316-D688-4AD0-A823-0C3123CE747F_1_105_c" src="https://github.com/user-attachments/assets/581332de-718b-40d0-84a4-e77ddb5de555" />

<img width="570" height="1650" alt="image" src="https://github.com/user-attachments/assets/d820a9e7-4602-4bb0-a0ed-31e696f7eef6" />


<img width="406" height="1734" alt="image" src="https://github.com/user-attachments/assets/e54127eb-5052-4993-89f6-73df056a285a" />


* How to 
    * Set Up
        * download
           * Easiest way is do a Git Pull from your Projects directory, Git will create a subdirectory for NodeMonitor    
        * install
           * There's a script in the scripts folder for building this on a Raspberry Pi. It will fetch any missing libraries.
           * NodeMonitor needs to connect to the internet for map tiles and address geocoding 
        * run
           * Start the listener with "sudo systemctl restart nmdisplay.service".
           * A startup message will give you a link for running NodeMonitor on your device. 
    * Usage
       *  
       * Selecting a reference node
       * Starting a new session
       * Selecting an existing session


NodeMonitor Technical Details
* Code
    * Python
    * Javascript
    * HTML/CSS
* Components
    * NMData
        * Storage location for Session databases and runtime data
    * NMListener
        * This process connects to the Meshtastic reference node
        * Subscribes to the mesh stream 
        * Parses and records all the live packet and node data to the database
    * NMDisplay
        * Hosts a single web page on an http server for NodeMonitor
        * Provides an API for populating the NodeMonitor data elements
        * Offers an Index.html page as entry to NodeMonitor
        * Permits users to refresh the monitor page on a timer, typically 30 seconds


NodeMonitor Main Screen
* Settings page
    * NodeMonitor Session management
    * Choosing a node to monitor
    * Other config settings
* Title bar
    * Shows the current Page Refresh Rate from settings
    * Last Updated is when the screen was last refreshed
    * If the Meshtastic node has environmental telemetry, that will be displayed here
    * 🔔This slider is for setting the volume on the received packet alert
    * Log
        * The log shows application messaging in a panel just below the Packet Activity panel. 
        * You’ll see an entry for each packet received and various messages related to activity
    * CSV
        * Clicking the CSV button will present the CSV menu
            * You’ll see a list of session databases
            * You can click on Nodes or Packets to get a CSV file of that type of data
    * 
* Top row Dashboard 
    * NODES
        * Dir - Direct connections requiring no hops
        * Act - Active, received a packet in the last hour
        * Tot - All nodes heard
    * PKT/MIN
        * Packets in the last minute
    * PKT/HOUR
        * Packets in the last hour
    * SIGNAL
        * SNR
            * Signal to Noise ratio as reported
        * RSSI
            * Signal Strength as reported
        * NOISE
            * Noise floor level as reported
    * SESSION PKTS
        * Number of packets recorded in this Session
    * ELAPSED
        * How long this Session has been active
    * ROLES
        * Breakdown of 📱Client vs 📡 Repeater-type roles.
    * CLOSEST
        * The node with the closest reported location. Requires the reference node to have GPS
    * FARTHEST
        * The node with the farthest reported location. Requires the reference node have GPS
* Map Panel
    * Title bar
        * To the right of the HOPS slider is the Reference node’s details
            * Node Name
            * Channel (or Slot) Number
            * Frequency
    * Node Markers
        * 📱 Phone - role = CLIENT
        * 📡 Sat dish - role = any repeater-type role
        * 🐾 Paw prints - role = Tracker
        * 🏡 House/Tree - role = CLIENT_BASE
        * 😶 Smiley Face (no mouth) - role = CLIENT_MUTE
        * 🚙 Car - Any node with multiple different positions in the last hour
        * ✈️ Airplane - Any node with altitude over 20k feet
        * Top Left Menus
        * +/- Zoom in and out on the map
        * ⦿ Fly to reference node marker node marker
        * [ ] Show map full screen
        * ⚀ Show all node markers
        * ⭔ Toggle convex hull (shows the square mile coverage of the reference node)
            * A line will be drawn on the map surrounding the nodes
            * The total square miles will be displayed in the bottom left corner of the map
        * 📍Show fly out menu for filtering Position Sources
            * HW GPS - Actual hardware GPS inside the radio
            * Phone/Host - GPS location comes from a node with a connected phone 
            * Manual - Mesh user entered the location manually
            * UnKnown - Meshtastic reports the location source as Unknown
    * Top Right Menus
        * Map Layers
            * Several different map styles are available. Some require an API key before they will actually show
        * TRK
            * Hide Tracks
                * After showing one or more sets of tracks, this is how to turn them off
            * Show All Tracks
                * If your session has one or more cars, then this will expose all the tracks at once
            * Show Selected
                * If you have a car selected, it will show the track labels just for that node
            * Hide Labels
                * Turns off track labels
        * 
    * Hops Slider
        * Use to filter the Nodes shown on the map by the number of hops they are away 
    * Age Slider
        * Use to filter the Nodes by the age of the last packet heard from them
    * Cluster Slider
        * Use to cluster Nodes together (and put a node count on the round marker it creates) 
    * Map Functions
        * Hover over a Node marker to see it’s Node Name
        * Click the Node marker to see the Node Info card details
        * Detail data
            * ID
                * Meshtastic default node name
            * Short
                * User-given Short Name*
            * Long
                * User-given Long Name*
            * HW
                * Meshtatsic node hardware device name*
            * Role
                * Role of the Meshtastic user*
            * Packet Source
                * Over The Air or MQTT
            * Hops Away
                * Calculated number of hops the last packet for this node took
            * Distance
                * How far from the reference node‡
            * RSSI
                * Signal strength reported by Meshtastic
            * SNR
                * Signal to Noise ration reported by Meshtastic
            * Battery
                * Battery %
            * Position
                * GPS location‡
            * Location Source
                * Where the GPS location came from‡
            * Sats
                * Number of satellites the hardware GPS has‡
            * Precision
                * Precision of position‡
            * Altitude
                * Height‡
            * Packets
                * Total number of packets from this node
            * Last Heard
                * Timestamp of last packet heard
            * First Heard
                * Timestamp of first packet heard
            * Street
                * Street address as obtained by GeoLoc‡
            * City, State
                * Other address info from GeoLoc‡
            * ‡ These items are only populated if we have received a Position packet from this node
            * * These items are only populated if we have received a NodeInfo packet from this node
        * Buttons
            * See Traffic
                * Shows a popup of all the packet traffic for this node
            * Show Track
                * Shows a line superimposed over the map of the Nodes movements
                * Only shown if there is a Track stored for this user 
            * DELETE Node
                * Removes the Node from the Session database
* Packet Activity Panel
    * Packet Activity
        * Shows the number of packets received and mesh utilization for each minute
        * Vertical bars colored to match the Packet Breakdown packet types
        * Hover over the bar to see the detailed counts for that minute
        * Utilization shown as a yellow line across the bars
        * Slider on the left changes the duration of the gauge
* Telemetry Lists
    * Usage
        * Turn the Charts on and off in Settings
        * Scroll inside the list to see more entries
        * Configure how many entries to load in Settings
        * Resize a list with the bottom handle
        * All the telemetry lists can be moved to a different order by clicking and dragging in the title bar
        * Click on a list header to reverse the list sorting
    * Lists
        * Last Heard
            * Sorts the node list by duration since last heard
        * Nearby
            * Sorts the node list by who is closest
            * click the header to see who is farthest
        * Big Sig
            * Sorts the list by highest signal strength
            * Click the header to see weakest signals 
        * Most Packets
            * Sorts node list by who has the most packets
        * Msgs Sent
            * Sorts the node list by the number of messages a node has sent
        * Msgs Received
            * Sorts the node list by the number of messages a node has received
    * Charts
        * Hop Counts
            * Shows counts for Hops Used and Hops Left in the same chart
        * Percent of Hops Used
            * Shows the percentage of hops it took for the packet to reach our reference node
            * The percentage is calculated using reported Hop Start and Hops Left values
        * Hop Efficiency
            * This chart shows the breakdown of packets that start with a specific Hop value 
            * Each row shows the breakdown of how many hops it took
        * Packet Breakdown
            * Charts the number of received packets of each type
        * Hardware
            * Charts the number of each type of hardware, as reported in the NodeInfo packet
        * Roles
            * Charts the number of each Role found in the session

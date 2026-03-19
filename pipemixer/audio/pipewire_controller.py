import subprocess
import time
import threading


class PipeWireController:

    def __init__(self, poll_interval=0.1):
        self.node_cache = {}        # app_name -> list of node IDs
        self.last_values = {}       # app_name -> last slider value (0.0–1.0)
        self.poll_interval = poll_interval
        self.running = True

        # Start background thread to refresh nodes automatically
        threading.Thread(target=self._poll_nodes_loop, daemon=True).start()

    def set_volume(self, app_name, midi_value):
        """
        Update slider value for an app (0–127 MIDI)
        """
        volume = midi_value / 127
        self.last_values[app_name] = volume

        # Apply immediately to any known nodes
        node_ids = self.get_cached_nodes(app_name)
        for node in node_ids:
            self._apply_node_volume(node, volume)

    def get_cached_nodes(self, app_name):
        """
        Return cached nodes if they exist; otherwise find and cache them.
        """
        if app_name in self.node_cache:
            return self.node_cache[app_name]

        nodes = self.find_nodes(app_name)
        self.node_cache[app_name] = nodes
        print(f"Cached nodes for {app_name}: {nodes}")
        return nodes

    def find_nodes(self, name):
        """
        Return a list of PipeWire node IDs matching the app name.
        """
        result = subprocess.run(
            ["wpctl", "status"],
            capture_output=True,
            text=True
        )

        nodes = []
        for line in result.stdout.splitlines():
            if name.lower() in line.lower():
                parts = line.strip().split(".")
                if parts[0].isdigit():
                    nodes.append(parts[0])

        return nodes

    def _apply_node_volume(self, node_id, volume):
        subprocess.run(
            ["wpctl", "set-volume", str(node_id), str(volume)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def _poll_nodes_loop(self):
        """
        Background thread to detect new nodes and apply last slider value.
        """
        while self.running:
            for app_name, volume in self.last_values.items():
                nodes = self.find_nodes(app_name)
                # Only update cache if new nodes appear
                if app_name not in self.node_cache:
                    self.node_cache[app_name] = nodes
                else:
                    # Add new nodes that weren’t in cache
                    for node in nodes:
                        if node not in self.node_cache[app_name]:
                            self.node_cache[app_name].append(node)

                # Apply last known volume to all nodes
                for node in self.node_cache[app_name]:
                    self._apply_node_volume(node, volume)

            time.sleep(self.poll_interval)

    def stop(self):
        """Stop the polling thread cleanly."""
        self.running = False

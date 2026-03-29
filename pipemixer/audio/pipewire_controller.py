import subprocess
import time
import threading


class PipeWireController:

    def __init__(self, poll_interval=0.1):
        self.node_cache = {}        # app_name -> list of node IDs
        self.last_values = {}       # app_name -> last slider value
        self.last_node_volume = {}  # node_id -> last volume (FIXED)

        self.poll_interval = poll_interval
        self.running = True

        threading.Thread(target=self._poll_nodes_loop, daemon=True).start()

    # ---------------- Public ----------------
    def set_volume(self, app_name, volume):
        self.last_values[app_name] = volume

        node_ids = self.get_cached_nodes(app_name)
        for node in node_ids:
            self._apply_node_volume(node, volume)

    # ---------------- Node Discovery ----------------
    def get_cached_nodes(self, app_name):
        if app_name in self.node_cache:
            return self.node_cache[app_name]

        nodes = self.find_nodes(app_name)
        self.node_cache[app_name] = nodes
        print(f"Cached nodes for {app_name}: {nodes}")
        return nodes

    def find_nodes(self, name):
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

    # ---------------- Volume ----------------
    def _apply_node_volume(self, node_id, volume):
        # Skip tiny changes (WORKING CACHE NOW)
        if node_id in self.last_node_volume:
            if abs(self.last_node_volume[node_id] - volume) < 0.01:
                return

        self.last_node_volume[node_id] = volume

        # SYNCHRONOUS = FASTEST
        subprocess.run(
            ["wpctl", "set-volume", str(node_id), str(volume)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    # ---------------- Background ----------------
    def _poll_nodes_loop(self):
        while self.running:
            for app_name, volume in self.last_values.items():
                nodes = self.find_nodes(app_name)

                if app_name not in self.node_cache:
                    self.node_cache[app_name] = nodes
                else:
                    for node in nodes:
                        if node not in self.node_cache[app_name]:
                            self.node_cache[app_name].append(node)

                for node in self.node_cache[app_name]:
                    self._apply_node_volume(node, volume)

            time.sleep(self.poll_interval)

    def stop(self):
        self.running = False

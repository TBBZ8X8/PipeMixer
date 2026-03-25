import subprocess
import time
import threading


class PipeWireController:

    def __init__(self, poll_interval=0.25):
        self.node_cache = {}
        self.last_values = {}
        self.poll_interval = poll_interval
        self.running = True

        threading.Thread(target=self._poll_nodes_loop, daemon=True).start()

    # ---------------- Public ----------------
    def set_volume(self, app_name, volume):
        """
        Volume is expected 0.0–1.0
        """
        self.last_values[app_name] = volume

        nodes = self._get_nodes_for_app(app_name)
        for node in nodes:
            self._apply_node_volume(node, volume)

    # ---------------- Node Discovery ----------------
    def _get_nodes_for_app(self, app_name):
        current_nodes = set(self._find_nodes(app_name))

        if app_name not in self.node_cache:
            self.node_cache[app_name] = current_nodes
            print(f"Cached nodes for {app_name}: {list(current_nodes)}")
            return current_nodes

        new_nodes = current_nodes - self.node_cache[app_name]
        if new_nodes:
            print(f"New nodes for {app_name}: {list(new_nodes)}")

        self.node_cache[app_name] = current_nodes
        return current_nodes

    def _find_nodes(self, name):
        result = subprocess.run(
            ["wpctl", "status"],
            capture_output=True,
            text=True
        )

        nodes = []

        for line in result.stdout.splitlines():
            if name.lower() in line.lower():
                parts = line.strip().split()
                if parts and parts[0].endswith("."):
                    node_id = parts[0].replace(".", "")
                    if node_id.isdigit():
                        nodes.append(node_id)

        return nodes

    # ---------------- Volume ----------------
    def _apply_node_volume(self, node_id, volume):
        last_volume = getattr(self, "_last_node_volume", {})
        if node_id in last_volume and abs(last_volume[node_id] - volume) < 0.01:
            return  # Skip tiny changes
        last_volume[node_id] = volume

        # Run asynchronously to avoid blocking
        threading.Thread(target=subprocess.run, args=(
            ["wpctl", "set-volume", str(node_id), str(volume)],), kwargs={"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}).start()

    # ---------------- Background ----------------
    def _poll_nodes_loop(self):
        while self.running:
            for app_name, volume in self.last_values.items():
                current_nodes = set(self._find_nodes(app_name))
                cached_nodes = self.node_cache.get(app_name, set())

                new_nodes = current_nodes - cached_nodes

                for node in new_nodes:
                    print(f"Applying volume to new node {node} ({app_name})")
                    self._apply_node_volume(node, volume)

                self.node_cache[app_name] = current_nodes

            time.sleep(self.poll_interval)

    def stop(self):
        self.running = False

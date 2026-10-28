"""Тесты для модуля автоматического построения транспортной сети auto_network.py."""

try:
    import _bootstrap
except ImportError:
    pass

import unittest
from core.tower import Tower
from networking.network import Network
from networking.auto_network import build_backhaul_network


class TestAutoNetwork(unittest.TestCase):
    def test_build_backhaul_network_creates_valid_topology(self):
        net = Network()
        towers = [
            Tower("БС-1", 55.750, 37.610, 1800.0, 20.0, 25.0, 4.0, 20.0, [0, 0, 0, 255], db_id=1),
            Tower("БС-2", 55.752, 37.612, 1800.0, 20.0, 30.0, 4.0, 20.0, [0, 0, 0, 255], db_id=2),
            Tower("БС-3", 55.755, 37.618, 1800.0, 20.0, 28.0, 4.0, 20.0, [0, 0, 0, 255], db_id=3),
        ]

        stats = build_backhaul_network(net, towers)
        self.assertEqual(stats["status"], "ok")
        self.assertGreater(stats["routers"], 0)
        self.assertGreater(stats["links"], 0)

        # Проверяем наличие ядра
        core_devs = [d for d in net.devices.values() if d.role == "core"]
        self.assertEqual(len(core_devs), 1)

        # Проверяем наличие БС
        tower_devs = [d for d in net.devices.values() if d.role == "tower"]
        self.assertEqual(len(tower_devs), 3)

        # Проверяем, что все вышки получили IP адреса
        for tw_dev in tower_devs:
            self.assertGreater(len(tw_dev.ips), 0, f"Устройство {tw_dev.name} не получило IP")

        # Проверяем пинг от вышки до ядра сети
        ping_res = net.ping(tower_devs[0].id, core_devs[0].id)
        self.assertTrue(ping_res.ok, f"Пинг от БС до ядра не прошел: {ping_res.reason}")

    def test_empty_towers(self):
        net = Network()
        stats = build_backhaul_network(net, [])
        self.assertEqual(stats["status"], "empty")

    def test_multiple_clusters(self):
        net = Network()
        towers = [
            Tower(f"БС-{i}", 55.750 + i * 0.01, 37.610 + i * 0.01, 1800.0, 20.0, 25.0, 4.0, 20.0, [0, 0, 0, 255], db_id=i)
            for i in range(1, 8)
        ]
        stats = build_backhaul_network(net, towers, max_towers_per_router=3)
        self.assertEqual(stats["status"], "ok")
        self.assertGreater(stats["routers"], 1)

    def test_network_undo_restore(self):
        net = Network()
        snapshot = net.to_dict()
        towers = [
            Tower("БС-1", 55.750, 37.610, 1800.0, 20.0, 25.0, 4.0, 20.0, [0, 0, 0, 255], db_id=1),
        ]
        build_backhaul_network(net, towers)
        self.assertGreater(len(net.devices), 0)

        # Восстановление снимка сети (как при Ctrl+Z)
        restored = Network.from_dict(snapshot)
        self.assertEqual(len(restored.devices), 0)
        self.assertEqual(len(restored.links), 0)


if __name__ == "__main__":
    unittest.main()

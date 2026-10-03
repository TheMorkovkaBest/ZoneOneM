import math
import requests


class BuildingData:
    def __init__(self):
        self.buildings = []
        self.loaded_centers = []

    def fetch(self, lat, lon, radius_m=1200):
        for c_lat, c_lon in self.loaded_centers:
            dist = math.hypot((lat - c_lat) * 111320.0, (lon - c_lon) * 111320.0 * math.cos(math.radians(lat)))
            if dist < 600:
                return

        query = f"""
        [out:json][timeout:15];
        (
          way["building"](around:{radius_m},{lat},{lon});
          relation["building"](around:{radius_m},{lat},{lon});
        );
        out body;
        >;
        out skel qt;
        """
        urls = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"
        ]
        headers = {'User-Agent': 'Mozilla/5.0', 'Content-Type': 'text/plain; charset=utf-8'}

        for url in urls:
            try:
                resp = requests.post(url, data=query.encode('utf-8'), headers=headers, timeout=10)
                if resp.status_code == 200:
                    self._parse_overpass(resp.json())
                    self.loaded_centers.append((lat, lon))
                    return
            except Exception:
                continue

    def _parse_overpass(self, data):
        nodes = {}
        for elem in data.get('elements', []):
            if elem['type'] == 'node':
                nodes[elem['id']] = (elem['lat'], elem['lon'])

        for elem in data.get('elements', []):
            if elem['type'] == 'way' and 'nodes' in elem:
                poly = [nodes[nid] for nid in elem['nodes'] if nid in nodes]
                if len(poly) >= 3:
                    tags = elem.get('tags', {})
                    h_str = tags.get('height') or tags.get('building:levels')
                    height = 15.0
                    if h_str:
                        try:
                            val = float(''.join(c for c in h_str.split()[0] if c.isdigit() or c == '.'))
                            height = val * 3.0 if 'level' in tags.get('building:levels', '') else val
                        except:
                            pass

                    lats = [p[0] for p in poly]
                    lons = [p[1] for p in poly]
                    self.buildings.append({
                        'height': height,
                        'poly': poly,
                        'bbox': (min(lats), max(lats), min(lons), max(lons))
                    })
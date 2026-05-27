import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from api.api import Api

api = Api()

# Test get_vips for event 'sao-paulo'
vips = api.get_vips("sao-paulo")
print("API vips for 'sao-paulo':")
for v in vips:
    print(v)

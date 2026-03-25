from pathlib import Path
import json

sensors = {}

path = Path("data/replacement_names.json")
with open(path, "r") as f:
    sensors = json.load(f)

def replace_name(code):
    if(sensors == {}):
        return False
    try:
        return sensors[code]
    except KeyError:
        return False
    
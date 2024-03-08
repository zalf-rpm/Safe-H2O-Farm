#!/usr/bin/python
# -*- coding: UTF-8

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/. */

# Authors:
# Michael Berg-Mohnicke <michael.berg@zalf.de>
#
# Maintainers:
# Currently maintained by the authors.
#
# This file has been created at the Institute of
# Landscape Systems Analysis at the ZALF.
# Copyright (C: Leibniz Centre for Agricultural Landscape Research (ZALF)

import capnp
from collections import defaultdict
import csv
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

PATH_TO_REPO = Path(os.path.realpath(__file__)).parent
PATH_TO_MAS_INFRASTRUCTURE_REPO = PATH_TO_REPO / "../mas-infrastructure"
PATH_TO_PYTHON_CODE = PATH_TO_MAS_INFRASTRUCTURE_REPO / "src/python"
if str(PATH_TO_PYTHON_CODE) not in sys.path:
    sys.path.insert(1, str(PATH_TO_PYTHON_CODE))

from pkgs.common import common
from pkgs.common import fbp

PATH_TO_CAPNP_SCHEMAS = (PATH_TO_MAS_INFRASTRUCTURE_REPO / "capnproto_schemas").resolve()
abs_imports = [str(PATH_TO_CAPNP_SCHEMAS)]
fbp_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "fbp.capnp"), imports=abs_imports)
mgmt_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "model/monica/monica_management.capnp"), imports=abs_imports)
geo_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "geo.capnp"), imports=abs_imports)

config = {
    "path_to_data_dir": str(PATH_TO_REPO / "data"),
    "years": "[2018]",
    "years_in_sr": None,  # "[2018]" :string of json serialized list of years
    "out_sr": None,  # empty IP with attributes: ilr, soil, latlon
}
common.update_config(config, sys.argv, print_config=True, allow_new_keys=False)
ports, close_out_ports = fbp.connect_ports(config)

# get default country ids
years = json.loads(config["years"])

while ports["years"] and ports["out"]:
    try:
        msg = ports["years"].read().wait()
        if msg.which() == "done":
            ports["years"] = None
            continue
        else:
            years_ip = msg.value.as_struct(fbp_capnp.IP)
            years_txt = years_ip.content.as_text()
            if len(years_txt):
                years = json.loads(years_txt)
                if isinstance(years, int):
                    years = [years]

        for year in years:
            data = {}
            with open(f"{config['path_to_data_dir']}/{year}_MONICA.csv") as file:
                dialect = csv.Sniffer().sniff(file.read(), delimiters=';,\t')
                file.seek(0)
                reader = csv.reader(file, dialect)
                header = next(reader)
                for line in reader:
                    row = {}
                    id = None
                    for i, value in enumerate(line):
                        if header[i].lower() == "id":
                            id = value
                        if value == "":
                            row[header[i]] = None
                        else:
                            row[header[i]] = value
                    data[id] = row

            for id, site in data.items():
                year = int(site["year"])
                sd = date.fromisoformat(site["transplant_date"])
                hd = date.fromisoformat(site["harvesting_date"])
                ilr = mgmt_capnp.ILRDates.new_message(
                    sowing={"year": year, "month": sd.month, "day": sd.day},
                    harvest={"year": year, "month": hd.month, "day": hd.month}
                )

                # build soil profile
                soil_profile = []
                for l, t in [("0-5", 0.1), ("5-15", 0.1), ("15-30", 0.1), ("30-60", 0.3),
                             ("60-100", 0.4), ("100-200", 1.0)]:
                    soil_profile.append({
                        "Thickness": [t, "m"],
                        "SoilOrganicCarbon": [float(site[f"soc{l}"]), "%"],
                        "SoilBulkDensity": [float(site[f"bdod{l}"]), "kg m-3"],
                        "Sand": [float(site[f"sand{l}"]), "fraction"],
                        "Clay": [float(site[f"clay{l}"]), "fraction"],
                        "pH": [float(site[f"ph{l}"]), ""],
                    })

                climate_id = int(site["climate_id"])
                out_ip = fbp_capnp.IP.new_message(attributes=[
                    {"key": "ilr", "value": ilr},
                    {"key": "soil", "value": json.dumps(soil_profile)},
                    {"key": "latlon", "value": geo_capnp.LatLonCoord.new_message(
                        lat=float(site["latitude"]), lon=0.0)},
                    {"key": "id", "value": str(id)},
                    {"key": "climate_id", "value": str(climate_id)},
                    {"key": "year", "value": str(year)},
                ])

                ports["out"].write(value=out_ip).wait()
    except Exception as e:
        print(f"{os.path.basename(__file__)} Exception:", e)

close_out_ports()
print(f"{os.path.basename(__file__)}: process finished")

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
from pyproj import CRS, Transformer
import sys
import time

PATH_TO_REPO = Path(os.path.realpath(__file__)).parent
PATH_TO_MAS_INFRASTRUCTURE_REPO = PATH_TO_REPO / "../mas-infrastructure"
PATH_TO_PYTHON_CODE = PATH_TO_MAS_INFRASTRUCTURE_REPO / "src/python"
if str(PATH_TO_PYTHON_CODE) not in sys.path:
    sys.path.insert(1, str(PATH_TO_PYTHON_CODE))

from pkgs.common import common
from pkgs.common import fbp

PATH_TO_CAPNP_SCHEMAS = (PATH_TO_MAS_INFRASTRUCTURE_REPO / "capnproto_schemas").resolve()
abs_imports = [str(PATH_TO_CAPNP_SCHEMAS)]
common_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "common.capnp"), imports=abs_imports)
fbp_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "fbp.capnp"), imports=abs_imports)
soil_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "soil_data.capnp"), imports=abs_imports)
model_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "model.capnp"), imports=abs_imports)
climate_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "climate_data.capnp"), imports=abs_imports)
mgmt_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "models/monica/monica_management.capnp"), imports=abs_imports)
geo_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "geo_coord.capnp"), imports=abs_imports)
grid_capnp = capnp.load(str(PATH_TO_CAPNP_SCHEMAS / "grid.capnp"), imports=abs_imports)

import monica_io3
import monica_run_lib as Mrunlib

config = {
    "in_sr": None, # string
    "out_sr": None, # utm_coord + id attr
    "sim.json": "sim.json",
    "crop.json": "crop.json",
    "site.json": "site.json",
    "dgm_attr": "dgm",
    "slope_attr": "slope",
    "climate_attr": "climate",
    "soil_attr": "soil",
    "coord_attr": "latlon",
    "ilr_attr": "ilr",
    "id_attr": "id",
}
common.update_config(config, sys.argv, print_config=True, allow_new_keys=False)
ports, close_out_ports = fbp.connect_ports(config)

wgs84_crs = CRS.from_epsg(4326)
utm32_crs = CRS.from_epsg(25832)
utm32_to_latlon_transformer = Transformer.from_crs(utm32_crs, wgs84_crs, always_xy=True)


with open(config["sim.json"]) as _:
    sim_json = json.load(_)

with open(config["site.json"]) as _:
    site_json = json.load(_)
# if len(scenario) > 0 and scenario[:3].lower() == "rcp":
#    site_json["EnvironmentParameters"]["rcp"] = scenario

with open(config["crop.json"]) as _:
    crop_json = json.load(_)

# create environment template from json templates
env_template = monica_io3.create_env_json_from_json_config({
    "crop": crop_json,
    "site": site_json,
    "sim": sim_json,
    "climate": ""
})

while ports["in"] and any(ports["out"]):
    try:
        in_msg = ports["in"].read().wait()
        # check for end of data from in port
        if in_msg.which() == "done":
            ports["in"] = None
            continue

        in_ip = in_msg.value.as_struct(common_capnp.IP)
        llcoord = common.get_fbp_attr(in_ip, config["coord_attr"]).as_struct(geo_capnp.LatLonCoord)
        #height_nn = common.get_fbp_attr(in_ip, config["dgm_attr"]).as_struct(grid_capnp.Grid.Value).f
        #slope = common.get_fbp_attr(in_ip, config["slope_attr"]).as_struct(grid_capnp.Grid.Value).f
        timeseries = common.get_fbp_attr(in_ip, config["climate_attr"]).as_interface(climate_capnp.TimeSeries)
        #soil_profile = common.get_fbp_attr(in_ip, config["soil_attr"]).as_struct(soil_capnp.Profile)
        soil_profile = json.loads(common.get_fbp_attr(in_ip, config["soil_attr"]).as_text())
        ilr = common.get_fbp_attr(in_ip, config["ilr_attr"]).as_struct(mgmt_capnp.ILRDates)
        id = common.get_fbp_attr(in_ip, config["id_attr"]).as_text()

        #if len(soil_profile.layers) == 0:
        #    continue
        if len(soil_profile) == 0:
            continue
        env_template["params"]["siteParameters"]["SoilProfileParameters"] = soil_profile

        worksteps = env_template["cropRotation"][0]["worksteps"]
        sowing_ws = next(filter(lambda ws: ws["type"][-6:] == "Sowing", worksteps))
        if ilr._has("sowing"):
            s = ilr.sowing
            sowing_ws["date"] = "{:04d}-{:02d}-{:02d}".format(s.year, s.month, s.day)
        if ilr._has("earliestSowing"):
            s = ilr.earliestSowing
            sowing_ws["earliest-date"] = "{:04d}-{:02d}-{:02d}".format(s.year, s.month, s.day)
        if ilr._has("latestSowing"):
            s = ilr.latestSowing
            sowing_ws["latest-date"] = "{:04d}-{:02d}-{:02d}".format(s.year, s.month, s.day)

        harvest_ws = next(filter(lambda ws: ws["type"][-7:] == "Harvest", worksteps))
        if ilr._has("harvest"):
            h = ilr.harvest
            harvest_ws["date"] = "{:04d}-{:02d}-{:02d}".format(h.year, h.month, h.day)
        if ilr._has("latestHarvest"):
            h = ilr.latestHarvest
            harvest_ws["latest-date"] = "{:04d}-{:02d}-{:02d}".format(h.year, h.month, h.day)

        #env_template["params"]["siteParameters"]["heightNN"] = height_nn
        #env_template["params"]["siteParameters"]["slope"] = slope / 100.0
        env_template["params"]["siteParameters"]["Latitude"] = llcoord.lat

        #env_template[
        #    "pathToClimateCSV"] = "/run/user/1000/gvfs/sftp:host=login01.cluster.zalf.de,user=rpm/beegfs/common/data/climate/dwd/csvs/germany/row-0/col-181.csv"

        env_template["customId"] = {
            "setup_id": setup.runId,
            "id": id,
            "crop_id": setup.cropId,
            "lat": llcoord.lat, "lon": llcoord.lon
        }

        capnp_env = model_capnp.Env.new_message()
        capnp_env.timeSeries = timeseries
        #capnp_env.soilProfile = soil_profile
        capnp_env.rest = common_capnp.StructuredText.new_message(value=json.dumps(env_template),
                                                                 structure={"json": None})

        out_ip = common_capnp.IP.new_message(content=capnp_env, attributes=[{"key": "id", "value": id}])
        ports["out"].write(value=out_ip).wait()


    except Exception as e:
        print(f"{os.path.basename(__file__)} Exception :", e)

close_out_ports()
print(f"{os.path.basename(__file__)}: process finished")

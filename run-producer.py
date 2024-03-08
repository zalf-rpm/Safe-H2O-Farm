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

import csv
import json
import os
import sys
import zmq

import monica_io3
import shared

PATHS = {
    "local-remote": {
        "monica_path_to_climate_dir": "/project/climate_data",
        "path_to_data_dir": "./data",
    },
    "local-local": {
        "monica_path_to_climate_dir": "/home/berg/GitHub/Safe-H2O-Farm/data",
        "path_to_data_dir": "./data/",
    },

    "remoteProducer-remoteMonica": {
        "monica_path_to_climate_dir": "/monica_data/climate-data",
        "path_to_data_dir": "./data/",  # mounted path to archive or hard drive with data
    }
}


def run_producer(server = {"server": None, "port": None}, shared_id = None):

    context = zmq.Context()
    socket = context.socket(zmq.PUSH) # pylint: disable=no-member

    config = {
        "mode": "local-local",
        "server-port": server["port"] if server["port"] else "6666",
        "server": server["server"] if server["server"] else "localhost",
        "sim.json": os.path.join(os.path.dirname(__file__), "sim.json"),
        "crop.json": os.path.join(os.path.dirname(__file__), "crop.json"),
        "site.json": os.path.join(os.path.dirname(__file__), "site.json"),
        "setups-file": None,
        "run-setups": "[2018,2019]",  # setup-ids used as years
    }
    shared.update_config(config, sys.argv, print_config=True, allow_new_keys=False)

    paths = PATHS[config["mode"]]

    socket.connect("tcp://" + config["server"] + ":" + config["server-port"])

    with open(config["sim.json"]) as _:
        sim_json = json.load(_)

    with open(config["site.json"]) as _:
        site_json = json.load(_)

    with open(config["crop.json"]) as _:
        crop_json = json.load(_)

    env_template = monica_io3.create_env_json_from_json_config({
        "crop": crop_json,
        "site": site_json,
        "sim": sim_json,
        "climate": ""  # climate_csv
    })

    years = json.loads(config["run-setups"])
    for year in years:
        data = {}
        csvPath = f"{paths['path_to_data_dir']}/{year}_MONICA.csv"
        print("CSV path:", csvPath)

        with open(csvPath) as file:
            dialect = csv.Sniffer().sniff(file.read(), delimiters=';,\t')
            file.seek(0)
            reader = csv.reader(file, dialect)
            header = next(reader)
            for line in reader:
                row = {}
                id = None
                for i, value in enumerate(line):
                    if header[i].lower() == "id":
                        id = int(value)
                    if value == "":
                        row[header[i]] = None
                    else:
                        row[header[i]] = value
                data[id] = row

        for id, site in data.items():

            year = int(site["year"])
            climate_id = int(site["climate_id"])

            env_template["csvViaHeaderOptions"] = sim_json["climate.csv-options"]
            env_template["pathToClimateCSV"] = \
                f"{paths['monica_path_to_climate_dir']}/Results_{year}/result_{climate_id:05d}_{year}.csv"
            if not os.path.exists(env_template["pathToClimateCSV"]):
                print("path does not exist: ", env_template["pathToClimateCSV"])
                continue

            sowing_ws = env_template["cropRotation"][0]["worksteps"][0]
            sowing_ws["date"] = site["transplant_date"]

            harvest_ws = env_template["cropRotation"][0]["worksteps"][1]
            harvest_ws["date"] = site["harvesting_date"]

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

            # build environment
            if len(soil_profile) == 0:
                continue
            env_template["params"]["siteParameters"]["SoilProfileParameters"] = soil_profile

            env_template["customId"] = {
                "id": id,
                "year": year,
                #"years": years,
                "climate_id": climate_id,
                "nodata": False,
            }
            socket.send_json(env_template)
            print(f"{os.path.basename(__file__)} sent job {year}/{id}")

    # send done message
    env_template["customId"] = {
        "nodata": True,
    }
    socket.send_json(env_template)

    print("done")

if __name__ == "__main__":
    run_producer()
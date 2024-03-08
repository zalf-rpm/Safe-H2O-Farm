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
import os
import sys
import zmq

import shared

PATHS = {
    "local": {
        "path-to-output-dir": "out/",
    },
    "remoteConsumer-remoteMonica": {
        "path-to-output-dir": "/out/",
    }
}

def run_consumer(server = {"server": None, "port": None}):

    config = {
        "mode": "local",
        "port": server["port"] if server["port"] else "7777",
        "server": server["server"] if server["server"] else "localhost",
        "path_to_out_file": "out.csv",
    }
    
    path = PATHS[config["mode"]]
    outPath = path["path-to-output-dir"]
    if not path["path-to-output-dir"].startswith("/") :
      outPath = os.path.join(os.path.dirname(__file__), outPath)
    config["path_to_out_file"] = os.path.join(outPath, config["path_to_out_file"])

    shared.update_config(config, sys.argv, print_config=True, allow_new_keys=False)

    context = zmq.Context()
    socket = context.socket(zmq.PULL)
    socket.connect("tcp://" + config["server"] + ":" + config["port"])

    socket.RCVTIMEO = 6000

    dir = os.path.dirname(config["path_to_out_file"])
    if not os.path.exists(dir):
        try:
            os.makedirs(dir)
        except OSError:
            print(f"{os.path.basename(__file__)} Couldn't create dir {dir}! Exiting.")
            exit(1)

    filepath = config["path_to_out_file"]
    with open(filepath, "wt") as _:
        writer = csv.writer(_, delimiter=",")
        writer.writerow(["id", "year", "yield", "climate_id"])
        writer.writerow(["", "", "[kg/ha]", ""])

        while True:
            try:
                msg = socket.recv_json()

                if msg.get("errors", []):
                    print(f"{os.path.basename(__file__)} received errors: {msg['errors']}")
                    continue

                custom_id = msg.get("customId", {})
                if len(custom_id) == 0:
                    print(f"{os.path.basename(__file__)} no custom_id")
                    continue

                if custom_id.get("nodata", False):
                    print(f"{os.path.basename(__file__)} received nodata=true -> done")
                    break

                id = custom_id.get("id", None)
                year = custom_id.get("year", None)
                climate_id = custom_id.get("climate_id", None)

                print(f"{os.path.basename(__file__)} received result {year}/{id}")

                for data in msg.get("data", []):
                    results = data.get("results", [])
                    row = [id]
                    for vals in results:
                        if "Yield" in vals:
                            row.append(vals["Year"])
                            row.append(vals["Yield"])
                    row.append(climate_id)
                    writer.writerow(row)

            except Exception as e:
                print(f"{os.path.basename(__file__)} Exception: {e}")

    print(f"{os.path.basename(__file__)} exiting run_consumer()")

if __name__ == "__main__":
    run_consumer()
    
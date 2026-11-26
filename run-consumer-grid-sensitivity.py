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

from collections import defaultdict
import csv
from pathlib import Path
import sys

import zmq

PATHS = {
    "re-local-remote": {
        "path-to-data-dir": "./data/",
        "path-to-output-dir": "D:/Safe-H2O-Farm/out/out/",
        "path-to-csv-output-dir": "D:/Safe-H2O-Farm/out/csv-out/"
    },
    "remoteConsumer-remoteMonica": {
        "path-to-data-dir": "./data/",
        "path-to-output-dir": "/out/out/",
        "path-to-csv-output-dir": "/out/csv-out/"
    }
}

SENSITIVITY_FIELDS = (
    "run-id", "year", "latitude", "longitude", "soil_row", "soil_col", "climate_row", "climate_col",
    "AbBiom", "Yield", "DOY-3", "DOY-4", "DOY-5", "DOY-6", "DOY-7",
    "HeatRed", "Irrigation", "Evapotranspiration", "SumNUp",
    "NLeach", "PercolationRate", "SOC", "PeaAbBiom", "PeaTotBiomN"
)


def create_output(msg):
    phenology_keys = ("DOY-3", "DOY-4", "DOY-5", "DOY-6", "DOY-7")
    crop_results = defaultdict(dict)
    phenology_results = defaultdict(dict)
    yearly_results = {}

    for section in msg.get("data", []):
        orig_spec = section.get("origSpec", "")
        for vals in section.get("results", []):
            if orig_spec == '"yearly"' and "Year" in vals:
                yearly_values = yearly_results.setdefault((None, vals["Year"]), {})
                yearly_values.update({key: value for key, value in vals.items() if key != "Year"})
            elif "CM-count" in vals:
                cm_count = vals["CM-count"]
                if orig_spec == '"crop"':
                    crop_results[cm_count].update(vals)
                else:
                    for key in phenology_keys:
                        if key in vals:
                            phenology_results[cm_count].setdefault(key, vals[key])

    for cm_count, vals in crop_results.items():
        if "Year" in vals:
            yearly_results[(cm_count, vals["Year"])] = {**vals, **phenology_results.get(cm_count, {})}

    return yearly_results


def sensitivity_rows(msg):
    """Collect tomato, field pea and soil output in one row per calendar year."""
    custom_id = msg["customId"]
    location = {
        "run-id": custom_id["setup_id"],
        "latitude": custom_id["latitude"],
        "longitude": custom_id["longitude"],
        "soil_row": custom_id["srow"],
        "soil_col": custom_id["scol"],
        "climate_row": custom_id["crow"],
        "climate_col": custom_id["ccol"],
    }
    years = defaultdict(dict)
    for (cm_count, year), values in create_output(msg).items():
        if cm_count is None:
            years[year].update(NLeach=values["NLeach-yearly"], PercolationRate=values["WDrain-yearly"],
                               SOC=values["SOC-yearly"])
            continue

        species = values["Crop"].partition("/")[0].casefold()
        if species == "tomato":
            years[year].update(AbBiom=values["AbBiom"], Yield=values["Yield"],
                               HeatRed=values["HeatRed"], Irrigation=values["Irrig"],
                               Evapotranspiration=values["Evapotranspiration"], SumNUp=values["SumNUp"])
            years[year].update({field: values[field] for field in values if field.startswith("DOY-")})
        elif species == "field pea":
            years[year].update(PeaAbBiom=values["AbBiom"], PeaTotBiomN=values["TotBiomN"])

    return [{**location, "year": year, **years[year]} for year in sorted(years)]


def run_consumer(leave_after_finished_run=True, server={"server": None, "port": None}, shared_id=None):
    """Collect coordinate results from MONICA workers and write sensitivity CSVs."""

    config = {
        "mode": "re-local-remote",
        "port": server["port"] if server["port"] else "7778",
        "server": server["server"] if server["server"] else "login01.cluster.zalf.de",
        "shared_id": shared_id,
        "timeout": 600000,  # 10 minutes, in milliseconds
        "csv-out": "",
        "expected": "0",
    }

    if len(sys.argv) > 1 and __name__ == "__main__":
        for arg in sys.argv[1:]:
            k, v = arg.split("=", 1)
            if k in config:
                config[k] = v

    config["csv-out"] = config["csv-out"] or PATHS[config["mode"]]["path-to-csv-output-dir"]
    sensitivity_dir = Path(config["csv-out"])

    expected = int(config["expected"])

    print("consumer config:", config)

    context = zmq.Context()
    if config["shared_id"]:
        socket = context.socket(zmq.DEALER)
        identity = config["shared_id"]
        socket.setsockopt(zmq.IDENTITY, identity.encode() if isinstance(identity, str) else identity)
    else:
        socket = context.socket(zmq.PULL)

    socket.connect("tcp://" + config["server"] + ":" + str(config["port"]))
    socket.RCVTIMEO = int(config["timeout"])

    rows_written_by_run = defaultdict(int)
    failures = 0

    def process_message(msg):
        nonlocal failures
        if msg.get("errors"):
            failures += 1
            print("MONICA error for", msg.get("customId"), msg["errors"], file=sys.stderr)
            return

        custom_id = msg.get("customId") or {}
        if "latitude" not in custom_id or "longitude" not in custom_id:
            raise ValueError("Sensitivity results require latitude and longitude in customId")

        rows = sensitivity_rows(msg)
        if not rows:
            raise ValueError(f"No annual or harvest results for {custom_id}")
        run_id = int(custom_id["setup_id"])
        sensitivity_csv = sensitivity_dir / f"sensitivity_{run_id}.csv"
        first_result = rows_written_by_run[run_id] == 0
        if first_result:
            sensitivity_dir.mkdir(parents=True, exist_ok=True)
        with sensitivity_csv.open("w" if first_result else "a", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=SENSITIVITY_FIELDS)
            if first_result:
                writer.writeheader()
            writer.writerows(rows)
        rows_written_by_run[run_id] += len(rows)

    received = 0
    batch_id = None
    batch_expected = 0
    batch_cells = set()
    try:
        while not (leave_after_finished_run and expected and received >= expected):
            try:
                msg = socket.recv_json()
            except zmq.Again:
                print(f"No worker response within {socket.RCVTIMEO} ms.", file=sys.stderr)
                break

            if msg.get("type") == "finish":
                if leave_after_finished_run:
                    break
                continue

            custom_id = msg.get("customId") or {}
            if leave_after_finished_run and not expected and custom_id.get("batch_id"):
                if batch_id is None:
                    batch_id = custom_id["batch_id"]
                    batch_expected = custom_id["batch_size"]
                if custom_id["batch_id"] == batch_id:
                    cell = (custom_id["setup_id"], custom_id["srow"], custom_id["scol"])
                    if cell in batch_cells:
                        continue
                    batch_cells.add(cell)

            received += 1
            try:
                process_message(msg)
            except Exception as error:
                failures += 1
                print(f"Invalid result for {msg.get('customId')}: {error}", file=sys.stderr)
            if batch_expected and len(batch_cells) == batch_expected:
                break
    finally:
        socket.close()
        context.term()

    print("exiting run_consumer()")


if __name__ == "__main__":
    run_consumer()

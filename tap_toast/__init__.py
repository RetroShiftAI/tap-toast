#!/usr/bin/env python3
import json
import os
import sys
import singer
from singer import metadata
from tap_toast.toast import Toast
from tap_toast.discover import discover_streams
from tap_toast.sync import sync_stream
from tap_toast.streams import STREAMS
from tap_toast.context import Context


LOGGER = singer.get_logger()

REQUIRED_CONFIG_KEYS = [
    "client_id",
    "client_secret",
    "pc_numbers",
    "start_date"
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING_FILE = os.path.join(PROJECT_ROOT, '.secrets', 'pc_to_guid_mapping.json')


def load_pc_to_guid_mapping():
    if not os.path.exists(MAPPING_FILE):
        raise FileNotFoundError(
            f"PC to GUID mapping file not found at: {MAPPING_FILE}\n"
            f"Generate it by running the partners API curl command and saving to .secrets/pc_to_guid_mapping.json"
        )
    with open(MAPPING_FILE) as f:
        mapping = json.load(f)
    return {item['pc_number']: item for item in mapping}


def resolve_locations(pc_numbers, mapping):
    resolved = []
    missing = []
    for pc in pc_numbers:
        if pc in mapping:
            resolved.append(mapping[pc])
        else:
            missing.append(pc)
    if missing:
        raise ValueError(
            f"PC numbers not found in mapping file: {missing}\n"
            f"Available PC numbers: {list(mapping.keys())}"
        )
    return resolved


def do_discover(client):
    LOGGER.info("Starting discover")
    catalog = {"streams": discover_streams(client)}
    json.dump(catalog, sys.stdout, indent=2)
    LOGGER.info("Finished discover")


def stream_is_selected(mdata):
    return mdata.get((), {}).get('selected', False)


def get_selected_streams(catalog):
    selected_stream_names = []
    for stream in catalog.streams:
        mdata = metadata.to_map(stream.metadata)
        if stream_is_selected(mdata):
            selected_stream_names.append(stream.tap_stream_id)
    return selected_stream_names


class DependencyException(Exception):
    pass


def populate_class_schemas(catalog, selected_stream_names):
    for stream in catalog.streams:
        if stream.tap_stream_id in selected_stream_names:
            STREAMS[stream.tap_stream_id].stream = stream


def ensure_credentials_are_authorized(client):
    client.is_authorized()


def do_sync(client, catalog, state):
    ensure_credentials_are_authorized(client)
    selected_stream_names = get_selected_streams(catalog)

    for stream in catalog.streams:
        stream_name = stream.tap_stream_id

        mdata = metadata.to_map(stream.metadata)

        if stream_name not in selected_stream_names:
            LOGGER.info("%s: Skipping - not selected", stream_name)
            continue

        key_properties = metadata.get(mdata, (), 'table-key-properties')
        singer.write_schema(stream_name, stream.schema.to_dict(), key_properties)

        LOGGER.info("%s: Starting sync", stream_name)
        instance = STREAMS[stream_name](client)
        instance.stream = stream
        counter_value = sync_stream(state, instance)
        singer.write_state(state)
        LOGGER.info("%s: Completed sync (%s rows)", stream_name, counter_value)

    singer.write_state(state)
    LOGGER.info("Finished sync")


@singer.utils.handle_top_exception(LOGGER)
def main():
    parsed_args = singer.utils.parse_args(REQUIRED_CONFIG_KEYS)

    pc_numbers = parsed_args.config['pc_numbers']
    if isinstance(pc_numbers, str):
        pc_numbers = [pc_numbers]

    mapping = load_pc_to_guid_mapping()
    locations = resolve_locations(pc_numbers, mapping)

    Context.config = parsed_args.config

    if parsed_args.discover:
        first_location = locations[0]
        creds = {
            "client_id": parsed_args.config['client_id'],
            "client_secret": parsed_args.config['client_secret'],
            "location_guid": first_location['restaurant_guid'],
            "management_group_guid": first_location['management_group_guid'],
            "start_date": parsed_args.config['start_date']
        }
        client = Toast(**creds)
        do_discover(client)
    elif parsed_args.catalog:
        state = parsed_args.state or {}

        for location in locations:
            location_guid = location['restaurant_guid']
            management_group_guid = location['management_group_guid']
            pc_number = location['pc_number']

            LOGGER.info("Syncing location: %s (%s)", location.get('location_name', pc_number), pc_number)

            Context.location_guid = location_guid

            creds = {
                "client_id": parsed_args.config['client_id'],
                "client_secret": parsed_args.config['client_secret'],
                "location_guid": location_guid,
                "management_group_guid": management_group_guid,
                "start_date": parsed_args.config['start_date']
            }

            client = Toast(**creds)
            do_sync(client, parsed_args.catalog, state)

        singer.write_state(state)

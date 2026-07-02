#!/usr/bin/env python3
"""
Wrapper script for tap-toast that handles state persistence.

This script:
1. Reads the last state from a state file
2. Runs tap-toast with that state
3. Streams output in real-time to the output file
4. Deduplicates records on resume using GUID tracking
5. Tracks the latest STATE message for state persistence
6. Updates the state file after sync completes or crashes
7. On resume, appends to the output file from where it left off

Usage:
    python sync_with_state.py --config config.json --catalog catalog.json --state state.json --output output.jsonl
"""

import argparse
import json
import subprocess
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_state(state_file):
    """Load state from file, return empty dict if file doesn't exist or is empty."""
    if os.path.exists(state_file) and os.path.getsize(state_file) > 0:
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
                logger.info(f"Loaded state from {state_file}")
                return state
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load state from {state_file}: {e}")
            return {}
    logger.info(f"No state file found at {state_file}, starting fresh")
    return {}


def save_state(state_file, state):
    """Save state to file atomically."""
    tmp_file = state_file + '.tmp'
    try:
        with open(tmp_file, 'w') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_file, state_file)
        logger.info(f"State saved to {state_file}")
    except IOError as e:
        logger.error(f"Failed to save state to {state_file}: {e}")
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        raise


def load_existing_guids(output_file):
    """Load all record GUIDs from existing output file for deduplication on resume."""
    guids = set()
    if not os.path.exists(output_file):
        return guids

    file_size = os.path.getsize(output_file)
    if file_size == 0:
        return guids

    logger.info(f"Loading existing record GUIDs from {output_file} for deduplication...")

    with open(output_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
                if isinstance(message, dict) and message.get('type') == 'RECORD':
                    record = message.get('record', {})
                    guid = record.get('guid')
                    if guid:
                        guids.add(guid)
            except json.JSONDecodeError:
                continue

    logger.info(f"Loaded {len(guids)} existing record GUIDs for deduplication")
    return guids


def run_tap(config_file, catalog_file, state_file, output_file=None):
    """Run tap-toast with state persistence and real-time output streaming."""

    state = load_state(state_file)

    state_input_file = state_file + '.input'
    save_state(state_input_file, state)

    cmd = [
        'tap-toast',
        '--config', config_file,
        '--catalog', catalog_file,
        '--state', state_input_file
    ]

    logger.info(f"Running: {' '.join(cmd)}")

    output_fp = None
    record_count = 0
    duplicate_count = 0
    last_state = None
    existing_guids = set()

    try:
        if output_file:
            os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
            existing_guids = load_existing_guids(output_file)
            output_fp = open(output_file, 'a')
            logger.info(f"Streaming output to {output_file}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True
        )

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
                if isinstance(message, dict) and message.get('type') == 'STATE':
                    logger.info("STATE message received (has bookmarks: %s)", 'bookmarks' in message.get('value', {}))
                    last_state = message.get('value')
                    if last_state is not None:
                        save_state(state_file, last_state)
                elif isinstance(message, dict) and message.get('type') == 'RECORD':
                    record = message.get('record', {})
                    guid = record.get('guid')

                    if guid and guid in existing_guids:
                        duplicate_count += 1
                        continue

                    if guid:
                        existing_guids.add(guid)

                    if output_fp:
                        output_fp.write(line + '\n')
                        output_fp.flush()
                        record_count += 1
                    else:
                        print(line)
                else:
                    if output_fp:
                        output_fp.write(line + '\n')
                        output_fp.flush()
                        record_count += 1
                    else:
                        print(line)
            except json.JSONDecodeError:
                if output_fp:
                    output_fp.write(line + '\n')
                    output_fp.flush()
                    record_count += 1
                else:
                    print(line)

        proc.wait()

        if output_fp:
            output_fp.flush()
            logger.info(f"Output streamed to {output_file} ({record_count} new records, {duplicate_count} duplicates skipped)")

        if last_state is not None:
            save_state(state_file, last_state)
        else:
            logger.warning("No STATE message found in output")

        if proc.returncode != 0:
            logger.error(f"Tap exited with code {proc.returncode}")
            if os.path.exists(state_input_file):
                os.remove(state_input_file)
            raise subprocess.CalledProcessError(proc.returncode, cmd)

        if os.path.exists(state_input_file):
            os.remove(state_input_file)

        logger.info("Sync completed successfully")

    except subprocess.TimeoutExpired:
        logger.error("Tap timed out after 24 hours")
        proc.kill()
        if output_fp:
            output_fp.close()
        if os.path.exists(state_input_file):
            os.remove(state_input_file)
        raise
    except Exception as e:
        logger.error(f"Failed to run tap: {e}")
        if output_fp:
            output_fp.close()
        if os.path.exists(state_input_file):
            os.remove(state_input_file)
        raise
    finally:
        if last_state is not None:
            save_state(state_file, last_state)
        if output_fp and not output_fp.closed:
            output_fp.close()


def main():
    parser = argparse.ArgumentParser(description='Run tap-toast with state persistence')
    parser.add_argument('--config', required=True, help='Path to config.json')
    parser.add_argument('--catalog', required=True, help='Path to catalog.json')
    parser.add_argument('--state', required=True, help='Path to state.json (will be created/updated)')
    parser.add_argument('--output', help='Path to output file (defaults to stdout)')

    args = parser.parse_args()

    run_tap(args.config, args.catalog, args.state, args.output)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Wrapper script for tap-toast that handles state persistence.

This script:
1. Reads the last state from a state file
2. Runs tap-toast with that state
3. Captures all output (RECORD, SCHEMA, STATE messages)
4. Extracts the final STATE message
5. Updates the state file with the latest state
6. Writes non-STATE messages to stdout for downstream targets

Usage:
    python sync_with_state.py --config config.json --catalog catalog.json --state state.json --output output.jsonl
"""

import argparse
import json
import subprocess
import sys
import os
import logging
import threading

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


def parse_singer_messages(output_lines):
    """Parse Singer messages from output lines, separating STATE from other messages."""
    records_and_schemas = []
    last_state = None

    for line in output_lines:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
            if isinstance(message, dict) and message.get('type') == 'STATE':
                last_state = message.get('value')
            else:
                records_and_schemas.append(line)
        except json.JSONDecodeError:
            records_and_schemas.append(line)

    return records_and_schemas, last_state


def stream_output(proc, output_lines):
    """Stream stderr output in real-time for progress visibility."""
    for line in proc.stderr:
        line = line.strip()
        if line:
            print(line, file=sys.stderr, flush=True)


def run_tap(config_file, catalog_file, state_file, output_file=None):
    """Run tap-toast with state persistence."""

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

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stderr_thread = threading.Thread(target=stream_output, args=(proc, []))
        stderr_thread.start()

        stdout_data, _ = proc.communicate(timeout=86400)
        stderr_thread.join(timeout=5)

        all_lines = stdout_data.split('\n')
        records_and_schemas, new_state = parse_singer_messages(all_lines)

        if proc.returncode != 0:
            logger.error(f"Tap exited with code {proc.returncode}")
            if new_state:
                save_state(state_file, new_state)
            raise subprocess.CalledProcessError(proc.returncode, cmd)

        if new_state:
            save_state(state_file, new_state)
        else:
            logger.warning("No STATE message found in output")

        if output_file:
            os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
            with open(output_file, 'w') as f:
                for line in records_and_schemas:
                    f.write(line + '\n')
            logger.info(f"Output written to {output_file}")
        else:
            for line in records_and_schemas:
                print(line)

        if os.path.exists(state_input_file):
            os.remove(state_input_file)

        logger.info("Sync completed successfully")

    except subprocess.TimeoutExpired:
        logger.error("Tap timed out after 24 hours")
        proc.kill()
        if os.path.exists(state_input_file):
            os.remove(state_input_file)
        raise
    except Exception as e:
        logger.error(f"Failed to run tap: {e}")
        if os.path.exists(state_input_file):
            os.remove(state_input_file)
        raise


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

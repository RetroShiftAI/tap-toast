
import sys
import logging
import singer
import singer.metrics as metrics
from singer import metadata
from singer import Transformer

logger = logging.getLogger(__name__)

STATE_WRITE_INTERVAL = 1000


def sync_stream(state, instance):
    stream = instance.stream

    with metrics.record_counter(stream.tap_stream_id) as counter:
        for (stream, record) in instance.sync(state):
            counter.increment()

            with Transformer() as transformer:
                record = transformer.transform(record, stream.schema.to_dict(), metadata.to_map(stream.metadata))

            singer.write_record(stream.tap_stream_id, record)

            if counter.value % STATE_WRITE_INTERVAL == 0:
                logger.info('%s: Processed %s records', stream.tap_stream_id, counter.value)
                singer.write_state(state)

        if instance.replication_method == "INCREMENTAL":
            singer.write_state(state)

        logger.info('%s: Total records synced: %s', stream.tap_stream_id, counter.value)

        return counter.value

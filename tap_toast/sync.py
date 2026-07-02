
import sys
import singer
import singer.metrics as metrics
from singer import metadata
from singer import Transformer


def sync_stream(state, instance):
    stream = instance.stream

    with metrics.record_counter(stream.tap_stream_id) as counter:
        for (stream, record) in instance.sync(state):
            counter.increment()

            with Transformer() as transformer:
                record = transformer.transform(record, stream.schema.to_dict(), metadata.to_map(stream.metadata))

            singer.write_record(stream.tap_stream_id, record)

            if counter.value % 1000 == 0:
                singer.logger.info('%s: Processed %s records', stream.tap_stream_id, counter.value)

        if instance.replication_method == "INCREMENTAL":
            singer.write_state(state)

        singer.logger.info('%s: Total records synced: %s', stream.tap_stream_id, counter.value)

        return counter.value

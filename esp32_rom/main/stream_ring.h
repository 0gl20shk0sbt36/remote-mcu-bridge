/* Bounded fixed-record log storage. Caller serializes access; no allocation/I/O. */
#ifndef STREAM_RING_H
#define STREAM_RING_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define STREAM_RECORDS 32
#define STREAM_RECORD_BYTES 256
struct stream_record {
    size_t length;
    char bytes[STREAM_RECORD_BYTES];
};
struct stream_ring {
    uint64_t next;
    struct stream_record records[STREAM_RECORDS];
};
_Static_assert(sizeof(struct stream_ring) <= 16384, "log ring RAM limit");
static void stream_push(struct stream_ring *r, const char *bytes, size_t n) {
    struct stream_record *p = &r->records[r->next % STREAM_RECORDS];
    if (n > STREAM_RECORD_BYTES)
        n = STREAM_RECORD_BYTES;
    memcpy(p->bytes, bytes, n);
    p->length = n;
    ++r->next;
}
/* Returns copied length; advances cursor and explicitly counts overwritten records. */
static size_t stream_pop(struct stream_ring *r, uint64_t *cursor, char *bytes, uint64_t *lost) {
    uint64_t oldest = r->next > STREAM_RECORDS ? r->next - STREAM_RECORDS : 0;
    *lost = 0;
    if (*cursor < oldest) {
        *lost = oldest - *cursor;
        *cursor = oldest;
    }
    if (*cursor >= r->next)
        return 0;
    struct stream_record *p = &r->records[(*cursor)++ % STREAM_RECORDS];
    memcpy(bytes, p->bytes, p->length);
    return p->length;
}
static int stream_baud_allowed(unsigned baud) {
    return baud == 9600 || baud == 19200 || baud == 38400 || baud == 57600 || baud == 115200;
}
#endif

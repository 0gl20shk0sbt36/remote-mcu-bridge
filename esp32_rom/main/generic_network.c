/* Base-owned, protocol-neutral authenticated TCP listeners (ABI4). */
#include "generic_network.h"
#include "rmb/port.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <unistd.h>
#include "lwip/inet.h"

extern bool module_runtime_code_pointer(const void *);
extern bool module_runtime_ready(void);
extern bool authenticated(const char *);
extern bool socket_exact(int, void *, size_t, bool, int64_t);

#define LISTENERS 4u
#define HEADER_MAX 2048u
#define TOKEN_MAX 256u
#define AUTH_US 10000000LL
#define IO_US 5000000LL
#define GENERATION_MAX ((uint32_t)(INT32_MAX - LISTENERS) / LISTENERS)
enum { COLD, INIT, COMMITTED, ABORTED };
static atomic_int phase;
static rmb_task_t init_task;
static unsigned listener_count;
static bool registration_failed;
typedef struct {
    int listener, fd;
    uint16_t port;
    uint32_t flags, generation;
    bool active;
    rmb_connection_fn handler;
    void *context;
    rmb_task_t task;
    rmb_mutex_t lock;
    unsigned char replay[HEADER_MAX];
    size_t replay_size, replay_pos;
} connection_slot;
static connection_slot slots[LISTENERS];

/* Strict bounded JSON recognizer. Unlike a C-string JSON parser, it rejects
 * embedded/escaped NUL anywhere, including names and ignored nested fields. */
typedef struct {
    const unsigned char *p, *end;
    unsigned depth, tokens;
    char token[TOKEN_MAX + 1];
} parser;
static void space(parser *p) {
    while (p->p < p->end && (*p->p == ' ' || *p->p == '\t' || *p->p == '\r' || *p->p == '\n'))
        ++p->p;
}
static int hex(unsigned char c) {
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}
static bool unicode(parser *p, uint32_t *out) {
    if ((size_t)(p->end - p->p) < 4)
        return false;
    uint32_t v = 0;
    for (unsigned i = 0; i < 4; ++i) {
        int h = hex(*p->p++);
        if (h < 0)
            return false;
        v = v * 16 + (unsigned)h;
    }
    *out = v;
    return true;
}
static bool string(parser *p, char *out, size_t cap) {
    if (p->p == p->end || *p->p++ != '"')
        return false;
    size_t n = 0;
    while (p->p < p->end) {
        uint32_t c = *p->p++;
        if (c == '"') {
            if (out)
                out[n] = 0;
            return true;
        }
        if (c < 32)
            return false;
        if (c == '\\') {
            if (p->p == p->end)
                return false;
            c = *p->p++;
            switch (c) {
            case '"':
            case '\\':
            case '/':
                break;
            case 'b':
                c = 8;
                break;
            case 'f':
                c = 12;
                break;
            case 'n':
                c = 10;
                break;
            case 'r':
                c = 13;
                break;
            case 't':
                c = 9;
                break;
            case 'u': {
                if (!unicode(p, &c) || !c)
                    return false;
                if (c >= 0xd800 && c <= 0xdbff) {
                    if (p->end - p->p < 6 || p->p[0] != '\\' || p->p[1] != 'u')
                        return false;
                    p->p += 2;
                    uint32_t low;
                    if (!unicode(p, &low) || low < 0xdc00 || low > 0xdfff)
                        return false;
                    c = 0x10000 + ((c - 0xd800) << 10) + (low - 0xdc00);
                } else if (c >= 0xdc00 && c <= 0xdfff)
                    return false;
                break;
            }
            default:
                return false;
            }
        } else if (c >= 128) {
            /* Decode raw UTF-8 to reject overlong, surrogate and invalid forms. */
            unsigned extra;
            uint32_t minimum;
            if (c >= 0xc2 && c <= 0xdf) {
                extra = 1;
                minimum = 0x80;
                c &= 31;
            } else if (c >= 0xe0 && c <= 0xef) {
                extra = 2;
                minimum = 0x800;
                c &= 15;
            } else if (c >= 0xf0 && c <= 0xf4) {
                extra = 3;
                minimum = 0x10000;
                c &= 7;
            } else
                return false;
            if ((size_t)(p->end - p->p) < extra)
                return false;
            while (extra--) {
                unsigned b = *p->p++;
                if ((b & 0xc0) != 0x80)
                    return false;
                c = (c << 6) | (b & 63);
            }
            if (c < minimum || c > 0x10ffff || (c >= 0xd800 && c <= 0xdfff))
                return false;
        }
        unsigned char bytes[4];
        unsigned count;
        if (c < 0x80) {
            bytes[0] = c;
            count = 1;
        } else if (c < 0x800) {
            bytes[0] = 0xc0 | (c >> 6);
            bytes[1] = 0x80 | (c & 63);
            count = 2;
        } else if (c < 0x10000) {
            bytes[0] = 0xe0 | (c >> 12);
            bytes[1] = 0x80 | ((c >> 6) & 63);
            bytes[2] = 0x80 | (c & 63);
            count = 3;
        } else {
            bytes[0] = 0xf0 | (c >> 18);
            bytes[1] = 0x80 | ((c >> 12) & 63);
            bytes[2] = 0x80 | ((c >> 6) & 63);
            bytes[3] = 0x80 | (c & 63);
            count = 4;
        }
        if (out) {
            if (n + count >= cap)
                return false;
            memcpy(out + n, bytes, count);
            n += count;
        }
    }
    return false;
}
static bool token_key(const unsigned char *start, const unsigned char *end) {
    parser key = {.p = start, .end = end};
    char name[6];
    return string(&key, name, sizeof name) && !strcmp(name, "token");
}
static bool value(parser *p);
static bool compound(parser *p, bool object, bool top) {
    if (++p->depth > 16)
        return false;
    ++p->p;
    space(p);
    unsigned char end = object ? '}' : ']';
    if (p->p < p->end && *p->p == end) {
        ++p->p;
        --p->depth;
        return true;
    }
    for (;;) {
        bool token = false;
        if (object) {
            /* Validate arbitrary names without a header-sized recursive stack.
             * A second tiny decoding pass recognizes only the exact token key. */
            const unsigned char *start = p->p;
            if (!string(p, NULL, 0))
                return false;
            token = top && token_key(start, p->p);
            space(p);
            if (p->p == p->end || *p->p++ != ':')
                return false;
            space(p);
        }
        if (token) {
            if (++p->tokens != 1 || !string(p, p->token, sizeof p->token))
                return false;
        } else if (!value(p))
            return false;
        space(p);
        if (p->p == p->end)
            return false;
        unsigned char c = *p->p++;
        if (c == end) {
            --p->depth;
            return true;
        }
        if (c != ',')
            return false;
        space(p);
    }
}
static bool value(parser *p) {
    space(p);
    if (p->p == p->end)
        return false;
    unsigned char c = *p->p;
    if (c == '"')
        return string(p, NULL, 0);
    if (c == '{' || c == '[')
        return compound(p, c == '{', false);
    const char *lit = c == 't' ? "true" : c == 'f' ? "false" : c == 'n' ? "null" : NULL;
    if (lit) {
        size_t n = strlen(lit);
        if ((size_t)(p->end - p->p) < n || memcmp(p->p, lit, n))
            return false;
        p->p += n;
        return true;
    }
    if (c == '-') {
        ++p->p;
        if (p->p == p->end)
            return false;
    }
    if (*p->p == '0')
        ++p->p;
    else {
        if (*p->p < '1' || *p->p > '9')
            return false;
        do {
            ++p->p;
        } while (p->p < p->end && *p->p >= '0' && *p->p <= '9');
    }
    if (p->p < p->end && *p->p == '.') {
        ++p->p;
        const unsigned char *s = p->p;
        while (p->p < p->end && *p->p >= '0' && *p->p <= '9')
            ++p->p;
        if (s == p->p)
            return false;
    }
    if (p->p < p->end && (*p->p == 'e' || *p->p == 'E')) {
        ++p->p;
        if (p->p < p->end && (*p->p == '+' || *p->p == '-'))
            ++p->p;
        const unsigned char *s = p->p;
        while (p->p < p->end && *p->p >= '0' && *p->p <= '9')
            ++p->p;
        if (s == p->p)
            return false;
    }
    return true;
}
static bool auth_header(connection_slot *s) {
    /* Keep the raw line separate from decoded validation: legacy services must
     * see byte-for-byte replay, not JSON reserialization or a second auth ACK. */
    size_t n = 0;
    int64_t deadline = rmb_port_now_us() + AUTH_US;
    while (n < HEADER_MAX) {
        if (!socket_exact(s->fd, s->replay + n, 1, false, deadline) || !s->replay[n])
            return false;
        if (s->replay[n++] == '\n')
            break;
    }
    if (!n || s->replay[n - 1] != '\n')
        return false;
    parser p = {.p = s->replay, .end = s->replay + n};
    space(&p);
    if (p.p == p.end || *p.p != '{' || !compound(&p, true, true))
        return false;
    space(&p);
    if (p.p != p.end || p.tokens != 1 || rmb_port_now_us() >= deadline ||
        !authenticated(p.token))
        return false;
    s->replay_pos = 0;
    s->replay_size = (s->flags == RMB_NET_AUTH_REPLAY) ? n : 0;
    return true;
}
static uint32_t ticks_left(int64_t deadline) {
    int64_t left = deadline - rmb_port_now_us();
    if (left <= 0)
        return 0;
    /* Round down: lock waiting must not exceed the supplied deadline. */
    uint64_t ticks = (uint64_t)RMB_MS_TO_TICKS(left / 1000);
    return ticks > RMB_WAIT_FOREVER - 1 ? RMB_WAIT_FOREVER - 1 : (uint32_t)ticks;
}
static connection_slot *acquire(rmb_connection handle, bool nonblocking, int64_t deadline,
                                int *failure) {
    /* Recheck generation under the I/O lock so a recycled socket cannot revive
     * a capability borrowed by a previous callback. Caller releases the lock. */
    if (failure)
        *failure = RMB_IO_ERROR;
    if (handle <= 0)
        return NULL;
    uint32_t raw = (uint32_t)handle - 1, index = raw % LISTENERS, generation = raw / LISTENERS;
    connection_slot *s = &slots[index];
    if (!s->lock)
        return NULL;
    if (rmb_port_mutex_take(s->lock, nonblocking ? 0 : ticks_left(deadline))) {
        if (failure && nonblocking)
            *failure = RMB_IO_AGAIN;
        return NULL;
    }
    if (!s->active || s->generation != generation) {
        rmb_port_mutex_give(s->lock);
        return NULL;
    }
    return s;
}
static bool net_exact(rmb_connection h, void *buffer, size_t size, bool writing, int64_t deadline) {
    if ((size && !buffer) || deadline <= rmb_port_now_us())
        return false;
    connection_slot *s = acquire(h, false, deadline, NULL);
    if (!s)
        return false;
    unsigned char *p = buffer;
    if (!writing && size) {
        size_t n = s->replay_size - s->replay_pos;
        if (n > size)
            n = size;
        memcpy(p, s->replay + s->replay_pos, n);
        s->replay_pos += n;
        p += n;
        size -= n;
    }
    bool ok = rmb_port_now_us() < deadline &&
              (!size || socket_exact(s->fd, p, size, writing, deadline));
    ok = ok && rmb_port_now_us() < deadline;
    rmb_port_mutex_give(s->lock);
    return ok;
}
static int transfer(rmb_connection h, void *buffer, size_t size, bool writing, bool nonblocking) {
    if ((size && !buffer) || size > INT_MAX)
        return RMB_IO_ERROR;
    int64_t deadline = rmb_port_now_us() + IO_US;
    int failure = RMB_IO_ERROR;
    connection_slot *s = acquire(h, nonblocking, deadline, &failure);
    if (!s)
        return failure;
    int result = RMB_IO_ERROR;
    if (!size) {
        result = 0;
        goto done;
    }
    if (!writing && s->replay_pos < s->replay_size) {
        size_t n = s->replay_size - s->replay_pos;
        if (n > size)
            n = size;
        memcpy(buffer, s->replay + s->replay_pos, n);
        s->replay_pos += n;
        result = (int)n;
        goto done;
    }
    for (;;) {
        if (!nonblocking) {
            int64_t left = deadline - rmb_port_now_us();
            if (left <= 0)
                break;
            struct timeval tv = {.tv_sec = left / 1000000, .tv_usec = left % 1000000};
            fd_set fds;
            FD_ZERO(&fds);
            FD_SET(s->fd, &fds);
            int r = select(s->fd + 1, writing ? NULL : &fds, writing ? &fds : NULL, NULL, &tv);
            if (r < 0 && errno == EINTR)
                continue;
            if (r <= 0)
                break;
        }
        result = writing ? (int)send(s->fd, buffer, size, 0) : (int)recv(s->fd, buffer, size, 0);
        if (result >= 0) {
            if (writing && result == 0)
                result = RMB_IO_ERROR;
            break;
        }
        if (errno == EINTR) {
            if (nonblocking) {
                result = RMB_IO_AGAIN;
                break;
            }
            continue;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            if (!nonblocking)
                continue;
            result = RMB_IO_AGAIN;
        } else
            result = RMB_IO_ERROR;
        break;
    }
done:
    rmb_port_mutex_give(s->lock);
    return result;
}
static int net_read(rmb_connection h, void *b, size_t n, bool nb) {
    return transfer(h, b, n, false, nb);
}
static int net_write(rmb_connection h, const void *b, size_t n, bool nb) {
    return transfer(h, (void *)b, n, true, nb);
}
static bool nonblock(int fd) {
    int f = fcntl(fd, F_GETFL, 0);
    return f >= 0 && fcntl(fd, F_SETFL, f | O_NONBLOCK) >= 0;
}
static void listener_worker(void *arg) {
    connection_slot *s = arg;
    rmb_port_task_wait_notify();
    while (atomic_load(&phase) == COMMITTED) {
        if (!module_runtime_ready()) {
            rmb_port_delay_ticks(RMB_MS_TO_TICKS(20) + 1);
            continue;
        }
        struct timeval tv = {.tv_sec = 0, .tv_usec = 100000};
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(s->listener, &fds);
        if (select(s->listener + 1, &fds, NULL, NULL, &tv) <= 0)
            continue;
        int fd = accept(s->listener, NULL, NULL);
        if (fd < 0)
            continue;
        if (fd >= FD_SETSIZE || !nonblock(fd) || s->generation == GENERATION_MAX) {
            close(fd);
            if (s->generation == GENERATION_MAX) {
                /* Permanently retire an exhausted listener rather than wrap. */
                close(s->listener);
                s->listener = -1;
                break;
            }
            continue;
        }
        s->fd = fd;
        if (auth_header(s) && atomic_load(&phase) == COMMITTED && module_runtime_ready()) {
            rmb_port_mutex_take(s->lock, RMB_WAIT_FOREVER);
            ++s->generation;
            s->active = true;
            rmb_connection h =
                (rmb_connection)(s->generation * LISTENERS + (unsigned)(s - slots) + 1);
            rmb_port_mutex_give(s->lock);
            s->handler(h, s->context);
            /* ESP-IDF reports the minimum unused stack in BYTES. Include the
             * complete service/driver/libc call chain, not just its C frame. */
            RMB_LOGI("rmb-net", "port=%u stack_min_free_bytes=%u", (unsigned)s->port,
                     (unsigned)rmb_port_task_stack_min_free());
            /* Synchronous supervised jobs are complete before callback returns. */
            rmb_port_mutex_take(s->lock, RMB_WAIT_FOREVER);
            s->active = false;
            close(fd);
            s->fd = -1;
            rmb_port_mutex_give(s->lock);
        } else {
            close(fd);
            s->fd = -1;
        }
    }
    rmb_port_task_end();
}
static int try_listen(uint16_t port, uint32_t flags, rmb_connection_fn handler, void *context) {
    if (atomic_load(&phase) != INIT || rmb_port_task_current() != init_task || !port ||
        port == 3335 || port == 3337 || flags > RMB_NET_AUTH_REPLAY || !handler ||
        !module_runtime_code_pointer((const void *)handler) || listener_count == LISTENERS)
        return -1;
    for (unsigned i = 0; i < listener_count; ++i)
        if (slots[i].port == port)
            return -1;
    int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0)
        return -1;
    struct sockaddr_in addr = {
        .sin_family = AF_INET, .sin_port = htons(port), .sin_addr = {.s_addr = htonl(INADDR_ANY)}};
    if (fd >= FD_SETSIZE || !nonblock(fd) || bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0 ||
        listen(fd, 2) < 0) {
        close(fd);
        return -1;
    }
    connection_slot *s = &slots[listener_count];
    if (rmb_port_mutex_create(&s->lock)) {
        close(fd);
        return -1;
    }
    s->listener = fd;
    s->fd = -1;
    s->port = port;
    s->flags = flags;
    s->handler = handler;
    s->context = context;
    ++listener_count;
    return 0;
}
static int net_listen(uint16_t port, uint32_t flags, rmb_connection_fn handler, void *context) {
    int rc = try_listen(port, flags, handler, context);
    if (rc && atomic_load(&phase) == INIT && rmb_port_task_current() == init_task)
        registration_failed = true;
    return rc;
}
const rmb_net_api rmb_network_api = {.abi = RMB_SERVICE_ABI,
                                     .size = sizeof(rmb_net_api),
                                     .listen = net_listen,
                                     .exact = net_exact,
                                     .read = net_read,
                                     .write = net_write,
                                     .authenticated = authenticated};
void rmb_network_init_begin(void) {
    int expected = COLD;
    if (atomic_compare_exchange_strong(&phase, &expected, INIT))
        init_task = rmb_port_task_current();
}
void rmb_network_init_abort(void) {
    int state = atomic_exchange(&phase, ABORTED);
    if (state == ABORTED || state == COLD)
        return;
    /* Only the initializer calls abort, before runtime readiness. Workers have
     * not entered any service code; delete them before closing their sockets. */
    for (unsigned i = 0; i < listener_count; ++i) {
        connection_slot *s = &slots[i];
        if (s->task) {
            rmb_port_task_terminate(s->task);
            s->task = NULL;
        }
        if (s->listener >= 0) {
            close(s->listener);
            s->listener = -1;
        }
        if (s->fd >= 0) {
            close(s->fd);
            s->fd = -1;
        }
        s->active = false;
        if (s->lock) {
            vSemaphoreDelete(s->lock);
            s->lock = NULL;
        }
    }
}
bool rmb_network_init_commit(void) {
    /* Publish only after all workers exist. Notifications hold workers behind
     * this barrier; runtime readiness separately gates entry into module code. */
    if (atomic_load(&phase) != INIT || rmb_port_task_current() != init_task)
        return false;
    if (registration_failed) {
        rmb_network_init_abort();
        return false;
    }
    for (unsigned i = 0; i < listener_count; ++i) {
        if (rmb_port_task_create("rmb-listener", listener_worker, &slots[i],
                                 RMB_CONFIG_LISTENER_STACK_BYTES, 5, &slots[i].task)) {
            rmb_network_init_abort();
            return false;
        }
    }
    atomic_store(&phase, COMMITTED);
    for (unsigned i = 0; i < listener_count; ++i)
        rmb_port_task_notify(slots[i].task);
    return true;
}

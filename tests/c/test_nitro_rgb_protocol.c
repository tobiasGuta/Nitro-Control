/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../experiments/acer-wmi-rgb-enable-poc/nitro_rgb_protocol.h"

#define MAX_CALLS 64
#define MAX_LOGS  64

struct mock_reply {
	int transport_ok;
	int allocate_buf;
	struct nitro_acpi_obj obj;
	u8 buf_storage[32];
	size_t align_offset;
};

struct mock_call_record {
	u32 method_id;
	u8 in_buf[16];
	size_t in_len;
	enum nitro_resp_contract contract;
};

struct mock_ctx {
	struct mock_call_record calls[MAX_CALLS];
	size_t n_calls;

	/* Scripted method-specific callbacks / queues */
	struct mock_reply (*handler)(struct mock_ctx *ctx, u32 method_id,
				     const u8 *in_buf, size_t in_len,
				     enum nitro_resp_contract contract);

	/* Simulated firmware registers */
	u8 zones[4][3];
	u8 mode;
	u8 speed;
	u8 brightness;
	u8 gm_return;
	u8 direction;
	u8 effect_rgb[3];

	/* Fault injection switches */
	int fail_method2;
	int fail_method5;
	int fail_method6_idx; /* 1..4 to fail that zone write */
	int fail_method20;
	int fail_method21;
	int fail_method7_idx; /* 1..4 to override zone 7 reply */
	struct mock_reply override_m2;
	struct mock_reply override_m6;
	struct mock_reply override_m7;
	struct mock_reply override_m20;
	struct mock_reply override_m21;
	int use_override_m2;
	int use_override_m6;
	int use_override_m7;
	int use_override_m20;
	int use_override_m21;

	/* Leak detector for simulated ACPI_ALLOCATE_BUFFER */
	int allocs;
	int frees;

	/* Captured logs */
	char logs[MAX_LOGS][256];
	int is_err[MAX_LOGS];
	size_t n_logs;
	unsigned int slept_ms;
};

static int zone_to_idx(u8 z)
{
	switch (z) {
	case 1: return 0;
	case 2: return 1;
	case 4: return 2;
	case 8: return 3;
	default: return -1;
	}
}

static struct mock_reply make_int_reply(u64 v, int allocate)
{
	struct mock_reply r;
	memset(&r, 0, sizeof(r));
	r.transport_ok = 1;
	r.allocate_buf = allocate;
	r.obj.type = NITRO_ACPI_INTEGER;
	r.obj.integer_value = v;
	return r;
}

static struct mock_reply make_buf_reply(const u8 *data, size_t len, size_t offset, int allocate)
{
	struct mock_reply r;
	memset(&r, 0, sizeof(r));
	r.transport_ok = 1;
	r.allocate_buf = allocate;
	r.obj.type = NITRO_ACPI_BUFFER;
	assert(offset + len <= sizeof(r.buf_storage));
	if (data && len)
		memcpy(r.buf_storage + offset, data, len);
	r.align_offset = offset;
	r.obj.buffer_len = len;
	return r;
}

static struct mock_reply default_fw_handler(struct mock_ctx *m, u32 method_id,
					    const u8 *in_buf, size_t in_len,
					    enum nitro_resp_contract contract)
{
	struct mock_reply r;
	(void)contract;
	memset(&r, 0, sizeof(r));
	r.transport_ok = 1;
	r.allocate_buf = 0;

	if (method_id == ACER_WMID_GET_GAMING_SYS_INFO_METHODID) {
		if (m->fail_method5)
			r.transport_ok = 0;
		else
			r = make_int_reply(0x1234ULL, 1);
		return r;
	}
	if (method_id == ACER_WMID_SET_GAMING_LED_METHODID) {
		if (m->use_override_m2)
			return m->override_m2;
		if (m->fail_method2) {
			r.transport_ok = 0;
			r.allocate_buf = 1; /* Ensure failure path also frees allocated buffer */
			return r;
		}
		return make_int_reply(0ULL, 1);
	}
	if (method_id == ACER_WMID_SET_GAMING_STATIC_LED_METHODID) {
		if (m->use_override_m6)
			return m->override_m6;
		assert(in_len == 8);
		int zidx = zone_to_idx(in_buf[0]);
		assert(zidx >= 0);
		if (m->fail_method6_idx == zidx + 1) {
			r.transport_ok = 0;
			return r;
		}
		m->zones[zidx][0] = in_buf[1];
		m->zones[zidx][1] = in_buf[2];
		m->zones[zidx][2] = in_buf[3];
		return make_int_reply(0ULL, 1);
	}
	if (method_id == ACER_WMID_GET_GAMING_RGB_KB_METHODID) {
		assert(in_len == 8);
		int zidx = zone_to_idx(in_buf[0]);
		assert(zidx >= 0);
		if (m->use_override_m7 && m->fail_method7_idx == zidx + 1)
			return m->override_m7;
		u32 w = nitro_zone_wire_u32(0, m->zones[zidx][0], m->zones[zidx][1], m->zones[zidx][2]);
		return make_int_reply((u64)w, 1);
	}
	if (method_id == ACER_WMID_SET_GAMING_KB_BACKLIGHT_METHODID) {
		if (m->use_override_m20)
			return m->override_m20;
		if (m->fail_method20) {
			r.transport_ok = 0;
			r.allocate_buf = 1;
			return r;
		}
		assert(in_len == 16);
		m->mode = in_buf[0];
		m->speed = in_buf[1];
		m->brightness = in_buf[2];
		m->direction = in_buf[4];
		m->effect_rgb[0] = in_buf[5];
		m->effect_rgb[1] = in_buf[6];
		m->effect_rgb[2] = in_buf[7];
		return make_int_reply(0ULL, 1);
	}
	if (method_id == ACER_WMID_GET_GAMING_KB_BACKLIGHT_METHODID) {
		if (m->use_override_m21)
			return m->override_m21;
		if (m->fail_method21) {
			r.transport_ok = 0;
			r.allocate_buf = 1;
			return r;
		}
		u8 raw[16];
		memset(raw, 0, sizeof(raw));
		raw[0] = m->gm_return;
		raw[1] = m->mode;
		raw[2] = m->speed;
		raw[3] = m->brightness;
		raw[5] = m->direction;
		raw[6] = m->effect_rgb[0];
		raw[7] = m->effect_rgb[1];
		raw[8] = m->effect_rgb[2];
		return make_buf_reply(raw, sizeof(raw), 1 /* test unaligned ACPI buffer */, 1);
	}
	r.transport_ok = 0;
	return r;
}

static int mock_eval_u64(void *vctx, u32 method_id, const u8 *in_buf, size_t in_len,
			 enum nitro_resp_contract contract, u64 *out_u64,
			 struct nitro_kb_state *out_state)
{
	struct mock_ctx *m = (struct mock_ctx *)vctx;
	struct mock_reply rep;
	void *heap_alloc = NULL;
	size_t heap_size = 0;
	u8 *heap_payload = NULL;
	size_t payload_size = 0;
	int ret;

	if (m->n_calls < MAX_CALLS) {
		m->calls[m->n_calls].method_id = method_id;
		m->calls[m->n_calls].in_len = in_len;
		m->calls[m->n_calls].contract = contract;
		if (in_buf && in_len <= 16)
			memcpy(m->calls[m->n_calls].in_buf, in_buf, in_len);
		m->n_calls++;
	}

	rep = (m->handler ? m->handler : default_fw_handler)(m, method_id, in_buf, in_len, contract);
	if (rep.allocate_buf) {
		heap_size = 64;
		heap_alloc = malloc(heap_size);
		assert(heap_alloc != NULL);
		memset(heap_alloc, 0x5A, heap_size);
		m->allocs++;
	}

	if (rep.obj.type == NITRO_ACPI_BUFFER && rep.obj.buffer_ptr == NULL && rep.obj.buffer_len > 0) {
		/* Keep NULL when explicitly testing NULL buffer_ptr! */
		if (rep.align_offset != (size_t)-1) {
			payload_size = rep.align_offset + rep.obj.buffer_len;
			heap_payload = (u8 *)malloc(payload_size);
			assert(heap_payload != NULL);
			memset(heap_payload, 0x3C, rep.align_offset);
			memcpy(heap_payload + rep.align_offset,
			       rep.buf_storage + rep.align_offset, rep.obj.buffer_len);
			rep.obj.buffer_ptr = heap_payload + rep.align_offset;
		}
	}

	ret = nitro_validate_acpi_reply(rep.transport_ok, &rep.obj, contract, out_u64, out_state);

	/*
	 * Userspace lifetime check: scramble and free heap-allocated mock
	 * payload after decoding to prove decoded outputs hold no dangling
	 * pointers. Actual kernel wmi_evaluate_method()/kfree() behavior is
	 * verified by source inspection and Kbuild W=1 compilation.
	 */
	if (heap_payload) {
		memset(heap_payload, 0xA5, payload_size);
		free(heap_payload);
	}
	if (heap_alloc) {
		memset(heap_alloc, 0xA5, heap_size);
		free(heap_alloc);
		m->frees++;
	}

	return ret;
}

static void mock_sleep(void *vctx, unsigned int ms)
{
	((struct mock_ctx *)vctx)->slept_ms += ms;
}

static void mock_log_info(void *vctx, const char *msg)
{
	struct mock_ctx *m = (struct mock_ctx *)vctx;
	if (m->n_logs < MAX_LOGS) {
		snprintf(m->logs[m->n_logs], sizeof(m->logs[0]), "%s", msg);
		m->is_err[m->n_logs] = 0;
		m->n_logs++;
	}
}

static void mock_log_err(void *vctx, const char *msg)
{
	struct mock_ctx *m = (struct mock_ctx *)vctx;
	if (m->n_logs < MAX_LOGS) {
		snprintf(m->logs[m->n_logs], sizeof(m->logs[0]), "%s", msg);
		m->is_err[m->n_logs] = 1;
		m->n_logs++;
	}
}

static void init_mock(struct mock_ctx *m, struct nitro_wmi_ops *ops)
{
	memset(m, 0, sizeof(*m));
	m->brightness = 100;
	ops->ctx = m;
	ops->eval_u64 = mock_eval_u64;
	ops->sleep_ms = mock_sleep;
	ops->log_info = mock_log_info;
	ops->log_err = mock_log_err;
}

static int logs_contain(const struct mock_ctx *m, const char *needle)
{
	for (size_t i = 0; i < m->n_logs; ++i) {
		if (strstr(m->logs[i], needle))
			return 1;
	}
	return 0;
}

/* 1. Decoder contract unit tests: types, buffer lengths, NULL pointers, unaligned LE, cleanup */
static void test_decoder_contracts(void)
{
	u64 out = 0xdeadbeefULL;
	struct nitro_kb_state kb;
	struct nitro_acpi_obj obj;
	u8 unaligned[32];

	/* Absent response (NULL / NITRO_ACPI_NONE) */
	assert(nitro_validate_acpi_reply(1, NULL, NITRO_RESP_OPTIONAL_ANY, NULL, NULL) == 0);
	assert(nitro_validate_acpi_reply(1, NULL, NITRO_RESP_OPTIONAL_STATUS_ZERO, &out, NULL) == 0);
	assert(nitro_validate_acpi_reply(1, NULL, NITRO_RESP_REQUIRED_U64, &out, NULL) == -EIO);
	assert(out == 0ULL);
	assert(nitro_validate_acpi_reply(1, NULL, NITRO_RESP_REQUIRED_KB_STATE, NULL, &kb) == -EIO);

	/* Unsupported type */
	memset(&obj, 0, sizeof(obj));
	obj.type = NITRO_ACPI_OTHER;
	assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_OPTIONAL_STATUS_ZERO, NULL, NULL) == -EIO);
	assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_REQUIRED_U64, &out, NULL) == -EIO);
	assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_REQUIRED_KB_STATE, NULL, &kb) == -EIO);

	/* NULL buffer pointer */
	obj.type = NITRO_ACPI_BUFFER;
	obj.buffer_ptr = NULL;
	obj.buffer_len = 4;
	assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_REQUIRED_U64, &out, NULL) == -EIO);
	obj.buffer_len = 16;
	assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_REQUIRED_KB_STATE, NULL, &kb) == -EIO);

	/* Unsupported buffer lengths */
	memset(unaligned, 0, sizeof(unaligned));
	for (size_t bad_len = 0; bad_len <= 18; ++bad_len) {
		if (bad_len == 4 || bad_len == 8)
			continue;
		obj.type = NITRO_ACPI_BUFFER;
		obj.buffer_ptr = unaligned + 1;
		obj.buffer_len = bad_len;
		assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_REQUIRED_U64, &out, NULL) == -EIO);
		assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_OPTIONAL_STATUS_ZERO, NULL, NULL) == -EIO);
	}

	/* Unaligned 4-byte LE buffer decoding: bytes [00, AB, CD, EF] => 0xEFCDAB00 */
	unaligned[1] = 0x00;
	unaligned[2] = 0xab;
	unaligned[3] = 0xcd;
	unaligned[4] = 0xef;
	obj.type = NITRO_ACPI_BUFFER;
	obj.buffer_ptr = unaligned + 1;
	obj.buffer_len = 4;
	assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_REQUIRED_U64, &out, NULL) == 0);
	assert(out == 0xefcdab00ULL);
	assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_OPTIONAL_STATUS_ZERO, NULL, NULL) == -EIO);

	/* Unaligned 8-byte LE buffer zero status => success */
	memset(unaligned, 0, sizeof(unaligned));
	obj.buffer_ptr = unaligned + 3;
	obj.buffer_len = 8;
	assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_OPTIONAL_STATUS_ZERO, &out, NULL) == 0);
	assert(out == 0ULL);

	/* Transport failure zeroes output */
	out = 0x12345ULL;
	assert(nitro_validate_acpi_reply(0, &obj, NITRO_RESP_REQUIRED_U64, &out, NULL) == -EIO);
	assert(out == 0ULL);

	/* Heap-allocated keyboard-state buffer remains value-independent after poison + free */
	{
		u8 *heap_state = (u8 *)malloc(16);
		assert(heap_state != NULL);
		memset(heap_state, 0, 16);
		heap_state[0] = 0;
		heap_state[1] = 3;
		heap_state[2] = 5;
		heap_state[3] = 100;
		heap_state[6] = 0x22;
		heap_state[7] = 0xca;
		heap_state[8] = 0xff;
		obj.type = NITRO_ACPI_BUFFER;
		obj.buffer_ptr = heap_state;
		obj.buffer_len = 16;
		assert(nitro_validate_acpi_reply(1, &obj, NITRO_RESP_REQUIRED_KB_STATE, NULL, &kb) == 0);
		memset(heap_state, 0xA5, 16);
		free(heap_state);
		assert(kb.gm_return == 0 && kb.gm_output[0] == 3 && kb.gm_output[1] == 5);
		assert(kb.gm_output[2] == 100 && kb.gm_output[5] == 0x22 && kb.gm_output[7] == 0xff);
	}
}

/* 2. Malformed all-black readback defect & valid all-black + partial zone verify */
static void test_static_palette_and_all_black_defect(void)
{
	struct mock_ctx m;
	struct nitro_wmi_ops ops;
	struct nitro_palette black = { { {0, 0, 0}, {0, 0, 0}, {0, 0, 0}, {0, 0, 0} } };
	struct nitro_palette midnight = { { {0x59, 0x31, 0xb5}, {0x45, 0x36, 0xb6}, {0x24, 0x5a, 0x9b}, {0x16, 0x86, 0x9b} } };
	struct nitro_palette red = { { {255, 0, 0}, {255, 0, 0}, {255, 0, 0}, {255, 0, 0} } };
	u8 bad_buf[16];

	/* Valid all-black succeeds when firmware genuinely returns 0 integer/buffer */
	init_mock(&m, &ops);
	assert(nitro_proto_apply_static_palette(&ops, &black) == 0);
	assert(m.allocs == m.frees && m.allocs > 0);

	/* Reject malformed method-7 responses EVEN when requested palette is all-black! */
	struct mock_reply malformed[5];
	memset(malformed, 0, sizeof(malformed));
	malformed[0].transport_ok = 1;
	malformed[0].allocate_buf = 0;
	malformed[0].obj.type = NITRO_ACPI_NONE;
	malformed[1].transport_ok = 1;
	malformed[1].allocate_buf = 1;
	malformed[1].obj.type = NITRO_ACPI_OTHER;
	memset(bad_buf, 0, sizeof(bad_buf));
	malformed[2] = make_buf_reply(bad_buf, 3, 0, 1);
	malformed[3] = make_buf_reply(bad_buf, 16, 0, 1);
	malformed[4].transport_ok = 1;
	malformed[4].allocate_buf = 1;
	malformed[4].obj.type = NITRO_ACPI_BUFFER;
	malformed[4].obj.buffer_ptr = NULL;
	malformed[4].obj.buffer_len = 4;
	malformed[4].align_offset = (size_t)-1;

	for (int k = 0; k < 5; ++k) {
		init_mock(&m, &ops);
		m.use_override_m7 = 1;
		m.fail_method7_idx = 2; /* zone 2 fails/malformed */
		m.override_m7 = malformed[k];
		assert(nitro_proto_verify_colors(&ops, &black) == -EIO);
		assert(m.allocs == m.frees);
	}

	/* Nonzero method-7 status byte (0x00000001) on black MUST fail */
	init_mock(&m, &ops);
	m.use_override_m7 = 1;
	m.fail_method7_idx = 1;
	m.override_m7 = make_int_reply(1ULL, 1);
	assert(nitro_proto_verify_colors(&ops, &black) == -EIO);

	/* Valid colors, mismatched zone 4 readback fails */
	init_mock(&m, &ops);
	m.zones[0][0] = 0x59; m.zones[0][1] = 0x31; m.zones[0][2] = 0xb5;
	m.zones[1][0] = 0x45; m.zones[1][1] = 0x36; m.zones[1][2] = 0xb6;
	m.zones[2][0] = 0x24; m.zones[2][1] = 0x5a; m.zones[2][2] = 0x9b;
	m.zones[3][0] = 0x00; m.zones[3][1] = 0x00; m.zones[3][2] = 0x00;
	assert(nitro_proto_verify_colors(&ops, &midnight) == -EIO);

	/* Successful 4-zone apply (Midnight palette) verifies Method 7 + Method 21 */
	init_mock(&m, &ops);
	assert(nitro_proto_apply_static_palette(&ops, &midnight) == 0);
	assert(m.zones[0][0] == 0x59 && m.zones[3][2] == 0x9b);
	assert(m.mode == 0 && m.brightness == 100);
	assert(m.allocs == m.frees);

	/* Colors match via Method 7 yet Method 21 reports wrong mode/brightness -> rejected! */
	init_mock(&m, &ops);
	u8 wrong_mode_state[16] = { 0, 3 /* Wave! */, 4, 100, 0 };
	m.use_override_m21 = 1;
	m.override_m21 = make_buf_reply(wrong_mode_state, sizeof(wrong_mode_state), 2, 1);
	assert(nitro_proto_apply_static_palette(&ops, &red) == -EIO);

	/* Method 20 void (absent) reply allowed when Method 21 confirms static+100; malformed rejected */
	init_mock(&m, &ops);
	m.use_override_m20 = 1;
	m.override_m20.transport_ok = 1;
	m.override_m20.obj.type = NITRO_ACPI_NONE;
	assert(nitro_proto_apply_static_palette(&ops, &midnight) == 0);

	init_mock(&m, &ops);
	m.use_override_m20 = 1;
	m.override_m20 = make_int_reply(7ULL, 1); /* Nonzero status */
	assert(nitro_proto_apply_static_palette(&ops, &midnight) == -EIO);

	/* Method 2 uses transport-only contract (NITRO_RESP_OPTIONAL_ANY): void or non-zero outputs allowed if M7+M21 pass */
	init_mock(&m, &ops);
	m.use_override_m2 = 1;
	m.override_m2.transport_ok = 1;
	m.override_m2.obj.type = NITRO_ACPI_NONE;
	assert(nitro_proto_apply_static_palette(&ops, &midnight) == 0);
	assert(m.allocs == m.frees && m.allocs > 0);

	init_mock(&m, &ops);
	m.use_override_m2 = 1;
	m.override_m2 = make_int_reply(1ULL, 1);
	assert(nitro_proto_apply_static_palette(&ops, &midnight) == 0);
	assert(m.allocs == m.frees && m.allocs > 0);

	/* Partial zone write failure on zone 3 aborts cleanly and frees allocations */
	init_mock(&m, &ops);
	m.fail_method6_idx = 3;
	assert(nitro_proto_apply_static_palette(&ops, &midnight) == -EIO);
	assert(m.allocs == m.frees && m.allocs > 0);

	/* Allocation cleanup even on ACPI failures in method 2 and 20 */
	init_mock(&m, &ops);
	m.fail_method2 = 1;
	assert(nitro_proto_apply_static_palette(&ops, &midnight) == -EIO);
	assert(m.allocs == m.frees && m.allocs > 0);
}

static struct mock_reply fail_once_m20_handler(struct mock_ctx *m, u32 method_id,
					       const u8 *in_buf, size_t in_len,
					       enum nitro_resp_contract contract)
{
	if (method_id == ACER_WMID_SET_GAMING_KB_BACKLIGHT_METHODID && !m->fail_method20) {
		m->fail_method20 = 1; /* Fail ONLY the first Method 20 call (effect attempt), succeed fallback */
		return make_int_reply(5ULL, 1);
	}
	int saved = m->fail_method20;
	m->fail_method20 = 0;
	struct mock_reply r = default_fw_handler(m, method_id, in_buf, in_len, contract);
	m->fail_method20 = saved;
	return r;
}

/* 3. Dynamic effect preconditions, readback validation, and truthful Static vs Dynamic recovery */
static void test_dynamic_effects_and_recovery(void)
{
	struct mock_ctx m;
	struct nitro_wmi_ops ops;
	struct nitro_effect_outcome out;

	/* Precondition: abort BEFORE writes if backup Method 7 returns malformed data */
	init_mock(&m, &ops);
	m.use_override_m7 = 1;
	m.fail_method7_idx = 3;
	m.override_m7.transport_ok = 1;
	m.override_m7.obj.type = NITRO_ACPI_OTHER;
	assert(nitro_proto_apply_effect(&ops, 1, 4, 0x22caffU, false, &out) == -EIO);
	assert(out.recovery == NITRO_RECOVERY_NONE);
	for (size_t i = 0; i < m.n_calls; ++i)
		assert(m.calls[i].method_id != ACER_WMID_SET_GAMING_KB_BACKLIGHT_METHODID);

	/* Precondition: abort if brightness != 100 */
	init_mock(&m, &ops);
	m.brightness = 65;
	assert(nitro_proto_apply_effect(&ops, 1, 4, 0x22caffU, false, &out) == -EINVAL);

	/* Successful effect switch checks mode, speed, brightness */
	init_mock(&m, &ops);
	assert(nitro_proto_apply_effect(&ops, 1, 6, 0x22caffU, false, &out) == 0);
	assert(out.status == 0 && out.recovery == NITRO_RECOVERY_NONE);
	assert(m.mode == 1 && m.speed == 6 && m.brightness == 100);

	/* Failed effect attempt from Dynamic prior state (mode=3 Wave) => Static fallback succeeds, returns -EIO */
	init_mock(&m, &ops);
	m.mode = 3;
	m.speed = 7;
	m.zones[0][0] = 0x11; m.zones[0][1] = 0x22; m.zones[0][2] = 0x33;
	m.handler = fail_once_m20_handler;
	assert(nitro_proto_apply_effect(&ops, 2, 5, 0x00ff00U, false, &out) == -EIO);
	assert(out.prior_mode == 3 && out.recovery == NITRO_RECOVERY_STATIC_FALLBACK);
	assert(m.mode == 0 && m.brightness == 100);
	assert(logs_contain(&m, "NOT restored") && logs_contain(&m, "Static fallback"));

	/* Failed effect attempt where both effect AND fallback fail => RECOVERY_FAILED */
	init_mock(&m, &ops);
	m.mode = 0;
	m.use_override_m20 = 1;
	m.override_m20 = make_int_reply(99ULL, 1);
	assert(nitro_proto_apply_effect(&ops, 3, 5, 0xabcdefU, false, &out) == -EIO);
	assert(out.recovery == NITRO_RECOVERY_FAILED);
	assert(logs_contain(&m, "recovery failed"));

	/* Timed effect from Static state (mode=0) => restores Static cleanly */
	init_mock(&m, &ops);
	m.mode = 0;
	m.zones[2][1] = 0x77;
	assert(nitro_proto_apply_effect(&ops, 2, 5, 0x00ff00U, true, &out) == 0);
	assert(m.slept_ms == 8000U);
	assert(out.recovery == NITRO_RECOVERY_STATIC_RESTORED);
	assert(m.mode == 0 && m.zones[2][1] == 0x77);
	assert(logs_contain(&m, "Static palette restored"));

	/* Timed effect starting from ALREADY DYNAMIC state (mode=3 Wave) => Static fallback (NOT dynamic restored!) */
	init_mock(&m, &ops);
	m.mode = 3;
	m.speed = 7;
	m.zones[1][0] = 0x10; m.zones[1][1] = 0x20; m.zones[1][2] = 0x30;
	assert(nitro_proto_apply_effect(&ops, 1, 2, 0xff00ffU, true, &out) == 0);
	assert(out.prior_mode == 3);
	assert(out.recovery == NITRO_RECOVERY_STATIC_FALLBACK);
	assert(m.mode == 0 && m.brightness == 100);
	assert(logs_contain(&m, "NOT restored"));
	assert(logs_contain(&m, "Static fallback"));
	assert(!logs_contain(&m, "original Static palette restored"));

	/* Timed effect from Dynamic state where fallback fails => NITRO_RECOVERY_FAILED & non-zero */
	init_mock(&m, &ops);
	m.mode = 4;
	m.fail_method2 = 1; /* Fails when recovery runs nitro_proto_enable_zones() */
	assert(nitro_proto_apply_effect(&ops, 1, 2, 0xff00ffU, true, &out) == -EIO);
	assert(out.recovery == NITRO_RECOVERY_FAILED);
	assert(logs_contain(&m, "dynamic mode=4 NOT restored"));
	assert(m.allocs == m.frees);
}

/* 4. Color parsing & brightness gate */
static void test_color_parser(void)
{
	struct nitro_palette pal;
	assert(nitro_proto_parse_colors("5931B5,4536B6,245A9B,16869B", 100, &pal) == 0);
	assert(pal.rgb[0][0] == 0x59 && pal.rgb[0][1] == 0x31 && pal.rgb[0][2] == 0xb5);
	assert(pal.rgb[3][0] == 0x16 && pal.rgb[3][1] == 0x86 && pal.rgb[3][2] == 0x9b);

	/* Brightness != 100 rejected */
	assert(nitro_proto_parse_colors("FF0000,FF0000,FF0000,FF0000", 65, &pal) == -EINVAL);
	assert(nitro_proto_parse_colors("FF0000,FF0000,FF0000,FF0000", 25, &pal) == -EINVAL);
	/* Malformed strings rejected */
	assert(nitro_proto_parse_colors("FF0000,FF0000,FF0000,FF000", 100, &pal) == -EINVAL);
	assert(nitro_proto_parse_colors("FF0000;FF0000,FF0000,FF0000", 100, &pal) == -EINVAL);
	assert(nitro_proto_parse_colors("ZZ0000,FF0000,FF0000,FF0000", 100, &pal) == -EINVAL);
}

int main(void)
{
	test_decoder_contracts();
	test_static_palette_and_all_black_defect();
	test_dynamic_effects_and_recovery();
	test_color_parser();
	printf("ALL_C_PROTOCOL_TESTS_OK\n");
	return 0;
}

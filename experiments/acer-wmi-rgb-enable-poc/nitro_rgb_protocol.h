/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Shared pure-C Acer AN515-58 gaming WMI protocol, decoding, verification,
 * and recovery helpers. Compiled both inside nitro_rgb_enable_poc.ko and
 * inside offline userspace regression tests.
 */
#ifndef NITRO_RGB_PROTOCOL_H
#define NITRO_RGB_PROTOCOL_H

#ifdef __KERNEL__
#include <linux/errno.h>
#include <linux/kernel.h>
#include <linux/string.h>
#include <linux/types.h>
#else
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#endif

#define ACER_WMID_SET_GAMING_LED_METHODID          2U
#define ACER_WMID_GET_GAMING_SYS_INFO_METHODID     5U
#define ACER_WMID_SET_GAMING_STATIC_LED_METHODID   6U
#define ACER_WMID_GET_GAMING_RGB_KB_METHODID       7U
#define ACER_WMID_SET_GAMING_KB_BACKLIGHT_METHODID 20U
#define ACER_WMID_GET_GAMING_KB_BACKLIGHT_METHODID 21U

#define ACER_GAMING_KBL_SET_ON        (1ULL << 3)
#define ACER_GAMING_KBL_SET_ALL_ZONES (0xFULL << 40)

enum nitro_acpi_val_type {
	NITRO_ACPI_NONE = 0,
	NITRO_ACPI_INTEGER = 1,
	NITRO_ACPI_BUFFER = 2,
	NITRO_ACPI_OTHER = 3,
};

struct nitro_acpi_obj {
	enum nitro_acpi_val_type type;
	u64 integer_value;
	const u8 *buffer_ptr;
	size_t buffer_len;
};

enum nitro_resp_contract {
	NITRO_RESP_OPTIONAL_ANY = 0,
	NITRO_RESP_OPTIONAL_STATUS_ZERO = 1,
	NITRO_RESP_REQUIRED_U64 = 2,
	NITRO_RESP_REQUIRED_KB_STATE = 3,
};

struct nitro_kb_state {
	u8 gm_return;
	u8 gm_output[15];
};

struct nitro_palette {
	u8 rgb[4][3];
};

enum nitro_recovery_kind {
	NITRO_RECOVERY_NONE = 0,
	NITRO_RECOVERY_STATIC_RESTORED = 1,
	NITRO_RECOVERY_STATIC_FALLBACK = 2,
	NITRO_RECOVERY_FAILED = 3,
};

struct nitro_effect_outcome {
	int status;
	int prior_mode;
	int prior_speed;
	int prior_brightness;
	enum nitro_recovery_kind recovery;
};

struct nitro_wmi_ops {
	void *ctx;
	/*
	 * invoke_method evaluates a WMI method with an explicit response contract.
	 * The implementation MUST invoke the transport, decode/validate only via the
	 * shared contract helpers, and release any allocated ACPI output buffer on
	 * both transport success and transport failure before returning.
	 */
	int (*eval_u64)(void *ctx, u32 method_id, const u8 *in_buf, size_t in_len,
			enum nitro_resp_contract contract, u64 *out_u64,
			struct nitro_kb_state *out_state);
	void (*sleep_ms)(void *ctx, unsigned int ms);
	void (*log_info)(void *ctx, const char *msg);
	void (*log_err)(void *ctx, const char *msg);
};

static inline void nitro_log_info(const struct nitro_wmi_ops *ops, const char *msg)
{
	if (ops && ops->log_info)
		ops->log_info(ops->ctx, msg);
}

static inline void nitro_log_err(const struct nitro_wmi_ops *ops, const char *msg)
{
	if (ops && ops->log_err)
		ops->log_err(ops->ctx, msg);
}

static inline u32 nitro_get_le32(const u8 *p)
{
	return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

static inline u64 nitro_get_le64(const u8 *p)
{
	return (u64)nitro_get_le32(p) | ((u64)nitro_get_le32(p + 4) << 32);
}

static inline void nitro_put_le64(u8 out[8], u64 v)
{
	out[0] = (u8)(v & 0xffU);
	out[1] = (u8)((v >> 8) & 0xffU);
	out[2] = (u8)((v >> 16) & 0xffU);
	out[3] = (u8)((v >> 24) & 0xffU);
	out[4] = (u8)((v >> 32) & 0xffU);
	out[5] = (u8)((v >> 40) & 0xffU);
	out[6] = (u8)((v >> 48) & 0xffU);
	out[7] = (u8)((v >> 56) & 0xffU);
}

static inline u32 nitro_zone_wire_u32(u8 tag_or_status, u8 red, u8 green, u8 blue)
{
	return (u32)tag_or_status |
	       ((u32)red << 8) |
	       ((u32)green << 16) |
	       ((u32)blue << 24);
}

static inline void nitro_decode_zone_rgb(u64 raw, u8 *red, u8 *green, u8 *blue)
{
	if (red)
		*red = (u8)((raw >> 8) & 0xffU);
	if (green)
		*green = (u8)((raw >> 16) & 0xffU);
	if (blue)
		*blue = (u8)((raw >> 24) & 0xffU);
}

static inline int nitro_decode_u64_object(const struct nitro_acpi_obj *obj, u64 *out_val)
{
	if (!obj || !out_val)
		return -EIO;
	if (obj->type == NITRO_ACPI_INTEGER) {
		*out_val = obj->integer_value;
		return 0;
	}
	if (obj->type == NITRO_ACPI_BUFFER) {
		if (!obj->buffer_ptr)
			return -EIO;
		if (obj->buffer_len == 4) {
			*out_val = nitro_get_le32(obj->buffer_ptr);
			return 0;
		}
		if (obj->buffer_len == 8) {
			*out_val = nitro_get_le64(obj->buffer_ptr);
			return 0;
		}
		return -EIO;
	}
	return -EIO;
}

/*
 * Decode and validate an ACPI reply according to its method-specific contract.
 * Output pointers are zero-initialized on failure so stale stack data is never reused.
 */
static inline int nitro_validate_acpi_reply(int transport_ok,
					    const struct nitro_acpi_obj *obj,
					    enum nitro_resp_contract contract,
					    u64 *out_u64,
					    struct nitro_kb_state *out_state)
{
	u64 decoded = 0;
	int ret;

	if (out_u64)
		*out_u64 = 0;
	if (out_state)
		memset(out_state, 0, sizeof(*out_state));

	if (!transport_ok)
		return -EIO;

	switch (contract) {
	case NITRO_RESP_OPTIONAL_ANY:
		return 0;

	case NITRO_RESP_OPTIONAL_STATUS_ZERO:
		if (!obj || obj->type == NITRO_ACPI_NONE)
			return 0;
		ret = nitro_decode_u64_object(obj, &decoded);
		if (ret)
			return ret;
		if (out_u64)
			*out_u64 = decoded;
		return decoded == 0 ? 0 : -EIO;

	case NITRO_RESP_REQUIRED_U64:
		if (!obj || obj->type == NITRO_ACPI_NONE)
			return -EIO;
		ret = nitro_decode_u64_object(obj, &decoded);
		if (ret)
			return ret;
		if (out_u64)
			*out_u64 = decoded;
		return 0;

	case NITRO_RESP_REQUIRED_KB_STATE:
		if (!out_state || !obj || obj->type != NITRO_ACPI_BUFFER ||
		    !obj->buffer_ptr || obj->buffer_len != sizeof(*out_state))
			return -EIO;
		memcpy(out_state, obj->buffer_ptr, sizeof(*out_state));
		return 0;
	}
	return -EIO;
}

static inline int nitro_proto_hex_nibble(char c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -EINVAL;
}

static inline int nitro_proto_parse_colors(const char *colors_str, unsigned int bright,
					   struct nitro_palette *pal)
{
	int i, j, hi, lo;

	if (!colors_str || !pal || strlen(colors_str) != 27 || bright != 100U)
		return -EINVAL;
	for (i = 0; i < 4; ++i) {
		if (i < 3 && colors_str[i * 7 + 6] != ',')
			return -EINVAL;
		for (j = 0; j < 3; ++j) {
			hi = nitro_proto_hex_nibble(colors_str[i * 7 + j * 2]);
			lo = nitro_proto_hex_nibble(colors_str[i * 7 + j * 2 + 1]);
			if (hi < 0 || lo < 0)
				return -EINVAL;
			pal->rgb[i][j] = (u8)((hi << 4) | lo);
		}
	}
	return 0;
}

static inline int nitro_proto_read_kb_state(const struct nitro_wmi_ops *ops,
					    struct nitro_kb_state *state)
{
	u8 in_buf[8];
	int ret;

	if (!ops || !ops->eval_u64 || !state)
		return -EINVAL;
	nitro_put_le64(in_buf, 1ULL);
	ret = ops->eval_u64(ops->ctx, ACER_WMID_GET_GAMING_KB_BACKLIGHT_METHODID,
			    in_buf, sizeof(in_buf), NITRO_RESP_REQUIRED_KB_STATE,
			    NULL, state);
	if (ret) {
		nitro_log_err(ops, "nitro_rgb_enable_poc: method 21 state read failed or returned invalid object");
		return -EIO;
	}
	return 0;
}

static inline int nitro_proto_verify_static_kb_state(const struct nitro_wmi_ops *ops)
{
	struct nitro_kb_state state;
	char msg[160];
	int ret;

	ret = nitro_proto_read_kb_state(ops, &state);
	if (ret)
		return ret;
	if (state.gm_return != 0 || state.gm_output[0] != 0 || state.gm_output[2] != 100) {
		snprintf(msg, sizeof(msg),
			 "nitro_rgb_enable_poc: static mode/brightness readback mismatch (status=%u mode=%u brightness=%u)",
			 state.gm_return, state.gm_output[0], state.gm_output[2]);
		nitro_log_err(ops, msg);
		return -EIO;
	}
	return 0;
}

static inline int nitro_proto_enable_zones(const struct nitro_wmi_ops *ops)
{
	u8 in_buf[8];
	u64 zone_enable = ACER_GAMING_KBL_SET_ON | ACER_GAMING_KBL_SET_ALL_ZONES;
	int ret;

	if (!ops || !ops->eval_u64)
		return -EINVAL;
	nitro_log_info(ops, "nitro_rgb_enable_poc: issuing gaming-system-info poll");
	nitro_put_le64(in_buf, 0ULL);
	ret = ops->eval_u64(ops->ctx, ACER_WMID_GET_GAMING_SYS_INFO_METHODID,
			    in_buf, sizeof(in_buf), NITRO_RESP_OPTIONAL_ANY, NULL, NULL);
	if (ret) {
		nitro_log_err(ops, "nitro_rgb_enable_poc: method 5 failed");
		return -EIO;
	}

	nitro_put_le64(in_buf, zone_enable);
	ret = ops->eval_u64(ops->ctx, ACER_WMID_SET_GAMING_LED_METHODID,
			    in_buf, sizeof(in_buf), NITRO_RESP_OPTIONAL_ANY, NULL, NULL);
	if (ret) {
		nitro_log_err(ops, "nitro_rgb_enable_poc: method 2 failed");
		return -EIO;
	}
	return 0;
}

static inline int nitro_proto_set_kb_backlight(const struct nitro_wmi_ops *ops,
					       u8 bright, u8 mode, u8 speed, u32 rgb)
{
	u8 gm_input[16] = {
		0, 0, 0, 0,
		0, 0, 0, 0,
		3, 1, 0, 0,
		0, 0, 0, 0
	};
	int ret;

	if (!ops || !ops->eval_u64)
		return -EINVAL;
	gm_input[2] = bright;
	if (mode) {
		gm_input[0] = mode;
		gm_input[1] = speed;
		gm_input[3] = mode == 3 ? 8 : 0;
		gm_input[4] = 2;
		gm_input[5] = (u8)((rgb >> 16) & 0xffU);
		gm_input[6] = (u8)((rgb >> 8) & 0xffU);
		gm_input[7] = (u8)(rgb & 0xffU);
		gm_input[8] = 0;
	}
	ret = ops->eval_u64(ops->ctx, ACER_WMID_SET_GAMING_KB_BACKLIGHT_METHODID,
			    gm_input, sizeof(gm_input), NITRO_RESP_OPTIONAL_STATUS_ZERO,
			    NULL, NULL);
	if (ret) {
		nitro_log_err(ops, "nitro_rgb_enable_poc: method 20 rejected or returned malformed status");
		return -EIO;
	}
	return 0;
}

static inline int nitro_proto_set_static_zone_u64(const struct nitro_wmi_ops *ops,
						  u8 zone, u8 red, u8 green, u8 blue)
{
	u8 in_buf[8];
	u64 payload = (u64)nitro_zone_wire_u32(zone, red, green, blue);
	int ret;

	if (!ops || !ops->eval_u64)
		return -EINVAL;
	nitro_put_le64(in_buf, payload);
	ret = ops->eval_u64(ops->ctx, ACER_WMID_SET_GAMING_STATIC_LED_METHODID,
			    in_buf, sizeof(in_buf), NITRO_RESP_OPTIONAL_STATUS_ZERO,
			    NULL, NULL);
	if (ret) {
		nitro_log_err(ops, "nitro_rgb_enable_poc: method 6 static zone write rejected");
		return -EIO;
	}
	return 0;
}

static inline int nitro_proto_verify_colors(const struct nitro_wmi_ops *ops,
					    const struct nitro_palette *pal)
{
	static const u8 zone_ids[4] = { 1, 2, 4, 8 };
	u8 in_buf[8];
	u64 raw;
	u32 expected;
	int i, ret;
	char msg[160];

	if (!ops || !ops->eval_u64 || !pal)
		return -EINVAL;
	for (i = 0; i < 4; ++i) {
		raw = 0;
		nitro_put_le64(in_buf, (u64)zone_ids[i]);
		ret = ops->eval_u64(ops->ctx, ACER_WMID_GET_GAMING_RGB_KB_METHODID,
				    in_buf, sizeof(in_buf), NITRO_RESP_REQUIRED_U64,
				    &raw, NULL);
		if (ret) {
			snprintf(msg, sizeof(msg),
				 "nitro_rgb_enable_poc: method 7 zone %d returned missing/malformed object",
				 i + 1);
			nitro_log_err(ops, msg);
			return -EIO;
		}
		if ((raw & 0xffULL) != 0ULL) {
			snprintf(msg, sizeof(msg),
				 "nitro_rgb_enable_poc: method 7 zone %d nonzero firmware status=0x%02x",
				 i + 1, (unsigned int)(raw & 0xffULL));
			nitro_log_err(ops, msg);
			return -EIO;
		}
		expected = nitro_zone_wire_u32(0, pal->rgb[i][0], pal->rgb[i][1], pal->rgb[i][2]);
		if ((u32)(raw & 0xffffffffULL) != expected) {
			snprintf(msg, sizeof(msg),
				 "nitro_rgb_enable_poc: zone %d firmware readback differs (got=0x%08x wanted=0x%08x)",
				 i + 1, (unsigned int)(raw & 0xffffffffULL), (unsigned int)expected);
			nitro_log_err(ops, msg);
			return -EIO;
		}
	}
	nitro_log_info(ops, "nitro_rgb_enable_poc: all four firmware zone color readbacks match");
	return 0;
}

static inline int nitro_proto_apply_static_palette(const struct nitro_wmi_ops *ops,
						   const struct nitro_palette *pal)
{
	static const u8 zone_ids[4] = { 1, 2, 4, 8 };
	int i, ret;

	if (!pal)
		return -EINVAL;
	ret = nitro_proto_enable_zones(ops);
	if (ret)
		return ret;
	ret = nitro_proto_set_kb_backlight(ops, 100, 0, 0, 0);
	if (ret)
		return ret;
	for (i = 0; i < 4; ++i) {
		ret = nitro_proto_set_static_zone_u64(ops, zone_ids[i],
						      pal->rgb[i][0], pal->rgb[i][1], pal->rgb[i][2]);
		if (ret)
			return ret;
	}
	ret = nitro_proto_verify_colors(ops, pal);
	if (ret)
		return ret;
	return nitro_proto_verify_static_kb_state(ops);
}

static inline int nitro_proto_probe_state(const struct nitro_wmi_ops *ops)
{
	static const u8 zone_ids[4] = { 1, 2, 4, 8 };
	struct nitro_kb_state state;
	u8 in_buf[8];
	u64 raw;
	char msg[192];
	int i, ret;

	ret = nitro_proto_read_kb_state(ops, &state);
	if (ret)
		return ret;
	snprintf(msg, sizeof(msg),
		 "nitro_rgb_enable_poc: keyboard state: return=%u mode=%u speed=%u brightness=%u direction=%u rgb=%u,%u,%u",
		 state.gm_return, state.gm_output[0], state.gm_output[1], state.gm_output[2],
		 state.gm_output[4], state.gm_output[5], state.gm_output[6], state.gm_output[7]);
	nitro_log_info(ops, msg);

	for (i = 0; i < 4; ++i) {
		raw = 0;
		nitro_put_le64(in_buf, (u64)zone_ids[i]);
		ret = ops->eval_u64(ops->ctx, ACER_WMID_GET_GAMING_RGB_KB_METHODID,
				    in_buf, sizeof(in_buf), NITRO_RESP_REQUIRED_U64,
				    &raw, NULL);
		if (ret) {
			snprintf(msg, sizeof(msg),
				 "nitro_rgb_enable_poc: method 7 zone %d read failed or malformed",
				 i + 1);
			nitro_log_err(ops, msg);
			return -EIO;
		}
		snprintf(msg, sizeof(msg),
			 "nitro_rgb_enable_poc: zone %d raw=0x%016llx",
			 i + 1, (unsigned long long)raw);
		nitro_log_info(ops, msg);
	}
	return 0;
}

static inline int nitro_proto_apply_effect(const struct nitro_wmi_ops *ops,
					   unsigned int req_mode,
					   unsigned int req_speed,
					   u32 req_rgb,
					   bool do_timed_test,
					   struct nitro_effect_outcome *outcome)
{
	static const u8 zone_ids[4] = { 1, 2, 4, 8 };
	struct nitro_kb_state state;
	struct nitro_effect_outcome local_out;
	struct nitro_palette prev_pal;
	u8 in_buf[8];
	u64 raw;
	u8 prev_mode;
	int i, ret, restore;
	char msg[220];

	memset(&local_out, 0, sizeof(local_out));
	local_out.recovery = NITRO_RECOVERY_NONE;

	if (!ops || !ops->eval_u64 || req_mode < 1U || req_mode > 5U ||
	    req_speed < 1U || req_speed > 9U || req_rgb > 0xffffffU) {
		local_out.status = -EINVAL;
		if (outcome)
			*outcome = local_out;
		return -EINVAL;
	}

	ret = nitro_proto_read_kb_state(ops, &state);
	if (ret) {
		local_out.status = ret;
		if (outcome)
			*outcome = local_out;
		return ret;
	}
	if (state.gm_return || state.gm_output[0] > 5 || state.gm_output[2] != 100) {
		snprintf(msg, sizeof(msg),
			 "nitro_rgb_enable_poc: Effect requires readable keyboard state at WMI brightness 100 (status=%u mode=%u brightness=%u)",
			 state.gm_return, state.gm_output[0], state.gm_output[2]);
		nitro_log_err(ops, msg);
		local_out.status = -EINVAL;
		if (outcome)
			*outcome = local_out;
		return -EINVAL;
	}

	prev_mode = state.gm_output[0];
	local_out.prior_mode = prev_mode;
	local_out.prior_speed = state.gm_output[1];
	local_out.prior_brightness = state.gm_output[2];

	for (i = 0; i < 4; ++i) {
		raw = 0;
		nitro_put_le64(in_buf, (u64)zone_ids[i]);
		ret = ops->eval_u64(ops->ctx, ACER_WMID_GET_GAMING_RGB_KB_METHODID,
				    in_buf, sizeof(in_buf), NITRO_RESP_REQUIRED_U64,
				    &raw, NULL);
		if (ret || (raw & 0xffULL) != 0ULL) {
			nitro_log_err(ops, "nitro_rgb_enable_poc: aborted before write because pre-effect zone readback failed or returned malformed data");
			local_out.status = -EIO;
			if (outcome)
				*outcome = local_out;
			return -EIO;
		}
		nitro_decode_zone_rgb(raw, &prev_pal.rgb[i][0], &prev_pal.rgb[i][1], &prev_pal.rgb[i][2]);
	}

	ret = nitro_proto_set_kb_backlight(ops, 100, (u8)req_mode, (u8)req_speed, req_rgb);
	if (!ret) {
		ret = nitro_proto_read_kb_state(ops, &state);
		if (!ret && (state.gm_return || state.gm_output[0] != req_mode ||
			     state.gm_output[1] != req_speed || state.gm_output[2] != 100)) {
			snprintf(msg, sizeof(msg),
				 "nitro_rgb_enable_poc: effect readback mismatch (status=%u mode=%u speed=%u brightness=%u)",
				 state.gm_return, state.gm_output[0], state.gm_output[1], state.gm_output[2]);
			nitro_log_err(ops, msg);
			ret = -EIO;
		}
		if (!ret) {
			snprintf(msg, sizeof(msg),
				 "nitro_rgb_enable_poc: requested effect=%u accepted; firmware readback confirmed mode=%u speed=%u brightness=%u (effect color and physical LEDs not verified)",
				 req_mode, state.gm_output[0], state.gm_output[1], state.gm_output[2]);
			nitro_log_info(ops, msg);
			if (!do_timed_test) {
				local_out.status = 0;
				if (outcome)
					*outcome = local_out;
				return 0;
			}
			if (ops->sleep_ms)
				ops->sleep_ms(ops->ctx, 8000U);
		}
	}

	restore = nitro_proto_apply_static_palette(ops, &prev_pal);
	if (restore) {
		local_out.recovery = NITRO_RECOVERY_FAILED;
		if (prev_mode == 0) {
			nitro_log_err(ops, "nitro_rgb_enable_poc: recovery failed; previous Static palette NOT verified/restored");
		} else {
			snprintf(msg, sizeof(msg),
				 "nitro_rgb_enable_poc: previous dynamic mode=%u NOT restored and Static fallback failed",
				 prev_mode);
			nitro_log_err(ops, msg);
		}
		local_out.status = ret ? ret : restore;
		if (outcome)
			*outcome = local_out;
		return local_out.status;
	}

	if (prev_mode == 0) {
		local_out.recovery = NITRO_RECOVERY_STATIC_RESTORED;
		nitro_log_info(ops, "nitro_rgb_enable_poc: previous Static palette restored and firmware-verified at brightness=100 (verify physical LEDs)");
	} else {
		local_out.recovery = NITRO_RECOVERY_STATIC_FALLBACK;
		snprintf(msg, sizeof(msg),
			 "nitro_rgb_enable_poc: previous state was dynamic mode=%u (exact dynamic restoration unsupported; NOT restored); applied verified Static fallback at brightness=100",
			 prev_mode);
		nitro_log_info(ops, msg);
	}

	local_out.status = ret;
	if (outcome)
		*outcome = local_out;
	return ret;
}

#endif /* NITRO_RGB_PROTOCOL_H */

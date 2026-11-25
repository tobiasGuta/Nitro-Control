// SPDX-License-Identifier: GPL-2.0-only
/*
 * Nitro Control AN515-58 RGB proof-of-concept.
 *
 * This is deliberately NOT a replacement for acer_wmi. It provides:
 *   - default dry-run validation only;
 *   - an explicit read-only firmware-state probe (probe=1);
 *   - validated brightness=100 static four-zone apply (apply=1);
 *   - validated brightness=100 dynamic effect application (effect_mode=1..5);
 *   - guarded static-red full recovery sequence (full_test=1) at brightness=100.
 *
 * Historical sub-100% / partial diagnostic experiments (enable, red_test,
 * backlight_test, effect_test) are compiled out unless built explicitly with
 * NITRO_ENABLE_EXPERIMENTAL_PARAMS=1 and are rejected by Nitro Control staging.
 *
 * No autoload alias is provided. The module must be inserted manually.
 */

#include <linux/acpi.h>
#include <linux/delay.h>
#include <linux/dmi.h>
#include <linux/hex.h>
#include <linux/init.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/wmi.h>

#include "nitro_rgb_protocol.h"

#define ACER_GAMING_WMI_GUID "7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56"

static bool probe;
module_param(probe, bool, 0400);
MODULE_PARM_DESC(probe,
	"Read keyboard mode/brightness and four zone-color firmware state (default: false)");

static bool full_test;
module_param(full_test, bool, 0400);
MODULE_PARM_DESC(full_test,
	"Explicit one-shot recovery: method 5/2 + 20@100 + u64 method-6 static red + readback verify");

/* Opt-in configurable four-zone application. No service or boot autoload. */
static bool apply;
module_param(apply, bool, 0400);
MODULE_PARM_DESC(apply, "Apply validated per-zone static RGB; requires colors and brightness=100");

static unsigned int effect_mode;
module_param(effect_mode, uint, 0400);
MODULE_PARM_DESC(effect_mode, "Acer effect mode 1-5 (0 leaves effects disabled)");

static unsigned int effect_speed = 4;
module_param(effect_speed, uint, 0400);
MODULE_PARM_DESC(effect_speed, "Effect speed 1-9");

static unsigned int effect_rgb;
module_param(effect_rgb, uint, 0400);
MODULE_PARM_DESC(effect_rgb, "Effect color as 0xRRGGBB");

static char colors[28] = "FF0000,FF0000,FF0000,FF0000";
module_param_string(colors, colors, sizeof(colors), 0400);
MODULE_PARM_DESC(colors, "Four six-digit RRGGBB hex values separated by commas");

static unsigned int brightness = 100;
module_param(brightness, uint, 0400);
MODULE_PARM_DESC(brightness, "Development safety gate: write modes require brightness=100");

#ifdef NITRO_ENABLE_EXPERIMENTAL_PARAMS
struct led_four_zone_set_param {
	u8 zone;
	u8 red;
	u8 green;
	u8 blue;
} __packed;

static bool enable;
module_param(enable, bool, 0400);
MODULE_PARM_DESC(enable,
	"EXPERIMENTAL diagnostic: gaming-WMI zone-enable sequence only");

static bool red_test;
module_param(red_test, bool, 0400);
MODULE_PARM_DESC(red_test,
	"EXPERIMENTAL diagnostic: legacy 4-byte method-6 red write without readback");

static bool backlight_test;
module_param(backlight_test, bool, 0400);
MODULE_PARM_DESC(backlight_test,
	"EXPERIMENTAL diagnostic (sub-100%): method 20 static mode at brightness=25");

static bool effect_test;
module_param(effect_test, bool, 0400);
MODULE_PARM_DESC(effect_test,
	"EXPERIMENTAL diagnostic: timed 8s effect with Static restoration or fallback");
#endif

static bool nitro_exact_model(void)
{
	const char *vendor = dmi_get_system_info(DMI_SYS_VENDOR);
	const char *product = dmi_get_system_info(DMI_PRODUCT_NAME);

	return vendor && product &&
	       !strcmp(vendor, "Acer") &&
	       !strcmp(product, "Nitro AN515-58");
}

static int nitro_kernel_eval_u64(void *ctx, u32 method_id,
				 const u8 *in_buf, size_t in_len,
				 enum nitro_resp_contract contract,
				 u64 *out_u64, struct nitro_kb_state *out_state)
{
	u8 local_in[16];
	struct acpi_buffer input;
	struct acpi_buffer output = {
		.length = ACPI_ALLOCATE_BUFFER,
		.pointer = NULL,
	};
	struct nitro_acpi_obj nobj = {
		.type = NITRO_ACPI_NONE,
		.integer_value = 0,
		.buffer_ptr = NULL,
		.buffer_len = 0,
	};
	union acpi_object *obj;
	acpi_status status;
	int ret;

	(void)ctx;
	if (!in_buf || in_len > sizeof(local_in))
		return -EINVAL;
	memcpy(local_in, in_buf, in_len);
	input.length = (acpi_size)in_len;
	input.pointer = local_in;

	status = wmi_evaluate_method(ACER_GAMING_WMI_GUID, 0, method_id,
				     &input, &output);
	if (ACPI_FAILURE(status)) {
		kfree(output.pointer);
		pr_err("nitro_rgb_enable_poc: method %u ACPI failure: %s\n",
		       method_id, acpi_format_exception(status));
		return nitro_validate_acpi_reply(0, NULL, contract, out_u64, out_state);
	}

	obj = output.pointer;
	if (obj) {
		if (obj->type == ACPI_TYPE_INTEGER) {
			nobj.type = NITRO_ACPI_INTEGER;
			nobj.integer_value = obj->integer.value;
		} else if (obj->type == ACPI_TYPE_BUFFER) {
			nobj.type = NITRO_ACPI_BUFFER;
			nobj.buffer_ptr = obj->buffer.pointer;
			nobj.buffer_len = obj->buffer.length;
		} else {
			nobj.type = NITRO_ACPI_OTHER;
		}
	}

	ret = nitro_validate_acpi_reply(1, &nobj, contract, out_u64, out_state);
	kfree(output.pointer);
	return ret;
}

static void nitro_kernel_sleep_ms(void *ctx, unsigned int ms)
{
	(void)ctx;
	msleep(ms);
}

static void nitro_kernel_log_info(void *ctx, const char *msg)
{
	(void)ctx;
	pr_info("%s\n", msg);
}

static void nitro_kernel_log_err(void *ctx, const char *msg)
{
	(void)ctx;
	pr_err("%s\n", msg);
}

static const struct nitro_wmi_ops nitro_kernel_ops = {
	.ctx = NULL,
	.eval_u64 = nitro_kernel_eval_u64,
	.sleep_ms = nitro_kernel_sleep_ms,
	.log_info = nitro_kernel_log_info,
	.log_err = nitro_kernel_log_err,
};

static int nitro_parse_colors(struct nitro_palette *pal)
{
	if (strlen(colors) != 27 || brightness != 100)
		return -EINVAL;
	return nitro_proto_parse_colors(colors, brightness, pal);
}

static int nitro_full_static_red_test(void)
{
	static const struct nitro_palette red_pal = {
		.rgb = {
			{ 255, 0, 0 },
			{ 255, 0, 0 },
			{ 255, 0, 0 },
			{ 255, 0, 0 },
		},
	};
	int ret;

	pr_info("nitro_rgb_enable_poc: full test start: state before writes\n");
	ret = nitro_proto_probe_state(&nitro_kernel_ops);
	if (ret)
		return ret;

	ret = nitro_proto_apply_static_palette(&nitro_kernel_ops, &red_pal);
	if (ret)
		return ret;

	pr_info("nitro_rgb_enable_poc: full test completed; firmware readback matches static red at brightness=100 (inspect keyboard visually)\n");
	return nitro_proto_probe_state(&nitro_kernel_ops);
}

static int nitro_apply_custom(void)
{
	struct nitro_palette pal;
	int ret;

	ret = nitro_parse_colors(&pal);
	if (ret) {
		pr_err("nitro_rgb_enable_poc: invalid colors or brightness (100 required) for apply=1\n");
		return ret;
	}
	ret = nitro_proto_probe_state(&nitro_kernel_ops);
	if (ret)
		return ret;

	ret = nitro_proto_apply_static_palette(&nitro_kernel_ops, &pal);
	if (ret)
		return ret;

	pr_info("nitro_rgb_enable_poc: firmware confirmed static mode=0, brightness=%u, and four zone color readbacks (inspect keyboard visually)\n",
		brightness);
	return 0;
}

#ifdef NITRO_ENABLE_EXPERIMENTAL_PARAMS
static int nitro_backlight_only_test(void)
{
	int ret;

	pr_info("nitro_rgb_enable_poc: probing state before method 20\n");
	ret = nitro_proto_probe_state(&nitro_kernel_ops);
	if (ret)
		return ret;

	ret = nitro_proto_set_kb_backlight(&nitro_kernel_ops, 25, 0, 0, 0);
	if (ret)
		return ret;

	pr_info("nitro_rgb_enable_poc: probing state after method 20\n");
	return nitro_proto_probe_state(&nitro_kernel_ops);
}

static int nitro_set_static_zone(u8 zone, u8 red, u8 green, u8 blue)
{
	struct led_four_zone_set_param params = {
		.zone = zone,
		.red = red,
		.green = green,
		.blue = blue,
	};
	struct acpi_buffer input = {
		.length = sizeof(params),
		.pointer = &params,
	};
	struct acpi_buffer output = {
		.length = ACPI_ALLOCATE_BUFFER,
		.pointer = NULL,
	};
	struct nitro_acpi_obj nobj = {
		.type = NITRO_ACPI_NONE,
		.integer_value = 0,
		.buffer_ptr = NULL,
		.buffer_len = 0,
	};
	union acpi_object *obj;
	acpi_status status;
	int ret;

	status = wmi_evaluate_method(ACER_GAMING_WMI_GUID, 0,
				     ACER_WMID_SET_GAMING_STATIC_LED_METHODID,
				     &input, &output);
	if (ACPI_FAILURE(status)) {
		kfree(output.pointer);
		pr_err("nitro_rgb_enable_poc: method 6 zone 0x%x write failed: %s\n",
		       zone, acpi_format_exception(status));
		return -EIO;
	}
	obj = output.pointer;
	if (obj) {
		if (obj->type == ACPI_TYPE_INTEGER) {
			nobj.type = NITRO_ACPI_INTEGER;
			nobj.integer_value = obj->integer.value;
		} else if (obj->type == ACPI_TYPE_BUFFER) {
			nobj.type = NITRO_ACPI_BUFFER;
			nobj.buffer_ptr = obj->buffer.pointer;
			nobj.buffer_len = obj->buffer.length;
		} else {
			nobj.type = NITRO_ACPI_OTHER;
		}
	}
	ret = nitro_validate_acpi_reply(1, &nobj, NITRO_RESP_OPTIONAL_STATUS_ZERO, NULL, NULL);
	kfree(output.pointer);
	if (ret) {
		pr_err("nitro_rgb_enable_poc: method 6 zone 0x%x firmware rejected\n", zone);
		return ret;
	}
	pr_info("nitro_rgb_enable_poc: method 6 wrote zone 0x%x rgb=%u,%u,%u\n",
		zone, red, green, blue);
	return 0;
}

static int nitro_static_red_test(void)
{
	static const u8 zone_ids[] = { 0x1, 0x2, 0x4, 0x8 };
	int i, ret;

	ret = nitro_proto_enable_zones(&nitro_kernel_ops);
	if (ret)
		return ret;

	for (i = 0; i < (int)ARRAY_SIZE(zone_ids); i++) {
		ret = nitro_set_static_zone(zone_ids[i], 255, 0, 0);
		if (ret)
			return ret;
	}

	pr_info("nitro_rgb_enable_poc: static-red write completed; inspect keyboard visually\n");
	return nitro_proto_probe_state(&nitro_kernel_ops);
}
#endif

static int __init nitro_rgb_enable_poc_init(void)
{
	struct nitro_effect_outcome effect_out;
	int active_modes;

	BUILD_BUG_ON(sizeof(struct nitro_kb_state) != 16);

	if (!nitro_exact_model()) {
		pr_err("nitro_rgb_enable_poc: refusing non-AN515-58 hardware\n");
		return -ENODEV;
	}

	if (!wmi_has_guid(ACER_GAMING_WMI_GUID)) {
		pr_err("nitro_rgb_enable_poc: Acer gaming WMI GUID not present\n");
		return -ENODEV;
	}

#ifdef NITRO_ENABLE_EXPERIMENTAL_PARAMS
	if (effect_test && !effect_mode)
		return -EINVAL;
	active_modes = !!probe + !!enable + !!red_test + !!backlight_test +
		       !!full_test + !!apply + !!effect_mode;
#else
	active_modes = !!probe + !!full_test + !!apply + !!effect_mode;
#endif

	if ((effect_mode && (effect_mode > 5 || effect_speed < 1 || effect_speed > 9 ||
			     effect_rgb > 0xffffff || brightness != 100)) ||
	    ((apply || full_test) && brightness != 100)) {
		pr_err("nitro_rgb_enable_poc: invalid brightness or effect parameter range\n");
		return -EINVAL;
	}

	if (active_modes > 1) {
		pr_err("nitro_rgb_enable_poc: specify at most one mode\n");
		return -EINVAL;
	}

	if (!active_modes) {
		pr_info("nitro_rgb_enable_poc: dry run only; model and WMI GUID validated\n");
		pr_info("nitro_rgb_enable_poc: use probe=1 for read-only firmware-state inspection\n");
		return 0;
	}

	if (probe) {
		pr_info("nitro_rgb_enable_poc: starting read-only keyboard-state probe\n");
		return nitro_proto_probe_state(&nitro_kernel_ops);
	}

	if (full_test)
		return nitro_full_static_red_test();

	if (apply)
		return nitro_apply_custom();

	if (effect_mode) {
		int eff_ret;
#ifdef NITRO_ENABLE_EXPERIMENTAL_PARAMS
		eff_ret = nitro_proto_apply_effect(&nitro_kernel_ops, effect_mode,
						   effect_speed, effect_rgb,
						   effect_test, &effect_out);
#else
		eff_ret = nitro_proto_apply_effect(&nitro_kernel_ops, effect_mode,
						   effect_speed, effect_rgb,
						   false, &effect_out);
#endif
		if (eff_ret && effect_out.recovery != NITRO_RECOVERY_NONE) {
			pr_err("nitro_rgb_enable_poc: effect outcome status=%d prior_mode=%d recovery=%d\n",
			       effect_out.status, effect_out.prior_mode, (int)effect_out.recovery);
		} else if (!eff_ret && effect_out.recovery != NITRO_RECOVERY_NONE) {
			pr_info("nitro_rgb_enable_poc: effect outcome status=%d prior_mode=%d recovery=%d\n",
				effect_out.status, effect_out.prior_mode, (int)effect_out.recovery);
		}
		return eff_ret;
	}

#ifdef NITRO_ENABLE_EXPERIMENTAL_PARAMS
	if (enable)
		return nitro_proto_enable_zones(&nitro_kernel_ops);

	if (backlight_test)
		return nitro_backlight_only_test();

	if (red_test)
		return nitro_static_red_test();
#endif

	return 0;
}

static void __exit nitro_rgb_enable_poc_exit(void)
{
	pr_info("nitro_rgb_enable_poc: unloaded (firmware state is not changed on unload)\n");
}

module_init(nitro_rgb_enable_poc_init);
module_exit(nitro_rgb_enable_poc_exit);

MODULE_AUTHOR("Nitro Control project");
MODULE_DESCRIPTION("AN515-58 guarded RGB firmware experiment");
MODULE_LICENSE("GPL");

// SPDX-License-Identifier: GPL-2.0-only
/*
 * Nitro Control AN515-58 RGB proof-of-concept.
 *
 * This is deliberately NOT a replacement for acer_wmi. It provides:
 *   - default dry-run validation only;
 *   - an explicit read-only firmware-state probe;
 *   - the earlier explicit RGB-enable sequence for comparison.
 *
 * No autoload alias is provided. The module must be inserted manually.
 */

#include <linux/acpi.h>
#include <linux/dmi.h>
#include <linux/init.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/wmi.h>

#define ACER_GAMING_WMI_GUID "7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56"

#define ACER_WMID_GET_GAMING_LED_METHODID          4
#define ACER_WMID_GET_GAMING_SYS_INFO_METHODID     5
#define ACER_WMID_SET_GAMING_STATIC_LED_METHODID    6
#define ACER_WMID_GET_GAMING_RGB_KB_METHODID       7
#define ACER_WMID_SET_GAMING_KB_BACKLIGHT_METHODID 20
#define ACER_WMID_GET_GAMING_KB_BACKLIGHT_METHODID 21

#define ACER_GAMING_KBL_SET_ON        BIT_ULL(3)
#define ACER_GAMING_KBL_SET_ALL_ZONES GENMASK_ULL(43, 40)

struct get_four_zoned_kb_output {
	u8 gm_return;
	u8 gm_output[15];
} __packed;

struct led_four_zone_set_param {
	u8 zone;
	u8 red;
	u8 green;
	u8 blue;
} __packed;

static bool enable;
module_param(enable, bool, 0400);
MODULE_PARM_DESC(enable,
	"Issue the RFC AN515-58 gaming-WMI zone-enable sequence (default: false)");

static bool probe;
module_param(probe, bool, 0400);
MODULE_PARM_DESC(probe,
	"Read keyboard mode/brightness and four zone-color firmware state (default: false)");

static bool red_test;
module_param(red_test, bool, 0400);
MODULE_PARM_DESC(red_test,
	"Write static red to all four zones using RFC method 6 (default: false)");

static bool backlight_test;
module_param(backlight_test, bool, 0400);
MODULE_PARM_DESC(backlight_test,
	"Issue only Linuwu-style method 20 static-mode/brightness=25 setup (default: false)");

static bool nitro_exact_model(void)
{
	const char *vendor = dmi_get_system_info(DMI_SYS_VENDOR);
	const char *product = dmi_get_system_info(DMI_PRODUCT_NAME);

	return vendor && product &&
	       !strcmp(vendor, "Acer") &&
	       !strcmp(product, "Nitro AN515-58");
}

static acpi_status nitro_wmi_call_u64(u32 method_id, u64 value, u64 *result)
{
	struct acpi_buffer input = {
		.length = sizeof(value),
		.pointer = &value,
	};
	struct acpi_buffer output = {
		.length = ACPI_ALLOCATE_BUFFER,
		.pointer = NULL,
	};
	union acpi_object *obj;
	acpi_status status;
	u64 tmp = 0;

	status = wmi_evaluate_method(ACER_GAMING_WMI_GUID, 0, method_id,
				     &input, &output);
	if (ACPI_FAILURE(status))
		return status;

	obj = output.pointer;
	if (obj && result) {
		if (obj->type == ACPI_TYPE_INTEGER) {
			tmp = obj->integer.value;
		} else if (obj->type == ACPI_TYPE_BUFFER) {
			if (obj->buffer.length == sizeof(u32))
				tmp = *(u32 *)obj->buffer.pointer;
			else if (obj->buffer.length == sizeof(u64))
				tmp = *(u64 *)obj->buffer.pointer;
		}
		*result = tmp;
	}

	kfree(output.pointer);
	return status;
}

static int nitro_probe_keyboard_state(void)
{
	static const u8 zone_ids[] = { 0x1, 0x2, 0x4, 0x8 };
	struct acpi_buffer input;
	struct acpi_buffer output = {
		.length = ACPI_ALLOCATE_BUFFER,
		.pointer = NULL,
	};
	struct get_four_zoned_kb_output state;
	union acpi_object *obj;
	acpi_status status;
	u64 in = 1;
	u64 raw;
	int i;

	input.length = sizeof(in);
	input.pointer = &in;
	status = wmi_evaluate_method(ACER_GAMING_WMI_GUID, 0,
				     ACER_WMID_GET_GAMING_KB_BACKLIGHT_METHODID,
				     &input, &output);
	if (ACPI_FAILURE(status)) {
		pr_err("nitro_rgb_enable_poc: method 21 state read failed: %s\n",
		       acpi_format_exception(status));
		return -EIO;
	}

	obj = output.pointer;
	if (!obj || obj->type != ACPI_TYPE_BUFFER ||
	    obj->buffer.length != sizeof(state)) {
		pr_err("nitro_rgb_enable_poc: method 21 returned unexpected object (type=%u len=%u)\n",
		       obj ? obj->type : 0,
		       obj && obj->type == ACPI_TYPE_BUFFER ? obj->buffer.length : 0);
		kfree(output.pointer);
		return -EIO;
	}

	memcpy(&state, obj->buffer.pointer, sizeof(state));
	kfree(output.pointer);

	pr_info("nitro_rgb_enable_poc: keyboard state: return=%u mode=%u speed=%u brightness=%u direction=%u rgb=%u,%u,%u\n",
		state.gm_return,
		state.gm_output[0], state.gm_output[1], state.gm_output[2],
		state.gm_output[4], state.gm_output[5],
		state.gm_output[6], state.gm_output[7]);

	for (i = 0; i < ARRAY_SIZE(zone_ids); i++) {
		raw = 0;
		status = nitro_wmi_call_u64(ACER_WMID_GET_GAMING_RGB_KB_METHODID,
					    zone_ids[i], &raw);
		if (ACPI_FAILURE(status)) {
			pr_err("nitro_rgb_enable_poc: method 7 zone %d read failed: %s\n",
			       i + 1, acpi_format_exception(status));
			return -EIO;
		}
		pr_info("nitro_rgb_enable_poc: zone %d raw=0x%016llx\n",
			i + 1, raw);
	}

	return 0;
}

static int nitro_enable_zones(void)
{
	acpi_status status;
	u64 zone_enable = ACER_GAMING_KBL_SET_ON |
			  ACER_GAMING_KBL_SET_ALL_ZONES;

	pr_info("nitro_rgb_enable_poc: issuing gaming-system-info poll\n");
	status = nitro_wmi_call_u64(ACER_WMID_GET_GAMING_SYS_INFO_METHODID,
				    0, NULL);
	if (ACPI_FAILURE(status)) {
		pr_err("nitro_rgb_enable_poc: method 5 failed: %s\n",
		       acpi_format_exception(status));
		return -EIO;
	}

	pr_info("nitro_rgb_enable_poc: enabling all four keyboard zones (0x%llx)\n",
		zone_enable);
	status = nitro_wmi_call_u64(ACER_WMID_GET_GAMING_LED_METHODID,
				    zone_enable, NULL);
	if (ACPI_FAILURE(status)) {
		pr_err("nitro_rgb_enable_poc: method 4 failed: %s\n",
		       acpi_format_exception(status));
		return -EIO;
	}

	pr_info("nitro_rgb_enable_poc: WMI enable sequence completed\n");
	return 0;
}

static int nitro_set_kb_backlight_static_25(void)
{
	u8 gm_input[16] = {
		0,  /* static mode */
		0,  /* speed */
		25, /* preserve observed firmware brightness */
		0,
		0,  /* direction */
		0, 0, 0, /* global RGB; per-zone RGB is stored separately */
		3, 1,
		0, 0, 0, 0, 0, 0
	};
	struct acpi_buffer input = {
		.length = sizeof(gm_input),
		.pointer = gm_input,
	};
	struct acpi_buffer output = {
		.length = ACPI_ALLOCATE_BUFFER,
		.pointer = NULL,
	};
	union acpi_object *obj;
	acpi_status status;
	u64 response = 0;

	status = wmi_evaluate_method(ACER_GAMING_WMI_GUID, 0,
				     ACER_WMID_SET_GAMING_KB_BACKLIGHT_METHODID,
				     &input, &output);
	if (ACPI_FAILURE(status)) {
		pr_err("nitro_rgb_enable_poc: method 20 failed: %s\n",
		       acpi_format_exception(status));
		return -EIO;
	}

	obj = output.pointer;
	if (obj) {
		if (obj->type == ACPI_TYPE_INTEGER) {
			response = obj->integer.value;
		} else if (obj->type == ACPI_TYPE_BUFFER) {
			if (obj->buffer.length == sizeof(u32))
				response = *(u32 *)obj->buffer.pointer;
			else if (obj->buffer.length == sizeof(u64))
				response = *(u64 *)obj->buffer.pointer;
		}
	}
	kfree(output.pointer);

	if (response) {
		pr_err("nitro_rgb_enable_poc: method 20 firmware response=%llu\n",
		       response);
		return -EIO;
	}

	pr_info("nitro_rgb_enable_poc: method 20 accepted static mode, brightness=25\n");
	return 0;
}

static int nitro_backlight_only_test(void)
{
	int ret;

	pr_info("nitro_rgb_enable_poc: probing state before method 20\n");
	ret = nitro_probe_keyboard_state();
	if (ret)
		return ret;

	ret = nitro_set_kb_backlight_static_25();
	if (ret)
		return ret;

	pr_info("nitro_rgb_enable_poc: probing state after method 20\n");
	return nitro_probe_keyboard_state();
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
	acpi_status status;

	status = wmi_evaluate_method(ACER_GAMING_WMI_GUID, 0,
				     ACER_WMID_SET_GAMING_STATIC_LED_METHODID,
				     &input, NULL);
	if (ACPI_FAILURE(status)) {
		pr_err("nitro_rgb_enable_poc: method 6 zone 0x%x write failed: %s\n",
		       zone, acpi_format_exception(status));
		return -EIO;
	}

	pr_info("nitro_rgb_enable_poc: method 6 wrote zone 0x%x rgb=%u,%u,%u\n",
		zone, red, green, blue);
	return 0;
}

static int nitro_static_red_test(void)
{
	static const u8 zone_ids[] = { 0x1, 0x2, 0x4, 0x8 };
	int i, ret;

	ret = nitro_enable_zones();
	if (ret)
		return ret;

	for (i = 0; i < ARRAY_SIZE(zone_ids); i++) {
		ret = nitro_set_static_zone(zone_ids[i], 255, 0, 0);
		if (ret)
			return ret;
	}

	pr_info("nitro_rgb_enable_poc: static-red write completed; inspect keyboard visually\n");

	/* Read back immediately so the test records firmware state too. */
	return nitro_probe_keyboard_state();
}

static int __init nitro_rgb_enable_poc_init(void)
{
	int ret;

	if (!nitro_exact_model()) {
		pr_err("nitro_rgb_enable_poc: refusing non-AN515-58 hardware\n");
		return -ENODEV;
	}

	if (!wmi_has_guid(ACER_GAMING_WMI_GUID)) {
		pr_err("nitro_rgb_enable_poc: Acer gaming WMI GUID not present\n");
		return -ENODEV;
	}

	if (!probe && !enable && !red_test && !backlight_test) {
		pr_info("nitro_rgb_enable_poc: dry run only; model and WMI GUID validated\n");
		pr_info("nitro_rgb_enable_poc: use probe=1 for read-only firmware-state inspection\n");
		return 0;
	}

	if (probe) {
		pr_info("nitro_rgb_enable_poc: starting read-only keyboard-state probe\n");
		ret = nitro_probe_keyboard_state();
		if (ret)
			return ret;
	}

	if (enable) {
		ret = nitro_enable_zones();
		if (ret)
			return ret;
	}

	if (backlight_test)
		return nitro_backlight_only_test();

	if (red_test)
		return nitro_static_red_test();

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
